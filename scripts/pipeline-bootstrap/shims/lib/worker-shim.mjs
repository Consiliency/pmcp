import { TextDecoder } from "node:util";

const VALUE_FLAGS = new Set([
  "--sandbox",
  "--output-last-message",
  "--model",
  "-c",
  "--output-format",
  "--setting-sources",
  "--permission-mode",
  "--agent",
  "--effort",
  "--session-id",
  "--resume",
  "--json-schema",
  "--plugin-dir",
  "--approval-mode",
]);

const BOOLEAN_FLAGS = new Set([
  "--full-auto",
  "--json",
  "-p",
  "--dangerously-skip-permissions",
]);

function usage(message) {
  const error = new Error(message);
  error.code = "USAGE";
  return error;
}

function requireValue(argv, index, flag) {
  const value = argv[index + 1];
  if (value === undefined || value.startsWith("--")) {
    throw usage(`missing value for ${flag}`);
  }
  return value;
}

function parseCodex(argv, stdin) {
  if (argv[0] === "-p") {
    if (argv.length !== 2) throw usage("codex -p smoke shortcut accepts only prompt text");
    const prompt = argv[1];
    if (!prompt || !prompt.trim()) throw usage("codex shim requires a non-empty prompt");
    return { prompt, args: [] };
  }
  if (argv[0] !== "exec") throw usage("codex shim supports governed-pipeline codex exec only");
  const passThrough = [];
  let prompt = null;

  for (let i = 1; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "-") {
      if (i !== argv.length - 1) throw usage("codex stdin prompt marker must be final");
      prompt = stdin ?? "";
      continue;
    }
    if (arg === "resume") {
      passThrough.push(arg, requireValue(argv, i, arg));
      i++;
      continue;
    }
    if (VALUE_FLAGS.has(arg)) {
      passThrough.push(arg, requireValue(argv, i, arg));
      i++;
      continue;
    }
    if (BOOLEAN_FLAGS.has(arg)) {
      passThrough.push(arg);
      continue;
    }
    if (arg.startsWith("-")) throw usage(`unsupported codex arg ${arg}`);
    if (i !== argv.length - 1) throw usage(`unsupported codex positional arg ${arg}`);
    prompt = arg;
  }

  if (!prompt || !prompt.trim()) throw usage("codex shim requires a non-empty prompt");
  return { prompt, args: passThrough };
}

function parseClaude(argv) {
  if (argv.length === 0) throw usage("claude shim requires a prompt");
  const prompt = argv[argv.length - 1];
  if (prompt.startsWith("-") || !prompt.trim()) throw usage("claude shim requires final prompt text");
  const passThrough = [];

  for (let i = 0; i < argv.length - 1; i++) {
    const arg = argv[i];
    if (VALUE_FLAGS.has(arg)) {
      passThrough.push(arg, requireValue(argv, i, arg));
      i++;
      continue;
    }
    if (BOOLEAN_FLAGS.has(arg)) {
      passThrough.push(arg);
      continue;
    }
    throw usage(`unsupported claude arg ${arg}`);
  }

  return { prompt, args: passThrough };
}

function parseGemini(argv) {
  const passThrough = [];
  let prompt = null;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "-p") {
      prompt = requireValue(argv, i, arg);
      i++;
      continue;
    }
    if (VALUE_FLAGS.has(arg)) {
      passThrough.push(arg, requireValue(argv, i, arg));
      i++;
      continue;
    }
    if (BOOLEAN_FLAGS.has(arg)) {
      passThrough.push(arg);
      continue;
    }
    throw usage(`unsupported gemini arg ${arg}`);
  }

  if (!prompt || !prompt.trim()) throw usage("gemini shim requires -p prompt text");
  return { prompt, args: passThrough };
}

export function parseWorkerShimArgs(harness, argv, stdin = "") {
  if (harness === "codex") return parseCodex(argv, stdin);
  if (harness === "claude") return parseClaude(argv);
  if (harness === "gemini") return parseGemini(argv);
  throw usage(`unsupported harness ${harness}`);
}

function write(stream, chunk) {
  if (chunk) stream.write(String(chunk));
}

function endpoint(baseUrl) {
  return `${baseUrl.replace(/\/+$/, "")}/jobs/agent-invoke`;
}

function parseJson(data, fallback = {}) {
  try {
    return JSON.parse(data);
  } catch {
    return fallback;
  }
}

function parseSseFrame(frame) {
  let event = "message";
  const data = [];
  for (const rawLine of frame.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    let value = colon === -1 ? "" : rawLine.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  return { event, data: data.join("\n") };
}

async function readSse(response, { stdout, stderr }) {
  const reader = response.body?.getReader?.();
  if (!reader) throw new Error("worker response did not include a readable SSE body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary;
    while ((boundary = nextSseBoundary(buffer)) !== null) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary.length);
      const { event, data } = parseSseFrame(frame);
      if (event === "stdout") write(stdout, data);
      else if (event === "stderr") write(stderr, data);
      else if (event === "exit") {
        const payload = parseJson(data, null);
        if (!payload || !Number.isInteger(payload.code)) throw new Error("malformed worker exit event");
        return payload.code;
      } else if (event === "error") {
        write(stderr, "worker terminal error\n");
        return 1;
      }
    }
  }

  if (buffer.trim()) throw new Error("unterminated worker SSE frame");
  throw new Error("worker SSE ended without terminal event");
}

function nextSseBoundary(buffer) {
  const lf = buffer.indexOf("\n\n");
  const crlf = buffer.indexOf("\r\n\r\n");
  if (lf === -1 && crlf === -1) return null;
  if (lf !== -1 && (crlf === -1 || lf < crlf)) return { index: lf, length: 2 };
  return { index: crlf, length: 4 };
}

export async function runWorkerShim({
  harness,
  argv,
  stdin = "",
  stdout = process.stdout,
  stderr = process.stderr,
  env = process.env,
  fetchFn = globalThis.fetch,
} = {}) {
  const baseUrl = env.WORKER_BASE_URL;
  const apiKey = env.WORKER_API_KEY;
  if (!baseUrl || !apiKey) {
    write(stderr, `usage: set WORKER_BASE_URL and WORKER_API_KEY for ${harness} shim\n`);
    return 2;
  }

  let parsed;
  try {
    parsed = parseWorkerShimArgs(harness, argv ?? [], stdin);
  } catch (error) {
    if (error.code === "USAGE") {
      write(stderr, `usage: ${error.message}\n`);
      return 2;
    }
    throw error;
  }

  let response;
  try {
    response = await fetchFn(endpoint(baseUrl), {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
      },
      body: JSON.stringify({ harness, prompt: parsed.prompt, args: parsed.args }),
    });
  } catch {
    write(stderr, "worker infrastructure failure: network error\n");
    return 3;
  }

  if (response.status === 401) {
    write(stderr, "worker authorization failed\n");
    return 1;
  }
  if (response.status >= 500) {
    write(stderr, `worker infrastructure failure: HTTP ${response.status}\n`);
    return 3;
  }
  if (!response.ok) {
    write(stderr, `worker request rejected: HTTP ${response.status}\n`);
    return 1;
  }

  try {
    return await readSse(response, { stdout, stderr });
  } catch {
    write(stderr, "worker infrastructure failure: malformed SSE\n");
    return 3;
  }
}
