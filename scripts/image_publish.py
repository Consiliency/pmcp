"""Guarded container image publisher for .github/workflows/image.yml.

Standard library only; image.yml runs it as ``python3 -I -B`` from a sparse
checkout of ``github.workflow_sha``. Subcommands:

``verify-upstream``  GET-only re-check of the Test run that triggered image.yml.
``oci-inspect``      verify the single-manifest OCI archive layout and digests.
``smoke``            compare the locally loaded image with the archive and drive
                     a real downstream tool call through it as UID 1000.
``promote``          the only registry writer reachable from image.yml: write the
                     immutable ``sha-<40 hex>`` tag, then move ``main``.
``tag``              exact-version retag of an existing ``sha-<40 hex>`` digest.
                     Implemented and tested, but no workflow invokes it.

Every write path re-checks that PMCP_IMAGE_PUBLISH is exactly ``enabled``. The
registry token reaches skopeo only through a 0600 authfile, never argv.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import IO, Any, Callable, Iterator, NamedTuple

REPOSITORY = "Consiliency/pmcp"
REPOSITORY_ID = 1123923171
TEST_WORKFLOW_ID = 219243728
TEST_WORKFLOW_PATH = ".github/workflows/test.yml"
REQUIRED_JOB = "ci-required"
REGISTRY = "ghcr.io"
IMAGE = "consiliency/pmcp"
API = "api.github.com"
SKOPEO_IMAGE = "quay.io/skopeo/stable@sha256:cb18e3ddb78c7ce4c06d53de494e0d83b9a63f58bd205149d2c156d75fdecfe4"
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_TYPE = "application/vnd.oci.image.config.v1+json"
ACCEPT = (
    INDEX_TYPE,
    MANIFEST_TYPE,
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
SHA40 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z][0-9A-Za-z.-]*)?")
SMOKE_UID = "1000"
SMOKE_TEXT = "image-smoke"
MAX_JSON = 8 * 1024 * 1024
MAX_JOB_PAGES = 50
TIMEOUT = 60


class Refusal(Exception):
    """A fixed refusal. Its text never carries a token, a body or argv."""


class Response(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


Transport = Callable[[str, dict[str, str]], Response]


def publishing_enabled(environb: Any = None) -> bool:
    """Phase 1: byte-exact ``enabled``; no case folding or trimming."""
    env = os.environb if environb is None else environb
    return env.get(b"PMCP_IMAGE_PUBLISH") == b"enabled"


# --- HTTP (GET only, two hosts) -----------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def urllib_get(url: str, headers: dict[str, str]) -> Response:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {API, REGISTRY}:
        raise Refusal("request host refused")
    if parts.port is not None or parts.username is not None:
        raise Refusal("request host refused")
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_JSON + 1)
            status, items = response.status, response.headers.items()
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_JSON + 1) if exc.fp is not None else b""
        status = exc.code
        items = exc.headers.items() if exc.headers is not None else []
    except (OSError, ValueError) as exc:
        raise Refusal("request failed or timed out") from exc
    if len(body) > MAX_JSON:
        raise Refusal("oversize response")
    return Response(status, {k.lower(): v for k, v in items}, body)


class GitHub:
    """GET-only GitHub REST reader authenticated by GH_TOKEN from the env."""

    def __init__(self, token: str | None, transport: Transport = urllib_get):
        self._token = token
        self._transport = transport

    def get(self, path: str) -> Any:
        if not path.startswith(f"repos/{REPOSITORY}/") or ".." in path:
            raise Refusal("API path refused")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        response = self._transport(f"https://{API}/{path}", headers)
        if response.status != 200:
            raise Refusal("GitHub API read failed")
        try:
            return json.loads(response.body)
        except ValueError as exc:
            raise Refusal("GitHub API returned invalid JSON") from exc


class Registry:
    """GET-only manifest digest reader for ghcr.io/consiliency/pmcp."""

    def __init__(self, token: str | None, transport: Transport = urllib_get):
        self._token = token
        self._transport = transport
        self._bearer: str | None = None

    def _auth(self) -> dict[str, str]:
        if self._bearer is None:
            headers = {}
            if self._token:
                basic = base64.b64encode(f"token:{self._token}".encode()).decode()
                headers["Authorization"] = f"Basic {basic}"
            response = self._transport(
                f"https://{REGISTRY}/token?scope=repository:{IMAGE}:pull", headers
            )
            if response.status != 200:
                raise Refusal("registry token refused")
            try:
                bearer = json.loads(response.body).get("token")
            except (ValueError, AttributeError) as exc:
                raise Refusal("registry token refused") from exc
            if not isinstance(bearer, str) or not bearer:
                raise Refusal("registry token refused")
            self._bearer = bearer
        return {"Authorization": f"Bearer {self._bearer}"}

    def digest(self, tag: str) -> str | None:
        """The manifest digest of ``tag``, or ``None`` when it does not exist."""
        headers = dict(self._auth(), Accept=", ".join(ACCEPT))
        response = self._transport(
            f"https://{REGISTRY}/v2/{IMAGE}/manifests/{tag}", headers
        )
        if response.status == 404:
            return None
        if response.status != 200:
            raise Refusal("registry read failed")
        digest = response.headers.get("docker-content-digest", "")
        if DIGEST.fullmatch(digest) is None:
            raise Refusal("registry digest missing")
        if "sha256:" + hashlib.sha256(response.body).hexdigest() != digest:
            raise Refusal("registry manifest does not match its digest")
        return digest


# --- upstream verification ------------------------------------------------------


def verify_upstream(get: Callable[[str], Any], run_id: int, attempt: int) -> str:
    """Return the verified ``head_sha`` of the Test run attempt, or refuse."""
    base = f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}"
    try:
        run = get(base)
        checks = {
            "id": run["id"] == run_id,
            "repository": run["repository"]["id"] == REPOSITORY_ID,
            "head_repository": run["head_repository"]["id"] == REPOSITORY_ID,
            "workflow_id": run["workflow_id"] == TEST_WORKFLOW_ID,
            "path": run["path"] == TEST_WORKFLOW_PATH,
            "event": run["event"] == "push",
            "head_branch": run["head_branch"] == "main",
            "run_attempt": run["run_attempt"] == attempt,
            "status": run["status"] == "completed",
            "conclusion": run["conclusion"] == "success",
            "head_sha": isinstance(run["head_sha"], str)
            and SHA40.fullmatch(run["head_sha"]) is not None,
        }
        failed = sorted(name for name, ok in checks.items() if ok is not True)
        if failed:
            raise Refusal("upstream run refused: " + ", ".join(failed))
        jobs: list[Any] = []
        total = None
        for page in range(1, MAX_JOB_PAGES + 1):
            data = get(f"{base}/jobs?per_page=100&page={page}")
            count, rows = data["total_count"], data["jobs"]
            if type(count) is not int or not isinstance(rows, list):
                raise Refusal("upstream jobs listing refused")
            if total is None:
                total = count
            if count != total or len(rows) > 100:
                raise Refusal("upstream jobs listing changed")
            jobs.extend(rows)
            if len(jobs) == total:
                break
            if not rows or len(jobs) > total:
                raise Refusal("upstream jobs total_count mismatch")
        else:
            raise Refusal("upstream jobs pagination limit")
        required = [job for job in jobs if job["name"] == REQUIRED_JOB]
        if len(required) != 1 or required[0]["conclusion"] != "success":
            raise Refusal("upstream needs exactly one successful ci-required job")
        if any(
            job["run_id"] != run_id or job["run_attempt"] != attempt for job in jobs
        ):
            raise Refusal("upstream job belongs to another run attempt")
    except (KeyError, TypeError, AttributeError) as exc:
        raise Refusal("upstream response refused") from exc
    return str(run["head_sha"])


# --- OCI archive layout -----------------------------------------------------------


def _hash_stream(stream: IO[bytes]) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return "sha256:" + digest.hexdigest(), size


def inspect_archive(path: Path) -> dict[str, Any]:
    """Verify the single-manifest OCI layout; every blob must match its digest."""
    try:
        with tarfile.open(path, "r:") as tar:
            members: dict[str, tarfile.TarInfo] = {}
            for member in tar.getmembers():
                name = member.name[2:] if member.name.startswith("./") else member.name
                if member.isdir():
                    continue
                if (
                    not member.isfile()
                    or name.startswith("/")
                    or ".." in name.split("/")
                    or name in members
                ):
                    raise Refusal("OCI archive member refused")
                members[name] = member

            def small(name: str) -> bytes:
                member = members.get(name)
                if member is None or member.size > MAX_JSON:
                    raise Refusal("OCI archive entry missing or oversize")
                stream = tar.extractfile(member)
                if stream is None:
                    raise Refusal("OCI archive entry unreadable")
                return stream.read()

            blobs: dict[str, int] = {}
            for name, member in members.items():
                if not name.startswith("blobs/"):
                    continue
                match = re.fullmatch(r"blobs/sha256/([0-9a-f]{64})", name)
                stream = tar.extractfile(member)
                if match is None or stream is None:
                    raise Refusal("OCI archive blob refused")
                actual, size = _hash_stream(stream)
                if actual != f"sha256:{match[1]}":
                    raise Refusal("OCI blob does not match its digest")
                blobs[actual] = size

            def blob(descriptor: Any, media_type: str) -> bytes:
                digest = descriptor.get("digest")
                if (
                    descriptor.get("mediaType") != media_type
                    or not isinstance(digest, str)
                    or digest not in blobs
                    or descriptor.get("size") != blobs[digest]
                ):
                    raise Refusal("OCI descriptor refused")
                return small("blobs/sha256/" + digest.split(":", 1)[1])

            index = json.loads(small("index.json"))
            manifests = index.get("manifests")
            if not isinstance(manifests, list) or len(manifests) != 1:
                raise Refusal("OCI index must hold exactly one descriptor")
            manifest_bytes = blob(manifests[0], MANIFEST_TYPE)
            manifest = json.loads(manifest_bytes)
            if manifest.get("mediaType", MANIFEST_TYPE) != MANIFEST_TYPE:
                raise Refusal("OCI manifest media type refused")
            config = json.loads(blob(manifest.get("config"), CONFIG_TYPE))
            layers = manifest.get("layers")
            if not isinstance(layers, list) or not layers:
                raise Refusal("OCI manifest has no layers")
            for layer in layers:
                digest = layer.get("digest") if isinstance(layer, dict) else None
                if digest not in blobs or layer.get("size") != blobs[digest]:
                    raise Refusal("OCI layer descriptor refused")
            diff_ids = config["rootfs"]["diff_ids"]
            if not isinstance(diff_ids, list) or len(diff_ids) != len(layers):
                raise Refusal("OCI config rootfs refused")
    except (OSError, tarfile.TarError, ValueError, KeyError, TypeError) as exc:
        raise Refusal("OCI archive unreadable") from exc
    return {
        "manifest_digest": manifests[0]["digest"],
        "config_digest": manifest["config"]["digest"],
        "diff_ids": diff_ids,
    }


# --- local smoke ------------------------------------------------------------------


def _run(argv: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=True
    )


def loaded_identity(ref: str, run: Callable[..., Any] = _run) -> dict[str, Any]:
    """Config digest and diff_ids of a locally loaded image, from ``docker save``."""
    with tempfile.TemporaryDirectory(prefix="pmcp-image-loaded-") as scratch:
        saved = Path(scratch) / "loaded.tar"
        run(["docker", "image", "save", "-o", str(saved), ref], timeout=600)
        with tarfile.open(saved, "r:") as tar:

            def read(name: str) -> bytes:
                stream = tar.extractfile(tar.getmember(name))
                if stream is None:
                    raise Refusal("saved image entry unreadable")
                return stream.read(MAX_JSON + 1)

            manifest = json.loads(read("manifest.json"))
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise Refusal("saved image must hold exactly one image")
            config_bytes = read(manifest[0]["Config"])
    config = json.loads(config_bytes)
    return {
        "config_digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
        "diff_ids": config["rootfs"]["diff_ids"],
    }


class _Lines:
    """Line reader over a pipe with a deadline; stdlib threads, no select."""

    def __init__(self, stream: IO[str]):
        self._lines: list[str] = []
        self._done = False
        self._cond = threading.Condition()
        threading.Thread(target=self._pump, args=(stream,), daemon=True).start()

    def _pump(self, stream: IO[str]) -> None:
        for line in stream:
            with self._cond:
                self._lines.append(line)
                self._cond.notify_all()
        with self._cond:
            self._done = True
            self._cond.notify_all()

    def next(self, deadline: float) -> str:
        with self._cond:
            while not self._lines:
                if self._done:
                    raise Refusal("gateway closed its output")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Refusal("gateway response timed out")
                self._cond.wait(remaining)
            return self._lines.pop(0)


def _call(proc: Any, lines: _Lines, ident: int, method: str, params: Any) -> Any:
    message = {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + 90
    while True:
        try:
            reply = json.loads(lines.next(deadline))
        except ValueError:
            continue
        if isinstance(reply, dict) and reply.get("id") == ident:
            if "result" not in reply:
                raise Refusal(f"gateway refused {method}")
            return reply["result"]


def _tool_body(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("isError"):
        raise Refusal("gateway tool call failed")
    text = "\n".join(
        block.get("text", "")
        for block in result.get("content") or []
        if isinstance(block, dict)
    )
    body = json.loads(text)
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise Refusal("gateway tool call reported failure")
    return body


def smoke_gateway(
    ref: str,
    probe_server: Path,
    *,
    run: Callable[..., Any] = _run,
    spawn: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    """Drive client -> gateway -> stdio downstream -> back, inside the image."""
    name = f"pmcp-image-smoke-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="pmcp-image-probe-") as scratch:
        probe = Path(scratch)
        shutil.copyfile(probe_server, probe / "p1_probe_server.py")
        (probe / "config.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "p1probe": {
                            "command": "python",
                            "args": ["/probe/p1_probe_server.py"],
                        }
                    }
                }
            )
        )
        (probe / "policy.yaml").write_text('servers:\n  allowlist: ["p1probe"]\n')
        for path in probe.iterdir():
            path.chmod(0o644)
        probe.chmod(0o755)
        argv = [
            "docker", "run", "--rm", "-i", "--init", "--name", name,
            "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", "1g", "--cpus", "2",
            "--tmpfs", "/tmp:rw,nosuid,size=64m",
            "-v", f"{probe}:/probe:ro",
            ref,
            "--config", "/probe/config.json", "--project", "/tmp",
            "--policy", "/probe/policy.yaml", "--lock-dir", "/tmp/lock",
        ]  # fmt: skip
        proc = spawn(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            lines = _Lines(proc.stdout)
            _call(
                proc,
                lines,
                1,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pmcp-image-smoke", "version": "1"},
                },
            )
            proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
                + "\n"
            )
            proc.stdin.flush()
            _tool_body(
                _call(
                    proc,
                    lines,
                    2,
                    "tools/call",
                    {
                        "name": "gateway.connect_server",
                        "arguments": {"server_name": "p1probe"},
                    },
                )
            )
            invoked = _call(
                proc,
                lines,
                3,
                "tools/call",
                {
                    "name": "gateway.invoke",
                    "arguments": {
                        "tool_id": "p1probe::p1_echo",
                        "arguments": {"text": SMOKE_TEXT},
                    },
                },
            )
            if f"p1-floor-ok:{SMOKE_TEXT}" not in json.dumps(_tool_body(invoked)):
                raise Refusal("downstream response did not round-trip")
            uid = run(["docker", "exec", name, "id", "-u"], timeout=60).stdout.strip()
            if uid != SMOKE_UID:
                raise Refusal("smoke container does not run as UID 1000")
            proc.stdin.close()
            proc.wait(timeout=60)
            left = run(
                ["docker", "ps", "-aq", "--filter", f"name=^{name}$"], timeout=60
            ).stdout.strip()
            if left:
                raise Refusal("smoke container survived a normal shutdown")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=60,
                check=False,
            )
    return {"real_downstream_response": True, "uid": int(uid)}


def smoke(
    archive: Path,
    ref: str,
    probe_server: Path,
    *,
    run: Callable[..., Any] = _run,
    spawn: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    candidate = inspect_archive(archive)
    loaded = loaded_identity(ref, run=run)
    if (
        loaded["config_digest"] != candidate["config_digest"]
        or loaded["diff_ids"] != candidate["diff_ids"]
    ):
        raise Refusal("loaded image does not match the candidate archive")
    result = smoke_gateway(ref, probe_server, run=run, spawn=spawn)
    result.update(config_digest=candidate["config_digest"])
    return result


# --- registry writes --------------------------------------------------------------


@contextlib.contextmanager
def authfile(path: Path, token: str) -> Iterator[Path]:
    """A 0600 skopeo authfile that exists only for the duration of the writes."""
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"auths": {REGISTRY: {"auth": auth}}}, stream)
        if os.stat(path).st_mode & 0o777 != 0o600:
            raise Refusal("authfile mode is not 0600")
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def skopeo_copy_argv(
    source: str, tag: str, auth: Path, archive: Path | None
) -> list[str]:
    """Pinned skopeo in a container; the credential is only the mounted file."""
    argv = ["docker", "run", "--rm", "-v", f"{auth}:/auth.json:ro"]
    if archive is not None:
        argv += ["-v", f"{archive.parent}:/work:ro"]
    argv += [SKOPEO_IMAGE, "copy", "--preserve-digests"]
    if source.startswith("docker://"):
        argv += ["--src-authfile", "/auth.json"]
    argv += [
        "--dest-authfile",
        "/auth.json",
        source,
        f"docker://{REGISTRY}/{IMAGE}:{tag}",
    ]
    return argv


def make_copier(
    auth: Path, archive: Path | None, run: Callable[..., Any] = _run
) -> Callable[[str, str], None]:
    def copy(source: str, tag: str) -> None:
        if source == "archive":
            if archive is None:
                raise Refusal("no archive to copy")
            source = f"oci-archive:/work/{archive.name}"
        try:
            run(skopeo_copy_argv(source, tag, auth, archive), timeout=1800)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Refusal(f"registry write to {tag} failed") from exc

    return copy


def promote(
    *,
    archive: Path,
    manifest_digest: str,
    head_sha: str,
    api_get: Callable[[str], Any],
    registry_digest: Callable[[str], str | None],
    copy: Callable[[str, str], None],
    environb: Any = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Phases 1-6; any refusal raises before the next phase."""
    if not publishing_enabled(environb):
        raise Refusal("phase 1: PMCP_IMAGE_PUBLISH is not exactly enabled")
    if DIGEST.fullmatch(manifest_digest) is None or SHA40.fullmatch(head_sha) is None:
        raise Refusal("phase 1: candidate identity is malformed")
    log("phase 1: publishing enabled")
    if inspect_archive(archive)["manifest_digest"] != manifest_digest:
        raise Refusal("phase 2: archive does not match the build-smoke digest")
    log(f"phase 2: archive verified {manifest_digest}")

    def remote_main() -> str:
        ref = api_get(f"repos/{REPOSITORY}/git/ref/heads/main")
        try:
            sha = ref["object"]["sha"]
        except (KeyError, TypeError) as exc:
            raise Refusal("main ref unreadable") from exc
        return str(sha)

    if remote_main() != head_sha:
        raise Refusal("phase 3: remote main is no longer the verified head; stale")
    log("phase 3: remote main equals the verified head")
    target = f"sha-{head_sha}"
    existing = registry_digest(target)
    if existing is None:
        copy("archive", target)
        if registry_digest(target) != manifest_digest:
            raise Refusal(f"phase 4: {target} readback does not match")
        sha_state = "written"
    elif existing == manifest_digest:
        sha_state = "identical"
    else:
        raise Refusal(f"phase 4: {target} holds a different digest; refusing")
    log(f"phase 4: {target} {sha_state}")
    if remote_main() != head_sha:
        raise Refusal("phase 5: main moved; the SHA target stays, main is untouched")
    log("phase 5: remote main unchanged")
    copy("archive", "main")
    if registry_digest("main") != manifest_digest:
        raise Refusal("phase 6: main readback does not match")
    log("phase 6: main moved and read back")
    return {"sha_tag": target, "sha_state": sha_state, "main": manifest_digest}


