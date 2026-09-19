# Detailed plan: re-record the operator's trust approval when startup-policy rewrites `.mcp.json` (#253)

## Task
`pmcp startup add/set --source project` (→ `config/loader.py::set_startup_policy`) rewrites the project `.mcp.json` via `_atomic_write_json`, changing its bytes. v13 trust approval is **content-keyed** (`trust_store.is_approved(path, content)` matches a SHA-256 of the exact bytes). So an operator write silently invalidates the operator's own prior `pmcp trust approve <.mcp.json>`, and the next startup refuses the now-unrecognised file with the consent gate's "not approved" message.

**Owner decision: RE-RECORD.** When the pre-write bytes were approved, re-record an `approved` decision for the exact new bytes. Never create a new approval for a file that was not already approved.

This plan supersedes the earlier hand-written `.consiliency/plans/detailed-253-re-record-approval-on-startup-rewrite.md` (merged via #257). Its design invariants below are the product of five rounds of cross-vendor panel review; they close two consent races the panel surfaced and must be preserved.

## Execution Policy
- execute: effort=high, reason=security-sensitive consent path with two TOCTOU races and a content-keyed trust store

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
- `set_startup_policy` — modify — bind approval to one captured input snapshot, in this exact order:
  1. **Symlink refusal (up front):** if `target.path` is a symlink, refuse (`ok=False`, `invalid_source` diagnostic) rather than rewriting through it — so `os.replace` and the recorded key cannot refer to different files.
  2. **Capture input ONCE:** `input_bytes = target.path.read_bytes()` (guard `OSError` → `was_approved = False`, first-time source). Parse the object to mutate FROM `input_bytes`, reusing the existing `_read_config_object` read rather than issuing a second independent read — the bytes checked for approval must be the bytes transformed.
  3. `pinned_key = target.path.resolve()` — resolve the canonical trust key exactly once.
  4. `was_approved = trust_store.is_approved_resolved(pinned_key, input_bytes)` (function-local `from pmcp import trust_store`).
  5. In the `should_write` branch: `output_bytes = _atomic_write_json(target.path, data)` — the exact bytes written, returned by the writer.
  6. If `was_approved`: `trust_store.record_resolved(pinned_key, output_bytes, trust_store.PROJECT_SCOPE, trust_store.APPROVED)` — approve exactly the written bytes against the pinned key. **No post-write read; no re-resolution.** Wrap in `try/except trust_store.TrustStoreError` → append `StartupPolicyDiagnostic(code="approval_not_carried_forward", ...)`, keep `ok=True`, leave the file written (checkout-resident store, post-#252; do not crash — next startup fails safe).
  7. Never re-record on dry-run/no-op (`should_write` already gates this).
- `StartupPolicyPreview` — modify (optional) — add `approval_carried_forward: bool` only if the CLI/JSON consumer needs to show it; the diagnostic path already covers the failure case. `ok` already exists.

### `src/pmcp/cli.py` (modify)
- `set-startup-policy` handler (around line 1898) — modify — after `After:`, print one human line if the approval was carried forward, or if a diagnostic says it was not. Single line; do not change the JSON shape beyond the optional new field.
- `_TRUST_SCOPE` (around line 2618) — modify — reference the shared `trust_store` constant instead of a local literal.

## Documentation impact
- `SECURITY.md` — modify — limitation **C-28** currently records this defect ("an operator write that invalidates their own approval"). After the fix, narrow or promote C-28 to reflect that the approval is carried forward, and add the new tests to its evidence. Re-run `scripts/check_security_claims.py` — must stay exit 0 (load-bearing: the checker fails if C-28's wording/evidence drift from the code, and a limitation must remain a single `characterizes:`-cited sentence — see #252's C-36 for the R12 single-sentence rule).
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

Mutations (each must turn its test RED): (a) revert `_atomic_write_json` to not returning bytes and re-read the file post-write → test 4 red; (b) let `set_startup_policy` call `record` (re-resolving) instead of `record_resolved(pinned_key, …)` → test 5 red; (c) drop the `was_approved` guard → test 2 red.

Ledger/lint: `uv run python scripts/check_security_claims.py SECURITY.md` exit 0; `uv run mypy src/`; `uv run ruff check` + `ruff format --check` on touched files.

## Acceptance criteria
- [ ] After `pmcp trust approve <.mcp.json>` then `pmcp startup add --source project`, `trust_store.is_approved(<.mcp.json>, <new on-disk bytes>)` is True and the next `load_configs` applies the server with no consent refusal.
- [ ] `set_startup_policy` does NOT create an approval for a `.mcp.json` that was not already approved, and records nothing on a dry-run.
- [ ] Approval is recorded via `trust_store.record_resolved(pinned_key, <writer-returned bytes>, …)` — verified by spying `record_resolved`, never a disk re-read; `set_startup_policy` never calls the re-resolving `trust_store.record`.
- [ ] A byte- or symlink-substitution at `target.path` AFTER the write does NOT transfer approval to substituted content (`is_approved(attacker_bytes/B)` is False); a symlinked `.mcp.json` is refused up front.
- [ ] A checkout-resident store yields `ok=True` + `approval_not_carried_forward` diagnostic without raising.
- [ ] `set_startup_policy` and `pmcp trust approve` use one shared scope constant.
- [ ] `scripts/check_security_claims.py SECURITY.md` exits 0 with C-28 updated and the new tests cited.
