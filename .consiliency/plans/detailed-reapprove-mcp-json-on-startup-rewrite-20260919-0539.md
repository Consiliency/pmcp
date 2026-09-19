# Detailed plan: re-record the operator's trust approval when startup-policy rewrites `.mcp.json` (#253)

## Task
`pmcp startup add/set --source project` (→ `config/loader.py::set_startup_policy`) rewrites the project `.mcp.json` via `_atomic_write_json`, changing its bytes. v13 trust approval is **content-keyed** (`trust_store.is_approved(path, content)` matches a SHA-256 of the exact bytes). So an operator write silently invalidates the operator's own prior `pmcp trust approve <.mcp.json>`, and the next startup refuses the now-unrecognised file with the consent gate's "not approved" message.

**Owner decision: RE-RECORD.** When the pre-write bytes were approved, re-record an `approved` decision for the exact new bytes. Never create a new approval for a file that was not already approved.

This plan supersedes the earlier hand-written `.consiliency/plans/detailed-253-re-record-approval-on-startup-rewrite.md` (merged via #257). Its design invariants below are the product of six rounds of cross-vendor panel review; they close three consent races the panel surfaced (byte-content TOCTOU, path-identity-at-record, and capture-vs-resolve ordering) and must be preserved.

## Threat model & scope (owner decision)
The panel surfaced a further class of race: a live attacker holding concurrent write access to the operator's project directory who swaps `target.path` between individual syscalls of the operator's own `pmcp startup` invocation (e.g. during procfs pathname resolution) can still bind a different file. **This sub-syscall race against the operator's own authenticated CLI is declared OUT of scope for #253** (owner decision, 2026-09-19). The v13 threat model is a hostile *committed* repository's files and a prompt-injectable agent brokered through the gateway — not an attacker racing the operator's interactive terminal at syscall granularity. Closing that class would require descriptor-anchored trust keys (a validated fd↔key association replacing the path-based key), a larger `trust_store` key-model change with its own semantics cost (inode reuse, key stability across same-path edits); it is not undertaken here.

What this plan DOES guarantee (in scope): the byte-content and path-identity-at-record races are closed for the ordinary case, and a symlinked `.mcp.json` is refused up front. The out-of-scope sub-syscall residual is an **accepted limitation, not a fail-safe** — and it must not be described as one. A partial fail-safe does hold for the byte-content dimension: a post-write substitution that changes the on-disk bytes makes the recorded approval fail the next-startup content-hash check, so *that* shape is refused. But it does NOT hold in general: on the `was_approved` branch the plan records `(pinned_key, output_bytes)`, so if a sub-syscall race bound a different file `B` whose contents equal `output_bytes`, then `is_approved(B, output_bytes)` succeeds at the next startup and `B` is trusted — content equality is not file identity (panel finding, codex). Excluding this race from the threat model justifies leaving it unresolved; it does NOT make every occurrence fail safe. #253 is a UX/consistency convenience (its own issue: "fails safe... not a bypass"), and accepting this narrow, out-of-scope mis-binding risk is proportionate to that — but it is accepted, not neutralised.

## Execution Policy
- execute: effort=high, reason=security-sensitive consent path with three TOCTOU races and a content-keyed trust store

## Research summary
Read in-session (no Explore recon — the file map and design are fully in context):
- `config/loader.py::set_startup_policy` (around line 723): resolves one target source via `_select_policy_source`, reads it with `_read_config_object(target.path)`, computes the new `autoStart`, and only when `should_write = operation.apply and not operation.dry_run and changed` sets `data["autoStart"]` and calls `_atomic_write_json(target.path, data)`. Returns a `StartupPolicyPreview(ok, source, path, changed, dry_run, before_autoStart, after_autoStart, message, diagnostics, next_step)` — the error branches already return `ok=False`, the success branch `ok=True`.
- `config/loader.py::_atomic_write_json` (around line 712): `json.dump(data, indent=2)` + trailing `"\n"` to a temp file, then `tmp.replace(path)`. **Returns `None` today.**
- `trust_store.is_approved(path, content)` (around line 425) and `trust_store.record(path, content, scope, decision)` (around line 450): both call `Path(path).resolve()` internally; `record` raises `TrustStoreError` if the store is unusable (post-#252 this includes a store resident in the checkout enclosing the approved path). `is_approved` matches on resolved path + content SHA + `decision == APPROVED`, ignoring scope; never raises.
- `cli.py::_run_trust_approve` (around line 2609) records with scope `cli._TRUST_SCOPE` (around line 2618).
- Import posture: `config/loader` is imported by `pmcp.project_consent`, which `trust_store` imports; `trust_store` already imports `find_project_root` from loader **function-locally** to break that cycle. Mirror it — import `trust_store` inside `set_startup_policy`.

## Changes

### `src/pmcp/trust_store.py` (modify)
- `is_approved_resolved(resolved_path: Path, content: bytes) -> bool` — add — canonical-key lookup that treats `resolved_path` as the key **verbatim** (no internal `.resolve()`).
- `record_resolved(resolved_path: Path, content: bytes, scope: str, decision: str) -> TrustRecord` — add — canonical-key record that treats `resolved_path` as the key **verbatim** (no internal `.resolve()`); retains the existing residency/lock/validation behaviour.
- `is_approved` / `record` — modify — refactor to `Path(path).resolve()` then delegate to the `_resolved` variants, so every existing caller's behaviour is byte-identical. This is the concrete home of the "pin the canonical key" invariant that closes the path-identity race.
- `_TRUST_SCOPE` (shared scope constant) — add — move the scope literal that `cli._run_trust_approve` uses (currently `cli._TRUST_SCOPE`) to `trust_store` (e.g. `trust_store.PROJECT_SCOPE`) and have `cli._TRUST_SCOPE` reference it, so `set_startup_policy` and `pmcp trust approve` provably share one constant, never two literals.

### `src/pmcp/config/loader.py` (modify)
- `_atomic_write_json` — modify — **return the exact bytes it writes.** Serialise once (`payload = json.dumps(data, indent=2) + "\n"`), write `payload.encode("utf-8")`, and return those bytes. Never re-read the file to obtain the bytes to approve (post-write re-read is the byte-content TOCTOU). Update its one other caller (the return is additive — callers ignoring it are unaffected).
- `set_startup_policy` — modify — bind input capture, approval lookup, the write target, and the record to ONE stable identity, established ATOMICALLY, in this exact order. The identity must be pinned *before or together with* the input read — a plan that reads input, then resolves, leaves a window in which `target.path` is swapped to a symlink pointing at a different, separately-approved file `B`, so the approval lookup keys on `B` while the bytes came from `A` and the record then grants `B`'s unrewritten contents (panel finding, codex — capture-vs-resolve ordering race):
  1. **Pin identity + capture input atomically:** open `target.path` ONCE with `O_NOFOLLOW` on the final component — this refuses a symlinked `.mcp.json` up front AND fixes the file the rest of the operation binds to, with no capture-vs-resolve window. From that open descriptor: read `input_bytes`, and capture `pinned_key` as the descriptor's canonical path (`os.path.realpath("/proc/self/fd/<fd>")`, or the platform equivalent; on failure/`OSError`, refuse or treat as first-time `was_approved = False`). Parse the object to mutate FROM `input_bytes` (reuse this single read; never a second independent read of `target.path`). (This `O_NOFOLLOW` use is for CAPTURE, distinct from the re-record key — the earlier note dropped an fd as the *post-replace* record key, which remains correct; the record still keys on `pinned_key`, the path.)
  2. `was_approved = trust_store.is_approved_resolved(pinned_key, input_bytes)` (function-local `from pmcp import trust_store`).
  3. In the `should_write` branch: `output_bytes = _atomic_write_json(pinned_key, data)` — write to the PINNED path (not the mutable `target.path`), returning the exact bytes written.
  4. If `was_approved`: `trust_store.record_resolved(pinned_key, output_bytes, trust_store.PROJECT_SCOPE, trust_store.APPROVED)` — approve exactly the written bytes against the same pinned key. **No post-write read; no re-resolution.** Wrap in `try/except trust_store.TrustStoreError` → append `StartupPolicyDiagnostic(code="approval_not_carried_forward", ...)`, keep `ok=True`, leave the file written (checkout-resident store, post-#252; do not crash — next startup fails safe).
  5. Never re-record on dry-run/no-op (`should_write` already gates this).
  - **Invariant (in scope):** `input_bytes` read, `is_approved_resolved`, `_atomic_write_json`, and `record_resolved` all name the SAME `pinned_key` established by the single `O_NOFOLLOW` open, which refuses a symlinked `.mcp.json` and binds the ordinary operation to one file. A sub-syscall swap during pathname resolution is the documented out-of-scope residual (see Threat model & scope); it is an accepted limitation, NOT covered by a fail-safe (a mis-bound file whose contents equal the written bytes would be trusted).
- `StartupPolicyPreview` — modify (optional) — add `approval_carried_forward: bool` only if the CLI/JSON consumer needs to show it; the diagnostic path already covers the failure case. `ok` already exists.

### `src/pmcp/cli.py` (modify)
- `set-startup-policy` handler (around line 1898) — modify — after `After:`, print one human line if the approval was carried forward, or if a diagnostic says it was not. Single line; do not change the JSON shape beyond the optional new field.
- `_TRUST_SCOPE` (around line 2618) — modify — reference the shared `trust_store` constant instead of a local literal.

## Documentation impact
- `SECURITY.md` — add — a limitation recording the in-scope guarantee AND the out-of-scope sub-syscall residual as an ACCEPTED risk (a mis-bound file whose contents equal the written bytes could be approved) — do NOT describe it as fail-safe — as a single `characterizes:`-cited sentence (R12). Then the existing limitation **C-28** currently records this defect ("an operator write that invalidates their own approval"). After the fix, narrow or promote C-28 to reflect that the approval is carried forward, and add the new tests to its evidence. Re-run `scripts/check_security_claims.py` — must stay exit 0 (load-bearing: the checker fails if C-28's wording/evidence drift from the code, and a limitation must remain a single `characterizes:`-cited sentence — see #252's C-36 for the R12 single-sentence rule).
- `CHANGELOG.md` — add — one `### Changed` bullet under `[Unreleased]`: startup-policy edits now carry the operator's approval forward. Reference `#253`.
- CONSENT post-execution amendment 10b references this as an open item — the roadmap/plan text that calls it "open" should note it is closed by this change (no edit to the historical amendment itself).

## Dependencies & order
- **Depends on #252 (merged).** `record`/`record_resolved` refuse a store resident in the checkout enclosing the path; the `try/except TrustStoreError` → diagnostic path is what keeps that interaction graceful.
- Land the shared-scope-constant move and the `_resolved` trust_store variants BEFORE wiring `set_startup_policy`, so it can pass a `pinned_key` that is never re-resolved.
- `_atomic_write_json` must return bytes before `set_startup_policy` can record them.

## Verification
`automation.suite_command`: `uv run pytest -q -p no:cacheprovider tests/test_startup_policy_reapproval.py tests/test_startup_policy*.py tests/test_project_consent_gate.py tests/test_trust_store.py tests/test_trust_cli.py`

New `tests/test_startup_policy_reapproval.py`, driving the real `set_startup_policy` + `trust_store`:
1. **Happy path:** approve a project `.mcp.json`; run `set_startup_policy(add, apply=True, dry_run=False)`; assert the file changed AND `is_approved(path, path.read_bytes())` is True AND a fresh `load_configs`/`read_and_gate_project_config` loads the server with no consent refusal.
2. **Not-previously-approved:** an unapproved `.mcp.json` is NOT auto-approved after `set_startup_policy(add)` (`is_approved(path, new_bytes)` is False).
3. **Dry-run records nothing.**
4. **Byte-substitution (post-write TOCTOU):** a concurrent process replaces `.mcp.json` with attacker bytes immediately AFTER `_atomic_write_json` returns; **spy `trust_store.record_resolved`** and assert its `resolved_path` == `pinned_key` AND its `content` == the writer's returned bytes AND == an independent `json.dumps(parse(input_snapshot) with autoStart)`; assert `is_approved(path, attacker_bytes)` is False. Never use a disk re-read as the oracle. `set_startup_policy` must never call the re-resolving `trust_store.record`.
5. **Symlink-substitution (post-write path-identity race):** after the write, replace `target.path` with a symlink to a previously-UNAPPROVED file `B` whose contents equal `output_bytes`; assert `is_approved(B, output_bytes)` is False (consent recorded against `pinned_key`, not transferred to `B`). Also assert a symlinked `.mcp.json` presented up front is refused.
6. **Checkout-resident diagnostic:** with a checkout-resident store (post-#252), `record_resolved` raises `TrustStoreError`; `set_startup_policy` returns `ok=True` with an `approval_not_carried_forward` diagnostic and does not raise.
7. **Capture-vs-resolve substitution (panel finding, codex):** file `A` holds unapproved bytes `I`; file `B` has a stored approval for `I` but currently holds unapproved bytes `O` equal to the planned rewrite of `I`. After `A`'s bytes are captured, swap `A`→symlink→`B` (before key resolution in a naive ordering). Assert the operation does NOT approve `B`'s contents (`is_approved(B, O)` stays False) — because identity is pinned atomically with capture via the single `O_NOFOLLOW` open, the swap either fails the open (symlink refused) or leaves the operation bound to the original file. A mutation that reads input before pinning the key (capture-then-resolve ordering) must turn this red.

Mutations (each must turn its test RED): (a) revert `_atomic_write_json` to not returning bytes and re-read the file post-write → test 4 red; (b) let `set_startup_policy` call `record` (re-resolving) instead of `record_resolved(pinned_key, …)` → test 5 red; (c) drop the `was_approved` guard → test 2 red; (d) reorder to read input BEFORE pinning the key (capture-then-resolve, no `O_NOFOLLOW`) → test 7 red.

Ledger/lint: `uv run python scripts/check_security_claims.py SECURITY.md` exit 0; `uv run mypy src/`; `uv run ruff check` + `ruff format --check` on touched files.

## Acceptance criteria
- [ ] After `pmcp trust approve <.mcp.json>` then `pmcp startup add --source project`, `trust_store.is_approved(<.mcp.json>, <new on-disk bytes>)` is True and the next `load_configs` applies the server with no consent refusal.
- [ ] `set_startup_policy` does NOT create an approval for a `.mcp.json` that was not already approved, and records nothing on a dry-run.
- [ ] Approval is recorded via `trust_store.record_resolved(pinned_key, <writer-returned bytes>, …)` — verified by spying `record_resolved`, never a disk re-read; `set_startup_policy` never calls the re-resolving `trust_store.record`.
- [ ] A byte- or symlink-substitution at `target.path` AFTER the write does NOT transfer approval to substituted content (`is_approved(attacker_bytes/B)` is False); a symlinked `.mcp.json` is refused up front.
- [ ] Input capture, approval lookup, write, and record all bind to one `pinned_key` established atomically by a single `O_NOFOLLOW` open; a swap of `target.path` between capture and key resolution cannot transfer approval to another file.
- [ ] A checkout-resident store yields `ok=True` + `approval_not_carried_forward` diagnostic without raising.
- [ ] `set_startup_policy` and `pmcp trust approve` use one shared scope constant.
- [ ] `scripts/check_security_claims.py SECURITY.md` exits 0 with C-28 updated and the new tests cited.