def tag_version(
    *,
    version: str,
    source_sha: str,
    registry_digest: Callable[[str], str | None],
    copy: Callable[[str, str], None],
    environb: Any = None,
) -> str:
    """Retag an existing ``sha-<40 hex>`` digest onto one exact version (D1)."""
    if not publishing_enabled(environb):
        raise Refusal("PMCP_IMAGE_PUBLISH is not exactly enabled")
    if EXACT_VERSION.fullmatch(version) is None:
        raise Refusal("tag mode writes exact version tags only")
    if SHA40.fullmatch(source_sha) is None:
        raise Refusal("source must be a 40 lowercase hex commit")
    source = registry_digest(f"sha-{source_sha}")
    if source is None:
        raise Refusal("source sha tag is absent; tag mode never builds")
    existing = registry_digest(version)
    if existing == source:
        return "identical"
    if existing is not None:
        raise Refusal(f"{version} holds a different digest; refusing")
    copy(f"docker://{REGISTRY}/{IMAGE}:sha-{source_sha}", version)
    if registry_digest(version) != source:
        raise Refusal(f"{version} readback does not match")
    return "written"


# --- CLI ----------------------------------------------------------------------


def _positive(value: str) -> int:
    if re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return int(value)


def _outputs(pairs: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    for key, value in pairs.items():
        if not re.fullmatch(r"[0-9a-z:]+", value):
            raise Refusal("refusing to emit a malformed output")
        print(f"{key}={value}")
        if path:
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    up = sub.add_parser("verify-upstream", help="GET-only Test run verification")
    up.add_argument("--run-id", required=True, type=_positive)
    up.add_argument("--attempt", required=True, type=_positive)
    oci = sub.add_parser("oci-inspect", help="verify the OCI archive layout")
    oci.add_argument("--archive", required=True, type=Path)
    sm = sub.add_parser("smoke", help="local loaded-image comparison and smoke")
    sm.add_argument("--archive", required=True, type=Path)
    sm.add_argument("--image", required=True)
    sm.add_argument("--probe-server", required=True, type=Path)
    pr = sub.add_parser("promote", help="write sha-<head>, then move main")
    pr.add_argument("--archive", required=True, type=Path)
    pr.add_argument("--manifest-digest", required=True)
    pr.add_argument("--head-sha", required=True)
    pr.add_argument("--authfile", required=True, type=Path)
    tg = sub.add_parser("tag", help="exact-version retag (no workflow caller)")
    tg.add_argument("--version", required=True)
    tg.add_argument("--source-sha", required=True)
    tg.add_argument("--authfile", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    token = os.environ.get("GH_TOKEN") or None
    try:
        if args.command == "verify-upstream":
            head = verify_upstream(GitHub(token).get, args.run_id, args.attempt)
            _outputs({"head_sha": head})
        elif args.command == "oci-inspect":
            found = inspect_archive(args.archive)
            _outputs(
                {
                    "manifest_digest": found["manifest_digest"],
                    "config_digest": found["config_digest"],
                }
            )
        elif args.command == "smoke":
            print(json.dumps(smoke(args.archive, args.image, args.probe_server)))
        else:
            if not publishing_enabled():
                raise Refusal("PMCP_IMAGE_PUBLISH is not exactly enabled")
            if not token:
                raise Refusal("no registry credential in the environment")
            registry = Registry(token)
            with authfile(args.authfile, token) as auth:
                if args.command == "promote":
                    result: Any = promote(
                        archive=args.archive,
                        manifest_digest=args.manifest_digest,
                        head_sha=args.head_sha,
                        api_get=GitHub(token).get,
                        registry_digest=registry.digest,
                        copy=make_copier(auth, args.archive),
                    )
                else:
                    result = tag_version(
                        version=args.version,
                        source_sha=args.source_sha,
                        registry_digest=registry.digest,
                        copy=make_copier(auth, None),
                    )
            print(json.dumps(result))
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
