# Detailed plan: an auto-discovered policy that is a policy must not fail open

## Task

Close Consiliency/pmcp#202. A policy file at a default location that fails to load
is discarded, and the gateway falls back to `GatewayPolicy()` — which is
**allow-all**. Allow/deny lists, limits and redaction all revert to permissive.

**The fix splits by failure mode**, because the two are not the same event:

| the file… | today | after |
|---|---|---|
| does not exist | no policy, silent | unchanged — legitimately "no policy" |
| exists, cannot be parsed at all | warn, allow-all | **unchanged** — see below |
| parses as a document, fails schema validation | warn, allow-all | **refuse to start** |

## Research summary

Verified in this worktree.

**The fallback is `GatewayPolicy()`, and it is allow-all.** Every field is a
`default_factory` (`types.py:988-999`), so a discarded policy is not a partial
policy — it is no policy.

**`_load_policy` (`policy/policy.py:59-78`) has one `try` covering three distinct
failures**: `read_text`, the YAML/JSON parse, and `GatewayPolicy.model_validate`.
Only `fatal` distinguishes the caller, not the failure. The split below needs the
parse boundary made explicit — it already exists in the code's structure, it is
simply not observable to the caller.

**`fatal=False` appears exactly once in `src/`** (`policy.py:54`), inside the
auto-discovery loop, which `break`s at the first existing path. Explicit
`--policy` uses `fatal=True` and raises correctly. Four constructors pass a
possibly-`None` path (`server.py:146`, `cli.py:874`, `:1135`, `:2433`), so
auto-discovery is the common path in practice.

**The current behaviour is deliberate and pinned by a test.**
`test_scoped_advisor_audit.py:87`,
`test_explicit_policy_failures_are_fatal_but_default_discovery_is_best_effort`
(added in `5ec4545`), asserts all three failure modes are fatal for `--policy`
and that a **malformed — i.e. unparseable — file** (`"{"`) at a default path
falls back with `is_gateway_tool_allowed("gateway.provision") is True`.

That is why this plan does **not** simply make everything fatal. A file that
cannot be parsed at all could be anything — a half-written file, an unrelated
`.json` someone dropped at the repo root, a merge conflict. A file that parses as
a mapping but fails `GatewayPolicy` validation is unmistakably *a policy with a
mistake in it*, and that is the case where falling back to allow-all is
indefensible. The existing test's intent is preserved exactly; only the case it
does not cover changes.

**`DEFAULT_POLICY_PATHS` is built at import time** (`policy.py:31-36`), so its two
`Path.cwd()` entries freeze the working directory as of module import rather than
`PolicyManager()` construction. That reaches the same unrestricted state by a
different road — and **that road has no warning at all**, because "no policy
file" is legitimately silent.

## Changes

### `src/pmcp/policy/policy.py` (modify)

- `DEFAULT_POLICY_PATHS` (`:31`) — modify — replace the module-level list with
  `_default_policy_paths()` evaluated at construction, so `Path.cwd()` is read
  when the manager is built. Keep the name exported as a function or keep a
  module-level constant for the two `Path.home()` entries; the two `cwd` entries
  are the ones that must move.
  **Compatibility note:** `test_scoped_advisor_audit.py:104` monkeypatches
  `pmcp.policy.policy.DEFAULT_POLICY_PATHS`. Whatever shape is chosen must keep
  that patch point working, or that test must be updated in the same change —
  silently breaking a test's seam is how a guard stops guarding.
- `_load_policy` (`:59`) — modify — separate *parse* from *validate* so the
  caller can tell them apart. Read + parse in one `try`; `model_validate` in a
  second. On the auto-discovery path:
  - parse failure (including `read_text` failure and the non-mapping root) →
    warn and continue, exactly as today;
  - **validation failure → raise**, with a message naming the path and the
    validation error, distinct from the explicit-policy message so the two are
    not confused in a log.
