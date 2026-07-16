# PMCP half — register `@consiliency/agent-board-mcp` (message-board delivery plane)

> **Owner:** PMCP. **Responds to:** `plans/HANDOFF-agent-board-mcp-registration.md` (from
> `Consiliency/consiliency-portal` PR #210) and the spine contract
> `consiliency-portal:plans/unification/message-board-plane/CONTRACT-message-board-plane.md`.
> **Ratifies:** IF-0-MBPLANE-CRED-1, IF-0-MBPLANE-ENDPOINT-1.
> **Scope of this doc:** the two interface confirmations + a phased roadmap. **No PMCP code is
> changed by this artifact** — a live provision is blocked on the board endpoint (IF-ENDPOINT-1),
> so nothing fans out until the fork below is resolved.

---

## 1. Confirmation — the credential-slot syntax (ask #1)

**PMCP does not have a `${cred:namespace/key}` slot syntax.** The proposal's
`${cred:agent-board/credential-descriptor}` does not match PMCP's model; the contract left this open
("*slot syntax is whatever PMCP's current secrets rework defines … PMCP confirms*"), so this is the
confirmation.

PMCP's credential model has **two** distinct injection paths, and which one applies decides the fork
in §3:

1. **Manifest catalog entry (public `manifest.yaml`) — store-resolved, single slot.**
   A manifest server declares `env_var` (the runtime var the subprocess reads) and, optionally,
   `secret_key` (a namespaced storage key). At `pmcp_provision`, PMCP resolves the value from its
   secret store via `credential_lookup_keys` (namespaced `secret_key` first, then legacy `env_var`)
   and injects it into the subprocess under `env_var`. The value is stored out-of-band
   (`pmcp secrets set <secret_key> <value>` or `gateway.auth_connect`), **not** interpolated from a
   `${…}` token. Today this supports **exactly one** credential var per server, and the public
   `ServerConfig` schema has **no `env:` block** for non-secret config (verified: `manifest/loader.py`
   `ServerConfig`, and the private overlay reuses the same class).

2. **Configured `.mcp.json` / private overlay entry — verbatim env.**
   A local `.mcp.json` entry carries an arbitrary `env: { … }` map, injected **verbatim** into the
   subprocess (this is exactly how `firecrawl` / `browser-use` ship multi-var config today). Local
   stdio env is **not** `${VAR}`-expanded (only remote HTTP headers are). PMCP's recent namespacing
   work resolves a *dead* `${VAR}`/`$VAR` placeholder in a server's own declared credential slot to
   the namespaced credential — but there is no general `${cred:ns/key}` scheme.

So: **multiple env vars and non-secret config are natively expressible via path (2), not path (1).**

## 2. Ratification — IF-0-MBPLANE-CRED-1 (ask #1 + descriptor-only)

> **⚠️ Superseded var names (2026-07-16):** the specific descriptor var names referenced in this section
> (`MESSAGE_BOARD_RUNTIME_CREDENTIAL_DESCRIPTOR` / `MESSAGE_BOARD_AGENT_1PASSWORD_ITEM`) are **not** what
> the shipped client reads — verified against the published `@consiliency/agent-board-client@1.2.1` dist,
> which reads neither. The *structural* CRED-1 ratification below (PMCP injects descriptor pointers only,
> never key material) stands; only the example var names are stale. The proven credential contract is in
> `plans/agent-board-overlay.template.jsonc` (op:// refs for `..._SUPABASE_SERVICE_ROLE_KEY` /
> `..._SIGNING_PRIVATE_KEY`).

**RATIFIED**, with one precision the portal + board halves must record:

- **PMCP's contribution to "descriptor pointer only, never a raw signing key" is *structural*, not
  *validational*.** PMCP injects only the env vars an entry declares and will declare **no** raw-key
  var for `agent-board` — so no key material is injected *by construction*. PMCP treats the stored/
  configured value **opaquely**; it does **not** inspect that the value is a `1password` descriptor
  vs. a key. The "never a raw key" guarantee therefore rests on **(a)** the entry declaring only
  descriptor-named slots (`MESSAGE_BOARD_RUNTIME_CREDENTIAL_DESCRIPTOR` /
  `MESSAGE_BOARD_AGENT_1PASSWORD_ITEM`), and **(b)** the board rejecting raw keys by contract — not on
  PMCP validating the value. This is a *strengthening* clarification, not a weakening.
- The var **names** are unchanged and the descriptor-only property is preserved, so this is a
  confirmation *within* CRED-1, **not an amendment** requiring re-ratification. (What *would* require
  re-ratification: changing the descriptor var names, or weakening the descriptor-only property.)

> **RESOLVED (portal + message-board, verified against `agent-board-runtime/src/config.ts`; recorded
> in the frozen contract §7 / portal #211, merged):** **Option B.** The board credential is a pure
> 1Password **pointer** — `config.ts` reads only `MESSAGE_BOARD_RUNTIME_CREDENTIAL_DESCRIPTOR` /
> `MESSAGE_BOARD_AGENT_1PASSWORD_ITEM` and resolves it itself; there is **no required raw-secret var**,
> so there is nothing to store-resolve. Ship the minimal public manifest **stub** for discoverability;
> the real ENDPOINT/descriptor/config live in the operator `.mcp.json` overlay, **verbatim**. Option A
> is **not** taken (no store-resolved actual-secret var exists). If a future deployment ever introduces
> a true-secret var, *that single var* — not the pointers — becomes the Option-A case.
>
> **`${machine_id}` RESOLVED:** owned by the **operator / wiring layer** (portal half's `MBP-WIRE`
> phase), per-host. `config.ts` has no hostname/UUID fallback, so each host's overlay sets
> `MESSAGE_BOARD_RUNTIME_MACHINE_ID`. It is **not** a PMCP catalog-static value → **not** in the stub.

## 3. The fork — how the entry is shaped (recommendation + one question back)

The message-board entry needs **6 config vars** (`ENDPOINT`, `SUPABASE_URL`,
`MODE=embedded_mcp`, `TRUST_DOMAIN`, `BOARD_SCOPE`, `MACHINE_ID`) + **2 descriptor slots**
(`CREDENTIAL_DESCRIPTOR`, `1PASSWORD_ITEM`) + an optional `REFRESH_TOKEN`. Two ways to land that:

### Option B — overlay-verbatim entry + minimal manifest stub  ← **recommended**
- Ship a **minimal public manifest stub** `agent-board` (command/args + keywords + description +
  `env_instructions` documenting the full var contract) purely for **discoverability**
  (`gateway_catalog_search` / `gateway.request_capability` surface it).
- The real **config + descriptor pointers** live in the operator's `.mcp.json` (or private overlay)
  `env:` map, injected verbatim. **The descriptor pointers are `op://…` references — pointers, not
  secret material — so literal config is appropriate;** PMCP still never sees a raw key.
- **Zero PMCP schema change. Lands today.** Config that is per-deployment/org-specific — `ENDPOINT`
  is literally unknown until the board deploys — correctly stays *out* of PMCP's shipped public
  catalog and in the operator overlay.

### Option A — extend the manifest/overlay schema (store-resolved, multi-slot)
Only if the portal/board require the descriptor pointer to be **resolved through PMCP's secret store**
(not literal overlay config). Then PMCP's `ServerConfig` (public manifest *and* the v1.18.0 private
overlay) gains **(i)** an `env:` config block and **(ii)** support for **multiple** descriptor-only
credential slots (each `secret_key`-namespaced), plus the resolver injecting all of them. Heavier,
and it builds against an interface three other repos have not finished — **defer** unless required.

### → Question back to the portal / message-board (blocks choosing A vs B)
**Do the descriptor pointers (`CREDENTIAL_DESCRIPTOR`, `1PASSWORD_ITEM`) need to be resolved through
PMCP's secret store, or is literal `.mcp.json`/overlay config acceptable?** Since a 1Password
*pointer* is not itself the secret, Option B (literal overlay config) is defensible and collapses
PMCP's half to a stub + scope policy. If store-resolution is a hard requirement, we take Option A.

Secondary open item (not scoped work here): **`MESSAGE_BOARD_RUNTIME_MACHINE_ID: "${machine_id}"`** —
PMCP does not resolve a `${machine_id}` token. Confirm whether per-host identity is **board-side**
(the runtime derives it) or must be **PMCP-side** (PMCP could source it from its `identity` module).
If PMCP-side, it's a small additive item folded into whichever option we take.

## 4. Ratification — IF-0-MBPLANE-ENDPOINT-1 (ask #3 context)

**RATIFIED as consumed.** The entry targets `MESSAGE_BOARD_RUNTIME_ENDPOINT` with
`MESSAGE_BOARD_RUNTIME_MODE=embedded_mcp` (enum `standalone | embedded_mcp`; PMCP treats these as
opaque config values). PMCP asserts nothing about a `gateway` runtime mode — "gateway-only delivery"
is the board's enforcement property, not a PMCP concern. **A live provision is blocked until this
endpoint + health probe exist and are owned;** the discoverability stub (Option B) can land ahead.

## 5. Scope / tier policy (ask #3) — safe-tier default

**RATIFIED INTENT: default provision = safe tier only — but this is an OPEN OPERATOR STEP, not an
enforced default. ⚠️ Do not read this section as a shipped guarantee.** Two facts (verified against the
shipped artifacts, 2026-07-16) mean safe-tier is NOT the effective default until the operator gates it:
- The shipped `agent-board-mcp@1.2.2` server registers **all** tool tiers (`safe_default` + `supervisor`
  + `elevated_admin`) **unconditionally** — `MCP_TOOL_GROUPS` exists but nothing gates registration on it.
- PMCP's tool policy **defaults to allow-all** (`policy/policy.py` `is_tool_allowed` returns `True` with
  no allow/denylist configured), and **no `agent-board` policy or manifest stub landed** in this repo.

