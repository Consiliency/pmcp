# Mutation evidence — Consiliency/pmcp#183

Protocol: `src/**/__pycache__` purged and `PYTHONDONTWRITEBYTECODE=1` on every
apply/restore, per the tooling note in `mutation-180.md`. Stale bytecode
fabricated a false RED during #184 and can equally fabricate a false GREEN.

## 1. The refusal removed (the #183 defect itself)

    FAILED ...::TestUpdateServerVersionRepair::test_update_server_never_probes_a_script_name
    1 failed in 2.06s

The test asserts **no probe was executed**, not merely that the parse changed.
The correctness seat independently reproduced this end-to-end on a copy by
reverting `version_checker.py` to main, and captured what the defect actually
produces:

    UpdateServerOutput(ok=True, ...,
      message="Updated and restarted 'scripted' (npm:mcp); the new version is now active.")

Fabricated success, plus `npx -y mcp@latest` installed and run from the public
registry. That is the whole issue in one line of output.

## 2. Allowlist reverted to the denylist that shipped first

    FAILED ...::test_a_subcommand_without_a_package_operand_fails_closed[args9]
    FAILED ...::test_a_subcommand_without_a_package_operand_fails_closed[args10]
    5 failed, 9 passed, 136 deselected in 0.33s

**This is the mutant that mattered, and the first implementation did not kill
it.** The original fix denied `run` and `create` only, so everything else fell
through as an installable package name:

    npm start        -> ('npm', 'start')      -> npx -y start@latest
    npm test         -> ('npm', 'test')       -> npx -y test@latest
    npm run-script X -> ('npm', 'run-script') -> npx -y run-script@latest
    npm init foo     -> ('npm', 'init')       -> npx -y init@latest
    npm rum mcp      -> ('npm', 'rum')        -> npx -y rum@latest   (a TYPO)

Every one of those names resolves on registry.npmjs.org. All three CLI seats
plus the correctness seat found this independently and all four recommended the
same repair: invert to an allowlist.

The design lesson is the reusable part. A denylist **fails open**, which is
backwards for a fix whose purpose is preventing unintended execution: the cost
of omitting an entry is arbitrary package execution, while the cost of an
over-broad allowlist is only that an unusual launch form cannot be
auto-updated. I had closed the two forms the issue named and left the class
open -- treating the issue's examples as the specification.

Two related errors worth recording:

* `run-script` is npm's **canonical** command; `run` is the alias. I fixed the
  alias and missed the real name.
* `init` is the alias of `create`, and I fixed only `create`.

## Pre-existing tests inverted, not weakened

`test_npm_command` and `test_npm_with_no_subcommand_still_finds_the_package`
both asserted `npm -y server-pkg` -> `server-pkg`. That is not a legal npm
invocation (npm requires a subcommand), so those assertions had encoded the
parser's old permissiveness -- the general form of this very defect. They are
now inverted, with the reason in their docstrings, rather than the guard being
relaxed to keep them green.

---

# Board round on #182 (PR #190) — two real defects, two false alarms

Four seats. grok AGREE, gemini AGREE, codex DISAGREE, fable PARTIALLY AGREE.
Every finding checked against source before acting; two were real, two were not.

## REAL — a collision the fix itself introduced (red-team seat)

`_pep508_base_name` listed `@` among the name terminators, so a PEP 508
**direct reference** truncated to its bare name:

    pkg @ git+https://x/y  ->  ('pypi', 'pkg')
    pkg @ git+https://x/z  ->  ('pypi', 'pkg')     COLLIDES

Two different repositories, one identity — the exact defect class this change
exists to close, newly created by the fix for it. It also violated this plan's
own acceptance criterion that distinct URLs keep distinct identities; the test
covered only the *bare* URL form and missed the standard named one.

Fixed by removing `@` from the terminator set. Verified after: the two repos
resolve distinctly, and extras/version normalization still works
(`browser-use[cli]` -> `browser-use`, `index-it-mcp==1.2.0` -> `index-it-mcp`).

## REAL — the 21st mutant, and my fix for it had a hollow spot (correctness seat)

Deleting the `following.startswith("-")` arm left **all 364 tests green** while
`uvx --from --offline a` and `... b` both resolved to `('pypi','--offline')`.
Verified end-to-end: `_same_package('--offline','pypi','--offline','pypi')`
returns **True**, so the mutant's collision passes the identity gate.

Then — mutation-proving my own patch rather than trusting it — the third mutant
(dropping the `following is None` arm) **passed all four new tests**. My
end-of-argv case used `["--from"]`, which returns before ever reaching the
guard. Probed five inputs for one that distinguishes the arm; none does. It is
genuinely **unreachable**: a trailing positive flag ends the loop and falls
through to unknown anyway. The test now says so rather than implying a proof.

## NOT REAL — and the disclosures are why

- *"Missing body in `test_readme_documented_pin_form_resolves_to_the_package`"*
  — it has a full assertion body. The seat read the docstring as the whole
  test. That docstring already discloses which half the test pins.
- *"Unreachable fallthrough in `_scan_for_package_token`"* — the `--` branch is
  already labelled in-code as measured-redundant-but-kept, with the reasoning
  inline.

Both false alarms landed on spots the implementer had *already* labelled as
non-discriminating. Writing "this does not pin what it looks like it pins"
directly into the code did not stop a reviewer flagging it — but it made the
claim checkable in seconds instead of requiring a fresh investigation.
