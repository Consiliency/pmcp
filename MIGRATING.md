# Upgrading from 2.x to 3.0

pmcp 3.0.0 is a major release because it refuses things 2.7.3 accepted. Most
of the changes come from the v13 trust boundary described in
[SECURITY.md](SECURITY.md). The biggest one is that **a repository's own
`.mcp.json`, manifest overlay and policy file are ignored until you approve
them**, so after you upgrade a project's servers can stop appearing until you
run one command.

This guide has a section for every item in the CHANGELOG's *Upgrade notes*,
in the same order, plus a few related changes the notes don't list. Each
section answers four questions: **Am I affected?**, **What changed**, **What to
do**, and **How to verify**. The CHANGELOG has the full detail.

Examples use `~` for your home directory and `/path/to/repo` for a checkout.
pmcp writes its own log to `.pmcp/logs/gateway.log` under the directory the
gateway runs in. A systemd user service runs in your home directory, so its
log is `~/.pmcp/logs/gateway.log`.

## Contents

- [Upgrade checklist](#upgrade-checklist)
- Breaking changes, in the order of the Upgrade notes:
  [project files](#project-files-need-approval) ·
  [exported `PMCP_*` variables](#pmcp_manifest_path-pmcp_config-and-pmcp_policy-must-be-exported-in-your-shell) ·
  [`.env` keys](#spawned-servers-no-longer-inherit-the-keys-pmcp-loaded-from-env) ·
  [symlinked `.env.pmcp`](#a-symlinked-project-envpmcp-is-refused) ·
  [project files supply credentials only](#a-project-file-supplies-credentials-only) ·
  [discovered servers](#discovered-servers-are-default-deny) ·
  [`packages:` policy](#new-packages-policy-section) ·
  [feedback](#feedback-submission-is-off-by-default) ·
  [auth URLs](#auth-urls-must-be-canonical) ·
  [auth responses](#auth-responses-changed) ·
  [`tools/call` gate](#the-toolscall-gate-enforces-the-schemas-pmcp-advertises) ·
  [task numbers](#task-numbers-are-bounded) ·
  [task units](#task-ttl-and-poll_interval-are-seconds-in-pmcp-and-milliseconds-on-the-wire) ·
  [redaction](#redaction-removes-more) ·
  [version pins](#manifest-version-pins) ·
  [unusable overlay entries](#an-overlay-entry-pmcp-cannot-use-is-skipped) ·
  [downstream servers](#downstream-servers-see-more-from-pmcp) ·
  [logs](#logs) ·
  [dependency floors](#dependency-floors) ·
  [agent hints](#agent-facing-hints) ·
  [symlinked `.mcp.json`](#a-symlinked-mcpjson-is-no-longer-edited) ·
  [`NaN`](#nan-from-httpsse-servers) ·
  [error text](#error-text-names-the-real-failure) ·
  [known issues](#known-issues-in-300)
- [Other things you may notice](#other-things-you-may-notice)
- [Rolling back to 2.7.3](#rolling-back-to-273)

## Upgrade checklist

1. **Back up your user-scoped configuration.** Project files are already in
   their repositories.

   <!-- run: backup -->
   ```bash
   backup=~/pmcp-backup-2.7.3
   for f in ~/.claude/gateway-policy.yaml ~/.claude/gateway-policy.json ~/.claude/gateway-guidance.yaml ~/.mcp.json ~/.claude/.mcp.json ~/.pmcp/manifest.yaml ~/.config/pmcp; do
     if [ -e "$f" ]; then dest="$backup/${f#"$HOME"/}"; mkdir -p "$(dirname "$dest")" && cp -RLp "$f" "$dest" && echo "saved $f"; fi
   done
   ```

   This works in bash and zsh. It skips files you don't have, prints one
   `saved` line per file it copies, and keeps each file's path under the
   backup directory, so `~/.mcp.json` and `~/.claude/.mcp.json` don't collide.
   `cp -RLp` copies the *contents* of a symlink, such as a `~/.mcp.json` that
   points into a dotfiles repository, and keeps file modes. `~/.config/pmcp`
   holds the credentials pmcp stored (`pmcp.env`, mode `0600`). Start from a
   backup directory that doesn't exist yet.

2. **Upgrade.** Use whichever matches how you installed pmcp:

   ```bash
   pmcp upgrade --dry-run     # shows the command it would run (uv tool or pip)
   pmcp upgrade
   # or, by hand:
   uv tool upgrade pmcp
   pip install --upgrade pmcp
   ```

   If you need more time, hold 2.x for now with `uv tool install --force 'pmcp<3'`
   or `pip install 'pmcp<3'`, and pin `pmcp<3` in any requirements file or
   lockfile that installs pmcp.

3. **Restart any running gateway.** `pmcp status` and `pmcp update` ask the
   gateway that is already running, so until you restart it you are still
   looking at 2.7.3. Run `systemctl --user restart pmcp`, or use
   `pmcp upgrade --restart-service` in step 2. Then check `pmcp --version`
   prints `pmcp 3.0.0`.

4. **Check the configuration.** In each checkout you run pmcp from:

   ```bash
   cd /path/to/repo
   pmcp config status      # a project .mcp.json awaiting approval shows: WARN invalid_source: project_source_not_approved
   pmcp doctor
   pmcp status
   ```

5. **Approve the project files you trust.** Read them first, then approve
   them; see [Project files need approval](#project-files-need-approval).

6. **Check the gateway log for the new WARNINGs**:

   ```bash
   grep -E '\[WARNING\] (Ignoring |Spawning |Installing |Starting install job |Verifying installation of |Running update probe|Skipping invalid )' ~/.pmcp/logs/gateway.log | tail -n 50
   journalctl --user -u pmcp --since today | grep -E 'Ignoring|Fatal error'
   ```

   The first command shows every WARNING that 3.0 adds or rewords in the log
   file: anything pmcp ignored (project files, invalid version pins, malformed
   config, overlay entries it cannot use) and every package-runner start, install
   and update probe. The
   "Ignoring PMCP_…" lines for `PMCP_*` variables set in a `.env` file go only
   to the gateway's stderr, not to `gateway.log`; the `journalctl` line catches
   them for a service, or watch the terminal for a gateway you started by hand.

   The first command reads a service's log. For a gateway started from a
   checkout, read `<checkout>/.pmcp/logs/gateway.log` instead. `pmcp logs`
   prints the same file, but its `--level` filter matches nothing at
   `warn` (log lines say `[WARNING]`), so use `grep`.

7. If anything blocks you, [roll back to 2.7.3](#rolling-back-to-273). Read
   that section first: one 3.0 setting stops 2.7.3 from starting.

## Breaking changes

### Project files need approval

**Am I affected?** You are if any checkout you run pmcp from contains one of
these files:

```bash
cd /path/to/repo
ls -l .mcp.json .mcp-gateway-policy.yaml .mcp-gateway-policy.json .pmcp/manifest.yaml 2>/dev/null
```

After upgrading, each such file is ignored, and pmcp logs one WARNING per
file:

```text
[WARNING] Ignoring project .mcp.json at /path/to/repo/.mcp.json: it has not been approved. To use it, run: pmcp trust approve /path/to/repo/.mcp.json
```

The other two kinds read `project gateway policy` and `project manifest
overlay`. If you edit a file after approving it, the reason reads `it changed
since it was approved`. `pmcp config status` reports a project `.mcp.json` that
is not approved as `WARN invalid_source: project_source_not_approved`, and that
project's servers don't appear.

You are also affected if you keep both `~/.claude/gateway-policy.yaml` and a
project `.mcp-gateway-policy.yaml`, and the project file was meant to *allow*
something your user policy does not.

**What changed.** pmcp ignores a repository's `.mcp.json`,
`.mcp-gateway-policy.yaml`/`.json` and `.pmcp/manifest.yaml` until you approve
the file's exact bytes with `pmcp trust approve`. Approvals are kept in
`~/.config/pmcp/trust.json` and are keyed on content: editing an approved file
by even one byte revokes the approval, with no command needed. A project
policy can now only **narrow** your user policy. Both files are read, and a
server, tool or resource is allowed only if both allow it. A project allowlist
cannot re-admit something you denied, a project limit can lower yours but
never raise it, and project redaction patterns are added to the defaults
rather than replacing them. 2.7.3 used the project policy *instead of* yours.

**These are unaffected:** `~/.mcp.json`, `~/.claude/.mcp.json`,
`~/.claude/gateway-policy.yaml`, `~/.pmcp/manifest.yaml`, and any file you
pass with `--config` or `--policy`. They apply without a trust record, as
before.

**What to do.** Read each file. A manifest overlay can run any command, and a
`.mcp.json` starts servers. If you trust it, approve it by absolute path from
anywhere:

<!-- run: approve -->
```bash
cd /path/to/repo
git log -p -- .mcp.json | head -n 40     # review what you are about to trust
pmcp trust approve "$PWD/.mcp.json"
pmcp trust approve "$PWD/.mcp-gateway-policy.yaml"
pmcp trust approve "$PWD/.pmcp/manifest.yaml"
```

The command prints `Approved <path>` and the file's sha256. A relative path
also works, because pmcp resolves it. To approve every project file in the
current checkout at once:

<!-- run: approve-loop -->
```bash
for f in .mcp.json .mcp-gateway-policy.yaml .mcp-gateway-policy.json .pmcp/manifest.yaml; do if [ -f "$f" ]; then pmcp trust approve "$PWD/$f"; fi; done
```

Approve again after every change you accept, including a `git pull` that
touches one of these files. `pmcp config set-startup-policy --source project
--apply` is the exception: when the file was approved before, it re-records
the approval for the bytes it writes. To withdraw an approval, run
`pmcp trust revoke <absolute path>`. To see every approval, run
`pmcp trust list`.

If your project policy granted something, move the grant into
`~/.claude/gateway-policy.yaml`. The project file can keep its denials.

<!-- snippet: policy -->
```yaml
# ~/.claude/gateway-policy.yaml -- grants live here now
servers:
  allowlist: ["github", "filesystem"]
```

In CI or other automation where nobody can approve files, pass the files
explicitly. Explicit paths need no approval:

```bash
pmcp --config /path/to/repo/.mcp.json --policy /path/to/repo/.mcp-gateway-policy.yaml
```

**How to verify.** Run `pmcp trust list`. Each approved path appears as
`approved  <digest>  <time>  <path>`. In the checkout, `pmcp config status`
lists the project's servers (for example `echo: lazy (project)`) and shows no
`project_source_not_approved`. After you restart the gateway, `pmcp status`
lists them and the log has no `Ignoring project` line for the file.

*If `pmcp trust approve` fails with `Trust store … resolves inside the
checkout at …`*, then `~/.config/pmcp` is inside a git checkout (often because
it is a symlink into a dotfiles repository) and you ran the command from inside
that checkout. pmcp refuses a store that a repository could ship itself. Move
`~/.config/pmcp` out of the checkout, or run the command from outside it.

### `PMCP_MANIFEST_PATH`, `PMCP_CONFIG` and `PMCP_POLICY` must be exported in your shell

**Am I affected?** You are if any of the three variables is set in a dotenv
file instead of your shell or service definition:

```bash
grep -nE '^(export )?PMCP_(MANIFEST_PATH|CONFIG|POLICY)=' .env .env.pmcp ~/.env ~/.config/pmcp/pmcp.env 2>/dev/null
```

After upgrading, pmcp logs this line for each one and behaves as if the
variable were unset:

```text
Ignoring PMCP_CONFIG=/path/to/config.json: it was set by a project file (.env or .env.pmcp) rather than exported in the operator's environment, so pmcp will not let a checkout redirect itself through it.
```

The gateway checks these at startup and prints the result on stderr, so for a
service look in `journalctl --user -u pmcp`. `PMCP_MANIFEST_PATH`, and
`PMCP_CONFIG` as read by `pmcp status`, are logged at WARNING in
`gateway.log` too.

**What changed.** These three variables choose which manifest, config and
policy pmcp uses. pmcp now honours them only when they came from your own
environment. A value loaded from `.env`, `.env.pmcp` or
`~/.config/pmcp/pmcp.env` is ignored, so a checkout cannot redirect pmcp
through a dotenv file.

A checkout's `.env` or `.env.pmcp` no longer sets any variable at all in
pmcp's environment; see [A project file supplies credentials
only](#a-project-file-supplies-credentials-only).

**What to do.** Export them in the environment that starts pmcp, or pass
the equivalent flags:

```bash
export PMCP_POLICY=~/.claude/strict-policy.yaml
pmcp
# or
pmcp --policy ~/.claude/strict-policy.yaml --config ~/work/mcp.json
```

For a systemd user service, `Environment=` and `EnvironmentFile=` both count
as exported, because systemd sets them before pmcp starts:

```ini
[Service]
Environment=PMCP_POLICY=%h/.claude/strict-policy.yaml
```

Then delete the lines from the dotenv file so they don't mislead the next
reader. There is no manifest flag, so `PMCP_MANIFEST_PATH` has to be exported.

**How to verify.** Restart the gateway. Startup prints no `Ignoring PMCP_`
line, and with `PMCP_POLICY` the log shows `Loaded policy from <your path>`.

### Spawned servers no longer inherit the keys pmcp loaded from `.env`

**Am I affected?** You are if a server you run through pmcp read a variable
that you put in a `.env` file instead of exporting it. Two `.env` files are
involved:

- the `.env` that python-dotenv finds by walking up from the directory pmcp is
  *installed* in. For a `uv tool` or `pip --user` install this is usually
  `~/.env`. For an editable install from a checkout it is that checkout's
  `.env`;
- the `.env` at the project root (`--project`, else the root found from the
  working directory), which pmcp reads -- into its credential map, never its
  environment -- when it looks up a credential for that project.

```bash
ls -l ~/.env "$PWD/.env" 2>/dev/null
```

Symptoms after upgrading: a server that worked now fails with an
authentication or "missing variable" error, even though the key is in `.env`.

**What changed.** A `~/.env` (in your home directory or an ancestor of it) still
loads into pmcp's own environment, and pmcp now strips every key it loaded that
way from the environment of the servers it spawns. Any other `.env` -- a
project's, or a checkout's that the install walk reaches -- never enters pmcp's
environment at all: its values stay in pmcp's credential map, where only a
credential lookup for that project reads them, so no spawned server inherits
them. A server still gets **its own declared `env_var`**: the variable a
manifest entry names as its credential, resolved from `.env` if necessary.
Variables you export in your shell are still inherited, deliberately.
Credentials stored with `pmcp secrets set` or `gateway.auth_connect` reach only
the server they belong to, as in 2.7.3. That now also holds for an install
spawn when the gateway runs with `--project <dir>` from another directory.
2.7.3 looked for the project credential store under the working directory,
so the install child could inherit another server's credential from
`<dir>/.env.pmcp`.

**What to do.** Pick whichever of these fits the variable:

- Export it in the shell or service that starts pmcp (`export FOO_API_KEY=...`,
  or `Environment=`/`EnvironmentFile=` in the unit).
- Give it to the one server that needs it, in that server's `env` block in a
  user-scoped `~/.mcp.json`, which is not committed and needs no approval. The
  value is passed literally, because pmcp does not expand `${VAR}` in a stdio
  server's `env`:

  <!-- snippet: mcp-json -->
  ```json
  {
    "mcpServers": {
      "internal-search": {
        "command": "npx",
        "args": ["-y", "@acme/search-mcp@2.1.0"],
        "env": {"ACME_SEARCH_API_KEY": "replace-with-the-real-key"}
      }
    }
  }
  ```

- For a server pmcp ships in its manifest, store the credential under the name
  the manifest declares. You are prompted for the value:
  `pmcp secrets set FIRECRAWL_API_KEY --scope user`.

**How to verify.** Add a throwaway server to the `mcpServers` of your
`~/.mcp.json` that writes down the names of the variables it receives:

<!-- snippet: mcp-json -->
```json
{"mcpServers": {"envcheck": {"command": "/bin/sh", "args": ["-c", "env | cut -d= -f1 | sort > /tmp/pmcp-envcheck.txt"]}}}
```

Then make the gateway start it with the gateway's own environment. Don't use
`pmcp status --probe` while a gateway is running: `pmcp status` then asks the
running gateway, which has not loaded `envcheck`, and nothing is written.

- **Gateway running as a service.** Mark `envcheck` to start with the gateway,
  restart the service, and read the file:

  ```bash
  pmcp config set-startup-policy add envcheck --source user --apply
  systemctl --user restart pmcp
  sleep 5; grep -c . /tmp/pmcp-envcheck.txt && grep -E 'YOUR_KEY_NAME' /tmp/pmcp-envcheck.txt
  ```

  If `~/.mcp.json` is a symlink, add `--path "$(readlink -f ~/.mcp.json)"`
  instead of `--source user` (see
  [A symlinked `.mcp.json` is no longer edited](#a-symlinked-mcpjson-is-no-longer-edited)).
- **No service.** Stop any gateway you started by hand (Ctrl-C in its
  terminal, or `kill` its process), then probe from the shell you start pmcp
  from:

  ```bash
  pmcp status --probe --server envcheck     # reports "envcheck error (Server envcheck disconnected)"; that is expected
  grep -c . /tmp/pmcp-envcheck.txt && grep -E 'YOUR_KEY_NAME' /tmp/pmcp-envcheck.txt
  ```

A key you exported is in the list. A key that only `.env` sets is not.
Afterwards, run `pmcp config set-startup-policy remove envcheck --source user --apply`,
delete the `envcheck` entry and restart the gateway.

### A symlinked project `.env.pmcp` is refused

**Am I affected?** You are if a project's `.env.pmcp` is a symlink (to any
target, inside the project or out of it), a fifo or a socket, and you either
run `pmcp` (any command, the gateway included) from that project's directory
or store project credentials with `pmcp secrets set`,
`pmcp secrets sync --to-scope project` or `gateway.auth_connect` with
`scope="project"`. The same goes for a tenant store
`.pmcp/tenants/<id>/pmcp.env`, for a `.env` in the directory the gateway
runs from, and for a checkout's `.env` when pmcp is installed in that
checkout's `.venv` (`uv run pmcp`, `pip install -e .`):

```bash
ls -l .env.pmcp .env .pmcp/tenants/*/pmcp.env
```

**What changed.** pmcp no longer follows a symlink in a project's credential
store at all. These commands refuse a `.env.pmcp` that is a symlink, or that
sits below a symlinked directory, before reading or writing it, and report
`refusing to write .env.pmcp: it is a symlink`. 2.7.3 wrote your secrets
wherever the link pointed, and in a cloned repository the repository chooses
that target. Every `pmcp` command also loads the served project's `.env.pmcp`
(`--project`, else the project root found from the working directory) at
startup; it
now skips such a store, prints `pmcp: refusing to load .env.pmcp: it is a
symlink` on stderr and carries on without those keys, where 2.7.3 loaded
whatever the link pointed at. A `.env.pmcp` that is not a regular file is
refused with `refusing to write .env.pmcp: it is not a regular file` (and
skipped at startup) instead of hanging the command. Any other failure to read
or write the store, including bytes that are not UTF-8 and a `--project` path
the system cannot resolve, comes back as `"ok": false` instead of crashing the
command; when `pmcp secrets sync` only reads the project store, the refusal
says `refusing to read`. The user store `~/.config/pmcp/pmcp.env` still follows
its link.
Every other reader of a file the repository controls reads it the same way
(Consiliency/pmcp#367): remote-header `${VAR}` resolution (the gateway,
`pmcp status`, `pmcp doctor`), a tenant `.pmcp/tenants/<id>/pmcp.env`, the
gateway's credential check (which also reads the checkout's `.env`), spawn-time
env stripping, `pmcp secrets check`, and a `.env` pmcp finds at startup by
walking up from where it is installed, unless that file is in your home
directory or above it. A store any of them refuses counts as empty, so a
`${VAR}` header it used to fill is reported missing, and pmcp prints
`pmcp: refusing to read .env.pmcp: it is a symlink` once per store in each
configuration load; feedback submission is refused instead. A tenant id made
only of dots (`.`, `..`) is refused. A store behind a directory pmcp cannot
search is unreadable, not absent: pmcp warns and goes on without it, and
feedback submission and `pmcp secrets set` refuse. These protections
are about what a repository ships; a process already running as you that
rewrites the store's directory while a command runs is out of scope.

**What to do.** Keep credentials you link from elsewhere, such as a shared
secrets file the gateway used to load, in your user store, then remove the
project link:

```bash
pmcp secrets set FIRECRAWL_API_KEY --scope user
```

If you want a project store, make `.env.pmcp` a regular file. If you didn't
create the link, the repository did: delete it and don't store project secrets
there.

**How to verify.** `pmcp secrets set <KEY> --scope project` stores the value
and reports success, or the key shows up under `--scope user`. `ls -l
.env.pmcp` shows a regular file, or nothing.

### A project file supplies credentials only

**Am I affected?** You are if a checkout's `.env.pmcp`, the `.env` in the
directory pmcp runs from, or a tenant `pmcp.env` sets anything other than a
credential a server or a remote header uses — for example `PMCP_LOG_LEVEL`,
`PMCP_PORT`, a proxy, `NODE_OPTIONS` — or if you relied on a project file
winning over `~/.config/pmcp/pmcp.env`:

```bash
grep -nE '^(export )?[A-Za-z_][A-Za-z0-9_]*=' .env.pmcp .env 2>/dev/null | cut -d= -f1
```

pmcp prints a line like this, without the value, for each variable it reads
from its own environment that such a file sets:

```text
pmcp: Ignoring PMCP_LOG_LEVEL in .env.pmcp: a project file supplies credentials only, and this variable decides what pmcp or a program it starts loads, trusts or connects to, so export it in the shell that starts pmcp (or set it in ~/.config/pmcp/pmcp.env).
```

**What changed.** Values from those files never enter pmcp's environment
(Consiliency/pmcp#367). They are credentials: a server's declared credential,
a remote server's `${VAR}` header, the credential checks and `pmcp secrets`
still find them. pmcp's own settings, `HOME`, `XDG_*`, `PATH`, proxy and CA
variables in them are ignored, and no child process pmcp starts — a server,
an installer, `systemctl`, `pmcp upgrade` — sees any of their values unless
it is that server's own declared credential. A value in such a file is
expanded only from keys defined earlier in the same file: `X=${GITHUB_TOKEN}`
is ignored with `pmcp: Ignoring X in .env.pmcp: its value refers to a
variable the file does not define, ...`. A variable your shell exported, or
`~/.config/pmcp/pmcp.env` sets, now wins over the same name in a project
file, for every lookup. Every lookup follows one order, by presence: your
environment, your user store, then the project files -- a checkout `.env`
that pmcp's startup walk found before `.env.pmcp`; a tenant lookup checks the
tenant store before the project files, and with `include_process_env` off
reads the tenant store alone. Every reader -- the gateway, the provision
check, a server's credential, `pmcp secrets check`, `pmcp doctor` -- reads
the same values through the same lookup, so the diagnostics answer what the
gateway will do. Project files are read per project: a lookup for one
project never uses another project's `.env` or `.env.pmcp`, and a changed
project file is read again on the next lookup (your user store, which is in
the environment, is read at startup). The project is the one pmcp serves:
`--project` when given, otherwise the project root found from the working
directory -- the root `.mcp.json` is loaded from. So `pmcp --project B` started
inside project A gives B's servers B's credentials -- and B's `.mcp.json`,
`.pmcp/manifest.yaml` overlay and `.mcp-gateway-policy.yaml`: every project
input follows the same project, so an endpoint and its credential always come
from one project. A project overlay is found at the project root, no longer by
walking up from the working directory (a directory holding one counts as a
project root), and the project policy is read from the project root, no longer
from the working directory; `pmcp init` without `--project` writes the served
project's `.mcp.json`. One exception, unchanged from 2.7.3: a local server
starts in pmcp's own working directory unless its config sets `cwd`, so with
`--project B` started from A, B's servers start in A and see A's `./.env`,
`.npmrc` and `node_modules`. Set `cwd` in B's server configs, or start pmcp
from B, until that changes. And pmcp started from a
subdirectory of a project reads the root's `.env.pmcp` (the file `pmcp secrets
set --scope project` writes) where 2.7.3 loaded the working directory's. If you
kept a `.env.pmcp` in a subdirectory you start pmcp from, move its entries to
the project root's `.env.pmcp`, or pass that subdirectory as `--project`. A
server pmcp spawns keeps every variable you exported: a project `.env.pmcp`
that lists a name such as `NO_PROXY` no longer removes your value of it from
the server's environment; only your user store's names, and credentials pmcp
itself put into its environment, are withheld from other servers. In 2.7.3 a checkout's `.env` won over the user
store, and `pmcp secrets check` preferred a project `.env.pmcp`. (Remote
`${VAR}` headers in the gateway, `pmcp status` and `pmcp doctor` already
preferred your user store, which those load first.) So a per-project override
of a key that is also in your user store -- a `TENANT_CODE_MODE_TENANT_ID` set
with `pmcp secrets set --scope project` in each project, say -- does not
override it. A variable your shell exports as empty stays unavailable.

`pmcp secrets sync --from-scope project --to-scope user` now copies
credentials only: a credential-shaped name (`*_TOKEN`, `*_KEY`, `*_SECRET`,
`*_PASSWORD` and the like) or one a manifest server declares as its
credential (`POSTGRES_URL`). Any other name -- `UV_INDEX_URL`, `DATABASE_URL`,
anything named `PMCP_*`, proxies, code-loading and package-manager names -- and
any value containing `${` or spanning lines is skipped with
`pmcp: Not copying <NAME> from .env.pmcp: ...` and listed under `"refused"`;
the rest still sync. If you meant one, set it yourself:
`pmcp secrets set DATABASE_URL --scope user`. `gateway.auth_connect` accepts
only a server's declared credential or a credential-shaped name, never one of
those names, and refuses a credential containing `${`. `pmcp secrets check`
now answers what a running pmcp would do: a variable your shell exports
counts as available, and one it exports as empty counts as missing even when
a store has it. Lookups that hand a
project value only to the server the project configured -- a remote header,
a server's declared credential -- keep the narrower rule: they refuse
pmcp's own, proxy, code-loading and package-manager names. A `.env` that pmcp's startup walk
finds in your home directory or an ancestor of it, such as `~/.env` for a
`uv tool` install, is yours and loads as before; one anywhere else, such as
a checkout with pmcp in its `.venv`, is a project file. A local server's `env` in `.mcp.json` is passed
as written, as before: `${VAR}` there was never expanded.

**What to do.** Export pmcp's own settings in the shell or service that
starts pmcp, or put them in your user store:

```bash
pmcp secrets set PMCP_LOG_LEVEL --scope user
```

If you relied on a project file overriding a user-store key, keep that key
only in the project stores (remove it from the user store), or export the
per-project value in the shell that starts pmcp for that project:

```bash
grep -nE '^(export )?TENANT_CODE_MODE_TENANT_ID=' ~/.config/pmcp/pmcp.env
```

Keep credentials in the project file if you like; servers still get them. A
variable a server needs that is not its declared credential belongs in that
server's `env` block in `.mcp.json`.

**How to verify.** Run the grep above: every name it lists is either a
credential a server or a header uses, or one you have moved to your shell or
user store. pmcp prints an `Ignoring` line only for its own variables and for
values it will not expand; any other non-credential variable in a project
file is ignored without one. `pmcp secrets check` lists the project keys you
expect.

### Discovered servers are default-deny

**Am I affected?** You are if your agents use `gateway.search_registry` and
`gateway.register_discovered_server` to add servers that aren't in pmcp's
manifest or your `.mcp.json`. After upgrading, `gateway.provision`,
`gateway.connect_server` and `gateway.restart_server` refuse such a server:

```text
Refused to provision everything: its package has not been approved by an operator. To approve it, run: pmcp trust approve-package @modelcontextprotocol/server-everything@2026.8.31
```

**What changed.** Registration now resolves the package in the npm registry
and pins the exact version into both the install command and the server's
`args`. A package it cannot pin to one version is refused, and nothing is
stored. Provisioning a discovered server then needs one of two things from
the operator: an approval of that exact `name@version`, or a
`packages.allowlist` match in your policy. A `packages.denylist` match refuses
the server even when it is approved. `gateway.update_server` refuses every
discovered server, because an update would fetch a version nobody approved. A
discovered server may declare only credential-shaped `env_vars`: names ending
in `_TOKEN`, `_KEY`, `_SECRET(S)`, `_PASSWORD`, `_CREDENTIAL(S)`, `_PAT`,
`_DSN` or `_AUTH`, and never `NPM_CONFIG_*`, `NODE_*`, `COREPACK_*`, `YARN_*`,
`PNPM_*` or `BUN_*`. Servers from the manifest and from `.mcp.json` need no
approval. Discovered registrations were never kept across a restart, so you
have nothing stored to migrate.

**What to do.** Approve the exact version the refusal prints:

```bash
pmcp trust approve-package @modelcontextprotocol/server-everything@2026.8.31
pmcp trust list-packages
```

A range or a dist-tag is refused (`'some-mcp@latest' does not name one exact
version …`). Each new version needs its own approval. To move a discovered
server to a newer version, have the agent call
`gateway.register_discovered_server` again (it resolves and pins the current
version), approve the version it prints, and connect it. Withdraw an approval
with `pmcp trust revoke-package <name>@<version>`, or drop every version with
`pmcp trust revoke-package <name>`.

To allow a whole family of packages without approving each version, add a
name-glob allowlist to **your** policy (see the next section):

<!-- snippet: policy -->
```yaml
# ~/.claude/gateway-policy.yaml
packages:
  allowlist: ["@modelcontextprotocol/*"]
```

A server that needs a non-credential variable (for example a database URL)
should be configured in `~/.mcp.json`, not registered.

**How to verify.** `gateway.provision` for the server returns `"ok": true`
with a `job_id`. `pmcp trust list-packages` shows
`approved  <time>  npm:<name>@<version>  …`.

### New `packages:` policy section

**Am I affected?** You are if you want to control which npm packages pmcp may
run, or if you share one policy file between 2.7.3 and 3.0 gateways. 2.7.3
**refuses to start** on a policy that has a `packages:` key. See
[Rolling back](#rolling-back-to-273).

**What changed.** The gateway policy has a new `packages:` section with
`allowlist` and `denylist` globs that match the package **name** only.

- An allowlist match lets a *discovered* package provision without an
  approval.
- A denylist match refuses it even when it is approved. It also refuses a
  manifest server whose npm package it names, in `provision`,
  `connect_server`, `restart_server` and `update_server`, with
  `auth_state: "policy_denied"`.
- While any denylist is in force, a manifest entry whose npx packages pmcp
  cannot determine is refused the same way. Such an entry has an npx option
  pmcp cannot interpret (for example `--registry`) or a selected package that
  is not a plain npm spec.
- `.mcp.json` servers are not checked against these lists.
- A project policy can add denials, but its allowlist grants nothing on its
  own.
- An entry that names a version, such as `evil-pkg@1.2.3`, makes the whole
  policy file invalid, and pmcp refuses to start rather than run without it.

**What to do.** Write name globs only:

<!-- snippet: policy -->
```yaml
packages:
  allowlist: ["@acme/*", "*-mcp"]
  denylist: ["firecrawl-mcp", "@untrusted/*"]
```

Not this: the version-bearing entry below makes the file invalid.

<!-- snippet: policy-invalid -->
```yaml
packages:
  denylist: ["evil-pkg@1.2.3"]
```

**How to verify.** Load the file explicitly and read the first lines:

```bash
pmcp status --policy ~/.claude/gateway-policy.yaml 2>&1 | head -n 3
```

An invalid file fails with `Fatal error: Failed to load explicit policy …` and
names the bad entry (`package pattern 'evil-pkg@1.2.3' names a version`). Pass
`--policy` after `status`: the gateway's own `--policy` before a subcommand
does not reach `status`. Once the file
is valid, provisioning a denied manifest server returns `"auth_state":
"policy_denied"` with a message naming `packages.denylist`.

### Feedback submission is off by default

**Am I affected?** You are if your agents filed pmcp feedback issues through
`gateway.submit_feedback` with `confirm_submission=true`, using `GITHUB_TOKEN`,
`GH_TOKEN`, the `gh` CLI or `PMCP_FEEDBACK_TOKEN`, or if you set
`PMCP_FEEDBACK_REPO` in a `.env`.

**What changed.** pmcp posts an issue only when all of these hold:

- the operator has run `pmcp guidance --feedback-submission on`. This writes
  `guidance.enable_feedback_submission: true` to
  `~/.claude/gateway-guidance.yaml`;
- `PMCP_FEEDBACK_TOKEN` is exported in the shell that starts pmcp. A value
  pmcp loaded from `.env`, `.env.pmcp` or `~/.config/pmcp/pmcp.env` is
  refused;
- the call passes `confirm_submission=true`.

Otherwise the call returns a preview with a browser URL and sends nothing.
`GITHUB_TOKEN`, `GH_TOKEN` and the `gh issue create` fallback are gone. A
`PMCP_FEEDBACK_REPO` set by a checkout's `.env` is refused. The default
repository is `Consiliency/pmcp` (it was `ViperJuice/pmcp`). A preview reports
`repository_visibility: "unknown"`. The output gains `submission_outcome`:
`created`, `refused`, `not_dispatched` or `dispatched_unconfirmed`, or `null`
on a preview. `dispatched_unconfirmed` means **the issue may exist**: check
before filing it again.

**What to do.** If you want pmcp to post, turn it on and export a token. A
fine-grained GitHub token with issue write access on one repository is
enough.

```bash
pmcp guidance --feedback-submission on
export PMCP_FEEDBACK_TOKEN=github_pat_replace_me
pmcp
```

To turn it off again, run `pmcp guidance --feedback-submission off`. If you
leave it off, nothing else is needed. Agents get a preview and a URL to file
by hand.

<!-- snippet: guidance -->
```yaml
# ~/.claude/gateway-guidance.yaml after the command above
guidance:
  enable_feedback_submission: true
```

**How to verify.** `pmcp guidance` prints `Feedback Submission: ✓`. Before you
export the token, a confirmed call returns `"submitted": false` and a message
naming `PMCP_FEEDBACK_TOKEN`. After you export it, the call reports
`submission_outcome: "created"` and an `issue_url`.

### Auth URLs must be canonical

**Am I affected?** You are if you run the HTTP transport with
`--auth-mode resource-server` (or `PMCP_AUTH_MODE=resource-server`) and your
`--oauth-jwks-url` / `PMCP_OAUTH_JWKS_URL` is not in canonical form. You are
also affected if you call `create_http_app` with a
`protected_resource_metadata_url`, or a downstream server sends URL-mode
elicitation URLs. With a bad JWKS URL, pmcp now refuses to start:

```text
Error: Public auth URL host must be a DNS name of dot-separated labels of 1 to 63 ASCII letters, digits and hyphens, each starting and ending with a letter or digit, the last starting with a letter, so never a number (an IDN in its xn-- form); a dotted-quad IPv4 address with no leading zeros; or a bracketed IPv6 address with no zone id.
```

**What changed.** pmcp refuses an auth URL instead of rewriting it. The host
must be one of three forms:

- an ASCII DNS name whose last label starts with a letter;
- a dotted-quad IPv4 address with no leading zeros;
- a bracketed IPv6 address with no zone id.

A control character or a backslash anywhere in the URL is refused. An IPv6
host must be public, and so must any IPv4 address embedded in it. In
resource-server mode, pmcp refuses to start on an unusable JWKS URL or
required scope. `create_http_app` refuses a bad metadata URL instead of
quietly dropping the metadata route. Plain `http://` was already refused for
these URLs; only its message changed.

**What to do.** Rewrite the URL. Each row below was run through pmcp's
validator on 2.7.3 and on 3.0:

| 2.7.3 accepted | 3.0 says | Use instead |
|---|---|---|
| `https://bücher.example/jwks.json` (IDN) | not canonical | `https://xn--bcher-kva.example/jwks.json`. Convert with `python3 -c 'import sys; print(sys.argv[1].encode("idna").decode())' bücher.example` |
| `https://ＡＵＴＨ.example.com/…` (full-width letters) | not canonical | `https://auth.example.com/…` |
| `https://auth.example.com./jwks.json` (trailing dot) | not canonical | `https://auth.example.com/jwks.json` |
| `https://auth_server.example.com/…` (underscore) | not canonical | a hostname without `_`, for example a DNS alias such as `auth-server.example.com` |
| `https://134744072/jwks.json` (legacy numeric IPv4) | not canonical | `https://8.8.8.8/jwks.json`. Convert with `python3 -c 'import ipaddress, sys; print(ipaddress.IPv4Address(int(sys.argv[1], 0)))' 134744072` (also handles `0x…`) |
| `https://example.123/…` (numeric last label) | not canonical | a real DNS name or a dotted-quad address |
| `https://[2606:4700:4700::1111%25eth0]/…` (IPv6 zone id) | not canonical | `https://[2606:4700:4700::1111]/…` |
| `https://auth.example.com\@evil.example/…` (backslash; 2.7.3 stored it as `https://evil.example/…`) | `Public auth URL contains a backslash.` | write the URL you mean. In a path, percent-encode it as `%5C` |
| `https://auth.example.com/key<TAB>set.json` (control character; 2.7.3 deleted it silently) | `Public auth URL contains a control character.` | `https://auth.example.com/keyset.json` |
| `http://auth.example.com/…` | `Plain http:// is not accepted for this public auth URL.` (2.7.3: `…only allows http:// URLs for loopback hosts.`) | `https://…` |

Uppercase letters are still accepted and lowercased, and an explicit port
such as `:8443` is still fine.

**How to verify.** Start the gateway with the corrected URL:

```bash
pmcp --transport http --auth-mode resource-server --oauth-issuer https://auth.example.com --oauth-audience https://pmcp.example.com/mcp --oauth-jwks-url https://auth.example.com/.well-known/jwks.json
```

It keeps running, `curl -s http://127.0.0.1:3344/health` answers, and a
request to `/mcp` without a token gets `401` with
a `WWW-Authenticate` header carrying
`resource="https://pmcp.example.com/mcp"`.

### Auth responses changed

**Am I affected?** You are if a client, proxy, alert or test matches pmcp's
401/503 bodies or `error_description` text, alerts on `500`s from a
resource-server gateway, or reads `resource` from
`/.well-known/oauth-protected-resource`.

```bash
grep -rnE 'Missing bearer token|Unsupported token algorithm|shared-secret auth mode requires auth_token|only allows http:// URLs for loopback' /path/to/your/monitoring /path/to/your/tests
```

**What changed.**

- Reworded: `Missing bearer token.` → `Empty token.`; `Unsupported token
  algorithm.` → `The token's algorithm is not supported.`; the shared-secret
  startup error `shared-secret auth mode requires auth_token.` → `auth_token
  is required when auth_mode is shared-secret.`; the plain-`http://` refusal
  (see above). Every fixed auth message now comes from one registry,
  `pmcp.auth.AuthMessage`.
- An unknown `kid` refetches the JWKS at most once per 10 s. Within that
  window it gets a `401`.
- A forged token that pairs an algorithm with a key of another type, and an
  `Authorization` header containing non-ASCII bytes, now get a `401` instead
  of a `500`. Token errors whose library text could quote the token back get
  a fixed description.
- Any JWKS failure, including a key set with no usable keys, is a `503`
  (`error="temporarily_unavailable"`) instead of a `500`. A failed fetch backs
  off for 5 s, shared by every waiting request, and each fetch has a 5 s
  timeout.
- The metadata route's `resource` is `--oauth-audience`, or the metadata URL's
  origin plus `/mcp`. It no longer echoes the request `Host`. The CLI gateway
  doesn't serve `/.well-known/oauth-protected-resource` (a request gets
  `Not Found`). Only code that passes `protected_resource_metadata_url` to
  `create_http_app` serves that route and sees this change.

**What to do.** Match the new strings, or better, match the status code and
the `error` parameter of `WWW-Authenticate`. Stop alerting on the `500`s a
malformed token used to cause. Treat `503` from the auth layer
as "the issuer's key set is unavailable", not as a pmcp crash. Behind a
reverse proxy, set `--oauth-audience` to the public URL clients use (for
example `https://pmcp.example.com/mcp`), because the metadata no longer
follows the `Host` header.

**How to verify.**
`pmcp --transport http --auth-mode shared-secret` without a token exits with
`Fatal error: auth_token is required when auth_mode is shared-secret.`. A
request without a token in resource-server mode gets a `401` whose challenge
carries your audience as `resource`.

### The `tools/call` gate enforces the schemas pmcp advertises

**Am I affected?** You are if you drive pmcp's own `gateway.*` tools from code
or a custom client instead of an LLM. That covers sending `"1"`/`1` where a
boolean or integer is advertised, sending empty strings for identifiers,
parsing `{"error": true}` payloads, or reading the scoped-advisor audit
(`--audit-jsonl`).

**What changed.** pmcp's gateway tool schemas are now generated from the
argument models, and the `tools/call` gate enforces them. A call that breaks a
constraint the model always had comes back as an MCP **`isError: true`**
result reading `Input validation error: …`. In 2.7.3 the handler answered
with an `{"error": true, …}` JSON payload instead. Some cases checked on 3.0:

| Call | 2.7.3 | 3.0 |
|---|---|---|
| `gateway.invoke` with `task: {"enabled": 1}` | accepted, coerced to `true` | `Input validation error: 1 is not of type 'boolean'` |
| `gateway.invoke` with `task: {"enabled": true, "ttl": "5"}` | accepted, coerced to `5` | `Input validation error: '5' is not of type 'integer', 'null'` |
| `gateway.describe` with `tool_id: ""` | reached the handler | `Input validation error: '' should be non-empty` |
| `gateway.submit_feedback` with a 5-character title | reached the handler | `Input validation error: 'short' is too short` (titles are 8–160 characters) |
| `gateway.catalog_search` with `query: null` | `Input validation error: None is not of type 'string'` | accepted |

<!-- gate-case: {"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": 1}}} => 1 is not of type 'boolean' -->
<!-- gate-case: {"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": true, "ttl": "5"}}} => '5' is not of type 'integer', 'null' -->
<!-- gate-case: {"name": "gateway.describe", "arguments": {"tool_id": ""}} => '' should be non-empty -->
<!-- gate-case: {"name": "gateway.submit_feedback", "arguments": {"title": "short", "description": "x"}} => 'short' is too short -->
<!-- gate-case: {"name": "gateway.catalog_search", "arguments": {"query": null}} => accepted -->
<!-- gate-case: {"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": true, "ttl": 0}}} => 0 is less than the minimum of 1 -->
<!-- gate-case: {"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": true, "poll_interval": NaN}}} => nan is not of type 'number', 'null' -->
<!-- gate-case: {"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": true, "ttl": 300}}} => accepted -->

An explicit `null` for any optional argument is now accepted; 28 arguments
used to reject it. Policy is judged before the schema, so a blocked tool gets
`Gateway tool blocked by policy` whatever its arguments. Unknown keys are
still accepted and ignored. A gate rejection is written to the scoped-advisor
audit as an `audit.rejection` event with `terminal_status:
"invalid_arguments"`, `rejected_argument_path` and
`rejected_argument_validator`. It carries no values from the call's arguments.
In 2.7.3 these calls were recorded as `audit.invocation` with `failure`.

`audit.invocation` records change too:

- A call refused by policy, or made to a tool name pmcp doesn't have, is
  recorded `denied`, and every field taken from the arguments is `null`:
  `run_correlation_id`, `seat_correlation_id`, `downstream_tool_id`,
  `evidence_label_digest` and `source_reference_hash`. 2.7.3 copied a
  correlation id from such a call's arguments into the record.
- For an unregistered name, `gateway_tool_digest` is now the digest of
  nothing rather than of the caller's string, and the result digest no longer
  covers the caller's tool name.
- Every other record reads only the top-level arguments that tool's schema
  declares. For example, a `run_correlation_id` passed to `gateway.describe`
  is no longer recorded. `gateway.invoke` declares every field the record
  reads, so its records are unchanged.

**What to do.** Send real JSON types: `true`/`false`, numbers rather than
numeric strings, and non-empty identifiers. Check `result.isError` before
parsing the text. If you read the audit, handle `"event": "audit.rejection"`,
or dispatch on `event` and skip it. If you correlate audit records by
`run_correlation_id`, pass it to `gateway.invoke` (which declares it); on
other tools and on denied calls it is now `null`. Here is a call the gate
accepts:

<!-- snippet: tools-call-accepted -->
```json
{"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {"query": "pmcp"}, "options": null, "task": {"enabled": true, "ttl": 300}}}
```

and the shape it now refuses:

<!-- snippet: tools-call-rejected reason="1 is not of type 'boolean'" path="task.enabled" -->
```json
{"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": 1, "ttl": 300}}}
```

**How to verify.** Replay your client's calls. None should come back as
`isError` with `Input validation error`. With `--audit-jsonl`, the audit
file shows `audit.rejection` lines only for calls you meant to be invalid,
and `run_correlation_id` is set only on `gateway.invoke` records.

### Task numbers are bounded

**Am I affected?** You are if you call `gateway.invoke` with a `task` object,
or use a downstream server that supports MCP tasks.

**What changed.** `task.ttl` must be an integer number of seconds from 1 to
9,007,199,254,740, and `task.poll_interval` a finite number of seconds above 0
and at most 9,007,199,254,740 (so the milliseconds pmcp sends stay within
2^53−1; see the next section). Zero,
negative values, `NaN` and `±Infinity` are refused at the gate:
`Input validation error: 0 is less than the minimum of 1`, or
`nan is not of type 'number', 'null'`. Both transports can deliver `NaN` and
`Infinity`, and the gate treats them as non-numbers for every numeric
argument. pmcp no longer sends a downstream anything that is not strict JSON:
a request whose `arguments` contain `NaN` fails with `outbound frame is not
strict JSON`. A downstream task field pmcp cannot use (`ttl`, `poll_interval`,
`created_at`, `updated_at` or `status`) is reported as `null` and named in the
task's new `unusable_fields` array. 2.7.3 coerced or stored such values.
Finished tasks beyond the 100-record cap are evicted in the order pmcp
recorded them, no longer by the downstream's own timestamps.

**What to do.** Send positive values, in seconds. Don't put `NaN` or `Infinity` in tool arguments. If you read task results,
treat a `null` field listed in `unusable_fields` as "the server sent
something unusable". A `null` `ttl` that is *not* listed there still means
"unlimited".

**How to verify.** A task call with `"ttl": 300` is accepted, one with
`"ttl": 0` returns `isError` with `Input validation error`, and one with
`"ttl": 9007199254741` returns
`Input validation error: 9007199254741 is greater than the maximum of 9007199254740`.

<!-- gate-case: {"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": true, "ttl": 9007199254741}}} => 9007199254741 is greater than the maximum of 9007199254740 -->

### Task `ttl` and `poll_interval` are seconds in pmcp and milliseconds on the wire

**Am I affected?** You are if you call `gateway.invoke` with `task.ttl` or
`task.poll_interval`, read `ttl`/`poll_interval` from the tasks pmcp returns,
or run a downstream server that supports MCP tasks. Look for task values in
your callers and in what your servers expect:

```bash
grep -rnE '"?(ttl|poll_interval)"?[:=] *[0-9]{4,}' /path/to/your/callers
```

A `ttl` of 1000 or more is often milliseconds sent to work around 2.7.3.

**What changed.** pmcp's own interface stays in **seconds**, and pmcp now
converts at the boundary, because MCP 2025-11-25 defines task `ttl` and
`pollInterval` in **milliseconds**. 2.7.3 passed the number through, so
`ttl: 300` (five minutes in pmcp's docs) gave a spec-conforming server 300 ms.
Checked with a fake task-capable stdio server, calling `gateway.invoke` with
`task: {"ttl": 300, "poll_interval": 2.5}`:

| | 2.7.3 | 3.0 |
|---|---|---|
| sent downstream | `"task": {"ttl": 300, "pollInterval": 2.5}` | `"task": {"ttl": 300000, "pollInterval": 2500.0}` |
| task pmcp returns, for a downstream that echoes `ttl` and sends `pollInterval: 2500` | `ttl: 300`, `poll_interval: 2500.0` | `ttl: 300.0`, `poll_interval: 2.5` |

Every task pmcp returns (`gateway.invoke`'s `task`, `gateway.tasks_list`,
`gateway.tasks_get`, `gateway.tasks_result`, `gateway.tasks_cancel`) reports
`ttl` and `poll_interval` in seconds, read from the downstream's milliseconds
(`pollInterval`, or the `poll_interval` alias). `ttl` may now be fractional:
1500 ms is reported as `1.5`. A task's `raw` object and relayed results keep
the downstream's own milliseconds. `gateway.invoke` also recognises a task
the downstream returns at the top level of its reply (`{"taskId": …}`).

**What to do.**
- **If you sent milliseconds to work around 2.7.3, send seconds now.** A
  `ttl: 300000` that meant five minutes now asks for 300,000 seconds, about
  3.5 days. Divide by 1000: `ttl: 300`. The same applies to `poll_interval`.
- **If you run a tenant server built to pmcp's earlier tenant contract**
  (`ttl` in seconds), it now receives milliseconds: a caller's `ttl: 300`
  arrives as `ttl: 300000`. Read `ttl` as milliseconds, and return `ttl` and
  `pollInterval` (or `poll_interval`) in milliseconds, or pmcp reports them
  1000× too small. See `specs/tenant-code-mode-host-contract.md`.
- If you read task results, expect `ttl` as a number of seconds that may be
  fractional, not an integer of milliseconds.

**How to verify.** Point a task call at a server you control, or the fake
server pattern above, and log what it receives: `task: {"ttl": 300}` arrives
as `"ttl": 300000`. The task pmcp returns shows `ttl: 300.0`, and the task
outlives 0.3 s.

### Redaction removes more

**Am I affected?** You are if you read tool output, logs or auth diagnostics
that pass through pmcp's redaction and expect long random-looking strings to
survive. Typical examples are API object IDs in base62 or mixed case, signed
URLs, and URLs with credentials.

**What changed.** `sanitize_auth_diagnostic`, `PolicyManager.redact_secrets`
and `process_output` (which handles every downstream tool result) run new
rules on top of the old ones, and the new rules can only remove more. They
replace these with `[REDACTED]`:

- vendor token shapes (AWS access key IDs, Slack, Google, and `sk-`/`ghp_`/`glpat-`-style prefixes);
- JWTs and PEM private-key blocks;
- high-entropy runs;
- values under a wider set of secret-like keys;
- URL userinfo and fragments, and secret-keyed query values.

The markers that existed before are unchanged, and a JSON document stays valid
because only string values are touched. In our checks, 40-character hex
commit SHAs, UUIDs and file paths were left alone, and a 39-character
mixed-case alphanumeric ID was redacted.

**What to do.** There is no switch to turn the new rules off. If an agent
needs an identifier that is now redacted, have the downstream tool return it
in a field the agent can use without pmcp's output processing, for example a
file the tool writes. Or fetch it outside pmcp. Treat `[REDACTED]` as a value
pmcp withheld, not as data.

**How to verify.** Compare a known tool result before and after the upgrade.
Only secret-shaped substrings should differ.

### Manifest version pins

**Am I affected?** You are if a manifest overlay (`~/.pmcp/manifest.yaml`, an
approved project `.pmcp/manifest.yaml` or `$PMCP_MANIFEST_PATH`) has a
`version:` key on a `servers:` entry or a top-level `server_version:` map.
2.7.3 ignored both:

```bash
grep -nE '^server_version:|^\s+version:' ~/.pmcp/manifest.yaml .pmcp/manifest.yaml 2>/dev/null
```

**What changed.** A pin now holds an npx-launched server at that exact
version. pmcp writes `name@version` into the server's `args` and every
`install` argv. An invalid pin is ignored with a WARNING that names the source
but not the value; invalid means a range, a dist-tag, build metadata, a
non-npx launcher or a remote server. `gateway.update_server` does not move a
pinned server, and `pmcp update` prints `[PINNED] <server>: pinned at
<version>` for it.

**What to do.** If a `version:` key in an overlay was only a comment, delete
it, or it now pins the server. To pin on purpose:

<!-- snippet: manifest-overlay -->
```yaml
# ~/.pmcp/manifest.yaml
server_version:
  firecrawl: "3.25.5"
```

To remove a pin, delete its line. `server_version: {firecrawl: null}` sets
nothing. For uvx, pip, cargo or docker servers, pin the version in that
server's own `command`/`args` in `.mcp.json`.

**How to verify.** `pmcp update firecrawl` (with the gateway running) prints
`[PINNED] firecrawl: pinned at 3.25.5`. The gateway log has no pin warning,
and the next spawn logs `Spawning firecrawl: npx -y firecrawl-mcp@3.25.5`.
`pmcp update` prints `[FAILED] … Could not determine a registry package`
instead when pmcp cannot see past npm configuration. That happens when the
entry sets an npm setting, when the gateway's environment sets any
`npm_config_*` or `NODE_OPTIONS`, or when a directory at or above the
gateway's working directory holds a `package.json` or `node_modules`. The pin
is still applied in those cases.

### An overlay entry pmcp cannot use is skipped

**Am I affected?** You are if you keep a manifest overlay
(`~/.pmcp/manifest.yaml`, an approved project `.pmcp/manifest.yaml` or
`$PMCP_MANIFEST_PATH`) and one of its entries has a field of the wrong type:
`keywords: [1]`, a number in `args` or `command`, a `transport` that is not a
string, or a `cli_alternatives` entry with an empty `check_command`. On 2.7.3
such an entry loaded, and then `gateway.catalog_search` failed: for every query
with `keywords: [1]` or an empty `check_command`, and for every query that
matched the entry with a non-string `transport`. A number in `args` or
`command` instead stopped startup and `gateway.refresh` for every server. You are also
affected if an overlay adds or replaces a `cli_alternatives` entry that you
expect `gateway.request_capability` to recommend.

**What changed.** Such an entry is skipped when the overlay is read, with a
WARNING that names the field but not its value, and not the entry's name
unless pmcp ships it: `Skipping invalid server entry (…) in overlay …` or
`Skipping invalid cli_alternative (…) in overlay …`. Every other entry and the
shipped manifest load as before. A blank field (`description:` with nothing
after it) means "not set" and takes its default. Discovery weighs keywords
from the shipped manifest alone, so an overlay server that shares a keyword no
longer hides another server. A CLI an overlay adds or replaces ranks after
every server and after pmcp's own CLIs: `gateway.request_capability`
recommends it only when no server matches, and `gateway.catalog_search` lists
it after pmcp's own CLIs in `cli_hints`. The warning for a `.mcp.json` entry
that would start pmcp itself now names the field (`command`, `args` or
`name`), not the command line, because that entry may have inherited its
command from an overlay.

**What to do.** Fix the field the WARNING names, or delete the entry. For
example, `keywords` and `args` are lists of strings:

<!-- snippet: manifest-overlay -->
```yaml
# ~/.pmcp/manifest.yaml
servers:
  inventory-tool:
    description: Internal inventory lookups
    keywords: [inventory, stock levels]
    command: npx
    args: ["-y", "inventory-tool-mcp@1.0.0"]
```

If you relied on an overlay CLI being recommended ahead of a server, run the
CLI directly; `gateway.catalog_search` still lists it in `cli_hints` when it
is installed.

**How to verify.** `pmcp config status` loads the manifest and its overlays
fresh, so it reports a skipped entry directly, with no log history:

```bash
pmcp config status 2>&1 | grep -c 'Skipping invalid '   # prints 0 once every entry loads
```

Run it from the directory the gateway runs in, so it reads the same project
overlay. The gateway's own log keeps the lines from earlier runs, so a count
there does not drop to 0 after a fix. After restarting the gateway,
`gateway.catalog_search` with `include_offline: true` and a keyword that only
your entry declares returns it: for the example above,
`{"query": "stock levels", "include_offline": true}` returns `inventory-tool`.
Without `include_offline: true`, `catalog_search` returns no manifest
candidates at all.

### Downstream servers see more from pmcp

**Am I affected?** You are if you maintain an MCP server that pmcp connects
to, especially one that sends requests to its client, relies on the
connection dropping, or ignores cancellation.

**What changed.** pmcp now answers a server→client request: `ping` gets an
empty result, and any other method gets `-32601` "Method not found". 2.7.3
dropped them. Cancellation is sent as `notifications/cancelled`: on
`gateway.cancel`, on an idle or ceiling timeout, and when the caller goes
away, once per cancellation and never for `initialize`. A malformed frame is
dropped and logged at debug level instead of ending the connection, on stdio,
SSE and streamable HTTP alike. A non-UTF-8 byte on stdout no longer drops a
stdio server either.

**What to do.** Nothing for most servers. If yours sends requests to the
client, handle the `-32601` error. If it runs long jobs, stop them when
`notifications/cancelled` arrives for their request id.

**How to verify.** Your server's log shows `ping` answered and cancellations
received. pmcp no longer reconnects after a single bad frame.

### Logs

**Am I affected?** You are if you alert on WARNING lines, ship the gateway
log somewhere with a volume budget, or grep the log for install commands.

**What changed.**

- Every install spawn, every start of a stdio server whose command is a
  package runner (`npx`, `uvx`, `pnpx`, `bunx`), and every update probe logs
  the command line at WARNING before it runs. The line is secret-safe: only
  the executable, `-y`/`--yes`/`--quiet`, `--registry` by name, and a pinned
  `name@version` are shown, and everything else is `<redacted>`. In 2.7.3,
  `install_server` logged the full argv, credentials included, at INFO.
- A manifest's warnings are logged once each time its inputs change, not on
  every load.

```text
[WARNING] Spawning pinned: npx -y @acme/files-mcp@1.2.3 <redacted> <redacted>
[WARNING] Spawning floating: npx -y <redacted>
```

The messages begin `Spawning <server>:`, `Starting install job <id> for
<server>:`, `Installing <server>:`, `Verifying installation of <server>:` and
`Running update probe:`.

**What to do.** If you alert on WARNING, exclude those five prefixes, or
alert on `ERROR` instead.
To see which package a manifest server runs, pin it (see above). An unpinned
package shows as `<redacted>`.

**How to verify.**
`grep -c 'WARNING\] Spawning ' ~/.pmcp/logs/gateway.log` counts one line per
package-runner start.

### Dependency floors

**Am I affected?** You are if you install pmcp into an environment where
other packages pin older versions of these:

| Package | 2.7.3 required | 3.0 requires |
|---|---|---|
| `pyjwt[crypto]` | `>=2.10.0` | `>=2.15.0` |
| `aiohttp` | `>=3.9.0` | `>=3.14.2` |
| `python-dotenv` | `>=1.0.0` | `>=1.2.2` |
| `starlette` (the `http` extra) | `>=0.27.0` | `>=1.3.1` |
| `pytest` (the `dev` extra) | `>=7.0` | `>=9.0.3`, plus `pytest-timeout>=2.3` |

**What changed.** The floors exclude versions with published advisories on
the auth path and its dependencies.

**What to do.** Install pmcp in its own environment (`uv tool install pmcp`)
so nothing else constrains it, or raise the conflicting pins.

**How to verify.** `pip check` (or `uv pip check`) in pmcp's environment
reports no broken requirements, and `pmcp --version` prints `pmcp 3.0.0`.

### Agent-facing hints

**Am I affected?** You are if you match pmcp's code hints or examples in
prompts, tests or tooling.

**What changed.** The `try/catch` code hint is now `try`; at the default
`max_hint_length` of 8 it used to be cut to `try/catc`. The Playwright
screenshot pattern and example now name the server's real tool and argument:
`playwright::browser_screenshot` → `playwright::browser_take_screenshot`, and
its `path` argument → `filename`. Tool and argument descriptions are clearer,
and the shipped snippets are valid Python.

**What to do.** Update any matcher that expects `try/catch` or
`playwright::browser_screenshot`.

**How to verify.** The shipped data is in the installed `pmcp` package:
`manifest/code_patterns.yaml` maps `playwright::browser_take_screenshot` to
`loop`, and `templates/code_examples.yaml` calls it with `filename`.

### A symlinked `.mcp.json` is no longer edited

**Am I affected?** You are if your `~/.mcp.json` (or a project or custom one)
is a symlink, for example into a dotfiles repository, and you change
`autoStart` with `pmcp config set-startup-policy` or the
`gateway.set_startup_policy` tool:

```bash
ls -l ~/.mcp.json ~/.claude/.mcp.json .mcp.json 2>/dev/null | grep -- '->'
```

**What changed.** pmcp refuses to rewrite a symlinked `.mcp.json`, including
in a preview, and reports
`invalid_source: symlinked_config: refusing to edit a symlinked .mcp.json at …`.
2.7.3 replaced your symlink with a regular file. The command still exits 0,
so scripts have to check its output, or `"ok": false` in `--json` output.

**What to do.** Edit the link's target directly, with `--path`:

```bash
pmcp config set-startup-policy add github --path "$(readlink -f ~/.mcp.json)" --apply
```

**How to verify.** The command prints `Startup policy updated.` with the
target as `Source:`, and `ls -l ~/.mcp.json` still shows the symlink.

### `NaN` from HTTP/SSE servers

**Am I affected?** You are if a downstream server you reach over HTTP or SSE
can put `NaN`, `Infinity` or `1e400` in its replies.

**What changed.** pmcp now reads HTTP and SSE replies exactly as sent, as it
already did for stdio. A `NaN` inside a tool result reaches the caller as a
`NaN` token instead of `null`
([Consiliency/pmcp#335](https://github.com/Consiliency/pmcp/issues/335)). A
`nextCursor` of `NaN` makes that listing unreadable, and pmcp keeps the
previous tools/resources/prompts instead of treating page one as complete.

**What to do.** If your client's JSON parser rejects `NaN`, parse leniently,
or fix the downstream server to send `null` or a string.

**How to verify.** A listing from that server stays stable across refreshes,
and your client parses its tool results.

### Error text names the real failure

**Am I affected?** You are if you match on `unhandled errors in a TaskGroup`
in monitoring or scripts.

**What changed.** Status, `doctor`, health output and the connect and
disconnect errors from a remote transport now name the exceptions inside an
exception group, for example
`ExceptionGroup(1 sub-exception): ConnectError: All connection attempts failed`.
The text passes through the auth-diagnostic redactor first. One place still
prints the old string: the batch-connect line
`Failed to connect to <server>: unhandled errors in a TaskGroup (1 sub-exception)`,
which also appears in the CLI's `Error: cannot reach PMCP gateway: …` when
the gateway is down. The WARNING lines just before it name the cause. A
`gateway.update_server` probe that hangs on Python 3.10 now reports
`Update probe timed out after 60 seconds.` instead of an empty
`Failed to run update probe: `.

**What to do.** Match on the underlying error (`ConnectError`, `Timeout`,
`ConnectionResetError` …) instead of the group string.

**How to verify.** With no gateway running, `pmcp update --all` logs
`Connection to pmcp-gateway failed (attempt 1/3), retrying in 1.0s: ExceptionGroup(1 sub-exception): ConnectError: All connection attempts failed`.

### A cancelled teardown kills stdio servers at once

**Am I affected?** You are if a stdio server you run needs its SIGTERM handler
to flush state on exit (e.g. a browser saving its profile), and your gateway's
disconnects, restarts, refreshes or shutdowns are sometimes cancelled or time out
-- a client hanging up mid-call, or a service manager stopping the gateway.

**What changed.** A teardown whose caller is cancelled now finishes
synchronously: pmcp SIGKILLs the server's process group (so its children die
too) instead of waiting out the SIGTERM grace period; if the cancel arrives
during that wait, SIGTERM has already been sent and the kill follows at once.
Shutdown does the same when its 10-second budget runs out. An uncancelled
teardown is unchanged: SIGTERM, then SIGKILL after the 5-second grace period.

**What to do.** Nothing, for most servers. If a server must flush on exit, stop
it with an uncancelled `gateway.disconnect_server` call (there is no `pmcp`
subcommand that disconnects a single server) and
let it return before stopping the gateway, and give the service manager a stop
timeout longer than the gateway's 10-second shutdown budget.

**How to verify.** The gateway log shows the teardown; a server that ignores
SIGTERM no longer outlives a cancelled disconnect or a timed-out shutdown
(`ps` shows no leftover process group).

### Known issues in 3.0.0

- **`pmcp refresh` writes to the wrong cache directory.** By default it
  writes `.pmcp/descriptions.yaml`, but the gateway reads
  `.mcp-gateway/descriptions.yaml` relative to its working directory. Until
  [Consiliency/pmcp#352](https://github.com/Consiliency/pmcp/issues/352) is
  fixed, run it from the gateway's working directory with:

  ```bash
  pmcp refresh --cache-dir .mcp-gateway
  ```

  How to verify: `ls -l .mcp-gateway/descriptions.yaml` shows a fresh
  timestamp.
- **Tenant-aware header resolution for remote servers is not wired yet**
  ([Consiliency/pmcp#353](https://github.com/Consiliency/pmcp/issues/353)).
  pmcp never reads per-tenant credentials from `.pmcp/tenants/<id>/pmcp.env`,
  in 2.7.3 or in 3.0. Every remote server's headers come from the gateway's
  own environment. If you need different credentials per tenant, run one
  gateway per tenant, each started with that tenant's variables exported.

## Other things you may notice

- **`pmcp status` shows the running gateway.** When a gateway is up,
  `pmcp status` reports *its* view, so restart it after upgrading or
  approving files. `pmcp config status` reads the files directly.
- **The trust store lives outside every checkout.** `~/.config/pmcp/trust.json`
  and `~/.config/pmcp/package_approvals.json` are created with mode `0600`.
  Any read failure counts as "not approved", never as "approved".
- **A project's overlay search stops at `$HOME`.** pmcp no longer treats your
  own `~/.pmcp/manifest.yaml` as a project overlay that needs approval.
- **`pmcp secrets set` takes the value from a prompt or `--stdin`**, so it
  stays out of your shell history: `pmcp secrets set API_TOKEN --scope user`.

## Rolling back to 2.7.3

Rolling back works, but do the first step **before** you reinstall 2.7.3:

1. **Remove the `packages:` section** from `~/.claude/gateway-policy.yaml`,
   from any file you pass with `--policy` or `PMCP_POLICY`, and from any
   project policy. 2.7.3 does not know the key and **refuses to start**:

   <!-- quote: 2.7.3 -->
   ```text
   Fatal error: Invalid policy file ~/.claude/gateway-policy.yaml: 1 validation error for GatewayPolicy
   packages
     Extra inputs are not permitted [type=extra_forbidden, …]
   ```

2. Reinstall 2.7.3 and restart the gateway:

   ```bash
   uv tool install --force 'pmcp==2.7.3'
   # or
   pip install 'pmcp==2.7.3'
   systemctl --user restart pmcp
   pmcp --version
   ```

What happens to 3.0 state under 2.7.3. We checked each item by running 2.7.3
against a home directory 3.0 had written to:

| 3.0 state | Under 2.7.3 |
|---|---|
| `~/.config/pmcp/trust.json`, `package_approvals.json` (and their `.lock` files) | Ignored. `pmcp status` runs normally. Keep them: if you upgrade again, approvals of unchanged files still hold. |
| `guidance.enable_feedback_submission` in `~/.claude/gateway-guidance.yaml` | Ignored. `pmcp guidance` loads the file without an error. |
| `server_version:` / `version:` pins in overlays | Ignored silently. The servers run unpinned again (`npx -y firecrawl-mcp`). |
| `packages:` in any policy | **2.7.3 refuses to start.** Remove it first (step 1). |
| `~/.config/pmcp/pmcp.env` and a project `.env.pmcp` | Read as before. 3.0 writes the same format as 2.7.3 (`KEY=value`, with the value double-quoted only when it contains whitespace, `#`, `=`, a quote or a backslash, or is empty) with mode `0600`, so 2.7.3 reads what 3.0 stored. |
| Explicit `--config`/`--policy`, user-scoped `.mcp.json` files | Unchanged. |

**2.7.3 also gives up the 3.0 protections.** These come back:

- Project `.mcp.json`, manifest overlays and policy apply again without
  approval, and a project policy *replaces* your user policy.
- `PMCP_*` redirects from `.env` are honoured.
- Every key from `.env` reaches every spawned server.
- Discovered packages provision without approval.
- `gateway.submit_feedback` with `confirm_submission=true` can post again,
  with no operator switch, using `PMCP_FEEDBACK_TOKEN` or `GITHUB_TOKEN`
  (including one a checkout's `.env.pmcp` planted) or a logged-in `gh` CLI.
  See the feedback row in the table below.

### Undo what you changed for 3.0

Downgrading the package does not undo the changes you made to your own code,
config and servers while following this guide, and some of them mean
something different to 2.7.3. Go through this table **before** you restart
on 2.7.3. Each row is one section of this guide. "Safe on 2.7.3" means
2.7.3 accepts the migrated form and behaves as you expect; "Reverse:" means
you must undo that step. Rows marked † were checked by running 2.7.3.

<!-- rollback-table -->
| Section | On 2.7.3 |
|---|---|
| [Project files need approval](#project-files-need-approval) | Reverse: if you moved grants out of a project `.mcp-gateway-policy.yaml` into `~/.claude/gateway-policy.yaml`, 2.7.3 uses the project file *instead of* yours, so a deny-only project file allows everything else. Move the project file aside (`mv .mcp-gateway-policy.yaml .mcp-gateway-policy.yaml.3x`) and copy any of its denials you want into your own policy, or copy your full policy back into the project file.† Approvals in `trust.json` are ignored, and explicit `--config`/`--policy` paths work as before. |
| [`PMCP_MANIFEST_PATH`, `PMCP_CONFIG` and `PMCP_POLICY` must be exported in your shell](#pmcp_manifest_path-pmcp_config-and-pmcp_policy-must-be-exported-in-your-shell) | Safe on 2.7.3: it honours an exported variable and the `--config`/`--policy` flags. |
| [Spawned servers no longer inherit the keys pmcp loaded from `.env`](#spawned-servers-no-longer-inherit-the-keys-pmcp-loaded-from-env) | Safe on 2.7.3: shell exports are inherited, a server's `env` block in `~/.mcp.json` is passed as written,† and `pmcp secrets set` works the same. |
| [A symlinked project `.env.pmcp` is refused](#a-symlinked-project-envpmcp-is-refused) | Safe on 2.7.3: a regular `.env.pmcp` and your user store work the same. 2.7.3 follows a symlinked `.env.pmcp` again, for writes and at startup, wherever it points, and hangs on a fifo `.env.pmcp`, so check `ls -l .env.pmcp` in repositories you clone. |
| [A project file supplies credentials only](#a-project-file-supplies-credentials-only) | Reverse: if you moved entries from a subdirectory's `.env.pmcp` into the project root's `.env.pmcp`, 2.7.3 started from that subdirectory reads only that subdirectory's `.env.pmcp` and does not see them: copy the entries back into the subdirectory's `.env.pmcp`, or start pmcp from the project root (`cd` to it first). Otherwise 2.7.3 loads a project `.env.pmcp` and `.env` into its own environment again, so settings in them apply, a checkout's `.env` wins over your user store, and `pmcp secrets check` again prefers a project `.env.pmcp`, and `pmcp secrets sync --from-scope project --to-scope user` again copies every key, `UV_INDEX_URL` and the like included; credentials keep working. Re-check your user store after any such sync on 2.7.3. |
| [Discovered servers are default-deny](#discovered-servers-are-default-deny) | Safe on 2.7.3: package approvals are ignored and discovered packages provision without one. A `packages.allowlist` must go, as for the next row. |
| [New `packages:` policy section](#new-packages-policy-section) | Reverse: remove every `packages:` section (step 1 above), or 2.7.3 refuses to start.† |
| [Feedback submission is off by default](#feedback-submission-is-off-by-default) | Reverse: 2.7.3 ignores `enable_feedback_submission`† and posts on `confirm_submission=true` through the first of these that works: `PMCP_FEEDBACK_TOKEN`, then `GITHUB_TOKEN` (each exported, or loaded from a `.env`, a checkout's `.env.pmcp` or `~/.config/pmcp/pmcp.env`), then a `gh` CLI on the gateway's `PATH` using its stored login.† It posts to `ViperJuice/pmcp`, the project's former name, which GitHub redirects to `Consiliency/pmcp`, unless `PMCP_FEEDBACK_REPO` names another.† To stop every channel, run `pmcp guidance --telemetry off` before you restart on 2.7.3; the call then refuses before it reads any token.† 3.0 honours the same setting. Otherwise unset `PMCP_FEEDBACK_TOKEN` and `GITHUB_TOKEN`, delete them from those files, and keep `gh` off the gateway's `PATH`.† `GH_TOKEN` alone posts nothing without `gh`.† |
| [Auth URLs must be canonical](#auth-urls-must-be-canonical) | Safe on 2.7.3: it accepts every rewritten URL in the table.† |
| [Auth responses changed](#auth-responses-changed) | Reverse: monitoring or tests that now match `Empty token.`, `The token's algorithm is not supported.` or a `503` must match the 2.7.3 texts and `500`s again (or both). `--oauth-audience` exists in 2.7.3. |
| [The `tools/call` gate enforces the schemas pmcp advertises](#the-toolscall-gate-enforces-the-schemas-pmcp-advertises) | Reverse: don't send an explicit `null` for an optional argument; 2.7.3 rejects it (`Input validation error: None is not of type 'object'` for `"options": null`).† Correct JSON types are accepted by both. An audit reader that handles `audit.rejection` sees none. |
| [Task numbers are bounded](#task-numbers-are-bounded) | Safe on 2.7.3 for the bounds; the units change is the next row. |
| [Task `ttl` and `poll_interval` are seconds in pmcp and milliseconds on the wire](#task-ttl-and-poll_interval-are-seconds-in-pmcp-and-milliseconds-on-the-wire) | Reverse: 2.7.3 sends `ttl` and `poll_interval` to the server unchanged and reports the server's values back unchanged.† Which step undoes the migration depends on the server, so do **one** of these per downstream server, never both. **A spec-conforming (third-party) server** reads milliseconds, so a migrated `ttl: 300` keeps a task for 0.3 s, not five minutes:† Multiply by 1000 again (`ttl: 300000`, `poll_interval: 2500`) in the callers of that server; tasks pmcp returns from it show milliseconds again (`poll_interval: 2500.0`).† **A pmcp tenant server built to the old seconds contract that you switched to milliseconds for 3.0**: Switch it back to reading and returning seconds, and keep its callers sending seconds (`ttl: 300`), as they did on 2.7.3; tasks it returns show seconds again. Doing both keeps a five-minute task for 300000 seconds. |
| [Redaction removes more](#redaction-removes-more) | Safe on 2.7.3: it redacts less. |
| [Manifest version pins](#manifest-version-pins) | Reverse: 2.7.3 ignores `version:` and `server_version:` silently,† so a pinned server runs whatever npm resolves. To keep a version, put it in that server's `args` in `~/.mcp.json` (for example `"args": ["-y", "firecrawl-mcp@3.25.5"]`). |
| [An overlay entry pmcp cannot use is skipped](#an-overlay-entry-pmcp-cannot-use-is-skipped) | Safe on 2.7.3: it loads a corrected entry the same way. An entry 3.0 skips loads again on 2.7.3, and can again make `gateway.catalog_search` fail, for every query or for every query that matches it. |
| [Downstream servers see more from pmcp](#downstream-servers-see-more-from-pmcp) | Safe on 2.7.3: a server that handles `-32601` and `notifications/cancelled` simply doesn't receive them. |
| [Logs](#logs) | Safe on 2.7.3: alert exclusions for the new WARNINGs match nothing. |
| [Dependency floors](#dependency-floors) | Safe on 2.7.3: its floors for `pyjwt`, `aiohttp`, `python-dotenv` and `starlette` are lower, with no upper bound on any of the four.† (2.7.3 caps other packages: `mcp<3.0.0`, `httpx<1.0`, `httpx2<3.0.0`, `jsonschema<5.0.0` and `semver<4`; installing 2.7.3 resolves those itself.) |
| [Agent-facing hints](#agent-facing-hints) | Reverse: matchers that now expect `try` or `playwright::browser_take_screenshot` see `try/catch` and `playwright::browser_screenshot` again.† Match both. |
| [A symlinked `.mcp.json` is no longer edited](#a-symlinked-mcpjson-is-no-longer-edited) | Safe on 2.7.3: `--path` exists there.† Don't run `set-startup-policy` with `--source` against a symlink on 2.7.3: it replaces the link with a file.† |
| [`NaN` from HTTP/SSE servers](#nan-from-httpsse-servers) | Safe on 2.7.3: a lenient parser also reads the `null` 2.7.3 sends. |
| [Error text names the real failure](#error-text-names-the-real-failure) | Reverse: 2.7.3 logs only `unhandled errors in a TaskGroup (1 sub-exception)`, without the cause,† so a matcher on `ConnectError` finds nothing. Match both. |
| [A cancelled teardown kills stdio servers at once](#a-cancelled-teardown-kills-stdio-servers-at-once) | Safe on 2.7.3: an uncancelled `gateway.disconnect_server` and a longer stop timeout work the same. |

Prefer holding at `pmcp<3` for a short time over running 2.7.3 for long.
