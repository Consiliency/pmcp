# Detailed plan: re-record the trust approval when `set_startup_policy` rewrites `.mcp.json` (#253)

## Task
`pmcp startup add/remove --source project` (→ `config/loader.py::set_startup_policy`) rewrites the project `.mcp.json` via `_atomic_write_json`, changing its bytes. v13 trust approval is **content-keyed** (`trust_store.is_approved(path, content)` matches a SHA-256 of the exact bytes). So an operator write silently invalidates the operator's own prior `pmcp trust approve <.mcp.json>`, and the next startup refuses the now-unrecognised file with the consent gate's "not approved" message.

**Decision (owner, 2026-09-18): re-record.** After writing the new bytes, if the pre-write bytes were approved, re-record an `approved` decision for the new bytes — the operator explicitly ran this write, so their consent carries forward. Do **not** create a new approval for a file that was not already approved.

## Research summary
- `set_startup_policy` (`src/pmcp/config/loader.py:723`) resolves a single target source, reads it via `_read_config_object`, computes the new `autoStart`, and — only when `should_write = operation.apply and not operation.dry_run and changed` — sets `data["autoStart"]` and calls `_atomic_write_json(target.path, data)` (`:803-805`). It returns a `StartupPolicyPreview` carrying `ok`, `source`, `path`, `changed`, `dry_run`, `before_autoStart`, `after_autoStart`, `message`, `diagnostics`, `next_step` (the error branches already return `ok=False`; the success branch `ok=True`, so AC-4's `ok=True` uses an existing field).
- `_atomic_write_json` (`:712`) writes `json.dump(data, indent=2)` + a trailing `"\n"` via a temp file + `replace`. **Do NOT re-read the file after the write to get the bytes to approve** — that opens a TOCTOU window (a concurrent writer could substitute attacker bytes between the write and the re-read, and approval would be recorded for content the operator never consented to; panel finding, codex). Instead the writer serialises once and returns the exact bytes it wrote, and approval is recorded for *those* bytes. If another process overwrites the file after our write, the recorded digest simply no longer matches on-disk and the next startup fails safe by refusing it — which is correct.
- `trust_store.is_approved(path, content)` (`src/pmcp/trust_store.py:425`) matches on resolved path + content SHA + `decision == APPROVED`; it **ignores scope** and never raises. `trust_store.record(path, content, scope, decision)` (`:450`) writes the record and **raises `TrustStoreError` if the store is checkout-resident / unreadable / corrupt** (this is the residency guard, and post-#252 it also refuses a store resident in the checkout enclosing `path`).
- `pmcp trust approve` records with scope `cli._TRUST_SCOPE` (`src/pmcp/cli.py:2618`). Re-record with the **same** scope for consistency.
- Import posture: `config/loader.py` is imported by `pmcp.project_consent`, which `trust_store` imports; `trust_store` already imports `find_project_root` from loader **lazily** to break that cycle. Mirror that — import `trust_store` **inside** `set_startup_policy` (function-local), not at module scope, and confirm a clean-interpreter import still works.

## Changes

### `src/pmcp/config/loader.py` (modify)
- `_atomic_write_json` — modify — **return the exact bytes it writes.** Serialise once (`payload = json.dumps(data, indent=2) + "\n"`), write `payload.encode("utf-8")`, and return those bytes. The re-record uses this return value; it MUST never `read_bytes()` after the write (the TOCTOU hole above). Update its one other caller accordingly (the return is additive — callers that ignore it are unaffected).
- `set_startup_policy` — modify — bind approval to a single captured input snapshot, in this exact order:
  - **Capture the input ONCE**: `input_bytes = target.path.read_bytes()` (guard `OSError` → `was_approved = False`, a first-time source has no prior bytes). Parse the object to mutate FROM `input_bytes` (the same bytes), not via a second independent read of `target.path`, so the bytes checked for approval are exactly the bytes being transformed. (Reconcile with the existing `_read_config_object(target.path)` read: read once, reuse; do not check approval of one read while transforming another.)
  - `was_approved = trust_store.is_approved(target.path, input_bytes)` (function-local `from pmcp import trust_store`).
  - In the `should_write` branch: `output_bytes = _atomic_write_json(target.path, data)` — the exact bytes written, returned by the writer.
  - If `was_approved`: `trust_store.record(target.path, output_bytes, <shared scope>, trust_store.APPROVED)` — approve exactly the bytes just written, derived deterministically from the approved input snapshot. **No post-write read.** Wrap in `try/except trust_store.TrustStoreError` → append a `StartupPolicyDiagnostic(code="approval_not_carried_forward", ...)` and leave the file written (checkout-resident store, post-#252; do not crash — the next startup fails safe). The failure-diagnostic test exercises this via `record_resolved` too.
  - **Pin the canonical trust key; do not let recording re-resolve a mutable path (panel finding, codex — path-identity race; gemini — make the contract explicit).** Both `is_approved` and `record` call `Path(path).resolve()` internally, so a symlink swap of `target.path` between the approval check and the record (or between the write and the record) redirects resolution: `A` is approved, then `A` is replaced by a symlink to a previously-unapproved `B` holding `output_bytes`, and `record(target.path, ...)` resolves `A`→`B` and approves `B` — transferring consent to a file the operator never approved. Closing only the byte-content race is insufficient. Bind consent to ONE canonical identity captured up front:
    - Resolve the trust key exactly once, with the input snapshot: `pinned_key = target.path.resolve(strict=False)`.
    - Add a keyed trust_store API that treats the given path as the canonical key **verbatim, with no internal re-resolution**: `trust_store.is_approved_resolved(pinned_key, content)` and `trust_store.record_resolved(pinned_key, content, scope, decision)` (refactor `is_approved`/`record` to resolve-then-delegate to these, so existing callers are unchanged). Use the `_resolved` pair here with `pinned_key`.
    - **Symlink contract (explicit):** a project `.mcp.json` that is itself a symlink is refused up front — `set_startup_policy` rejects it (raise/`invalid_source` diagnostic) rather than rewriting through it — so `_atomic_write_json`'s `os.replace` and the recorded key refer to the same real file and cannot diverge. (The earlier fd/`O_NOFOLLOW` option is dropped: an fd opened before the write points at the pre-`replace` inode, so it cannot key the post-`replace` file — incompatible with atomic replace, per gemini.)

- `StartupPolicyPreview` — modify (if needed) — optional `approval_carried_forward` bool; the diagnostic path covers the failure case.

### `src/pmcp/trust_store.py` (modify)
- `is_approved_resolved(resolved_path: Path, content: bytes) -> bool` and `record_resolved(resolved_path: Path, content: bytes, scope, decision) -> TrustRecord` — add — the canonical-key variants that do NOT re-resolve. Refactor the existing `is_approved`/`record` to `resolve()` their `path` argument and delegate to these, so behaviour for existing callers is byte-identical and `set_startup_policy` can pass a `pinned_key` that is never re-resolved. This is the concrete home of the "pin the canonical key" invariant.

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
  7. **Substitution / consent-bypass test (panel finding, codex + grok):** the load-bearing window is a substitute AFTER `_atomic_write_json` returns (a pre-write substitute is harmlessly clobbered by `replace`, so testing only that window would pass even for the buggy post-write-read design). The test MUST: (a) let a concurrent process replace `.mcp.json` with attacker bytes immediately after `_atomic_write_json` returns; (b) **spy on `trust_store.record_resolved`** (the API `set_startup_policy` actually calls — spying `record` would never fire, since `record` only resolves-then-delegates, and calling `record` here would reintroduce the path race) and assert its `resolved_path` argument equals the `pinned_key` AND its `content` equals the writer's returned bytes AND equals an independent `json.dumps(parse(input_snapshot) with autoStart)` — never trust a disk re-read as the oracle; (c) assert `is_approved(path, attacker_bytes)` is False. This proves approval is bound to the writer's output, not to whatever is on disk at record time. The happy-path and mutation tests do not catch this — it is a required case.
  8. **Path-identity substitution test (panel finding, codex):** after the write, replace `target.path` with a symlink to a previously-UNAPPROVED file `B` whose contents equal `output_bytes`; assert that `is_approved(B, output_bytes)` is False afterward — consent was recorded against the pinned canonical key, not transferred to `B` by a re-resolution at record time. A mutation that lets `record` re-resolve the mutable path must turn this red.
- Mutation: drop the re-record call → test (3) goes red (`is_approved` False after the rewrite).
- `scripts/check_security_claims.py SECURITY.md` exit 0; `mypy src/`; `ruff check` + `ruff format --check`.

## Acceptance criteria
- [ ] After `pmcp trust approve <.mcp.json>` then `pmcp startup add --source project`, `trust_store.is_approved(<.mcp.json>, <new bytes on disk>)` is `True` and the next `load_configs` applies the server with no consent refusal.
- [ ] `set_startup_policy` does **not** create an approval for a `.mcp.json` that was not already approved.
- [ ] A dry-run `set_startup_policy` records nothing.
- [ ] When `record()` refuses (checkout-resident store), `set_startup_policy` returns `ok=True` with an `approval_not_carried_forward` diagnostic and does not raise.
- [ ] `set_startup_policy` and `pmcp trust approve` use one shared scope constant (no duplicated literal).
- [ ] `trust_store.record_resolved` is called with the `pinned_key` and the writer's returned bytes (verified by spying it, not by a disk re-read); `set_startup_policy` never calls the re-resolving `trust_store.record`; a file substituted by another process AFTER the write returns does NOT receive approval (`is_approved(path, attacker_bytes)` is False).
- [ ] Approval is recorded against the canonical trust key pinned once up front; a symlink substituted at `target.path` after the write cannot transfer the approval to a different (previously-unapproved) file.
- [ ] `scripts/check_security_claims.py` exits 0 with C-28 updated and the new test cited.

## Notes for the implementer
- Record approval from the writer's returned bytes; NEVER `read_bytes()` after the write (TOCTOU consent-bypass — panel finding). Serialise once, write, return, and approve that same value.
- Keep the `trust_store` import function-local in `loader.py` (import-cycle; mirror `find_project_root`'s lazy import).
- This is the same family as #252 (an operator write vs. the content-keyed gate); land after or alongside #252 so the residency failure mode is the intended one.