So with the overlay entry alone, every harness on the gateway can reach the supervisor/admin tools over a
`service_role` connection (which bypasses RLS). **The operator MUST add a PMCP tool-policy denylist** to
realize the safe-tier default — deny `agent-board::*`, then allow only the safe/read set (status, inbox,
notification, runtime-diagnostics, fetch-descriptor) plus the send/thread tools actually used (see the
overlay template's TOOL-TIER SAFETY note). PMCP's `PolicyManager` gates at the **server**/**tool** level
(`is_server_allowed` / `is_tool_allowed`); there is no native `safe|supervisor|admin` tier enum, so the
tiers map onto tool allow/deny. Independently, the cross-server secret-bleed fix in **v1.19.3** (#96)
ensures an `agent-board` subprocess never receives another server's credentials regardless of tier — but
that is orthogonal to tool-tier gating. The exact tool → tier mapping is owned by message-board's tool
contract.

---

## Phased roadmap (PMCP half)

| Phase | Deliverable | Gate / blocker |
|---|---|---|
| **P0 (this doc)** | Interface confirmations (§1–§5): slot syntax, CRED-1 + ENDPOINT-1 ratified, safe-tier default. **A/B fork now RESOLVED → Option B; `${machine_id}` → operator.** | ✅ done |
| **P1** | Author the operator `.mcp.json` overlay template (`plans/agent-board-overlay.template.jsonc`). **No schema change.** | ✅ done — pin published to **public npm** (`@consiliency/agent-board-mcp@1.2.2`). |
| **P2** | Overlay-verbatim entry (Option B). Config lives in the operator overlay, not the public catalog. | ✅ overlay done (service_role + op:// descriptors). ⚠️ **safe-tier gating is an OPEN operator step, not shipped** — see §5. |
| **P3** | Live end-to-end provision + VERIFY round-trip. | ✅ **done — send↔receive PROVEN through the gateway 2026-07-16** (see closeout below). |

**(Historical — superseded by the 2026-07-16 go-live; see the closeout below.)** *At the time of
writing:* holding at P0 by agreement (portal + advisor) — the stub was cleared to author, but two
upstream halves gated it: the **published npm pin** (message-board's IF-SCHEMA-1) and the **live
endpoint** (IF-ENDPOINT-1). Both have since landed; P1–P3 are complete.

---

## Unblock verification — 2026-07-13 (message-board deliverables landed, with two corrections)

message-board reported both deliverables done. **Verified against source of truth:**

- **IF-ENDPOINT-1 — SATISFIED.** `MESSAGE_BOARD_RUNTIME_ENDPOINT = https://mqccjfqngptkemnchlko.supabase.co`
  (`MODE=embedded_mcp`) is live and reachable over HTTPS (root `404`, `/rest/v1/` `401` — the expected
  authenticated-only Supabase behavior). Operator overlay template landed:
  `plans/agent-board-overlay.template.jsonc`.
- **The mcp pin — EXISTS but on a PRIVATE registry, not public npm (correction).** The hand-off said
  "fill the catalog with `@consiliency/agent-board-mcp@1.1.0`". That version **does** exist — but on
  **GitHub Packages** (org `Consiliency`, private, repo `message-board`), **not** public npm
  (`npm view` → E404). The deployed `@consiliency/agent-board-schema@1.1.0` is the DB *schema bundle*;
  `agent-board-mcp` is the separate stdio-server package. **New requirement:** a catalog/overlay
  `npx -y @consiliency/agent-board-mcp@1.1.0` fails against default npm — the `@consiliency` scope must
  route to `https://npm.pkg.github.com` with a `read:packages` token (an operator `.npmrc`), **or**
  message-board publishes the mcp package to public npm.

### New coordination question (binds PMCP ↔ message-board)
**Private GitHub Packages vs public npm for `@consiliency/agent-board-mcp`:** should the operator
supply a GitHub Packages `.npmrc` + `read:packages` token (folds into the same operator/wiring layer as
`MACHINE_ID` / the descriptor), or will message-board publish the mcp package to **public npm**? Public
npm is the cleaner catalog story (no registry auth to distribute); private is fine if the token wiring
is owned by the operator half. **This decides whether a live provision needs npm-registry wiring.**

### Superseded assumption (recorded, per message-board's flag)
The endpoint is now a **message-board-owned** Supabase project, not portal-hosted — this **supersedes
ratified Assumption 1** (endpoint-to-be-portal-hosted). No change to PMCP's half (we consume the URL
wherever hosted); recorded here for the contract trail.

### Remaining inputs before a LIVE provision + VERIFY round-trip
1. The registry decision above (public npm, or the GitHub Packages `.npmrc`/token approach).
2. `MESSAGE_BOARD_RUNTIME_CREDENTIAL_DESCRIPTOR` / `MESSAGE_BOARD_AGENT_1PASSWORD_ITEM` — the `op://`
   descriptor pointers (operator/1Password-owned; not fabricated here).
3. `MESSAGE_BOARD_RUNTIME_BOARD_SCOPE` and `MACHINE_ID` (per-host, operator/MBP-WIRE).

**Not a PMCP blocker beyond these inputs.** The overlay template is ready; a live `agent-board`
provision + the VERIFY round-trip fill in the three items above. (Maintainer countersign for the
Portal's prod mutation is separate and the maintainer's, not PMCP's.)

## Acceptance (mirrors the spine VERIFY)
A real message round-trips: agent A sends via a PMCP-provisioned `agent-board` → board persists →
agent B reads via its own PMCP-provisioned `agent-board`, **zero manual paste, descriptor-based
credentials only.** PMCP's half is *done* for P0–P2 once the entry is discoverable + safe-tier-gated
and the credential path is descriptor-only; P3 is jointly gated with the board-deploy + drift halves.

---

## GO-LIVE COMPLETE — 2026-07-16

**Acceptance met.** Signed send↔receive proven end-to-end through the PMCP gateway, descriptor-only:

- `create_thread` → `ok:true` (thread `e67e9cc5-…`); earlier gateway-native proof `ea9202ae-…`.
- `send_direct_message` → `ok:true`, `message_id 9ef6849f-…`, delivered `inbox_item 01f3d919-…`,
  Ed25519 `verification_status:"signed"` (enforcementMode `observe`).
- read back via `agent_board.v_thread_feed` → the message retrieved. `mode:service_role, valid:true`.

**What unblocked P3 (both shipped upstream):**
1. **`@consiliency/agent-board-mcp@1.2.2` on public npm** — resolves the registry question (public npm,
   no `.npmrc`/token). Reads `MESSAGE_BOARD_AGENT_AUTH_MODE` and passes it to the client, enabling a
   **signed `service_role` write with no user session** (the shipped client otherwise requires a
   user-scoped session, which headless hosts can't mint).
2. **op://-in-env resolution in mcp 1.2.2 / client 1.2.1** — the env credential path now resolves op://
   descriptors in-client, so CRED-1 (descriptor-only) holds with a plain `npx` spawn. (On 1.2.1 the env
   path read values literally → needed an `op run --` wrapper; unnecessary on 1.2.2.)

**Operating mode (ratified):** `service_role` + Ed25519 signing. The only secret at rest is
`OP_SERVICE_ACCOUNT_TOKEN` (secret-zero) — every other credential stays an op:// pointer. #96 held: the
subprocess gets its own token + config, never another server's secrets. Proven recipe:
`plans/agent-board-overlay.template.jsonc`.

**Superseded by the proven config:** the earlier proposed var names
(`MESSAGE_BOARD_RUNTIME_MODE`/`_RUNTIME_TRUST_DOMAIN`/`_RUNTIME_BOARD_SCOPE`/`_RUNTIME_CREDENTIAL_DESCRIPTOR`)
are **not** what the shipped client reads — see the overlay template for the actual contract
(`MESSAGE_BOARD_BOARD_SCOPE` / `MESSAGE_BOARD_TRUST_DOMAIN` — same `MESSAGE_BOARD_` prefix but **no
`RUNTIME_` segment**; connection + signing via op:// refs; `MESSAGE_BOARD_AGENT_AUTH_MODE`).

**Durable track (off critical path):** board-native Ed25519 auth-exchange (short-lived tokens minted
from the host signing key) — **message-board#27**. `service_role` is the ratified stopgap and is
holding. The op://-in-env ergonomic was **message-board#29**, now shipped. Coordination PR closed by
merge; message-board delivery plane confirmed complete on their side.
