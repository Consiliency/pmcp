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

**RATIFIED: default provision = safe tier only.** PMCP's `PolicyManager` gates at the **server** and
**tool** level (`is_server_allowed` / `is_tool_allowed`); there is no native `safe|supervisor|admin`
*tier enum*, so the three tiers map onto tool allow/deny:
- **Safe (default):** allow the read-only/metadata-safe tools (status, inbox, notification, runtime
  diagnostics, fetch-descriptor).
- **Supervisor / Admin:** **denied by default**; enabled only via explicit PMCP policy (an operator
  allowlist). Admin additionally must never print secrets — enforced by the policy gate, and by the
  cross-server secret-bleed fix already shipped in **v1.19.3** (#96), so an `agent-board` subprocess
  never receives another server's credentials regardless of tier.
The exact tool → tier mapping is owned by message-board's tool contract; PMCP consumes their
self-described tiers and ships the safe set as the provision default.

---

## Phased roadmap (PMCP half)

| Phase | Deliverable | Gate / blocker |
|---|---|---|
| **P0 (this doc)** | Interface confirmations (§1–§5): slot syntax, CRED-1 + ENDPOINT-1 ratified, safe-tier default, the A/B fork + questions back. | none — landing now |
| **P1** | Resolve the fork with the portal/board (§3 question). If **B**: author the minimal discoverability stub in `manifest.yaml` (+ `env_instructions` documenting the var contract) and the operator `.mcp.json` overlay template — no schema change. If **A**: schema extension roadmap (env block + multi-slot descriptor credentials) as a separate versioned PMCP phase. | blocked on the §3 answer |
| **P2** | Land the chosen entry (stub or extended) + the safe-tier scope policy default. Discoverable via `catalog_search`; documents the contract. | after P1; no live endpoint needed |
| **P3** | Live end-to-end provision + VERIFY round-trip. | **blocked on IF-ENDPOINT-1** (board deployed + health probe + owner) and portal#208 drift (IF-DRIFT-1) |

## Acceptance (mirrors the spine VERIFY)
A real message round-trips: agent A sends via a PMCP-provisioned `agent-board` → board persists →
agent B reads via its own PMCP-provisioned `agent-board`, **zero manual paste, descriptor-based
credentials only.** PMCP's half is *done* for P0–P2 once the entry is discoverable + safe-tier-gated
and the credential path is descriptor-only; P3 is jointly gated with the board-deploy + drift halves.
