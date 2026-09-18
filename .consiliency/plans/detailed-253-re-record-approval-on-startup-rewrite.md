# Detailed plan: re-record the trust approval when `set_startup_policy` rewrites `.mcp.json` (#253)

## Task
`pmcp startup add/remove --source project` (→ `config/loader.py::set_startup_policy`) rewrites the project `.mcp.json` via `_atomic_write_json`, changing its bytes. v13 trust approval is **content-keyed** (`trust_store.is_approved(path, content)` matches a SHA-256 of the exact bytes). So an operator write silently invalidates the operator's own prior `pmcp trust approve <.mcp.json>`, and the next startup refuses the now-unrecognised file with the consent gate's "not approved" message.

**Decision (owner, 2026-09-18): re-record.** After writing the new bytes, if the pre-write bytes were approved, re-record an `approved` decision for the new bytes — the operator explicitly ran this write, so their consent carries forward. Do **not** create a new approval for a file that was not already approved.

## Research summary
- `set_startup_policy` (`src/pmcp/config/loader.py:723`) resolves a single target source, reads it via `_read_config_object`, computes the new `autoStart`, and — only when `should_write = operation.apply and not operation.dry_run and changed` — sets `data["autoStart"]` and calls `_atomic_write_json(target.path, data)` (`:803-805`). It returns a `StartupPolicyPreview` carrying `source`, `path`, `changed`, `dry_run`, `before_autoStart`, `after_autoStart`, `message`, `diagnostics`, `next_step`.
- `_atomic_write_json` (`:712`) writes `json.dump(data, indent=2)` + a trailing `"\n"` via a temp file + `replace`. The exact on-disk bytes after the write are authoritative — re-read them rather than re-serialising, so the recorded digest cannot drift from what `_read_and_gate_project_config` will later hash.
- `trust_store.is_approved(path, content)` (`src/pmcp/trust_store.py:425`) matches on resolved path + content SHA + `decision == APPROVED`; it **ignores scope** and never raises. `trust_store.record(path, content, scope, decision)` (`:450`) writes the record and **raises `TrustStoreError` if the store is checkout-resident / unreadable / corrupt** (this is the residency guard, and post-#252 it also refuses a store resident in the checkout enclosing `path`).
- `pmcp trust approve` records with scope `cli._TRUST_SCOPE` (`src/pmcp/cli.py:2618`). Re-record with the **same** scope for consistency.
- Import posture: `config/loader.py` is imported by `pmcp.project_consent`, which `trust_store` imports; `trust_store` already imports `find_project_root` from loader **lazily** to break that cycle. Mirror that — import `trust_store` **inside** `set_startup_policy` (function-local), not at module scope, and confirm a clean-interpreter import still works.

## Changes

### `src/pmcp/config/loader.py` (modify)
- `set_startup_policy` — modify — capture the pre-write approval state and re-record after the write:
  - Immediately before `_atomic_write_json` in the `should_write` branch, read the current on-disk bytes: `old_bytes = target.path.read_bytes()` guarded by `try/except OSError` (a source being created for the first time has no prior bytes → `was_approved = False`).
  - `was_approved = trust_store.is_approved(target.path, old_bytes)` (function-local `from pmcp import trust_store`).
  - After `_atomic_write_json(target.path, data)`, if `was_approved`: read the exact written bytes `new_bytes = target.path.read_bytes()` and call `trust_store.record(target.path, new_bytes, cli._TRUST_SCOPE, trust_store.APPROVED)`. Wrap the `record` call in `try/except trust_store.TrustStoreError` — if the store is unusable (e.g. checkout-resident, post-#252), append a `StartupPolicyDiagnostic(code="approval_not_carried_forward", message=..., source, path)` to the returned preview instead of crashing, and leave the file written (the operator's autoStart edit still applies; only the re-approval failed, and the next startup will fail safe by refusing the unapproved file).
  - Scope constant: to avoid a loader→cli import (cli imports loader), define the scope value where both can reach it. Prefer moving `_TRUST_SCOPE` to a neutral module (`trust_store` already owns approval concepts — add `trust_store.PROJECT_SCOPE` or reuse an existing constant) and have `cli._TRUST_SCOPE` reference it, so `set_startup_policy` and `pmcp trust approve` provably use the same scope. Decide the exact home during implementation; the invariant is *one shared constant*, not two literals.
  - Never re-record on a dry run or a no-op (`should_write` already gates this).
- `StartupPolicyPreview` — modify (if needed) — it already carries `diagnostics`; add a boolean like `approval_carried_forward` only if the CLI/JSON consumer needs to show it. Optional; the diagnostic path covers the failure case.

### `src/pmcp/cli.py` (modify)
- `set-startup-policy` handler (`:1898`) — modify — after printing `After:`, if the preview reports the approval was carried forward (or a diagnostic says it was not), print one human line so the operator knows their approval survived (or did not). Keep it a single line; do not change the JSON shape beyond the new field if one is added.

### Documentation impact
- `SECURITY.md` — modify — limitation **C-28** currently records this as a known gap ("an operator write that invalidates their own approval"). After the fix, C-28 either moves to a guarantee (the approval is carried forward) or is narrowed to the residual (a checkout-resident store still can't be re-recorded). Update C-28 and add the new test to its evidence; re-run `scripts/check_security_claims.py` (must stay exit 0). **This is load-bearing** — the claim-ledger checker will fail if C-28's wording and evidence drift from the code.
- CONSENT post-execution amendment 10b references this defect — leave the historical amendment, but the roadmap/plan text that calls it "open" should note it is closed by this change.

## Dependencies & order
- **Depends on #252** (`record()` refusing a store resident in the enclosing checkout). The re-record calls `record()`, so it inherits #252's residency behaviour; the `try/except TrustStoreError` diagnostic path is what keeps that interaction graceful. Land #252 first (or at least reason about the interaction) so the re-record's failure mode is the intended one.
- Resolve the shared-scope-constant placement before writing code (avoids a loader↔cli import cycle).

## Verification
- `uv run pytest -q -p no:cacheprovider tests/test_startup_policy*.py tests/test_project_consent_gate.py tests/test_trust_store.py tests/test_trust_cli.py` — existing behaviour intact.
- New test (e.g. `tests/test_startup_policy_reapproval.py`), driving the real `set_startup_policy` + `trust_store`:
  1. Approve a project `.mcp.json` (`trust_store.record` / `pmcp trust approve`), assert `is_approved(path, old_bytes)`.
  2. Run `set_startup_policy(add ...)` with `apply=True, dry_run=False`.
  3. Assert the file changed AND `is_approved(path, path.read_bytes())` is **True** (approval carried forward), and a fresh `read_and_gate_project_config` / `load_configs` loads the server without a consent refusal.
  4. A **not-previously-approved** `.mcp.json`: after `set_startup_policy(add)`, `is_approved(path, new_bytes)` is **False** (no auto-approval created).
  5. `dry_run=True`: no record written.
  6. Checkout-resident store (post-#252): `set_startup_policy` returns `ok=True` with an `approval_not_carried_forward` diagnostic and does not raise.
- Mutation: drop the re-record call → test (3) goes red (`is_approved` False after the rewrite).
- `scripts/check_security_claims.py SECURITY.md` exit 0; `mypy src/`; `ruff check` + `ruff format --check`.

## Acceptance criteria
- [ ] After `pmcp trust approve <.mcp.json>` then `pmcp startup add --source project`, `trust_store.is_approved(<.mcp.json>, <new bytes on disk>)` is `True` and the next `load_configs` applies the server with no consent refusal.
- [ ] `set_startup_policy` does **not** create an approval for a `.mcp.json` that was not already approved.
- [ ] A dry-run `set_startup_policy` records nothing.
- [ ] When `record()` refuses (checkout-resident store), `set_startup_policy` returns `ok=True` with an `approval_not_carried_forward` diagnostic and does not raise.
- [ ] `set_startup_policy` and `pmcp trust approve` use one shared scope constant (no duplicated literal).
- [ ] `scripts/check_security_claims.py` exits 0 with C-28 updated and the new test cited.

## Notes for the implementer
- Re-read the written bytes for the digest; never re-serialise (drift risk vs `_atomic_write_json`'s exact format).
- Keep the `trust_store` import function-local in `loader.py` (import-cycle; mirror `find_project_root`'s lazy import).
- This is the same family as #252 (an operator write vs. the content-keyed gate); land after or alongside #252 so the residency failure mode is the intended one.