- The `except` message (`:78`) — modify — the current warning says "Failed to
  load policy from …". Once the two cases diverge it must say which one happened
  and, for the surviving warn-path, state plainly that **no policy is in effect**.
  A reader of that line today cannot tell that the gateway is now unrestricted.

### `tests/test_scoped_advisor_audit.py` (modify)

- `test_explicit_policy_failures_are_fatal_but_default_discovery_is_best_effort`
  — modify — keep its unparseable-file case asserting the fallback, since that
  intent is preserved. Rename it so the name states the new, narrower rule
  (best-effort applies to *unparseable*, not to *invalid*), because the current
  name will otherwise assert something broader than the code does.

### `tests/test_policy_fail_open.py` (create)

- A schema-invalid file at a default path → `PolicyManager()` **raises**, and the
  message names the path.
- An unparseable file at a default path → does **not** raise, and the warning
  says no policy is in effect.
- A **valid** file at a default path → loads, and a deny rule in it is actually
  enforced. Without this the suite cannot tell "refused correctly" from "refused
  everything".
- No file at any default path → no raise, no warning (unchanged).
- `Path.cwd()` is honoured at **construction**: `chdir` into a directory
  containing a valid policy after import, construct, and assert the policy
  applies. This test must fail against `main`.
- The explicit `--policy` path still raises for all three modes (unchanged).

## Documentation impact

- `CHANGELOG.md` — add — `### Changed`, not `### Fixed`: a gateway that starts
  today can refuse to start after this. Name the exact condition (an
  auto-discovered file that parses but fails validation) and say that an
  unparseable file still warns and continues.
- `README.md` — modify **only if** it documents policy auto-discovery; check
  before editing.

## Dependencies & order

1. The cwd fix first — it is independent, and doing it first means the
   discovery-path tests are written against the corrected path resolution.
2. The parse/validate split.
3. Tests, including the must-fail-on-`main` cwd test.
4. CHANGELOG.

## Verification

```bash
uv run pytest -q tests/test_policy_fail_open.py tests/test_scoped_advisor_audit.py
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/

# The cwd test must fail against main -- prove it rather than assume:
#   stash nothing; run the new test with PYTHONPATH pointed at a clean
#   `git archive` export of main, expect RED, then against this branch, GREEN.
```

Edge cases: a default path that exists but is unreadable (permissions); a YAML
file whose root is a list; an empty file (`yaml.safe_load` → `None`); a file
whose suffix is `.json` but whose content is YAML; two default paths where the
first is invalid and the second is valid — the loop `break`s at the first
*existing* path, so the second is never reached, and that should stay true.

## Acceptance criteria

- [ ] A **schema-invalid** file at a default path makes `PolicyManager()` raise,
      and the error names the path. Proven by a test that fails against `main`.
- [ ] An **unparseable** file at a default path still does not raise — the
      existing tested intent, asserted by the original test's own case.
- [ ] A **valid** file at a default path still loads *and enforces*, proven by a
      deny rule actually denying. A change that refuses everything must not pass.
- [ ] `Path.cwd()` is read at construction: `chdir` after import into a directory
      holding a valid policy, construct, and the policy applies. **This test must
      be shown RED against `main`** — the import-time freeze is invisible to any
      test that does not chdir between import and construction.
- [ ] The surviving warn-path message states that **no policy is in effect**;
      asserted on the emitted text, not on the log level alone.
- [ ] `pmcp.policy.policy.DEFAULT_POLICY_PATHS` remains monkeypatchable, or
      `test_scoped_advisor_audit.py:104` is updated in the same change.
- [ ] Full suite green.

## Non-goals

- Changing explicit `--policy` behaviour. It already raises on all three modes.
- A policy *merge* or partial-application model. A policy is applied whole or not
  at all; that is not in question here.
- Auditing other fail-open fallbacks elsewhere in the codebase. If any exist they
  deserve their own issue rather than being swept in.

## Execution Policy

- execute: effort=medium, reason=small diff on a security control, where the
  risk is a change that refuses too much rather than too little
