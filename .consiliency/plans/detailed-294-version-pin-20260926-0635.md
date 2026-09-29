# Detailed plan: version pinning and drift detection for self-hosted-backed MCP clients (Consiliency/pmcp#294, proposals 1 and 2)

> **Bounded-plan verdict: within threshold.** Four source files change
> (`src/pmcp/manifest/loader.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/types.py`,
> `src/pmcp/cli.py`), plus one new test file, a one-stub edit to
> `tests/test_pkgid_panel_fixes.py` (keeps an existing test offline), and three docs
> (README, CHANGELOG, CONTRIBUTING). There are two
> conceptually distinct changes: (1) a first-class `version:` / `server_version:` pin that
> `gateway.update_server` reports on, and (2) an advisory warning for an unpinned client
> in front of a self-hosted backend. Proposals 3 (post-update smoke probe) and 4 (client
> version in error hints) are **named follow-up slices** with a design note each. They are
> not planned here.
>
> **Base: `origin/main` = `7680445`** (re-fetched for revision 22: unchanged. Revision 22 ran
> on it: gates clean, 493 pin tests, the full suite with `-x --timeout=600` exit 0, 5200
> passed, 3 skipped, 80 deselected. Re-fetched for revision 21: unchanged. Revision 21 ran
> on it: gates clean, 490 pin tests, the full suite with `-x --timeout=600` exit 0, 5197
> passed, 3 skipped, 80 deselected. Re-fetched for revision 20: unchanged. Revision 20 ran
> on it: gates clean, 479 pin tests, the full suite with `-x --timeout=600` exit 0, 5186
> passed, 3 skipped, 80 deselected. Re-fetched for revision 19: unchanged. Revision 19 ran
> on it: gates clean, 461 pin tests, the full suite with `-x --timeout=600` exit 0, 5168
> passed, 3 skipped, 80 deselected. Re-fetched for revision 18: unchanged. Revision 18 ran
> on it: gates clean, 449 pin tests, the full suite with `-x --timeout=600` exit 0, 5156
> passed, 3 skipped, 80 deselected. Re-fetched for revision 17: unchanged. Revision 17 ran
> on it: gates clean, 435 pin tests, the full suite with `-x` 5142 passed, 3 skipped, 80
> deselected (the Revision 17 section records a first `-x` run under mutant load that hit a
> flake in main's `test_monitor_reads_stderr`). Re-fetched for revision 16: unchanged. Revision 16's
> spike, mutants and full suite ran on `7680445` itself: gates clean, 429 pin tests, full
> suite 5136 passed, 3 skipped, 80 deselected. Re-fetched for revision 15: main moved from `4d2790f` by
> Consiliency/pmcp#310, the additive redaction layer: `redaction_additive.py`, `auth.py`,
> `policy/policy.py` and new test files, none of them touched by this plan. The patch applies
> there byte-identically, and gates (171 files formatted, mypy 52 source files), the 407 pin
> tests and the full suite pass there, 5114 passed, 3 skipped, 80 deselected in 543.96s (0:09:03). Revision 15's spike and mutants were run
> on `4d2790f`, where the full suite gave 4908 passed with the first 405-test cut. Revision 14 re-fetched `4d2790f`
> (Consiliency/pmcp#288, plan-only) and passed there, 4891 passed.
> Revisions 10-14's spikes and mutants were run on `260cc1a`; `876fd33` for revisions 7-9; `959d4d4` since revision 5). The reference patch applies to
> `260cc1a`, `876fd33` and `959d4d4`, all of which carry Consiliency/pmcp#299 (the exact-version check follows
> npm's classification) and Consiliency/pmcp#300 (gateway tool schemas derived from their
> models). The plan branch itself still descends from `9ca081e`, and its commits change
> only this file and `plans/manifest.json`. The revision-22 numbers were **measured this
> session** on throwaway spike worktrees. For revision 10, main moved from `876fd33` to
> **`260cc1a`** (Consiliency/pmcp#304, Consiliency/pmcp#306: scoped audit, `server.py`, a new test file;
> nothing this plan touches, and no new spawn site: the spawn-site test passes there). The
> patch applies to `260cc1a` byte-identically, and gates, pin tests and the full suite were
> re-run on it. Before that, the spikes were off `959d4d4`. Re-fetched for revision 7, main had
> moved to **`876fd33`** (Consiliency/pmcp#303: `auth.py`, `keyword_matcher.py`, a new test
> file and CHANGELOG, none of them touched by this plan). The patch applies to `876fd33`
> unchanged (byte-identical `git diff`), and the gates and the full suite were re-run there
> (Verification steps 3, 5 and 11). All spike worktrees were removed afterwards. The spike diff and the new test file are
> embedded verbatim below as the reference patch; extracting both from this file and
> `cmp`-ing them against the spike is part of Verification (step 11).

## Revision 22 (2026-09-29): board round 20 on `a49dc28`: every judge diagnostic through the secret-safe renderer

In the round-20 external board (a slim bundle), claude AGREED (with N1-N3) and gemini raised
no blocker. codex found one blocker in the revision-20/21 code, and it was **reproduced
first** against revision 21 (`repro_r22.py`, appendix).

**B1: the interpreter diagnostic quoted the whole requirement.** `_read_uvx` built its
problem as `uv reads {_operator_safe(requirement_text)!r} …`. `_operator_safe` escapes and
shell-quotes, but it does **not** redact, and `reading.problem` flows into the health
warning and `update_server`. So `uvx --from "python @
https://user:SYNTHETIC_REVIEW_TOKEN@example.test/python.whl" python` put the token in the
warning verbatim; a query-string token did the same.

**The class, measured.** The same construction, `_operator_safe(<entry text>)`, was used
for more than the interpreter message, and every one leaked on revision 21 (`repro_r22.py`,
property test):
- the cargo `+toolchain`;
- the cwd path, in all three cwd problems;
- an unknown flag's name before `=` (`--https://user:…@…`);
- the launcher spellings.

**Fixed as the class. Every string the one parser, the per-member judge or the warning
builds is made only of:**
- **(a) fixed text;**
- **(b) a value through a closed grammar** in `pmcp/manifest/loader.py`, shared with the
  loader's own pin logs:
  - `diag_name`: `@?[A-Za-z0-9][A-Za-z0-9._+/-]{0,127}`, for package, image, command,
    env-KEY, relaxer and install-target names;
  - `diag_flag`: `--?[A-Za-z0-9][A-Za-z0-9-]{0,63}`, for a flag's name, never its value;
  - `diag_selector`: `[A-Za-z0-9.+*^~<>=!|, -]{1,64}` or `sha{256,384,512}:<hex>`, for a
    version, range, dist-tag or digest;
  - none admits `:`, `?`, `&`, `#`, `%`, a control character or an `@` after the first
    character, so no URL userinfo, query string, `k=v` value or env value can pass;
    anything else is `<redacted>`;
- **(c) the one secret-safe renderer** (`_render_argv`, which is
  `installer._render_install_argv`), for an argv or an executable.

What each diagnostic now shows:
- **The interpreter message** names only the matched interpreter, from a closed set: `uv
  reads this --from as an interpreter request (python), not a package`.
- **The cwd** is no longer shown: `the entry sets a cwd (not shown), …`. The operator knows
  the entry's cwd; the path is entry text.
- **`update_server`'s held/pinned text and `floating_selector`** go through
  `diag_selector`, because main's own pin reader can hand back raw requirement text.
- **The loader's "Ignoring version pin …" logs** go through `diag_selector` and `diag_name`,
  and no longer interpolate the exception text.
- **The one patch-added debug log** shows only the exception type.
- `_operator_safe` is no longer imported by `handlers.py`.

**Property test (`test_no_credential_reaches_a_warning_an_update_message_or_a_log`).** It
has 42 inputs, each carrying `SYNTHETIC_REVIEW_TOKEN` in a value position: URL userinfo, a
query string, `--flag=value`, `--token value`, an env value, a cwd, or an install argv. For
each input, the sentinel must not appear in the health warning, `update_server`'s message
and warnings, or any log line (`caplog` at DEBUG).

The test also proves it exercised **every** problem-producing branch:
- It records each `_Reading(problem=…)` construction.
- It requires every such call site, **found from the source with `ast` at test time**, to
  have been hit. There are 33, including the empty-spawn-set branch, which it calls
  directly.
- It requires all four member-problem kinds to appear.

So a new problem branch without a sentinel case fails the test.

**Static check (`test_every_judge_diagnostic_interpolates_only_allowed_sources`).** Every
`{…}` in 15 functions must be one of the allowed forms. The functions are the parsers,
`_docker_short_cluster`, `_read_argv`, `_member_problems`, `_verdict_of`, `_may_talk_reason`,
the warning, `_unverifiable_warning`, `_floating_reason`, `_install_members` and
`_Spawn.where`. The allowed forms:
- **A call** to `diag_name`, `diag_flag`, `diag_selector`, `_render_argv`, `_safe_token`,
  `_may_talk_reason` or `_floating_reason` (the last two are themselves checked).
- **A text another checked function built:** `reading.problem`, `verdict.detail`,
  `problems[0]`.
- **A closed value:** `reading.family`, `_CWD_CONFIG_FAMILIES[...]`,
  `type(exc).__name__`, `self.label`, `server_name` (the operator's own server key, which
  every main message shows).
- **One of six local names** (`flag`, `which`, `rendered`, `head`, `state`, `where`). Each
  of their assignments must itself be an allowed form.

This is narrower than "any sanitised text", and each allowance is justified above. The test
also asserts that `_operator_safe` does not appear in `handlers.py`.

**N1 (claude seat, folded in).** uv reads a `v`-prefixed request (`pythonv3.10`,
`cpythonV3`) as an interpreter. The version-request rule now allows `[vV]` after the
whitespace, so the message names it; it already failed closed.

| # | finding (round 20) | resolution | evidence (rev 21 → rev 22, `repro_r22.py`) |
|---|---|---|---|
| **B1** (codex, blocking) | A credential in the requirement reached the warning. | Every diagnostic through a closed grammar or the renderer; the interpreter message names only the interpreter. | userinfo `--from`, query `--from`, cargo `+<URL>`, a cwd, `--<URL>`: sentinel **LEAKS** → **absent** in every row. Tests: the property test (42 inputs, every problem branch found by `ast`), the static check, and `test_the_interpreter_diagnostic_names_only_the_interpreter` (the codex repro and the loader's pin log). Mutants: M159 (the rev-21 `_operator_safe(requirement_text)` restored), M160 (the cwd path shown), M161 (the toolchain raw), M162-M164 (`diag_selector`/`diag_name`/`diag_flag` admit anything); M82, M84, M86, M89, M142, M151, M155-M157 re-targeted. |
| N1 (claude) | `v`-prefixed interpreter request | `[vV]` allowed | same test; mutant M165 |

**Costs.**
- **Shipped cost** is unchanged: **0 of 77**. No shipped message changes except that a cwd
  path is no longer echoed.
- **Operator cost:** a name outside the closed grammars shows as `<redacted>`, for example
  a docker image with a registry port (`host:5000/img`), or a dist-tag with a `/`. The
  message still says which check failed.

**Full suite:** once with `-x --timeout=600`, with the npm env unset: exit 0, **5200
passed, 3 skipped, 80 deselected** in 762.82s. Nothing failed.

**Tests:** the revision-22 file has **493 tests** (490 in revision 21). Against the
revision-21 code it gives **28 failed, 465 passed**:
- the 3 new tests;
- 25 assertions whose wording changed on purpose: the cwd path is no longer in the text (6),
  and the interpreter message now reads `interpreter request (<name>)` (19).

The property test fails on revision 21 with **6 leaking inputs**: the two interpreter URLs,
the toolchain, two cwds and the flag-name URL.

## Rev 21 board findings — before/after, measured

`repro_r22.py` (appendix), one fresh process per row: a configured `firecrawl` entry,
`FIRECRAWL_API_URL` in the entry env. Trees: `7680445` + the revision-21 patch (`a49dc28`),
and `7680445` + the revision-22 patch.

| case | rev 21 | rev 22 |
|---|---|---|
| `uvx --from 'python @ https://user:<T>@…/python.whl' python` | **LEAKS**: `uv reads "'python @ https://user:SYNTHETIC_REVIEW_TOKEN@…'" as an interpreter request …` | absent: `uv reads this --from as an interpreter request (python), not a package` |
| `uvx --from 'python @ https://…?token=<T>' python` | **LEAKS** | absent: the same |
| `cargo +https://user:<T>@… install fc` | **LEAKS**: `its argv selects toolchain +https://user:SYNTHETIC_REVIEW_TOKEN@…` | absent: `its argv selects toolchain +<redacted>` |
| `npx -y firecrawl-mcp@1.0.0`, cwd `/srv/<T>` | **LEAKS**: `the entry sets cwd '/srv/SYNTHETIC_REVIEW_TOKEN' …` | absent: `the entry sets a cwd (not shown) …` |
| `uvx --https://user:<T>@… fc-mcp==1.0` | **LEAKS**: `its argv passes --https://user:SYNTHETIC_REVIEW_TOKEN@…` | absent: `its argv passes <redacted>` |

## Revision 21 (2026-09-29): board round 19 on `23e1e44`: interpreter check on the parsed name; [PINNED] only from the judge

The round-19 claude seat returned DISAGREE on one blocker, with no regressions (479 tests,
full suite 5186). Its blocker and its `[PINNED]` observation were **reproduced first**
against revision 20 (`repro_r21.py`, appendix).

**B1: whitespace after the interpreter name.** Revision 20's text rule wanted `@`, a digit
or an operator **directly** after the name. uv's version parser trims leading whitespace,
and `packaging` accepts a space or tab between the name and the specifier. So these were
read as exact pins, while real uv 0.12.19 ran interpreters:
- `uvx 'python ==3.10'`
- `uvx --from 'python == 3.10' python`
- `uv tool run 'cpython ==3.10'`
- `uvx $'python\t==3.10'`

**Fixed as the class (a).** `_read_uvx` refuses when **either** check says interpreter:
- **The parsed name:** `Requirement(...).name.lower()` is one of `python`, `pythonw`,
  `cpython`, `pypy`, `graalpy`, `pyodide`. Any spelling `packaging` accepts is covered,
  including `python[x]==3.10`, which is over-refused, and that is harmless.
- **The raw text, as uv reads it:** the version-request rest may now start with whitespace
  (`\s*`, dot-all). This catches spellings `packaging` rejects but uv runs, such as a
  vertical tab (measured: `python\v==3.10` ran 3.10.21). It also catches suffixed spellings
  `packaging` reads as another name, such as `Python3.10`.

**(b) `[PINNED]` only from the judge.** On revision 20, `update_server`'s label read main's
own pin reader (`_detect_effective_version_pin`, `pinned_to`) and required the judge's
`silent` verdict and an equal selector, so the judge already gated it. Now the judge's
selector is the **only** value the label can carry:
- `judge_pin` is set only in the `silent` branch;
- `exact = judge_pin is not None and …`;
- the reported pin is `judge_pin`;
- `pinned_to` still decides that `update_server` **stops**, which is main's own refusal, and
  never that it says "pinned".

A test drives argvs the judge refuses but main's reader pins (`python ==3.10`, a tab, an
extra, an env problem) and asserts that neither `pinned_version` nor `[PINNED]` appears.

On revision 20, main's reader gave `' 3.10'` (with a leading space) for `--from 'python ==
3.10'`, which never equalled the judge's `3.10`, so that one form was `[FLOATING]`, not
`[PINNED]`. The bare and tab forms were `[PINNED]`.

**Differential against measured uv (`measure_uv.py`).** The seat's generator: 28 name
spellings × 6 separators (none, space, two spaces, tab, newline, ` (…)`) × 18 version tails.
That is 3,024 components, each run through real uv 0.12.19 in all three launch forms
(offline, no cache, cwd `/tmp`):
- **I (interpreter): 880.**
- **P (package): 1,975.** This includes offline "no solution" resolutions.
- **E (uv rejects the spelling, nothing runs): 169.**
- **The three forms gave identical tables.**

The table is embedded in the test file (`_UV_MEASURED`, 3.5 KB). Of the 9,072 argvs:
- **revision 20** read **891** of uv's interpreter argvs as exact pins;
- **revision 21** reads **0**.

pmcp refuses 2,289 argvs that uv reads as packages. Every one fails closed: markers,
parentheses, `pythonw` on Linux, `python3==…`, extras.

| # | finding (round 19) | resolution | evidence (rev 20 → rev 21, `repro_r21.py`) |
|---|---|---|---|
| **B1** (blocking) | Whitespace between the interpreter name and the version request was read as an exact pin. | Parsed-name check plus a whitespace-skipping text check. | The 4 spellings plus `python[x]==3.10`: warning `None`, verdict `silent` exact → `talks …, but pmcp cannot verify …: uv reads '…' as an interpreter request, not a package`. Control `python-dotenv==1.0.1` unchanged. Differential `test_no_argv_real_uv_runs_as_an_interpreter_is_read_as_a_pin` (880 measured cells × 3 forms); `test_a_uv_interpreter_request_is_never_an_exact_pin` +5 ids (`space`, `from-space`, `tool-space`, `vertical-tab`, `upper-suffix`). Mutants M156 (the parsed name not checked), M157 (whitespace not skipped), and M151 re-targeted. M152 (case-sensitive text) survived at first, because the parsed-name check lowercases; `upper-suffix` (`Python3.10`, which only the text check sees) kills it. |
| (b) | `[PINNED]` read main's separate pin reader. | The label carries only the judge's selector. | `test_an_argv_the_judge_refuses_never_shows_pinned` (5 ids; `ws`, `tab`, `extra` red on rev 20). Mutant M158 (`[PINNED]` from main's reader). |

**Costs.**
- **Shipped cost** is unchanged: **0 of 77**, and 0 of 510 shipped argvs read as an
  interpreter request.
- **Operator cost:** only the fail-closed spellings above, none of which is a real client
  package.

**Full suite:** once with `-x --timeout=600`, after the mutant run, with the npm env
unset: exit 0, **5197 passed, 3 skipped, 80 deselected** in 1043.84s. Nothing failed.

**Tests:** the revision-21 file has **490 tests** (479 in revision 20). Against the
revision-20 code it gives **8 failed, 482 passed**:
- `space`, `from-space`, `tool-space`, `vertical-tab`;
- the differential;
- the `[PINNED]` ids `ws`, `tab`, `extra`.

## Rev 20 board findings — before/after, measured

`repro_r21.py` (appendix), one fresh process per row: a configured `firecrawl` entry,
`FIRECRAWL_API_URL` in the entry env. "main reader" is the value `update_server`'s label
used to start from. Trees: `7680445` + the revision-20 patch (`23e1e44`), and `7680445` +
the revision-21 patch.

| case | rev 20 warning / verdict / main reader | rev 21 |
|---|---|---|
| `uvx 'python ==3.10'` | `None` / `silent` exact / `'3.10'` (so `[PINNED]`) | `… uv reads "'python ==3.10'" as an interpreter request, not a package …`; never `[PINNED]` |
| `uvx --from 'python == 3.10' python` | `None` / `silent` exact / `' 3.10'` | the same, `"'python == 3.10'"` |
| `uv tool run 'cpython ==3.10'` | `None` / `silent` exact / `None` | the same, `"'cpython ==3.10'"` |
| `uvx $'python\t==3.10'` | `None` / `silent` exact / `'3.10'` | the same |
| `uvx 'python[x]==3.10'` | `None` / `silent` exact / `'3.10'` | the same (over-refused, harmless) |
| control `uvx python-dotenv==1.0.1` | `None` / `silent` exact / `'1.0.1'` | unchanged |

## Revision 20 (2026-09-28): board round 18 on `a9e53b8`: uv interpreter requests are never an exact pin

In the round-18 external board (a slim bundle), claude AGREED and gemini raised no blocker.
codex raised one blocker, reproduced on uv 0.12.19 offline, and it was **reproduced first**
against revision 19 (`repro_r20.py`, appendix).

**B1: `uvx python==3.10` was read as an exact PyPI pin.** `_read_uvx` gave `family='pypi',
package='python', selector='3.10', exact=True`, so the warning was `None` and the update
line said `[PINNED] firecrawl: pinned at 3.10`. uv does not install the package `python`:
- `ToolRequest::parse` (`crates/uv/src/commands/tool/mod.rs`) first tries the `--from` value
  (or the command) as `PythonRequest::try_from_tool_name`
  (`crates/uv-python/src/discovery.rs`).
- That function matches `python` (and `pythonw` on Windows) or an implementation's long name
  (`ImplementationName::long_name`: `cpython`, `pypy`, `graalpy`, `pyodide`), after ASCII
  lowercasing.
- The match can stand alone, or be followed by a version request:
  `@<version>`, or a rest that parses as a version or specifier.

So `python==3.10` selects an interpreter of the 3.10 series, and its patch floats: it ran
3.10.21 here. The same happens with `uvx --from python==3.10 python` and `uv tool run
python==3.10`.

**Fixed as a class, in the one parser.** `_uv_interpreter_request` applies uv's rule to the
`--from` value (or the command) before any requirement parsing:
- the prefixes are `python`, `pythonw` (on every platform, which fails closed), `cpython`,
  `pypy`, `graalpy` and `pyodide`, matched case-insensitively;
- the rest must be `@…` or begin with a digit or `= < > ~ !`, with no `-`.

A match is "cannot verify: uv reads '<component>' as an interpreter request, not a package",
never exact, for `uvx`, `uvx --from` and `uv tool run` alike.

**Checked against real uv 0.12.19 on this host** (offline; "interpreter" means uv ran an
interpreter, or said a managed interpreter download was needed):
- **Interpreter:** `python`, `Python`, `PYTHON`, `python==3.10`, `python3`, `python310`,
  `python3.10`, `python3.13t`, `python3.10+debug`, `python3.10a`, `python>=3.10,<3.12`,
  `python~=3.10`, `python!=3.11`, `cpython`, `CPython==3.10`, `cpython3.10`, `cpython@3.10`,
  `pypy`, `pypy==3.10`, `pypy3`, `pypy3.10`, `graalpy`, `GraalPy==3.10`, `graalpy3`,
  `pyodide==3.10`. Every one is refused.
- **Package:** `python3-openid`, `python-dotenv`, `python3.10-foo`, `pythonnet`, `pypylon`,
  `cp==3.10`, `pp`, `gp`, `jython`, `ironpython`, `micropython`, `rustpython`. None is refused.
  (The short names `cp`, `pp` and `gp` are not tool names in uv: `try_from_tool_name` passes
  long names only.)
- **Fail-closed differences,** where pmcp refuses and uv would read a package:
  `python3==3.10` and `pypy3==3.10` (uv cannot parse `3==3.10` as a version), `pythonw` on
  Linux, and `python3stuff`. Each is loud, never a false pin.

**`==X` for real packages, re-checked (uv 0.12.19, local wheels, `uv pip compile --offline
--no-index --find-links`).** With wheels `1.2.3`, `1.2.3.post1`, `1.2.3+cpu`, `1.2.3.0`,
`1!1.2.3` and `1.2.4` on the index:
- `demo-pkg==1.2.3` resolved to **`1.2.3+cpu`**. That is the local label already disclosed
  for non-PyPI indexes.
- Without that wheel, it resolved to **`1.2.3.0`**, the zero-padded equal version.
- It never resolved to `.post1`, `1!1.2.3` or `1.2.4`.
- `==1.2` matched nothing.

So the only other `==X` result is a zero-padded equal release. PEP 440 calls it the same
version, and PyPI refuses a second upload of an equal canonical version, so it too needs a
non-PyPI index. It is added to that disclosure (the README text below).

| # | finding (round 18) | resolution | evidence (rev 19 → rev 20, `repro_r20.py`) |
|---|---|---|---|
| **B1** (codex, blocking) | uv interpreter requests read as exact PyPI pins. | `_uv_interpreter_request` in `_read_uvx`, from uv's `try_from_tool_name`. | `uvx python==3.10`, `uvx --from python==3.10 python`, `uv tool run python==3.10`, `uvx CPython==3.10`: warning `None` and verdict `silent` (exact, `[PINNED]`) → `talks …, but pmcp cannot verify …: uv reads 'python==3.10' as an interpreter request, not a package`. `uvx pypy3`: `is unpinned` (as a package) → the same "interpreter request". Control: `python-dotenv==1.0.1` stays silent and exact. Test `test_a_uv_interpreter_request_is_never_an_exact_pin` (14 ids: the 3 forms and every alias and spelling; the health warning and `update_server`'s `pinned_version`). Controls: `test_a_package_named_like_an_interpreter_is_still_a_package` (4). Mutants: M151 (an interpreter name read as a package), M152 (case-sensitive), M153 (only `python`), M154 (a version-request suffix ignored), M155 (a `-` suffix read as an interpreter). |

**Costs.**
- **Shipped cost** is unchanged: **0 of 77**. Of the 510 shipped argvs (`args` and every
  `install` argv), **0** are read as an interpreter request.
- **Operator cost:** a server launched as `uvx python…` / `uvx pypy…` was never a pinned
  package, so the only change is that it is no longer called one.

**Full suite:** once with `-x --timeout=600`, after the mutant run, with the npm env
unset: exit 0, **5186 passed, 3 skipped, 80 deselected** in 713.43s. Nothing failed.

**Tests:** the revision-20 file has **479 tests** (461 in revision 19). Against the
revision-19 code it gives **14 failed, 465 passed**: the 14 interpreter ids. The 4 controls
pass on both revisions.

## Rev 19 board findings — before/after, measured

`repro_r20.py` (appendix), one fresh process per row: a configured `firecrawl` entry,
`FIRECRAWL_API_URL` in the entry env. Trees: `7680445` + the revision-19 patch (`a9e53b8`),
and `7680445` + the revision-20 patch.

| case | rev 19 warning / verdict | rev 20 warning / verdict |
|---|---|---|
| `uvx python==3.10 -c …` | `None` / `silent python@3.10 exact=True` | `talks …, but pmcp cannot verify …: uv reads 'python==3.10' as an interpreter request, not a package` |
| `uvx --from python==3.10 python` | `None` / `silent` exact | the same |
| `uv tool run python==3.10` | `None` / `silent` exact | the same |
| `uvx CPython==3.10` | `None` / `silent CPython@3.10` exact | `… uv reads 'CPython==3.10' as an interpreter request …` |
| `uvx pypy3` | `… client pypi:pypy3 is unpinned …` | `… uv reads 'pypy3' as an interpreter request …` |
| control `uvx python-dotenv==1.0.1` | `None` / `silent` exact | `None` / `silent` exact |

## Revision 19 (2026-09-28): board round 17 on `98b367e`: any member problem makes the relaxer may-talk

The round-17 claude seat returned DISAGREE: the same class as round 16, reached through env
and cwd. The blocker was **reproduced first** against revision 18 (`repro_r19.py`,
appendix).

**B-1: a fully read member whose env or cwd the judge already flagged was not "may".**
`_member_relaxer` gave "may" only for an unreadable argv. For a fully read member it read the
process (or container) env, and `_unpinned_self_hosted_warning` returned `None` before the
judge's flag was consulted. The seat's rows, measured with the real client:
- **G:** `npx -y firecrawl-mcp@3.25.5` with entry env
  `NODE_OPTIONS=--import=data:text/javascript,process.env.FIRECRAWL_API_URL=…`. The client
  received the URL; the warning was silent.
- **G2:** the same, unpinned. Silent.
- **G3:** `npm_config_node_options`. Silent.
- **The overlay's `server_env: {NODE_OPTIONS: …}` with a pin.** Silent.
- **I:** `docker run -e NODE_OPTIONS=… image@sha256:…`. The container received the URL;
  silent.
- **H:** an entry cwd holding a `.env` with the URL, which firecrawl-mcp's `dotenv.config()`
  reads. Silent.

**Decided (coordinator): close the class at its root; the relaxer decision and the
per-member judge must not diverge.** For a server with relaxer keys, if **any** spawn member
has **any** judge problem, the relaxer is "may talk" and the warning applies. A judge problem
is any of:
- an unreadable argv;
- a non-allowlisted env key, including a docker `-e` key in the container env;
- an entry-set cwd;
- a launcher-identity problem.

The text names the actual problem (the key, the cwd, the unreadable argv) through the
secret-safe renderer. Only a member with **no** problem at all is judged by its env:
the container env for a clean docker run, otherwise the process env.

**Structure: one function, no second path.**
- `_judge_members(spawns, declared, local_commands, path_var)` is the **only** place member
  problems are computed. It returns `_Judged(member, reading, problems)`, with
  `all_problems` adding the reading's own problem.
- `_verdict_of(judged)` combines those into the verdict. `_judge_spawn_set` is now
  `_verdict_of(_judge_members(...))`, which `update_server`'s `[PINNED]` label uses.
- `_member_relaxer(judged, relaxers, project_root)` takes the same `_Judged` and no longer
  takes `declared` or `path_var`, so it cannot recompute anything.
- `_unpinned_self_hosted_warning` computes `_judge_members` **once**, and feeds the same list
  to `_member_relaxer` and to `_verdict_of`.
- A test pins the signature, and mutant M150 (the verdict recomputed on a separate path)
  dies.

The "may" reasons, by case:
- **An unreadable argv:** `(<member>, which pmcp cannot fully read, can set <RELAXER> for the
  client)`.
- **docker's `--env-file` (N-3: the specific wording is restored):** `(its docker
  --env-file, which pmcp cannot read, can set <RELAXER> in the container)`.
- **A fully read member with an env or cwd problem:** `(<member>: <the judge's problem>, so
  it can set <RELAXER> for the client)`. For example: `(its args (`npx -y
  firecrawl-mcp@3.25.5`): the entry's env sets NODE_OPTIONS, which is not on the list of keys
  known inert for it, so it can set FIRECRAWL_API_URL for the client)`.

The env value (the URL inside `NODE_OPTIONS`) is never rendered. `repro_r19.py`'s output
contains `ai:3002` zero times.

**N-1 (README).** A URL in the project `.env` that pmcp loads is stripped from the child's
env (Consiliency/pmcp#229), so the warning cannot see it. firecrawl-mcp's own
`dotenv.config()` reads it back from the inherited cwd, which is pmcp's. This is host state:
trusted, but stated.

**N-2 (R18, measured with the seat's shapes).** The cost is listed in R18 below (the operator
cost of revision 19, measured).

| # | finding (round 17) | resolution | evidence (rev 18 → rev 19, `repro_r19.py`) |
|---|---|---|---|
| **B-1** (blocking) | The relaxer ignored the judge's env/cwd problems. | One `_judge_members`; any member problem → "may", naming it. | G, G2, G3, overlay, I, H: `None` → `may talk … (its args (…): <problem>, so it can set FIRECRAWL_API_URL for the client) …`. Controls: an npx pin with no env, and a docker digest with a declared `-e FIRECRAWL_API_KEY`, `None` → `None`. Tests: `test_a_fully_read_member_with_a_judge_problem_may_talk` (5 ids), `test_an_overlay_env_key_problem_may_talk`, `test_the_relaxer_and_the_verdict_read_one_judged_member`; controls `test_a_member_with_no_judge_problem_and_no_relaxer_stays_quiet` (4 ids). Mutants: M148 (the relaxer ignores env/cwd problems), M150 (a second path recomputes the verdict's problems), and M131-M147 re-targeted at `_Judged`. |
| N-1 | project `.env` + the client's own dotenv | README line | text |
| N-2 | the operator cost list | R18 rewritten with the measured list and the remedy | `operator_cost19.py` |
| N-3 | `--env-file` wording | restored (`_Reading.env_file`) | `test_the_env_file_reason_keeps_its_specific_wording`; mutant M149 |

**Operator cost of revision 19, measured (`operator_cost19.py`).** The cases are legitimate
**vendor-hosted** configured `firecrawl` entries: `FIRECRAWL_API_KEY` only, no URL anywhere,
the real shipped manifest, and `PATH=<tmp>/bin:/usr/bin:/bin` with a `<tmp>/bin/npx`.

| # | shape | rev 18 | rev 19 |
|---|---|---|---|
| 1 | `npx -y firecrawl-mcp` | quiet | quiet |
| 2 | an exact pin | quiet | quiet |
| 3 | an absolute npx equal to `which` | quiet | quiet |
| 4 | an absolute npx not equal to `which` (macOS GUI clients) | warns | warns |
| 5 | `cmd /c npx -y firecrawl-mcp` (Windows Claude Desktop) | warns | warns |
| 6 | a global `firecrawl-mcp` | warns | warns |
| 7 | `node …/dist/index.js` | warns | warns |
| 8 | `bunx` | warns | warns |
| 9 | `pnpm dlx` | warns | warns |
| 10 | `mise exec -- npx` | warns | warns |
| 11 | `docker run -e FIRECRAWL_API_KEY` | quiet | quiet |
| 12 | docker `--env-file` | warns | warns (the specific wording) |
| 13 | `npx --package=firecrawl-mcp firecrawl-mcp` | warns | warns |
| 14 | + entry `FIRECRAWL_RETRY_MAX_ATTEMPTS` | quiet | **warns** |
| 15 | + entry `HTTPS_PROXY` | quiet | **warns** |
| 16 | `npx.cmd` on Linux | warns | warns |
| 17 | + entry `NODE_OPTIONS=--max-old-space-size=4096` | quiet | **warns** |

**10 of 17 warn on revision 18; 13 of 17 warn on revision 19.** The shipped `firecrawl`, as
shipped with no relaxer set, stays quiet, so shipped cost is still **0 of 77**. All of these
are advisory warnings on relaxer-bearing servers only (today, `firecrawl`). Nothing fails,
refuses or stops. To silence one, launch the client as bare `npx` (or the exact path `which`
returns) with an exact pin, and no extra entry env beyond the declared keys and locale.

**Full suite:** once with `-x --timeout=600`, after the mutant run, with the npm env
unset: exit 0, **5168 passed, 3 skipped, 80 deselected** in 575.23s. Nothing failed.

**Tests:** the revision-19 file has **461 tests** (449 in revision 18). Against the
revision-18 code it gives **9 failed, 452 passed**:
- the 5 fully-read-problem ids;
- the overlay test;
- the `--env-file` wording test;
- the one-judged-member test;
- `test_an_exception_judging_one_server_never_hides_another`. Its seam moved from
  `_judge_spawn_set` to `_verdict_of`, which revision 18 does not have. The test's intent
  (one server's judging failure never hides another's warning) is unchanged.

The 4 controls pass on both revisions.

## Rev 18 board findings — before/after, measured

`repro_r19.py` (appendix), one fresh process per row, under the shipped `firecrawl` relaxer,
with no `FIRECRAWL_API_URL` in the host env or in the entry's env block. Trees: `7680445` +
the revision-18 patch (`98b367e`), and `7680445` + the revision-19 patch.

| case | rev 18 | rev 19 |
|---|---|---|
| G: npx exact pin + `NODE_OPTIONS=--import=data:…` | `None` | `may talk … (its args (`npx -y firecrawl-mcp@3.25.5`): the entry's env sets NODE_OPTIONS, which is not on the list of keys known inert for it, so it can set FIRECRAWL_API_URL for the client), but pmcp cannot verify …` |
| G2: npx unpinned + `NODE_OPTIONS` | `None` | `may talk … (its args (`npx -y <redacted>`): the entry's env sets NODE_OPTIONS …) but its client npm:firecrawl-mcp is unpinned …` |
| G3: npx pin + `npm_config_node_options` | `None` | `may talk … the entry's env sets npm_config_node_options …` |
| overlay `server_env: {NODE_OPTIONS}` + pin | `None` | `may talk … the entry's env sets NODE_OPTIONS …` |
| I: docker `-e NODE_OPTIONS=…` digest | `None` | `may talk … (its args (`docker <redacted> …`): its argv sets container env NODE_OPTIONS, so it can set FIRECRAWL_API_URL for the client) …` |
| H: npx pin + an entry cwd holding `.env` | `None` | `may talk … the entry sets cwd '…', whose project configuration npm reads, so it can set FIRECRAWL_API_URL for the client …` |
| control: npx pin, no env, no cwd | `None` | `None` |
| control: docker digest, `-e FIRECRAWL_API_KEY` (declared) | `None` | `None` |

## Revision 18 (2026-09-27): board round 16 on `9d0a73f`: an unreadable spawn member may talk to the backend

The round-16 claude seat returned DISAGREE on one blocker, which has been present since
revision 16 and is not a regression. Revision 17's fix held, and rounds 12-15 held. The
blocker was **reproduced first** against revision 17 (`repro_r18.py`, appendix).

**B-1: only docker could be "may".** `_member_relaxer` gave "may talk" only for a docker
reading with a problem. Every other member, including one whose reading has a problem, was
judged by its launcher's process env. So an argv pmcp cannot read could set
`FIRECRAWL_API_URL` **on the client itself**, and the warning was `None`. The verdict was
`cannot_verify`, but the operator never saw it:
- A: `env FIRECRAWL_API_URL=… npx -y firecrawl-mcp`;
- B: `sh -c '… exec npx …'`;
- C: `npx -y -c '…'`, where the npx parser already flags `-c`;
- D: `/usr/bin/docker run -e …`, where the child's `PATH` resolves docker elsewhere;
- E: `podman run -e …`;
- F: an env prefix + an exact npx pin.

**Decided (coordinator), stronger than the seat's key-mention probe.** For a server with
relaxer keys (shipped ∪ overlay), **any** spawn member the one parser cannot fully read
counts as "may talk to a self-hosted backend". That covers any launcher and any reason: an
unknown launcher or wrapper, an unreadable flag, a path-launcher mismatch, or docker not
launched as itself. The warning then applies, says it cannot verify, and **names the
member** (through the secret-safe renderer, so the URL is not echoed).

Only a **fully read** member is judged by its env: the container env for a clean docker run
launched as docker itself that passed the per-member judge, otherwise its process env. A
non-docker unreadable member whose own env holds the relaxer still says "talks … (X is
set)". The "may" wording is no longer docker's:

> `'<server>' may talk to a self-hosted backend (its args (`<rendered argv>`), which pmcp
> cannot fully read, can set <RELAXER> for the client), but pmcp cannot verify …`

**R21 updated.** A URL passed as a client flag or in a config file is still out of scope. A
URL set through the environment by a command pmcp cannot read is now covered.

| # | finding (round 16) | resolution | evidence (rev 17 → rev 18, `repro_r18.py`) |
|---|---|---|---|
| **B-1** (blocking) | An unreadable non-docker member was judged by its process env. | Any member with a reading problem is "may"; the warning names it. | A-F: `None` → `may talk to a self-hosted backend (its args (`env <redacted> …`), which pmcp cannot fully read, can set FIRECRAWL_API_URL for the client), but pmcp cannot verify …` (C: `its argv passes -c`; D: `… is not 'docker' as the child's PATH resolves it`). Controls: a fully read `npx -y firecrawl-mcp` with no URL anywhere, `None` → `None`; a fully read `docker run -e K=V :latest` stays `talks … is unpinned`. Tests `test_an_unreadable_member_may_talk_to_the_self_hosted_backend` (8 ids: A-F, including the seat's key-mention rows, plus 2 without any key mention), `test_an_unreadable_install_member_may_talk_to_the_self_hosted_backend`, controls `test_a_fully_read_member_without_the_relaxer_stays_quiet` (5 ids). Mutants M146 (an unreadable non-docker member judged by its process env), M147 (an unreadable docker member's own env taken as the container's), M132 re-targeted (an unreadable argv not "may"), M135 re-targeted (docker decided by basename; revision 17's target is now equivalent, see below). |

**Costs, measured (`operator_cost.py`, `shipped_cost.py`).**
- **Shipped cost: 0 of 77**, unchanged. `firecrawl` is the only shipped server with a
  relaxer, and as shipped with no relaxer set it gives `None` on both revisions.
- **Operator cost:** a configured `firecrawl` entry launched through `sh -c 'exec npx -y
  firecrawl-mcp'` or `/opt/bin/fc-wrapper`, with **no** relaxer set (vendor-hosted), gave
  `None` on revision 17. It now gives `may talk … (its args (`sh <redacted> <redacted>`),
  which pmcp cannot fully read, …)`.
- So a custom or unreadable command on a relaxer-bearing server now **always** warns, whether
  or not it is self-hosted. R18 is widened accordingly. The operator's remedy is the same as
  before: launch the client in a shape pmcp reads.

**M135 re-targeted, not retired.** Revision 17's M135 changed the docker branch's family check
to a basename check. In revision 18 that mutant is equivalent: a reading with a problem
returns before the family check, and a clean reading whose basename is `docker` is always
family `docker` (docker is a modelled launcher, read only after
`_launcher_spelled_as_itself`). It survived (449 passed), so it was re-targeted. It now
targets the one place a basename could still decide: whether an unreadable member's own env
is docker's (not the client's) or the client's. That version is red (8 failed).

**Full suite:** once, with `-x --timeout=600`, run after the mutant run rather than
alongside it: exit 0, **5156 passed, 3 skipped, 80 deselected** in 588.49s. Nothing failed.

**Tests:** the revision-18 file has **449 tests** (435 in revision 17). Against the
revision-17 code it gives **9 failed, 440 passed**: the 8 unreadable-member ids and the
install-member test. The 5 controls pass on both revisions, and all 435 revision-17 tests
pass unchanged.

## Rev 17 board findings — before/after, measured

`repro_r18.py` (appendix), one fresh process per row. A configured entry under the shipped
`firecrawl` relaxer, with no `FIRECRAWL_API_URL` in the host env or in the entry's env block.
`PATH=<tmp>/bin:/usr/bin:/bin`, with a `<tmp>/bin/docker`. Trees: `7680445` + the
revision-17 patch (`9d0a73f`), and `7680445` + the revision-18 patch.

| case | rev 17 | rev 18 |
|---|---|---|
| A `env K=V npx -y firecrawl-mcp` | `None` | `may talk … (its args (`env <redacted> <redacted> -y <redacted>`), which pmcp cannot fully read, can set FIRECRAWL_API_URL for the client), but pmcp cannot verify …: it launches through 'env' …` |
| B `sh -c 'K=V exec npx -y firecrawl-mcp'` | `None` | `may talk … (its args (`sh <redacted> <redacted>`) …` |
| C `npx -y -c 'K=V firecrawl-mcp'` | `None` | `may talk … cannot verify …: its argv passes -c …` |
| D `/usr/bin/docker run -e K=V`, not the `PATH`'s docker | `None` | `may talk … (its args (`/usr/bin/docker <redacted> …`) …` |
| E `podman run -e K=V` | `None` | `may talk … it launches through 'podman' …` |
| F `env K=V npx -y firecrawl-mcp@3.25.5` | `None` | `may talk … it launches through 'env' …` |
| control: `npx -y firecrawl-mcp`, no URL anywhere | `None` | `None` |
| control: `docker run -e K=V :latest` (fully read) | `talks … is unpinned` | `talks … is unpinned` |

## Revision 17 (2026-09-27): board round 15 on `56dfca4`: the container env counts only for a member that passed the judge

The round-15 claude seat returned DISAGREE on one narrow blocker. Everything else held:
- It fuzzed 1,248 docker argvs against real docker 29.8.1 and found **0 mismatches** between
  pmcp's reading and docker's container (image, Cmd, Entrypoint, WorkingDir, User, mounts,
  env).
- Rounds 12-14 are closed.
- The sweep found nothing else.

The blocker was **reproduced first** against revision 16 (`repro_r17.py`, appendix).

**B-1: identity by spelling alone.** `_launcher_spelled_as_itself` accepts a bare `docker`
by its spelling. `_member_relaxer` then swapped in the container env and ignored
`_member_problems`, which already flags the two inputs that decide which `docker` runs: an
entry `PATH`, and an entry cwd that the `PATH` searches. The rows:
- **R2:** entry `PATH=<evil>/bin:...` + entry URL, with `:latest` or a digest (R2b). Silent,
  while a fake `docker` ran with the URL in its env.
- **R3:** entry `PATH=.:...` + an entry cwd holding `./docker`. Silent.
- **R1:** round 12's `hostdot`: host `PATH=.:...` + host URL + entry cwd. Silent, where
  revision 14 was loud.

**Fixed as a class: a launcher's identity is trusted only after the member passed the
per-member judge.** `_member_relaxer` now takes the shipped declarations
(`_declared_env_keys`) and the child's `PATH`:
- **A clean docker reading whose `_member_problems` is empty** is known to be docker, so the
  client sees only the container env. This keeps the round-14 precision: a URL left in
  docker's own env, or a declared URL in the entry env not passed with `-e`, stays quiet.
- **A clean docker reading with any member problem** may not be docker. **Both** its process
  env and its container env count.
- **A docker reading with a problem** is "may", as before.
- **Every other member** uses its process env, as before.

**The generalisation, swept.** The rule "identity only after the judge" now holds in both
places that trust identity:
- **The verdict** (`_judge_spawn_set`) already required `_member_problems` to be empty
  before `silent`, for docker, local and pinned families alike: `args_problems` and each
  later member's `problems` are checked before the silent return.
- **The relaxer** (`_member_relaxer`) was the only place that trusted a spelling without the
  judge. It is fixed above.

No other rule consumes `_launcher_spelled_as_itself`'s result.

**N-1 (disclosed, R22).** A URL baked into the image's own `ENV` is never seen. The container
env model starts from `{}`, and pmcp does not call docker to read image config. The seat
built such an image to confirm this. Revision 14 would also miss it, since it read docker's
process env.

**N-2 (noted, harmless).** The parser reads some argvs as clean that docker then refuses to
create, such as `-e =K`, or `--name -e` (an invalid container name): 56 of the seat's 1,248.
Nothing runs for them, so no warning can be wrong about what runs.

**Full suite with `-x`.** As asked, the full suite was run once **with `-x --timeout=600`**.
The results:
- **First `-x` run, concurrent with the full mutant run: exit 1.**
  `tests/test_manifest.py::TestMonitorInstall::test_monitor_reads_stderr` failed
  (`AssertionError: Expected stderr in output: []`, with `ValueError: I/O operation on closed
  file` logging errors), giving 1 failed, 1522 passed, 1 skipped, 80 deselected in 228.33s.
  That test is main's, and it drives `JobManager.start_install` with `bash -c 'echo … >&2'`.
  The patch changes neither `installer.py` nor `test_manifest.py`.
- **Measured afterwards:**
  - the test alone, 5 times on each tree: passed on both the patched tree and on
    `7680445` without the patch;
  - `tests/test_manifest.py` with `-x`, 3 times on each tree: 110 passed, 1 skipped;
  - the full `-x` suite on `7680445` without the patch, concurrent with the same mutant run:
    exit 0, 4707 passed, 3 skipped, 80 deselected;
  - **the full `-x` suite on the patched tree, re-run without load: exit 0, 5142 passed,
    3 skipped, 80 deselected in 567.08s.**
- **Conclusion:** a load-timing flake in main's stderr-monitor test. It was not reproduced
  on either tree without load, nor in isolation. The seat's first `-x` failure had its log
  overwritten, so whether it was the same test is not known.

| # | finding (round 15) | resolution | evidence (rev 16 → rev 17, `repro_r17.py`) |
|---|---|---|---|
| **B-1** (blocking) | The container env was used for a bare `docker` the entry can redirect. | The container env is used only when `_member_problems` is empty; otherwise the process env counts too. | R2, R2b, R3, R1: `None` → `talks … (FIRECRAWL_API_URL is set) …` (R2, R3 `is unpinned`; R2b `cannot verify: … the entry's env sets PATH`; R1 `cannot verify: … the entry sets cwd`). Controls: a declared entry URL without `-e`, and a host URL without `-e`, `None` → `None`. Test `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env` (4 red ids, 2 controls). Mutants M143 (the container env despite member problems), M144 (the judge without the shipped declarations), M145 (the container env only for a member with problems). |
| N-1 | image `ENV` | R22 | text |
| N-2 | parse-clean argvs that docker refuses | noted | text |

**Costs.** Shipped cost is unchanged, **0 of 77**. For the operator, a docker entry that sets
`PATH` in its env, or a cwd under a cwd-searching `PATH`, now warns when the URL is in that
entry's env. The pin verdict was already "cannot verify" for it.

**Tests:** the revision-17 file has **435 tests** (429 in revision 16). Against the
revision-16 code it gives **4 failed, 431 passed**: the 4 redirect ids. The 2 controls pass
on both revisions.

## Rev 16 board findings — before/after, measured

`repro_r17.py` (appendix), one fresh process per row. A configured bare-`docker` entry
(`run -i --rm <image>`) under the shipped `firecrawl` relaxer, with a fake `docker` in
`<evil>/bin` and in the entry's cwd. Trees: `7680445` + the revision-16 patch (`56dfca4`),
and `7680445` + the revision-17 patch.

| case | rev 16 | rev 17 |
|---|---|---|
| R2: entry `PATH=<evil>/bin:...`, entry URL, `:latest` | `None` | `talks … (FIRECRAWL_API_URL is set) but its client docker:mendable/firecrawl-mcp is unpinned …` |
| R2b: the same, digest | `None` | `talks …, but pmcp cannot verify …: … the entry's env sets PATH, which is not on the list of keys known inert …` |
| R3: entry `PATH=.:...`, entry cwd with `./docker`, entry URL, `:latest` | `None` | `talks … is unpinned …` |
| R1: host `PATH=.:...`, host URL, entry cwd with `./docker`, digest | `None` | `talks …, but pmcp cannot verify …: … the entry sets cwd …` |
| control: entry URL (declared), no `-e`, `:latest` | `None` | `None` |
| control: host URL, no `-e`, `:latest` | `None` | `None` |

## Revision 16 (2026-09-27): board round 14 on `6086712`: the relaxer is judged per spawn member by the one parser

The round-14 claude seat returned DISAGREE on one blocker. It reproduced the blocker first on
real docker 29.8.1, and it found no hole in revision 15's literal `which` rule. Each row below
was **reproduced first** against revision 15 (`repro_r16.py`, appendix).

**B-1: revision 15's N-2 fix brought back round 8's class.** To decide whether the warning
applies, revision 15 swapped the child env for a "container env". That env came from a
**second** docker-argv reader (`_docker_container_env`), chosen by the args command's
**basename** and reading the **args member only**. Three premises went unchecked:
- **Row 1 (the blocking one, a regression):** an overlay sets `command: docker` with a
  digest-pinned image. It keeps the shipped `install: npx -y firecrawl-mcp` and puts
  `FIRECRAWL_API_URL` in `extra_env`. The args container never sees the URL, so the warning
  switched off on both health and `update_server`. Meanwhile `gateway.provision` spawned and
  adopted the unpinned npx **with** the URL. Revision 14 was loud.
- **Row 2 (a regression):** a launcher named `docker` that is not docker (an absolute fake,
  `./docker`, `DOCKER`, `docker.exe` on Linux, a planted `/proc/self/cwd/...`). With a
  host-exported URL it was silent. The binary has no container: the URL is in its own env,
  where it runs.
- **Row 3:** docker's pflag shorthand (`-eK=V`, `-ie K=V`, `-ieK=V`, `-ie=K=V`, all measured
  on docker 29.8.1) was invisible to the second reader, so it was silent with `:latest`.

**Fixed as the class ("two readers" + "an exemption decided before every member is
judged"):**
- **One reader.** `_docker_container_env` is deleted. `_read_docker` reads every `-e`
  spelling docker accepts and returns them in argv order as `_Reading.container_env`:
  - `-e K=V`, `--env K`, `--env=K=V`, `-e=K=V`;
  - `-eK=V`, and a shorthand cluster read letter by letter as pflag does
    (`_docker_short_cluster`): an inert boolean letter consumes itself, and `e` takes the
    rest of the cluster (a leading `=` dropped) or the next argument;
  - any other letter is not a recognised shape (`-iP` names `-P`).
- **Per member (`_member_relaxer`), for every member of `_spawn_set`, each read by
  `_read_argv`.** Which env the MCP client sees depends on the reading:
  - A **clean docker** reading (family `docker`, which `_read_argv` gives only to a launcher
    spelled as docker itself, and no `problem`) uses the container's env. Assignments apply
    in order, and the last one wins: a later bare `-e KEY` passes docker's value through,
    and unsets KEY when docker has none (measured).
  - A **docker reading with a problem** (`--env-file`, or a flag pmcp does not read) is
    **"may"**: the warning applies and says pmcp cannot read what the container receives.
  - **Every other member** (npx, uvx, cargo, a local binary, a fake `docker`) uses its own
    process env (`sanitized_subprocess_env(member.env)`).
- **The warning applies if ANY member applies.** The decision is made on the spawn set
  before the verdict, and never from one member. A failure while reading the members is
  contained to the server and says "may talk ... evaluating its argvs failed", never "does
  not apply".

Revision 15's intended precision holds only where it is proven: a real, clean `docker run`
whose container never receives the URL, with no other member, stays quiet.

**N-1 (Windows, stated, not changed).** On Windows `shutil.which` appends the extension as
`PATHEXT` spells it (`.CMD`), so an absolute `C:\...\npx.cmd` does not equal `which`'s
`C:\...\npx.CMD`. **Chosen: fail closed and say so.** On Windows an absolute launcher path is
"cannot verify" unless it is spelled exactly as `which` returns it. Launch the client as bare
`npx`/`npx.cmd` (the bare platform spellings are accepted). A case-insensitive comparison was
not adopted: it would widen the one rule the round-14 seat found no hole in, to buy an
operator convenience.

**N-2 (text).** The sweep table's docker-relaxer row now sits under "does the warning
apply?". It says per member, through the one parser. The "launcher identity" row's "no
basename" is now true everywhere: the only basename read is `_read_argv`'s launcher lookup,
and a modelled launcher found that way must then pass `_launcher_spelled_as_itself` before it
gets its family.

**Input sweep, re-run: every decision and what it reads.** Measured with a `grep` over the
patch's added lines for `normalized_executable_name`, `_split_flag`, `partition("=")`,
`config.args`, `config.command`, `.argv[` and path resolution. Every hit is one of the
readers below.

| decision | reads | through |
|---|---|---|
| does the warning apply? (relaxer keys) | `_warning_relaxers`: shipped ∪ overlay | no argv read |
| does the warning apply? (relaxer value) | each `_spawn_set` member's `_read_argv` reading + that member's env | `_member_relaxer`: the one parser and the per-member rule above |
| which servers health judges | `_relaxable_manifest_servers` + connected configs | no argv read |
| is it silent? / the verdict | each member's `_read_argv` reading + `_member_problems` | `_judge_spawn_set`: the one parser and the per-member judge |
| docker cwd exemption | the member's own `argv[0]` (`_is_bare_launcher`), after `_read_argv` gave it family `docker` | the per-member judge (`_member_problems`) |
| `[PINNED]` / `[FLOATING]` | `_judge_spawn_set(_spawn_set(...))`, whose selector must equal main's pin | the one parser and the per-member judge; two readings must agree |
| the rendered argv in any message | `_render_argv` | the secret-safe renderer (display only) |

No decision reads the args command's basename, the args argv alone, or a second parser.

| # | finding (round 14) | resolution | evidence (rev 15 → rev 16, `repro_r16.py`) |
|---|---|---|---|
| **B-1 row 1** (blocking) | docker args + the shipped npx install | Relaxer per member; ANY member applies. | `None` → `talks … (FIRECRAWL_API_URL is set), but pmcp cannot verify … its linux install argv …`. Tests `test_a_docker_args_member_does_not_switch_off_an_install_member` (health), `test_update_server_warns_about_a_docker_args_member_with_an_npx_install`. Mutants M134 (args member only), M140 (every member must apply). |
| **B-1 row 2** | a fake `docker` | Container env only for a clean docker reading, which requires the launcher to be docker itself. | 4 spellings: `None` → `talks … cannot verify: it launches through '…', which is not 'docker' …`. Test `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env` (4 spellings × entry env / host export). Mutant M135 (by basename). |
| **B-1 row 3** | pflag shorthand | The one reader parses clusters as pflag does. | 4 forms: `None` → `is unpinned`. Tests: `test_docker_judges_the_relaxer_on_the_containers_env` +8 ids (4 cluster forms, `-e=`, last-wins-unset, last-wins-empty, an unread cluster), `test_the_one_docker_reader_returns_the_container_env_in_order`, and the shape id `docker-unknown-letter-in-cluster`. Mutants M136 (a second parser), M137, M138, M139, M142. |
| (contained) | a failure while deciding the relaxer | "may talk … evaluating its argvs failed" | `test_a_failure_while_deciding_whether_the_warning_applies_is_loud`. Mutant M141. |
| N-1 | Windows `PATHEXT` case | Stated: fail closed. | text |
| N-2 | sweep table | Row moved and corrected. | text |

**Costs.** Shipped cost is unchanged, **0 of 77**: no shipped entry uses docker, and every
shipped command is a bare `npx` or `uvx`. For the operator, a docker entry whose argv pmcp
cannot fully read now warns "may" even when the URL was never passed into it. So does an
overlay that turns a shipped npx entry into docker while keeping the npx install. Both are
the safe direction.

**Tests:** the revision-16 file has **429 tests** (407 in revision 15). Against the
revision-15 code it gives **20 failed, 409 passed**:
- 7 container-env ids;
- 8 fake-docker ids;
- the two row-1 tests;
- the one-reader test;
- the contained-failure test;
- the `-iP` shape id (revision 15 refused it too, but named `-iP`, not `-P`).

The existing env-file id now asserts the generic "may talk" text.

## Rev 15 board findings — before/after, measured

`repro_r16.py` (appendix), one fresh process per row, under the shipped `firecrawl` relaxer,
with `PATH=/usr/bin:/bin` (this host has `/usr/bin/docker`). Trees: `7680445` + the
revision-15 patch (`6086712`), and `7680445` + the revision-16 patch.

| case | rev 15 | rev 16 |
|---|---|---|
| row 1: docker + digest args, the shipped `npx -y firecrawl-mcp` install, URL in the entry env | `None` | `talks … (FIRECRAWL_API_URL is set), but pmcp cannot verify …: the argv pins docker:example/client at sha256:…, but its linux install argv …` |
| row 2: absolute fake `docker`, host-exported URL | `None` | `talks …, but pmcp cannot verify …: it launches through '/tmp/…/docker', which is not 'docker' as the child's PATH resolves it` |
| row 2: `./docker` | `None` | the same, naming `./docker` |
| row 2: `DOCKER` | `None` | the same, naming `DOCKER` |
| row 2: `docker.exe` on Linux | `None` | the same, naming `docker.exe` |
| row 3: `-eK=V`, `:latest` | `None` | `talks … but its client docker:example/client is unpinned …` |
| row 3: `-ie K=V` | `None` | the same |
| row 3: `-ieK=V` | `None` | the same |
| row 3: `-ie=K=V` | `None` | the same |
| control: clean `docker run`, URL only in docker's env, `:latest` | `None` | `None` |

docker 29.8.1 on this host, `node:24`:
- `-ie=K=cl` gives `cl`;
- `-ieK=qux` gives `qux`;
- `-eK=b` gives `b`;
- `-e=K=x` gives `x`;
- `-ie K=baz` gives `baz`;
- `-e K=V -e K`, with K unset, gives unset;
- `-e K=V -eK=` gives empty;
- `-ei K=x` fails (`e` takes `i` as its value), which pmcp also refuses.

## Revision 15 (2026-09-27): board round 13 on `78b21f4`: cwd exemptions only for bare launchers

> **Corrected by revision 16.** The N-2 fix below used a second docker-argv reader
> (`_docker_container_env`), chosen by the args command's basename and reading only the
> args member. Revision 16 deletes it: the relaxer is judged per spawn member from the one
> parser's reading, and the warning applies if any member applies.

The round-13 claude seat returned DISAGREE on one blocker. Its systematic sweep of every
silent or exempt branch found only the items below. Each was **reproduced first** against
revision 14 (`repro_r15.py`, appendix).

**B-1: an absolute path can still depend on the cwd.** `/proc/self/cwd/` + `'../' * 12` +
`usr/bin/docker` (also `/proc/thread-self/cwd`), with a deep entry cwd and a digest-pinned
image, was silent and `[PINNED]`. pmcp's `realpath` resolved `/proc/self/cwd` in **pmcp's**
cwd; the child, after `chdir`, resolves it in the **entry's**, where a planted binary can
sit. **Fixed as a class, not as `/proc`:**
- **(a) The docker cwd exemption holds only for the bare `docker` spelling.** Any path
  spelling of a launcher under an entry-set cwd is "cannot verify": `the entry sets cwd
  '<dir>', and its launcher is a path, which can depend on that cwd`. npx, uvx, cargo and
  local commands were already loud on any entry cwd.
- **(b) Nothing is resolved in pmcp's own process.** `_launcher_spelled_as_itself` no longer
  calls `realpath`. An absolute path counts only if it is, character for character, what
  the child's `PATH` search yields (`shutil.which`). So `/proc/self/cwd/...`,
  `/proc/self/fd/...` or a symlinked detour is refused even without a cwd.
- **(c) A `/proc/...` entry in the child's `PATH`** counts as searching the cwd, for docker
  with an entry cwd.

**Audit: every place the patch resolves a path in pmcp's process.** `git diff` of
`handlers.py` shows:
- three reads of pmcp's own packaged `manifest.yaml`, which are not about the child;
- one `shutil.which(name, path=<child PATH>)`, whose result is compared literally. A
  relative `PATH` entry makes it return a relative path, which never equals an absolute
  command, so it fails closed.

No `realpath`, `abspath`, `Path.resolve`, `stat` or `exists` on a child path remains. For an
entry with no cwd, the child's cwd is pmcp's own, so even the `which` existence checks look
where the child would.

The mutation run then found one rule that the literal comparison had made look redundant.
Deleting the "a relative path is never the launcher" guard (M124) survived the first 405-test
cut. It is not redundant: with a relative `PATH` entry (`PATH=rbin`), `which` returns the
same relative spelling (`rbin/docker`), so the comparison alone would accept a cwd-relative
launcher. `test_a_relative_path_equal_to_a_relative_path_search_is_not_the_launcher`
(npx, docker) pins the guard, and M124 is red again.

**N-1: Windows `.cmd` launchers, decided and stated.** On Windows, npx and npm are `.cmd`
shims, and any `.cmd`/`.bat` runs through `cmd.exe`, which does not escape `&`, `|`, `%`.
So `npx.cmd -y firecrawl-mcp@3.25.5 x|npx -y firecrawl-mcp@latest` would pipe into an
unpinned npx. **Chosen:** on Windows, an npx/npm launch or any `.cmd`/`.bat` spelling is
accepted only when every argument matches `[A-Za-z0-9@._/:=+,~-]+` (the pin grammar's
characters and flag spellings: no `& | < > ^ % ! " ( )`, no whitespace). Otherwise it is
"cannot verify": `its argv passes an argument cmd.exe would interpret (a .cmd launcher runs
through cmd.exe)`. Tested by simulation, through the `_is_windows()` seam.

**N-2: docker's relaxer is the container's, fixed.** The MCP client runs **inside** the
container, so the relaxer that makes the warning apply is judged on the env the container
receives (`_docker_container_env`):
- `-e KEY=VALUE` and `--env=KEY=VALUE` set it;
- `-e KEY` passes docker's own value through;
- `--env-file` is unreadable, so the warning **applies** and says `'<server>' may talk to a
  self-hosted backend (its docker --env-file, which pmcp cannot read, can set
  FIRECRAWL_API_URL)`, then "cannot verify" (the shape refuses `--env-file`);
- a URL that stays in docker's own env never reaches the client and does not count.

**N-3: Windows text corrected.** `subprocess` on Windows resolves the executable with the
**parent's** current directory and `PATH` (`CreateProcess`), never the child's `cwd` or env.
So an entry-set cwd cannot move a bare launcher there, and `_path_searches_the_cwd` is false
on Windows. Round 12's "always loud on Windows" was wrong and is withdrawn. On Windows the
launcher-path comparison uses the parent's `PATH`.

| # | finding (round 13) | resolution | evidence (rev 14 → rev 15, `repro_r15.py`) |
|---|---|---|---|
| **B-1** (blocking) | A `/proc/self/cwd` absolute path resolved in pmcp's cwd. | The docker cwd exemption only for the bare spelling; a literal `which` comparison, never `realpath`; `/proc` `PATH` entries count as cwd-dependent. | `/proc/self/cwd/<rel>/usr/bin/docker` + deep cwd: `None` → cannot verify (`... which is not 'docker' as the child's PATH resolves it`). `/usr/bin/docker` (the `PATH` result) + deep cwd: `None` → cannot verify (`its launcher is a path, which can depend on that cwd`). Control: bare `docker` + cwd, `None` → `None`. Tests `test_a_path_launcher_under_an_entry_cwd_is_never_exempt` (4, host-independent: the `/proc` path is built to resolve, from pmcp's cwd, to exactly the `PATH` binary), `test_the_resolved_absolute_path_without_an_entry_cwd_is_the_launcher`, `test_a_proc_path_entry_counts_as_searching_the_cwd`. Mutants M127, M128, M129. |
| **N-1** | Windows `.cmd` + cmd metacharacters | Safe-charset rule on Windows. | Tests `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters` (pipe, `&`, `%`, space, and a safe control). Mutant M130. |
| **N-2** | docker relaxer read from docker's env | Judged on the container's env; `--env-file` applies the warning. | `-e FIRECRAWL_API_URL=…` with `:latest`: `None` → `is unpinned`; `--env-file`: `None` → `may talk … cannot verify`; the URL only in docker's env: `is unpinned` → `None`. Test `test_docker_judges_the_relaxer_on_the_containers_env` (5 ids). Mutants M131, M132. |
| **N-3** | Windows cwd text | Corrected: the parent's cwd and `PATH`. | `test_windows_searches_the_parents_cwd_not_the_entrys` (simulated). Mutant M133. |

**Costs.** Shipped cost is unchanged, **0 of 77** (no shipped docker entry, no shipped
cwd; every shipped command is a bare `npx` or `uvx`). The operator costs:
- a docker entry must pass the relaxer into the container, as a working one does;
- a path-spelled launcher with an entry cwd now warns;
- on Windows, an npx/npm argv containing a cmd metacharacter now warns.

**Tests:** the revision-15 file has **407 tests** (388 in revision 14). Against the
revision-14 code it gives **15 failed, 392 passed**:
- the 4 path-launcher ids;
- the `/proc` `PATH` entry;
- 5 Windows `.cmd` ids and the Windows cwd id (the `_is_windows` seam does not exist on
  revision 14);
- 4 container-env ids.

The 2 ids of the M124 test pass on revision 14, which also refused every relative path.

The existing docker tests now pass the relaxer into the container (`-e SELFHOST_API_URL`),
which the new rule requires.

## Rev 14 board findings — before/after, measured

`repro_r15.py` (appendix), one fresh process per row: a configured docker entry under the
shipped `firecrawl` relaxer, `PATH=/usr/bin:/bin`, and this host has `/usr/bin/docker`.
Trees: `4d2790f` + the revision-14 patch (`78b21f4`), and `4d2790f` + the revision-15 patch.

| case | rev 14 | rev 15 |
|---|---|---|
| `/proc/self/cwd/<rel>/usr/bin/docker`, deep cwd, `-e FIRECRAWL_API_URL` | `None` | cannot verify: `... which is not 'docker' as the child's PATH resolves it` |
| `/usr/bin/docker` (absolute), deep cwd, `-e FIRECRAWL_API_URL` | `None` | cannot verify: `the entry sets cwd …, and its launcher is a path, which can depend on that cwd` |
| `-e FIRECRAWL_API_URL=http://ai:3002`, `:latest`, URL not in docker's env | `None` | `... FIRECRAWL_API_URL is set) but its client docker:example/client is unpinned ...` |
| `--env-file /srv/env`, `:latest` | `None` | `... may talk to a self-hosted backend (its docker --env-file …) … cannot verify …` |
| URL only in docker's own env, not passed, `:latest` | `... is unpinned ...` | `None` (the client never receives it) |
| control: bare `docker`, digest, `-e FIRECRAWL_API_URL`, deep cwd | `None` | `None` |

## Revision 14 (2026-09-27): board round 12 on `03edb0a`: a relative launcher path is never silent

> **Corrected by revision 15.** The Windows text below ("always loud on Windows") is withdrawn:
> Windows searches the parent's cwd and `PATH`. Launcher paths are compared literally, never via
> `realpath`, and docker's cwd exemption holds only for the bare `docker` spelling.

The round-12 claude seat returned DISAGREE on one narrow blocker. Everything else held: its
own input sweep matched the plan's, the round-11 fixes are closed, the `PATH` rule uses the
child's `PATH`, and the 368 tests, the gates and the full suite (4871 passed) reproduced.
The blocker was **reproduced first** against revision 13 (`repro_r14.py`, appendix).

**B-1.** `_launcher_spelled_as_itself` compared a **relative** path command with
`realpath`, which resolves it from **pmcp's** cwd. POSIX runs a relative path containing
`/` from the **child's** cwd, and the entry sets that cwd (`.mcp.json`). npx, uvx, cargo
and local commands are already loud on an entry-set cwd, but docker is exempt. So
`'../' * 12 + 'usr/bin/docker'` with a deep entry cwd was silent and `[PINNED]`, while a
planted `<cwd>/../../usr/bin/docker` would run.
- **Fix, on every platform:** in the path branch, `if not os.path.isabs(command): return
  False`. A relative launcher path is never the launcher; it is "cannot verify": `it
  launches through '<command>', which is not 'docker' as the child's PATH resolves it`.

**N-2: decided, not only disclosed.** On POSIX, a child `PATH` with a relative or empty
entry (`.:$PATH`, `/usr/bin::/bin`, `bin:...`) makes a **bare** `docker` resolve inside the
entry's cwd first. `_path_searches_the_cwd(path_var)` detects this. With an entry-set cwd,
docker is then "cannot verify": `the entry sets cwd '<dir>', and the child's PATH would look
for the launcher inside it`. docker keeps its cwd exemption only while the child's `PATH`
cannot find `docker` in that cwd.

**N-3: Windows.** `CreateProcess` searches the current directory before `PATH`, so on
Windows `_path_searches_the_cwd` is always true. A docker entry with an entry-set cwd is
loud there. The relative-path refusal applies there too. npx, uvx and cargo were already
loud on any entry-set cwd.

**N-1: R20 widened.** A `.mcp.json`/`.pmcp.json` server under a name pmcp does not ship is
in the same position as an overlay-only one: it has no shipped relaxer to check against.

**Input sweep, corrected row.** Launcher identity: the bare platform spelling, or an
**absolute** path whose `realpath` equals the child `PATH`'s resolution. A relative path
is never accepted. A bare name resolved through a `PATH` that searches the cwd is accepted
only where the cwd is judged (npx/uvx/cargo: always loud on an entry cwd; docker: loud when
the `PATH` searches it).

| # | finding (round 12) | resolution | evidence (rev 13 → rev 14, `repro_r14.py`) |
|---|---|---|---|
| **B-1** (blocking) | A relative launcher path was resolved from pmcp's cwd, not the child's. | A relative path is never the launcher, on any platform. | `../` × 12 `usr/bin/docker` + deep entry cwd: `None` → cannot verify, `... which is not 'docker' as the child's PATH resolves it`. Tests `test_a_relative_launcher_path_is_never_the_launcher` (7 launchers × with/without an entry cwd) and `test_a_relative_path_to_the_resolved_launcher_is_still_not_the_launcher` (npx, docker: a relative path that, from pmcp's cwd, names exactly the resolved binary; host-independent). Mutant M124 (relative path accepted). |
| **N-1** | R20's scope | Widened to configured servers under an unshipped name. | Documentation. |
| **N-2** | `.:$PATH` + bare `docker` + entry cwd | Loud when the child `PATH` has a relative or empty entry. | `PATH=.:/usr/bin:/bin` and `PATH=/usr/bin::/bin`: `None` → cannot verify; `PATH=/usr/bin:/bin`: `None` → `None`. Test `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd` (4 ids). Mutants M125, M126. |
| **N-3** | Windows semantics | Relative paths refused; docker with an entry cwd loud on Windows (`CreateProcess` searches the cwd). | Stated here. |

**Costs.** Shipped cost is unchanged, **0 of 77** (every shipped command is a bare `npx` or
`uvx`, and no shipped entry sets a cwd). The operator cost: a relative launcher path under a
self-hosted relaxer now warns. So does docker with an entry cwd when the host `PATH` has a
relative entry, or on Windows.

**Tests:** the revision-14 file has **388 tests** (368 in revision 13). Against the
revision-13 code it gives **7 failed, 381 passed**:
- the two `docker-deep` ids (they depend on this host having `/usr/bin/docker`, which it
  does);
- the three `PATH`-searches-cwd ids;
- the two host-independent relative-path ids.

The other relative-path ids were already loud on revision 13, because the path did not
resolve to the `PATH` binary from pmcp's cwd. They stay as regression guards. The docker
digest control now pins `PATH=/usr/bin:/bin`, to be independent of the host's `PATH`.

## Rev 13 board findings — before/after, measured

`repro_r14.py` (appendix), one fresh process per row: a configured docker entry under the
shipped `firecrawl` relaxer, a 12-deep entry cwd, and `PATH` set per row. Trees: `260cc1a`
+ the revision-13 patch (`03edb0a`), and `260cc1a` + the revision-14 patch. The host has
`/usr/bin/docker`.

| case | rev 13 | rev 14 |
|---|---|---|
| `'../' * 12 + 'usr/bin/docker'`, deep cwd, `PATH=/usr/bin:/bin` | `None` | cannot verify: `it launches through '../../…/usr/bin/docker', which is not 'docker' as the child's PATH resolves it` |
| bare `docker`, cwd, `PATH=.:/usr/bin:/bin` | `None` | cannot verify: `the entry sets cwd …, and the child's PATH would look for the launcher inside it` |
| bare `docker`, cwd, `PATH=/usr/bin::/bin` | `None` | cannot verify (the same) |
| control: bare `docker`, cwd, `PATH=/usr/bin:/bin` | `None` | `None` |

## Revision 13 (2026-09-27): board round 11 on `791380c`: shipped relaxers; path launchers need evidence

> **Corrected by revision 14.** A relative launcher path is never the launcher (the
> launcher-identity row of the sweep below is superseded by revision 14's).

The round-11 claude seat returned DISAGREE on one blocker. Everything else held:
- round 10's B-1 is closed, and 58 more wrapper variants all warn;
- the shipped-command evidence cannot be tampered with;
- the parser is unchanged across 450,604 argvs, and a real-npm sample showed 0 violations;
- 358 tests, the gates and the full suite (4861 passed) reproduced.

Both items were **reproduced first** against revision 12 (`repro_r13.py`, appendix).

**Blocking: whether the warning applies was decided from the overlay's relaxer list.**
Three places read `api_key_optional_when` from the **overlay-applied** entry:
- `_version_pin_warning`'s early return, used by `update_server`;
- `_relaxable_manifest_servers`' filter, so health never looked at the server;
- `relaxed_by` in `_unpinned_self_hosted_warning`, through `credential_requirement`, which
  also short-circuits on `requires_api_key: false`.

So an overlay replacing `firecrawl` without the key (or with `requires_api_key: false`, or
an older copy of the entry) silenced the warning while `FIRECRAWL_API_URL` was set and an
unpinned `npx -y firecrawl-mcp` ran. **Fix:** `_warning_relaxers(server_name,
manifest_server)` = the **shipped** manifest's relaxer keys for that name
(`_shipped_manifest_relaxers`, read directly from the packaged file like the declarations
and commands) **union** the overlay's own. All three places use it. The relaxer counts
when its value in the child's environment is usable (`is_usable_credential_value`, the
same test the gate uses). **The credential gate is unchanged:** `credential_requirement`
still reads the loaded entry, and gate behaviour is out of scope here. **Disclosed limit:**
an overlay-only server under a name pmcp does not ship has no shipped entry to check
against. If it declares no relaxer, the warning cannot apply to it (R20).

**Non-blocking, now fixed: a basename is not evidence either.** Rev 11 judged a launcher
by its basename, so `/tmp/anything/npx`, `./npx`, `node_modules/.bin/npx`, `NPX`, `C:npx`
and `npx.cmd` on Linux were read as npx and were silent with a pinned argv. That is
inconsistent with rev 12's positive-evidence rule. Now a modelled launcher
(`npx`/`npm`/`uvx`/`uv`/`cargo`/`docker`) is that launcher only when it is spelled as
itself (`_launcher_spelled_as_itself`):
- the bare name as the platform spells it: `npx`; on Windows also `npx.cmd`, `.exe` or
  `.bat`, case-insensitively;
- or a path equal (by `realpath`) to what the **child's** `PATH` resolves the name to.

Anything else is "cannot verify": `it launches through '<command>', which is not 'npx' as
the child's PATH resolves it`. The child's `PATH` is the host's plus the entry's; an
entry-set `PATH` is itself not inert.

**The sweep (rev 13): every input the three decisions read, and where it comes from.**
None of them treats an overlay-controlled list as evidence.

| decision | input | source | verdict |
|---|---|---|---|
| does the warning apply? | relaxer keys | **shipped** `api_key_optional_when` ∪ the overlay's own (`_warning_relaxers`) | the overlay can only add |
| | relaxer value, per spawn member (rev 16) | `_member_relaxer` over EVERY `_spawn_set` member, each read by the **one parser** (`_read_argv`): a clean docker reading (launcher spelled as docker itself, no problem) that ALSO passed the per-member judge (`_member_problems` empty, with the shipped declarations; rev 17) → its `container_env` (`-e`, `--env`, pflag clusters; last wins), and with any member problem the process env counts too; ANY judge problem (rev 19: the ONE `_judge_members` result, `_Judged.all_problems`: an unreadable argv of any launcher, a non-allowlisted env key, a container env key, an entry cwd, a launcher-identity problem) → "may", naming the problem; a member with no problem → its container env (clean docker) or process env | applies if ANY member applies; no basename, no args-only read, no second parser |
| | relaxer value | the child's environment (host env, trusted, + the entry's env) | the premise: without it the client does not talk to a self-hosted backend. Limit R21: a self-hosted URL passed some other way (a client CLI flag) is not detected |
| | the entry exists | `load_manifest().get_server(name)`: an overlay replaces entries, it cannot delete a shipped one | the gate is the relaxer, above |
| | local vs remote | `resolved.config` (the connected config for health; the effective config for update) | a remote config spawns no client |
| | which servers health judges | `get_connected_configs()` ∩ `_relaxable_manifest_servers()` (the shipped ∪ overlay relaxers) | runtime truth plus the shipped keys |
| is it silent? | spawn set | `_SERVER_SPAWN_SITES` builders: `resolved.config` args, and for `source == "manifest"` the loaded entry's `install` argvs | members are **judged**, never trusted; `source` is how the gateway resolved the server (a configured one is lazy-started from its own args) |
| | each member's reading | the **one parser** over the member's own argv | argv only |
| | launcher identity | the bare platform spelling, or the child `PATH`'s resolution (rev 14: an ABSOLUTE path only; rev 15: equal to `which`'s result literally, never `realpath`; a path spelling never gets docker's cwd exemption; on Windows `which` spells `PATHEXT`'s case, so `...\npx.cmd` ≠ `...\npx.CMD`: fails closed) | the basename only selects which parser; a modelled launcher must then pass `_launcher_spelled_as_itself`; no list, nothing resolved in pmcp's process |
| | local-binary exemption | the **shipped** command for the name (`_shipped_local_commands`) | positive evidence only |
| | env keys | each member's env block, against the allowlist: **shipped** declarations, locale keys, per-launcher inert keys | overlay declarations exempt nothing |
| | cwd | the member's cwd (entry-set) | judged |
| | exactness | `_is_exact_pin`, pure grammar | no input |
| `[PINNED]`? | main's pin | `_detect_effective_version_pin` (main's refusal reader) | only chooses the branch |
| | the label | a `silent` verdict **and** its selector equal to main's pin | two readers must agree |

**Costs.** The shipped manifest declares one relaxer (`firecrawl`:
`FIRECRAWL_API_URL`), so the relaxer union adds warnings only for a `firecrawl` entry an
overlay changed. The launcher rule costs nothing shipped: every shipped command is the
bare `npx` or `uvx`. Shipped cost stays **0 of 77** (`shipped_cost.py`); all 97 local
shipped entries read as `unpinned`. The operator cost is an absolute-path or oddly spelled
launcher under a self-hosted relaxer, unless it is the path the child's `PATH` resolves.

| # | finding (round 11) | resolution | evidence (rev 12 → rev 13, `repro_r13.py`) |
|---|---|---|---|
| **blocking** | Relaxer keys were read from the overlay-applied entry in three places. | Shipped ∪ overlay relaxers decide whether the warning applies; the gate is unchanged; the limit is disclosed (R20). | Overlay drops `api_key_optional_when`, sets `requires_api_key: false`, or is an older copy: `None` → `... FIRECRAWL_API_URL is set) but its client npm:firecrawl-mcp is unpinned ...`. Tests `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer` (4 ids, health and `update_server`), `test_the_warning_relaxers_are_shipped_union_overlay`. Mutants M119 (relaxer read from the overlay only), M120 (the update wrapper), M121 (the health filter). |
| non-blocking | A basename was taken as the launcher. | A modelled launcher must be spelled as itself: the bare platform name, or the child `PATH`'s resolution. | `/tmp/anything/npx`, `./npx`, `node_modules/.bin/npx`, `NPX`, `C:npx`, `npx.cmd` on Linux: `None` → cannot verify, `... which is not 'npx' as the child's PATH resolves it`. Control: pinned `npx`, `None` → `None`. Tests `test_a_launcher_must_be_itself_not_named_like_it` (10 ids), `test_a_launcher_path_the_childs_path_resolves_is_the_launcher` (the resolved path is silent; a same-named binary elsewhere is loud). Mutants M122 (basename judged), M123 (any path named like the launcher counts). |

**Tests:** the revision-13 file has **368 tests** (358 in revision 12). Against the
revision-12 code it gives **16 failed, 352 passed**: the 10 launcher ids, the resolved-path
control, the 4 relaxer ids and the relaxer unit test. Rev 11's basename test
(`test_a_launcher_is_judged_by_its_basename`) is replaced by the two launcher tests above.

## Rev 12 board findings — before/after, measured

`repro_r13.py` (appendix), one fresh process per row, with `PATH=/usr/bin:/bin` and a
manifest entry `firecrawl` (the shipped relaxer and declarations, `FIRECRAWL_API_URL`
set). Trees: `260cc1a` + the revision-12 patch (`791380c`), and `260cc1a` + the
revision-13 patch.

| case | rev 12 | rev 13 |
|---|---|---|
| overlay drops `api_key_optional_when`, unpinned `npx -y firecrawl-mcp` | `None` | `... FIRECRAWL_API_URL is set) but its client npm:firecrawl-mcp is unpinned ...` |
| overlay `requires_api_key: false` | `None` | the same warning |
| overlay older copy (no relaxer, no `env_var`) | `None` | the same warning |
| `/tmp/anything/npx -y firecrawl-mcp@3.25.5` | `None` | cannot verify: `it launches through '/tmp/anything/npx', which is not 'npx' as the child's PATH resolves it` |
| `./npx`, `node_modules/.bin/npx`, `NPX`, `C:npx`, `npx.cmd` (Linux) | `None` | cannot verify (the same message, naming each) |
| control: `npx -y firecrawl-mcp@3.25.5` | `None` | `None` |

## Revision 12 (2026-09-27): board round 10 on `ba58ade`: the local-binary exemption needs positive evidence

> **Extended by revision 13.** Whether the warning applies now also rests on the shipped
> relaxers, and a launcher must be spelled as itself (basename alone is no longer read).

The round-10 claude seat returned DISAGREE on one blocker. Everything else held, including
dropping npm identity. The seat ran 450,604 generated argvs; the 4,960 that pmcp reads as
exact pins all ran the pinned package on real npm 10.9.9 and 11.19.0, against a fake
registry with an impostor, and the controls caught the impostor. 800k specs against npa
12/13 gave 0 mismatches. Round 9's four blockers are closed. B-1 was **reproduced first**
against revision 11 (`repro_r12.py`, appendix).

**B-1 is the denylist class again.** `_read_argv` modelled npx, npm, uvx/`uv tool run`,
cargo and docker, and flagged `_UNMODELLED_RUNNERS`. **Every other command** became
`local`, which waives the pin, so "not on the runner list" counted as evidence. The
following were silent with `FIRECRAWL_API_URL` set:
- `timeout 600 npx -y firecrawl-mcp`;
- `nice`/`stdbuf`/`nohup`/`setsid`/`sudo -E` prefixes;
- `busybox sh -c "npx ..."`, `mise exec -- npx ...`, `volta run npx ...`,
  `corepack pnpm dlx ...`;
- `pipx run firecrawl-mcp`, `python3 -m uv tool run ...`, `go run example.com/fc@latest`;
- `npx.js`/`npx.ps1`, because the basename strips only `.exe`/`.cmd`/`.bat`.

**The rule now:**
- **A command counts as a local server binary only on positive evidence:** pmcp's
  **shipped** manifest must name that exact command for that server name.
  `_shipped_manifest_commands`/`_shipped_local_commands` read the packaged
  `manifest.yaml` directly, as the declarations are read, never through overlays.
- **Any other unrecognised command is `unrecognised`:** "cannot verify", naming the
  command. That includes wrappers, runners and version managers.
- **A known launcher or runner name with an extension pmcp does not strip** (`npx.js`,
  `npx.ps1`, `uvx.sh`) is always `unrecognised` ("a spelling of 'npx' that pmcp does not
  read"). It is never local, even if a manifest named it.
- **Modelled launchers are unchanged.** So is the rest of the judge: env, cwd and the
  whole spawn set are still checked for a local binary.

**Costs.**
- **Shipped cost: none.** The shipped manifest's commands are only `npx` (79) and `uvx`
  (19), plus 9 remote/empty entries. Both are modelled, so no shipped entry relies on the
  exemption. Measured with `shipped_cost.py`: **0 of 77** pinnable entries are not silent
  once pinned, on rev 11 and rev 12 alike. All 97 local shipped entries read as `unpinned`
  on both, with none newly "cannot verify".
- **Operator cost.** An overlay, `.pmcp.json` or `.mcp.json` entry under a self-hosted
  relaxer that launches with a custom command now warns "cannot verify", naming the
  command. That covers a locally installed binary, a wrapper and a version manager alike.
  The remedy is to launch through a modelled launcher with an exact pin. The shipped
  manifest could also name such a binary, which would be a reviewed change.

**Non-blocking items.**
- **N-1: narrowed claim.** `_update_server_unwarned`'s `package_type == "unknown"` branch
  is main's code and is untouched by this plan. It still prints the raw command line
  (`from \`<command args>\``). `tests/test_tools.py:5101` asserts it names `npm run mcp`,
  its purpose being to name the command line. The redaction claim is therefore narrowed:
  every argv shown by **this feature's** warning, label and pinned-refusal line goes
  through the renderer. Main's unknown-package line is listed as a follow-up.
- **N-2: executable and cwd.** A cwd is now shown through `_operator_safe`, escaped. The
  executable names, env key names and cwd paths shown are escaped, not redacted. They
  are paths and names, not argument values.
  - `_materialize_version_pin`'s load-time refusal log still prints the operator's
    `command!r`. It is a log line about the operator's own overlay, not a health or
    update diagnostic.
  - A string-valued `install` argv (`install.linux: "npx -y x"`) is one unreadable
    member, no longer a character tuple. A member that is a local command says "(it runs
    a local command)" instead of `None@latest`.
- **N-3: README.** A uv `==X` pin is a **version**, not an artifact. PEP 440 `==1.2.3`
  also matches a local version `1.2.3+x` on a non-PyPI index, and PyPI allows new files
  to be added to an existing release. Both involve the host's index or PyPI itself, so
  they are outside the boundary, and the README now says so.

| # | finding (round 10) | resolution | evidence (rev 11 → rev 12, `repro_r12.py`) |
|---|---|---|---|
| **B-1** (blocking) | Any command outside the runner list was a "local binary", and the pin was waived. | Positive evidence only: the exact command pmcp's shipped manifest names for the server. Everything else is `unrecognised`. A launcher with an unstripped extension is never local. | Every case listed above: `None` → cannot verify: `it launches through '<command>', a command pmcp does not recognise; only the command pmcp's own manifest names for this server is exempt from the pin` (`npx.js`/`npx.ps1`: `a spelling of 'npx' that pmcp does not read`). Control: pinned npx, `None` → `None`. Tests: `test_an_unrecognised_command_is_never_a_local_binary`, **generated**: each of 10 wrappers × 4 launched argvs (unpinned npx, pinned npx, pinned uvx, `pnpm dlx`), plus `pipx run`, `python3 -m uv`, `go run`, `npx.js`, `npx.ps1`, `uvx.sh` and a bare unshipped command, each as a manifest and as a configured entry (94 ids); and `test_only_the_shipped_command_is_a_local_binary`. **95 red on rev 11.** Mutants M116 (an unrecognised command treated as local, 89 red), M117 (extension guard removed), M118 (the entry's own command taken as evidence, 88 red). |
| **N-1** | Main's unknown-package message prints the raw argv. | Claim narrowed; follow-up listed. | Documentation. |
| **N-2** | cwd, command and key names unredacted; string install and `None@latest` cosmetics. | cwd escaped; string install is one unreadable member; local-member wording; the rest disclosed. | Covered by the existing token test, plus the new rule tests. |
| **N-3** | uv `==X` versus local versions and added files. | README sentence. | Documentation. |

**Tests:** the revision-12 file has **358 tests** (263 in revision 11). Against the
revision-11 code it gives **95 failed, 263 passed**: all 94 generated ids and the rule
unit test. The existing local-binary tests keep passing, because the autouse fixture now
also supplies their shipped local commands (`_TEST_SHIPPED_LOCAL_COMMANDS`), the
positive evidence the exemption requires.

## Rev 11 board findings — before/after, measured

`repro_r12.py` (appendix), one fresh process per row, with a manifest entry `firecrawl`
(shipped command `npx`), its shipped declarations and the relaxer set. Trees: `260cc1a` +
the revision-11 patch (`ba58ade`), and `260cc1a` + the revision-12 patch.

| argv | rev 11 | rev 12 |
|---|---|---|
| `timeout 600 npx -y firecrawl-mcp` | `None` | cannot verify: `it launches through 'timeout', a command pmcp does not recognise; ...` |
| `nice` / `stdbuf -oL` / `nohup` / `setsid` / `sudo -E` + `npx -y firecrawl-mcp` | `None` | cannot verify, naming `nice` / `stdbuf` / `nohup` / `setsid` / `sudo` |
| `mise exec -- npx ...`, `volta run npx ...`, `corepack pnpm dlx ...` | `None` | cannot verify, naming `mise` / `volta` / `corepack` |
| `pipx run firecrawl-mcp`, `python3 -m uv tool run firecrawl-mcp`, `go run example.com/fc@latest` | `None` | cannot verify, naming `pipx` / `python3` / `go` |
| `npx.js -y firecrawl-mcp@3.25.5`, `npx.ps1 -y firecrawl-mcp@3.25.5` | `None` | cannot verify: `it launches through 'npx.js', a spelling of 'npx' that pmcp does not read` |
| control: `npx -y firecrawl-mcp@3.25.5` | `None` | `None` |

(`busybox sh -c "npx ..."` is covered by the generated test. The shell loop that prints
this table does not handle a case name containing quotes.)

## Revision 11 (2026-09-27): board round 9 on `0a63441`: one parser, one judge per spawn member

> **Corrected by revision 12.** The "local" family below required no evidence; revision 12
> requires the shipped manifest to name the command. The redaction claim is narrowed to this
> feature's own diagnostics (N-1).

Round 9 returned the following verdicts:
- claude: PARTIALLY AGREE, with NB-1 to NB-4 (test gaps and a claim);
- gemini: no blocking finding;
- grok: two blockers;
- codex: two blockers.

Every blocker was **reproduced first** against revision 10 (`repro_r11.py`, appendix).
They are two recurring classes: **two readers that disagree** (grok B1) and **an
exemption applied before a check** (grok B2). The codex pair adds unredacted diagnostics
and unguarded exceptions. Revision 11 fixes each class structurally.

1. **ONE parser per launcher** (`_read_argv` → `_read_npx_slot`, `_read_npm`, `_read_uvx`,
   `_read_cargo`, `_read_docker`), each modelled on the launcher's own semantics. It is the
   only reader of a spawning argv in this feature. Pin reading, the shape check, the
   warning and the `[PINNED]` label all use its `_Reading` (family, package, selector,
   exact, problem). `detect_package_type`, `_detect_effective_version_pin` and npm
   identity are **no longer consulted** by the warning. The parser's rules:
   - **A single-valued flag that repeats is not a recognised shape.** This covers uv's
     `--from` (clap `Option<String>`, which resolves last-wins), `--color`, cargo's
     `--version`/`--vers` (or a version given both in `crate@X` and `--version`), and
     docker's `--name`/`--pull`/`--network`/`--platform`. pmcp refuses to guess.
   - **`npm exec` reads flags after the package itself.** Unlike npx, whose pre-scan
     inserts `--`, so only `--` or nothing may follow the spec.
   - **A range is read, not refused.** `pkg@^3` floats rather than being unreadable,
     because npm fetches the same registry package.
   - **The launcher is judged by its basename.** `/usr/bin/npx`, `npx.cmd`, `uvx.exe`: the
     path is trusted like a local binary's (NB-4), and the README says so.
   - **Harmless spellings are now recognised.** `uvx -qq`, `cargo install -fq`,
     `cargo install fc@1.2.3`, `docker container run -it` and `npm exec ... -- args` were
     documented false positives (R14); the parser is pmcp's own, so they read correctly.

   `update_server` keeps main's refusal (main's reader decides *whether* to refuse). The
   label comes from the judge: `[PINNED]` only when the judge finds one exact pin **and**
   it is the pin main's reader found. If the two readings disagree, it is not a pin
   (`pmcp's reading of the argv does not name this pin`).
2. **ONE judge per spawn member** (`_judge_spawn_set`). Every member of `_spawn_set` is
   read and checked, in order parse → shape → pin → env (`_member_problems`: every key of
   the member's env, plus docker `-e` keys) → cwd, **before** any verdict is chosen. The
   local-binary exemption now waives **only the pin requirement**. It never waives shape,
   env or cwd. It applies last, and only when every member is literally
   `[command, *args]`. So a local command whose env sets `NODE_OPTIONS`, `LD_PRELOAD`,
   `OPENSSL_CONF` or `PATH=""`, or whose entry sets `cwd`, is "cannot verify". That holds
   for manifest and configured (`.pmcp.json`) entries alike (grok B2).
3. **ONE secret-safe renderer** (`_render_argv` → `installer._render_install_argv`). Every
   argv a warning or an `update_server` message shows goes through it (codex 1), including:
   - a spawn member's `where()`;
   - the local-exemption message;
   - main's refusal line, which this patch now renders instead of joining the raw argv.

   Single tokens are rendered as a package slot (`_safe_token`), flag names through
   `_operator_safe`, and a URL requirement is shown as `@ <URL>`.
4. **Per-server containment** (codex 2):
   - an argv with a non-string element is `unreadable` ("its argv is empty or not a list
     of strings"), never formatted raw;
   - an exception inside the judge is that server's "evaluating its argvs failed";
   - `gateway.health`'s loop catches **per server** (`_unverifiable_warning`), so one
     server can never suppress another's warning;
   - the `update_server` wrapper and label do the same.
5. **The spawn-site check is load-bearing and complete** (claude NB-1 to NB-3):
   - `_SERVER_SPAWN_SITES` now maps each site to the **builder** of its members
     (`_args_member`, `_install_members`), and `_spawn_set` iterates it. So dropping a site
     drops its members, and M107 is a real behaviour mutant.
     `test_the_spawn_set_is_built_from_the_site_table` asserts the sites a manifest entry's
     members come from.
   - The AST matcher now resolves each module's imports and aliases. It counts every
     **reference** to a primitive: `subprocess.*` (incl. `getoutput`), `asyncio.create_subprocess_*`,
     `os.system/popen/fork/forkpty/exec*/spawn*/posix_spawn*`, `pty.spawn/fork`,
     `anyio.open_process/run_process`, `multiprocessing.Process`,
     `mcp...stdio_client/StdioServerParameters`, and the `.subprocess_exec`,
     `.adopt_process` and `.containers.run` methods. That includes references through
     `functools.partial` and `getattr(module, "name")`, but excludes annotations.
   - It counts **call sites per qualname**, not a set. The table now carries a count per
     site, so a second spawn inside a classified function fails the test.
   - `test_the_spawn_site_matcher_resolves_aliases_and_counts_call_sites` runs it over a
     synthetic package holding **all 22 variants** the seat built (plus a two-spawn
     function and an annotation-only function), and asserts each is found.

**Superseded by this revision.** Because the warning no longer reads npm identity, three
earlier behaviours are replaced by the one parser, and their mutants (M29, M30, M38, M39,
M44) are retired:
- the rev-3 N2 identity-disabled structural fallback;
- the rev-4 N-b/N-d identity-refusal causes;
- the rev-6 NB-1 launcher-keyed cause.

The corresponding tests now assert the parser's reading. An `npm exec -y pkg` is "is
unpinned", and a tarball slot is "not a plain registry spec", in both identity modes.
npm identity (#195) is untouched and still serves main's `detect_package_type`, the
refresher and `update_server`'s refusal.

| # | finding (round 9) | resolution | evidence (rev 10 → rev 11, `repro_r11.py`) |
|---|---|---|---|
| **grok B1** (blocking) | Repeated `uvx --from`: the pin reader took the FIRST `--from`, the shape check the LAST, and uv uses the LAST. `uvx --from evil==1.0.0 --from cowsay==6.0 cowsay` was silent and `[PINNED] evil 1.0.0` while uv ran cowsay 6.0. Install argvs had the same problem. | (1): one parser; a repeated single-valued flag is unreadable. | Both `--from` cases: `None` → cannot verify: `its argv repeats --from, a single-valued uv option`. `update_server`: not `[PINNED]`. Tests `test_a_repeated_single_valued_flag_is_not_a_recognised_shape` (5, incl. cargo, docker and `npm exec --package` after the spec), `test_update_server_never_labels_a_repeated_from_pinned`, `test_a_repeated_from_in_an_install_argv_is_unverifiable`, and `test_update_server_never_labels_a_pin_its_judge_did_not_read`. Mutants M108-M111, M93. |
| **grok B2** (blocking) | The local-binary return came before the env check: `NODE_OPTIONS=--require`, `LD_PRELOAD`, `OPENSSL_CONF`, `PATH=""` were silent for a local command (also `.pmcp.json`). | (2): the exemption waives only the pin; env and cwd are judged for every member. | Local command + `NODE_OPTIONS` / `LD_PRELOAD` / `PATH=""`: `None` → cannot verify: `its args run a local command, but the entry's env sets ...`. Tests `test_a_local_command_does_not_waive_the_env_check` (4 keys × manifest/configured), `test_a_local_command_does_not_waive_the_cwd_check`, `test_the_judge_checks_every_members_own_env_and_cwd`. Mutants M112, M101, M95. |
| **codex 1** (blocking) | `_Spawn.where()` joined the argv raw, so a `--token` value reached health warnings and `update_server` messages. | (3): one renderer for every argv shown. | A token in an install argv: rev 10 printed it (`TOKEN-LEAKED`); rev 11 shows `npx -y <redacted> <redacted> <redacted>`. Test `test_no_diagnostic_renders_a_token_from_an_argv` (install divergence, local exemption, URL requirement, `update_server` message and warnings). Mutant M113. |
| **codex 2** (blocking) | `install.mac: ["echo", 42]` raised `TypeError`: `update_server` raised, health dropped the warning, and a loop-wide except suppressed a later server's warning. | (4): malformed argvs are unreadable members, and failures are contained per server. | rev 10: `RAISED TypeError`. rev 11: cannot verify: `its mac install argv (`echo <redacted>`) ... runs something pmcp cannot read (its argv is empty or not a list of strings)`. Tests `test_a_malformed_member_is_contained_to_its_own_server` (two servers; `update_server` returns `[FLOATING]`), `test_an_exception_judging_one_server_never_hides_another`, `test_health_contains_a_failure_outside_the_judge_per_server`. Mutants M114, M115. |
| **NB-1** | The AST matcher missed aliases, `from` imports and 18 primitive spellings (22 of 22 synthetic variants missed). | (5): import-resolving, reference-counting matcher. | 22 of 22 variants found, plus the 4 controls (measured on the seat's `astpkg/`). Test `test_the_spawn_site_matcher_resolves_aliases_and_counts_call_sites`. |
| **NB-2** | One entry per qualname hid a second spawn in a classified function. | (5): counts per site. | `dup.py:refresh_server` counts 2. The table has counts (`refresh_server` 2, `_restart_local_pmcp_service` 3). |
| **NB-3** | `_SERVER_SPAWN_SITES` did not drive `_spawn_set`, so M107 was a no-op. | (5): the dict maps sites to member builders, and `_spawn_set` iterates it. | M107 now kills behaviour tests. `test_the_spawn_set_is_built_from_the_site_table`. |
| **NB-4** | Absolute-path launchers are judged by basename. | Kept, and stated: the README says a launcher is judged by its basename and its path is trusted like a local binary's. | `test_a_launcher_is_judged_by_its_basename` (6 ids). |
| stale text | Rev-8 "any other command is not judged". | Marked superseded where it appears. | Documentation. |

**Shipped cost** (`shipped_cost.py`, now calling `_judge_spawn_set` over `_spawn_set`):
**0 of 77** pinnable entries are not silent once pinned. Judged as shipped (unpinned),
all 97 local shipped entries read as `unpinned`; none reads as "cannot verify". So the one
parser reads every shipped argv shape, including context7's windows `cmd /c npx` install
argv, which only matters if args is exactly pinned. Step 7: `77 pinnable, 30 refused`.

**Tests:** the revision-11 file has **263 tests** (236 in revision 10). Against the
revision-10 code it gives **34 failed, 229 passed** (`tests_on_rev10.log`):
- repeated single-valued flags: 5;
- the `update_server` label: 2 (a repeated `--from`, and a pin the judge did not read);
- a repeated `--from` in an install argv: 1;
- local commands' env and cwd: 8 + 1;
- the one judge per member: 1;
- a malformed member and the two containment tests: 3;
- the token test: 1;
- the modelled spellings: 4;
- launcher basename: 2;
- rewritten identity-era and read-launcher assertions: 2 + 1 + 1 + 1 (unknown-flag
  text, `npm exec` unpinned, the URL requirement, the `1.0.0-x.tar-gz` slot);
- `cargo run` not being a `cargo install` shape: 1.

Two first-run survivors (M52, M87) were closed with an npm-reason assertion in
`test_health_warns_on_a_range_or_dist_tag` and a `cargo run` id, and the full mutant set
was re-run on the final file.

The new AST and site-table tests pass on both revisions: they check the tests' own
matcher and the unchanged spawn sites.

## Rev 10 board findings — before/after, measured

`repro_r11.py` (appendix), one fresh process per row: a manifest entry `firecrawl` with
its shipped declarations and the relaxer set. Trees: `260cc1a` + the revision-10 patch
(`0a63441`), and `260cc1a` + the revision-11 patch.

| case | rev 10 | rev 11 |
|---|---|---|
| grok B1 `uvx --from evil==1.0.0 --from cowsay==6.0 cowsay` | `None` | cannot verify: `its argv repeats --from, a single-valued uv option` |
| grok B1 `uvx --from cowsay==6.1 --from cowsay==6.0 cowsay` | `None` | cannot verify (the same) |
| grok B2 local command + `NODE_OPTIONS=--require /srv/hook.js` | `None` | cannot verify: `its args run a local command, but the entry's env sets NODE_OPTIONS ...` |
| grok B2 local command + `LD_PRELOAD` | `None` | cannot verify (`... sets LD_PRELOAD ...`) |
| grok B2 local command + `PATH=""` | `None` | cannot verify (`... sets PATH ...`) |
| codex 1: a `--token` value in an install argv | the token printed in the warning | `npx -y <redacted> <redacted> <redacted>` |
| codex 2: `install.mac: ["echo", 42]` | `RAISED TypeError` | cannot verify: `its mac install argv (`echo <redacted>`) ... runs something pmcp cannot read` |
| `npm exec -y firecrawl-mcp@3.25.5 --package=evil` | `None` | cannot verify: `its argv passes --package after the package, which npm reads itself` |
| `cargo install fc@1.2.3` (was R14) | `is unpinned` | `None` |
| `docker container run -it img@sha256:...` (was R14) | `docker:container is unpinned` | `None` |
| control: shipped `firecrawl`, every argv pinned | `None` | `None` |
| control: local command, the same argv everywhere, declared env only | `None` | `None` |

## Revision 10 (2026-09-27): board round 8 on `bdc13ca`: the whole spawn set before any exemption

> **Superseded in part by revision 11.** `_SERVER_SPAWN_SITES` now builds the spawn set;
> the spawn-site test counts resolved references; the local-binary exemption waives only the
> pin; and argvs are read by one parser and rendered secret-safe.

The round-8 claude seat confirmed that round 7's B1 and B2 are closed. It returned
DISAGREE on one new blocker (B-1) and three non-blocking items (N-1 to N-3). B-1 was
**reproduced first** against revision 9 (`repro_r10.py`, appendix).

**B-1 is the same class as round 7's B1: a spawn path escaping the check.** Revision 9
judged the install argvs only on the exact-pin path. The local-binary early return
(`if launcher not in ("npx", "npm"): return None`) came before it. So an overlay copying
the shipped `firecrawl` with `command: firecrawl-mcp`, `args: []` kept the shipped
`install.linux: npx -y firecrawl-mcp`, which `gateway.provision` spawns and adopts. It
was silent while latest ran. The same held for a `uvx` install and an absolute-path
command. **The structural fix:**

1. **The spawn set is computed first.** `_spawn_set(manifest_server, resolved)` lists
   every argv that can spawn or be adopted as this server, with its env and cwd. It is
   built from `_SERVER_SPAWN_SITES`, the spawning sites of the table below, and it is
   computed before any verdict:
   - `args` (`ClientManager._connect_stdio`), with the entry's env and cwd;
   - for a manifest-sourced server, every non-empty `install[platform]`
     (`JobManager.start_install`, adopted by `_finalize_server_ready`), with the same
     env and pmcp's cwd.
2. **Every member is judged before any exemption.** On the exact-pin path, each
   non-args member must run the same exact pin (`_install_argv_problem`, which now walks
   the spawn set). The **local-binary exemption** (a command pmcp does not model is the
   host's) now applies **only when every member is literally the entry's `[command,
   *args]`**. Any other member makes it loud: `its linux install argv (`npx -y
   firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, is not
   the entry's own command (`firecrawl-mcp`), so the local-binary exemption does not
   apply`. That includes a **pinned** install argv under a local command. The seat noted
   that its silence in revision 9 was an accident, because nothing looked at it.
3. **A new spawn path fails a test.** `test_every_spawn_site_is_classified` walks
   `src/pmcp` with `ast`. It finds every spawn primitive (`create_subprocess_exec`,
   `Popen`, `subprocess.run`/`call`/`check_*`, `StdioServerParameters`, `adopt_process`,
   `exec*`, `spawn*`, `posix_spawn`) with its enclosing `file:qualname`. It asserts that
   the set equals the test's classification table (this plan's spawn-site table below),
   and that exactly the sites classified "judged" are `handlers._SERVER_SPAWN_SITES`. A
   new call site fails until someone classifies it.

**The spawn-site table (derived from the code; the test holds the same list).**

| site (`src/pmcp/...:qualname`) | spawns | disposition |
|---|---|---|
| `client/manager.py:ClientManager._connect_stdio` | the resolved config's `command` + `args`, entry env and cwd: connect, lazy connect, restart, reconnect, the respawn after `gateway.refresh`, and `update_server`'s restart | **in the spawn set** ("its args") |
| `manifest/installer.py:JobManager.start_install` | `install[detect_platform()]` (`wsl` → `linux`), entry env; `gateway.provision` | **in the spawn set** for a manifest-sourced server, every non-empty platform |
| `tools/handlers.py:GatewayTools._finalize_server_ready` | nothing; it **adopts** `start_install`'s process | covered through `start_install`. See N-3 |
| `manifest/installer.py:install_server`, `:verify_installation` | `install[platform]`; `command args[:1] --help` | **library functions with no production caller** (exported from `pmcp.manifest`, used only by tests; there is no `pmcp install` command). N-2 corrects revision 9's text. |
| `manifest/refresher.py:refresh_server` | the **manifest** entry's `command` + `args` (startup `refresh_all(servers=connected_names)`, `pmcp refresh`), built with **no `env`** (the mcp SDK default environment) | **not in the spawn set**; see N-1 |
| `tools/handlers.py:GatewayTools._run_update_probe_command` | `<pkg>@latest --help` and the like | an update probe of latest, by design; not the server |
| `manifest/environment.py:check_cli`, `:get_cli_help` | host CLI probes | not a server |
| `manifest/npm_resolver.py:NpmResolver._spawn` | the npm identity helper (`node _npm_resolve.js`) | not a server |
| `cli.py:_is_pmcp_system_service_active`, `:_restart_local_pmcp_service`, `:run_upgrade` | systemctl/launchctl; pmcp's own upgrade | not a server |

**N-1 (descriptions refresh).** `refresh_server` spawns the **manifest** argv even for a
server connected from `.mcp.json`/`.pmcp.json` with a different, pinned argv, so revision
9's "the same argv, already judged" was true only for manifest-sourced servers. It is
**not in the spawn set**, and that is correct for this warning, for three reasons:
- it is never adopted and never serves requests;
- `StdioServerParameters` is built with no `env`, so the child gets only the SDK's default
  environment, with no relaxer and no credential, and so no identity to talk to the
  self-hosted backend with;
- it exits after listing tools.

What it can do is execute an unpinned client once, and cache tool **descriptions** from a
version other than the one served. That is the existing descriptions-cache limit (Non-goal
and R2), now also stated for configured servers. Restricting the refresh to the connected
config is a descriptions-cache change and is left to that follow-up.

**N-2.** Fixed: the table and docstrings no longer mention `pmcp install`. `install_server`
and `verify_installation` are library functions without a production caller.

**N-3 (the handoff re-reads the manifest).** `_finalize_server_ready` re-reads
`load_manifest()` at handoff and records that config for the adopted process. If an
overlay is edited **during the install window**, the process came from the old install
argv while health judges the new entry. Revision 10 **documents this as a non-goal (R15)**
rather than threading the spawned config through `JobManager`, which is a provisioning
change outside this plan. It is pre-existing, needs an operator edit mid-install, and is
visible on the next restart. A clean follow-up is to snapshot the `ServerConfig` at
`start_install` and adopt with it.

| # | finding (round 8) | resolution | evidence (rev 9 → rev 10, `repro_r10.py`) |
|---|---|---|---|
| **B-1** (blocking) | The local-binary early return skipped the install argvs, which `gateway.provision` adopts. | Spawn set first; every member judged; the local exemption applies only when every member is the entry's own argv; a static test pins the spawn sites. | Local command `firecrawl-mcp` with `install.linux` `npx -y firecrawl-mcp`, with `uvx firecrawl-mcp`, with an absolute path and `--stdio`, and with a **pinned** npx install: `None` → `... its linux install argv (...), which gateway.provision spawns and adopts as the live server, is not the entry's own command ...`. Controls (a local command whose install argvs are the same argv; the shipped pinned firecrawl): `None` → `None`. Tests `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts` (4), `test_a_local_command_is_exempt_only_when_it_is_the_whole_spawn_set` (control, which also shows a configured local binary's spawn set is args only), `test_every_spawn_site_is_classified`. Mutants M104 (an early return skips the spawn set), M105 (install argvs left out of the set), M106 (the set judged only in part), M107 (a spawning site dropped from `_SERVER_SPAWN_SITES`). |
| **N-1** | The refresher spawns the manifest argv for configured servers. | Table corrected. Not in the spawn set: not adopted, no entry env, descriptions only (R2). | Documentation. |
| **N-2** | There is no `pmcp install`. | Text fixed. | Documentation. |
| **N-3** | The handoff re-reads the manifest. | Non-goal R15 with its risk. | Documentation. |

**Shipped cost:** **0 of 77** (`shipped_cost.py` now walks `_spawn_set` for the install
argvs). The new local-command rule affects no shipped entry: **0 shipped entries** have a
local command whose install argvs differ from it (the same script lists them). Step 7:
`77 pinnable, 30 refused`.

**Tests:** the revision-10 file has **236 tests** (230 in revision 9). Against the
revision-9 code it gives **5 failed, 231 passed**: the 4 B-1 ids, plus the spawn-site
test, which references `_SERVER_SPAWN_SITES`. The control is green on both.

## Rev 9 board findings — before/after, measured

`repro_r10.py` (appendix), one fresh process per row, with a manifest-sourced entry named
`firecrawl`, its shipped declarations, and the relaxer set. Trees: `876fd33` + the
revision-9 patch (`bdc13ca`), and `876fd33` + the revision-10 patch.

| case | rev 9 | rev 10 |
|---|---|---|
| `command: firecrawl-mcp`, `args: []`, `install.linux: npx -y firecrawl-mcp` | `None` | cannot verify: `its linux install argv (`npx -y firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, is not the entry's own command (`firecrawl-mcp`) ...` |
| same, `install.linux: uvx firecrawl-mcp` | `None` | cannot verify (the same message, `uvx firecrawl-mcp`) |
| `command: /opt/fc/bin/firecrawl-mcp`, `args: [--stdio]`, npx install | `None` | cannot verify (the same message) |
| `command: firecrawl-mcp`, `install.linux: npx -y firecrawl-mcp@3.25.5` (pinned) | `None` (an accident) | cannot verify: the install argv is not the entry's own command |
| control: a local command whose install argvs are all `[command, *args]` | `None` | `None` |
| control: shipped `firecrawl`, args and every install argv pinned | `None` | `None` |

## Revision 9 (2026-09-27): board round 7 on `253445a`: every spawning argv; shipped declarations only

> **Corrected by revision 10.** The spawn-site table below is superseded by revision 10's
> (N-1: the descriptions refresh spawns the manifest argv; N-2: there is no `pmcp install`),
> and install argvs are now judged before the local-binary exemption, not only on the
> exact-pin path.

The round-7 claude seat returned DISAGREE, with two blocking findings inside the trust
boundary (B1, B2) and three non-blocking ones (N1-N3). Both blockers were **reproduced
first** against revision 8 (`repro_r9.py`, appendix).

**1. Every argv that can spawn the server is judged (B1).** I enumerated the spawn sites
from the code (`create_subprocess_exec` / `StdioServerParameters` in `src/pmcp`):

| spawn site | argv | how rev 9 treats it |
|---|---|---|
| `client/manager.py` `_connect_stdio`: connect, restart, lazy reconnect, and the respawn `gateway.refresh` triggers | `config.command` + `config.args` | judged, as before ("its args") |
| `manifest/refresher.py`: descriptions refresh (`StdioServerParameters`) | `config.command` + `config.args` | the same argv, already judged |
| `manifest/installer.py` `JobManager.start_install`, which `gateway.provision` calls; `_finalize_server_ready` **adopts that process as the live server** | `install[platform]` | **now judged**, every non-empty platform |
| `manifest/installer.py` `install_server` (one-shot install) | `install[platform]` (`wsl` falls back to `linux`) | **now judged**, by the same rule |
| `manifest/installer.py` install verify: `command args[:1] --help` | a probe of the entry's own command | not a server spawn; its command is the judged `command` |
| `tools/handlers.py` update probe: `<pkg>@latest --help` | a deliberate probe of latest, by `update_server` | not a server spawn; it is the update itself |

Install argvs are spawned only for a **manifest-sourced** server. A configured
(`.pmcp.json`/`.mcp.json`) server is lazily started by `ClientManager` from its own
`args` (`gateway.provision`: "User/project configured servers are lazy-started via
ClientManager"). So for `resolved.source == "manifest"`, `_install_argv_problem` requires
every non-empty `install[platform]` argv to pass the same rules as `args`:

- it is readable, via `_read_spawn_pin`: the same detection, the structural npx read when
  identity refuses, and `uv tool run` read as uvx;
- it names **the same package at the same pin**, the rule `provision_gate._config_runs_exactly`
  applies to approvals;
- it holds one exact version;
- it has a recognised shape;
- its env (the same `extra_env` and credential, `build_install_child_env`) is inert. Its
  cwd is pmcp's own.

The first failure is loud and names the argv, for example `its linux install argv (`npx -y
firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, does not run
firecrawl-mcp@3.25.5 (it names firecrawl-mcp@latest)`. `update_server` applies the same
check before `[PINNED]`, so a divergent install argv is `floating_selector` / `[FLOATING]`.
A `version:` pin was already all-or-nothing across `args` and `install` (the
materialiser, D3). This closes the hand-pinned `args` case.

**2. Only the shipped manifest's declarations exempt a key (B2).** Revision 8 let an
entry's own `env_var`/`api_key_optional_when` exempt a key, guarded by a namespace
**denylist**, and the seat laundered `OPENSSL_CONF` (and `TARGET_CC`, `PROTOC`) through it.
Revision 9 removes the denylist (`_TOOL_ENV_*`, `_in_tool_namespace`). A key is the
server's own only if **pmcp's shipped `manifest.yaml`** declares it for that server name
(`_shipped_manifest_declarations`, read from the packaged file directly, never through
`load_manifest`, which applies overlays; cached once per process). Declarations in an
overlay, `.pmcp.json` or `.mcp.json` are entry-controlled and exempt nothing.
`code_patterns.yaml` declares no env keys (`grep -c` = 0). The shipped manifest declares
**84 names**, all application credentials or endpoints, from `AIRTABLE_API_KEY` to
`ZAPIER_MCP_URL`, including `FIRECRAWL_API_KEY` and `FIRECRAWL_API_URL`.

**3. N1-N3.**
- **N1:** the README now says an entry's `npm_config_@<scope>:registry` for a *dependency's*
  scope is allowed, because a pin holds the top-level package only. So the entry can
  redirect a dependency scope and the warning does not report it.
- **N2:** `docker run -it` (combined inert short flags) and `npx --yes=true` are modelled.
  `docker container run`, `uvx -qq`, `cargo install -fq` and `cargo install fc@1.2.3` stay
  loud or unpinned, which fails closed. main's `detect_package_type` does not read those
  spellings (it reads `docker container run` as the image `container`, and `uvx -qq` and
  `cargo -fq` as unknown), and changing that parser is outside this plan. The README lists
  them as known false positives.
- **N3:** the README says install argvs are judged too.

**Costs.**
- **Shipped cost** (`shipped_cost.py`, now including every install argv and the
  shipped-only declarations): **revision 8, 0 of 77; revision 9, 0 of 77.** Every
  shipped pinnable entry's args and install argvs agree once pinned, and the shipped
  manifest injects no undeclared keys. Step 7: `77 pinnable, 30 refused`.
- **Operator cost** (R13, restated). An overlay or config that injects an application key
  now warns "cannot verify" unless the **shipped** entry of that name declares it. In
  particular, an **overlay-only** self-hosted server (one the shipped manifest does not
  define) always warns, because its own relaxer variable is in its env and is not
  shipped-declared (measured: `SEMVER_API_URL` → cannot verify). The shipped `firecrawl`
  with its own `FIRECRAWL_API_URL`/`FIRECRAWL_API_KEY` stays silent. That covers the
  ViperJuice/dotfiles#325 shape.

| # | finding (round 7) | resolution | evidence (rev 8 → rev 9, `repro_r9.py`) |
|---|---|---|---|
| **B1** (blocking) | `gateway.provision` spawns and adopts `install[platform]`, but the warning and `[PINNED]` judged only `args`. Hand-pinned `args` plus a copied `install.linux: ["npx","-y","firecrawl-mcp"]` was silent while latest ran. | (1): every install argv of a manifest-sourced server must run the same exact pin in a recognised shape with an inert env, in the warning and in `update_server`. | `args pinned, linux install unpinned`: `None` → `... its linux install argv (`npx -y firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, does not run ...`. `install via sh -c`: `None` → `... runs something pmcp cannot read`. Tests `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable` (4) and `test_update_server_never_labels_a_divergent_install_argv_pinned`. Mutants M97-M101. |
| **B2** (blocking) | The declared-key guard was a namespace denylist. An overlay declaring `OPENSSL_CONF` as `env_var` or relaxer made npx silent while node loaded an entry-chosen `.so`; `TARGET_CC`/`PROTOC` did the same for cargo. | (2): only the shipped manifest's declarations exempt a key. No namespace list remains. | Overlay `env_var: OPENSSL_CONF`, overlay relaxer `OPENSSL_CONF`, and cargo `TARGET_CC`: `None` → "cannot verify: the entry's env sets ...". Tests `test_an_overlay_declaration_exempts_no_key` (3); control `test_the_shipped_firecrawl_declarations_keep_a_self_hosted_pin_silent` (real shipped declarations). Mutant M96 (an overlay declaration exempts a key). |
| **N1** | A dependency-scope `@scope:registry` in the entry's env is allowed. | Documented (README, Non-goals): a pin holds the top-level package. | No change in behaviour. |
| **N2** | Benign spellings warned. | `-it` and `--yes=true` are modelled. The rest are documented as known false positives, because of main's parser. | `docker run -it`, `npx --yes=true`: "cannot verify" → `None`. Test `test_common_inert_spellings_are_recognised` (2). Mutants M102, M103. |
| **N3** | The README did not mention install argvs. | README updated. | Documentation. |

**Tests:** the revision-9 file has **230 tests** (219 in revision 8). Against the
revision-8 code it gives **11 failed, 219 passed**. The 11 are the 4 install-argv ids,
the `update_server` install test, the 3 overlay-declaration ids, the 2 N2 spellings, and
the allowlist unit test, which now asserts shipped-only declarations. The handler tests'
entries stand for shipped entries through one autouse fixture
(`_test_entries_are_shipped`, which extends the shipped declaration table). The fixture
uses `getattr(..., raising=False)`, so a run on revision 8 measures behaviour instead of
erroring. The overlay-only tests use a name outside that table.

## Rev 8 board findings — before/after, measured

`repro_r9.py` (appendix), one fresh process per row: a manifest-sourced entry, npm
identity read with the node-less tables, and the self-hosted relaxer set. Trees:
`876fd33` + the revision-8 patch (`253445a`), and `876fd33` + the revision-9 patch.

| case | rev 8 | rev 9 |
|---|---|---|
| B1 `args: -y firecrawl-mcp@3.25.5`, `install.linux: npx -y firecrawl-mcp` | `None` | cannot verify: `its linux install argv (`npx -y firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, does not run firecrawl-mcp@3.25.5 (it names firecrawl-mcp@latest)` |
| B1 `install.linux: sh -c "npx -y firecrawl-mcp"` | `None` | cannot verify: `its linux install argv (...) runs something pmcp cannot read` |
| B2 overlay-only entry, `env_var: OPENSSL_CONF` | `None` | cannot verify (`the entry's env sets FIRECRAWL_API_URL`, the first undeclared key; `OPENSSL_CONF` is undeclared too) |
| B2 overlay-only entry, relaxer `OPENSSL_CONF` | `None` | cannot verify: `the entry's env sets OPENSSL_CONF` |
| B2 overlay-only cargo entry, `env_var: TARGET_CC` | `None` | cannot verify (the entry's env holds undeclared keys) |
| N2 `docker run -it --rm example/client@sha256:...` | cannot verify (`its argv passes -it`) | `None` |
| N2 `npx --yes=true firecrawl-mcp@3.25.5` | cannot verify (`its argv passes --yes`) | `None` |
| cost: overlay-only self-hosted entry with an app relaxer `SEMVER_API_URL` | `None` | cannot verify: `the entry's env sets SEMVER_API_URL` (R13) |
| control: shipped `firecrawl`, args and every install argv pinned | `None` | `None` |

## Revision 8 (2026-09-27): board round 6 on `15dae94`: allowlists, not denylists

> **Superseded in part by revision 9.** The declared-key rule below (an entry's own
> declarations, guarded by a namespace list) is replaced by shipped-manifest declarations
> only, and install argvs are judged too. The shape and env allowlists stand.

The round-6 claude seat returned DISAGREE, with two blocking findings inside the trust
boundary (X1, X2) and four non-blocking ones (N1-N4). Each was **reproduced first**
against revision 7 (`repro_r8.py`, appendix). All six share **one root cause**: both
entry rules in revision 7 were **denylists**, and a denylist fails open on every spelling
it does not list. Revision 7 listed "keys that redirect" (`XDG_CONFIG_HOME`, not
`XDG_CONFIG_DIRS`; not `LD_PRELOAD` or `CC`) and "flags that redirect" (not a container
command after the image, not `npx -p X sh`, not `uvx --from X sh`). Revision 8 **inverts
both rules**:

1. **ENV is an allowlist.** Every key in the entry's env block (`config.env`, including an
   overlay's `server_env`) must be **proven inert** for the launcher. Otherwise the exact
   pin is "cannot verify". A key is inert only if it is one of:
   - **one of the server's own declared keys**: the entry's `env_var` and its
     `api_key_optional_when` relaxers. The MCP server reads these; the launcher and its
     package manager never do. A declared name that falls in a tool namespace (`NPM_`,
     `NODE_`, `UV_`, `PIP_`, `PYTHON*`, `CARGO_`, `RUSTUP_`, `RUSTC*`, `DOCKER_`, `XDG_`,
     `LD_`, `DYLD_`, `SSL_`, `GIT_`, `PATH`, `HOME`, `CC`, the proxies ...) is **not**
     treated as the server's own, so an overlay cannot launder `NPM_CONFIG_REGISTRY`
     through `env_var:`. That guard only narrows the allowlist; it is not the rule;
   - **locale/terminal/colour**: `LANG`, `LANGUAGE`, `LC_*`, `TERM`, `TZ`, `NO_COLOR`,
     `FORCE_COLOR`;
   - **per launcher, the short logging/timing/credential lists** kept from revision 7:
     npm's 17 `npm_config_*` keys, `//host/:` credentials and another scope's
     `@scope:registry`; uv's `UV_NO_PROGRESS`, `UV_HTTP_TIMEOUT`, `UV_HTTP_RETRIES` and
     `UV_INDEX_<N>_USERNAME/PASSWORD`; cargo's 8 `CARGO_TERM_*`/timing keys and its
     registry tokens. docker has none.

   Everything else is not inert: an unknown key, `XDG_*`, `LD_*`, `CC`, a proxy, a CA file.
   **Values are not special-cased.** An empty value of a key that is not inert is still
   not inert. That closes N1: an empty `PATH` made exec search the cwd.
2. **ARGV is an allowlist of SHAPES.** The exact pin is silent only when the argv
   matches a recognised shape in which **the pinned package is what runs**:

   | launcher | recognised shape | inert flags | not a shape (loud) |
   |---|---|---|---|
   | npx | `npx [flags] <pkg>@<exact> [arguments to the package]` | `-y`, `--yes`, `-q`, `--quiet` | any other flag, incl. `-p`/`--package X <cmd>` |
   | `npm exec` / `npm x` | `npm exec [flags] <pkg>@<exact> [args]` | as npx | any other subcommand |
   | uvx, and `uv tool run` (judged as uvx) | `uvx [flags] <req==exact> [args]` or `uvx [flags] --from <req==exact> <req's own name> [args]` | `-q`, `--quiet`, `-v`, `--verbose`, `--no-progress`, `--isolated`, `--refresh`, `--no-cache`, `-n`, `--color <v>` | `--from X <other command>` (e.g. `sh -c`); `--python` (as `UV_PYTHON`, N3); any index/with/overrides/config flag |
   | cargo | `cargo install [flags] <crate> --version <exact> [flags]` | `--locked`, `-q`, `-v`, `-f`, `--force`, `--color <v>`, `-j/--jobs <n>` | `+toolchain` (as `RUSTUP_TOOLCHAIN`, N3); any source flag (`--git`, `--registry`, `--path` ...) |
   | docker | `docker run [flags] [-e KEY[=V]]* <image>@<digest>` and **nothing after the image** | `-i`, `-t`, `--rm`, `--init`, `--pull <v>`, `--name <v>`, `--network <v>`, `--platform <v>`; `-e KEY` only when KEY is inert by rule 1 (docker's family: declared/locale keys only) | a container command after the image; `--entrypoint`; `-v`/`--volume`/`--mount`; `--env-file`; `-e NODE_OPTIONS=...`; any other flag |

   Arguments **after** the package (npx, uvx) are the package's own. An unrecognised
   flag or shape is loud. The same shape check drives `update_server`: an exact selector
   in an unrecognised shape is `floating_selector`/`[FLOATING]`, never `[PINNED]`.
3. **Launchers.** `uv tool run` is judged as uvx. Package runners and wrappers pmcp does
   not model are "cannot verify", pinned or not: `bunx`, `bun`, `pnpx`, `pnpm` (`dlx`),
   `yarn` (`dlx`), `uv` (other than `tool run`), `deno`, `node <script>`, the shells
   (`sh`, `bash`, `zsh`, `dash`, `ksh`, `fish`, `cmd`, `powershell`, `pwsh`) and `env`.
   Any **other** command, such as a locally installed server binary, is the host's and is
   not judged. The README says so. *(Superseded: rev 10 exempts such a command only when it
   is the whole spawn set, and rev 11 still judges its env and cwd.)*
4. **What a docker digest pins.** It pins the **image**, not what runs in it. The
   container command, entrypoint, mounts and env decide that, which is why the shape
   forbids them (D7, README).

| # | finding (round 6) | resolution | evidence (rev 7 → rev 8, `repro_r8.py`) |
|---|---|---|---|
| **X1** (blocking) | The argv's command slot could run something other than the pinned package: `docker run ... node@sha256:... npx -y semver` ran the latest semver; also `--entrypoint`, `-v`, `-e NODE_OPTIONS=`; `npx -y -p semver@7.6.0 sh -c ...` and `uvx --from cowsay==6.1 sh -c ...` ran `sh`. All were silent and `[PINNED]`. | Shape allowlist (2), and the same check in `update_server`. | Each case: `None` → "cannot verify" naming the problem (`its argv passes a container command after the image ('npx')`, `its argv passes --entrypoint`, `its argv passes -v`, `its argv sets container env NODE_OPTIONS`, `its argv passes -p`, `its argv runs 'sh' from the --from environment ...`). `update_server` for the docker case: `floating_selector=<digest>`, `[FLOATING]`. Tests `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable` (9 ids) and `test_update_server_never_labels_a_container_command_pinned`. Mutants M82, M84-M90, M93, M94. |
| **X2** (blocking) | An entry `XDG_CONFIG_DIRS` redirected uv to an impostor wheel through a system `uv.toml`. `XDG_CACHE_HOME` was silent while `UV_CACHE_DIR` was loud. | Env allowlist (1): no `XDG_*` key is inert. | `XDG_CONFIG_DIRS`, `XDG_CACHE_HOME`: `None` → `the entry's env sets XDG_...`. Test `test_an_entry_key_not_proven_inert_is_unverifiable` (5 ids, incl. an unknown key). Mutants M75, M95. |
| **N1** | Empty values were skipped for every key, but only npm skips them, so `PATH=""` made exec search the cwd. | Values are not special-cased (1). | `PATH=""`: `None` → `the entry's env sets PATH`. Test id `empty-path`. |
| **N2** | The README said "the launcher", but `uv tool run`, `bunx`, `pnpx`, `pnpm dlx`, `sh -c`, `env` and `node npx-cli.js` were silent even unpinned. | (3): `uv tool run` is judged as uvx, and the unmodelled runners/wrappers are loud. The README names the judged launchers and says other commands are not judged. | `uv tool run cowsay`: `None` → `is unpinned`; `bunx`, `pnpm dlx`, `sh -c`: `None` → `a package runner or wrapper pmcp does not model`. Tests `test_an_unmodelled_runner_or_wrapper_is_unverifiable` (7), `test_uv_tool_run_is_judged_as_uvx`. Mutants M91, M92. |
| **N3** | `uvx --python` was inert while `UV_PYTHON` was loud; `cargo +nightly` was silent (identified as `'+nightly'`) while `RUSTUP_TOOLCHAIN` was loud. | `--python` is not an inert uvx flag, and a `+toolchain` is not a cargo shape. | Both: `None` → "cannot verify" (`its argv passes --python`; `its argv selects toolchain +nightly`). The cargo package name is still read as `+nightly` by main's `detect_package_type`; the message names the toolchain. Mutant M87. |
| **N4** | `LD_PRELOAD`, `CC`, `CFLAGS` were silent outside the family prefixes. | The env allowlist (1) makes every such key loud. | `LD_PRELOAD` (npx), `CC` (cargo): `None` → "cannot verify". Controls stay silent: the firecrawl self-hosted config with its declared `FIRECRAWL_API_URL`/`FIRECRAWL_API_KEY`, a docker digest with `-e FIRECRAWL_API_URL`, and `uvx --from cowsay==6.1 cowsay`. Mutants M77, M78 (the declared and locale keys must stay inert), M76 (the namespace guard). |

**Shipped-coverage cost (measured, `shipped_cost.py`).** For every shipped entry a
`version:` pin can reach (step 7's 77), the pin was applied at `1.0.0`, the config the
gateway would spawn was built (`manifest_server_to_config`), and each revision was asked
whether that exact pin is silent. Result: **revision 7, 0 of 77 not silent; revision 8,
0 of 77 not silent.** The one shipped relaxer entry (`firecrawl`) is silent on both, with
its self-hosted `FIRECRAWL_API_URL` declared. So the allowlists cost nothing in shipped
coverage. Their cost falls on operator configs that inject **undeclared** keys: for
example, a `.pmcp.json` that sets `FIRECRAWL_RETRY_MAX_ATTEMPTS` next to the pin now warns
"cannot verify" (R13). Step 7 is unchanged at `77 pinnable, 30 refused`.

**Tests:** the revision-8 file has **219 tests** (190 in revision 7). Against the
revision-7 code it gives **24 failed, 195 passed**:

- the 9 unrecognised-shape ids;
- the 5 not-inert-key ids;
- the 7 runner/wrapper ids;
- the allowlist unit test;
- the `update_server` `[FLOATING]` test;
- the `uv tool run` test.

Three revision-7 controls changed their argv or env to stay inside the new shapes: the uvx
control drops `--python`, the uvx cwd case uses the `--from` package's own name, and the
npm control swaps an empty `npm_config_package` for the declared `SELFHOST_API_KEY=""` and
`LANG`. The new controls (`test_a_recognised_shape_with_an_exact_pin_is_silent`, 6 ids)
are green on both revisions.

**What stays as revision 7:** the pin grammar and materialiser (`loader.py`, so the
corpus is unaffected; re-run anyway), per-launcher exactness and the PEP 508 check, the
trust boundary (host state is trusted), the cwd ruling, and the NB-1 cause.

## Rev 7 board findings — before/after, measured

`repro_r8.py` (appendix), one fresh process per row, npm identity read with the node-less
tables. Trees: `876fd33` + the revision-7 patch (`15dae94`), and `876fd33` + the
revision-8 patch. The self-hosted relaxer (`FIRECRAWL_API_URL`) is set in the entry's env.

| case | rev 7 | rev 8 |
|---|---|---|
| X1 `docker run --rm --network host node@sha256:... npx -y semver --help` | `None` | cannot verify: `its argv passes a container command after the image ('npx') ...` |
| X1 `docker run --entrypoint /bin/sh node@sha256:... -c "npx -y firecrawl-mcp"` | `None` | cannot verify: `its argv passes --entrypoint` |
| X1 `docker run -v /srv/evil:/app node@sha256:...` | `None` | cannot verify: `its argv passes -v` |
| X1 `docker run -e NODE_OPTIONS=--require=/x node@sha256:...` | `None` | cannot verify: `its argv sets container env NODE_OPTIONS` |
| X1 `npx -y -p semver@7.6.0 sh -c ...` | `None` | cannot verify: `its argv passes -p` |
| X1 `uvx --from cowsay==6.1 sh -c ...` | `None` | cannot verify: `its argv runs 'sh' from the --from environment, not the 'cowsay' package's own command` |
| X2 uvx, entry `XDG_CONFIG_DIRS` | `None` | cannot verify: `the entry's env sets XDG_CONFIG_DIRS` |
| X2 uvx, entry `XDG_CACHE_HOME` | `None` | cannot verify: `the entry's env sets XDG_CACHE_HOME` |
| N1 npx, entry `PATH=""` | `None` | cannot verify: `the entry's env sets PATH` |
| N2 `uv tool run cowsay` (unpinned) | `None` | `... is unpinned ...` |
| N2 `bunx semver@7.6.0` / `pnpm dlx semver@7.6.0` / `sh -c "npx -y semver@7.6.0"` | `None` | cannot verify: `it launches through 'bunx'` / `'pnpm'` / `'sh'`, `a package runner or wrapper pmcp does not model` |
| N3 `uvx --python /srv/python cowsay==6.1` | `None` | cannot verify: `its argv passes --python` |
| N3 `cargo +nightly install fc --version 1.2.3` | `None` | cannot verify: `its argv selects toolchain +nightly` |
| N4 npx, entry `LD_PRELOAD` | `None` | cannot verify: `the entry's env sets LD_PRELOAD` |
| N4 cargo, entry `CC` | `None` | cannot verify: `the entry's env sets CC` |
| control: npx `firecrawl-mcp@3.25.5`, entry `FIRECRAWL_API_URL` + `FIRECRAWL_API_KEY` | `None` | `None` |
| control: `docker run -i --rm -e FIRECRAWL_API_URL node@sha256:...` | `None` | `None` |
| control: `uvx --from cowsay==6.1 cowsay` | `None` | `None` |

## Revision 7 (2026-09-27): board round 5 on `cfedeca`, and the trust boundary

> **Superseded in part by revision 8.** Revision 7's entry rules were denylists (keys and
> flags that redirect). Revision 8 inverts both into allowlists (env keys proven inert;
> argv shapes in which the pinned package runs). The trust boundary, the cwd ruling and
> the exactness fixes below stand.

The round-5 claude seat returned DISAGREE, with two blocking findings (B1, B2) and five
non-blocking ones (N1-N5). Each was **reproduced first** against revision 6. The
maintainer then set the scope for this revision:

> **Trust the host.** The host's own npm/uv/cargo configuration is the operator's trusted
> environment, like `PATH` already is: npmrc files at every level, the operator's shell
> environment, shims, caches, global bins, and proxy/CA settings. The warning fails loud
> only on what a manifest entry or overlay controls: its argv (pin grammar, per-launcher
> exactness), the env block it injects into the child, and its launcher.

Revision 7 implements that boundary. It cuts revision 6's host discovery (npmrc walking,
the local-prefix search, the host-environment scan) back to **the entry's own env block**
(`config.env`, which carries an overlay's `server_env`). It applies the same entry rule to
uvx and cargo, and states the boundary as a non-goal here and in the README text
(Documentation impact). Tests that are red on revision 6 exist wherever behaviour changes.
Mutants M57-M71 cover the new rules. The rev-6 host-discovery mutants (M41, M42, M45-M50,
M54-M56) are retired with the code they mutated. M10 and M18 are re-targeted at
`_argv_pin_is_exact`.

| # | finding (round 5) | resolution | evidence |
|---|---|---|---|
| **B1** (blocking) | Revision 6 located the builtin and global npmrc through `realpath(which(npx/node))`. A wrapper shim (asdf, Volta, mise, a two-line `exec` script) sent that lookup to the wrong place, so a redirecting npmrc was missed while npx ran the impostor. | **Out of scope by the trust boundary.** npmrc files at every level, shims and the host's node/npm install are the operator's. The whole discovery (`_npm_config_files`, `_npmrc_redirecting_key`, the `_has_local_prefix` walk, the host-env scan) is **removed**, so no fail-open lookup remains to be fooled. The non-goal names shims and npmrc explicitly. | Rev 6 was silent in the seat's shim rows. Rev 7 is silent in **every** host-npmrc/local-project case, by design: `test_the_hosts_npm_configuration_files_are_trusted` (user, cwd, local prefix, global, builtin × 2 identity modes) is **10 red on rev 6**, which warned there. |
| **B2** (blocking) | An exact non-npm pin had no context check: `uvx --from cowsay==6.1` ran 6.0 under `UV_OVERRIDE`, `UV_INDEX_URL` redirected the index, and `cargo install --git ... --version 1.2.3` was `exact True`, all silent. | **In scope for what the entry sets.** One entry rule for every launcher (`_entry_redirect`). (1) The **entry's env block**: `PATH`, `HOME`, `XDG_CONFIG_HOME`, the proxy variables (any case) and `SSL_CERT_FILE`/`SSL_CERT_DIR`/`NODE_EXTRA_CA_CERTS` for all launchers, plus each family's own keys. npm: `npm_config_*`, `nvm_*`, `NODE_OPTIONS`, `NODE_PATH`, `PREFIX`, `DESTDIR`. pypi: `UV_*`, `PIP_*`, `PYTHONPATH`, `PYTHONHOME`. cargo: `CARGO_*`, `RUSTUP_*`, `RUSTC`, `RUSTC_WRAPPER`, `RUSTC_WORKSPACE_WRAPPER`, `RUSTFLAGS`, `RUSTDOCFLAGS`. docker: `DOCKER_*`. Each gives "cannot verify" unless it is on a small allowlist (below). (2) **argv flags** for uvx (up to the command) and cargo (anywhere): any flag outside a small allowlist (output, the interpreter, `--from`, `--version`, `--locked`, `--force`, `--jobs`) is loud, e.g. `--index-url`, `--with`, `--overrides`, `--git`, `--registry`. A uvx/cargo `UV_*`/`CARGO_*` setting the **host** exports is trusted, like any host setting. | Rev 6 → rev 7 (measured, `repro_r7.py`): entry `UV_OVERRIDE`, entry `UV_INDEX_URL`, argv `--index-url`, cargo argv `--git`, and entry `CARGO_REGISTRIES_X_INDEX`: **`None` → "cannot verify ... names the setting"**. Host `UV_OVERRIDE`: `None` → `None` (trusted). Tests: `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv` (6) and `..._cargo_..._redirects_cargo` (4), **all 10 red on rev 6**. Controls `..._is_silent_without_an_entry_redirect` (uvx, cargo) are green on both. Mutants M57, M59, M60, M62-M64, M70, M71. |
| **N1** | The rev-6 comment called a relocated `cache` "the same content-addressed store"; libnpmexec runs an existing `_npx/<hash>/node_modules/<pkg>` on its `package.json`'s word. | Comment corrected. The **host's** cache is trusted (the non-goal names the npx cache). An **entry-injected** `npm_config_cache` is loud, because the entry chose a directory whose contents can change what runs. | Rev 6 `None` → rev 7 "cannot verify ... the entry's env sets npm_config_cache". Test id `entry-cache` (×2), red on rev 6. |
| **N2** | A file named like the slot in the global bin runs before any registry lookup. | **Out of scope**: the global bin is the host's (named in the non-goal). | Documentation only. |
| **N3** | The proxy/CA environment spellings were not held to the key rule. | **In the entry's env**: `HTTP(S)_PROXY`, `ALL_PROXY`, `NO_PROXY`, `PROXY` (any case), `SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS` are loud for every launcher. **In the host's env**: trusted. `NODE_TLS_REJECT_UNAUTHORIZED` is not listed: npm passes its `strict-ssl` default, so it is not read (the seat's own finding). | Rev 6 `None` → rev 7 "cannot verify" for entry `HTTPS_PROXY`; host `HTTPS_PROXY` `None` on both. Test ids `entry-https-proxy`, `entry-https-proxy-lower`, `entry-extra-ca` (×2), red on rev 6. Mutant M59. |
| **N4** | A pin holds only the top-level package; the dependency closure resolves fresh. | Wording: `_npm_key_can_redirect` is documented as judged for the **top-level** package, and the warning says "hold the top-level package at the pinned version". An entry's `@other:registry` for another scope stays quiet. The non-goal says a pin does not hold dependencies. | Control `test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent`. Mutant M51 is kept. |
| **N5** | Exactness nits: a uvx URL requirement read `==` from its fragment; PyPI `==1.0` versus a local `+x`; an upper-case docker digest was labelled a tag; an unreadable docker/uvx argv was silent. | (a) `_uvx_requirement_is_exact` parses PEP 508 with `packaging`: a URL requirement is **never exact**, and exact means exactly one `==` without a wildcard. `_argv_pin_is_exact` applies it in both the warning and the `[PINNED]` report. (b) `==1.0` is kept exact: a public index must not host local versions (PEP 440), and a non-default index or find-links reaches uvx only through host config (trusted) or the entry's env/argv (now loud). (c) A digest-shaped but malformed pin is labelled `a malformed content digest ...`. (d) A launcher whose argv pmcp reads (`uvx`, `pip`, `pip3`, `cargo`, `docker`, any spelling) with an argv it cannot read (an unknown flag, a non-bare spelling) fails loud, as the npm family does since round 3 N-b. | Rev 6 → rev 7: the URL requirement `None` → `floats on '1.0.0' (not one exact PEP 440 version from an index)`; the upper-case digest `(a docker tag, ...)` → `(a malformed content digest ...)`; `docker run --some-future-flag img:3.25.5` and `uvx --some-future-flag cowsay==6.1`: `None` → "cannot verify: it cannot read which package this ... argv runs". Tests `test_a_uvx_url_requirement_is_never_an_exact_pin`, `test_docker_digest_labels_and_the_entrys_docker_env`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher` (3): **5 red on rev 6**. Mutants M66, M67, M69. |
| **cwd** (coordinator ruling on revision 7) | Revision 7 had treated every cwd as the host's. But an entry or overlay that **sets** `cwd` chooses which directory's project configuration the launcher reads. | **An entry-set `cwd` is entry-controlled.** For launchers whose resolution reads cwd-relative configuration, an exact pin with an entry-set `cwd` is "cannot verify": `the entry sets cwd '<dir>', whose project configuration <npm\|uv\|cargo> reads`. That covers npm/npx (a `.npmrc` at the local prefix, found by walking up for `package.json`/`node_modules`, and a local bin there), uvx (`uv.toml` / `pyproject.toml` `[tool.uv]` up the tree) and cargo (`.cargo/config.toml` in the cwd and every parent). pmcp does not read the directory: that the entry chose it is enough. **docker is exempt.** `docker run` reads no cwd-relative configuration (its client config is `~/.docker` or `DOCKER_CONFIG`, which the env rule covers), and a digest is content-addressed. **With no entry-set cwd** the child inherits pmcp's own cwd, which stays the host's. Only a configured entry (`.mcp.json` / `.pmcp.json` / project config) can set `cwd`. The manifest's `ServerConfig` has no `cwd` field, and the shipped manifests set none: `grep -cE '^\s*cwd\s*:'` gives **0** for `manifest.yaml` and 0 for `code_patterns.yaml`. So shipped coverage is unchanged (step 7: `77 pinnable, 30 refused`). | `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable` (npx, uvx, cargo) is **3 red on the first rev-7 cut** (`1073923`). The control `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts` is green on both. Mutants M72 (entry cwd ignored), M73 (docker counted cwd-sensitive), M74 (the inherited host cwd judged too). |

**The small allowlists** (entry env; everything else in a family's namespace is loud):

- **npm** (`npm_config_*`, judged after npm's own `loadEnv` normalisation): 17 keys. The
  output/logging keys `loglevel`, `color`, `progress`, `timing`, `unicode`, `logs-dir`,
  `logs-max`, `fund`, `audit`, `update-notifier`; the install prompt `yes`; the network
  timing keys `fetch-retries`, `fetch-retry-factor`, `fetch-retry-maxtimeout`,
  `fetch-retry-mintimeout`, `fetch-timeout`, `maxsockets`. Also allowed: registry
  credentials (`//host/:...`), and `@scope:registry` for a scope other than the pinned
  package's. An empty value is skipped, as npm skips it. The rev-6 93-key allowlist shrinks
  to this, because an entry has no reason to inject anything else. `cache` and `store-dir`
  are no longer on it: dev0 sets them in the **host** environment, which is now trusted
  rather than allowlisted.
- **uv** (`UV_*`): `UV_NO_PROGRESS`, `UV_HTTP_TIMEOUT`, `UV_HTTP_RETRIES` (names checked
  against the uv 0.12.19 binary), and index credentials `UV_INDEX_<NAME>_USERNAME` /
  `_PASSWORD`.
- **cargo** (`CARGO_*`): `CARGO_TERM_COLOR`, `CARGO_TERM_QUIET`, `CARGO_TERM_VERBOSE`,
  `CARGO_TERM_PROGRESS_WHEN`, `CARGO_TERM_PROGRESS_WIDTH`, `CARGO_HTTP_TIMEOUT`,
  `CARGO_NET_RETRY`, `CARGO_BUILD_JOBS`, and the credentials `CARGO_REGISTRY_TOKEN` /
  `CARGO_REGISTRIES_<NAME>_TOKEN`.
- **docker** (`DOCKER_*`): none. Which daemon or config the CLI uses is loud even with a
  digest.

**What stays exactly as revision 6:** the pin grammar and materialiser (`loader.py`, and
so the conformance corpus), per-launcher `_is_exact_pin`, the floating texts (with the
malformed-digest addition), the NB-1 launcher-keyed cause, and the structural npx read
with identity disabled.

**Scope growth.** The code shrinks on the npm side: the npmrc and local-prefix discovery
is removed. It grows by the per-family entry rule, the uvx/cargo flag allowlists, and the
PEP 508 check. Still four source files.

## Rev 6 board findings — before/after, measured

Measured with `repro_r7.py` (appendix), one fresh process per row, against `959d4d4` plus the
revision-6 patch and `959d4d4` plus the revision-7 patch. It runs the real warning
function with a self-hosted relaxer set. "Entry" means the resolved config's env block;
"host" means the gateway's own environment.

| case | rev 6 | rev 7 |
|---|---|---|
| npx `firecrawl-mcp@3.25.5`, **entry** `npm_config_package=file:...` (codex C1 via the entry) | cannot verify | cannot verify: `the entry's env sets npm_config_package` |
| same, **host** `npm_config_package=file:...` | cannot verify | `None`: **documented out of scope** (host is trusted) |
| **entry** `npm_config_cache=/srv/shared` (N1) | `None` | cannot verify: `the entry's env sets npm_config_cache` |
| **entry** `HTTPS_PROXY` (N3) | `None` | cannot verify: `the entry's env sets HTTPS_PROXY` |
| **host** `HTTPS_PROXY` (N3) | `None` | `None` (trusted) |
| uvx `--from cowsay==6.1 cowsay`, **entry** `UV_OVERRIDE` (B2) | `None` | cannot verify: `the entry's env sets UV_OVERRIDE` |
| same, **host** `UV_OVERRIDE` (B2) | `None` | `None` (trusted) |
| uvx `cowsay==6.1`, **entry** `UV_INDEX_URL` (B2) | `None` | cannot verify: `the entry's env sets UV_INDEX_URL` |
| uvx `--index-url http://evil.test/simple cowsay==6.1` (B2) | `None` | cannot verify: `its argv passes --index-url` |
| `cargo install --git https://example.test/evil --version 1.2.3 fc` (B2) | `None` | cannot verify: `its argv passes --git` |
| cargo, **entry** `CARGO_REGISTRIES_X_INDEX` (B2) | `None` | cannot verify: `the entry's env sets CARGO_REGISTRIES_X_INDEX` |
| uvx `--from "firecrawl-py @ https://.../firecrawl_py-9.9.9-py3-none-any.whl#x==1.0.0" fc` (N5) | `None` | `floats on '1.0.0' (not one exact PEP 440 version from an index)` |
| `docker run example/client@sha256:<64 upper-case hex>` (N5) | `floats ... (a docker tag, ...)` | `floats ... (a malformed content digest: docker digests are lower-case hex of the algorithm's length)` |
| `docker run --some-future-flag example/client:3.25.5` (N5) | `None` | cannot verify: `it cannot read which package this 'docker' argv runs` |
| `uvx --some-future-flag cowsay==6.1` (N5) | `None` | cannot verify: `it cannot read which package this 'uvx' argv runs` |
| npx `firecrawl-mcp@3.25.5`, nothing set (control) | `None` | `None` |
| uvx `--from cowsay==6.1 cowsay`, nothing set (control) | `None` | `None` |

The seat's shim rows (B1) and the global-bin file (N2) involve host state only, so
revision 7 is silent there **by design**. The README's trust-boundary paragraph tells the
operator that these are theirs to control.

**Tests:** the revision-7 file has **190 tests** (153 in revision 6; the first rev-7 cut `1073923` had 186, and the cwd ruling adds 4: 3 red on `1073923` plus a control). The revision-6 C1
tests that read host state were replaced: `..._when_the_environment_redirects_npm` and
`..._when_the_server_env_redirects_npm` became `..._when_the_entry_env_redirects_npm` (11
keys × 2 identity modes) and `test_the_host_environment_is_trusted` (5 × 2).
`..._under_a_redirecting_npmrc` became `test_the_hosts_npm_configuration_files_are_trusted`
(5 × 2). The allowlist unit test became `test_which_entry_env_keys_can_redirect`. Against
the revision-6 code the file gives **54 failed, 132 passed**:

- 40 are behaviour changes:
  - host settings now trusted: 6 + 10;
  - the entry's `npm_config_cache`, `HTTPS_PROXY`, `https_proxy` and
    `NODE_EXTRA_CA_CERTS`: 8;
  - uvx entry redirects: 6;
  - cargo entry redirects: 4;
  - the URL requirement: 1;
  - the docker digest label and entry `DOCKER_HOST`: 1;
  - unreadable docker/uvx argvs: 3;
  - the new unit test, which imports rev-7 names: 1.
- 14 are the other entry-env npm ids, which rev 6 also warned on but with the rev-6
  message text (`... is set in the client's environment`).

Against revision 7: **186 passed**.

## Revision 6 (2026-09-27): board round 4 on `6b67f1b`

> **Superseded in part by revision 7.** Revision 7 cuts the host-environment discovery below
> (npmrc files, the local-prefix walk, the host-environment scan) back to the entry's own env
> block, by the maintainer's trust-boundary decision. The C2, NB-1, NB-2 and NB-3
> resolutions stand.

The claude seat returned AGREE with three non-blocking items (NB-1 to NB-3). The codex
seat found two blocking defects (C1, C2). Each was **reproduced first** on the
revision-5 spike, then fixed. Every fix has tests that are red on revision 5 and green on
revision 6, and mutants (M41-M56, plus M10/M17/M40 re-targeted at the new
`_is_exact_pin`). The before/after measurements are in the next section.

| # | finding | resolution | evidence |
|---|---|---|---|
| **C1** (blocking) | An exact-looking argv was taken as proof of a pin while npm's configuration could change what that spec runs. With `npm_config_package=file:<dir>` in the client's environment, `_unpinned_self_hosted_warning` read `firecrawl-mcp@3.25.5` structurally and returned `None`, although npx ran the local package. | **The class, not the instance.** Suppression on an exact npm pin now also requires that **nothing besides the argv can change what npm runs for that spec** (`_npm_redirecting_context`). It is computed from the inputs the warning already holds, never from the resolver's refusal text: the same context produced three different resolver status strings in the repro, depending only on whether an earlier lookup had started the child. It runs in **both** identity modes. Identity (#195) names the package an argv states, judging the server's env overlay, its cwd's local prefix and the gateway's env. The warning asks whether the client that runs is held at that version, so it reads every source npm loads configuration from (Research: *What can change what a registry spec runs*): the overlay keys identity selects (`PATH`/`HOME`/`NODE_PATH`/`NODE_OPTIONS`/`PREFIX`/`nvm_*`), `NODE_OPTIONS` and every non-empty `npm_config_*` in the child's environment, a local prefix, and the project, user, global and builtin npmrc files. An `npm_config_*` key or npmrc key is harmless only if it is on an **allowlist derived key by key from the config definitions of npm 10.9.9 and 11.19.0** (90 defined keys, plus three named undefined ones: pnpm's `store-dir`, and `email`/`always-auth`). The other 91 defined keys, and any key a later npm adds, fail loud. `//host/:` credential keys are harmless; `@scope:registry` is loud only for a package in that scope. The warning names the source: `pmcp cannot verify that its client is pinned: the argv pins firecrawl-mcp@3.25.5, but npm_config_package is set in the client's environment, which can change what npm runs for that spec.` | **Reproduced** (real resolver, real npx, offline temp cache): `npm_config_package=file:<dir> npx -y firecrawl-mcp@3.25.5` printed `LOCAL firecrawl-mcp 0.0.1`, and so did `package=file:<dir>` in `$HOME/.npmrc` and in `<cwd>/.npmrc`. Rev 5 returned `None` for all 9 redirecting contexts measured (overlay `npm_config_package`, overlay `NPM_CONFIG_REGISTRY`, gateway `npm_config_package`, a local prefix, `$HOME/.npmrc`, `<cwd>/.npmrc`, each with the resolver not started, active, or DISABLED). Rev 6 warns "cannot verify" for all of them and names the source. dev0's own `npm_config_cache` + `npm_config_store_dir` stay **silent** on both. Tests: `test_an_exact_argv_is_not_called_pinned_when_the_environment_redirects_npm` (6 keys × 2 identity modes), `..._when_the_server_env_redirects_npm` (2 × 2), `..._under_a_redirecting_npmrc` (5 sources × 2: user, cwd, local prefix, global, builtin), `test_which_npm_config_keys_can_redirect_a_registry_spec`: **27 red on rev 5**. The control `test_settings_that_cannot_redirect_npm_keep_an_exact_pin_silent` (×2) is green on both. Mutants M41, M42, M45-M51, M54-M56. **Revision 7 narrows this:** the redirect is covered when it comes from the **entry's or overlay's env block** (still "cannot verify"). A **host-level** `npm_config_package` (the gateway's own shell environment), host npmrc files and a local project are now **documented out of scope**. Rationale: the maintainer's trust-boundary decision (the host's npm configuration is the operator's, like `PATH`), and round 5 B1, which showed that host discovery through `which`/`realpath` fails open under version-manager shims. The warning fails loud on what an entry controls and does not claim to audit the host. |
| **C2** (blocking) | `_is_exact_pin` ran the generic SemVer check before the docker branch, so `docker run --pull=always example/client:3.25.5` counted as pinned (no warning, and `[PINNED]`). A docker tag can be re-pointed at another image. | `_is_exact_pin` is now a **per-launcher dispatch with no shared rule**, and anything unhandled is not exact. npm: `is_valid_package_version` (build metadata stays exact). docker: a content digest only (`sha256:` + 64 hex, or sha384/sha512), so every tag floats however version-like. pypi (uvx `pkg==X`): one PEP 440 version with no wildcard (`===X` arrives as `=X` and floats). cargo (`--version X`): `is_valid_package_version`, because cargo installs exactly X only when X has no operator (`=1.2.3` floats, conservatively). Every other launcher branch was checked for the same ordering: the rev-5 generic check also made pypi `1.0.0-x.tgzx` and an unknown type's `3.25.5` exact. The same predicate drives the report, so a docker tag is `floating_selector` / `[FLOATING]` and a digest is `pinned_version` / `[PINNED]`. The docker remedy names a digest (`image@sha256:...`). | **Reproduced:** rev 5 gives `_is_exact_pin('docker', '3.25.5') = True`, `_is_exact_pin('pypi', '1.0.0-x.tgzx') = True`, and `docker example/client:3.25.5 -> None`. Rev 6 gives `False`, `False`, and `... floats on '3.25.5' (a docker tag, which can be moved to another image; only an @sha256 digest pins one) ...`. A digest stays silent on both. Tests: `test_exactness_is_decided_per_launcher`, `test_health_warns_on_a_docker_tag_however_version_like`, `test_update_server_reports_a_docker_tag_as_floating`: **3 red on rev 5**. Mutants M43 (the generic check restored first) and M53 (any `sha256:` prefix counts as a digest). |
| **NB-1** | For a launcher identity does not read (`npx.cmd`, `/usr/bin/npx`, `npm.cmd`), the cause text reported the resolver's status, which depends on whether an earlier lookup started the child. | The cause is keyed on the launcher first: `_npm_identity_refusal_cause(command)` returns `pmcp's npm identity check reads only a bare \`npx\`/\`npm\` command, and this one is 'npx.cmd'` for any non-bare command, and consults the resolver only for a bare one. For such a launcher the resolver never ran, which is one more reason C1's context check is computed independently. | Rev 5 says `... (read from the argv: npm package identity is unavailable (gateway_diagnostics.npm_identity: not started ...))` for `npx.cmd`; rev 6 names the launcher. `test_the_cause_names_a_launcher_identity_does_not_read` (4 launchers × 2 resolver states): **8 red on rev 5**. Mutant M44. |
| **NB-2** | "(a range or tag, not one exact version)" mislabels strings that npm releases read differently: npa 13 reads `1.0.0-x.tar-gz` as a version and npa 12 as a file. | `_floating_reason(package_type)` says only what holds: npm `not one exact version on every npm release`; docker `a docker tag, which can be moved to another image; only an @sha256 digest pins one`; pypi `not one exact PEP 440 version`; cargo `cargo reads it as a version requirement, not one exact version`. `update_server` says `'X' does not hold the client at one version (<reason>): a later spawn can run another one`, and `pmcp update` prints `[FLOATING] fc: held at ^3.25.0, which is not one exact version and can resolve to another at a later spawn (latest 3.26.0)`. | `test_the_floating_label_claims_only_what_every_npm_reads` is red on rev 5. Two existing tests changed their expected text (`test_update_server_reports_a_range_as_floating_not_pinned`, `test_pmcp_update_renders_a_range_as_floating`), so they are red on rev 5 too. Mutant M52. |
| **NB-3** | R5-2 attributed 528 to two files. | Corrected in R5-2: `tests/test_gateway_tool_schemas.py` + `tests/test_tools.py` give **416 passed**. 528 was those two plus `tests/test_version_pin.py`'s 112. | Re-measured on the rev-5 spike: `416 passed in 18.71s`. |

**Scope growth.** Still four source files. `handlers.py` gains the per-launcher
`_is_exact_pin`, `_floating_reason`, the npm-context helpers (`_NPM_KEYS_THAT_CANNOT_REDIRECT`,
`_npm_env_config_key`, `_npm_key_can_redirect`, `_npmrc_redirecting_key`,
`_npm_config_files`, `_npm_redirecting_context`) and the launcher-keyed cause. `cli.py` and
`types.py` change wording only. `loader.py` is unchanged from revision 5, so the pin grammar
and the conformance corpus are unaffected (re-run anyway: 0/0 on both npa releases).

## Rev 5 board findings — before/after, measured

Measured this session with `repro_c1.py` and `repro_c2.py` (below) against two trees:
`959d4d4` + the revision-5 patch, and `959d4d4` + the revision-6 patch. Both use the real
npm resolver (npm 11.19.0) and the real warning function, with a self-hosted
`FIRECRAWL_API_URL` set. Each row is one fresh process.

| context (argv `npx -y firecrawl-mcp@3.25.5` unless noted) | resolver status | rev 5 | rev 6 |
|---|---|---|---|
| nothing redirects | `active (npm 11.19.0)` | `None` | `None` |
| gateway env `npm_config_cache` + `npm_config_store_dir` (dev0) | `DISABLED ... ('npm_config_cache' ...)` | `None` | `None` |
| server env `npm_config_package=file:<dir>` | `not started` | `None` | cannot verify: `npm_config_package is set in the client's environment` |
| server env `NPM_CONFIG_REGISTRY=http://127.0.0.1:9/` | `not started` | `None` | cannot verify: `NPM_CONFIG_REGISTRY is set ...` |
| gateway env `npm_config_package=file:<dir>` | `DISABLED ... ('npm_config_package' ...)` | `None` | cannot verify: `npm_config_package is set ...` |
| same server env, after another lookup started the child | `active (npm 11.19.0)` | `None` | cannot verify: `npm_config_package is set ...` |
| cwd inside a node project (`package.json`) | `not started` | `None` | cannot verify: `npm would set a local prefix at <dir> ...` |
| `$HOME/.npmrc` with `package=file:<dir>` | `active (npm 11.19.0)` | `None` | cannot verify: `<home>/.npmrc sets 'package'` |
| `<cwd>/.npmrc` with `package=file:<dir>`, no `package.json` | `active (npm 11.19.0)` | `None` | cannot verify: `<cwd>/.npmrc sets 'package'` |
| `npx.cmd -y firecrawl-mcp` (NB-1) | `not started` | `is unpinned (read from the argv: npm package identity is unavailable (gateway_diagnostics.npm_identity: not started ...))` | `is unpinned (read from the argv: pmcp's npm identity check reads only a bare \`npx\`/\`npm\` command, and this one is 'npx.cmd')` |
| `docker run --pull=always example/client:3.25.5` (C2) | n/a | `None`; `_is_exact_pin('docker', '3.25.5') = True` | `floats on '3.25.5' (a docker tag, which can be moved to another image; only an @sha256 digest pins one)`; `False` |
| `docker run example/client@sha256:<64 hex>` | n/a | `None` | `None` |
| `_is_exact_pin('pypi', '1.0.0-x.tgzx')` (the same ordering bug) | n/a | `True` | `False` |
| `npx -y firecrawl-mcp@1.0.0-x.tar-gz` (NB-2) | n/a | `floats on '1.0.0-x.tar-gz' (a range or tag, not one exact version)` | `floats on '1.0.0-x.tar-gz' (not one exact version on every npm release)` |

**That npm really runs the local package** (C1), measured with npm 11.19.0 against a local
package `firecrawl-mcp@0.0.1` whose bin prints `LOCAL firecrawl-mcp 0.0.1`, with an offline
temp cache:

```bash
npm_config_cache=<tmp> npm_config_offline=true npm_config_package=file:<dir> npx -y firecrawl-mcp@3.25.5
#   -> LOCAL firecrawl-mcp 0.0.1
cd <dir-with-.npmrc: package=file:<dir>> && npm_config_cache=<tmp> npm_config_offline=true npx -y firecrawl-mcp@3.25.5
#   -> LOCAL firecrawl-mcp 0.0.1
HOME=<home-with-.npmrc: package=file:<dir>> npm_config_cache=<tmp> npm_config_offline=true npx -y firecrawl-mcp@3.25.5
#   -> LOCAL firecrawl-mcp 0.0.1
```

**Tests:** the revision-6 file has **153 tests** (112 in revision 5, plus 41 new cases).
Against the revision-5 code it gives **41 failed, 112 passed**. The 41 failures are the 39
new red cases (C1 27, C2 3, NB-1 8, NB-2 1) plus the two existing tests whose NB-2 text
changed. The 112 passes are the 110 revision-5 tests that kept their text, plus the control
`test_settings_that_cannot_redirect_npm_keep_an_exact_pin_silent` in both identity modes,
which is correct on both revisions. Against revision 6: **153 passed**.

## Revision 5 (2026-09-27): re-derived against main `959d4d4`

Main now carries Consiliency/pmcp#299 and Consiliency/pmcp#300. This revision re-derives
the reference patch against `959d4d4`, re-measures everything, and makes no design
change to the feature.

| # | item | resolution | evidence |
|---|---|---|---|
| R5-1 | **The version rule is on main (#299).** `validation.NPM_FILE_TYPE_RE` uses npm 10's `isFileType` (npm-package-arg 12.x; its `.` before `gz` is unescaped, a superset of npm 11's). `is_valid_package_version` bounds each core part at 2**53-1, because node-semver refuses larger ones and npa then reads a dist-tag. | The revision-4 `validation.py` hunk **drops out of the patch**. The pin grammar **uses main's rule and never restates it**: `_parse_version_pin` calls `is_valid_package_version` (plus its own `+` refusal); `split_plain_registry_spec` imports `NPM_FILE_TYPE_RE` for the selector and unscoped-name checks and calls `is_valid_package_version` for the version branch; `_is_exact_pin` calls `is_valid_package_version`. No pin code calls the bare `matches_package_version_grammar`/`is_semver_package_version`. New fixed cases pin the two stricter rules: pin values `1.0.0-x.tar-gz` and `9007199254740992.0.0`, and slot ids `semver-tarball-npm10-tar-gz`, `tarball-npm10-tar-gz` and `oversized-core-is-a-tag`. | `grep -c validation.py` on the reference diff gives **0**. Mutants **M34** (pin value checked with `matches_package_version_grammar`, 7 red), **M35** (the split accepts a bare-grammar version before its file check, 7 red) and **M40** (`_is_exact_pin` on the bare grammar, 1 red) each go red if pin code bypasses main's rule. **Conformance per npa version** (below): **0/0 on both npm-package-arg 12.0.2 and 13.0.2**. The revision-4 rule on the same corpus scored **4,525/13 on 12.0.2** and **50/10 on 13.0.2**, so main's stricter rule is load-bearing for the pin grammar. |
| R5-2 | **Conflict with #300.** #300 asserts that each tool handler validates with its registered input model, by grepping the handler's own source (`tests/test_gateway_tool_schemas.py::test_handler_validates_arguments_with_the_registered_model`). Revision 4's `update_server` wrapper delegated validation to `_update_server_unwarned`, so it went red. | The public `update_server` now calls `UpdateServerInput.model_validate(input_data)` itself and passes the parsed model to `_update_server_unwarned(parsed)`. The behaviour is the same: a validation error raises before any work, exactly as before. `types.py`: the `UpdateServerOutput` hunk's trailing context moved (`AuthConnectInput(GatewayArguments)`), and the same fields are re-applied. Output models are not part of #300's input-schema snapshot, and that snapshot test stays green. | Measured on `959d4d4` + the revision-4 patch: that test fails for `gateway.update_server`. On revision 5, `tests/test_gateway_tool_schemas.py` and `tests/test_tools.py` together give **416 passed** (corrected in revision 6, NB-3: 528 was these two files plus `tests/test_version_pin.py`'s 112). The patch applies cleanly to `959d4d4` (the embedding proof). |
| R5-3 | Disclosure scope. | The plan describes its own feature: pin values, pinned argvs, the warning and the report. The version rule it relies on is described neutrally as "the exact-version check follows npm's classification". | The Revision 4 table and D1/D4 were rewritten to match. |

**Re-measured on the revision-5 spike** (`959d4d4` + patch):

- **Tests:** `tests/test_version_pin.py` has **112 tests**. On `959d4d4` without the
  patch, the file fails at collection (`ImportError: manifest_sources_fingerprint`). With
  a two-symbol import shim it gives **111 failed, 1 passed**; the pass is
  `test_explicit_config_args_win_over_the_manifest_pin`, an inertness guard. With the
  patch: **112 passed**.
- **Mutants:** **40 of 40 red**, each file restored and `cmp`-checked.
- **Full suite:** 4448 passed, 3 skipped, 25 deselected in 443.28s (0:07:23).
- **No-network run:** **1153 passed, 1 deselected** over 20 files (#300 added
  `tests/test_gateway_tool_schemas.py`). The file glob is now `--include='*.py'`, because
  #300's JSON fixture also matches the grep.
- **Neighbouring suites:** **1974 passed, 19 deselected**. These now include
  `tests/test_package_approvals.py` and `tests/test_gateway_tool_schemas.py`.
- **CI gates:** `ruff check`, `ruff format --check` (164 files) and `mypy src/` (50
  files) are all clean.

## Revision 4 (2026-09-26): board round 3 on `e5b5ce2`

The claude seat returned DISAGREE, with one blocker (B1') and four non-blocking items. It
used a 1,081,185-slot generated corpus run through npm's real classifier. Each item was
**reproduced first**, then fixed. Every item has tests that are red on the revision-3
spike and green on revision 4, plus mutants M34-M39. **All 89 revision-3 tests pass on
the revision-3 code**, which proves that the old tests did not cover B1'. The new cases
are **18 red on revision 3** and green now. The numbers below were re-measured on the
revision-4 spike, which was then reverted.

| # | finding | resolution | evidence |
|---|---|---|---|
| **B1'** (blocking) | A **SemVer-shaped tarball** gets through in both directions. npa runs `isFileType` *before* it reads a version, and a strict SemVer prerelease or build tail can end in `.tgz`/`.tar`/`.tar.gz` (`3.25.5-corp.tgz`, `1.0.0-x.TAR`, `3.25.5+b.tar.gz`). **Slot:** `split_plain_registry_spec` accepted such a version before its file check ran. **Pin value:** `_parse_version_pin` accepted `3.25.5-evil.tgz`. | The pin grammar follows npm's order. `split_plain_registry_spec` checks the selector's file suffix **before** any registry reading, and the pin value, the split's version branch and `_is_exact_pin` all use `is_valid_package_version`, whose exact-version check follows npm's classification (tarball-shaped versions are not exact versions). As of revision 5 that check is main's (#299); revision 4 carried an equivalent change in its own patch. | **Reproduced** with the seat's overlays (temp HOME). **Slot:** `9ca081e` keeps `['-y','firecrawl-mcp@3.25.5-corp.tgz']`; **rev 3 gives `version 3.25.6 args ['-y','firecrawl-mcp@3.25.6']`** (silent); **rev 4 gives `version None args ['-y','firecrawl-mcp@3.25.5-corp.tgz']`** plus the WARNING. **Pin value:** **rev 3 gives `firecrawl version 3.25.5-evil.tgz args ['-y','firecrawl-mcp@3.25.5-evil.tgz']`**; **rev 4 gives `version None args ['-y','firecrawl-mcp']`** plus the WARNING. The **generated npm conformance** run finds **0 slot and 0 pin-value violations** on rev 4, against **5,153 and 8** on rev 3 (npa 13.0.2). Tests: the tarball pin values, the `semver-tarball-*` slot ids, `test_a_tarball_shaped_version_is_never_an_exact_pin` (pin grammar and `_is_exact_pin`), and `test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes`. The rev-5 mutants M34/M35/M40 are described in the Revision 5 table. |
| **N-a** | Loose SemVer allows **any** run of leading `v`s, so `vv1`, `vvX`, `v1.X.xbeta` (range) and `vv1.2.3` (version) were accepted as tags. | `_PARTIAL_VERSION_WORD_RE` now allows any run of leading `v`/`V`/`=`, and any loose-prerelease tail with or without a hyphen. The "every range refused" invariant holds, and the conformance run's strict check counts a range with a selector as a violation. | Ids `range-vv`, `range-vvX`, `range-v-xbeta` and `version-vv` are red on rev 3. Mutant M37 (`[vV]?`) gives 3 failed. |
| **N-b** | With identity disabled, `npm exec -y firecrawl-mcp` and `npm exec -- firecrawl-mcp@latest` gave no warning, because the fallback handled only `npx`. | **It fails loud for the whole npm family.** An `npm` launcher (by `normalized_executable_name`) now warns `pmcp cannot verify that its client is pinned: <cause>, and pmcp reads only an npx argv structurally, and this one launches with npm`. npx keeps the structural read. | **Reproduced with the real disabled resolver** (`npm_config_cache` set): rev 3 is `[] (silent)` for both argvs, and rev 4 gives the warning for both. `test_health_fails_loud_for_an_npm_exec_launch_without_identity` is red on rev 3. Mutant M38. |
| **N-c** | The reserved names `node_modules` and `favicon.ico` were accepted, although npa refuses them (validate-npm-package-name `exclusionList`). | Refused case-insensitively, as validate-npm-package-name compares them. | Ids `excluded-node_modules`, `excluded-Node_Modules-versioned` and `excluded-favicon` are red on rev 3. Mutant M36. The corpus includes `Node_Modules` and `FAVICON.ICO`. |
| **N-d** | The fallback said "npm package identity is unavailable" even when identity was **on** and had simply refused a non-registry spec. | `_npm_identity_refusal_cause()` reads `get_resolver().status_summary()`, which never spawns. `active ...` gives `npm's own parser did not identify a registry package in this argv (npm identity is active (npm 11.19.0))`. Anything else gives `npm package identity is unavailable (gateway_diagnostics.npm_identity: <status>)`. | **Reproduced with the real active resolver:** `npx -y firecrawl-mcp@corp.tgz` on rev 3 says "unavailable", and rev 4 names npm's parser. The health test asserts the parser cause and the absence of "unavailable"; the npm-exec test asserts "unavailable" under the disabled fixture. Mutant M39. |

**Generated conformance corpus** (Verification step 10, which replaces the rev-3 461-slot
list). This is the seat's generator, adapted. In revision 4 it was 39 names × (30
prefixes × 33 cores × 36 suffixes), which gives **1,366,139 slots and 35,028 pin
values**; revision 5 enlarges it (see the Revision 5 table and step 10). It adds `vvv`/`v=`
prefixes, `xbeta`/`-X.Tar.Gz`/`+b.tar.gz` suffixes and case variants of the excluded
names. Results against the host's npm-package-arg (npm 11.19.0):

- **rev 4, slots:** accepted 14,526 (`version` 251, `tag` 14,250, bare-name `range *` 25), refused 1,351,613, **SLOT VIOLATIONS 0**.
- **rev 4, pin values:** accepted 8, **PIN VIOLATIONS 0**. The runner exits 0.
- **rev 3, same corpus:** **5,153 slot violations** (300 npa `file`, 3,924 excluded names, 925 ranges with a selector, and 4 bare excluded names that npa reads as nameless tags) and **8 pin violations**.

**Scope growth.** `loader.py` re-orders the selector check and gains the excluded names;
`handlers.py` gains the cause helper and the npm-exec branch. Revision 4 also carried a
`validation.py` change, which main now has (#299), so it is no longer in the patch.

## Revision 3 (2026-09-26): board round 2 on `95968c8`

The claude seat returned DISAGREE with one blocker (B1) and four non-blocking items. It
confirmed every other round-1 resolution and reproduced the round-1 numbers. Each
item below was **reproduced first**, then fixed, and has a test that is red on the
revision-2 spike and green on revision 3, plus a mutant (M25-M33). All numbers were
re-measured on the revision-3 spike, which was then reverted.

| # | finding | resolution | evidence |
|---|---|---|---|
| **B1** (blocking) | Tarball specs slipped through the P1 allowlist. npm treats `name@corp.tgz`, `name@x.tar` and `name@x.tar.gz` (any case), and a bare **unscoped** `corp.tgz` slot, as local tarball **files**. The rev-2 tag regex read them as dist-tags, so `firecrawl-mcp@corp-mcp.TGZ` plus a pin became the public `firecrawl-mcp@3.25.5`: dependency confusion. | npm's own rule, verbatim from npm-package-arg: `isFileType = /[.](?:tgz\|tar\.gz\|tar)$/i`. It is applied to the selector **and** to an unscoped name, exactly where npa applies it; a scoped name is exempt, as in npa. The allowlist was then **re-derived class by class** against npa's classifier (the class table below, in D3), which also closed N4. The rule is pure grammar in the new public `split_plain_registry_spec`. It never consults the resolver, so pins keep working while identity is disabled. | **Reproduced** end to end with the seat's overlay (temp HOME): main `9ca081e` gives `['-y','firecrawl-mcp@corp-mcp.TGZ']`; rev 2 (per the seat, and per the `grammar_conformance` probe below) gives `['-y','firecrawl-mcp@3.25.5']`, silently; **rev 3 gives `version: None args: ['-y','firecrawl-mcp@corp-mcp.TGZ']` plus the WARNING** `Ignoring version pin '3.25.5' for server 'corp-mcp': its args name no plain registry package ...`. **Grammar conformance against the host's real npa** (461 crafted slots): rev-2 rule, **136 violations** (accepted slots that npa classifies as `file`, as `range`, or as invalid); **rev-3 rule, 0 violations**. The P1 test now has one case per npa class (25 ids): **12 red on rev 2** (every tarball form, both bare tarballs, `x`, `X`, `v1.2.x`, and the trailing newline), green on rev 3. A new positive-control test covers 8 accepted classes. Mutant **M26** (drop the name clause) is red on **`bare-tarball-tgz`, `bare-tarball-TAR` and `tarball-name-with-version`**. M25 (drop the selector clause) is red on all 5 tarball-selector ids. |
| **N1** | Two cache-key components had no test: the project overlay and the trust store. | Added `test_fingerprint_changes_when_a_project_overlay_appears` and `test_fingerprint_changes_when_the_project_overlay_is_approved`. The latter uses `approve_project_file`, which changes only the trust store, and asserts that the approved `server_version` then loads. | Mutant **M32** (`parts.append(None)` for the project line) is red on the first test. **M33** (the same for the trust-store line) is red on the second. Before revision 3, both mutants survived (the seat measured 70 passed). |
| **N2** | The warning and `pmcp update` went silent when npm identity was disabled, which is dev0's default (`npm_config_cache`). | **I chose the structural fallback, and it fails loud when that can't decide.** For an npx launch whose identity is refused, the warning reads the slot with the provision gate's `_package_slot` and the same `split_plain_registry_spec` grammar, and says so (`(read from the argv: npm package identity is unavailable)`). An exact pin stays silent. When even the grammar can't name a plain registry package, it warns `pmcp cannot verify that its client is pinned: npm package identity is unavailable (see gateway_diagnostics.npm_identity) ...`. `pmcp update` prints the same warning under its result through the update wrapper. The update result itself is the pre-existing "Could not determine a registry package", because moving a package needs identity (#195) and that stays refused. | **Reproduced** with `npm_config_cache=/tmp/...` set: identity is `('unknown', None)`. rev 2 gives `warnings: []`. **rev 3 gives `'firecrawl' ... npm:firecrawl-mcp is unpinned (read from the argv: npm package identity is unavailable) ...`.** There are 3 tests (unpinned, exact pin, unreadable slot), using the `npm_identity_disabled` fixture. **2 are red on rev 2** (the exact-pin control is green on both). Mutants M29 and M30. |
| **N3** | Build metadata was refused only for manifest pins. A `.pmcp.json` argv `firecrawl-mcp@3.25.5+evil` was labelled `[PINNED] 3.25.5+evil`. | **The label says what npm runs.** A configured argv is the operator's own, so pmcp does not refuse it. `_is_exact_pin` keeps treating it as exact, correctly: npm runs exactly 3.25.5, so the warning stays suppressed. `update_server` now reports `pinned_version="3.25.5"`, compares 3.25.5 against latest, and its message says `(build metadata '+evil' is ignored by npm)`. The same applies to cargo. The manifest path still refuses `+` outright (round-1 N2). | `test_update_server_labels_build_metadata_with_what_npm_runs`: **red on rev 2** (`('3.25.5+evil', 'newer') == ('3.25.5', 'newer')`), green on rev 3. Mutant M31. |
| **N4** (nit) | `latest\n` was accepted (`match` rather than `fullmatch`), and `x`/`X` were accepted as "tags" although npm treats them as ranges. | The tag word is now `fullmatch`. Letter-led version or range words (`x`, `X`, `x.x`, `v1`, `v1.2.x`, `x-beta`) are **refused** by a partial-version-word clause, consistent with refusing every range. The clause is deliberately broad: a real tag it also catches only loses its pin, with a warning. | The P1 test ids `range-x`, `range-X`, `range-v-partial` and `tag-trailing-newline` are red on rev 2. Mutants M27 (range words) and M28 (`match`; red on `tag-trailing-newline` and 7 others). |
| **N5** | This was already documented. | Unchanged, as instructed. | None needed. |

**Scope growth.** There is still no new source file. `loader.py` gains the
`split_plain_registry_spec` grammar (three module regexes), and `handlers.py` gains the
identity-disabled fallback and the build-metadata label.

## Revision 2 (2026-09-26): board round on Consiliency/pmcp#295 @ `9d08184`

The board reached quorum: gemini AGREE, claude PARTIALLY AGREE, codex DISAGREE with 3
blocking findings, and grok timed out. Every finding is resolved below. Each blocking
one was **reproduced first**, then fixed, and is proved by a new test that is red on the
revision-1 spike and green on revision 2, plus a mutant (mutation table, M15-M24). All
numbers below were re-measured on the revision-2 spike, which was then reverted.

| # | finding | resolution | evidence |
|---|---|---|---|
| codex P1 (blocking) + claude N1 | The materialiser rewrote an alias `myalias@npm:firecrawl-mcp@3.25.5` to `myalias@3.25.5`, which is a different registry package. | `_pin_npx_args` now pins only a **plain registry spec**, defined by grammar as an allowlist: `name`, or `name@<selector>` where the selector is one exact SemVer version (`is_valid_package_version`) or a dist-tag (`package_identity._DIST_TAG_RE`, a letter-led `[A-Za-z0-9._-]` word). Everything else npm accepts after `name@` is refused with a WARNING and the entry stays unpinned: aliases, URLs, git/`github:`, `file:`/tarball, and also ranges (a range selects a set). See D3 step 3. | Reproduced with the real npm resolver: `detect_package_type("npx", ["-y","myalias@npm:firecrawl-mcp@3.25.5"])` gives `('unknown', None)`, and `["-y","myalias@3.25.5"]` gives `('npm', 'myalias')`. Test `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec` covers 7 cases, all **7 red on rev 1** (`assert '3.25.5' is None`) and green on rev 2. `test_version_replaces_a_dist_tag_slot` is the positive control. Mutants M15 (7 red) and M16 (2 red). |
| codex P2 (blocking) + claude F1 | A range or dist-tag (`^3.25.5`, `~`, `next`) silenced the self-hosted warning, and `pmcp update` labelled a range `[PINNED] ... pinned at ^3.25.0`. | New `_is_exact_pin(package_type, pin)`: exact means `is_valid_package_version` in every ecosystem, plus a docker `sha256:` digest and a PEP 440 `==X` with no wildcard. The warning suppresses only on an **exact** pin, and names a non-exact one: `floats on '^3.25.5' (a range or tag, not one exact version)`. `update_server`'s refusal is **unchanged**: it still does not move what the operator chose. It now reports a non-exact selector as `floating_selector` (with `pinned_version=None`, `latest_comparison=None`) and a message that starts `'fc' is held at '^3.25.0'`. `pmcp update` prints **`[FLOATING] fc: held at ^3.25.0, a range or tag that re-resolves at every spawn (latest 3.26.0)`**. See D6 and D7. | `test_health_warns_on_a_range_or_dist_tag` (4 specs), `test_update_server_reports_a_range_as_floating_not_pinned` and `test_pmcp_update_renders_a_range_as_floating`: **6 red on rev 1** (`assert 0 == 1` ×4, `AttributeError: ... 'floating_selector'`, `['[FAILED] fc: long message']`), all green on rev 2. Mutants M17 (5 red), M18 (1) and M24 (1). |
| codex P3 (blocking) | One malformed entry aborted the whole manifest load: `args: ["-y", 123]` plus `version:` raised `AttributeError`. | Materialisation is contained **per entry** (`_materialize_version_pin_soft`). Any exception costs that entry its pin, with a WARNING, and everything else loads. | Reproduced with the same overlay (user `~/.pmcp/manifest.yaml`, entry `malformed`, `args: ["-y", 123]`, `version: "1.2.3"`): **main `9ca081e`: `entries: 108`**; **rev 1: `LOAD FAILED: AttributeError 'int' object has no attribute 'startswith'`**; **rev 2: `entries: 108 malformed.version: None`**. Test `test_a_pin_on_a_malformed_entry_costs_only_that_entry` covers int in args, int in an install argv, and int `command`, asserting `len(servers) == shipped + 1`: **3 red on rev 1** (`AttributeError` ×2, `TypeError`), green on rev 2. Mutant M19 (3 red). |
| claude F2 | A relaxer that the child inherits from the gateway's own environment never triggered the warning. | The **advisory** warning now judges the relaxer on `sanitized_subprocess_env(resolved.config.env, project_root)`, the exact environment `client/manager.py:2409` spawns with (the gateway's env minus managed secrets, plus the entry's env). The credential gate is **unchanged**: `credential_requirement`'s gate callers still pass `config.env`. For a gate, ignoring the ambient value is the safe direction (#124). For a warning, including it is. `tests/test_credential_predicate_guard.py` (no `os.environ` passed as `child_env`) stays green. | `test_health_warns_when_the_relaxer_comes_from_the_gateway_environment` (`monkeypatch.setenv("SELFHOST_API_URL", ...)`, empty `extra_env`) is **red on rev 1** (`assert 0 == 1`) and green on rev 2. In the same test, `credential_requirement(server).required is True` proves the gate did not move. Mutant M20 (1 red). |
| claude F3 | `gateway.health` loaded the manifest once or twice per call (~92 ms each), and logged a WARNING per load while an unapproved project overlay was present. | Health no longer reads config files at all. (1) The relaxer-declaring manifest entries are cached by `manifest_sources_fingerprint()`, a new `stat`-only key over the shipped manifest, the user overlay, the project overlay found by the cwd walk, the `$PMCP_MANIFEST_PATH` value and target, and the **trust store** (an approval changes what loads without touching the overlay). The manifest is re-loaded only when that key changes. (2) The config judged is the one the gateway actually **connected** with (`ClientManager.get_connected_configs()`, which already exists), so there is no `load_configs()`. A server that is not connected is not judged by health; `update_server` still warns for it. | Measured with 21 `health()` calls, firecrawl connected, a user `server_env` URL and an **unapproved project overlay** in the cwd. **main: 0.0 ms mean, 0 WARNING lines. rev 1: 223.6 ms mean (220.7 ms steady), 42 WARNING lines (2 per call). rev 2: 9.5 ms mean, first call 174.1 ms (one load plus resolver spawn), then 1.3 ms steady; 1 WARNING line in total.** Test `test_health_loads_the_manifest_once_until_a_source_changes` expects 3 calls to give 1 load, and a user-overlay write to give a 2nd load. It is **red on rev 1** (`assert 3 == 1`) and green on rev 2. Mutants M21 (no cache) and M22 (fingerprint misses the user overlay), 1 red each. |
| claude N2 | Build metadata was accepted (`3.25.5+evil`). | **Refused.** npm ignores build metadata when resolving, so the argv would run 3.25.5 while every report echoed a label that names nothing. The rule is `is_valid_package_version(raw) and "+" not in raw`, because SemVer's build segment is the `+...` suffix. | The parametrized refusal set gains `"3.25.5+evil"`: **red on rev 1** (`assert '3.25.5+evil' is None`), green on rev 2. Mutant M23 (1 red). |
| gemini note 1 | PyPI `pkg==1.2.3`: the pinned report's latest lookup uses the name `pkg==1.2.3` and so comes back unknown. | **Tabled, explicitly.** It is pre-existing: `detect_package_type` keeps `==X` in a uvx "name" on purpose, so the existing pinned-refusal fires. This plan only adds the report, which honestly says "latest version could not be determined". Fixing it means a PEP 440 lookup name for uvx pins, and it lands with any future uvx `version:` support (D5), not here. `_is_exact_pin` already treats `==1.2.3` as exact, so no false FLOATING and no false warning. | D5 (unchanged text), plus this row. |
| gemini note 2 | The descriptions cache labels a pinned server's tools with the registry's latest. | **Tabled, explicitly**, as Non-goal plus R2. It is pre-existing for `.mcp.json` pins, causes regeneration churn only, and nothing reads the label as the running version since #150. | Non-goals and R2 (unchanged). |

**Other notes from the claude seat.** (a) A lazily registered server that has never
connected is not judged by health. That is now explicit, and `update_server` still warns
for it. (b) After adding a pin, run `gateway.refresh`, because a child spawned before the
pin keeps its version until it is respawned. The README subsection states this (see
Documentation impact).

**Scope growth.** Still within threshold. There is no new source file: `handlers.py`
gains `_is_exact_pin`, `_relaxable_manifest_servers` and the cache attribute; `loader.py`
gains `_materialize_version_pin_soft` and `manifest_sources_fingerprint`; `types.py`
gains `floating_selector`; `cli.py` gains the `[FLOATING]` branch.

## Task

Consiliency/pmcp#294. Built-in manifest entries launch npm MCP clients unversioned
(`firecrawl`: `npx -y firecrawl-mcp`), and `pmcp update` moves them to the latest
release. That is fine for a vendor-hosted backend. With a **self-hosted** backend
(`api_key_optional_when: ["FIRECRAWL_API_URL"]`, Consiliency/pmcp#114), the client can
move ahead of the server without anyone noticing. On 2026-09-25, `firecrawl-mcp` 3.25.5
started sending fields that the self-hosted API rejected with a 400.

The maintainer's 2026-09-26 measurements (issue comment) found two things:

- The 400 **no longer reproduces**: the server was fixed in Consiliency/firecrawl#9. The
  unpinned-client hazard is still real (`~/.pmcp.json` still says
  `firecrawl-mcp@latest`, and ViperJuice/dotfiles#325 is still open).
- The self-hosted search returns an empty `success:true` in **1 of 6** identical
  requests. That is a server-side problem, and it constrains follow-up slice 3 (below).

Scope of this plan:

- **Proposal 1.** A `version:` field on a manifest entry, which a user overlay can set on
  a built-in entry without restating its install matrix. `pmcp update` then reports
  "pinned at X, newer available: Y".
- **Proposal 2.** A warning in `pmcp update` and `gateway.health` when an entry's
  `api_key_optional_when` relaxer is active and its client is unpinned.

## Research summary (current tree, `9ca081e`)

**How an npm spec reaches the spawned command.** A manifest `ServerConfig`
(`src/pmcp/manifest/loader.py:43-90`) carries `command`, `args` and a per-platform
`install` argv. Two paths spawn from it:

- `config/loader.py` `_manifest_server_to_config` (`:1364`) builds a `LocalMcpServerConfig`
  from `server.command` + `server.args`, and `client/manager.py` spawns that on every
  connect, restart and lazy reconnect.
- `manifest/installer.py` (`:182`, `:686`) runs `server_config.install[platform]` for
  provisioning.

`provision_gate._config_runs_exactly` (`provision_gate.py:166-184`) already codifies
"both spawn": an argv pin counts only if `args` AND every `install` argv run exactly
`name@version` under npx. `_package_slot` (`:136`) defines the slot: the first argument
that is not in `_NPX_LEADING_FLAGS = {-y, --yes, -q, --quiet}`.

**How `.pmcp.json`/`.mcp.json` interacts.** `_merge_manifest_defaults`
(`config/loader.py:1022`) copies the manifest's `command` and prepends its `args`
**only when the configured entry has no `command`**. An entry with explicit
`command`/`args` (the ViperJuice/dotfiles#325 shape,
`"args": ["-y", "firecrawl-mcp@3.25.5"]`) is taken verbatim. Only the manifest's
`extra_env` is merged in, for keys the entry does not set.

**How update resolution reads a pin.** `gateway.update_server`
(`tools/handlers.py:5292`) resolves the *effective* config through
`_resolve_lifecycle_target`, where configured wins over manifest. It classifies the
config with `detect_package_type` and then calls `_detect_effective_version_pin`
(`handlers.py:347-450`). When that returns a pin, the tool **already refuses**, with
`"'X' is pinned to 'V' in <source> (...). gateway.update_server will not move a pinned
server..."`. `tests/test_pkgid_panel_fixes.py:900` asserts that substring. The npm branch
of the pin check reads the package token through `_npm_package_arg`, npm's own parser
(#195), and treats `@latest` as unpinned. uvx `==`, cargo `--version` and docker
`:tag`/`@sha256:` are detected too. What is missing is *what is available*:
`get_package_version` (`version_checker.py:1736`) strips the tag in
`detect_package_type`, so for a pinned argv it returns the registry's `dist-tags.latest`.
`compare_versions` (`:1906`) is the three-way classifier (#164). So the report needs no
new resolution code.

**`pmcp update` is a thin client.** `cli.py:943 run_update` calls `gateway.update_server`
per target and prints `[OK]`/`[FAILED] server: message` (`:1005-1010`). `--json` dumps the
results. It **never exits nonzero** on a per-server failure. A pinned server therefore
prints today as `[FAILED]` with a long refusal message.

**Relaxer judgement.** `credential_requirement(server, child_env=...)`
(`loader.py:164-231`) returns `relaxed_by`, the relaxer variable name, when the entry
declares `api_key_optional_when` and the *child's* environment carries a usable value for
it. It never reads `os.environ` (#124). `config/loader.py:1437 _local_env` and `:1453
_eager_requires_credential` show the required call shape: the resolved config's `env` is
passed as `child_env`.

**npm resolver cost.** `NpmResolver` (`npm_resolver.py:234`) is a persistent node child:
43 ms to start, then ~0.5 ms per query, with deliberately no memoisation (`:449-462`).
Measured this session: with the three `npm_config_*` variables unset, the real resolver
(`active (npm 11.19.0)`) resolves `firecrawl-mcp@3.25.5` to the token
`firecrawl-mcp@3.25.5` → `npm`/`firecrawl-mcp`, pin `3.25.5`, and `@playwright/mcp@1.2.3`
→ pin `1.2.3`. `firecrawl-mcp@latest` → pin `None`. A version-qualified spec is ordinary
IDENTITY input and weakens nothing. With `npm_config_cache` set, the resolver is DISABLED
and every npm server reads as `unknown`. That is by design, and it is why every test
command below unsets those variables.

**Live registry check (read only).** `get_package_version("npx", ["-y",
"firecrawl-mcp@3.25.4"], None, None)` → `3.25.5`, and `compare_versions("3.25.4",
"3.25.5", "npm")` → `newer`. That is the exact report proposal 1 asks for.

**Overlay precedent.** `server_env:` (Consiliency/pmcp#108/#109) is a top-level overlay
map that patches `extra_env` on an **existing** server, so an operator does not have to
restate its command, args or install block. It cannot create a server (unknown name →
warning, skipped). It is applied per source after that source's whole-entry replaces
(`loader.py:894-907`). A project overlay reaches the parser only through
`read_and_gate(..., "project_manifest")` (`:862`), so an unapproved project file
contributes nothing at all (tests in `tests/test_project_source_consent_manifest.py:125`).

**Which shipped entries a pin can apply to (measured).** The spike's materialiser was run
over all 107 shipped entries with `version="1.0.0"`. **77 pinnable, 30 refused**: 19 uvx,
9 remote with an empty command, `cloudflare` (npx argv but a `url`, so remote), and
`context7`. The `context7` refusal is correct: its windows install is
`["cmd", "/c", "npx", ...]`, which `_config_runs_exactly` would not accept as pinned
either. `firecrawl` and every other `api_key_optional_when` user are pinnable.

**No output-schema snapshots.** Nothing under `tests/` compares a `gateway.health` or
`update_server` payload by dict equality (`grep -rln "ServerHealthInfo\|UpdateServerOutput"
tests/` → only `tests/test_auth.py`, field reads). New optional output fields break no
existing test. The full-suite run below confirms it.

**What can change what a registry spec runs (revision 6, C1; revision 7 keeps this as the map of what is OUT of scope).** Revision 7 judges only the rows an entry controls: the argv and the entry's env block. The npmrc, local-prefix and host-environment rows are the host's, and are trusted (Non-goals). Derived from the npm
sources installed on this host: npm **10.9.9** (node 22.23.3,
`/opt/consiliency/tooling/releases/2026.09.04.1-22960aab68a0/.../lib/node_modules/npm`) and
npm **11.19.0** (node 24.20.0,
`/mnt/workspace/consiliency-tooling-archive/2026.09.04.1-5cff52125d24/.../lib/node_modules/npm`).
Both read configuration through `@npmcli/config`, in the same order and with the same rules:

| source | how npm reads it (`@npmcli/config/lib/index.js`, both releases) | how the rev-6 warning treats it |
|---|---|---|
| argv flags | `nopt` over the `npx-cli.js` pre-scan; the first positional gets a `--` inserted before it | Structural read: `_package_slot` takes the first token that is not `-y`/`--yes`/`-q`/`--quiet`, so any other flag before the package becomes the "slot", which `split_plain_registry_spec` refuses → cannot verify. Tokens after the slot are the bin's arguments. In identity mode the resolver's own allowlist (`yes`, `package`) refuses any other flag. |
| `npm_config_*` environment variables | `loadEnv`: prefix matched **case-insensitively**, an **empty value skipped**, then (unless the rest starts with `//`) every `_` except a leading one becomes `-` and the key is lowercased | Every non-empty one in the **child's** environment (`sanitized_subprocess_env`: the gateway's env plus the server's) is normalised by that rule and judged by the allowlist below. |
| `NODE_OPTIONS` | node applies it before npm starts, so it can load code into npm | Loud when non-empty, in the child's environment. |
| server env overlay `PATH`, `HOME`, `NODE_PATH`, `NODE_OPTIONS`, `PREFIX`, `nvm_*` | relocate the npm that runs, or where its config lives | Loud. This is identity's own selection (`npm_resolver._gate_relevant_env`), reused. |
| inherited `HOME`, `PREFIX`, `DESTDIR`, `PATH` | `loadHome` (`env.HOME \|\| homedir()`), `loadGlobalPrefix` (`env.PREFIX`, else the node binary's grandparent on POSIX or its parent on Windows, with `DESTDIR` prepended on POSIX), and the `npm`/`npx` found on `PATH` | Used to **locate** the files below, as npm does; the files themselves are then read. |
| project config `<localPrefix>/.npmrc` | `loadLocalPrefix` walks up from the cwd for `package.json` or `node_modules`; **if none is found, the local prefix is the cwd**, and `<cwd>/.npmrc` is loaded (unless it is the user file) | A local prefix found above the cwd → loud (identity's `_has_local_prefix`, reused: a project `.npmrc` or a local bin). Otherwise `<cwd>/.npmrc` is read. |
| user config `$HOME/.npmrc` (`userconfig`) | `loadUserConfig` | Read. (`userconfig` itself is a loud key.) |
| global config `<global prefix>/etc/npmrc` (`globalconfig`) | `loadGlobalConfig` | Read, at the location computed as npm does. (`globalconfig` and `prefix` are loud keys.) |
| builtin config `<npm root>/npmrc` | `loadBuiltinConfig` | Read. The npm root is found from the launcher on the child's `PATH` (`<root>/bin/npx-cli.js`, or `<prefix>/lib/node_modules/npm`), the two layouts `_npm_resolve.js` handles. |

Every npmrc line is parsed as `ini` does (`;`/`#` comments, `key = value`, `key[]`), and
its key is judged by the same allowlist. A `[section]`, a quoted key or a `${VAR}` key is
not on it, so it is loud. An existing but unreadable file is loud.

**Which keys are harmless.** A key is harmless only if it cannot change which package,
which version, or which bytes `npx -y <name>@<exact>` runs. The union of the two releases'
definitions has **181 keys** (npm 10.9.9 defines 155; npm 11.19.0 defines 181, a superset:
it adds `allow-*`, `min-release-age*`, token/org keys and a few others). Measured with a
script that loads `@npmcli/config/lib/definitions` from each install:

- **Allowlisted, 90 defined keys**, in five groups:
  - the cache's location, `cache`: the same content-addressed store and `_npx/<hash>`
    lookup as the default location, only elsewhere;
  - output and UI (`color`, `loglevel`, `progress`, `fund`, `audit`, ...);
  - network timing (`fetch-*`, `maxsockets`);
  - credentials that never pick a registry (`_auth`, `otp`, `auth-type`);
  - `yes`, and keys read only by commands other than exec: `init*`, `save*`,
    publish/version/search/diff/token/org/sbom keys.

  The code lists them in `_NPM_KEYS_THAT_CANNOT_REDIRECT`, each group commented.
- **Allowlisted, 3 undefined keys**, named: pnpm's `store-dir` (dev0 exports
  `npm_config_store_dir`), and `email`/`always-auth` (removed in npm 9, still common in
  old `~/.npmrc` files). Neither release passes an undefined key to the code that fetches,
  installs or runs a package. Both flatten config through `definitions/index.js`
  `flatten`, which copies an undefined key only if it matches `/@.*:registry$/i` or
  `^//`. `npm exec` itself reads only `call`, `script-shell`, `package` and `yes` by name
  (`lib/commands/exec.js`).
- **Credential keys `//host/:...`**: harmless. They say how to authenticate to one
  registry, never which registry to use.
- **`@scope:registry`**: loud only when the package is in that scope.
- **Loud, 91 defined keys**, including every key that changes resolution or execution:
  `package`, `call`, `registry`, `replace-registry-host`, `tag`, `before`,
  `min-release-age`, `offline`, `prefer-offline`, `prefer-online`, `cache-min`,
  `cache-max`, `userconfig`, `globalconfig`, `prefix`, `global`, `location`,
  `workspace(s)`, `include-workspace-root`, `proxy`, `https-proxy`, `noproxy`,
  `strict-ssl`, `ca`, `cafile`, `cert`, `key`, `local-address`, `node-options`,
  `script-shell`, `shell`, `node-gyp`, `ignore-scripts`, `foreground-scripts`, `omit`,
  `include`, `os`, `cpu`, `libc`, `install-*`, `legacy-peer-deps`, `package-lock`,
  `shrinkwrap`, `git`, and `allow-*`. The rest (`all`, `if-present`, `usage`, `version`,
  `versions`, `name`, `password`, `which`, `umask`, `user-agent`, ...) are loud
  **conservatively**: they are not proved harmless for `npm exec`, or they make npm run
  nothing at all. Any key a later npm defines is loud too, because the list is an
  allowlist.

**How the resolver's refusal reasons map onto this.** The warning never classifies a
refusal by its text. Every reason `npm_resolver.py` and `_npm_resolve.js` can return falls
into one of four classes, and each class is re-derived independently:

| class | reasons | re-derived by |
|---|---|---|
| launcher | `command is not a bare npx/npm` | `_npm_identity_refusal_cause` names the launcher (NB-1). The resolver never ran, so the argv and context checks below decide. |
| context | `server env sets [...]`, `npm would set a local prefix at ...`, `gateway environment sets '...'` (sticky, and naming only the **first** key it saw) | `_npm_redirecting_context`, over **every** key and file: harmless keys (dev0's `npm_config_cache`) keep a structurally exact pin silent, and anything else is loud. |
| argv | `config key outside the allowlist`, `--package must name exactly one distinct package`, `no package operand`, `empty package spec`, `package operand starts with "-"`, `npa rejected the spec`, `npa type is not a registry spec`, `npa produced no package name`, `parser threw`, `args is not a list of strings`, the npm-subcommand reasons | The structural read: only `[-y\|--yes\|-q\|--quiet]* <plain registry spec>` is read, which is narrower than every npx pre-scan. Anything else is "cannot verify". |
| infrastructure | missing helper, node not spawnable, cooling down, no/bad handshake, two npm roots, npm root not located, `npx-cli.js` unreadable or hash not recognised, parser not loadable, self-test failed, child died/timed out/malformed/mismatched/unknown status/unusable spec, npm changed under the resolver | These say nothing about what this argv means. The argv and context checks decide. |

The node-less `UNAVAILABLE` state falls back to the flag tables. It yields an identity, so
it takes the identity-mode path, and the context check runs there too.

## Design decisions

### D1. Version grammar: one exact SemVer version, nothing else

A pin is accepted only if `pmcp.validation.is_valid_package_version(raw)` accepts it. That
is SemVer 2.0.0 with ASCII classes and no leading zeros, prerelease and build allowed,
≤256 characters, and the same predicate the provision gate already requires of an argv
pin. The following are refused, fail-soft with a WARNING, and the entry stays unpinned:

| refused | why |
|---|---|
| ranges: `^3.25.5`, `~3.25.5`, `3.x`, `*`, `>=3.25.0` | `npx -y pkg@^3` re-resolves the newest match at every spawn, which is the drift this issue is about. A range "pin" is a pin in name only, and `package_identity._resolve_version` refuses ranges for the same reason. |
| dist-tags: `latest`, `next` | The registry moves them. `@latest` is exactly today's unpinned behaviour, and `_detect_effective_version_pin` already reads it as unpinned. |
| `v3.25.5`, `3.25`, YAML float `3.25`, `true`, `""` | Not one exact SemVer. |
| build metadata: `3.25.5+evil` (rev 2, board N2) | npm ignores `+...` when resolving, so the argv would run `3.25.5` while every report echoed a label that names nothing. |
| a SemVer-shaped tarball: `3.25.5-evil.tgz`, `1.0.0-x.TAR`, `1.0.0-a.tar.gz`, `1.0.0-x.tar-gz` (rev 4, board round 3 B1'; rev 5) | npa checks `isFileType` before it reads a version, so `pkg@3.25.5-evil.tgz` is a **local file**, not a registry version. Refused because `is_valid_package_version`'s exact-version check follows npm's classification (main, #299), so `_is_exact_pin` refuses it too. |
| a core part above 2**53-1: `9007199254740992.0.0` (rev 5) | node-semver refuses it, and npa then reads the selector as a dist-tag. Refused by the same check. |
| anything with a name, space or flag: `evil-pkg@1.0.0`, `npm:evil-pkg@1.0.0`, `3.25.5 --registry=http://evil.test`, `../../tmp/x` | The value is a version and only a version. See D3. |

Validation is **syntactic and offline**. `load_manifest` never touches the network.
Whether `3.25.5` exists is proven later: by npx at spawn (a nonexistent version fails
the spawn loudly, which is the right failure), and by the registry read in
`update_server`.

### D2. Overlay semantics: `version:` on any entry, `server_version:` to patch one

- **`version:`** is an ordinary key on any `servers:` entry, whether shipped or a
  whole-entry overlay replace. It is parsed by `_parse_version_pin(name, raw, "version")`.
- **`server_version:`** is a new top-level overlay map, `{server-name: "X.Y.Z"}`. It is
  the version counterpart of `server_env:`, with the same rules:
  - It patches `version` on a server the manifest **already defines**. An unknown name
    gets a WARNING and is skipped, so the map can never create a server.
  - It is applied per source, **after** that source's whole-entry replaces, so a patch
    can refine an entry the same file just replaced.
  - Precedence is that of the sources: user < project < `$PMCP_MANIFEST_PATH`. A later
    source's whole-entry replace **drops** an earlier source's pin, because whole-entry
    replace means whole entry. This is the same as `server_env` today.
  - It travels in the project overlay's gated bytes. An **unapproved** project overlay's
    `server_version` contributes nothing (test
    `test_unapproved_project_server_version_contributes_nothing`, mutant M5), and an
    approved one applies (`test_approved_project_server_version_applies`).

  This is how an operator pins the built-in firecrawl:

  ```yaml
  # ~/.pmcp/manifest.yaml
  server_env:
    firecrawl:
      FIRECRAWL_API_URL: "http://ai:3002"
  server_version:
    firecrawl: "3.25.5"
  ```

- **Why a new map instead of a partial `servers: firecrawl: {version: ...}`.**
  `servers:` is whole-entry replace. README (`:1096-1099`) already warns that a partial
  `firecrawl:` entry erases the install block and resets `requires_api_key`. Making
  `servers:` merge would change what every existing overlay means. That is a separate,
  larger decision, and it is listed as an open question.

### D3. Materialisation: one post-overlay pass that rewrites the npx package slot everywhere it spawns

`load_manifest` ends with a single pass,
`servers = {name: _materialize_version_pin(entry) ...}`. It runs **after all overlays**
and also for an explicit `manifest_path`. For an entry with `version` set:

1. A remote entry (`url`) is refused, because there is no local client to pin.
2. The command must be npx under `provision_gate._is_npx`, the strict spelling set
   `npx`/`npx.cmd`/`npx.exe` that the pin check itself uses. Otherwise the pin is refused
   with a message that names the escape hatch (D5).
3. `_pin_npx_args(args, version)` finds the slot with the gate's rule (the first argument
   not in `_NPX_LEADING_FLAGS`) and parses it with `validation.parse_package_spec`. It
   keeps the **name** and replaces only the version suffix, so `firecrawl-mcp` becomes
   `firecrawl-mcp@3.25.5` and `@playwright/mcp@latest` becomes `@playwright/mcp@1.2.3`.
   If the slot is missing or not a package spec (`-p x`, `github:x/y`), the pin is
   refused. **Revision 2 (codex P1):** the slot must also be a **plain registry spec**,
   `name` or `name@<exact-version | dist-tag>`, decided by grammar
   (`is_valid_package_version` or `package_identity._DIST_TAG_RE`). An alias
   (`myalias@npm:other@1`), URL, git/`github:`, `file:`/tarball, or range selector is
   refused, because replacing it with `@<version>` would change **which** package runs.
   `myalias@npm:firecrawl-mcp@3.25.5` → `myalias@3.25.5` is the registry package
   `myalias` (measured with the real resolver).
4. The same rewrite is applied to **every** `install[platform]` argv. Each must be npx
   and must name the **same package** as `args`. Otherwise the whole pin is refused, all
   or nothing, because pinning `args` and not `install` "approves X and runs latest".
5. A refusal logs a WARNING, returns the entry unchanged with `version=None`, and so
   leaves the entry **honestly unpinned**. Proposal 2's warning then still fires for it.
   "`version` is set" always means "every spawning argv is pinned". The firecrawl test
   pins that invariant through the gate's own predicate:
   `_config_runs_exactly(pinned, "firecrawl-mcp@3.25.5")`.
   **Revision 3 (board round 2, B1/N4): the allowlist, derived class by class from
   npm-package-arg.** The source is npa as shipped with the host's npm
   (`node_modules/npm-package-arg/lib/npa.js`). npa classifies a spec in a fixed order,
   and only the last branch, `fromRegistry`, fetches `name` from the registry.
   `split_plain_registry_spec(arg)` accepts a slot only if it reaches that branch as a
   `version` or `tag` (or as a bare name, which npa types as the range `*`):

   | npa class (branch, in npa's order) | npa trigger | how `split_plain_registry_spec` treats it |
   |---|---|---|
   | url / git url (`isURL`, whole arg) | `^(?:git[+])?[a-z]+:` | refused: the name half contains `:`, so `parse_package_spec` rejects it |
   | git ssh (`isGit`, whole arg) | `^[^@]+@[^:.]+\.[^:]+:.+$` | refused: the selector contains `:` and is not a tag word |
   | file / directory, unscoped name part (whole arg) | name part has `/` **or `isFileType`** | refused: `/` fails `is_valid_package_name`; **`isFileType` on an unscoped name is refused explicitly (B1)**. A scoped name is exempt, as in npa. |
   | file (`isFileSpec`, selector) | `file:` prefix, or starts with `.`, `~/`, `/` or `X:` | refused: not a version and not a tag word (`:`, `/`, `.`-led, `~`) |
   | alias (`isAliasSpec`, selector) | `npm:` prefix | refused: `:` |
   | hosted git (`HostedGit.fromUrl`, selector) | `github:`, `gitlab:`, `user/repo`, ... | refused: `:` or `/` |
   | remote (`isURL`, selector) | `https:` and similar | refused: `:` |
   | file (`hasSlashes \|\| isFileType`, selector) | any `/`, **or `.tgz`/`.tar`/`.tar.gz`, any case**, including a strict SemVer ending that way (`3.25.5-corp.tgz`) | refused: `/` is not a tag-word char; **`isFileType` on the selector is checked FIRST, before the version branch, in npa's order (B1, then B1' in rev 4)**, and `is_valid_package_version` itself refuses a tarball-shaped version |
   | registry `version` (`semver.valid`, loose) | e.g. `3.25.5` | **accepted** when `is_valid_package_version` (strict SemVer, and never tarball-shaped since rev 4). Loose forms such as `v3.25.5`, `vv1.2.3` and `=3.25.5` are refused, which is conservative. |
   | registry `range` (`semver.validRange`, loose) | `^`, `~`, `>=`, `*`, `x`, `X`, `v1`, `1.x`, ... | **refused**. Symbol forms fail the tag word. **Letter-led forms (`x`, `X`, `v1.2.x`, and since rev 4 any run of `v`/`=`: `vv1`, `vvX`, `v1.X.xbeta`) are refused by the partial-version-word clause (N4, N-a).** A bare name (npa range `*`) is accepted: that is the entry running `name` at latest. |
   | registry `tag` (`encodeURIComponent(spec) === spec`) | anything URI-safe that is not a version or range | **accepted** when it fullmatches the letter-led `[A-Za-z][A-Za-z0-9._-]*` (a subset of npa's tags), and is neither a tarball name nor a partial-version word |
   | invalid (`EINVALIDTAGNAME` / `EINVALIDPACKAGENAME`) | e.g. `latest\n`, `tag!`, and the excluded names `node_modules`/`favicon.ico` in any case | refused: `fullmatch` (N4), `!` is outside the tag word, and the exclusion list is checked explicitly (rev 4, N-c) |

   **Measured against the real npa**, now with a **generated** corpus (rev 4,
   `corpus_conformance.py`, Verification step 10): **1,366,139 slots**, with 0 accepted
   slots that npa does not fetch as the same name from the registry as `version`/`tag`
   (or a bare name). There are also **35,028 pin values**, with 0 accepted that npa does
   not read as exactly that registry `version`. The rev-3 rule had 5,153 and 8 on the
   same corpus. The rev-3 hand-picked 461-slot list reported 0 and missed B1', which is
   why it was replaced. The tests use one case per class (`_NON_PLAIN_SLOTS`, 25 ids) plus 8
   accepted-class controls, because round 2 showed that example lists which only
   use `:`-prefixed forms can't see a letter-led class.
6. **Revision 2 (codex P3): per entry.** The pass calls `_materialize_version_pin_soft`,
   so an exception while reading one entry (a non-string argv element or `command`,
   which overlays can carry because only parsed fields are shape-checked) costs that
   entry its pin, with a WARNING. It never costs the manifest its other entries.

**Revision 4: "one exact version" means one exact *registry* version.** A strict SemVer string can still name a local file to npm (`1.0.0-x.tgz`). The pin grammar uses `is_valid_package_version`, whose check follows npm's classification, so no pin value or pinned argv can name a file.

**Why no redirect is possible.** The pin value passes D1's exact-version grammar (no `@`,
`/`, `:` or whitespace), and the package name always comes from the entry's own argv. A
`server_version` patch can therefore only pick a version of the package the entry
already runs. For honesty: a user-scope `servers:` **whole-entry replace** can still
point a built-in at any command. That is pre-existing, documented (README "Security"
note) and ungated by design, because it is the operator's own file. The invariant here is
about the version path, which adds no new redirect capability.

**Why rewriting argv beats a spawn-time override.** Every consumer already reads
`args`/`install`: `_manifest_server_to_config`, the installer,
`_detect_effective_version_pin`, `_config_runs_exactly`, `_manifest_package_names`
(policy denylist) and `_refresh_config_unchanged`. Rewriting once at load makes all of
them see the pinned spec with **zero** changes to spawn code. Two side effects are
desirable:

- A `.pmcp.json` entry with no `command` inherits the pinned args through
  `_merge_manifest_defaults` (test `test_a_config_entry_without_a_command_inherits_the_pin`).
- Changing a pin changes `args`, so `_refresh_config_unchanged` sees a new config and
  `gateway.refresh` respawns the server onto the new version.

### D4. Security invariants, checked one by one

- **#195 (npm identity refuses what it can't prove).** Nothing in the resolver changes.
  A pinned token is ordinary input to `_npm_package_arg`, measured to resolve IDENTITY
  (research). The materialiser's slot rule is the provision gate's, which is *stricter*
  than npm's parser (it rejects `-p`/`--package` before the slot), so a pin can only be
  applied where the gate would also call the argv pinned.
- **Overlay cannot redirect.** Covered in D3. Tests: the parametrized refusal cases
  `evil-pkg@1.0.0`, `npm:evil-pkg@1.0.0`, `3.25.5 --registry=...`, plus mutants M1 and M3.
- **Credential gates (#124).** Untouched. The warning *reads* `credential_requirement`
  with the resolved config's env as `child_env`, which is the same contract the seven
  gate consumers use (never `os.environ`). `extra_env`, `server_env` and the gates are
  not modified.
- **Project overlay approval.** `server_version` is parsed from the gated bytes only
  (`_parse_overlay_document(overlay_path, content)` after `read_and_gate`). There is no
  new file read. Test and mutant M5.
- **Provision gate.** A manifest entry is `manifest_backed` (rule 2) whether pinned or
  not. `_manifest_package_names` reads the same name from a pinned argv, so a
  `packages.denylist` still binds.

### D5. Non-npm installers: rejected with a clear error, and the existing escape hatch named

| installer | behaviour of `version:`/`server_version:` |
|---|---|
| npx (`npx`, `npx.cmd`, `npx.exe`) | supported (D3) |
| `npm exec`, `cmd /c npx` wrappers | refused: "install command is not npx" / "pins npx-launched servers only" |
| uvx / pip | refused: `'version'/'server_version' pins npx-launched servers only and this one runs 'uvx'; pin a uvx/pip/cargo/docker server with explicit command and args in .mcp.json or .pmcp.json instead` |
| cargo / docker | refused, same message |
| remote (`url`) | refused: "it is a remote server, so there is no local client to pin" |

The escape hatch already works, because `_detect_effective_version_pin` honours uvx
`pkg==X`, cargo `--version X` and docker `:tag`/`@sha256:` in a configured entry's
explicit args. `update_server`'s new "pinned at X, newer available: Y" report (D6) applies
to those pins too, with one limit: for uvx, `detect_package_type` keeps `pkg==X` as the
"name", so the PyPI lookup fails, and the report reads "latest version could not be
determined". That is honest and not wrong. Proper uvx support means PEP 440 grammar plus
`--from` slot handling, and is left to a follow-up if anyone asks.

### D6. How `pmcp update` reports

**`gateway.update_server` (the pinned branch, `handlers.py:5411-5428`).** The existing
refusal stays: `ok=False`, no probe, no restart, and the substring
`is pinned to 'V' in <source>` that `tests/test_pkgid_panel_fixes.py:900` asserts. The
change adds a **registry read**. `get_package_version(command, args, env, cwd,
timeout=5.0)` strips the pin and returns `dist-tags.latest`. Then
`compare_versions(pinned, latest, package_type)` classifies it three ways, never
negated (#164). Three new output fields are set:
`pinned_version`, `latest_available`, and `latest_comparison ∈ {"newer","not_newer","incomparable"} | None`
(`None` = the lookup failed). The message gains one sentence:
`Pinned at 3.25.5, newer available: 3.26.0.` / `Pinned at 3.25.5, up to date.` /
`...; the latest (X) cannot be ordered against it.` / `...; the latest version could not be determined.`

**`pmcp update` (the CLI renderer).** The per-result print moves into a pure
`_format_update_result(item) -> list[str]`. A result with `pinned_version` renders as
`[PINNED] firecrawl: pinned at 3.25.5, newer available: 3.26.0` (or `up to date` /
`latest X cannot be compared` / `latest unknown`) instead of `[FAILED] firecrawl: <long
refusal>`. Other results render exactly as today. Every string in `warnings` prints under
its result as `  warning: <text>`. `--json` gets the new fields for free. The exit code is
unchanged (0), because `run_update` has never exited nonzero on a per-server result.

**Revision 2 (codex P2 / claude F1): a range or tag is FLOATING, not PINNED.**
`_detect_effective_version_pin` answers "does the argv carry any version selector". That
is the right question for the refusal: do not move what the operator chose, and the
refusal is unchanged. It is the wrong question for the label. `_is_exact_pin` decides
the label. An exact pin reports as above. A range or dist-tag (`^3.25.0`, `~3.25.5`,
`next`) returns `floating_selector="^3.25.0"`, `pinned_version=None` and
`latest_comparison=None`, with a message that starts `'fc' is held at '^3.25.0' in ...`
and says `'^3.25.0' is a range or tag, not one exact version: it re-resolves at every
spawn, so it does not hold the client still (latest: 3.26.0).` `pmcp update` prints
`[FLOATING] fc: held at ^3.25.0, a range or tag that re-resolves at every spawn (latest 3.26.0)`
(wording replaced in revision 6, NB-2: see the rev-6 table below). `[FLOATING]` is not a failure, and the exit code stays 0.

**Revision 3 (board round 2, N3): build metadata in a configured argv.** npm and cargo
ignore `+...` when resolving, so for an exact pin the label is **what runs**:
`firecrawl-mcp@3.25.5+evil` reports `pinned_version="3.25.5"`, compares `3.25.5` with
latest, and says `(build metadata '+evil' is ignored by npm)`. A manifest `version:`
with `+` is still refused at load.

**Revision 6 (board round 4, C2/NB-2): "exact" is decided per launcher.** `_is_exact_pin`
dispatches on the package type **before** any rule, and shares no rule across launchers. An
unhandled type is never exact. The same predicate drives the warning, `pinned_version` /
`floating_selector`, and `[PINNED]` / `[FLOATING]`.

| launcher | the pin `_detect_effective_version_pin` returns | exact when | floating text (`_floating_reason`) |
|---|---|---|---|
| npm (`npx`, `npm exec`) | the spec's selector | `is_valid_package_version`: one registry version, never a range, a dist-tag or a tarball-shaped string. `+build` stays exact, because npm runs the version it decorates (D6, N3). | `not one exact version on every npm release` (npa 12 and 13 read `1.0.0-x.tar-gz` differently, so the text claims only what holds on both) |
| docker (`docker run`) | the `@digest` if present, else the `:tag` | a content digest only: `sha256:` + 64 lowercase hex (or `sha384:`/96, `sha512:`/128, the OCI-registered algorithms). **Every tag floats, however version-like**, because a registry can re-point it. | `a docker tag, which can be moved to another image; only an @sha256 digest pins one`; the remedy names `image@sha256:...` |
| pypi (`uvx pkg==X`) | the text after `==` | one PEP 440 version (`_parse_version`) with no `*`. `===X` arrives as `=X`, which is not a version, so it floats. | `not one exact PEP 440 version` |
| cargo (`cargo install --version X`) | X | `is_valid_package_version`. cargo installs exactly X only when X has no operator; `=1.2.3` is a requirement to cargo, so it floats here (conservative: a warning, never silence). | `cargo reads it as a version requirement, not one exact version` |
| anything else | n/a | never | n/a |

`update_server`'s floating message now reads `'X' does not hold the client at one version
(<floating text>): a later spawn can run another one (latest: Y).`, and `pmcp update`
prints `[FLOATING] fc: held at ^3.25.0, which is not one exact version and can resolve to
another at a later spawn (latest 3.26.0)`. Revision 5 checked `is_valid_package_version`
first for every type. That made a docker tag `3.25.5` exact (C2), and also pypi
`1.0.0-x.tgzx` (not PEP 440) and an unknown type's `3.25.5`.

### D7. The unpinned-self-hosted warning (proposal 2)

`_unpinned_self_hosted_warning(server_name, manifest_server, resolved)` in `handlers.py`
is a pure function. It fires only when **both** of these hold:

1. **The relaxer is active on the child env.** That is
   `credential_requirement(manifest_server, child_env=resolved.config.env).relaxed_by`.
   For a manifest-only server, `resolved` is `manifest_server_to_config(...)`, whose env
   carries `extra_env` (including `server_env` patches). For a configured entry it is
   the merged config, so an entry that blanks `FIRECRAWL_API_URL` is judged on the blank
   value.
2. **The spawning argv is unpinned** by the *same* `_detect_effective_version_pin` that
   `update_server` uses, so the two can never disagree. `@latest` counts as unpinned.

It judges the **effective** config (configured over manifest), so the
ViperJuice/dotfiles#325 shape (`.pmcp.json` with explicit `firecrawl-mcp@3.25.5`) does
**not** warn, and `~/.pmcp.json`'s current `firecrawl-mcp@latest` **does**. The remedy
text depends on the source. A manifest-sourced npm entry is told
`pin it with \`server_version: {firecrawl: <version>}\` in ~/.pmcp/manifest.yaml`. Anything
else is told to pin it in the args of the config that launches it.

**Revision 2 changes to the predicate:**

- **Exactness (codex P2 / claude F1).** The argv counts as pinned only if
  `_is_exact_pin(package_type, pin)`: `is_valid_package_version(pin)` in every
  ecosystem, a docker `sha256:` digest (immutable), or a PyPI `==X` with no wildcard.
  A bare spec, `@latest`, a range or another dist-tag all warn. A non-exact selector is
  named in the text: `floats on '^3.25.5' (a range or tag, not one exact version)`.
  (Revision 6: exactness is decided per launcher, so a docker tag floats (C2), and the
  text names each launcher's reason (NB-2). See the D6 rev-6 table.)
- **Inherited environment (claude F2).** The relaxer is judged on
  `sanitized_subprocess_env(resolved.config.env, project_root)`, which is exactly what
  `client/manager.py:2409` spawns the child with. So a `FIRECRAWL_API_URL` exported in the
  shell that started pmcp counts. This is advisory only: every credential **gate** keeps
  `child_env=config.env` and is not touched (for a gate, ignoring the ambient value is
  the safe direction, #124), and `tests/test_credential_predicate_guard.py` stays green.
- **Health cost (claude F3).** Health judges the config the gateway **connected** with
  (`ClientManager.get_connected_configs()`, an existing public method), so there is no
  config-file I/O. The relaxer-declaring manifest entries are cached per `GatewayTools`
  and keyed by `manifest_sources_fingerprint()` (`stat` only), so the manifest is
  re-loaded only when a source or the trust store changes. A server that is not
  connected is not judged by health; `update_server` still judges it, on a fresh
  resolution.

**Revision 3 change (board round 2, N2): npm identity disabled.** When
`detect_package_type` refuses an **npx** launch (identity disabled or refused, #195),
the warning does not go silent. It reads the slot with `provision_gate._package_slot`
and `split_plain_registry_spec`, the grammar the materialiser uses. An exact pin is
silent. `@latest`, a bare spec, a range or a tag warns, with `(read from the argv: npm
package identity is unavailable)`. A slot the grammar can't name at all warns
`pmcp cannot verify that its client is pinned ... (see gateway_diagnostics.npm_identity)`.
Any other command with unknown identity stays silent, as before. *(Superseded by revisions
10-11: see the Revision 11 section.)*

**Revision 4 (board round 3, N-b/N-d).** The whole npm family fails loud: an `npm` launcher (for example `npm exec ...`) has no structural slot pmcp reads, so it warns `cannot verify ... pmcp reads only an npx argv structurally, and this one launches with npm`. Every fallback message names the **actual** cause, via `_npm_identity_refusal_cause()` (`status_summary()`, which never spawns). `active` gives `npm's own parser did not identify a registry package in this argv`. Anything else gives `npm package identity is unavailable (gateway_diagnostics.npm_identity: <status>)`.

**Revision 6 (board round 4, C1/NB-1): an exact argv is a pin only when nothing else can
redirect it.** For npm, `_is_exact_pin` on the argv's selector is now **necessary, not
sufficient**. Before suppressing, the warning asks `_npm_redirecting_context(command,
package, config.env, config.cwd, child_env)` whether anything besides the argv can change
what npm runs for that spec. It asks in **both identity modes**, and it computes the answer
itself from the inputs it holds, never from the resolver's refusal reason or status. The
same context gave three different status strings depending on whether an earlier lookup
had started the child. The sticky DISABLED reason also names only the first key it saw.

The sources and the key allowlist are derived in Research (*What can change what a
registry spec runs*). If any source can redirect, the warning names it and says it cannot
verify the pin:

`'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify
that its client is pinned: the argv pins fc-mcp@3.25.5, but npm_config_package is set in
the client's environment, which can change what npm runs for that spec. Remove it, or
launch the client where it does not apply, to hold the client at the pinned version.`

dev0's `npm_config_cache` and `npm_config_store_dir` are on the allowlist, so the
structural read of revision 3 (N2) still keeps an exact pin silent there. That read was
the reason for N2. An unpinned or floating argv warns as before; the context check runs
only when the warning would otherwise go silent. The check applies to the npm family
only. For uvx/pip and cargo, pmcp does not read the package index configuration (see R8).

The **cause** in a fallback message is keyed on the launcher first (NB-1). For a command
identity does not read (`npx.cmd`, `/usr/bin/npx`, `C:\tools\npx.cmd`, `npm.cmd`), it is
`pmcp's npm identity check reads only a bare \`npx\`/\`npm\` command, and this one is
'npx.cmd'`. The resolver was never asked, so its status says nothing about this argv.
For such a launcher, the context check is the only context gate. That is one more reason
it must not depend on the resolver.

**Revision 7 (board round 5, maintainer decision): the entry-controlled boundary.** The
revision-6 paragraph above is superseded where it reads host state. The suppression rule
is now: the argv's selector is exact for its launcher (`_argv_pin_is_exact`, which adds
the PEP 508 check for uvx), **and** `_entry_redirect(package_type, command, args, package,
config.env)` is `None`. That function judges only what the entry controls:

- **the entry's env block** (`config.env`, including an overlay's `server_env`), per
  launcher family, with small allowlists (Revision 7 table);
- **a `cwd` the entry sets**, for npm/npx, uvx and cargo, whose resolution reads
  cwd-relative project configuration. docker is exempt. An inherited cwd (none set) is
  the host's;
- **argv flags** for uvx (up to the command) and cargo (anywhere), with small allowlists.
  npm's argv is read by the structural slot rule, and a docker digest is
  content-addressed;
- **the launcher**: a launcher whose argv pmcp reads (npm family, `uvx`, `pip`, `pip3`,
  `cargo`, `docker`) with an argv it cannot read fails loud (`cannot read which package
  this ... argv runs`).

Anything the host provides (its environment, npmrc/uv/cargo/docker configuration files,
shims, caches, global bins, proxy/CA settings) is trusted and not read. The warning for a
redirect reads: `'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp
cannot verify that its client is pinned: the argv pins npm:fc-mcp at 3.25.5, but the
entry's env sets npm_config_package, which can change what runs for that pin. Remove it
from the entry to hold the top-level package at the pinned version.` The same rule applies
to every launcher, so uvx and cargo pins are judged too (B2).

**Revision 8 (board round 6): allowlists.** The revision-7 paragraph above keeps its
boundary (the entry's argv, env, cwd and launcher; the host is trusted), but both of its
rules become allowlists. `_entry_redirect(package_type, command, args, package, pin, env,
cwd, declared)` returns `None` only when:

- `_argv_shape_problem` recognises the argv as a shape in which the pinned package runs
  (the Revision 8 table);
- every key of the entry's env block is `_entry_env_key_is_inert` (the server's declared
  keys outside tool namespaces, locale/terminal keys, and the per-launcher
  logging/timing/credential lists), with no value special-cased;
- no cwd is set that the launcher reads configuration from.

The unknown-identity branch makes unmodelled runners and wrappers loud, and `uv tool run`
is rewritten to uvx before detection. `update_server` applies the same shape check before
it labels a pin `[PINNED]`. **What a docker digest pins:** the image, not the command,
entrypoint, mounts or env that decide what runs in it. So the docker shape forbids all of
them, except `-e` with an inert key.

**Revision 9 (board round 7).** Two changes to the revision-8 rule above. (a) For a
manifest-sourced server, `_install_argv_problem` judges every non-empty `install[platform]`
argv by the same rules and requires the same package and pin (the
`_config_runs_exactly` rule), because `gateway.provision` spawns and adopts that process.
The same check gates `[PINNED]`. (b) `declared` comes from `_declared_env_keys(server_name)`,
the **shipped** manifest's declarations for that name, never from the entry, an overlay or
a config file. The namespace guard is removed.

**Revision 10 (board round 8).** The warning first computes `_spawn_set(manifest_server,
resolved)`, every argv that can spawn or be adopted as the server (`_SERVER_SPAWN_SITES`),
and judges every member before any exemption. On the exact-pin path, every non-args member
must run the same pin. The local-binary exemption applies only when every member is
literally `[command, *args]`. `update_server` walks the same set before `[PINNED]`.

**Revision 11 (board round 9).** The rule is now one judge (`_judge_spawn_set`) over the
spawn set. Each member is read by the one parser for its launcher (`_read_argv`). Then
every member's shape, pin, env and cwd are checked before any verdict is chosen. The
local-binary exemption waives only the pin. The warning maps the verdict (`silent`,
`unpinned`, `floating`, `cannot_verify`) to its message. `update_server` labels
`[PINNED]` only from a `silent` verdict whose selector is the pin main's reader found.

**Revision 12 (board round 10).** `_read_argv(argv, local_commands)` returns `local` only
for a command in `local_commands`: `_shipped_local_commands(server_name)`, the exact
command pmcp's shipped manifest names. Any other unmodelled command is `unrecognised`
("cannot verify"), and so is a known launcher or runner name carrying an unstripped
extension. The warning and `update_server`'s label pass the shipped commands to
`_judge_spawn_set`.

**Revision 13 (board round 11).** The relaxer that makes the warning apply is the first
of `_warning_relaxers(server_name, manifest_server)` (the shipped keys ∪ the overlay's)
whose value in the child's environment is usable. `_version_pin_warning` and
`_relaxable_manifest_servers` gate on the same function. `_read_argv(argv,
local_commands, path_var)` reads a modelled launcher only when
`_launcher_spelled_as_itself`, with `path_var` the child environment's `PATH`.

**Revision 14 (board round 12).** `_launcher_spelled_as_itself` refuses a relative path
before any comparison. `_member_problems(..., path_var)` makes docker with an entry-set cwd
loud when `_path_searches_the_cwd(path_var)` (a relative or empty `PATH` entry, or
Windows).

**Revision 15 (board round 13).** `_launcher_spelled_as_itself` compares an absolute path
literally with `shutil.which`'s result. `_is_bare_launcher` gates docker's cwd exemption.
`_path_searches_the_cwd` counts `/proc` entries and is false on Windows (the parent's cwd
and `PATH` are searched there). `_read_argv` refuses cmd metacharacters for a Windows `.cmd`
launch. (Revision 15 read docker's relaxer from `_docker_container_env`; revision 16
deletes it.)

**Revision 16 (board round 14).** `_read_docker` is the one docker reader. It parses pflag
shorthand clusters (`_docker_short_cluster`) and returns `_Reading.container_env`.
`_member_relaxer` decides, per `_spawn_set` member and from that member's `_read_argv`
reading, whether the client talks to a self-hosted backend: the container's env for a clean
docker reading, "may" for a docker reading with a problem, the member's own env otherwise.
`_unpinned_self_hosted_warning` applies if any member applies, then judges the same spawn
set with `_judge_spawn_set`.

**Revision 22 (board round 20).** Every diagnostic the parser, the per-member judge and
the warning build is fixed text, a value through a closed grammar (`diag_name`,
`diag_flag`, `diag_selector` in `loader.py`), or the one renderer; the interpreter message
names only the matched interpreter; a cwd path is never echoed; `update_server`'s label text
and the loader's pin logs use the same grammars.

**Revision 21 (board round 19).** `_read_uvx` also refuses when the PARSED requirement
name (`Requirement(...).name.lower()`) is an interpreter prefix, and the text rule skips
leading whitespace as uv does. `update_server`'s `[PINNED]` label carries only the judge's
selector (`judge_pin`); main's `_detect_effective_version_pin` only decides that the tool
stops.

**Revision 20 (board round 18).** `_read_uvx` refuses an interpreter request
(`_uv_interpreter_request`: uv's `try_from_tool_name` prefixes `python`, `pythonw`, `cpython`,
`pypy`, `graalpy`, `pyodide`, case-insensitive, alone or with a version request) before any
requirement parsing, for `uvx`, `--from` and `uv tool run`.

**Revision 19 (board round 17).** `_judge_members` is the one place member problems are
computed (`_Judged`). `_verdict_of` combines them into the verdict, and `_member_relaxer(judged,
…)` reads the same objects: any problem makes the relaxer "may", and `_may_talk_reason` names
it. `_judge_spawn_set` is `_verdict_of(_judge_members(...))`.

**Revision 18 (board round 16).** In `_member_relaxer`, any reading with a problem, for any
launcher, returns "may" (plus, for a non-docker member, its own env's relaxer key). The
warning's "may" head names the first such member through `_Spawn.where()`.

**Revision 17 (board round 15).** `_member_relaxer(..., declared, path_var)` uses the
container env alone only when the member's `_member_problems` is empty; otherwise the
process env counts too. A launcher's identity is trusted only after the per-member judge.

The warning appears in two places:

- **`gateway.health`.** `ServerHealthInfo.warnings: list[str]` (default `[]`) is filled by
  `_attach_version_pin_warnings(servers)` just before diagnostics. The cost, **as of
  revision 2 and measured** (21 calls, an unapproved project overlay present): one
  `load_manifest()` per change of `manifest_sources_fingerprint()`, not per call, and
  then per connected relaxer-declaring server one `sanitized_subprocess_env` plus one
  resolver query. That is 174 ms on the first call and 1.3 ms steady, with 1 WARNING
  line in total. Revision 1 measured 220.7 ms steady and 42 WARNING lines. It is
  wrapped in `try/except`, logged at DEBUG, and never costs health its answer.
- **`gateway.update_server`.** The public method becomes a thin wrapper around the
  unchanged body (renamed `_update_server_unwarned`). It appends the warning computed on
  the configuration **after** the attempt. The warning never changes `ok` and never
  blocks the update. An unpinned self-hosted client is still updated, and still warned
  about, because it is still unpinned. `pmcp update` prints it under the result (D6).

## Changes

### `src/pmcp/manifest/loader.py` (modify)

| entity | action | reason |
|---|---|---|
| imports | add `from pmcp.validation import is_valid_package_version, parse_package_spec` | D1 grammar, D3 slot parse (validation imports nothing from pmcp, so there is no cycle; measured by mypy + the suite) |
| `ServerConfig.version: str \| None = None` | add, with a 5-line comment | the pin, `None` = unpinned, including a refused pin |
| `_parse_version_pin(name, raw, field_label)` | add | D1, fail-soft WARNING that names the field (`version` / `server_version`) |
| `_pin_npx_args(args, version)` | add | D3 step 3. Local import of `provision_gate._NPX_LEADING_FLAGS` (provision_gate imports `ServerConfig` under `TYPE_CHECKING` only, but a local import keeps the module graph as it is) |
| `_materialize_version_pin(server)` | add | D3 steps 1-5, all or nothing |
| `split_plain_registry_spec(arg)` + `_NPM_FILE_TYPE_RE` (an alias of main's `validation.NPM_FILE_TYPE_RE`, imported, never restated), `_NPM_EXCLUDED_NAMES`, `_TAG_WORD_RE`, `_PARTIAL_VERSION_WORD_RE` (rev 3; rev 4 re-orders the selector file check before the version branch, adds the exclusion names, and widens the range word to `[vV=]*`) | add; `_pin_npx_args` calls it for the slot | D3 class table: codex P1, board rounds 2-3 (B1, B1', N4, N-a, N-c) |
| `_materialize_version_pin_soft(server)` (rev 2) | add; the load pass calls it | D3 step 6, codex P3 |
| `manifest_sources_fingerprint()` (rev 2) | add, public, `stat` only | D7 health cache, claude F3 |
| `_parse_version_pin` (rev 2) | also refuse `+` build metadata | D1, claude N2 |
| `_parse_server_config` | add `version=_parse_version_pin(name, data.get("version"), "version")` | D2 `version:` key |
| `_OverlayDocument` | widen to a 4-tuple `(servers, clis, server_env, server_version)` | D2. The only callers are `_load_overlay_file` and `load_manifest` (grep: no test imports it) |
| `_load_overlay_file`, `_parse_overlay_document` | return 4-tuples; parse `server_version:` (a mapping of non-empty str → `_parse_version_pin`; a non-mapping gets a WARNING); docstring paragraph | D2 |
| `load_manifest` | unpack 4-tuples in both branches; include the server_version count in the "Applying manifest overlay" INFO line; apply `server_version` per source after `server_env` (unknown name → WARNING, skip); **after the overlay loop, outside `if apply_overlays`**, the materialisation pass | D2/D3 |

### `src/pmcp/types.py` (modify)

| entity | action | reason |
|---|---|---|
| `ServerHealthInfo.warnings: list[str] = Field(default_factory=list)` | add | D7. The same `default_factory` shape as `missing_env_vars` |
| `UpdateServerOutput.pinned_version`, `.latest_available: str \| None = None`, `.latest_comparison: Literal["newer","not_newer","incomparable"] \| None = None`, `.warnings: list[str]` | add | D6/D7 |
| `UpdateServerOutput.floating_selector: str \| None = None` (rev 2) | add | D6 FLOATING, codex P2 |

### `src/pmcp/tools/handlers.py` (modify)

| entity | action | reason |
|---|---|---|
| imports | add `compare_versions` (version_checker) and `credential_requirement` (manifest.loader) | D6/D7 |
| `_unpinned_self_hosted_warning(server_name, manifest_server, resolved, project_root)` (module level, after `_detect_effective_version_pin`) | add | D7 (rev 2: judges the relaxer on `sanitized_subprocess_env`, and suppresses only on `_is_exact_pin`) |
| `_is_exact_pin(package_type, pin)` (rev 2) | add | D6/D7, codex P2 |
| identity-disabled fallback in `_unpinned_self_hosted_warning` (rev 3; rev 4) | add: the npm family by `normalized_executable_name`; npx is read via `provision_gate._package_slot` + `split_plain_registry_spec`, and `npm` fails loud (imports added) | D7, board round 2 N2, round 3 N-b |
| `_npm_identity_refusal_cause()` (rev 4) | add | D7, round 3 N-d |
| pinned branch: `build_note` (rev 3) | add: strip `+...` from an exact npm/cargo pin before comparing and labelling | D6, board round 2 N3 |
| imports (rev 2) | add `_parse_version` (version_checker) and `manifest_sources_fingerprint` (manifest.loader) | `_is_exact_pin`, the health cache |
| `GatewayTools._relaxable_cache`, `_relaxable_manifest_servers()` (rev 2) | add | D7 health cache, claude F3 |
| `health` | call `self._attach_version_pin_warnings(servers)` before the diagnostics block | D7 |
| `_version_pin_warning(server_name)`, `_attach_version_pin_warnings(servers)` (methods, before `_config_source_paths_by_server`) | add | D7. Rev 2: health judges `get_connected_configs()`, not `load_configs()` |
| `update_server` | becomes a wrapper that **keeps the full contract docstring** (plus one #294 paragraph) **and validates with `UpdateServerInput.model_validate` itself** (rev 5, required by #300's handler-validates test); the body moves to `_update_server_unwarned(parsed: UpdateServerInput)` with a one-line pointer docstring and is otherwise unchanged except for the pinned branch | D7. `tests/test_tools.py::test_update_server_docstring_states_both_probe_window_env_contracts` reads `GatewayTools.update_server.__doc__` (measured: moving the docstring turns it red) |
| pinned branch of the body (`if pinned_to is not None:`) | add the registry read + `compare_versions`, set the three fields, and add the availability sentence to the message. Rev 2: `exact = _is_exact_pin(...)`; a non-exact selector sets `floating_selector`, and the message says `is held at` | D6 |
| `_is_exact_pin(package_type, pin)` (rev 6) | **rewrite** as a per-launcher dispatch with no shared rule, plus the module regex `_DOCKER_DIGEST_RE`: npm and cargo `is_valid_package_version`, docker a content digest only, pypi PEP 440 without `*`, anything else `False` | D6 rev-6 table, board round 4 C2 |
| `_floating_reason(package_type)` (rev 6) | add; used by the health warning's `floats on` text and by `update_server`'s floating sentence | D6, round 4 NB-2 |
| `_NPM_KEYS_THAT_CANNOT_REDIRECT`, `_npm_env_config_key`, `_npm_key_can_redirect`, `_npmrc_redirecting_key`, `_npm_config_files`, `_npm_redirecting_context` (rev 6) | add (module level, before `_npm_identity_refusal_cause`). A local import of `npm_resolver._gate_relevant_env` and `_has_local_prefix` (identity's own definitions, reused) | Research *What can change what a registry spec runs*, D7, round 4 C1 |
| `_npm_identity_refusal_cause(command)` (rev 6) | takes the command; a non-bare launcher names itself before the resolver is consulted | D7, round 4 NB-1 |
| `_unpinned_self_hosted_warning` exact branch (rev 6) | an exact npm pin is silent only when `_npm_redirecting_context(...)` is `None`, else "cannot verify ... <source>"; the docker remedy names a digest; the `floats on` text uses `_floating_reason` | D7, round 4 C1/C2/NB-2 |
| pinned branch of the body: the floating sentence (rev 6) | `'X' does not hold the client at one version (<reason>): a later spawn can run another one (latest: Y).` | D6, round 4 NB-2 |
| rev-6 npm host discovery (`_NPM_KEYS_THAT_CANNOT_REDIRECT`, `_npmrc_redirecting_key`, `_npm_config_files`, `_npm_redirecting_context`) (rev 7) | **remove** | Revision 7, trust boundary (round 5 B1) |
| `_ENTRY_ENV_REDIRECTING_KEYS`, `_ENTRY_ENV_FAMILY_KEYS`, `_NPM_ENTRY_KEYS_THAT_CANNOT_REDIRECT`, `_UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT`, `_CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT`, the uv/cargo credential regexes, `_entry_env_key_can_redirect`, `_UVX_FLAGS_THAT_CANNOT_REDIRECT`, `_CARGO_FLAGS_THAT_CANNOT_REDIRECT`, `_argv_redirecting_flag`, `_entry_redirect` (rev 7) | add; `_npm_env_config_key` and `_npm_key_can_redirect` (now against the 17-key entry allowlist) are kept | Revision 7 table, D7 rev 7 (C1 within the entry, B2, N1, N3, N4) |
| `_uvx_requirement_is_exact(args)`, `_argv_pin_is_exact(package_type, pin, command, args)` (rev 7) | add; used by the warning and by `update_server`'s `exact` | N5 (a URL requirement is never exact) |
| `_DOCKER_DIGEST_LIKE_RE`; `_floating_reason(package_type, pin="")` (rev 7) | add; a malformed digest gets its own label | N5 |
| `_READ_LAUNCHERS` and the unreadable-argv branch of `_unpinned_self_hosted_warning` (rev 7) | add | N5 (docker/uvx match the npm family's fail-loud) |
| `_entry_redirect(..., cwd=None)` and `_CWD_CONFIG_FAMILIES` (rev 7, cwd ruling) | an entry-set `cwd` is "cannot verify" for npm/uv/cargo; the warning passes `config.cwd` | Revision 7 table, **cwd** row |
| rev-7 denylists (`_ENTRY_ENV_REDIRECTING_KEYS`, `_ENTRY_ENV_FAMILY_KEYS`, `_entry_env_key_can_redirect`, `_UVX_FLAGS_THAT_CANNOT_REDIRECT`, `_CARGO_FLAGS_THAT_CANNOT_REDIRECT`, `_argv_redirecting_flag`) (rev 8) | **remove** | Revision 8, round 6 root cause |
| `_ENTRY_ENV_INERT_KEYS`/`_PREFIXES`, `_TOOL_ENV_PREFIXES`/`_NAMES`, `_in_tool_namespace`, `_declared_env_keys`, `_entry_env_key_is_inert` (rev 8) | add: the env allowlist | Revision 8 (1): X2, N1, N4 |
| `_NPX_INERT_FLAGS`, `_UVX_INERT_*`, `_CARGO_INERT_*`, `_DOCKER_INERT_*`, `_split_flag`, `_npx_shape_problem`, `_uvx_shape_problem`, `_cargo_shape_problem`, `_docker_shape_problem`, `_argv_shape_problem` (rev 8) | add: the argv shape allowlist | Revision 8 (2): X1, N3 |
| `_entry_redirect(package_type, command, args, package, pin, config_env, cwd, declared)` (rev 8) | shape first, then every env key against the allowlist, then cwd | Revision 8 |
| `_UNMODELLED_RUNNERS`; the `uv tool run` rewrite in `_unpinned_self_hosted_warning` (rev 8) | add | Revision 8 (3): N2 |
| `update_server` pinned branch: `shape_problem` (rev 8) | an exact selector in an unrecognised shape is `floating_selector`, and the reason is the shape problem | Revision 8: X1 in the report |
| `_TOOL_ENV_PREFIXES`, `_TOOL_ENV_NAMES`, `_in_tool_namespace`; `_declared_env_keys(server)` (rev 9) | **remove**; `_declared_env_keys(server_name)` reads `_shipped_manifest_declarations()` (the packaged `manifest.yaml`, `lru_cache`d) | Revision 9 (2): B2 |
| `_read_spawn_pin`, `_install_argv_problem` (rev 9) | add; called by the warning and by `update_server` for a manifest-sourced server | Revision 9 (1): B1 |
| `_NPX_INERT_FLAGS` gains `--yes=true`; the docker shape accepts combined inert short flags (`-it`) (rev 9) | modify | Revision 9 (3): N2 |
| `_SERVER_SPAWN_SITES`, `_Spawn`, `_spawn_set` (rev 10) | add; `_install_argv_problem(members, package_type, package, pin, declared)` walks the spawn set; the warning's local-binary branch requires every member to equal `[command, *args]`; `update_server` passes `_spawn_set(...)` | Revision 10: B-1 |
| `_argv_shape_problem`, the four `_*_shape_problem`s, `_entry_redirect`, `_read_spawn_pin`, `_install_argv_problem`, `_uvx_requirement_is_exact`, `_argv_pin_is_exact`, `_npm_identity_refusal_cause`, `_READ_LAUNCHERS` (rev 11) | **remove** | replaced by the one parser and one judge |
| `_Reading`, `_read_argv`, `_read_npx_slot`, `_read_npm`, `_read_uvx`, `_read_cargo`, `_read_docker`, `_npm_range_selector`, `_combined_short_flags`, `_render_argv`, `_safe_token` (rev 11) | add: the one parser per launcher and the one renderer | Revision 11 (1), (3) |
| `_member_problems`, `_Verdict`, `_judge_spawn_set`, `_unverifiable_warning`; `_args_member`, `_install_members`; `_SERVER_SPAWN_SITES` maps sites to builders (rev 11) | add: the one judge; per-server containment in `_attach_version_pin_warnings` and the `update_server` wrapper; `update_server`'s label from the judge | Revision 11 (2), (4), (5) |
| imports (rev 11) | add `NPM_FILE_TYPE_RE`, `parse_package_spec` (validation), `_docker_image_name` (version_checker), `_operator_safe` (installer); drop `_package_slot` | the parsers and the renderer |
| `_shipped_manifest_commands`, `_shipped_local_commands`, `_MODELLED_LAUNCHERS`; `_read_argv(argv, local_commands)`; `_judge_spawn_set(..., local_commands)` (rev 12) | add: positive evidence for the local-binary exemption; the `unrecognised` family | Revision 12: B-1 |
| `_install_members` (rev 12) | a string-valued install argv is one member, not a character tuple | Revision 12: N-2 |
| `_shipped_manifest_relaxers`, `_warning_relaxers`; `relaxed_by` computed from them in `_unpinned_self_hosted_warning`; `_version_pin_warning` and `_relaxable_manifest_servers` gate on them; `credential_requirement` import dropped (rev 13) | add/modify | Revision 13: blocking |
| `_launcher_spelled_as_itself`; `_read_argv(..., path_var)`; `_judge_spawn_set(..., path_var)`; the warning and `update_server` pass the child environment's `PATH` (rev 13) | add/modify | Revision 13: launcher evidence |
| `_launcher_spelled_as_itself` refuses a relative path; `_path_searches_the_cwd`; `_member_problems(..., path_var)` (rev 14) | modify/add | Revision 14: B-1, N-2, N-3 |
| `diag_name`, `diag_flag`, `diag_selector` (loader.py); every parser/judge/warning diagnostic through them or `_render_argv`; `_uv_interpreter_request` returns the matched name; cwd not echoed; loader pin logs sanitised (rev 22) | add/modify | Revision 22: B1, N1 |
| `_read_uvx` checks the parsed requirement name too; `_UV_VERSION_REQUEST_RE` skips leading whitespace; `update_server`'s label from `judge_pin` only (rev 21) | modify | Revision 21: B1, (b) |
| `_UV_INTERPRETER_PREFIXES`, `_UV_VERSION_REQUEST_RE`, `_uv_interpreter_request`; `_read_uvx` refuses an interpreter request (rev 20) | add/modify | Revision 20: codex B1 |
| `_Judged`, `_judge_members`, `_verdict_of`, `_may_talk_reason`, `_Reading.env_file`; `_member_relaxer(judged, relaxers, project_root)`; the warning judges once (rev 19) | add/modify | Revision 19: B-1, N-3 |
| `_member_relaxer`: any reading with a problem is "may"; the warning names the unreadable member (rev 18) | modify | Revision 18: B-1 |
| `_member_relaxer` takes `declared` and `path_var` and uses the container env alone only when `_member_problems` is empty (rev 17) | modify | Revision 17: B-1 |
| `_docker_short_cluster`, `_Reading.container_env`, `_member_relaxer`; `_read_docker` returns the container env; the warning's applies decision per spawn member; `_docker_container_env`, `_DOCKER_ENV_FLAGS` and `_DOCKER_INERT_SHORT` removed (rev 16) | add/modify/remove | Revision 16: B-1, N-1, N-2 |
| `_is_windows`, `_is_bare_launcher`, `_CMD_SAFE_ARG_RE`, `_docker_container_env` (removed in rev 16); `_launcher_spelled_as_itself` compares literally; `_path_searches_the_cwd` counts `/proc` and is false on Windows; docker relaxer from the container env (rev 15) | add/modify | Revision 15: B-1, N-1, N-2, N-3 |
| `from dataclasses import dataclass` (rev 10) | add import | `_Spawn` |

### `src/pmcp/cli.py` (modify)

| entity | action | reason |
|---|---|---|
| `run_update` print loop | `for line in _format_update_result(item): print(line)` | D6 |
| `_format_update_result(item)` (after `run_update`) | add | D6. Pure, so it is unit-tested without a gateway. Rev 2: the `[FLOATING]` branch |

### `tests/test_pkgid_panel_fixes.py` (modify): keep the pinned-refusal test offline

| entity | action | reason |
|---|---|---|
| `test_update_server_still_refuses_a_pinned_manifest_server_as_before` | add a `monkeypatch.setattr(handlers_module, "get_package_version", no_registry)` stub returning `(None, "npm")` before the call | The pinned branch now makes a **registry read** (D6). Without the stub, this pre-existing test makes a real HTTP request to registry.npmjs.org for `@shipped/server`. That can take up to the 5 s timeout, and it is swallowed to `None`, so it would never go red: a silent hermeticity regression (the Consiliency/pmcp#235 class). The refusal path was offline before this diff and must stay offline. |

**Measured** with a no-network plugin (Verification step 5b) over every test file that
drives `update_server` or `health` (19 files):

- HEAD: 0 registry lookups.
- The spike without this stub: exactly **1** offender, this test (`1 failed, 857 passed`).
- The spike with the stub: 0. Revision 2 re-measured this over the same 19 files: `878 passed, 1 deselected`; revision 3: `910 passed, 1 deselected`.

The pinned-refusal tests in `tests/test_tools.py` (`test_update_server_refuses_pinned_configured_override`,
`..._pinned_docker_tag`) already stub `get_package_version` and need no change. The stub
is included in the production diff above.

### `tests/test_version_pin.py` (create)

153 tests (rev 6; 112 in rev 5, 107 in rev 4, 89 in rev 3, 57 in rev 2, 37 in rev 1). Rev 6 adds 41 cases for board round 4: C1 (27: 6 redirecting environment keys, 2 overlay keys and 5 npmrc/local-prefix sources, each in both identity modes, plus the key-classification unit test), the C1 control (2), C2 (3), NB-1 (8) and NB-2 (1). It also adds an autouse fixture that removes ambient `npm_config_*`/`NODE_OPTIONS`, so a host's own npm configuration cannot decide a test, and it updates the expected floating text in two existing tests. Rev 5 adds main's two stricter rules as fixed cases: 2 pin values and 3 slot ids. Rev 4 adds 4 tarball-shaped pin values, 11 generated-class slot ids (SemVer tarballs, `vv` ranges and versions, excluded names), the tarball predicate/gate test, and the two health tests for tarballs and `npm exec`. The parametrized sets are: 17 version refusals, **25 npa-class non-plain slots**, 8 accepted-class controls, 3 malformed shapes and 4 floating specs. It also has 2 fingerprint tests, 3 identity-disabled tests and 1 build-metadata label test. Body verbatim below.

### Production diff (spike, verbatim; apply as-is)

The diff is against `959d4d4` (rev 5; earlier revisions were against `9ca081e`), already `ruff format`-clean.

```diff
diff --git a/src/pmcp/cli.py b/src/pmcp/cli.py
index 74ced0d..f3625d8 100644
--- a/src/pmcp/cli.py
+++ b/src/pmcp/cli.py
@@ -1005,15 +1005,52 @@ async def run_update(args: argparse.Namespace) -> None:
             return
 
         for item in results:
-            ok = bool(item.get("ok"))
-            status = "OK" if ok else "FAILED"
-            server = item.get("server", "unknown")
-            message = item.get("message", "")
-            print(f"[{status}] {server}: {message}")
+            for line in _format_update_result(item):
+                print(line)
     finally:
         await client_manager.disconnect_all()
 
 
+def _format_update_result(item: dict[str, object]) -> list[str]:
+    """Render one gateway.update_server result for `pmcp update`.
+
+    A pinned server was deliberately not moved, so it is ``[PINNED]`` with
+    its availability line, not ``[FAILED]`` (Consiliency/pmcp#294). A server
+    held at a range, a dist-tag or a docker tag was not moved either, but it
+    is not pinned -- it can resolve to another artifact at a later spawn -- so
+    it is ``[FLOATING]`` (#295 board).
+    Warnings are advisory and printed under the result whatever its status.
+    """
+    server = item.get("server", "unknown")
+    pinned = item.get("pinned_version")
+    floating = item.get("floating_selector")
+    if floating:
+        latest = item.get("latest_available") or "unknown"
+        lines = [
+            f"[FLOATING] {server}: held at {floating}, which is not one exact "
+            f"version and can resolve to another at a later spawn (latest {latest})"
+        ]
+    elif pinned:
+        latest = item.get("latest_available")
+        comparison = item.get("latest_comparison")
+        if comparison == "newer":
+            detail = f"pinned at {pinned}, newer available: {latest}"
+        elif comparison == "not_newer":
+            detail = f"pinned at {pinned}, up to date"
+        elif comparison == "incomparable":
+            detail = f"pinned at {pinned}, latest {latest} cannot be compared"
+        else:
+            detail = f"pinned at {pinned}, latest unknown"
+        lines = [f"[PINNED] {server}: {detail}"]
+    else:
+        status = "OK" if bool(item.get("ok")) else "FAILED"
+        lines = [f"[{status}] {server}: {item.get('message', '')}"]
+    warnings = item.get("warnings")
+    if isinstance(warnings, list):
+        lines.extend(f"  warning: {warning}" for warning in warnings)
+    return lines
+
+
 def _get_gateway_url() -> str:
     """Return the PMCP gateway MCP endpoint URL."""
     return os.environ.get(
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index 9837e82..08f7b29 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -14,9 +14,47 @@ from typing import Any, Literal, cast
 import yaml
 
 from pmcp.project_consent import log_refusal, read_and_gate
+from pmcp.validation import (
+    NPM_FILE_TYPE_RE,
+    is_valid_package_version,
+    parse_package_spec,
+)
 
 logger = logging.getLogger(__name__)
 
+
+_DIAG_REDACTED = "<redacted>"
+
+# Closed grammars for the only argv-derived text a version-pin diagnostic may
+# show besides the renderer (Consiliency/pmcp#295 board round 20). None admits
+# `:`, `?`, `&`, `#`, `%`, whitespace controls or an `@` after the first
+# character, so no URL userinfo, query string, `k=v` value or env VALUE can
+# pass; anything else is `<redacted>`.
+_DIAG_NAME_RE = re.compile(r"@?[A-Za-z0-9][A-Za-z0-9._+/-]{0,127}")
+_DIAG_FLAG_RE = re.compile(r"--?[A-Za-z0-9][A-Za-z0-9-]{0,63}")
+_DIAG_SELECTOR_RE = re.compile(
+    r"[A-Za-z0-9.+*^~<>=!|, -]{1,64}|sha(?:256|384|512):[0-9a-f]{1,128}"
+)
+
+
+def diag_name(value: object) -> str:
+    """A package, image, command, env-KEY or relaxer NAME, or `<redacted>`."""
+    text = str(value)
+    return text if _DIAG_NAME_RE.fullmatch(text) else _DIAG_REDACTED
+
+
+def diag_flag(value: object) -> str:
+    """A flag NAME (`--registry`, `-c`; never its value), or `<redacted>`."""
+    text = str(value)
+    return text if _DIAG_FLAG_RE.fullmatch(text) else _DIAG_REDACTED
+
+
+def diag_selector(value: object) -> str:
+    """A version, range, dist-tag or digest, or `<redacted>`."""
+    text = str(value)
+    return text if _DIAG_SELECTOR_RE.fullmatch(text) else _DIAG_REDACTED
+
+
 Platform = Literal["mac", "wsl", "linux", "windows"]
 ServerTransport = Literal["local", "remote", "sse", "http", "streamable-http"]
 
@@ -88,6 +126,12 @@ class ServerConfig:
     status: str | None = None
     source: str | None = None
     replacement: str | None = None
+    # The exact client version this entry runs (Consiliency/pmcp#294). Set by
+    # `version:` on an entry or by an overlay's `server_version:` patch, and
+    # materialised by `load_manifest` into the npx package slot of `args` and
+    # of every `install` argv. ``None`` means unpinned -- including a pin that
+    # was refused, so "version is set" always means "the argv is pinned".
+    version: str | None = None
 
 
 def credential_storage_key(server: Any) -> str | None:
@@ -553,6 +597,246 @@ def _parse_api_key_optional_when(
     return parsed
 
 
+def _parse_version_pin(name: str, raw: Any, field_label: str) -> str | None:
+    """Parse a client version pin, fail-soft and fail-closed.
+
+    One exact SemVer version (``is_valid_package_version``, the grammar the
+    provision gate already requires of an argv pin) or nothing. Refused, with
+    a warning: a range (``^3.25.5``, ``3.x``) and a dist-tag (``latest``) --
+    both re-resolve at every ``npx -y`` spawn, which is the drift a pin exists
+    to stop -- a ``v`` prefix, a YAML number, and anything carrying a package
+    name, whitespace or a flag. The value never names a package: the name is
+    always taken from the entry's own argv (see `_materialize_version_pin`).
+    """
+    if raw is None:
+        return None
+    # SemVer build metadata (the `+...` segment) is refused too: npm ignores it
+    # when resolving, so `3.25.5+x` would run 3.25.5 while every report echoed
+    # a label that names nothing (Consiliency/pmcp#295 board, N2).
+    if isinstance(raw, str) and is_valid_package_version(raw) and "+" not in raw:
+        return raw
+    logger.warning(
+        f"Ignoring '{field_label}' {diag_selector(raw)!r} for server '{name}': a version pin "
+        'must be one exact version such as "3.25.5" -- not a range, a '
+        'dist-tag such as "latest", build metadata (+...), a name npm '
+        "reads as a local tarball (.tgz/.tar/.tar.gz), or a package "
+        "spec; the server stays unpinned"
+    )
+    return None
+
+
+# npm-package-arg's classification, restated as the ALLOWLIST a pin may
+# rewrite (Consiliency/pmcp#295 board rounds 1-2). npa decides a spec's class
+# in a fixed order, and only its final branch, `fromRegistry`, fetches `name`
+# from the registry; every earlier branch (URL, git, alias, file, directory,
+# hosted git) names something else. The plan's class table maps each branch to
+# the clause below that refuses it.
+#
+# npa `isFileType`, as `validation.NPM_FILE_TYPE_RE` defines it (reused, never
+# restated here): a selector -- or an UNSCOPED bare name -- matching it is a
+# tarball FILE to npm. npa checks it BEFORE the registry branch, so before
+# "is this a version" too: `1.0.0-x.tgz` is valid SemVer and still a file,
+# which is why the selector is tested before `is_valid_package_version`
+# (itself tarball-aware) is consulted (round 3, B1').
+_NPM_FILE_TYPE_RE = NPM_FILE_TYPE_RE
+# validate-npm-package-name's exclusionList, compared case-insensitively as it
+# does: npa refuses these as names (round 3, N-c).
+_NPM_EXCLUDED_NAMES = frozenset({"node_modules", "favicon.ico"})
+# A dist-tag: npa's registry branch accepts any encodeURIComponent-safe word
+# that is neither a version nor a range; this is the letter-led subset of it
+# (fullmatch, so no trailing newline).
+_TAG_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
+# A letter-led word npm may read as a VERSION or RANGE, not a tag: semver's
+# loose grammar allows ANY run of leading `v`/`=` (`[v=\s]*`), then a partial
+# version whose parts are numbers or x/X/* wildcards, then a loose prerelease
+# with or without a hyphen (`x`, `X.x`, `v1`, `vv1.2.3`, `vvX`, `v1.X.xbeta`).
+# Refused like every range: rewriting it would be harmless to package identity
+# (still `fromRegistry`), but a range is not the version-or-tag class a pin
+# replaces. Deliberately broad -- any tail -- so a real tag it also matches
+# (`xyz`) only loses its pin, with a warning (round 3, N-a).
+_PARTIAL_VERSION_WORD_RE = re.compile(
+    r"[vV=]*(?:[0-9]+|[xX*])(?:\.(?:[0-9]+|[xX*])){0,2}.*"
+)
+
+
+def split_plain_registry_spec(arg: str) -> tuple[str, str | None] | None:
+    """``(name, selector)`` if npm would fetch *arg* from the registry as
+    ``name`` at an exact version or a dist-tag; else ``None``.
+
+    Accepted: ``name`` (npa: registry range ``*``), ``name@<exact SemVer>``
+    (npa: ``version``) and ``name@<dist-tag>`` (npa: ``tag``). Refused, by
+    class: anything ``parse_package_spec`` rejects (URLs, aliases, git, paths,
+    flags: their name half is not a package name, or they have no name); an
+    unscoped name ending in a tarball suffix (npa: ``file``); a selector that
+    is not an exact version or a tag word (``:`` / ``/`` / ``~`` / ``.``-led
+    forms, i.e. alias, git, URL, file, directory, and every range); a tag word
+    ending in a tarball suffix (npa: ``file``); and a tag word that npm reads as
+    a version or range (``x``, ``v1``, ``vvX``); a SemVer-shaped selector that
+    ends in a tarball suffix (npa: ``file``, checked first); and the names
+    npm excludes (``node_modules``, ``favicon.ico``). Pure grammar: it never asks the
+    resolver, so it works while npm package identity is disabled.
+    """
+    try:
+        name, selector = parse_package_spec(arg)
+    except (ValueError, TypeError, AttributeError):
+        return None
+    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):
+        return None
+    if name.lower() in _NPM_EXCLUDED_NAMES:
+        return None
+    if selector is None:
+        return name, None
+    # npa's order: `isFileType` on the selector BEFORE any registry reading, so
+    # a SemVer-shaped tarball (`3.25.5-corp.tgz`) is a file, not a version.
+    if _NPM_FILE_TYPE_RE.search(selector):
+        return None
+    if is_valid_package_version(selector):
+        return name, selector
+    if _TAG_WORD_RE.fullmatch(selector) and not _PARTIAL_VERSION_WORD_RE.fullmatch(
+        selector
+    ):
+        return name, selector
+    return None
+
+
+def _pin_npx_args(args: list[str], version: str) -> tuple[list[str], str] | None:
+    """*args* with the npx package slot pinned to *version*, and the name.
+
+    The slot is the provision gate's (`provision_gate._package_slot`): the
+    first argument that is not an allowlisted leading flag. It must be a plain
+    registry spec (`split_plain_registry_spec`); its NAME is kept and only its
+    version suffix is replaced, so a pin can select a version of the package
+    the entry already runs and never a different one. ``None`` otherwise:
+    ``myalias@npm:firecrawl-mcp@3.25.5`` -> ``myalias@3.25.5`` would be the
+    registry package ``myalias`` (codex P1), and
+    ``firecrawl-mcp@corp-mcp.TGZ`` -> ``firecrawl-mcp@3.25.5`` would turn a
+    local tarball into a public-registry fetch (round-2 B1).
+    """
+    # Local import: provision_gate is a consumer of this module's ServerConfig.
+    from pmcp.provision_gate import _NPX_LEADING_FLAGS
+
+    for index, arg in enumerate(args):
+        if arg in _NPX_LEADING_FLAGS:
+            continue
+        plain = split_plain_registry_spec(arg)
+        if plain is None:
+            return None
+        name, _selector = plain
+        return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name
+    return None
+
+
+def _materialize_version_pin(server: ServerConfig) -> ServerConfig:
+    """Write ``server.version`` into every argv that spawns the server.
+
+    Both ``args`` (what ``client/manager.py`` spawns) and every ``install``
+    argv (what ``start_install`` runs): pinning one and not the other approves
+    X and runs latest (`provision_gate._config_runs_exactly`). All or nothing:
+    if any argv cannot be pinned to the same package, the pin is dropped with
+    a warning and the entry is returned unpinned with ``version=None``, so
+    gateway.health's unpinned-self-hosted warning still sees it.
+    """
+    version = server.version
+    if version is None:
+        return server
+    from pmcp.provision_gate import _is_npx
+
+    def refuse(reason: str) -> ServerConfig:
+        logger.warning(
+            f"Ignoring version pin {diag_selector(version)!r} for server '{server.name}': "
+            f"{reason}; the server stays unpinned"
+        )
+        return replace(server, version=None)
+
+    if server.url:
+        return refuse("it is a remote server, so there is no local client to pin")
+    if not _is_npx(server.command):
+        return refuse(
+            f"'version'/'server_version' pins npx-launched servers only and this "
+            f"one runs {diag_name(server.command)!r}; pin a uvx/pip/cargo/docker server with "
+            "explicit command and args in .mcp.json or .pmcp.json instead"
+        )
+    pinned = _pin_npx_args(list(server.args), version)
+    if pinned is None:
+        return refuse(
+            "its args name no plain registry package (name or name@version/tag) "
+            "to pin -- an alias, URL, git, file or range spec could change which "
+            "package runs"
+        )
+    args, package = pinned
+    install: dict[Platform, list[str]] = {}
+    for platform, argv in server.install.items():
+        if not argv:
+            install[platform] = argv
+            continue
+        if not _is_npx(argv[0]):
+            return refuse(f"its {platform} install command is not npx")
+        pinned_install = _pin_npx_args(list(argv[1:]), version)
+        if pinned_install is None or pinned_install[1] != package:
+            return refuse(
+                f"its {platform} install command does not run the plain "
+                f"registry package {diag_name(package)!r} its args run"
+            )
+        install[platform] = [argv[0], *pinned_install[0]]
+    return replace(server, args=args, install=install)
+
+
+def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
+    """`_materialize_version_pin`, contained to one entry.
+
+    Overlay entries are only shape-checked where a field is parsed, so an argv
+    can still carry a non-string (``args: ["-y", 123]``) or a non-string
+    ``command``. HEAD loads such an entry untouched; a pin on it must cost that
+    entry its pin, never the whole manifest (Consiliency/pmcp#295 board,
+    codex P3).
+    """
+    try:
+        return _materialize_version_pin(server)
+    except Exception as exc:
+        logger.warning(
+            f"Ignoring version pin {diag_selector(server.version)!r} for server "
+            f"'{server.name}': its command/args/install could not be read "
+            f"({type(exc).__name__}); the server stays unpinned"
+        )
+        return replace(server, version=None)
+
+
+def manifest_sources_fingerprint() -> tuple[object, ...]:
+    """A cheap identity for everything ``load_manifest()`` reads.
+
+    ``stat`` only -- no parse and no log line -- over the shipped manifest,
+    the user overlay, the project overlay the cwd walk finds, the raw
+    ``$PMCP_MANIFEST_PATH`` value and its target, and the trust store (a
+    project overlay's approval changes what loads without touching the
+    overlay file). A caller that only needs a few manifest facts on a hot
+    path (gateway.health) re-loads when this changes instead of on every
+    call (Consiliency/pmcp#295 board, claude F3).
+    """
+
+    def stat(path: Path) -> tuple[str, int | None, int | None]:
+        try:
+            st = path.stat()
+        except OSError:
+            return (str(path), None, None)
+        return (str(path), st.st_mtime_ns, st.st_size)
+
+    parts: list[object] = [
+        stat(Path(__file__).parent / "manifest.yaml"),
+        stat(Path.home() / ".pmcp" / "manifest.yaml"),
+    ]
+    project = _find_project_manifest()
+    parts.append(stat(project) if project is not None else None)
+    env_value = os.environ.get("PMCP_MANIFEST_PATH")
+    parts.append((env_value, stat(Path(env_value).expanduser())) if env_value else None)
+    try:
+        from pmcp.trust_store import trust_store_path
+
+        parts.append(stat(trust_store_path()))
+    except Exception:
+        parts.append(None)
+    return tuple(parts)
+
+
 def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
     """Parse a server config from raw YAML data."""
     install_data = data.get("install", {})
@@ -613,6 +897,7 @@ def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
         status=data.get("status"),
         source=data.get("source"),
         replacement=data.get("replacement"),
+        version=_parse_version_pin(name, data.get("version"), "version"),
     )
 
 
@@ -722,7 +1007,10 @@ def _overlay_manifest_paths() -> list[tuple[str, Path]]:
 
 
 _OverlayDocument = tuple[
-    dict[str, ServerConfig], dict[str, CLIAlternative], dict[str, dict[str, str]]
+    dict[str, ServerConfig],
+    dict[str, CLIAlternative],
+    dict[str, dict[str, str]],
+    dict[str, str],
 ]
 
 
@@ -739,7 +1027,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
         content = path.read_bytes()
     except OSError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     return _parse_overlay_document(path, content)
 
@@ -747,7 +1035,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
 def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     """Parse overlay bytes, fail-soft. ``path`` is for messages only.
 
-    Returns ``(servers, cli_alternatives, server_env)``. A YAML error or a
+    Returns ``(servers, cli_alternatives, server_env, server_version)``. A YAML error or a
     non-mapping top-level document logs a warning naming the file and returns
     empty dicts. Each entry is parsed in its own try/except so one malformed
     entry is skipped without dropping siblings.
@@ -759,18 +1047,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     operator can point a shipped server at a self-hosted endpoint without
     restating its command, args, and install block. It deliberately cannot
     create a server: ``servers:`` remains whole-entry replace.
+
+    ``server_version`` is the same kind of patch for ``version``: it pins an
+    existing server's client without restating its install matrix, and it
+    cannot create a server either (Consiliency/pmcp#294).
     """
     try:
         data = yaml.safe_load(content)
     except yaml.YAMLError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     if not isinstance(data, dict):
         logger.warning(
             f"Skipping manifest overlay {path}: top-level document is not a mapping"
         )
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     servers: dict[str, ServerConfig] = {}
     raw_servers = data.get("servers", {})
@@ -814,7 +1106,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     elif raw_server_env:
         logger.warning(f"Skipping 'server_env' in overlay {path}: not a mapping")
 
-    return servers, cli_alternatives, server_env
+    server_version: dict[str, str] = {}
+    raw_server_version = data.get("server_version", {})
+    if isinstance(raw_server_version, dict):
+        for name, raw_version in raw_server_version.items():
+            if not isinstance(name, str) or not name:
+                logger.warning(
+                    f"Skipping non-string 'server_version' key in overlay {path}"
+                )
+                continue
+            version = _parse_version_pin(name, raw_version, "server_version")
+            if version is not None:
+                server_version[name] = version
+    elif raw_server_version:
+        logger.warning(f"Skipping 'server_version' in overlay {path}: not a mapping")
+
+    return servers, cli_alternatives, server_env, server_version
 
 
 def load_manifest(manifest_path: Path | None = None) -> Manifest:
@@ -868,19 +1175,31 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
                     continue
                 # Parse the bytes the gate judged. Re-opening `overlay_path`
                 # here would apply content nobody approved.
-                overlay_servers, overlay_clis, overlay_server_env = (
-                    _parse_overlay_document(overlay_path, content)
-                )
+                (
+                    overlay_servers,
+                    overlay_clis,
+                    overlay_server_env,
+                    overlay_server_version,
+                ) = _parse_overlay_document(overlay_path, content)
             else:
-                overlay_servers, overlay_clis, overlay_server_env = _load_overlay_file(
-                    overlay_path
-                )
-            if overlay_servers or overlay_clis or overlay_server_env:
+                (
+                    overlay_servers,
+                    overlay_clis,
+                    overlay_server_env,
+                    overlay_server_version,
+                ) = _load_overlay_file(overlay_path)
+            if (
+                overlay_servers
+                or overlay_clis
+                or overlay_server_env
+                or overlay_server_version
+            ):
                 logger.info(
                     f"Applying manifest overlay ({label}) from {overlay_path}: "
                     f"{len(overlay_servers)} servers, "
                     f"{len(overlay_clis)} CLI alternatives, "
-                    f"{len(overlay_server_env)} server_env patches"
+                    f"{len(overlay_server_env)} server_env patches, "
+                    f"{len(overlay_server_version)} server_version pins"
                 )
             for name in overlay_servers:
                 if name in servers:
@@ -906,6 +1225,25 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
                     existing, extra_env={**existing.extra_env, **patch}
                 )
 
+            # Same rules as server_env: after this source's replaces, and never
+            # for a server the manifest does not already define.
+            for name, version in overlay_server_version.items():
+                existing = servers.get(name)
+                if existing is None:
+                    logger.warning(
+                        f"Manifest overlay ({label}) from {overlay_path} has a "
+                        f"'server_version' pin for unknown server '{name}': skipped"
+                    )
+                    continue
+                servers[name] = replace(existing, version=version)
+
+    # Materialise every pin once, after all overlays: a later source's
+    # whole-entry replace or server_version patch must be what gets written
+    # into argv, not an earlier one's.
+    servers = {
+        name: _materialize_version_pin_soft(entry) for name, entry in servers.items()
+    }
+
     manifest = Manifest(
         version=data.get("version", "1.0"),
         cli_alternatives=cli_alternatives,
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 45a956c..4ed3058 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -11,6 +11,7 @@ import asyncio
 import time
 import platform
 from collections import deque
+from dataclasses import dataclass
 from datetime import datetime, timezone
 from pathlib import Path
 from collections.abc import Callable, Mapping
@@ -64,10 +65,13 @@ from pmcp.feedback_egress import (
     submit_feedback_issue,
 )
 from pmcp.validation import (
+    NPM_FILE_TYPE_RE,
     discovered_env_var_allowed,
     env_var_allowed,
     is_valid_package_name,
     is_valid_package_version,
+    normalized_executable_name,
+    parse_package_spec,
 )
 from pmcp.identity import filter_self_references
 from pmcp.manifest.code_patterns_loader import get_code_hint
@@ -79,7 +83,7 @@ from pmcp.manifest.installer import (
     get_job_manager,
     InstallError,
 )
-from pmcp.manifest.loader import load_manifest
+from pmcp.manifest.loader import diag_flag, diag_name, diag_selector, load_manifest
 from pmcp.manifest.package_identity import PackageIdentity, resolve_package_identity
 from pmcp.manifest.matcher import (
     _keyword_match_score,
@@ -99,10 +103,13 @@ from pmcp.manifest.npm_resolver import get_resolver
 from pmcp.manifest.version_checker import (
     _docker_image_arg,
     _docker_image_digest,
+    _docker_image_name,
     _docker_image_tag,
     _npm_package_arg,
     _npm_tag,
+    _parse_version,
     _uvx_package_arg,
+    compare_versions,
     detect_package_type,
     get_package_version,
 )
@@ -198,6 +205,8 @@ from pmcp.manifest.loader import (
     credential_lookup_keys,
     credential_storage_key,
     is_usable_credential_value,
+    manifest_sources_fingerprint,
+    split_plain_registry_spec,
     requires_credential,
 )
 
@@ -446,6 +455,1559 @@ def _detect_effective_version_pin(
     return None
 
 
+# A docker content digest: the one docker reference that cannot be re-pointed.
+# The algorithms and lengths the OCI image spec registers.
+_DOCKER_DIGEST_RE = re.compile(
+    r"sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|sha512:[0-9a-f]{128}"
+)
+# Shaped like a digest (an algorithm, a colon, hex) but not a well-formed one:
+# upper-case hex, or the wrong length. It floats, and it is not a tag either.
+_DOCKER_DIGEST_LIKE_RE = re.compile(r"sha(?:256|384|512):[0-9A-Fa-f]+")
+
+
+def _is_exact_pin(package_type: str, pin: str) -> bool:
+    """Does *pin* hold the client at ONE artifact, or only name a selector?
+
+    ``_detect_effective_version_pin`` answers "does the argv carry any
+    version selector", which is the right question for update_server's
+    refusal (do not move what the operator chose) and the wrong one for
+    drift: ``^3.25.5``, ``~3.25.5`` and ``next`` re-resolve at every spawn
+    (Consiliency/pmcp#295 board, codex P2 / claude F1).
+
+    Decided PER LAUNCHER, with no rule shared across them: a generic SemVer
+    check that ran first made ``docker run img:3.25.5`` exact, and a docker
+    tag can be re-pointed at another image (#295 board round 4, C2).
+
+    * npm: one exact registry version (``is_valid_package_version``: never a
+      range, a dist-tag, or a string npm reads as a tarball). Build metadata
+      stays exact, because npm ignores it and runs the version it decorates.
+    * docker: a content digest only (``sha256:`` + 64 lowercase hex, or
+      sha384/sha512). Every tag, however version-like, is floating.
+    * pypi (uvx ``pkg==X``): one PEP 440 version with no wildcard. ``===X``
+      arrives here as ``=X``, which is not a version, so it floats. The whole
+      requirement is judged by ``_argv_pin_is_exact`` (a URL requirement is
+      never exact).
+    * cargo (``--version X``): cargo installs exactly X only when X has no
+      operator, and ``is_valid_package_version`` is that shape. ``=1.2.3`` is
+      a requirement to cargo, so it floats here: conservative, never silent.
+    * anything else: not exact.
+    """
+    if package_type == "npm":
+        return is_valid_package_version(pin)
+    if package_type == "docker":
+        return _DOCKER_DIGEST_RE.fullmatch(pin) is not None
+    if package_type == "pypi":
+        return "*" not in pin and _parse_version(pin) is not None
+    if package_type == "cargo":
+        return is_valid_package_version(pin)
+    return False
+
+
+def _floating_reason(package_type: str, pin: str = "") -> str:
+    """Why a non-exact selector does not hold the client still, per launcher.
+
+    npm's reading of a version string is not the same on every release (npa
+    12 reads ``1.0.0-x.tar-gz`` as a file, npa 13 as a version), so the npm
+    text claims only what holds on all of them (#295 board round 4, NB-2).
+    """
+    if package_type == "docker":
+        if _DOCKER_DIGEST_LIKE_RE.fullmatch(pin):
+            return (
+                "a malformed content digest: docker digests are lower-case hex "
+                "of the algorithm's length"
+            )
+        return (
+            "a docker tag, which can be moved to another image; only an "
+            "@sha256 digest pins one"
+        )
+    if package_type == "pypi":
+        if pin.startswith("@"):
+            return (
+                "a URL requirement names a file, not one exact PEP 440 version "
+                "from an index"
+            )
+        return "not one exact PEP 440 version from an index"
+    if package_type == "cargo":
+        return "cargo reads it as a version requirement, not one exact version"
+    return "not one exact version on every npm release"
+
+
+# ---------------------------------------------------------------------------
+# What the ENTRY controls besides its pin (#295 board rounds 4-6).
+#
+# Trust boundary (maintainer decision, round 5): the host's own npm/uv/cargo/
+# docker configuration -- npmrc files at every level, uv.toml, cargo config,
+# the operator's shell environment, shims, caches, global bins, proxy and CA
+# settings -- is the operator's trusted environment, like PATH already is.
+# The warning fails loud only on what a manifest entry or overlay controls:
+# its argv, its launcher, a cwd it sets, and the env block it injects into the
+# child (``config.env``). The plan and the README state this as a non-goal.
+#
+# Both entry rules are ALLOWLISTS (round 6): a denylist of "keys that
+# redirect" or "flags that redirect" fails open on every spelling it does not
+# list (XDG_CONFIG_DIRS, LD_PRELOAD, a container command after the image...).
+# An injected key is silent only if it is proven inert, and an argv is silent
+# only if it has a recognised SHAPE in which the pinned package is what runs.
+# ---------------------------------------------------------------------------
+
+# Keys the entry may inject for any launcher: locale, terminal and colour
+# output. They change how output looks, never what is fetched or loaded.
+_ENTRY_ENV_INERT_KEYS = frozenset(
+    {"LANG", "LANGUAGE", "TERM", "TZ", "NO_COLOR", "FORCE_COLOR"}
+)
+_ENTRY_ENV_INERT_PREFIXES = ("LC_",)
+
+# npm config keys an entry may inject without changing which top-level package
+# or version runs: output and logging, network timing, the install prompt.
+# `cache` is NOT here: libnpmexec runs an existing `_npx/<hash>/node_modules/
+# <pkg>` on its package.json's word, without re-verifying the bytes, so a
+# cache an entry points elsewhere can change what runs (round 5, N1).
+_NPM_ENTRY_KEYS_THAT_CANNOT_REDIRECT = frozenset(
+    {
+        "loglevel",
+        "color",
+        "progress",
+        "timing",
+        "unicode",
+        "logs-dir",
+        "logs-max",
+        "fund",
+        "audit",
+        "update-notifier",
+        "yes",
+        "fetch-retries",
+        "fetch-retry-factor",
+        "fetch-retry-maxtimeout",
+        "fetch-retry-mintimeout",
+        "fetch-timeout",
+        "maxsockets",
+    }
+)
+# uv and cargo: output and network timing only, plus credentials (below).
+_UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT = frozenset(
+    {"UV_NO_PROGRESS", "UV_HTTP_TIMEOUT", "UV_HTTP_RETRIES"}
+)
+_CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT = frozenset(
+    {
+        "CARGO_TERM_COLOR",
+        "CARGO_TERM_QUIET",
+        "CARGO_TERM_VERBOSE",
+        "CARGO_TERM_PROGRESS_WHEN",
+        "CARGO_TERM_PROGRESS_WIDTH",
+        "CARGO_HTTP_TIMEOUT",
+        "CARGO_NET_RETRY",
+        "CARGO_BUILD_JOBS",
+        "CARGO_REGISTRY_TOKEN",
+    }
+)
+# Credentials say how to authenticate to a registry, never which one to use.
+_UV_CREDENTIAL_KEY_RE = re.compile(r"UV_INDEX_[A-Z0-9_]+_(?:USERNAME|PASSWORD)")
+_CARGO_CREDENTIAL_KEY_RE = re.compile(r"CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")
+
+
+@functools.lru_cache(maxsize=1)
+def _shipped_manifest_commands() -> dict[str, str]:
+    """``{server name: its command}`` from pmcp's OWN shipped manifest.
+
+    Read the same way as ``_shipped_manifest_declarations`` (the packaged
+    file directly, never through overlays). It is the only POSITIVE evidence
+    that a command pmcp does not model is a locally installed server binary
+    (#295 board round 10, B-1): an overlay, ``.pmcp.json`` or ``.mcp.json``
+    naming some other command proves nothing.
+    """
+    import yaml
+
+    from pmcp.manifest import loader as manifest_loader
+
+    path = Path(manifest_loader.__file__).parent / "manifest.yaml"
+    try:
+        servers = (yaml.safe_load(path.read_bytes()) or {}).get("servers") or {}
+    except (OSError, yaml.YAMLError, AttributeError):
+        return {}
+    return {
+        name: entry["command"]
+        for name, entry in servers.items()
+        if isinstance(name, str)
+        and isinstance(entry, dict)
+        and isinstance(entry.get("command"), str)
+        and entry["command"]
+    }
+
+
+@functools.lru_cache(maxsize=1)
+def _shipped_manifest_relaxers() -> dict[str, tuple[str, ...]]:
+    """``{server name: its api_key_optional_when relaxers}``, SHIPPED only.
+
+    Read directly from the packaged ``manifest.yaml`` like the declarations
+    and commands. Whether the self-hosted warning APPLIES must not depend on
+    an overlay keeping the relaxer: an overlay replacing ``firecrawl``
+    without ``api_key_optional_when`` (or with ``requires_api_key: false``, or
+    an older copy of the entry) used to switch the warning off (#295 board
+    round 11). A key equal to the shipped entry's own ``env_var`` is dropped
+    (self-relaxation is impossible, as ``credential_requirement`` says).
+    """
+    import yaml
+
+    from pmcp.manifest import loader as manifest_loader
+
+    path = Path(manifest_loader.__file__).parent / "manifest.yaml"
+    try:
+        servers = (yaml.safe_load(path.read_bytes()) or {}).get("servers") or {}
+    except (OSError, yaml.YAMLError, AttributeError):
+        return {}
+    relaxers: dict[str, tuple[str, ...]] = {}
+    for name, entry in servers.items():
+        if not isinstance(name, str) or not isinstance(entry, dict):
+            continue
+        keys = entry.get("api_key_optional_when") or []
+        own = entry.get("env_var")
+        if isinstance(keys, list):
+            found = tuple(k for k in keys if isinstance(k, str) and k != own)
+            if found:
+                relaxers[name] = found
+    return relaxers
+
+
+def _warning_relaxers(
+    server_name: str, manifest_server: ServerConfig | None
+) -> tuple[str, ...]:
+    """The relaxer keys that decide whether the warning APPLIES to a server.
+
+    The shipped manifest's keys for that name, UNION the loaded (overlay-
+    applied) entry's own: an overlay may add a relaxer, never remove a
+    shipped one. The credential GATE is unchanged and still reads the loaded
+    entry (``credential_requirement``). Limit: a server that exists only in
+    an overlay, under a name pmcp does not ship, has no shipped entry to check
+    against -- if it declares no relaxer, the warning cannot apply to it.
+    """
+    keys = list(_shipped_manifest_relaxers().get(server_name, ()))
+    if manifest_server is not None:
+        own = manifest_server.env_var
+        keys += [
+            k
+            for k in manifest_server.api_key_optional_when
+            if k != own and k not in keys
+        ]
+    return tuple(keys)
+
+
+def _shipped_local_commands(server_name: str) -> frozenset[str]:
+    """The command the shipped manifest names for *server_name*, if any."""
+    command = _shipped_manifest_commands().get(server_name)
+    return frozenset({command}) if command else frozenset()
+
+
+@functools.lru_cache(maxsize=1)
+def _shipped_manifest_declarations() -> dict[str, frozenset[str]]:
+    """``{server name: its declared keys}`` from pmcp's OWN shipped manifest.
+
+    Read from the packaged ``manifest.yaml`` directly -- never through
+    ``load_manifest``, which applies overlays -- so no user, project or
+    ``$PMCP_MANIFEST_PATH`` overlay, and no ``.pmcp.json``/``.mcp.json``
+    entry, can add a name (#295 board round 7, B2). The file ships with
+    pmcp and does not change under a running gateway, so it is read once.
+    A server's declared keys are its ``env_var`` and its
+    ``api_key_optional_when`` relaxers. (``code_patterns.yaml`` declares no
+    env keys.)
+    """
+    import yaml
+
+    from pmcp.manifest import loader as manifest_loader
+
+    path = Path(manifest_loader.__file__).parent / "manifest.yaml"
+    try:
+        servers = (yaml.safe_load(path.read_bytes()) or {}).get("servers") or {}
+    except (OSError, yaml.YAMLError, AttributeError):
+        return {}
+    declared: dict[str, frozenset[str]] = {}
+    for name, entry in servers.items():
+        if not isinstance(name, str) or not isinstance(entry, dict):
+            continue
+        keys = set()
+        if isinstance(entry.get("env_var"), str):
+            keys.add(entry["env_var"])
+        relaxers = entry.get("api_key_optional_when") or []
+        if isinstance(relaxers, list):
+            keys.update(k for k in relaxers if isinstance(k, str))
+        declared[name] = frozenset(keys)
+    return declared
+
+
+def _declared_env_keys(server_name: str) -> frozenset[str]:
+    """The keys pmcp's SHIPPED manifest declares as *server_name*'s own.
+
+    The only declarations that exempt a key: the shipped manifest is pmcp's
+    own reviewed file, and it declares only application keys (84 names:
+    credentials such as ``FIRECRAWL_API_KEY`` and the relaxer
+    ``FIRECRAWL_API_URL``). A declaration in an overlay, ``.pmcp.json`` or
+    ``.mcp.json`` is entry-controlled, so it exempts nothing: an overlay
+    that names ``OPENSSL_CONF`` as its credential must not make it inert
+    (#295 board round 7, B2). There is no namespace list to get wrong.
+    """
+    return _shipped_manifest_declarations().get(server_name, frozenset())
+
+
+def _npm_env_config_key(env_key: str) -> str:
+    """The config key npm reads from an ``npm_config_*`` variable.
+
+    npm's own ``loadEnv`` rule (``@npmcli/config``, identical in npm 10 and
+    11): strip the prefix case-insensitively; unless the rest starts with
+    ``//``, replace every ``_`` except a leading one with ``-`` and lowercase.
+    """
+    key = env_key[len("npm_config_") :]
+    if key.startswith("//"):
+        return key
+    return (key[:1] + key[1:].replace("_", "-")).lower()
+
+
+def _npm_key_can_redirect(key: str, package: str) -> bool:
+    """Can an entry-injected npm config *key* change what runs for *package*?
+
+    Judged for the TOP-LEVEL package the pin names: a pin never holds its
+    dependency closure, which npm resolves fresh from ranges (round 5, N4).
+    """
+    if key.startswith("//"):
+        # A credential scoped to one registry (`//host/:_authToken`).
+        return False
+    scope, sep, rest = key.partition(":")
+    if sep and scope.startswith("@") and rest.lower() == "registry":
+        # `@scope:registry` redirects only that scope's packages.
+        return package.lower().startswith(f"{scope.lower()}/")
+    return key not in _NPM_ENTRY_KEYS_THAT_CANNOT_REDIRECT
+
+
+def _entry_env_key_is_inert(
+    family: str, key: str, value: str, package: str, declared: frozenset[str]
+) -> bool:
+    """Is *key* (set by the entry's env block) PROVEN not to change what runs?
+
+    An allowlist: the server's own declared keys, locale/terminal/colour
+    keys, and per launcher a few logging, timing and credential keys. Every
+    other key -- unknown, ``XDG_*``, ``LD_*``, ``CC``, a proxy, a CA file --
+    is not inert (round 6, X2/N4). Values are not special-cased: an empty
+    ``PATH`` makes exec search the cwd (round 6, N1), so an empty value of a
+    non-inert key is still not inert.
+    """
+    if key in declared:
+        return True
+    upper = key.upper()
+    if upper in _ENTRY_ENV_INERT_KEYS or upper.startswith(_ENTRY_ENV_INERT_PREFIXES):
+        return True
+    if family == "npm" and key.lower().startswith("npm_config_"):
+        return not _npm_key_can_redirect(_npm_env_config_key(key), package)
+    if family == "pypi":
+        return upper in _UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(
+            _UV_CREDENTIAL_KEY_RE.fullmatch(upper)
+        )
+    if family == "cargo":
+        return upper in _CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(
+            _CARGO_CREDENTIAL_KEY_RE.fullmatch(upper)
+        )
+    return False
+
+
+def _pep503_name(name: str) -> str:
+    return re.sub(r"[-_.]+", "-", name).lower()
+
+
+# Recognised argv SHAPES (round 6, X1). For each launcher: the flags that are
+# inert (boolean, or taking one value), and the rule for what follows. An
+# unrecognised flag, a missing piece, or anything that runs a different
+# command than the pinned package is not a recognised shape.
+_NPX_INERT_FLAGS = frozenset({"-y", "--yes", "--yes=true", "-q", "--quiet"})
+_UVX_INERT_BOOLEAN_FLAGS = frozenset(
+    {
+        "-q",
+        "--quiet",
+        "-v",
+        "--verbose",
+        "--no-progress",
+        "--isolated",
+        "--refresh",
+        "--no-cache",
+        "-n",
+    }
+)
+_UVX_INERT_VALUE_FLAGS = frozenset({"--color"})
+_CARGO_INERT_BOOLEAN_FLAGS = frozenset(
+    {"--locked", "-q", "--quiet", "-v", "--verbose", "-f", "--force"}
+)
+_CARGO_INERT_VALUE_FLAGS = frozenset({"--color", "-j", "--jobs"})
+_DOCKER_INERT_BOOLEAN_FLAGS = frozenset(
+    {"-i", "--interactive", "-t", "--tty", "--rm", "--init"}
+)
+_DOCKER_INERT_VALUE_FLAGS = frozenset(
+    {"--pull", "--name", "--network", "--net", "--platform"}
+)
+
+
+def _split_flag(arg: str) -> tuple[str, str | None]:
+    name, sep, value = arg.partition("=")
+    return name, (value if sep else None)
+
+
+def _render_argv(argv: tuple[object, ...] | list[object]) -> str:
+    """Every argv this feature shows, through the ONE secret-safe renderer.
+
+    ``installer._render_install_argv`` shows the executable, the frozen flag
+    literals and a pinned package slot, and redacts everything else, so a
+    ``--token <value>`` in an entry's argv never reaches a warning or an
+    update_server message (#295 board round 9, codex 1). Non-string elements
+    (a malformed overlay argv) are stringified first, never trusted.
+    """
+    return _render_install_argv([str(part) for part in argv])
+
+
+def _safe_token(token: object) -> str:
+    """One argv token as the secret-safe renderer shows a package slot."""
+    return _render_argv(["_", token]).split(" ", 1)[1]
+
+
+def _combined_short_flags(arg: str, letters: frozenset[str]) -> bool:
+    """``-qq``, ``-it``, ``-fq``: several inert single-letter flags in one."""
+    return (
+        len(arg) > 2
+        and arg[0] == "-"
+        and arg[1] != "-"
+        and all(letter in letters for letter in arg[1:])
+    )
+
+
+def _short_letters(flags: frozenset[str]) -> frozenset[str]:
+    return frozenset(f[1] for f in flags if len(f) == 2 and f[0] == "-")
+
+
+@dataclass(frozen=True)
+class _Reading:
+    """What ONE argv runs, read by the ONE parser for its launcher.
+
+    ``family``: ``npm``/``pypi``/``cargo``/``docker`` for a modelled launcher,
+    ``runner`` for a package runner or wrapper pmcp does not model, ``local``
+    for any other command (a locally installed server binary), ``unreadable``
+    for an argv that is not a list of strings. ``problem`` is set when the
+    argv is not a recognised shape -- then nothing else about it is claimed.
+    ``selector`` is the version selector as the launcher reads it (``None``
+    = unpinned) and ``exact`` whether it is one exact version for that
+    launcher (``_is_exact_pin``). ``env_keys``: container env keys a docker
+    argv passes with ``-e``. ``container_env``: those ``-e`` assignments in
+    argv order, ``(KEY, VALUE)``, or ``(KEY, None)`` for ``-e KEY``, which
+    passes docker's own value through; docker applies them in order, so the
+    last one for a key wins.
+    """
+
+    family: str
+    launcher: str
+    package: str | None = None
+    selector: str | None = None
+    exact: bool = False
+    problem: str | None = None
+    env_keys: tuple[str, ...] = ()
+    container_env: tuple[tuple[str, str | None], ...] = ()
+    env_file: bool = False
+
+
+def _npm_range_selector(arg: str) -> tuple[str, str] | None:
+    """``(name, range)`` for a same-package registry RANGE (``pkg@^3``).
+
+    Not a plain spec (the materialiser's grammar refuses ranges), but npm
+    fetches the same registry package from it, so it FLOATS rather than
+    being unreadable. Tarballs, aliases, URLs, git and paths never qualify.
+    """
+    try:
+        name, selector = parse_package_spec(arg)
+    except (ValueError, TypeError, AttributeError):
+        return None
+    if (
+        selector is None
+        or not is_valid_package_name(name)
+        or (not name.startswith("@") and NPM_FILE_TYPE_RE.search(name))
+        or NPM_FILE_TYPE_RE.search(selector)
+        or any(c in selector for c in ":/\\")
+        or selector.startswith((".", "~/"))
+    ):
+        return None
+    return name, selector
+
+
+def _read_npx_slot(launcher: str, args: list[str]) -> tuple[_Reading, int]:
+    """``[-y|--yes|--yes=true|-q|--quiet]* <spec> ...``: npm's own reading.
+
+    npx's pre-scan inserts ``--`` before the first positional, so what
+    follows the spec is the package's own arguments. Any other flag before
+    the spec (``-p X``, ``--registry``) is not a recognised shape.
+    """
+    for index, arg in enumerate(args):
+        if arg in _NPX_INERT_FLAGS:
+            continue
+        if arg.startswith("-"):
+            flag = diag_flag(_split_flag(arg)[0])
+            return _Reading("npm", launcher, problem=f"its argv passes {flag}"), index
+        plain = split_plain_registry_spec(arg)
+        if plain is None:
+            ranged = _npm_range_selector(arg)
+            if ranged is None:
+                return (
+                    _Reading(
+                        "npm",
+                        launcher,
+                        problem=(
+                            f"its package slot ({_safe_token(arg)}) is not a plain "
+                            "registry spec"
+                        ),
+                    ),
+                    index,
+                )
+            return _Reading("npm", launcher, ranged[0], ranged[1], False), index
+        name, selector = plain
+        if selector == "latest":
+            selector = None
+        exact = selector is not None and _is_exact_pin("npm", selector)
+        return _Reading("npm", launcher, name, selector, exact), index
+    return _Reading("npm", launcher, problem="its argv names no package"), len(args)
+
+
+def _read_npm(args: list[str]) -> _Reading:
+    """``npm exec|x [inert flags] <spec> [-- arguments]``.
+
+    Unlike npx, npm itself parses flags AFTER the spec (``--package=other``
+    would win), so only ``--`` or nothing may follow it.
+    """
+    if not args or args[0] not in ("exec", "x"):
+        return _Reading("npm", "npm", problem="its argv is not `npm exec ...`")
+    reading, index = _read_npx_slot("npm", args[1:])
+    rest = args[1:][index + 1 :]
+    if reading.problem is None and rest and rest[0] != "--":
+        flag = diag_flag(_split_flag(rest[0])[0]) if rest[0].startswith("-") else None
+        return _Reading(
+            "npm",
+            "npm",
+            problem=(
+                f"its argv passes {flag} after the package, which npm reads itself"
+                if flag
+                else "its argv passes arguments after the package without `--`"
+            ),
+        )
+    return reading
+
+
+_UVX_INERT_SHORT = _short_letters(_UVX_INERT_BOOLEAN_FLAGS)
+_CARGO_INERT_SHORT = _short_letters(_CARGO_INERT_BOOLEAN_FLAGS)
+
+
+#: What uv's `ToolRequest::parse` hands to `PythonRequest::try_from_tool_name`
+#: (uv 0.12.19, `crates/uv-python/src/discovery.rs`) and reads as an INTERPRETER
+#: request, not a PyPI package: `python` (`pythonw` on Windows) or an
+#: implementation's long name (`ImplementationName::long_name`: cpython, pypy,
+#: graalpy, pyodide), ASCII-case-insensitively, alone or followed by a version
+#: request (`python==3.10`, `python3.10`, `python310`, `pypy3`, `cpython@3.10`,
+#: `python>=3.10,<3.12`). `pythonw` is included on every platform (fail closed).
+_UV_INTERPRETER_PREFIXES = (
+    "pythonw",
+    "python",
+    "cpython",
+    "pypy",
+    "graalpy",
+    "pyodide",
+)
+#: The rest after a prefix that uv tries as a version request: `@...`, or a
+#: version or specifier (a digit, or `= < > ~ !`). A `-` after the prefix
+#: (`python-dotenv`, `python3-openid`) is a package in uv, measured; pmcp also
+#: reads `python3stuff` as an interpreter request, which fails closed. Leading
+#: whitespace before the rest is skipped, as uv's version parser trims it
+#: (`python ==3.10` runs 3.10.x; #295 board round 19, B1).
+_UV_VERSION_REQUEST_RE = re.compile(r"\s*(?:@.*|[vV]?[0-9][^-]*|[=<>~!][^-]*)", re.S)
+
+
+def _uv_interpreter_request(component: str) -> str | None:
+    """The interpreter name uv would read this component as, else None.
+
+    The result is one of ``_UV_INTERPRETER_PREFIXES`` (a closed set), the
+    only part of the component a diagnostic may show.
+    """
+    lowered = component.lower()
+    for prefix in _UV_INTERPRETER_PREFIXES:
+        if lowered == prefix:
+            return prefix
+        rest = lowered[len(prefix) :] if lowered.startswith(prefix) else None
+        if rest and _UV_VERSION_REQUEST_RE.fullmatch(rest):
+            return prefix
+    return None
+
+
+def _read_uvx(args: list[str]) -> _Reading:
+    """``uvx [inert flags] [--from <req>] <command> [arguments to it]``.
+
+    Modelled on uv's own parser (``ToolRunArgs``): ``--from`` is a
+    single-valued option, which clap resolves LAST-wins. pmcp does not guess
+    which one wins -- a repeated single-valued flag is not a recognised shape
+    (#295 board round 9, grok B1). With ``--from``, the command must be the
+    requirement's own name; without it, the first positional is both the
+    requirement and the command. ``--python`` is not inert (as ``UV_PYTHON``
+    is not). A URL requirement names a file, never an index version.
+    """
+    from packaging.requirements import InvalidRequirement, Requirement
+
+    from_req: str | None = None
+    seen: set[str] = set()
+    index = 0
+    while index < len(args):
+        arg = args[index]
+        if arg.startswith("-") and arg != "-":
+            name, value = _split_flag(arg)
+            if name == "--from" or name in _UVX_INERT_VALUE_FLAGS:
+                if name in seen:
+                    return _Reading(
+                        "pypi",
+                        "uvx",
+                        problem=f"its argv repeats {diag_flag(name)}, a single-valued uv option",
+                    )
+                seen.add(name)
+                if value is None:
+                    if index + 1 >= len(args):
+                        return _Reading(
+                            "pypi",
+                            "uvx",
+                            problem=f"its argv passes {diag_flag(name)} with no value",
+                        )
+                    value = args[index + 1]
+                    index += 1
+                if name == "--from":
+                    from_req = value
+                index += 1
+                continue
+            if value is None and (
+                name in _UVX_INERT_BOOLEAN_FLAGS
+                or _combined_short_flags(arg, _UVX_INERT_SHORT)
+            ):
+                index += 1
+                continue
+            return _Reading("pypi", "uvx", problem=f"its argv passes {diag_flag(name)}")
+        requirement_text = from_req if from_req is not None else arg
+        try:
+            parsed_name: str | None = Requirement(requirement_text).name.lower()
+        except InvalidRequirement:
+            parsed_name = None
+        interpreter = _uv_interpreter_request(requirement_text) or (
+            parsed_name if parsed_name in _UV_INTERPRETER_PREFIXES else None
+        )
+        if interpreter is not None:
+            # `uvx python==3.10` runs an INTERPRETER of the 3.10 series (patch
+            # releases float), not the PyPI package `python` (#295 board
+            # round 18, codex B1): never a package pin. Only the matched name,
+            # from a closed set, is shown -- never the requirement text, which
+            # can carry a URL with credentials (round 20, codex B1).
+            which = "--from" if from_req is not None else "command"
+            return _Reading(
+                "pypi",
+                "uvx",
+                problem=(
+                    f"uv reads this {which} as an interpreter request "
+                    f"({diag_name(interpreter)}), not a package"
+                ),
+            )
+        try:
+            requirement = Requirement(requirement_text)
+        except InvalidRequirement:
+            return _Reading(
+                "pypi", "uvx", problem="its requirement is not a PEP 508 requirement"
+            )
+        if from_req is not None and _pep503_name(arg) != _pep503_name(requirement.name):
+            return _Reading(
+                "pypi",
+                "uvx",
+                problem=(
+                    f"its argv runs {diag_name(arg)!r} from the --from "
+                    f"environment, not the {diag_name(requirement.name)!r} package's own command"
+                ),
+            )
+        if requirement.url:
+            return _Reading("pypi", "uvx", requirement.name, "@ <URL>", False)
+        specs = list(requirement.specifier)
+        if not specs:
+            return _Reading("pypi", "uvx", requirement.name, None, False)
+        if len(specs) == 1 and specs[0].operator == "==" and requirement.marker is None:
+            version = specs[0].version
+            return _Reading(
+                "pypi", "uvx", requirement.name, version, _is_exact_pin("pypi", version)
+            )
+        return _Reading(
+            "pypi", "uvx", requirement.name, str(requirement.specifier), False
+        )
+    return _Reading("pypi", "uvx", problem="its argv names no command")
+
+
+def _read_cargo(args: list[str]) -> _Reading:
+    """``cargo install [inert flags] <crate>[@<version>] [--version <v>]``.
+
+    ``--version``/``--vers`` is single-valued: repeated, or next to a
+    ``crate@version``, it is not a recognised shape. A ``+toolchain`` is not
+    inert (as ``RUSTUP_TOOLCHAIN`` is not), nor is any source flag.
+    """
+    if args and args[0].startswith("+"):
+        return _Reading(
+            "cargo",
+            "cargo",
+            problem=f"its argv selects toolchain +{diag_name(args[0][1:])}",
+        )
+    if not args or args[0] != "install":
+        return _Reading("cargo", "cargo", problem="its argv is not `cargo install ...`")
+    crates: list[str] = []
+    version: str | None = None
+    seen: set[str] = set()
+    index = 1
+    while index < len(args):
+        arg = args[index]
+        if arg.startswith("-") and arg != "-":
+            name, value = _split_flag(arg)
+            key = "--version" if name in ("--version", "--vers") else name
+            if key == "--version" or name in _CARGO_INERT_VALUE_FLAGS:
+                if key in seen:
+                    return _Reading(
+                        "cargo",
+                        "cargo",
+                        problem=f"its argv repeats {diag_flag(key)}, a single-valued cargo option",
+                    )
+                seen.add(key)
+                if value is None:
+                    if index + 1 >= len(args):
+                        return _Reading(
+                            "cargo",
+                            "cargo",
+                            problem=f"its argv passes {diag_flag(name)} with no value",
+                        )
+                    value = args[index + 1]
+                    index += 1
+                if key == "--version":
+                    version = value
+                index += 1
+                continue
+            if value is None and (
+                name in _CARGO_INERT_BOOLEAN_FLAGS
+                or _combined_short_flags(arg, _CARGO_INERT_SHORT)
+            ):
+                index += 1
+                continue
+            return _Reading(
+                "cargo", "cargo", problem=f"its argv passes {diag_flag(name)}"
+            )
+        crates.append(arg)
+        index += 1
+    if len(crates) != 1:
+        return _Reading(
+            "cargo", "cargo", problem="its argv does not install exactly one crate"
+        )
+    crate, at, crate_version = crates[0].partition("@")
+    if at:
+        if version is not None:
+            return _Reading(
+                "cargo", "cargo", problem="its argv names the crate's version twice"
+            )
+        version = crate_version
+    exact = version is not None and _is_exact_pin("cargo", version)
+    return _Reading("cargo", "cargo", crate, version, exact)
+
+
+def _docker_short_cluster(
+    cluster: str, following: str | None
+) -> tuple[str | None, bool, str | None]:
+    """One pflag shorthand cluster (``-it``, ``-eK=V``, ``-ie K=V``, ``-e=K=V``).
+
+    Read exactly as docker's flag library (spf13/pflag) reads it: letters in
+    order; an inert boolean letter consumes itself; ``e`` takes the REST of
+    the cluster as its value (a leading ``=`` is dropped when a value follows
+    it), or, with nothing attached, the next argument. Any other letter is not
+    a recognised shape. Returns ``(env value or None, consumed the next
+    argument, problem)`` (#295 board round 14, B-1 row 3).
+    """
+    for position, letter in enumerate(cluster):
+        if letter == "e":
+            attached = cluster[position + 1 :]
+            if len(attached) >= 2 and attached[0] == "=":
+                return attached[1:], False, None
+            if attached:
+                return attached, False, None
+            if following is None:
+                return None, False, "its argv ends in -e, which needs a value"
+            return following, True, None
+        if "-" + letter not in _DOCKER_INERT_BOOLEAN_FLAGS:
+            return None, False, f"its argv passes {diag_flag('-' + letter)}"
+    return None, False, None
+
+
+def _read_docker(args: list[str]) -> _Reading:
+    """``docker [container] run [inert flags] [-e KEY[=V]]* <image>``.
+
+    Nothing may follow the image: a container command, like
+    ``--entrypoint``, a bind mount or an ``--env-file``, decides what runs
+    INSIDE the image, and a digest pins the image, not that (round 6, X1).
+    A single-valued flag may not repeat. The pin is the ``@digest`` (a tag
+    floats). The ``-e`` assignments, in every spelling docker accepts
+    (``-e K=V``, ``--env K``, ``--env=K=V``, ``-e=K=V``, ``-eK=V`` and a
+    shorthand cluster ending in ``e``), are returned for the env allowlist
+    AND as the container's env: this is the ONE reader of a docker argv
+    (#295 board round 14: no second parser decides the relaxer).
+    """
+    if args[:1] == ["run"]:
+        rest = args[1:]
+    elif args[:2] == ["container", "run"]:
+        rest = args[2:]
+    else:
+        return _Reading("docker", "docker", problem="its argv is not `docker run ...`")
+    assignments: list[tuple[str, str | None]] = []
+    seen: set[str] = set()
+    index = 0
+    while index < len(rest):
+        arg = rest[index]
+        following = rest[index + 1] if index + 1 < len(rest) else None
+        env_value: str | None = None
+        if arg.startswith("--"):
+            name, value = _split_flag(arg)
+            if name == "--env":
+                if value is None:
+                    if following is None:
+                        return _Reading(
+                            "docker",
+                            "docker",
+                            problem="its argv ends in --env, which needs a value",
+                        )
+                    value = following
+                    index += 1
+                env_value = value
+            elif name in _DOCKER_INERT_VALUE_FLAGS:
+                if name in seen:
+                    return _Reading(
+                        "docker",
+                        "docker",
+                        problem=f"its argv repeats {diag_flag(name)}, a single-valued docker option",
+                    )
+                seen.add(name)
+                index += 2 if value is None else 1
+                continue
+            elif value is None and name in _DOCKER_INERT_BOOLEAN_FLAGS:
+                index += 1
+                continue
+            else:
+                return _Reading(
+                    "docker",
+                    "docker",
+                    problem=f"its argv passes {diag_flag(name)}",
+                    env_file=name == "--env-file",
+                )
+        elif arg.startswith("-") and arg != "-":
+            env_value, consumed, problem = _docker_short_cluster(arg[1:], following)
+            if problem is not None:
+                return _Reading("docker", "docker", problem=problem)
+            if consumed:
+                index += 1
+        else:
+            if index + 1 < len(rest):
+                return _Reading(
+                    "docker",
+                    "docker",
+                    problem=(
+                        "its argv passes a container command after the image, which "
+                        "decides what runs inside it"
+                    ),
+                )
+            image = _docker_image_name(arg)
+            digest = _docker_image_digest(arg)
+            tag = _docker_image_tag(arg)
+            selector = digest or (tag if tag and tag != "latest" else None)
+            exact = selector is not None and _is_exact_pin("docker", selector)
+            return _Reading(
+                "docker",
+                "docker",
+                image,
+                selector,
+                exact,
+                None,
+                tuple(key for key, _v in assignments),
+                tuple(assignments),
+            )
+        if env_value is not None:
+            key, sep, assigned = env_value.partition("=")
+            assignments.append((key, assigned if sep else None))
+        index += 1
+    return _Reading("docker", "docker", problem="its argv names no image")
+
+
+# Package runners and wrappers pmcp does not model (round 6, N2): each can
+# fetch and run a package (bunx, pnpx, `pnpm dlx`, `yarn dlx`, `bun x`,
+# `uv run`, pip) or run an arbitrary command line (sh -c, env, node <script>),
+# so pmcp cannot say what version runs. Loud, pinned or not.
+_UNMODELLED_RUNNERS = frozenset(
+    {
+        "bunx",
+        "bun",
+        "pnpx",
+        "pnpm",
+        "yarn",
+        "uv",
+        "pip",
+        "pip3",
+        "deno",
+        "node",
+        "sh",
+        "bash",
+        "zsh",
+        "dash",
+        "ksh",
+        "fish",
+        "env",
+        "cmd",
+        "powershell",
+        "pwsh",
+    }
+)
+
+
+#: Launcher names this module models (and `uv`, a runner unless `tool run`).
+_MODELLED_LAUNCHERS = frozenset({"npx", "npm", "uvx", "uv", "cargo", "docker"})
+
+
+def _is_windows() -> bool:
+    """The platform the child is spawned on (a seam, so tests can simulate it)."""
+    return os.name == "nt"
+
+
+def _is_bare_launcher(command: str, name: str) -> bool:
+    """*command* is the launcher's bare name as the platform spells it."""
+    if _is_windows():
+        return command.lower() in {name, f"{name}.cmd", f"{name}.exe", f"{name}.bat"}
+    return command == name
+
+
+def _launcher_spelled_as_itself(command: str, name: str, path_var: str | None) -> bool:
+    """Is *command* the launcher *name* itself, not merely named like it?
+
+    A bare name as the platform spells it (``npx``; on Windows also
+    ``npx.cmd``/``.exe``/``.bat``, case-insensitively), or an absolute path
+    that is, character for character, what the child's ``PATH`` search
+    yields for *name*. ``/tmp/anything/npx``, ``./npx``,
+    ``node_modules/.bin/npx``, ``NPX`` or ``npx.cmd`` on Linux are not: a
+    basename is not evidence (#295 board round 11).
+
+    Nothing is RESOLVED in pmcp's own process (#295 board round 13, B-1):
+    ``realpath`` resolves ``/proc/self/cwd``, ``/proc/thread-self/cwd`` or a
+    ``/proc/self/fd`` link in pmcp's cwd and fds, not the child's, so an
+    absolute path could still depend on the entry's cwd. The literal
+    comparison needs no resolution. (On Windows ``subprocess`` searches with
+    the PARENT's ``PATH``, whatever the child's env says, so that is used.)
+    """
+    import shutil
+
+    if _is_bare_launcher(command, name):
+        return True
+    if "/" in command or "\\" in command:
+        # A RELATIVE path is resolved by the OS from the CHILD's cwd (an
+        # entry-set cwd), never from pmcp's, so pmcp cannot compare it with
+        # anything: never the launcher, on any platform (#295 board round 12).
+        if not os.path.isabs(command):
+            return False
+        resolved = shutil.which(name, path=None if _is_windows() else path_var)
+        return resolved is not None and command == resolved
+    return False
+
+
+#: Arguments a `.cmd`/`.bat` launcher may carry on Windows: the pin grammar's
+#: characters and flag spellings, and nothing cmd.exe interprets (no
+#: `& | < > ^ % ! " ( )`, no whitespace).
+_CMD_SAFE_ARG_RE = re.compile(r"[A-Za-z0-9@._/:=+,~-]+")
+
+
+def _read_argv(
+    argv: tuple[object, ...],
+    local_commands: frozenset[str] = frozenset(),
+    path_var: str | None = None,
+) -> _Reading:
+    """The ONE reader of a spawning argv for this feature (round 9).
+
+    Pin reading, the shape check, the warning and ``[PINNED]`` all use it;
+    nothing here consults a second reader. A launcher is judged by its
+    basename (``/usr/bin/npx`` is npx); its path is trusted like a local
+    binary's. ``uv tool run`` is uvx.
+
+    A command that is none of those is a LOCAL SERVER BINARY (``local``, pin
+    waived) only on positive evidence: *local_commands*, the exact command
+    pmcp's shipped manifest names for this server. Anything else --
+    ``timeout``, ``nice``, ``sudo``, ``busybox``, ``mise exec``, ``volta
+    run``, ``corepack``, ``pipx run``, ``python3 -m``, ``go run``, or a
+    launcher with an extension pmcp does not strip (``npx.js``,
+    ``npx.ps1``) -- is ``unrecognised``: "cannot verify", naming the command
+    (#295 board round 10, B-1). Not being on a list is never evidence.
+    """
+    if not argv or not all(isinstance(part, str) for part in argv):
+        return _Reading(
+            "unreadable", "", problem="its argv is empty or not a list of strings"
+        )
+    words = [str(part) for part in argv]
+    launcher = normalized_executable_name(words[0])
+    args = words[1:]
+    if (
+        _is_windows()
+        and (launcher in ("npx", "npm") or words[0].lower().endswith((".cmd", ".bat")))
+        and not all(_CMD_SAFE_ARG_RE.fullmatch(arg) for arg in args)
+    ):
+        # On Windows npx/npm are `.cmd` shims, and a `.cmd`/`.bat` runs through
+        # cmd.exe, which does not escape `&`, `|`, `%`...: `x|npx -y pkg@latest`
+        # in an argument pipes into another command (#295 board round 13, N-1).
+        return _Reading(
+            "unrecognised",
+            launcher,
+            problem=(
+                "its argv passes an argument cmd.exe would interpret (a .cmd "
+                "launcher runs through cmd.exe)"
+            ),
+        )
+    if launcher in _MODELLED_LAUNCHERS and not _launcher_spelled_as_itself(
+        words[0], launcher, path_var
+    ):
+        return _Reading(
+            "unrecognised",
+            launcher,
+            problem=(
+                f"it launches through `{_render_argv([words[0]])}`, which is not "
+                f"{diag_name(launcher)!r} as the child's PATH resolves it"
+            ),
+        )
+    if launcher == "uv" and args[:2] == ["tool", "run"]:
+        return _read_uvx(args[2:])
+    if launcher == "npx":
+        return _read_npx_slot("npx", args)[0]
+    if launcher == "npm":
+        return _read_npm(args)
+    if launcher == "uvx":
+        return _read_uvx(args)
+    if launcher == "cargo":
+        return _read_cargo(args)
+    if launcher == "docker":
+        return _read_docker(args)
+    if launcher in _UNMODELLED_RUNNERS:
+        return _Reading(
+            "runner",
+            launcher,
+            problem=(
+                f"it launches through {diag_name(launcher)!r}, a package runner or wrapper "
+                "pmcp does not model"
+            ),
+        )
+    stem = launcher.split(".", 1)[0]
+    if stem in _MODELLED_LAUNCHERS or stem in _UNMODELLED_RUNNERS:
+        return _Reading(
+            "unrecognised",
+            launcher,
+            problem=(
+                f"it launches through `{_render_argv([words[0]])}`, a spelling of "
+                f"{diag_name(stem)!r} that pmcp does not read"
+            ),
+        )
+    if words[0] in local_commands:
+        return _Reading("local", launcher)
+    return _Reading(
+        "unrecognised",
+        launcher,
+        problem=(
+            f"it launches through `{_render_argv([words[0]])}`, a command pmcp "
+            "does not recognise; only the command pmcp's own manifest names for "
+            "this server is exempt from the pin"
+        ),
+    )
+
+
+# Families whose resolution reads configuration relative to the cwd, and the
+# tool named in the message. A local binary is judged too: pmcp cannot prove
+# it ignores its cwd. docker is exempt: `docker run` reads no cwd-relative
+# configuration.
+_CWD_CONFIG_FAMILIES = {
+    "npm": "npm",
+    "pypi": "uv",
+    "cargo": "cargo",
+    "local": "a local command",
+}
+
+
+@dataclass(frozen=True)
+class _Spawn:
+    """One argv that can spawn, or be adopted as, the server."""
+
+    label: str
+    site: str
+    argv: tuple[object, ...]
+    env: Mapping[str, str] | None
+    cwd: str | None
+
+    def where(self) -> str:
+        rendered = _render_argv(self.argv)
+        if self.site.endswith("start_install"):
+            return (
+                f"{self.label} (`{rendered}`), which gateway.provision spawns and "
+                "adopts as the live server,"
+            )
+        return f"{self.label} (`{rendered}`)"
+
+
+def _args_member(
+    manifest_server: ServerConfig | None, resolved: ResolvedServerConfig
+) -> list[_Spawn]:
+    config = resolved.config
+    if not isinstance(config, LocalMcpServerConfig):
+        return []
+    return [
+        _Spawn(
+            "its args",
+            "client/manager.py:ClientManager._connect_stdio",
+            (config.command, *config.args),
+            config.env,
+            config.cwd,
+        )
+    ]
+
+
+def _install_members(
+    manifest_server: ServerConfig | None, resolved: ResolvedServerConfig
+) -> list[_Spawn]:
+    config = resolved.config
+    if (
+        resolved.source != "manifest"
+        or manifest_server is None
+        or not isinstance(config, LocalMcpServerConfig)
+    ):
+        return []
+    return [
+        _Spawn(
+            f"its {diag_name(target)} install argv",
+            "manifest/installer.py:JobManager.start_install",
+            tuple(argv) if isinstance(argv, (list, tuple)) else (argv,),
+            config.env,
+            None,
+        )
+        for target, argv in sorted(manifest_server.install.items())
+        if argv
+    ]
+
+
+#: Every code site that spawns, or adopts, a process as THIS server -> the
+#: builder of its spawn-set members (#295 board rounds 7-9). `_spawn_set` is
+#: built FROM this table, so dropping a site drops its members. The plan's
+#: spawn-site table lists every spawn primitive in ``src/pmcp`` with its
+#: disposition, and ``test_every_spawn_site_is_classified`` counts them with
+#: an import-resolving ``ast`` walk, so a new spawn path fails a test.
+_SERVER_SPAWN_SITES: dict[
+    str, Callable[[ServerConfig | None, ResolvedServerConfig], list[_Spawn]]
+] = {
+    # connect, lazy connect, restart, reconnect, the respawn after refresh,
+    # and update_server's restart: the resolved config's command + args, with
+    # the entry's env and cwd.
+    "client/manager.py:ClientManager._connect_stdio": _args_member,
+    # gateway.provision -> start_install spawns install[platform] for a
+    # manifest-sourced server; _finalize_server_ready adopts that process.
+    # Every non-empty platform (a superset of detect_platform() and its wsl ->
+    # linux fallback), with the same env (build_install_child_env) and pmcp's
+    # own cwd. A configured server is lazy-started from its own args.
+    "manifest/installer.py:JobManager.start_install": _install_members,
+}
+
+
+def _spawn_set(
+    manifest_server: ServerConfig | None, resolved: ResolvedServerConfig
+) -> list[_Spawn]:
+    """EVERY argv that can spawn or be adopted as this server, args first.
+
+    Built from ``_SERVER_SPAWN_SITES`` and computed before any verdict, so no
+    early return can skip a member (#295 board rounds 8-9).
+    """
+    members: list[_Spawn] = []
+    for build in _SERVER_SPAWN_SITES.values():
+        members.extend(build(manifest_server, resolved))
+    return members
+
+
+def _path_searches_the_cwd(path_var: str | None) -> bool:
+    """Would resolving a bare launcher name look inside the child's cwd?
+
+    On POSIX, when the child's ``PATH`` has a relative or empty entry
+    (``.:$PATH``, ``::``), or an entry through ``/proc`` (``/proc/self/cwd``
+    and kin resolve in whichever process looks). On Windows, no:
+    ``subprocess`` resolves the executable with the PARENT's current
+    directory and ``PATH`` (``CreateProcess``), never the child's ``cwd`` or
+    env, so an entry-set cwd cannot move the search (#295 board round 13, N-3;
+    round 12's "always on Windows" is corrected).
+    """
+    if _is_windows():
+        return False
+    entries = (path_var if path_var is not None else os.environ.get("PATH", "")).split(
+        os.pathsep
+    )
+    return any(
+        not entry or not os.path.isabs(entry) or entry.startswith("/proc/")
+        for entry in entries
+    )
+
+
+def _member_problems(
+    member: _Spawn,
+    reading: _Reading,
+    declared: frozenset[str],
+    path_var: str | None = None,
+) -> list[str]:
+    """Env and cwd problems of one member: the checks no exemption waives."""
+    problems: list[str] = []
+    package = reading.package or ""
+    for key in sorted(str(k) for k in (member.env or {})):
+        if not _entry_env_key_is_inert(reading.family, key, "", package, declared):
+            problems.append(
+                f"the entry's env sets {diag_name(key)}, which is not on the "
+                "list of keys known inert for it"
+            )
+    for key in reading.env_keys:
+        if not _entry_env_key_is_inert("docker", key, "", package, declared):
+            problems.append(
+                f"its argv sets container env {diag_name(key) if key else '<nothing>'}"
+            )
+    if member.cwd and reading.family in _CWD_CONFIG_FAMILIES:
+        problems.append(
+            "the entry sets a cwd (not shown), whose project configuration "
+            f"{_CWD_CONFIG_FAMILIES[reading.family]} reads"
+        )
+    elif (
+        member.cwd
+        and reading.family == "docker"
+        and (
+            not _is_bare_launcher(str(member.argv[0]), "docker")
+            or _path_searches_the_cwd(path_var)
+        )
+    ):
+        # The docker cwd exemption holds only for the BARE `docker` spelling
+        # searched through a PATH that cannot see the cwd. Any path spelling
+        # under an entry-set cwd can depend on it (`/proc/self/cwd/...`): loud
+        # (#295 board round 13, B-1).
+        # docker reads no cwd-relative configuration, but a bare `docker`
+        # resolved through a PATH that searches the cwd could be a binary the
+        # entry's cwd holds.
+        problems.append(
+            "the entry sets a cwd (not shown), and the child's "
+            "PATH would look for the launcher inside it"
+            if _is_bare_launcher(str(member.argv[0]), "docker")
+            else "the entry sets a cwd (not shown), and its "
+            "launcher is a path, which can depend on that cwd"
+        )
+    return problems
+
+
+@dataclass(frozen=True)
+class _Verdict:
+    """ONE judgement of a whole spawn set (#295 board round 9).
+
+    ``kind``: ``silent`` (every member runs the same exact pin, or the same
+    local binary, with inert env and cwd), ``unpinned``/``floating`` (args'
+    selector), or ``cannot_verify`` with ``detail``. ``reading`` is args'.
+    """
+
+    kind: str
+    reading: _Reading
+    detail: str | None = None
+
+
+@dataclass(frozen=True)
+class _Judged:
+    """ONE member as the per-member judge sees it: its reading and problems.
+
+    ``problems`` is ``_member_problems`` (env keys, container env keys, cwd).
+    ``all_problems`` adds the reading's own problem first. The verdict AND
+    the relaxer decision read this one object (#295 board round 17): they
+    cannot diverge.
+    """
+
+    member: _Spawn
+    reading: _Reading
+    problems: tuple[str, ...]
+
+    @property
+    def all_problems(self) -> tuple[str, ...]:
+        own = (self.reading.problem,) if self.reading.problem is not None else ()
+        return own + self.problems
+
+
+def _judge_members(
+    spawns: list[_Spawn],
+    declared: frozenset[str],
+    local_commands: frozenset[str] = frozenset(),
+    path_var: str | None = None,
+) -> list[_Judged]:
+    """The ONE place member problems are computed: read, then env and cwd."""
+    return [
+        _Judged(m, r, tuple(_member_problems(m, r, declared, path_var)))
+        for m in spawns
+        for r in [_read_argv(m.argv, local_commands, path_var)]
+    ]
+
+
+def _judge_spawn_set(
+    spawns: list[_Spawn],
+    declared: frozenset[str],
+    local_commands: frozenset[str] = frozenset(),
+    path_var: str | None = None,
+) -> _Verdict:
+    """Parse -> shape -> pin -> env -> cwd, for EVERY member, then combine."""
+    return _verdict_of(_judge_members(spawns, declared, local_commands, path_var))
+
+
+def _verdict_of(members: list[_Judged]) -> _Verdict:
+    """Combine the judged members into ONE verdict.
+
+    Every member is read and checked before any verdict is chosen. The
+    local-binary exemption only waives the PIN requirement, and only when
+    every member is literally the entry's own argv; it never waives the
+    shape, env or cwd checks (#295 board round 9, grok B2).
+    """
+    judged = [(j.member, j.reading, list(j.problems)) for j in members]
+    if not judged:
+        return _Verdict(
+            "cannot_verify",
+            _Reading("unreadable", "", problem="it has no argv pmcp can read"),
+            "it has no argv pmcp can read",
+        )
+    args_member, args_reading, args_problems = judged[0]
+    if args_reading.problem is not None:
+        return _Verdict("cannot_verify", args_reading, args_reading.problem)
+    pin_state: str | None = None
+    if args_reading.family != "local":
+        if args_reading.selector is None:
+            pin_state = "unpinned"
+        elif not args_reading.exact:
+            pin_state = "floating"
+    if pin_state is None and args_problems:
+        return _Verdict("cannot_verify", args_reading, args_problems[0])
+    for member, reading, problems in judged[1:]:
+        where = member.where()
+        if args_reading.family == "local":
+            if member.argv != args_member.argv:
+                return _Verdict(
+                    "cannot_verify",
+                    args_reading,
+                    f"{where} is not the entry's own command "
+                    f"(`{_render_argv(args_member.argv)}`), so the local-binary "
+                    "exemption does not apply",
+                )
+            continue
+        if pin_state is not None:
+            continue
+        if reading.problem is not None:
+            return _Verdict(
+                "cannot_verify",
+                args_reading,
+                f"{where} runs something pmcp cannot read ({reading.problem})",
+            )
+        if (reading.family, reading.package, reading.selector) != (
+            args_reading.family,
+            args_reading.package,
+            args_reading.selector,
+        ):
+            return _Verdict(
+                "cannot_verify",
+                args_reading,
+                f"{where} does not run {diag_name(args_reading.package)}@{diag_selector(args_reading.selector)} "
+                + (
+                    "(it runs a local command)"
+                    if reading.family == "local"
+                    else f"(it names {diag_name(reading.package)}@{diag_selector(reading.selector or 'latest')})"
+                ),
+            )
+        if problems:
+            return _Verdict(
+                "cannot_verify",
+                args_reading,
+                f"{where} is not proven to run the pin: {problems[0]}",
+            )
+    if pin_state is not None:
+        return _Verdict(pin_state, args_reading)
+    return _Verdict("silent", args_reading)
+
+
+def _member_relaxer(
+    judged: _Judged,
+    relaxers: tuple[str, ...],
+    project_root: Path | None,
+) -> tuple[str | None, bool]:
+    """Does THIS spawn member talk to a self-hosted backend? ``(key, may)``.
+
+    Read from the per-member judge's result (``_Judged``), never recomputed
+    (#295 board round 17). ``may`` is set for a member with ANY judge problem:
+    an argv pmcp cannot fully read, a non-allowlisted env key (``NODE_OPTIONS``
+    can set the relaxer inside the client), a container env key, an entry cwd
+    (a ``.env`` the client's own dotenv reads), a launcher-identity problem.
+    Only a member with NO problem is judged by its env alone: the container's
+    env for a clean docker run (a bare ``-e KEY`` takes docker's own value;
+    the last one wins), else its process env. ``key`` names a relaxer the
+    member is SEEN to receive (for a docker member with a problem, docker's
+    own env is not the container's unless its argv reads cleanly).
+    """
+    member, reading = judged.member, judged.reading
+    process_env = sanitized_subprocess_env(member.env, project_root)
+
+    def first_usable(env: Mapping[str, str]) -> str | None:
+        return next(
+            (k for k in relaxers if is_usable_credential_value(env.get(k))), None
+        )
+
+    container: dict[str, str] = {}
+    for key, value in reading.container_env:
+        if value is not None:
+            container[key] = value
+        elif key in process_env:
+            container[key] = process_env[key]
+        else:
+            container.pop(key, None)
+    problems = judged.all_problems
+    if reading.family == "docker":
+        if reading.problem is not None:
+            return None, bool(relaxers)
+        if problems:
+            return (
+                first_usable(process_env) or first_usable(container),
+                bool(relaxers),
+            )
+        return first_usable(container), False
+    return first_usable(process_env), bool(problems) and bool(relaxers)
+
+
+def _may_talk_reason(judged: _Judged, relaxer: str) -> str:
+    """Why a member MAY set the relaxer, naming the judge's actual problem."""
+    where = judged.member.where().rstrip(",")
+    if judged.reading.env_file:
+        return f"its docker --env-file, which pmcp cannot read, can set {diag_name(relaxer)} in the container"
+    if judged.reading.problem is not None:
+        return f"{where}, which pmcp cannot fully read, can set {diag_name(relaxer)} for the client"
+    return f"{where}: {judged.problems[0]}, so it can set {diag_name(relaxer)} for the client"
+
+
+def _unverifiable_warning(server_name: str, exc: BaseException) -> str:
+    """The per-server "cannot verify" warning when judging it failed outright."""
+    return (
+        f"pmcp cannot verify that '{server_name}''s client is pinned: evaluating "
+        f"it failed ({type(exc).__name__})."
+    )
+
+
+def _unpinned_self_hosted_warning(
+    server_name: str,
+    manifest_server: ServerConfig | None,
+    resolved: ResolvedServerConfig,
+    project_root: Path | None,
+) -> str | None:
+    """Warn when a self-hosted backend is served by an unpinned client.
+
+    Consiliency/pmcp#294, proposal 2. Fires only when BOTH hold:
+
+    * the manifest entry's ``api_key_optional_when`` relaxer is active on the
+      environment the child ACTUALLY receives: ``sanitized_subprocess_env``
+      of the resolved config's env, which is exactly what ``client/manager.py``
+      spawns with -- the gateway's own environment minus managed secrets, plus
+      the entry's env. So a relaxer exported in the shell that started pmcp
+      counts (Consiliency/pmcp#295 board, claude F2). This is an ADVISORY
+      read: the credential gates keep ``child_env=config.env`` and are not
+      touched (#124: for a gate, ignoring the ambient value is the safe
+      direction; for a warning, including it is); and
+    * ``_judge_spawn_set`` does not find every argv that can run as this
+      server (``_spawn_set``) holding the same exact pin, or the same local
+      binary, with a recognised shape and an inert env and cwd.
+
+    The host's own launcher configuration is trusted and out of scope (the
+    trust boundary above). A vendor-hosted server (relaxer inactive) never
+    warns: following the vendor's latest client is the intended default there.
+    """
+    if manifest_server is None or not isinstance(resolved.config, LocalMcpServerConfig):
+        return None
+    child_env = sanitized_subprocess_env(resolved.config.env, project_root)
+    # Advisory: the relaxer keys are the shipped ones UNION the overlay's
+    # (`_warning_relaxers`); a value counts when `credential_requirement`
+    # would count it (`is_usable_credential_value`). The gate is untouched.
+    # Whether the warning APPLIES is decided per spawn member, from the ONE
+    # parser's reading of that member (`_member_relaxer`): the container's env
+    # for a clean docker run, "may" for a docker argv pmcp cannot fully read,
+    # the member's own env otherwise. It applies if ANY member applies, and
+    # it is decided before, and independently of, the pin verdict (#295
+    # board round 14, B-1; round 13's N-2 used a second reader).
+    relaxers = _warning_relaxers(server_name, manifest_server)
+    path_var = child_env.get("PATH")
+    local_commands = _shipped_local_commands(server_name)
+    declared = _declared_env_keys(server_name)
+    try:
+        judged = _judge_members(
+            _spawn_set(manifest_server, resolved), declared, local_commands, path_var
+        )
+        members = [(j,) + _member_relaxer(j, relaxers, project_root) for j in judged]
+    except Exception as exc:  # contained to this server (round 9, codex 2)
+        if not relaxers:
+            return None
+        return (
+            f"'{server_name}' may talk to a self-hosted backend, and pmcp cannot "
+            f"verify that its client is pinned: evaluating its argvs failed "
+            f"({type(exc).__name__})."
+        )
+    relaxed_by = next((key for _j, key, _may in members if key is not None), None)
+    unsure = next((j for j, _key, may in members if may), None)
+    if relaxed_by is not None:
+        head = f"'{server_name}' talks to a self-hosted backend ({diag_name(relaxed_by)} is set)"
+    elif unsure is not None:
+        head = (
+            f"'{server_name}' may talk to a self-hosted backend "
+            f"({_may_talk_reason(unsure, relaxers[0])})"
+        )
+    else:
+        return None
+    try:
+        verdict = _verdict_of(judged)
+    except Exception as exc:  # contained to this server (round 9, codex 2)
+        return (
+            f"{head}, but pmcp cannot verify that its client is pinned: evaluating "
+            f"its argvs failed ({type(exc).__name__})."
+        )
+    reading = verdict.reading
+    if verdict.kind == "silent":
+        return None
+    if verdict.kind == "cannot_verify":
+        if reading.problem is not None:
+            return (
+                f"{head}, but pmcp cannot verify that its client is pinned: "
+                f"{verdict.detail}. Launch the client with npx, uvx, cargo or docker "
+                "and an exact pin, in a shape pmcp recognises."
+            )
+        if reading.family == "local":
+            return (
+                f"{head}, but pmcp cannot verify that its client is pinned: its args "
+                f"run a local command, but {verdict.detail}."
+            )
+        return (
+            f"{head}, but pmcp cannot verify that its client is pinned: the argv "
+            f"pins {reading.family}:{diag_name(reading.package)} at {diag_selector(reading.selector)}, but "
+            f"{verdict.detail}, which can change what runs for that pin. Remove it "
+            "from the entry to hold the top-level package at the pinned version."
+        )
+    if reading.family == "npm" and resolved.source == "manifest":
+        remedy = (
+            f"pin it with `server_version: {{{server_name}: <version>}}` in "
+            "~/.pmcp/manifest.yaml"
+        )
+    elif reading.family == "docker":
+        remedy = (
+            "pin an image digest (`image@sha256:...`) in the args of the config "
+            "that launches it"
+        )
+    else:
+        remedy = "pin an exact version in the args of the config that launches it"
+    state = (
+        f"floats on '{diag_selector(reading.selector)}' "
+        f"({_floating_reason(reading.family, reading.selector or '')})"
+        if verdict.kind == "floating"
+        else "is unpinned"
+    )
+    return (
+        f"{head} but its client {reading.family}:{diag_name(reading.package)} {state}, so a "
+        "spawn or `pmcp update` can move it ahead of the server; " + remedy + "."
+    )
+
+
 # Human-readable label for a ResolvedServerConfig.source, used in messages
 # that need to point an operator at the file a pin (or other override) came
 # from.
@@ -2254,6 +3816,8 @@ class GatewayTools:
                 )
             )
 
+        self._attach_version_pin_warnings(servers)
+
         diagnostics = self._transport_diagnostics.model_copy()
         diagnostics.audit_buffer_size = self._audit_events.maxlen or len(
             self._audit_events
@@ -2276,6 +3840,76 @@ class GatewayTools:
             audit_events=list(self._audit_events) or None,
         )
 
+    def _version_pin_warning(self, server_name: str) -> str | None:
+        """`_unpinned_self_hosted_warning` for *server_name*'s effective config."""
+        manifest_server = load_manifest().get_server(server_name)
+        if manifest_server is None or not _warning_relaxers(
+            server_name, manifest_server
+        ):
+            return None
+        resolved = self._load_all_configured_servers().get(
+            server_name
+        ) or manifest_server_to_config(manifest_server)
+        return _unpinned_self_hosted_warning(
+            server_name, manifest_server, resolved, self._project_root
+        )
+
+    #: (manifest_sources_fingerprint(), relaxer-declaring manifest entries).
+    _relaxable_cache: tuple[tuple[object, ...], dict[str, ServerConfig]] | None = None
+
+    def _relaxable_manifest_servers(self) -> dict[str, ServerConfig]:
+        """Manifest entries that declare ``api_key_optional_when``, cached.
+
+        gateway.health is polled; ``load_manifest()`` costs ~90 ms and logs a
+        WARNING per call while an unapproved project overlay is present. Re-load
+        only when a manifest source (or the trust store) changes on disk
+        (Consiliency/pmcp#295 board, claude F3).
+        """
+        key = manifest_sources_fingerprint()
+        cached = self._relaxable_cache
+        if cached is not None and cached[0] == key:
+            return cached[1]
+        relaxable = {
+            name: server
+            for name, server in load_manifest().servers.items()
+            if _warning_relaxers(name, server)
+        }
+        self._relaxable_cache = (key, relaxable)
+        return relaxable
+
+    def _attach_version_pin_warnings(self, servers: list[ServerHealthInfo]) -> None:
+        """Add the unpinned-self-hosted warning to each health entry it applies to.
+
+        Judged on the config the gateway actually CONNECTED the server with
+        (``get_connected_configs``), not on a fresh config read: that is the
+        argv and env that are running, and it costs no file I/O. A server that
+        is not connected is not judged here (update_server still warns for
+        it). Advisory: it never fails health. Contained PER SERVER (#295 board
+        round 9, codex 2): a failure judging one server becomes that server's
+        own "cannot verify" warning and never suppresses another server's.
+        """
+        try:
+            relaxable = self._relaxable_manifest_servers()
+            wanted = [info for info in servers if info.name in relaxable]
+            if not wanted:
+                return
+            connected = self._client_manager.get_connected_configs()
+        except Exception as exc:  # advisory; never fail health over it
+            logger.debug(f"version-pin warnings skipped: {type(exc).__name__}")
+            return
+        for info in wanted:
+            try:
+                resolved = connected.get(info.name)
+                if resolved is None:
+                    continue
+                warning = _unpinned_self_hosted_warning(
+                    info.name, relaxable[info.name], resolved, self._project_root
+                )
+            except Exception as exc:
+                warning = _unverifiable_warning(info.name, exc)
+            if warning:
+                info.warnings.append(warning)
+
     def _config_source_paths_by_server(self) -> dict[str, tuple[str, str]]:
         paths: dict[str, tuple[str, str]] = {}
         for source in load_config_sources(
@@ -4898,8 +6532,28 @@ class GatewayTools:
         Freezing the ambient environment across the update is deliberately NOT
         done here; it would mean threading a frozen env through ClientManager,
         which is a separate concern from this TOCTOU.
+
+        Wrapped (Consiliency/pmcp#294): the unpinned-self-hosted warning is
+        computed on the configuration as it stands AFTER the update attempt and
+        never changes ``ok`` -- an unpinned self-hosted client is still updated,
+        and still warned about, because it is still unpinned.
         """
+        # Validated HERE, in the handler the advertised schema is derived for
+        # (tests/test_gateway_tool_schemas.py, Consiliency/pmcp#236 piece A).
         parsed = UpdateServerInput.model_validate(input_data)
+        result = await self._update_server_unwarned(parsed)
+        try:
+            warning = self._version_pin_warning(result.server)
+        except Exception as exc:  # advisory; never fail the update over it
+            warning = _unverifiable_warning(result.server, exc)
+        if warning:
+            result.warnings.append(warning)
+        return result
+
+    async def _update_server_unwarned(
+        self, parsed: UpdateServerInput
+    ) -> UpdateServerOutput:
+        """The body of ``update_server``; its docstring states the contracts."""
         server_name = parsed.server_name
 
         # Resolve the server's EFFECTIVE config through the exact same
@@ -4994,14 +6648,121 @@ class GatewayTools:
             source_desc = _CONFIG_SOURCE_LABELS.get(
                 resolved_config.source, f"the {resolved_config.source} config"
             )
+            # "Pinned at X, newer available: Y" (Consiliency/pmcp#294). A
+            # registry READ only -- nothing is probed, fetched or restarted.
+            # `get_package_version` strips the pin off the spec, so this is the
+            # registry's latest, compared three-way (never negated, #164).
+            latest_available, _ = await get_package_version(
+                command, args, server_env, server_cwd, timeout=5.0
+            )
+            # A range, a dist-tag or a docker tag still stops this tool (the
+            # operator chose it), but it is not a pin: it can resolve to
+            # another artifact at a later spawn, so it is reported as
+            # FLOATING, never as "pinned at ^3.25.0" (Consiliency/pmcp#295
+            # board, codex P2 / claude F1; a docker tag since round 4, C2).
+            # The label comes from the SAME judge as the warning: every argv
+            # that can run as this server (`_spawn_set`), read by ONE parser
+            # per launcher, with shape, pin, env and cwd checked for each
+            # (#295 board round 9). `[PINNED]` only when that judge finds one
+            # exact pin -- and the very pin the refusal above read; if the two
+            # readings disagree (a repeated `--from`), it is not a pin.
+            shape_problem: str | None
+            # `[PINNED]` comes ONLY from the one judge: `judge_pin` is the
+            # selector of a `silent` verdict, and nothing else can make the
+            # label exact. `pinned_to` (main's reader) only decides that
+            # update_server stops, never that it says "pinned" (#295 board
+            # round 19).
+            judge_pin: str | None = None
+            try:
+                verdict = _judge_spawn_set(
+                    _spawn_set(
+                        load_manifest().get_server(server_name), resolved_config
+                    ),
+                    _declared_env_keys(server_name),
+                    _shipped_local_commands(server_name),
+                    sanitized_subprocess_env(
+                        resolved_config.config.env
+                        if isinstance(resolved_config.config, LocalMcpServerConfig)
+                        else None,
+                        self._project_root,
+                    ).get("PATH"),
+                )
+                if verdict.kind == "silent" and verdict.reading.family != "local":
+                    if verdict.reading.selector == pinned_to:
+                        shape_problem = None
+                        judge_pin = verdict.reading.selector
+                    else:
+                        shape_problem = (
+                            "pmcp's reading of the argv does not name this pin"
+                        )
+                elif verdict.kind == "floating":
+                    shape_problem = _floating_reason(package_type, pinned_to)
+                else:
+                    shape_problem = verdict.detail or _floating_reason(
+                        package_type, pinned_to
+                    )
+            except Exception as exc:  # contained to this server (round 9, codex 2)
+                shape_problem = f"evaluating its argvs failed ({type(exc).__name__})"
+            exact = judge_pin is not None and shape_problem is None
+            if exact and judge_pin is not None:
+                pinned_to = judge_pin
+            # SemVer build metadata selects nothing: `pkg@3.25.5+x` runs
+            # 3.25.5. Report what runs, and say the suffix was ignored -- the
+            # manifest path refuses such a pin outright; a configured argv is
+            # the operator's own and is labelled honestly instead (#295 board
+            # round 2, N3).
+            build_note = ""
+            if exact and package_type in ("npm", "cargo") and "+" in pinned_to:
+                pinned_to, _, build = pinned_to.partition("+")
+                build_note = (
+                    f" (build metadata '+{diag_selector(build)}' is ignored by "
+                    f"{package_type})"
+                )
+            # Shown only through the closed selector grammar: main's reader can
+            # hand back raw requirement text (#295 board round 20).
+            shown_pin = diag_selector(pinned_to)
+            comparison = (
+                compare_versions(pinned_to, latest_available, package_type)
+                if latest_available and exact
+                else None
+            )
+            if not exact:
+                availability = (
+                    f"'{shown_pin}' does not hold the client at one version "
+                    f"({shape_problem or _floating_reason(package_type, shown_pin)}): "
+                    "a later spawn can run "
+                    f"another one (latest: {latest_available or 'unknown'})."
+                )
+            elif comparison == "newer":
+                availability = (
+                    f"Pinned at {shown_pin}, newer available: {latest_available}."
+                )
+            elif comparison == "not_newer":
+                availability = f"Pinned at {shown_pin}, up to date."
+            elif comparison == "incomparable":
+                availability = (
+                    f"Pinned at {shown_pin}; the latest ({latest_available}) "
+                    "cannot be ordered against it."
+                )
+            else:
+                availability = (
+                    f"Pinned at {shown_pin}; the latest version could not be "
+                    "determined."
+                )
             return UpdateServerOutput(
                 ok=False,
                 server=server_name,
                 package_type=package_type,
                 package_name=package_name,
+                pinned_version=shown_pin if exact else None,
+                floating_selector=None if exact else shown_pin,
+                latest_available=latest_available,
+                latest_comparison=comparison,
                 message=(
-                    f"'{server_name}' is pinned to '{pinned_to}' in {source_desc} "
-                    f"({command} {' '.join(args)}). gateway.update_server will not "
+                    f"'{server_name}' is {'pinned to' if exact else 'held at'} "
+                    f"'{shown_pin}'{build_note} in {source_desc} "
+                    f"({_render_argv([command, *args])}). {availability} "
+                    "gateway.update_server will not "
                     "move a pinned server to the latest version -- edit or remove "
                     "the pin in that config to allow updates."
                 ),
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 95b5a53..0f273bb 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -1028,6 +1028,9 @@ class ServerHealthInfo(BaseModel):
     auth_metadata: AuthMetadataInfo | None = None
     auth_challenge: AuthChallengeInfo | None = None
     url_elicitations: list[UrlElicitationInfo] | None = None
+    # Advisory, never a status change: e.g. an unpinned client talking to a
+    # self-hosted backend (Consiliency/pmcp#294).
+    warnings: list[str] = Field(default_factory=list)
 
 
 class HealthOutput(BaseModel):
@@ -1528,6 +1531,21 @@ class UpdateServerOutput(BaseModel):
     cancelled_request_count: int = 0
     cancelled_task_count: int = 0
     message: str
+    # Set only when the server is pinned and therefore was not moved
+    # (Consiliency/pmcp#294): the pin, the registry's latest, and
+    # `compare_versions(pinned_version, latest_available)` -- None when the
+    # latest could not be fetched. Three-way on purpose (#164).
+    pinned_version: str | None = None
+    # Set instead of pinned_version when the config holds the server at a
+    # range, a dist-tag or a docker tag (`^3.25.0`, `next`, `img:3.25.5`): the
+    # tool still does not move it, but it is not a pin -- it can resolve to
+    # another artifact at a later spawn (#295 board).
+    floating_selector: str | None = None
+    latest_available: str | None = None
+    latest_comparison: Literal["newer", "not_newer", "incomparable"] | None = None
+    # Advisory; never changes `ok`. E.g. an unpinned client talking to a
+    # self-hosted backend.
+    warnings: list[str] = Field(default_factory=list)
 
 
 class AuthConnectInput(GatewayArguments):
diff --git a/tests/test_pkgid_panel_fixes.py b/tests/test_pkgid_panel_fixes.py
index 41cadd8..aa7cf6a 100644
--- a/tests/test_pkgid_panel_fixes.py
+++ b/tests/test_pkgid_panel_fixes.py
@@ -894,6 +894,14 @@ async def test_update_server_still_refuses_a_pinned_manifest_server_as_before(
         manifest_servers={"shipped": server},
     )
 
+    # The pinned branch now reads the registry's latest to report "newer
+    # available" (Consiliency/pmcp#294). Stub it: this refusal path was offline
+    # before that change and must stay offline.
+    async def no_registry(*_args: Any, **_kwargs: Any) -> tuple[None, str]:
+        return (None, "npm")
+
+    monkeypatch.setattr(handlers_module, "get_package_version", no_registry)
+
     result = await gateway.update_server({"server_name": "shipped"})
 
     assert result.ok is False
```

## Test bodies

`tests/test_version_pin.py`, verbatim from the spike (`ruff format`-clean):

```python
"""First-class client version pins (Consiliency/pmcp#294, proposals 1 and 2).

Proposal 1: a manifest entry may carry ``version:``, and an overlay may set it
on an existing (e.g. shipped) entry with ``server_version:`` -- without
restating the install matrix. The pin is materialised into the npx package
slot of ``args`` AND every ``install`` argv, keeping the package NAME from the
entry, so an overlay can choose a version of the same package and nothing
else. ``gateway.update_server`` then reports "pinned at X, newer available: Y".

Proposal 2: an entry whose ``api_key_optional_when`` relaxer is active (a
self-hosted backend is configured) and whose client is unpinned carries a
warning in ``gateway.health`` and ``gateway.update_server``.

Offline: npm argv is read with the node-less tables (``npm_tables``), and the
registry lookup is a monkeypatched ``get_package_version``.
"""

from __future__ import annotations

import collections
import logging
import os
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from pmcp.config.loader import _merge_manifest_defaults, manifest_server_to_config
from pmcp.manifest.loader import (
    Manifest,
    ServerConfig,
    credential_requirement,
    load_manifest,
    manifest_sources_fingerprint,
    split_plain_registry_spec,
)
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import _config_runs_exactly
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.types import (
    LocalMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)

PLATFORMS = ("mac", "linux", "wsl", "windows")


@pytest.fixture(autouse=True)
def _isolate_overlays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ambient overlay: HOME is already isolated by conftest; add a clean cwd."""
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)


@pytest.fixture(autouse=True)
def _no_ambient_npm_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """No npm configuration from the host's environment (dev0 exports
    npm_config_cache): the warning reads the child's environment, and a
    test that wants a key sets it itself."""
    import os

    for key in list(os.environ):
        if key.lower().startswith("npm_config_") or key.upper() == "NODE_OPTIONS":
            monkeypatch.delenv(key)


#: The handler tests' entries stand for SHIPPED manifest entries: pmcp's own
#: manifest declares their credential and relaxer keys (rev 9: only shipped
#: declarations exempt a key). Names outside this set are overlay-only.
_TEST_SHIPPED_NAMES = frozenset(
    {
        "fc",
        "dk",
        "dg",
        "du",
        "dh",
        "dc1",
        "uv",
        "cg",
        "sh1",
        "ok1",
        "rn1",
        "ut1",
        "ut2",
        "ow",
        "cf",
    }
)


_TEST_SHIPPED_LOCAL_COMMANDS = frozenset({"fc-mcp", "/opt/fc/bin/fc-mcp"})


@pytest.fixture(autouse=True)
def _test_entries_are_shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # getattr/raising=False: the fixture must not error on a tree without the
    # rev-9 seam, so red-on-previous-revision runs measure behaviour.
    real = getattr(handlers_module, "_shipped_manifest_declarations", dict)()
    table = dict(real)
    for name in _TEST_SHIPPED_NAMES:
        table[name] = frozenset({"SELFHOST_API_KEY", "SELFHOST_API_URL"})
    monkeypatch.setattr(
        handlers_module, "_shipped_manifest_declarations", lambda: table, raising=False
    )
    # Rev 12: the test entries' shipped manifest names these local server
    # binaries -- the positive evidence the local-binary exemption requires.
    real_local = getattr(handlers_module, "_shipped_local_commands", None)
    if real_local is not None:
        monkeypatch.setattr(
            handlers_module,
            "_shipped_local_commands",
            lambda name: _TEST_SHIPPED_LOCAL_COMMANDS
            if name in _TEST_SHIPPED_NAMES
            else real_local(name),
        )


@pytest.fixture
def npm_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read npm argv with the node-less tables, whatever npm the host has."""
    from pmcp.manifest import version_checker

    def tables(
        args: list[str], command: str, env: Any = None, cwd: Any = None
    ) -> str | None:
        return version_checker._npm_package_arg_from_tables(args, command)

    monkeypatch.setattr(version_checker, "_npm_package_arg", tables)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", tables)


def _user_overlay(text: str) -> None:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# Proposal 1 -- the manifest side
# ---------------------------------------------------------------------------


def test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns() -> None:
    """One overlay line pins args AND every install argv; nothing else changes."""
    shipped = load_manifest().servers["firecrawl"]
    assert shipped.version is None
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')

    pinned = load_manifest().servers["firecrawl"]

    assert pinned.version == "3.25.5"
    assert pinned.args == ["-y", "firecrawl-mcp@3.25.5"]
    assert pinned.install == {
        p: ["npx", "-y", "firecrawl-mcp@3.25.5"] for p in PLATFORMS
    }
    # The provision gate's own definition of "runs exactly this spec".
    assert _config_runs_exactly(pinned, "firecrawl-mcp@3.25.5")
    for field_name in ("command", "env_var", "api_key_optional_when", "extra_env"):
        assert getattr(pinned, field_name) == getattr(shipped, field_name)


def test_version_replaces_an_existing_tag_on_a_scoped_package() -> None:
    """``@playwright/mcp@latest`` becomes ``@playwright/mcp@1.2.3``, scope kept."""
    _user_overlay('server_version:\n  playwright: "1.2.3"\n')

    pinned = load_manifest().servers["playwright"]

    assert pinned.args == ["-y", "@playwright/mcp@1.2.3"]
    assert _config_runs_exactly(pinned, "@playwright/mcp@1.2.3")


def test_version_key_on_a_whole_servers_entry_is_materialised() -> None:
    _user_overlay(
        """
servers:
  pinned-custom:
    description: "custom"
    keywords: [c]
    install:
      linux: ["npx", "-y", "custom-mcp"]
    command: "npx"
    args: ["-y", "custom-mcp", "--port", "3000"]
    version: "2.0.0-rc.1"
"""
    )

    entry = load_manifest().servers["pinned-custom"]

    assert entry.args == ["-y", "custom-mcp@2.0.0-rc.1", "--port", "3000"]
    assert entry.install == {"linux": ["npx", "-y", "custom-mcp@2.0.0-rc.1"]}


@pytest.mark.parametrize(
    "raw",
    [
        '"^3.25.5"',  # range: re-resolves at every spawn
        '"~3.25.5"',
        '"3.x"',
        '"*"',
        '">=3.25.0"',
        '"latest"',  # dist-tag: the registry moves it
        '"next"',
        '"v3.25.5"',  # not SemVer
        "3.25",  # YAML float
        '"3.25"',
        '"3.25.5 --registry=http://evil.test"',
        '"evil-pkg@1.0.0"',  # an attempt to name a different package
        '"npm:evil-pkg@1.0.0"',
        '"../../tmp/x"',
        '""',
        "true",
        '"3.25.5+evil"',  # build metadata: npm ignores it, reports would echo it
        # SemVer-shaped tarballs: npa checks isFileType BEFORE reading a version,
        # so each of these makes npx run a LOCAL FILE (round 3, B1').
        '"3.25.5-evil.tgz"',
        '"1.0.0-x.TAR"',
        '"1.0.0-a.tar.gz"',
        '"1.2.3-X.Tar.Gz"',
        # npm 10's isFileType (npm-package-arg 12.x) has an unescaped dot in
        # `tar.gz`, so `tar-gz` is a tarball too; main's rule follows it.
        '"1.0.0-x.tar-gz"',
        # A core part above 2**53-1 is a dist-TAG to npm, not a version.
        '"9007199254740992.0.0"',
    ],
)
def test_server_version_refuses_anything_but_one_exact_version(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    shipped = load_manifest().servers["firecrawl"]
    _user_overlay(f"server_version:\n  firecrawl: {raw}\n")

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == shipped.args
    assert entry.install == shipped.install
    assert any("firecrawl" in m and "server_version" in m for m in _warnings(caplog))


def test_server_version_cannot_create_a_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _user_overlay('server_version:\n  no-such-server: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()

    assert "no-such-server" not in manifest.servers
    assert any("no-such-server" in m for m in _warnings(caplog))


def test_unapproved_project_server_version_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    monkeypatch.chdir(tmp_path / "proj")

    entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == ["-y", "firecrawl-mcp"]


def test_approved_project_server_version_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    assert load_manifest().servers["firecrawl"].args == ["-y", "firecrawl-mcp@3.25.5"]


def test_version_on_a_uvx_server_is_refused_with_the_escape_hatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shipped = load_manifest().servers["fetch"]
    assert shipped.command == "uvx"
    _user_overlay('server_version:\n  fetch: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["fetch"]

    assert entry.version is None
    assert entry.args == shipped.args
    assert any("npx" in m and ".mcp.json" in m for m in _warnings(caplog))


def test_version_is_refused_when_an_install_argv_names_another_package(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pinning args but not install would approve X and run latest."""
    _user_overlay(
        """
servers:
  split-brain:
    description: "args and install disagree"
    keywords: [s]
    install:
      linux: ["npx", "-y", "other-mcp"]
    command: "npx"
    args: ["-y", "split-mcp"]
server_version:
  split-brain: "1.0.0"
"""
    )

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["split-brain"]

    assert entry.version is None
    assert entry.args == ["-y", "split-mcp"]
    assert entry.install == {"linux": ["npx", "-y", "other-mcp"]}


# One case per npm-package-arg class that is NOT a registry version/tag
# (the plan's class table). Round 2 (B1) found the tarball class missing: every
# earlier file/URL case carried a `:` prefix, which the old regex excluded
# anyway, so no test exercised a letter-led tarball.
_NON_PLAIN_SLOTS = {
    "alias": "myalias@npm:firecrawl-mcp@3.25.5",
    "url": "t@https://evil.test/t.tgz",
    "git-url": "t@git+https://evil.test/t.git",
    "hosted-shortcut": "t@github:evil/x",
    "hosted-path": "t@evil/x",
    "git-ssh": "t@git@github.com:evil/x",
    "file-prefix": "t@file:../evil",
    "relative-dir": "t@../evil",
    "home-dir": "t@~/evil",
    "absolute-dir": "t@/abs/evil",
    "tarball-tgz": "t@corp.tgz",
    "tarball-TGZ-mixed": "firecrawl-mcp@corp-mcp.TGZ",
    "tarball-tar": "t@x.tar",
    "tarball-tar.gz": "t@x.tar.gz",
    "tarball-Tar.Gz": "@s/p@X.Tar.Gz",
    "bare-tarball-tgz": "corp.tgz",
    "bare-tarball-TAR": "x.TAR",
    "tarball-name-with-version": "corp.tgz@3.25.5",
    "range-caret": "t@^3.25.0",
    "range-gte": "t@>=1.0.0",
    "range-x": "t@x",
    "range-X": "t@X",
    "range-v-partial": "t@v1.2.x",
    "tag-trailing-newline": "t@latest\n",
    "not-uri-safe-tag": "t@tag!",
    # Round 3 (B1'): a strict-SemVer selector with a tarball tail is a FILE to
    # npa, which checks isFileType before it reads a version. A fixed,
    # node-free sample of the generated corpus's classes (Verification step 10).
    "semver-tarball-prerelease": "firecrawl-mcp@3.25.5-corp.tgz",
    "semver-tarball-TAR": "t@1.0.0-x.TAR",
    "semver-tarball-build": "t@3.25.5+b.tar.gz",
    "semver-tarball-scoped": "@s/p@1.2.3-X.Tar.Gz",
    "semver-tarball-npm10-tar-gz": "t@1.0.0-x.tar-gz",
    "tarball-npm10-tar-gz": "t@corp.tar-gz",
    "oversized-core-is-a-tag": "t@9007199254740992.0.0",
    # Round 3 (N-a): loose SemVer allows any run of leading v's.
    "range-vv": "t@vv1",
    "range-vvX": "t@vvX",
    "range-v-xbeta": "t@v1.X.xbeta",
    "version-vv": "t@vv1.2.3",
    # Round 3 (N-c): validate-npm-package-name's exclusion list, any case.
    "excluded-node_modules": "node_modules",
    "excluded-Node_Modules-versioned": "Node_Modules@1.0.0",
    "excluded-favicon": "favicon.ico@latest",
}


def _odd_slot_overlay(slot: str, version: str = "3.25.5") -> None:
    _user_overlay(
        yaml.safe_dump(
            {
                "servers": {
                    "odd-slot": {
                        "description": "odd slot",
                        "keywords": ["o"],
                        "install": {"linux": ["npx", "-y", slot]},
                        "command": "npx",
                        "args": ["-y", slot],
                        "version": version,
                    }
                }
            }
        )
    )


@pytest.mark.parametrize(
    "slot", list(_NON_PLAIN_SLOTS.values()), ids=list(_NON_PLAIN_SLOTS)
)
def test_version_refuses_a_slot_that_is_not_a_plain_registry_spec(
    slot: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Only ``name`` or ``name@<version-or-tag>`` may have its version replaced."""
    _odd_slot_overlay(slot)

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["odd-slot"]

    assert split_plain_registry_spec(slot) is None
    assert entry.version is None
    assert entry.args == ["-y", slot]
    assert entry.install == {"linux": ["npx", "-y", slot]}
    assert any("plain registry" in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    ("slot", "pinned"),
    [
        ("t", "t@3.25.5"),  # npa: registry range `*`
        ("t@1.0.0", "t@3.25.5"),  # npa: version
        ("t@1.0.0-rc.1", "t@3.25.5"),
        ("t@next", "t@3.25.5"),  # npa: tag
        ("t@beta-2.tgzx", "t@3.25.5"),  # tag: `.tgzx` is not a tarball suffix
        ("@s/p", "@s/p@3.25.5"),
        ("@s/p.tgz", "@s/p.tgz@3.25.5"),  # a SCOPED name is never a file to npa
        ("@s/p@latest", "@s/p@3.25.5"),
    ],
)
def test_version_pins_every_plain_registry_class(slot: str, pinned: str) -> None:
    _odd_slot_overlay(slot)

    entry = load_manifest().servers["odd-slot"]

    assert entry.args == ["-y", pinned]
    assert entry.version == "3.25.5"


def test_version_replaces_a_dist_tag_slot() -> None:
    """A dist-tag is part of the plain-registry grammar and is replaced."""
    _user_overlay(
        """
servers:
  tagged:
    description: "tagged"
    keywords: [t]
    install:
      linux: ["npx", "-y", "tagged-mcp@next"]
    command: "npx"
    args: ["-y", "tagged-mcp@next"]
    version: "1.0.0"
"""
    )

    assert load_manifest().servers["tagged"].args == ["-y", "tagged-mcp@1.0.0"]


@pytest.mark.parametrize(
    "shape",
    [
        'command: "npx"\n    args: ["-y", 123]',
        'command: "npx"\n    args: ["-y", "ok-mcp"]\n    install:\n      linux: ["npx", "-y", 123]',
        'command: 123\n    args: ["-y", "ok-mcp"]',
    ],
)
def test_a_pin_on_a_malformed_entry_costs_only_that_entry(
    shape: str, caplog: pytest.LogCaptureFixture
) -> None:
    """HEAD loads every entry of this overlay; the pin must not change that."""
    shipped_count = len(load_manifest().servers)
    _user_overlay(
        f"""
servers:
  malformed:
    description: "non-string argv"
    keywords: [m]
    {shape}
    version: "1.2.3"
"""
    )

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()  # must not raise

    assert len(manifest.servers) == shipped_count + 1
    assert manifest.servers["malformed"].version is None
    assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp"]
    assert any("malformed" in m for m in _warnings(caplog))


def test_a_config_entry_without_a_command_inherits_the_pin() -> None:
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest_servers = load_manifest().servers

    merged = _merge_manifest_defaults(
        "firecrawl", LocalMcpServerConfig(command="", args=[]), manifest_servers
    )

    assert merged is not None
    assert merged.args == ["-y", "firecrawl-mcp@3.25.5"]


def test_explicit_config_args_win_over_the_manifest_pin() -> None:
    """``.pmcp.json`` with its own command/args is the operator's override."""
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest_servers = load_manifest().servers

    merged = _merge_manifest_defaults(
        "firecrawl",
        LocalMcpServerConfig(command="npx", args=["-y", "firecrawl-mcp@3.20.0"]),
        manifest_servers,
    )

    assert merged is not None
    assert merged.args == ["-y", "firecrawl-mcp@3.20.0"]


# ---------------------------------------------------------------------------
# Harness for the handler tests
# ---------------------------------------------------------------------------


class _ClientManager:
    def __init__(self, statuses: list[ServerStatus] | None = None) -> None:
        self._statuses = statuses or []

    def get_all_tools(self) -> list[Any]:
        return []

    def get_server_status(self, name: str) -> None:
        return None

    def get_all_server_statuses(self) -> list[ServerStatus]:
        return list(self._statuses)

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    def get_connected_configs(self) -> dict[str, ResolvedServerConfig]:
        return dict(self.connected)

    connected: dict[str, ResolvedServerConfig] = {}


def _server(
    name: str,
    args: list[str],
    *,
    relaxer_value: str | None = "http://self-hosted.internal:3002",
    **extra: Any,
) -> ServerConfig:
    return ServerConfig(
        name=name,
        description=name,
        keywords=[name],
        install={p: ["npx", *args] for p in PLATFORMS},
        command="npx",
        args=list(args),
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
        extra_env={"SELFHOST_API_URL": relaxer_value} if relaxer_value else {},
        **extra,
    )


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    servers: dict[str, ServerConfig],
    *,
    configured: list[ResolvedServerConfig] | None = None,
    online: list[str] | None = None,
) -> GatewayTools:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers=dict(servers),
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: configured or [])
    policy_path = tmp_path / "gateway-policy.yaml"
    policy_path.write_text("servers: {}\n")
    statuses = [
        ServerStatus(name=n, status=ServerStatusEnum.ONLINE, tool_count=0)
        for n in (online or [])
    ]
    manager = _ClientManager(statuses)
    # What the gateway connected each online server with: the configured entry
    # when there is one, else the manifest's -- as startup resolution picks.
    by_name = {c.name: c for c in configured or []}
    manager.connected = {
        n: by_name.get(n) or manifest_server_to_config(servers[n])
        for n in (online or [])
    }
    gateway = GatewayTools(
        client_manager=cast(Any, manager),
        policy_manager=PolicyManager(policy_path=policy_path),
    )
    cast(Any, gateway)._platform = "linux"
    return gateway


def _latest(monkeypatch: pytest.MonkeyPatch, version: str | None) -> list[list[str]]:
    """Stub the registry lookup; record the args it was asked about."""
    asked: list[list[str]] = []

    async def fake(
        command: str, args: list[str], env: Any, cwd: Any, timeout: float = 10.0
    ) -> tuple[str | None, str]:
        asked.append(list(args))
        return (version, "npm")

    monkeypatch.setattr(handlers_module, "get_package_version", fake)
    return asked


def _no_probe(monkeypatch: pytest.MonkeyPatch, gateway: GatewayTools) -> list[Any]:
    probes: list[Any] = []

    async def probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        probes.append(list(command))
        return (False, "probe output")

    monkeypatch.setattr(gateway, "_run_update_probe_command", probe)
    return probes


# ---------------------------------------------------------------------------
# Proposal 1 -- update_server reports "pinned at X, newer available: Y"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_server_reports_a_newer_version_for_a_pinned_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    asked = _latest(monkeypatch, "3.26.0")
    probes = _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert result.ok is False
    assert probes == []  # a pinned server is never probed or moved
    assert asked == [["-y", "fc-mcp@3.25.5"]]
    assert (result.pinned_version, result.latest_available) == ("3.25.5", "3.26.0")
    assert result.latest_comparison == "newer"
    assert "is pinned to '3.25.5' in the manifest entry" in result.message
    assert "newer available: 3.26.0" in result.message


@pytest.mark.asyncio
async def test_update_server_says_up_to_date_when_the_pin_is_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    _latest(monkeypatch, "3.25.5")

    result = await gateway.update_server({"server_name": "fc"})

    assert result.latest_comparison == "not_newer"
    assert "up to date" in result.message
    assert "newer available" not in result.message


@pytest.mark.asyncio
async def test_update_server_says_so_when_latest_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    _latest(monkeypatch, None)

    result = await gateway.update_server({"server_name": "fc"})

    assert (result.pinned_version, result.latest_available) == ("3.25.5", None)
    assert result.latest_comparison is None
    assert "latest version could not be determined" in result.message


# ---------------------------------------------------------------------------
# Proposal 2 -- unpinned client against a self-hosted backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_warns_when_a_self_hosted_backend_client_is_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])}, online=["fc"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "SELFHOST_API_URL" in info.warnings[0]
    assert "unpinned" in info.warnings[0]
    assert "server_version" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_treats_latest_as_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@latest"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1


@pytest.mark.asyncio
async def test_health_is_silent_when_the_relaxer_is_not_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Vendor-hosted (no self-hosted URL): unpinned is the intended default."""
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"], relaxer_value=None)},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_health_is_silent_when_the_client_is_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"], version="3.25.5")},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_health_judges_the_configured_entry_not_the_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """The ViperJuice/dotfiles#325 shape: ``.pmcp.json`` pins with explicit args."""
    configured = ResolvedServerConfig(
        name="fc",
        source="user",
        config=LocalMcpServerConfig(
            command="npx",
            args=["-y", "fc-mcp@3.25.5"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"])},
        configured=[configured],
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.parametrize(
    "spec", ["fc-mcp@^3.25.5", "fc-mcp@~3.25.5", "fc-mcp@3.x", "fc-mcp@next"]
)
@pytest.mark.asyncio
async def test_health_warns_on_a_range_or_dist_tag(
    spec: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """A range or tag re-resolves at every spawn: it is not a pin."""
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", spec])}, online=["fc"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "floats on" in info.warnings[0]
    # NB-2 (rev 6): the npm reason claims only what holds on every npm release.
    assert "not one exact version on every npm release" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_warns_when_the_relaxer_comes_from_the_gateway_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """The child inherits the gateway's env, so an exported URL is self-hosting.

    Advisory only: the credential gate still ignores the ambient value.
    """
    server = _server("fc", ["-y", "fc-mcp"], relaxer_value=None)
    monkeypatch.setenv("SELFHOST_API_URL", "http://self-hosted.internal:3002")
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "SELFHOST_API_URL" in info.warnings[0]
    # The gate is unchanged: it never reads os.environ (#124).
    assert credential_requirement(server).required is True


@pytest.mark.asyncio
async def test_health_loads_the_manifest_once_until_a_source_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])}, online=["fc"]
    )
    loaded = handlers_module.load_manifest
    loads: list[int] = []

    def counting() -> Manifest:
        loads.append(1)
        return loaded()

    monkeypatch.setattr(handlers_module, "load_manifest", counting)

    for _ in range(3):
        health = await gateway.health()
    assert len(loads) == 1
    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1  # the cached answer is still an answer

    _user_overlay("server_env: {}\n")  # a manifest source changed on disk
    await gateway.health()
    assert len(loads) == 2


def test_fingerprint_changes_when_a_project_overlay_appears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    (project / ".pmcp").mkdir(parents=True)
    monkeypatch.chdir(project)
    before = manifest_sources_fingerprint()

    (project / ".pmcp" / "manifest.yaml").write_text(
        'server_version:\n  firecrawl: "3.25.5"\n'
    )

    assert manifest_sources_fingerprint() != before


def test_fingerprint_changes_when_the_project_overlay_is_approved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """Approval changes what loads without touching the overlay file."""
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    monkeypatch.chdir(tmp_path / "proj")
    before = manifest_sources_fingerprint()
    assert load_manifest().servers["firecrawl"].version is None

    approve_project_file(overlay)

    assert manifest_sources_fingerprint() != before
    assert load_manifest().servers["firecrawl"].version == "3.25.5"


@pytest.fixture
def npm_identity_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """What `npm_config_cache` in the gateway env produces: every npm argv refused."""
    from pmcp.manifest import version_checker

    def refused(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(version_checker, "_npm_package_arg", refused)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", refused)
    monkeypatch.setattr(
        handlers_module,
        "get_resolver",
        lambda: _ResolverStatus("DISABLED, refusing every query (test)"),
    )


class _ResolverStatus:
    def __init__(self, summary: str) -> None:
        self._summary = summary

    def status_summary(self) -> str:
        return self._summary


@pytest.fixture
def npm_identity_refusing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identity ON, but npm's parser refuses this argv (a file/alias spec)."""
    from pmcp.manifest import version_checker

    def refused(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(version_checker, "_npm_package_arg", refused)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", refused)
    monkeypatch.setattr(
        handlers_module, "get_resolver", lambda: _ResolverStatus("active (npm 11.19.0)")
    )


def test_a_tarball_shaped_version_is_never_an_exact_pin() -> None:
    """The pin grammar and the warning share the exact-version check.

    npm reads `pkg@1.0.0-x.tgz` as a local tarball, so such a version is not an
    exact registry pin: not as a pin value, not as a pinned argv's version.
    """
    from pmcp.manifest.loader import _parse_version_pin
    from pmcp.tools.handlers import _is_exact_pin

    for version in ("1.0.0-x.tgz", "3.25.5-corp.TGZ", "1.0.0-a.tar", "1.0.0-b.tar.gz"):
        assert _parse_version_pin("fc", version, "server_version") is None
        assert _is_exact_pin("npm", version) is False
    assert _parse_version_pin("fc", "1.0.0-x.tgzx", "server_version") == "1.0.0-x.tgzx"
    assert _is_exact_pin("npm", "1.0.0-x.tgzx") is True  # not a tarball suffix


@pytest.mark.asyncio
async def test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_refusing: None
) -> None:
    """Round 3 (B1'): `fc-mcp@3.25.5-evil.tgz` runs a local file; never silent.

    Rev 11: the warning reads the argv with ONE parser per launcher, whatever
    npm identity says, so the cause is the slot, in both identity modes."""
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5-evil.tgz"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert "is not a plain registry spec" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_fails_loud_for_an_npm_exec_launch_without_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    """Round 3 (N-b): `npm exec` must not go silent without identity. Rev 11
    reads it with the npm parser: `npm exec -y fc-mcp` is unpinned."""
    server = _server("fc", ["exec", "-y", "fc-mcp"])
    server.command = "npm"
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "npm:fc-mcp is unpinned" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_still_warns_when_npm_identity_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])}, online=["fc"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "npm:fc-mcp is unpinned" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_reads_an_exact_pin_structurally_when_identity_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_health_says_it_cannot_verify_an_unreadable_slot_without_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "-p", "other", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]


# ---------------------------------------------------------------------------
# Board round 4 (rev 6): what else can change what npm runs, per-launcher
# exactness, the launcher cause, the floating label
# ---------------------------------------------------------------------------


@pytest.fixture(params=["identity-on", "identity-off"])
def either_identity_mode(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run a test with npm identity naming the package, and with it refused."""
    if request.param == "identity-on":
        request.getfixturevalue("npm_tables")
    else:
        request.getfixturevalue("npm_identity_disabled")
    return cast(str, request.param)


_ENTRY_REDIRECTS = {
    # npm's `package` config: npx runs THAT package's bin, whatever the slot
    # says (reproduced in round 4: `firecrawl-mcp@3.25.5` ran a local 0.0.1).
    "entry-package": ("npm_config_package", "file:/srv/local-client"),
    # npm reads the prefix case-insensitively.
    "entry-registry-uppercase": ("NPM_CONFIG_REGISTRY", "http://mirror.test/"),
    "entry-call": ("npm_config_call", "other-client"),
    "entry-tag": ("npm_config_tag", "next"),
    "entry-userconfig": ("npm_config_userconfig", "/srv/other.npmrc"),
    # Round 5 N1: an existing `_npx/<hash>` tree runs without re-verification,
    # so a cache the ENTRY points elsewhere can change the bytes.
    "entry-cache": ("npm_config_cache", "/srv/shared-cache"),
    "entry-node-options": ("NODE_OPTIONS", "--require /srv/hook.js"),
    "entry-home": ("HOME", "/srv/other-home"),
    # Round 5 N3: the environment spellings of npm's proxy and CA keys.
    "entry-https-proxy": ("HTTPS_PROXY", "http://proxy.test:3128"),
    "entry-https-proxy-lower": ("https_proxy", "http://proxy.test:3128"),
    "entry-extra-ca": ("NODE_EXTRA_CA_CERTS", "/srv/ca.pem"),
}


@pytest.mark.parametrize(
    ("key", "value"), list(_ENTRY_REDIRECTS.values()), ids=list(_ENTRY_REDIRECTS)
)
@pytest.mark.asyncio
async def test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm(
    key: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    either_identity_mode: str,
) -> None:
    """C1, within the trust boundary: the entry's own env block (an overlay's
    ``server_env`` lands here) is the entry's to answer for."""
    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.extra_env[key] = value
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert f"the entry's env sets {key}" in info.warnings[0]


_HOST_SETTINGS = {
    "host-package": ("npm_config_package", "file:/srv/local-client"),
    "host-registry": ("NPM_CONFIG_REGISTRY", "http://mirror.test/"),
    "host-node-options": ("NODE_OPTIONS", "--require /srv/hook.js"),
    "host-https-proxy": ("HTTPS_PROXY", "http://proxy.test:3128"),
    "host-uv-override": ("UV_OVERRIDE", "/srv/overrides.txt"),
}


@pytest.mark.parametrize(
    ("key", "value"), list(_HOST_SETTINGS.values()), ids=list(_HOST_SETTINGS)
)
@pytest.mark.asyncio
async def test_the_host_environment_is_trusted(
    key: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    either_identity_mode: str,
) -> None:
    """Round 5 (maintainer decision): the operator's own shell environment is
    trusted, like PATH. A setting the host exports is out of scope; only the
    entry's env block, argv and launcher are judged."""
    monkeypatch.setenv(key, value)
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.parametrize(
    "where",
    ["user-npmrc", "cwd-npmrc", "local-prefix", "global-npmrc", "builtin-npmrc"],
)
@pytest.mark.asyncio
async def test_the_hosts_npm_configuration_files_are_trusted(
    where: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    either_identity_mode: str,
) -> None:
    """Round 5 (maintainer decision): npmrc files at every level, a local
    project, shims and global installs are the host's; revision 6's discovery
    of them is cut (its shim blind spot, round 5 B1, goes with it)."""
    if where == "user-npmrc":
        (Path.home() / ".npmrc").write_text("package=file:/srv/local-client\n")
    elif where == "cwd-npmrc":
        (Path.cwd() / ".npmrc").write_text("registry = http://mirror.test/\n")
    elif where == "local-prefix":
        (Path.cwd() / "package.json").write_text("{}\n")
    elif where == "global-npmrc":
        (tmp_path / "prefix" / "etc").mkdir(parents=True)
        (tmp_path / "prefix" / "etc" / "npmrc").write_text("tag=next\n")
        monkeypatch.setenv("PREFIX", str(tmp_path / "prefix"))
    else:
        root = tmp_path / "npm-root"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "npx-cli.js").write_text("")
        (root / "bin" / "npx-cli.js").chmod(0o755)
        (root / "npmrc").write_text("call=other-client\n")
        (tmp_path / "path-bin").mkdir()
        (tmp_path / "path-bin" / "npx").symlink_to(root / "bin" / "npx-cli.js")
        monkeypatch.setenv("PATH", f"{tmp_path / 'path-bin'}:{os.environ['PATH']}")
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, either_identity_mode: str
) -> None:
    """Control: logging, timing, a registry credential, another scope's
    registry and the server's own declared keys change nothing for
    `fc-mcp`."""
    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.extra_env.update(
        {
            "npm_config_loglevel": "warn",
            # npm reads the prefix case-insensitively: this is `update-notifier`.
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "npm_config_fetch_timeout": "60000",
            "npm_config_//registry.npmjs.org/:_authToken": "secret",
            "npm_config_@corp:registry": "http://mirror.test/",
            "SELFHOST_API_KEY": "",
            "LANG": "C.UTF-8",
        }
    )
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


def test_the_entry_env_allowlist() -> None:
    """Round 6: the env rule is an ALLOWLIST. A key is inert only if the entry
    declares it as the server's own (outside every tool namespace), it is a
    locale/terminal key, or it is on its launcher's short logging/timing/
    credential list. Everything else -- unknown keys included -- is not."""
    from pmcp.tools.handlers import (
        _declared_env_keys,
        _entry_env_key_is_inert,
        _npm_env_config_key,
    )

    declared = _declared_env_keys("fc")
    assert declared == {"SELFHOST_API_KEY", "SELFHOST_API_URL"}
    # Rev 9: declarations come from the SHIPPED manifest only, by name.
    assert _declared_env_keys("firecrawl") == {"FIRECRAWL_API_KEY", "FIRECRAWL_API_URL"}
    assert _declared_env_keys("overlay-only") == frozenset()

    not_inert = {
        "npm": [
            "npm_config_package",
            "NPM_CONFIG_REGISTRY",
            "npm_config_cache",
            "npm_config_store_dir",
            "npm_config_future_key",
            "NODE_OPTIONS",
            "NODE_PATH",
            "PREFIX",
            "nvm_dir",
            "PATH",
            "HOME",
            "https_proxy",
            "NODE_EXTRA_CA_CERTS",
            "LD_PRELOAD",
            "XDG_CONFIG_HOME",
            "SOME_UNKNOWN_KEY",
            "npm_config_@corp:registry",
        ],
        "pypi": [
            "UV_OVERRIDE",
            "UV_INDEX_URL",
            "UV_CONSTRAINT",
            "UV_CACHE_DIR",
            "UV_PYTHON",
            "UV_FUTURE_KEY",
            "PIP_INDEX_URL",
            "PYTHONPATH",
            "XDG_CONFIG_DIRS",
            "XDG_CACHE_HOME",
            "XDG_DATA_HOME",
            "SSL_CERT_FILE",
            "SOME_UNKNOWN_KEY",
        ],
        "cargo": [
            "CARGO_HOME",
            "CARGO_REGISTRIES_CORP_INDEX",
            "RUSTC_WRAPPER",
            "RUSTFLAGS",
            "RUSTUP_TOOLCHAIN",
            "CC",
            "CFLAGS",
            "LD_PRELOAD",
        ],
        "docker": ["DOCKER_HOST", "DOCKER_CONFIG", "NODE_OPTIONS", "SOME_UNKNOWN_KEY"],
    }
    inert = {
        "npm": [
            "npm_config_loglevel",
            "npm_config_yes",
            "npm_config_fetch_timeout",
            "npm_config_//registry.npmjs.org/:_authToken",
            "SELFHOST_API_URL",
            "SELFHOST_API_KEY",
            "LANG",
            "LC_ALL",
            "NO_COLOR",
        ],
        "pypi": [
            "UV_NO_PROGRESS",
            "UV_HTTP_TIMEOUT",
            "UV_INDEX_CORP_PASSWORD",
            "SELFHOST_API_URL",
            "TERM",
        ],
        "cargo": [
            "CARGO_TERM_COLOR",
            "CARGO_NET_RETRY",
            "CARGO_REGISTRY_TOKEN",
            "CARGO_REGISTRIES_CORP_TOKEN",
            "SELFHOST_API_KEY",
        ],
        "docker": ["SELFHOST_API_URL", "TZ"],
    }
    for family, keys in not_inert.items():
        for key in keys:
            assert not _entry_env_key_is_inert(
                family, key, "v", "@corp/fc", declared
            ), key
    for family, keys in inert.items():
        for key in keys:
            assert _entry_env_key_is_inert(family, key, "v", "fc-mcp", declared), key
    # Another scope's registry does not touch `fc`; an empty value is not special.
    assert _entry_env_key_is_inert(
        "npm", "npm_config_@corp:registry", "x", "fc", declared
    )
    assert not _entry_env_key_is_inert("npm", "PATH", "", "fc", declared)
    # npm's own env-name rule: case-insensitive prefix, `_` -> `-` except a
    # leading one, lowercased; a `//` key is left alone.
    assert _npm_env_config_key("NPM_CONFIG_SCRIPT_SHELL") == "script-shell"
    assert _npm_env_config_key("npm_config__auth") == "_auth"
    assert _npm_env_config_key("npm_config_//h/:_authToken") == "//h/:_authToken"


def _uvx_server(args: list[str], **env: str) -> ServerConfig:
    return ServerConfig(
        name="uv",
        description="uv",
        keywords=["uv"],
        install={},
        command="uvx",
        args=args,
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
        extra_env={"SELFHOST_API_URL": "http://self-hosted.internal:3002", **env},
    )


def _cargo_server(args: list[str], **env: str) -> ServerConfig:
    server = _uvx_server(args, **env)
    server.name, server.command = "cg", "cargo"
    return server


async def _one_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: ServerConfig
) -> list[str]:
    gateway = _gateway(
        monkeypatch, tmp_path, {server.name: server}, online=[server.name]
    )
    health = await gateway.health()
    (info,) = [s for s in health.servers if s.name == server.name]
    return info.warnings


@pytest.mark.parametrize(
    ("args", "env", "named"),
    [
        (["fc-mcp==1.2.3"], {"UV_OVERRIDE": "/srv/ovr.txt"}, "env sets UV_OVERRIDE"),
        (
            ["fc-mcp==1.2.3"],
            {"UV_INDEX_URL": "http://mirror.test/simple"},
            "env sets UV_INDEX_URL",
        ),
        (
            ["fc-mcp==1.2.3"],
            {"XDG_CONFIG_HOME": "/srv/cfg"},
            "env sets XDG_CONFIG_HOME",
        ),
        (
            ["--index-url", "http://mirror.test/simple", "fc-mcp==1.2.3"],
            {},
            "argv passes --index-url",
        ),
        (["--with", "other", "fc-mcp==1.2.3"], {}, "argv passes --with"),
        (
            ["--overrides=/srv/ovr.txt", "--from", "fc-mcp==1.2.3", "fc"],
            {},
            "argv passes --overrides",
        ),
    ],
    ids=[
        "env-override",
        "env-index",
        "env-xdg",
        "argv-index",
        "argv-with",
        "argv-overrides",
    ],
)
@pytest.mark.asyncio
async def test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv(
    args: list[str],
    env: dict[str, str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 5 B2, within the trust boundary: uv's out-of-argv redirects
    (reproduced: UV_OVERRIDE made `uvx --from cowsay==6.1` run 6.0)."""
    warnings = await _one_warning(monkeypatch, tmp_path, _uvx_server(args, **env))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.asyncio
async def test_an_exact_uvx_pin_is_silent_without_an_entry_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Control: output flags, timing and a credential (rev 8: `--python` is
    no longer inert, as `UV_PYTHON` is not; round 6 N3)."""
    monkeypatch.setenv("UV_OVERRIDE", "/srv/ovr.txt")  # the host's: trusted
    server = _uvx_server(
        ["-q", "--from", "fc-mcp==1.2.3", "fc-mcp", "--port", "1"],
        UV_HTTP_TIMEOUT="60",
        UV_INDEX_CORP_PASSWORD="secret",
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.parametrize(
    ("args", "env", "named"),
    [
        (
            ["install", "fc", "--version", "1.2.3"],
            {"CARGO_REGISTRIES_CORP_INDEX": "https://m.test"},
            "env sets CARGO_REGISTRIES_CORP_INDEX",
        ),
        (
            ["install", "fc", "--version", "1.2.3"],
            {"RUSTC_WRAPPER": "/srv/w"},
            "env sets RUSTC_WRAPPER",
        ),
        (
            ["install", "--git", "https://example.test/fc", "--version", "1.2.3", "fc"],
            {},
            "argv passes --git",
        ),
        (
            ["install", "fc", "--version", "1.2.3", "--registry", "corp"],
            {},
            "argv passes --registry",
        ),
    ],
    ids=["env-registry-index", "env-rustc-wrapper", "argv-git", "argv-registry"],
)
@pytest.mark.asyncio
async def test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo(
    args: list[str],
    env: dict[str, str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 5 B2 for cargo (the seat's `--git ... --version 1.2.3` was silent)."""
    warnings = await _one_warning(monkeypatch, tmp_path, _cargo_server(args, **env))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.asyncio
async def test_an_exact_cargo_pin_is_silent_without_an_entry_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _cargo_server(
        ["install", "--locked", "fc", "--version", "1.2.3"],
        CARGO_TERM_COLOR="never",
        CARGO_REGISTRY_TOKEN="secret",
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.asyncio
async def test_a_uvx_url_requirement_is_never_an_exact_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 5 N5: a PEP 508 URL requirement names a file, whatever `==` its
    fragment carries."""
    from pmcp.tools.handlers import _read_argv

    url = "fc-mcp @ https://example.test/fc_mcp-9.9.9-py3-none-any.whl#x==1.0.0"
    assert _read_argv(("uvx", "--from", url, "fc-mcp")).exact is False
    assert _read_argv(("uvx", "--from", "fc-mcp==1.0.0", "fc-mcp")).exact is True
    assert _read_argv(("uvx", "fc-mcp==1.0.0")).exact is True
    assert _read_argv(("uvx", "fc-mcp>=1.0,==1.0.0")).exact is False

    warnings = await _one_warning(
        monkeypatch, tmp_path, _uvx_server(["--from", url, "fc-mcp"])
    )

    assert len(warnings) == 1
    assert "a URL requirement names a file" in warnings[0]
    assert "example.test" not in warnings[0]


@pytest.mark.parametrize(
    ("command", "args", "tool"),
    [
        ("npx", ["-y", "fc-mcp@3.25.5"], "npm"),
        ("uvx", ["--from", "fc-mcp==1.2.3", "fc-mcp"], "uv"),
        ("cargo", ["install", "fc", "--version", "1.2.3"], "cargo"),
    ],
    ids=["npx", "uvx", "cargo"],
)
@pytest.mark.asyncio
async def test_an_entry_set_cwd_makes_an_exact_pin_unverifiable(
    command: str,
    args: list[str],
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Rev 7 (maintainer ruling): a cwd the ENTRY sets picks which directory's
    project configuration the launcher reads (.npmrc / package.json up the
    tree, uv.toml / [tool.uv], .cargo/config.toml), so it is the entry's to
    answer for. Nothing needs to be in the directory: pmcp does not read it."""
    project = tmp_path / "entry-project"
    project.mkdir()
    configured = ResolvedServerConfig(
        name="fc",
        source="user",
        config=LocalMcpServerConfig(
            command=command,
            args=args,
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(project),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"])},
        configured=[configured],
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    # Rev 22: the cwd path is not shown (it is entry text, not a closed name).
    assert "the entry sets a cwd (not shown)" in info.warnings[0]
    assert str(project) not in info.warnings[0]
    assert f"project configuration {tool} reads" in info.warnings[0]


@pytest.mark.asyncio
async def test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """docker run reads no cwd-relative configuration; and with no entry-set
    cwd the child inherits pmcp's own (here a node project), which is host."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # rev 14: no relative PATH entry
    (Path.cwd() / "package.json").write_text("{}\n")
    digest = "sha256:" + "e" * 64
    docker = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command="docker",
            args=["run", "-e", "SELFHOST_API_URL", f"example/client@{digest}"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(tmp_path),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "dk": _docker_server("example/client:latest"),
            "fc": _server("fc", ["-y", "fc-mcp@3.25.5"]),
        },
        configured=[docker],
        online=["dk", "fc"],
    )

    health = await gateway.health()

    assert all(s.warnings == [] for s in health.servers if s.name in ("dk", "fc"))


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (
            "docker",
            [
                "run",
                "-e",
                "SELFHOST_API_URL",
                "--some-future-flag",
                "example/client:3.25.5",
            ],
        ),
        ("uvx", ["--some-future-flag", "fc-mcp==1.2.3"]),
    ],
    ids=["docker-unknown-flag", "uvx-unknown-flag"],
)
@pytest.mark.asyncio
async def test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher(
    command: str, args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 5 N5: docker and uvx now match the npm family (round 3, N-b).
    (Rev 11: a non-bare spelling such as `uvx.exe` is judged by its basename,
    like `/usr/bin/npx`; see `test_a_launcher_is_judged_by_its_basename`.)"""
    server = _uvx_server(args)
    server.command = command

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert "its argv passes --some-future-flag" in warnings[0]


def test_exactness_is_decided_per_launcher() -> None:
    """C2: a docker tag is mutable whatever it looks like; each launcher has
    its own rule, and an unknown launcher is never exact."""
    from pmcp.tools.handlers import _is_exact_pin

    digest = "sha256:" + "a" * 64
    assert _is_exact_pin("docker", "3.25.5") is False
    assert _is_exact_pin("docker", "latest") is False
    assert _is_exact_pin("docker", digest) is True
    assert _is_exact_pin("docker", "sha256:beefbeef") is False
    assert _is_exact_pin("npm", "3.25.5") is True
    assert _is_exact_pin("npm", "3.25.5+build") is True
    assert _is_exact_pin("pypi", "1.2.3") is True
    assert _is_exact_pin("pypi", "1.*") is False
    assert _is_exact_pin("pypi", "=1.2.3") is False  # from `===1.2.3`
    assert _is_exact_pin("cargo", "1.2.3") is True
    assert _is_exact_pin("cargo", "^1.2") is False
    assert _is_exact_pin("unknown", "3.25.5") is False


def _docker_server(image: str) -> ServerConfig:
    return ServerConfig(
        name="dk",
        description="dk",
        keywords=["dk"],
        install={},
        command="docker",
        args=["run", "-e", "SELFHOST_API_URL", "--pull=always", image],
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
        extra_env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
    )


@pytest.mark.asyncio
async def test_health_warns_on_a_docker_tag_however_version_like(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2: `example/client:3.25.5` can be re-pointed; only a digest pins."""
    digest = "sha256:" + "b" * 64
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "dk": _docker_server("example/client:3.25.5"),
            "dg": _docker_server(f"example/client@{digest}"),
        },
        online=["dk", "dg"],
    )

    health = await gateway.health()

    (tagged,) = [s for s in health.servers if s.name == "dk"]
    (digested,) = [s for s in health.servers if s.name == "dg"]
    assert len(tagged.warnings) == 1
    assert "floats on '3.25.5' (a docker tag" in tagged.warnings[0]
    assert "image@sha256" in tagged.warnings[0]
    assert digested.warnings == []


@pytest.mark.asyncio
async def test_docker_digest_labels_and_the_entrys_docker_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 5 N5: an upper-case digest floats and is not called a tag. The
    entry's DOCKER_* env (which daemon, which config) is the entry's to answer
    for, even with a digest."""
    upper = _docker_server("example/client@sha256:" + "B" * 64)
    upper.name = "du"
    routed = _docker_server("example/client@sha256:" + "b" * 64)
    routed.name = "dh"
    routed.extra_env["DOCKER_HOST"] = "tcp://other-daemon.test:2375"
    gateway = _gateway(
        monkeypatch, tmp_path, {"du": upper, "dh": routed}, online=["du", "dh"]
    )

    health = await gateway.health()

    (du,) = [s for s in health.servers if s.name == "du"]
    (dh,) = [s for s in health.servers if s.name == "dh"]
    assert len(du.warnings) == 1
    assert "a malformed content digest" in du.warnings[0]
    assert "a docker tag" not in du.warnings[0]
    assert len(dh.warnings) == 1
    assert "the entry's env sets DOCKER_HOST" in dh.warnings[0]


@pytest.mark.asyncio
async def test_update_server_reports_a_docker_tag_as_floating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2 drives the report too: a tag is [FLOATING], a digest [PINNED]."""
    from pmcp.cli import _format_update_result

    digest = "sha256:" + "c" * 64
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "dk": _docker_server("example/client:3.25.5"),
            "dg": _docker_server(f"example/client@{digest}"),
        },
    )
    _latest(monkeypatch, "sha256:" + "d" * 64)
    probes = _no_probe(monkeypatch, gateway)

    tagged = await gateway.update_server({"server_name": "dk"})
    digested = await gateway.update_server({"server_name": "dg"})

    assert probes == []  # neither is moved: the operator chose both
    assert (tagged.pinned_version, tagged.floating_selector) == (None, "3.25.5")
    assert "a docker tag" in tagged.message
    assert (digested.pinned_version, digested.floating_selector) == (digest, None)
    assert _format_update_result(tagged.model_dump())[0].startswith("[FLOATING] dk")
    assert _format_update_result(digested.model_dump())[0].startswith("[PINNED] dg")


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("npx.cmd", ["-y", "fc-mcp@3.25.5"]),
        ("/usr/bin/npx", ["-y", "fc-mcp@3.25.5"]),
        ("/tmp/anything/npx", ["-y", "fc-mcp@3.25.5"]),
        ("./npx", ["-y", "fc-mcp@3.25.5"]),
        ("node_modules/.bin/npx", ["-y", "fc-mcp@3.25.5"]),
        ("NPX", ["-y", "fc-mcp@3.25.5"]),
        ("C:npx", ["-y", "fc-mcp@3.25.5"]),
        ("C:\\tools\\npx.cmd", ["-y", "fc-mcp@3.25.5"]),
        ("npm.cmd", ["exec", "-y", "fc-mcp@3.25.5"]),
        ("uvx.exe", ["fc-mcp==1.2.3"]),
    ],
    ids=[
        "npx.cmd",
        "abs-npx-not-on-path",
        "tmp-npx",
        "dot-npx",
        "node-modules-bin-npx",
        "upper-NPX",
        "drive-npx",
        "windows-npx",
        "npm.cmd",
        "uvx.exe",
    ],
)
@pytest.mark.asyncio
async def test_a_launcher_must_be_itself_not_named_like_it(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 11: a basename is not evidence. On this (POSIX) host only the
    bare name, or the path the child's PATH resolves that name to, is the
    modelled launcher; anything else is "cannot verify", named. (Rev 11's
    basename rule, NB-4, is superseded.)"""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path-dir"))
    server = _launch("sh1", command, args)

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert "as the child's PATH resolves it" in warnings[0]


@pytest.mark.asyncio
async def test_a_launcher_path_the_childs_path_resolves_is_the_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Control: an absolute path equal to what the child's PATH resolves
    `npx` to is npx; a same-named binary elsewhere is not."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "npx").write_text("#!/bin/sh\n")
    (bindir / "npx").chmod(0o755)
    other = tmp_path / "other"
    other.mkdir()
    (other / "npx").write_text("#!/bin/sh\n")
    (other / "npx").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    good = _launch("ok1", str(bindir / "npx"), ["-y", "fc-mcp@3.25.5"])
    bad = _launch("sh1", str(other / "npx"), ["-y", "fc-mcp@3.25.5"])
    gateway = _gateway(
        monkeypatch, tmp_path, {"ok1": good, "sh1": bad}, online=["ok1", "sh1"]
    )

    health = await gateway.health()

    by_name = {s.name: s.warnings for s in health.servers}
    assert by_name["ok1"] == []
    assert len(by_name["sh1"]) == 1 and "PATH resolves it" in by_name["sh1"][0]


@pytest.mark.asyncio
async def test_the_floating_label_claims_only_what_every_npm_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """NB-2: npa 13 reads `1.0.0-x.tar-gz` as a version and npa 12 as a file,
    so it is neither "a range" nor "a tag". Rev 11 reads it with the pin
    grammar, which refuses it as a possible tarball: cannot verify."""
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@1.0.0-x.tar-gz"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert "is not a plain registry spec" in info.warnings[0]
    assert "range or tag" not in info.warnings[0]


@pytest.mark.asyncio
async def test_update_server_labels_build_metadata_with_what_npm_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """A configured `pkg@3.25.5+evil` runs 3.25.5; the label must say that."""
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5+evil"])}
    )
    _latest(monkeypatch, "3.26.0")

    result = await gateway.update_server({"server_name": "fc"})

    assert (result.pinned_version, result.latest_comparison) == ("3.25.5", "newer")
    assert "build metadata '+evil' is ignored by npm" in result.message
    assert "3.25.5+evil" not in (result.pinned_version or "")


@pytest.mark.asyncio
async def test_update_server_reports_a_range_as_floating_not_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@^3.25.0"])}
    )
    _latest(monkeypatch, "3.26.0")
    probes = _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert result.ok is False
    assert probes == []  # still not moved: the operator chose the selector
    assert (result.pinned_version, result.floating_selector) == (None, "^3.25.0")
    assert result.latest_comparison is None
    assert "does not hold the client at one version" in result.message


@pytest.mark.asyncio
async def test_update_server_carries_the_unpinned_self_hosted_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])})
    probes = _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert probes == [["npx", "-y", "fc-mcp@latest", "--help"]]  # not blocked
    assert len(result.warnings) == 1
    assert "SELFHOST_API_URL" in result.warnings[0]


# ---------------------------------------------------------------------------
# pmcp update rendering
# ---------------------------------------------------------------------------


def test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed() -> None:
    from pmcp.cli import _format_update_result

    lines = _format_update_result(
        {
            "ok": False,
            "server": "firecrawl",
            "pinned_version": "3.25.5",
            "latest_available": "3.26.0",
            "latest_comparison": "newer",
            "message": "long message",
            "warnings": [],
        }
    )

    assert lines == ["[PINNED] firecrawl: pinned at 3.25.5, newer available: 3.26.0"]


def test_pmcp_update_renders_a_range_as_floating() -> None:
    from pmcp.cli import _format_update_result

    lines = _format_update_result(
        {
            "ok": False,
            "server": "fc",
            "floating_selector": "^3.25.0",
            "latest_available": "3.26.0",
            "message": "long message",
            "warnings": [],
        }
    )

    assert lines == [
        "[FLOATING] fc: held at ^3.25.0, which is not one exact version and "
        "can resolve to another at a later spawn (latest 3.26.0)"
    ]


def test_pmcp_update_prints_warnings_under_the_result() -> None:
    from pmcp.cli import _format_update_result

    lines = _format_update_result(
        {
            "ok": True,
            "server": "firecrawl",
            "message": "Updated.",
            "warnings": ["unpinned against a self-hosted backend"],
        }
    )

    assert lines == [
        "[OK] firecrawl: Updated.",
        "  warning: unpinned against a self-hosted backend",
    ]


# ---------------------------------------------------------------------------
# Board round 6 (rev 8): allowlists for the entry's env and argv shapes
# ---------------------------------------------------------------------------

_DIGEST = "sha256:" + "f" * 64


def _launch(name: str, command: str, args: list[str], **env: str) -> ServerConfig:
    server = _uvx_server(args, **env)
    server.name, server.command = name, command
    return server


_UNRECOGNISED_SHAPES = {
    # X1: something other than the pinned package runs.
    "docker-command-after-image": (
        "docker",
        [
            "run",
            "-e",
            "SELFHOST_API_URL",
            "-i",
            "--rm",
            f"node@{_DIGEST}",
            "npx",
            "-y",
            "semver",
        ],
        "a container command after the image",
    ),
    # Round 14: a shorthand cluster is read letter by letter, as docker's
    # pflag does; `-P` (publish all ports) is not an inert letter.
    "docker-unknown-letter-in-cluster": (
        "docker",
        ["run", "-e", "SELFHOST_API_URL", "-iP", f"node@{_DIGEST}"],
        "argv passes -P",
    ),
    "docker-entrypoint": (
        "docker",
        ["run", "-e", "SELFHOST_API_URL", "--entrypoint", "/bin/sh", f"node@{_DIGEST}"],
        "argv passes --entrypoint",
    ),
    "docker-volume": (
        "docker",
        ["run", "-e", "SELFHOST_API_URL", "-v", "/srv/evil:/app", f"node@{_DIGEST}"],
        "argv passes -v",
    ),
    "docker-env-file": (
        "docker",
        ["run", "-e", "SELFHOST_API_URL", "--env-file", "/srv/env", f"node@{_DIGEST}"],
        "argv passes --env-file",
    ),
    "docker-env-node-options": (
        "docker",
        [
            "run",
            "-e",
            "SELFHOST_API_URL",
            "-e",
            "NODE_OPTIONS=--require=/x",
            f"node@{_DIGEST}",
        ],
        "container env NODE_OPTIONS",
    ),
    "npx-package-then-sh": (
        "npx",
        ["-y", "-p", "fc-mcp@3.25.5", "sh", "-c", "echo other"],
        "argv passes -p",
    ),
    "uvx-from-then-sh": (
        "uvx",
        ["--from", "fc-mcp==1.2.3", "sh", "-c", "echo other"],
        "runs 'sh' from the --from environment",
    ),
    # N3: the interpreter and the toolchain are not inert.
    "uvx-python": (
        "uvx",
        ["--python", "/srv/python", "fc-mcp==1.2.3"],
        "argv passes --python",
    ),
    "cargo-not-install": (
        "cargo",
        ["run", "-e", "SELFHOST_API_URL", "--release", "fc"],
        "is not `cargo install ...`",
    ),
    "cargo-toolchain": (
        "cargo",
        ["+nightly", "install", "fc", "--version", "1.2.3"],
        "selects toolchain +nightly",
    ),
}


@pytest.mark.parametrize(
    ("command", "args", "named"),
    list(_UNRECOGNISED_SHAPES.values()),
    ids=list(_UNRECOGNISED_SHAPES),
)
@pytest.mark.asyncio
async def test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable(
    command: str,
    args: list[str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 6 X1/N3: the argv rule is an allowlist of SHAPES in which the
    pinned package is what runs; anything else is "cannot verify"."""
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("sh1", command, args))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (
            "docker",
            [
                "run",
                "-i",
                "--rm",
                "--pull=always",
                "-e",
                "SELFHOST_API_URL",
                f"example/client@{_DIGEST}",
            ],
        ),
        (
            "docker",
            [
                "run",
                "-e",
                "SELFHOST_API_URL",
                "-e",
                "SELFHOST_API_KEY=k",
                f"node:22@{_DIGEST}",
            ],
        ),
        ("npx", ["-y", "fc-mcp@3.25.5", "--port", "3000"]),
        ("uvx", ["--from", "fc-mcp==1.2.3", "fc-mcp", "--port", "3000"]),
        ("uvx", ["--from", "fc_mcp==1.2.3", "FC.MCP"]),
        ("cargo", ["install", "--locked", "fc", "--version", "1.2.3"]),
    ],
    ids=[
        "docker-digest",
        "docker-tagged-digest",
        "npx",
        "uvx-from-own-command",
        "uvx-pep503-name",
        "cargo",
    ],
)
@pytest.mark.asyncio
async def test_a_recognised_shape_with_an_exact_pin_is_silent(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Controls: each launcher's modelled shape, with its inert flags, the
    declared server keys passed to the container, and arguments to the
    package after it."""
    assert (
        await _one_warning(monkeypatch, tmp_path, _launch("ok1", command, args)) == []
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("XDG_CONFIG_DIRS", "/srv/xdg"),
        ("XDG_CACHE_HOME", "/srv/cache"),
        ("LD_PRELOAD", "/srv/hook.so"),
        ("SOME_UNKNOWN_KEY", "1"),
        ("PATH", ""),
    ],
    ids=[
        "xdg-config-dirs",
        "xdg-cache-home",
        "ld-preload",
        "unknown-key",
        "empty-path",
    ],
)
@pytest.mark.asyncio
async def test_an_entry_key_not_proven_inert_is_unverifiable(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 X2/N1/N4: the env rule is an allowlist, so a spelling nobody
    listed (XDG_CONFIG_DIRS redirected uv to an impostor wheel), a loader
    key, an unknown key and an empty PATH are all "cannot verify"."""
    server = _uvx_server(["fc-mcp==1.2.3"], **{key: value})

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert f"the entry's env sets {key}" in warnings[0]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("bunx", ["fc-mcp@3.25.5"]),
        ("pnpx", ["fc-mcp@3.25.5"]),
        ("pnpm", ["dlx", "fc-mcp@3.25.5"]),
        ("sh", ["-c", "npx -y fc-mcp@3.25.5"]),
        ("env", ["FOO=1", "npx", "-y", "fc-mcp@3.25.5"]),
        ("node", ["/usr/lib/node_modules/npm/bin/npx-cli.js", "-y", "fc-mcp"]),
        ("uv", ["run", "-e", "SELFHOST_API_URL", "fc-mcp"]),
    ],
    ids=["bunx", "pnpx", "pnpm-dlx", "sh-c", "env", "node", "uv-run"],
)
@pytest.mark.asyncio
async def test_an_unmodelled_runner_or_wrapper_is_unverifiable(
    command: str, args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 N2: runners pmcp does not model, and wrappers, fail loud."""
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("rn1", command, args))

    assert len(warnings) == 1
    assert "a package runner or wrapper pmcp does not model" in warnings[0]


@pytest.mark.asyncio
async def test_uv_tool_run_is_judged_as_uvx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 N2: `uv tool run` is uvx, so it is judged, not skipped."""
    pinned = _launch("ut1", "uv", ["tool", "run", "fc-mcp==1.2.3"])
    unpinned = _launch("ut2", "uv", ["tool", "run", "fc-mcp"])
    gateway = _gateway(
        monkeypatch, tmp_path, {"ut1": pinned, "ut2": unpinned}, online=["ut1", "ut2"]
    )

    health = await gateway.health()

    (one,) = [s for s in health.servers if s.name == "ut1"]
    (two,) = [s for s in health.servers if s.name == "ut2"]
    assert one.warnings == []
    assert len(two.warnings) == 1
    assert "is unpinned" in two.warnings[0]


@pytest.mark.asyncio
async def test_update_server_never_labels_a_container_command_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 X1 drives the report too: a digest-pinned image running
    `npx -y semver` is not a pinned client, so it is not [PINNED]."""
    from pmcp.cli import _format_update_result

    server = _launch(
        "dc1",
        "docker",
        [
            "run",
            "-e",
            "SELFHOST_API_URL",
            "--rm",
            f"node@{_DIGEST}",
            "npx",
            "-y",
            "semver",
        ],
    )
    gateway = _gateway(monkeypatch, tmp_path, {"dc1": server})
    _latest(monkeypatch, "sha256:" + "0" * 64)
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "dc1"})

    assert (result.pinned_version, result.floating_selector) == (None, _DIGEST)
    assert "a container command after the image" in result.message
    assert _format_update_result(result.model_dump())[0].startswith("[FLOATING] dc1")


# ---------------------------------------------------------------------------
# Board round 7 (rev 9): every spawning argv; only shipped declarations exempt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("install", "named"),
    [
        (
            ["npx", "-y", "fc-mcp"],
            "does not run fc-mcp@3.25.5 (it names fc-mcp@latest)",
        ),
        (["npx", "-y", "fc-mcp@3.26.0"], "does not run fc-mcp@3.25.5"),
        (["sh", "-c", "npx -y fc-mcp@3.25.5"], "runs something pmcp cannot read"),
        (["npx", "-y", "-p", "fc-mcp@3.25.5", "sh"], "linux install argv"),
    ],
    ids=["unpinned", "other-version", "sh-wrapper", "package-then-sh"],
)
@pytest.mark.asyncio
async def test_an_install_argv_that_does_not_run_the_pin_is_unverifiable(
    install: list[str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 B1: gateway.provision spawns `install[platform]` and adopts it as
    the live server, so a hand-pinned `args` with a copied unpinned install
    argv ran latest while the warning judged only `args`."""
    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.install = {**server.install, "linux": install}
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert "its linux install argv" in info.warnings[0]
    assert named in info.warnings[0]


@pytest.mark.asyncio
async def test_update_server_never_labels_a_divergent_install_argv_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    from pmcp.cli import _format_update_result

    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.install = {**server.install, "linux": ["npx", "-y", "fc-mcp"]}
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server})
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert (result.pinned_version, result.floating_selector) == (None, "3.25.5")
    assert "its linux install argv" in result.message
    assert _format_update_result(result.model_dump())[0].startswith("[FLOATING] fc")


@pytest.mark.parametrize(
    ("env_var", "relaxers", "env"),
    [
        (
            "OPENSSL_CONF",
            ["SELFHOST_API_URL"],
            {"OPENSSL_CONF": "/srv/evil.cnf", "SELFHOST_API_URL": "http://h"},
        ),
        ("SELFHOST_API_KEY", ["OPENSSL_CONF"], {"OPENSSL_CONF": "/srv/evil.cnf"}),
        (
            "TARGET_CC",
            ["SELFHOST_API_URL"],
            {"TARGET_CC": "/srv/cc", "SELFHOST_API_URL": "http://h"},
        ),
    ],
    ids=["env-var-openssl-conf", "relaxer-openssl-conf", "env-var-target-cc"],
)
@pytest.mark.asyncio
async def test_an_overlay_declaration_exempts_no_key(
    env_var: str,
    relaxers: list[str],
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 B2: an overlay-only entry that names a loader key as its own
    credential or relaxer gets no exemption: only pmcp's shipped manifest
    declares keys (reproduced: OPENSSL_CONF made npx load an arbitrary .so)."""
    server = ServerConfig(
        name="overlay-only",
        description="x",
        keywords=["x"],
        install={p: ["npx", "-y", "fc-mcp@3.25.5"] for p in PLATFORMS},
        command="npx",
        args=["-y", "fc-mcp@3.25.5"],
        requires_api_key=True,
        env_var=env_var,
        api_key_optional_when=relaxers,
        extra_env=env,
    )

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert "the entry's env sets" in warnings[0]


@pytest.mark.asyncio
async def test_the_shipped_firecrawl_declarations_keep_a_self_hosted_pin_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Control, against the REAL shipped declarations: firecrawl's own
    FIRECRAWL_API_KEY and FIRECRAWL_API_URL are exempt (the
    ViperJuice/dotfiles#325 shape)."""
    server = ServerConfig(
        name="firecrawl",
        description="x",
        keywords=["x"],
        install={p: ["npx", "-y", "firecrawl-mcp@3.25.5"] for p in PLATFORMS},
        command="npx",
        args=["-y", "firecrawl-mcp@3.25.5"],
        requires_api_key=True,
        env_var="FIRECRAWL_API_KEY",
        api_key_optional_when=["FIRECRAWL_API_URL"],
        extra_env={"FIRECRAWL_API_URL": "http://ai:3002", "FIRECRAWL_API_KEY": "k"},
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (
            "docker",
            [
                "run",
                "-e",
                "SELFHOST_API_URL",
                "-it",
                "--rm",
                f"example/client@{_DIGEST}",
            ],
        ),
        ("npx", ["--yes=true", "fc-mcp@3.25.5"]),
    ],
    ids=["docker-it", "npx-yes-true"],
)
@pytest.mark.asyncio
async def test_common_inert_spellings_are_recognised(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 N2: `docker run -it` and `npx --yes=true` are modelled."""
    assert (
        await _one_warning(monkeypatch, tmp_path, _launch("ok1", command, args)) == []
    )


# ---------------------------------------------------------------------------
# Board round 8 (rev 10): the whole spawn set before any exemption
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "args", "install"),
    [
        ("fc-mcp", [], ["npx", "-y", "fc-mcp"]),
        ("fc-mcp", [], ["uvx", "fc-mcp"]),
        ("/opt/fc/bin/fc-mcp", ["--stdio"], ["npx", "-y", "fc-mcp"]),
        # Pinned, but still not the entry's own command: the exemption is for a
        # local binary that is the ONLY thing that can run as the server.
        ("fc-mcp", [], ["npx", "-y", "fc-mcp@3.25.5"]),
    ],
    ids=[
        "local-command-npx-install",
        "local-command-uvx-install",
        "absolute-path",
        "pinned-install",
    ],
)
@pytest.mark.asyncio
async def test_a_local_command_does_not_exempt_an_install_argv_provision_adopts(
    command: str,
    args: list[str],
    install: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 8 B-1: an overlay copying the shipped entry with `command:
    firecrawl-mcp` kept `install.linux: npx -y firecrawl-mcp`; gateway.provision
    spawned and adopted that (latest) while the warning's local-binary early
    return never looked at it."""
    server = _server("fc", list(args))
    server.command = command
    server.install = {**{p: [command, *args] for p in PLATFORMS}, "linux": install}
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "its linux install argv" in info.warnings[0]
    assert "is not the entry's own command" in info.warnings[0]


@pytest.mark.asyncio
async def test_a_local_command_is_exempt_only_when_it_is_the_whole_spawn_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Controls: a local binary whose install argvs are that same argv (or
    empty) is the host's; a CONFIGURED local binary is lazy-started from its
    own args, so the manifest's install argv is not in its spawn set."""
    own = _server("ow", ["--stdio"])
    own.command = "/opt/fc/bin/fc-mcp"
    own.install = {p: ["/opt/fc/bin/fc-mcp", "--stdio"] for p in PLATFORMS}
    own.install["windows"] = []
    manifest_entry = _server("cf", ["-y", "fc-mcp"])
    configured = ResolvedServerConfig(
        name="cf",
        source="user",
        config=LocalMcpServerConfig(
            command="/opt/fc/bin/fc-mcp",
            args=[],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"ow": own, "cf": manifest_entry},
        configured=[configured],
        online=["ow", "cf"],
    )

    health = await gateway.health()

    assert all(s.warnings == [] for s in health.servers if s.name in ("ow", "cf"))


# Every spawn primitive in src/pmcp, by `file:qualname`, with HOW MANY spawn
# references it holds, and why it is (or is not) in the warning's spawn set --
# the plan's spawn-site table. A new call site, or a second spawn inside a
# classified function, fails `test_every_spawn_site_is_classified`.
_SPAWN_SITE_TABLE = {
    "client/manager.py:ClientManager._connect_stdio": (1, "server: args (judged)"),
    "manifest/installer.py:JobManager.start_install": (
        1,
        "server: install, adopted (judged)",
    ),
    "tools/handlers.py:GatewayTools._finalize_server_ready": (
        1,
        "adopts start_install's process",
    ),
    "manifest/installer.py:install_server": (
        1,
        "library function, no production caller",
    ),
    "manifest/installer.py:verify_installation": (
        1,
        "library function, no production caller",
    ),
    "manifest/refresher.py:refresh_server": (
        2,
        "descriptions refresh: StdioServerParameters + stdio_client; not adopted, no entry env",
    ),
    "tools/handlers.py:GatewayTools._run_update_probe_command": (
        1,
        "update probe of latest",
    ),
    "manifest/environment.py:check_cli": (1, "CLI probe, not a server"),
    "manifest/environment.py:get_cli_help": (1, "CLI probe, not a server"),
    "manifest/npm_resolver.py:NpmResolver._spawn": (
        1,
        "npm identity helper, not a server",
    ),
    "cli.py:_is_pmcp_system_service_active": (1, "service manager, not a server"),
    "cli.py:_restart_local_pmcp_service": (3, "service manager, not a server"),
    "cli.py:run_upgrade": (1, "pmcp self-upgrade, not a server"),
}

#: Fully qualified process-creating primitives. A reference to any of them --
#: called, aliased, passed to `functools.partial`, or reached through
#: `getattr(module, "name")` -- counts as a spawn site.
_SPAWN_PRIMITIVES = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.system",
        "os.popen",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.posix_spawnp",
        *(f"os.exec{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
        *(f"os.spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
        "pty.spawn",
        "pty.fork",
        "anyio.open_process",
        "anyio.run_process",
        "multiprocessing.Process",
        "mcp.client.stdio.stdio_client",
        "mcp.client.stdio.StdioServerParameters",
        "mcp.StdioServerParameters",
    }
)
#: Method names that spawn whatever object they are called on: an event loop's
#: `subprocess_exec`, pmcp's `adopt_process`, the docker SDK's `containers.run`.
_SPAWN_METHOD_SUFFIXES = (
    ".subprocess_exec",
    ".subprocess_shell",
    ".adopt_process",
    ".containers.run",
    ".containers.create",
)


def _spawn_call_sites(root: Path | None = None) -> collections.Counter[str]:
    """``{file:qualname: number of spawn references}`` under *root*.

    Resolves each module's imports and aliases (``import subprocess as sp``,
    ``from asyncio import create_subprocess_exec as cse``) and counts every
    REFERENCE to a primitive, not only direct calls and not one per function
    -- a second spawn added to an already-classified function changes its
    count (#295 board round 9, NB-1/NB-2). Annotations are not references.
    """
    import ast

    import pmcp

    base = root or Path(pmcp.__file__).parent
    found: collections.Counter[str] = collections.Counter()
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(base).as_posix()
        tree = ast.parse(path.read_text())
        aliases: dict[str, str] = {}
        annotation_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        aliases.setdefault(top, top)
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.AnnAssign):
                annotation_nodes.update(id(n) for n in ast.walk(node.annotation))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in [
                    *node.args.args,
                    *node.args.kwonlyargs,
                    *node.args.posonlyargs,
                ]:
                    if arg.annotation is not None:
                        annotation_nodes.update(id(n) for n in ast.walk(arg.annotation))
                if node.returns is not None:
                    annotation_nodes.update(id(n) for n in ast.walk(node.returns))

        def resolve(expr: ast.AST) -> str | None:
            if isinstance(expr, ast.Name):
                return aliases.get(expr.id, expr.id)
            if isinstance(expr, ast.Attribute):
                inner = resolve(expr.value)
                return f"{inner}.{expr.attr}" if inner else f"?.{expr.attr}"
            return None

        def is_spawn(dotted: str | None) -> bool:
            if not dotted:
                return False
            return dotted in _SPAWN_PRIMITIVES or dotted.endswith(
                _SPAWN_METHOD_SUFFIXES
            )

        def walk(node: ast.AST, scope: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                inner_scope = scope
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    inner_scope = [*scope, child.name]
                site = f"{rel}:{'.'.join(inner_scope)}"
                if id(child) in annotation_nodes:
                    continue
                if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
                    if is_spawn(resolve(child)):
                        found[site] += 1
                        continue  # the chain's inner nodes are not separate refs
                elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    if is_spawn(resolve(child)):
                        found[site] += 1
                elif (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "getattr"
                    and len(child.args) >= 2
                    and isinstance(child.args[1], ast.Constant)
                    and isinstance(child.args[1].value, str)
                ):
                    owner = resolve(child.args[0])
                    if is_spawn(f"{owner}.{child.args[1].value}"):
                        found[site] += 1
                walk(child, inner_scope)

        walk(tree, [])
    return found


def test_every_spawn_site_is_classified() -> None:
    """Rounds 8-9: every spawn reference in src/pmcp is classified with its
    count, and exactly the sites that spawn or adopt the server are the keys
    of `_SERVER_SPAWN_SITES`, from which `_spawn_set` is BUILT."""
    assert _spawn_call_sites() == collections.Counter(
        {site: count for site, (count, _why) in _SPAWN_SITE_TABLE.items()}
    )
    judged = {
        site for site, (_n, why) in _SPAWN_SITE_TABLE.items() if "(judged)" in why
    }
    assert judged == set(handlers_module._SERVER_SPAWN_SITES)


def test_the_spawn_set_is_built_from_the_site_table() -> None:
    """Round 9 NB-3: the members of a manifest server with install argvs come
    from exactly the sites `_SERVER_SPAWN_SITES` lists."""
    from pmcp.tools.handlers import _spawn_set

    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    members = _spawn_set(server, manifest_server_to_config(server))
    assert {m.site for m in members} == set(handlers_module._SERVER_SPAWN_SITES)
    assert len(members) == 1 + len(PLATFORMS)


_SYNTHETIC_SPAWNS = """
import asyncio, os, subprocess, pty, functools, multiprocessing
import subprocess as sp
from subprocess import run, Popen as P
from asyncio import create_subprocess_exec as cse
import anyio

async def alias_cse(): await cse("npx", "-y", "x")
def alias_run(): run(["npx", "x"])
def alias_sp_run(): sp.run(["npx", "x"])
def alias_popen(): P(["npx", "x"])
async def loop_subprocess_exec():
    loop = asyncio.get_running_loop(); await loop.subprocess_exec(asyncio.SubprocessProtocol, "npx")
def os_execl(): os.execl("/usr/bin/npx", "npx", "x")
def os_execlp(): os.execlp("npx", "npx", "x")
def os_execve(): os.execve("/usr/bin/npx", ["npx"], {})
def os_spawnlp(): os.spawnlp(os.P_WAIT, "npx", "npx", "x")
def os_posix_spawnp(): os.posix_spawnp("npx", ["npx"], {})
def os_system(): os.system("npx x")
def os_popen(): os.popen("npx x")
def pty_spawn(): pty.spawn(["npx", "x"])
async def anyio_open(): await anyio.open_process(["npx", "x"])
async def anyio_runp(): await anyio.run_process(["npx", "x"])
def mp(): multiprocessing.Process(target=print).start()
def getout(): subprocess.getoutput("npx x")
async def getattr_cse(): await getattr(asyncio, "create_subprocess_exec")("npx", "x")
async def partial_cse(): await functools.partial(asyncio.create_subprocess_exec, "npx")("x")
def docker_sdk(client): client.containers.run("img", "cmd")
async def stdio_only(params):
    from mcp.client.stdio import stdio_client
    async with stdio_client(params): pass
async def fork(): os.fork()
def two_spawns():
    subprocess.run(["npx", "x"])
    asyncio.create_subprocess_exec("npx", "y")
def not_a_spawn(proc: subprocess.Popen) -> "subprocess.Popen":
    return proc
"""


def test_the_spawn_site_matcher_resolves_aliases_and_counts_call_sites(
    tmp_path: Path,
) -> None:
    """Round 9 NB-1/NB-2: all 22 variants the seat built are found (aliases,
    `from` imports, os.exec*/spawn*/system/popen, pty, anyio, loop methods,
    stdio_client, the docker SDK, getattr and functools.partial), a second
    spawn in one function counts twice, and an annotation is not a spawn."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sites.py").write_text(_SYNTHETIC_SPAWNS)

    found = _spawn_call_sites(tmp_path / "pkg")

    variants = [
        "alias_cse",
        "alias_run",
        "alias_sp_run",
        "alias_popen",
        "loop_subprocess_exec",
        "os_execl",
        "os_execlp",
        "os_execve",
        "os_spawnlp",
        "os_posix_spawnp",
        "os_system",
        "os_popen",
        "pty_spawn",
        "anyio_open",
        "anyio_runp",
        "mp",
        "getout",
        "getattr_cse",
        "partial_cse",
        "docker_sdk",
        "stdio_only",
        "fork",
    ]
    assert len(variants) == 22
    for name in variants:
        assert found[f"sites.py:{name}"] == 1, name
    assert found["sites.py:two_spawns"] == 2
    assert "sites.py:not_a_spawn" not in found


# ---------------------------------------------------------------------------
# Board round 9 (rev 11): one parser, one judge per spawn member
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "args", "named"),
    [
        (
            "uvx",
            ["--from", "evil==1.0.0", "--from", "fc-mcp==6.0", "fc-mcp"],
            "repeats --from",
        ),
        (
            "uvx",
            ["--from", "fc-mcp==6.1", "--from", "fc-mcp==6.0", "fc-mcp"],
            "repeats --from",
        ),
        (
            "cargo",
            ["install", "fc", "--version", "1.2.3", "--vers", "1.2.4"],
            "repeats --version",
        ),
        (
            "docker",
            [
                "run",
                "-e",
                "SELFHOST_API_URL",
                "--name",
                "a",
                "--name",
                "b",
                f"example/client@{_DIGEST}",
            ],
            "repeats --name",
        ),
        ("npm", ["exec", "-y", "fc-mcp@3.25.5", "--package=evil"], "after the package"),
    ],
    ids=[
        "uvx-from-evil-then-real",
        "uvx-from-skew",
        "cargo-version-twice",
        "docker-name-twice",
        "npm-exec-trailing-flag",
    ],
)
@pytest.mark.asyncio
async def test_a_repeated_single_valued_flag_is_not_a_recognised_shape(
    command: str,
    args: list[str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 9 grok B1: the pin reader took the FIRST `--from` and the shape
    check the LAST, while uv uses the last. One parser now reads the argv, and
    a repeated single-valued flag is unreadable rather than guessed. `npm
    exec` also reads flags AFTER the package (unlike npx)."""
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("sh1", command, args))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.asyncio
async def test_update_server_never_labels_a_repeated_from_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 9 grok B1: `[PINNED] evil 1.0.0` while uv ran cowsay 6.0."""
    server = _launch(
        "uv", "uvx", ["--from", "evil==1.0.0", "--from", "cowsay==6.0", "cowsay"]
    )
    gateway = _gateway(monkeypatch, tmp_path, {"uv": server})
    _latest(monkeypatch, "7.0")
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "uv"})

    assert result.pinned_version is None
    assert "repeats --from" in result.message


@pytest.mark.asyncio
async def test_a_repeated_from_in_an_install_argv_is_unverifiable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _launch("uv", "uvx", ["--from", "fc-mcp==6.1", "fc-mcp"])
    server.install = {
        **{p: ["uvx", "--from", "fc-mcp==6.1", "fc-mcp"] for p in PLATFORMS},
        "linux": ["uvx", "--from", "fc-mcp==6.1", "--from", "fc-mcp==6.0", "fc-mcp"],
    }

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "its linux install argv" in warnings[0]
    assert "repeats --from" in warnings[0]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("NODE_OPTIONS", "--require /srv/hook.js"),
        ("LD_PRELOAD", "/srv/hook.so"),
        ("OPENSSL_CONF", "/srv/evil.cnf"),
        ("PATH", ""),
    ],
    ids=["node-options", "ld-preload", "openssl-conf", "empty-path"],
)
@pytest.mark.parametrize("source", ["manifest", "user"])
@pytest.mark.asyncio
async def test_a_local_command_does_not_waive_the_env_check(
    key: str,
    value: str,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 9 grok B2: the local-binary exemption returned before the env
    was judged. An exemption may waive only the PIN requirement."""
    server = _launch("ow", "/opt/fc/bin/fc-mcp", ["--stdio"])
    server.install = {p: ["/opt/fc/bin/fc-mcp", "--stdio"] for p in PLATFORMS}
    env = {"SELFHOST_API_URL": "http://self-hosted.internal:3002", key: value}
    configured = (
        [
            ResolvedServerConfig(
                name="ow",
                source="user",
                config=LocalMcpServerConfig(
                    command="/opt/fc/bin/fc-mcp", args=["--stdio"], env=env
                ),
            )
        ]
        if source == "user"
        else []
    )
    server.extra_env.update({key: value})
    gateway = _gateway(
        monkeypatch, tmp_path, {"ow": server}, configured=configured, online=["ow"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "ow"]
    assert len(info.warnings) == 1
    assert "run a local command" in info.warnings[0]
    assert f"the entry's env sets {key}" in info.warnings[0]


@pytest.mark.asyncio
async def test_a_local_command_does_not_waive_the_cwd_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = ResolvedServerConfig(
        name="ow",
        source="user",
        config=LocalMcpServerConfig(
            command="/opt/fc/bin/fc-mcp",
            args=[],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(tmp_path),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"ow": _launch("ow", "/opt/fc/bin/fc-mcp", [])},
        configured=[configured],
        online=["ow"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "ow"]
    assert len(info.warnings) == 1
    assert "the entry sets a cwd" in info.warnings[0]


_SECRET = "SYNTHETIC_REVIEW_TOKEN_0123456789"


@pytest.mark.asyncio
async def test_no_diagnostic_renders_a_token_from_an_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 9 codex 1: every argv a warning or message shows goes through the
    ONE secret-safe renderer, on every path."""
    divergent = _server("fc", ["-y", "fc-mcp@3.25.5", "--token", _SECRET])
    divergent.install = {
        **divergent.install,
        "linux": ["npx", "-y", "fc-mcp", "--token", _SECRET],
    }
    local = _launch("ow", "/opt/fc/bin/fc-mcp", ["--token", _SECRET])
    local.install = {
        **{p: ["/opt/fc/bin/fc-mcp", "--token", _SECRET] for p in PLATFORMS},
        "linux": ["npx", "-y", "fc-mcp", "--token", _SECRET],
    }
    url = f"https://user:{_SECRET}@example.test/fc_mcp-1.0.0-py3-none-any.whl"
    wheel = _launch("uv", "uvx", ["--from", f"fc-mcp @ {url}", "fc-mcp"])
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": divergent, "ow": local, "uv": wheel},
        online=["fc", "ow", "uv"],
    )
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    health = await gateway.health()
    update = await gateway.update_server({"server_name": "fc"})

    texts = [w for s in health.servers for w in s.warnings]
    texts += [update.message, *update.warnings]
    assert len([w for s in health.servers for w in s.warnings]) == 3
    assert all(_SECRET not in text for text in texts), texts


@pytest.mark.asyncio
async def test_a_malformed_member_is_contained_to_its_own_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 9 codex 2: `install.mac: ["echo", 42]` raised TypeError, which
    broke update_server and, through a loop-wide except, suppressed ANOTHER
    server's warning. Now it is that server's own "cannot verify"."""
    broken = _server("fc", ["-y", "fc-mcp@3.25.5"])
    broken.install = {**broken.install, "mac": ["echo", 42]}  # type: ignore[list-item]
    other = _server("ok1", ["-y", "other-mcp"])
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": broken, "ok1": other}, online=["fc", "ok1"]
    )
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    health = await gateway.health()
    update = await gateway.update_server({"server_name": "fc"})

    by_name = {s.name: s.warnings for s in health.servers}
    assert len(by_name["fc"]) == 1 and "cannot verify" in by_name["fc"][0]
    assert "not a list of strings" in by_name["fc"][0]
    assert len(by_name["ok1"]) == 1 and "is unpinned" in by_name["ok1"][0]
    assert update.pinned_version is None
    assert update.floating_selector == "3.25.5"


@pytest.mark.asyncio
async def test_an_exception_judging_one_server_never_hides_another(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    # Rev 19: the warning combines the ONE judged-member list with
    # `_verdict_of` (the relaxer reads the same list), so that is the seam.
    real = handlers_module._verdict_of

    def flaky(judged: Any, *rest: Any) -> Any:
        if judged and judged[0].member.argv[-1] == "boom-mcp":
            raise RuntimeError("boom")
        return real(judged, *rest)

    monkeypatch.setattr(handlers_module, "_verdict_of", flaky)
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "fc": _server("fc", ["-y", "boom-mcp"]),
            "ok1": _server("ok1", ["-y", "other-mcp"]),
        },
        online=["fc", "ok1"],
    )

    health = await gateway.health()

    by_name = {s.name: s.warnings for s in health.servers}
    assert len(by_name["fc"]) == 1 and "RuntimeError" in by_name["fc"][0]
    assert len(by_name["ok1"]) == 1 and "is unpinned" in by_name["ok1"][0]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("uvx", ["-qq", "fc-mcp==1.2.3"]),
        ("cargo", ["install", "-fq", "fc", "--version", "1.2.3"]),
        ("cargo", ["install", "fc@1.2.3"]),
        (
            "docker",
            [
                "container",
                "run",
                "-e",
                "SELFHOST_API_URL",
                "-it",
                f"example/client@{_DIGEST}",
            ],
        ),
        ("npm", ["exec", "-y", "fc-mcp@3.25.5", "--", "--port", "1"]),
    ],
    ids=[
        "uvx-qq",
        "cargo-fq",
        "cargo-crate-at-version",
        "docker-container-run",
        "npm-exec-dashdash",
    ],
)
@pytest.mark.asyncio
async def test_the_one_parser_models_common_spellings(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 N2, now cheap: the parsers are pmcp's own, not main's
    `detect_package_type`, so these harmless spellings are recognised."""
    assert (
        await _one_warning(monkeypatch, tmp_path, _launch("ok1", command, args)) == []
    )


@pytest.mark.asyncio
async def test_health_contains_a_failure_outside_the_judge_per_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 9 codex 2: the health loop's own containment -- a failure before
    the judge (here, in the warning function itself) is that server's own
    "cannot verify", and the next server still gets its warning."""
    real = handlers_module._unpinned_self_hosted_warning

    def flaky(name: str, *args: Any) -> Any:
        if name == "fc":
            raise KeyError("boom")
        return real(name, *args)

    monkeypatch.setattr(handlers_module, "_unpinned_self_hosted_warning", flaky)
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "fc": _server("fc", ["-y", "fc-mcp"]),
            "ok1": _server("ok1", ["-y", "other-mcp"]),
        },
        online=["fc", "ok1"],
    )

    health = await gateway.health()

    by_name = {s.name: s.warnings for s in health.servers}
    assert len(by_name["fc"]) == 1 and "KeyError" in by_name["fc"][0]
    assert len(by_name["ok1"]) == 1 and "is unpinned" in by_name["ok1"][0]


@pytest.mark.asyncio
async def test_update_server_never_labels_a_pin_its_judge_did_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 9: the refusal reads the pin with main's reader and the label
    with the feature's ONE parser; if they ever disagree, it is not a pin."""
    monkeypatch.setattr(
        handlers_module, "_detect_effective_version_pin", lambda *_a, **_k: "9.9.9"
    )
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert result.pinned_version is None
    assert "does not name this pin" in result.message


def test_the_judge_checks_every_members_own_env_and_cwd() -> None:
    """Round 9: ONE judge per spawn member. A member whose own env or cwd is
    not inert is "cannot verify" even when args' are clean."""
    from pmcp.tools.handlers import _judge_spawn_set, _Spawn

    clean = {"SELFHOST_API_URL": "http://h"}
    args = _Spawn(
        "its args",
        "client/manager.py:ClientManager._connect_stdio",
        ("npx", "-y", "fc-mcp@3.25.5"),
        clean,
        None,
    )
    for env, cwd, named in [
        ({**clean, "NODE_OPTIONS": "--require /x"}, None, "NODE_OPTIONS"),
        (clean, "/srv/project", "the entry sets a cwd"),
    ]:
        other = _Spawn(
            "its linux install argv",
            "manifest/installer.py:JobManager.start_install",
            ("npx", "-y", "fc-mcp@3.25.5"),
            env,
            cwd,
        )
        verdict = _judge_spawn_set(
            [args, other], frozenset({"SELFHOST_API_URL", "SELFHOST_API_KEY"})
        )
        assert verdict.kind == "cannot_verify"
        assert "its linux install argv" in (verdict.detail or "")
        assert named in (verdict.detail or "")
    assert (
        _judge_spawn_set([args, args], frozenset({"SELFHOST_API_URL"})).kind == "silent"
    )


# ---------------------------------------------------------------------------
# Board round 10 (rev 12): the local-binary exemption needs positive evidence
# ---------------------------------------------------------------------------

_WRAPPERS = {
    "timeout": ["timeout", "600"],
    "nice": ["nice"],
    "stdbuf": ["stdbuf", "-oL"],
    "nohup": ["nohup"],
    "setsid": ["setsid"],
    "sudo": ["sudo", "-E"],
    "busybox-sh": ["busybox", "sh", "-c"],
    "mise-exec": ["mise", "exec", "--"],
    "volta-run": ["volta", "run"],
    "corepack": ["corepack"],
}
_WRAPPED = {
    "npx": ["npx", "-y", "fc-mcp"],
    "npx-pinned": ["npx", "-y", "fc-mcp@3.25.5"],
    "uvx": ["uvx", "fc-mcp==1.2.3"],
    "pnpm-dlx": ["pnpm", "dlx", "fc-mcp"],
}


@pytest.mark.parametrize(
    "argv",
    [
        *[
            [*wrapper, *launched]
            for wrapper in _WRAPPERS.values()
            for launched in _WRAPPED.values()
        ],
        ["pipx", "run", "fc-mcp"],
        ["python3", "-m", "uv", "tool", "run", "fc-mcp==1.2.3"],
        ["go", "run", "example.com/fc@latest"],
        ["npx.js", "-y", "fc-mcp@3.25.5"],
        ["npx.ps1", "-y", "fc-mcp@3.25.5"],
        ["uvx.sh", "fc-mcp==1.2.3"],
        ["fc-mcp"],
    ],
    ids=[
        *[f"{w}+{name}" for w in _WRAPPERS for name in _WRAPPED],
        "pipx-run",
        "python-m-uv",
        "go-run",
        "npx.js",
        "npx.ps1",
        "uvx.sh",
        "bare-unshipped-command",
    ],
)
@pytest.mark.parametrize("source", ["manifest", "user"])
@pytest.mark.asyncio
async def test_an_unrecognised_command_is_never_a_local_binary(
    argv: list[str],
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 10 B-1: every command that is neither a modelled launcher nor a
    runner used to be a "local binary", which waived the pin -- `timeout 600
    npx -y firecrawl-mcp` was silent while latest ran. The exemption now
    needs positive evidence: the command pmcp's shipped manifest names for
    the server. The name here (`overlay-only`) has none."""
    server = ServerConfig(
        name="overlay-only",
        description="x",
        keywords=["x"],
        install={p: list(argv) for p in PLATFORMS},
        command=argv[0],
        args=list(argv[1:]),
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
    )
    monkeypatch.setattr(
        handlers_module,
        "_shipped_manifest_declarations",
        lambda: {"overlay-only": frozenset({"SELFHOST_API_URL", "SELFHOST_API_KEY"})},
    )
    env = {"SELFHOST_API_URL": "http://self-hosted.internal:3002"}
    configured = (
        [
            ResolvedServerConfig(
                name="overlay-only",
                source="user",
                config=LocalMcpServerConfig(command=argv[0], args=argv[1:], env=env),
            )
        ]
        if source == "user"
        else []
    )
    server.extra_env.update(env)
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"overlay-only": server},
        configured=configured,
        online=["overlay-only"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "overlay-only"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]


def test_only_the_shipped_command_is_a_local_binary() -> None:
    """The positive rule, directly: the exact command pmcp's shipped manifest
    names is `local`; anything else, or a launcher with an unstripped
    extension, is `unrecognised`."""
    from pmcp.tools.handlers import _read_argv

    shipped = frozenset({"/opt/fc/bin/fc-mcp"})
    assert _read_argv(("/opt/fc/bin/fc-mcp", "--stdio"), shipped).family == "local"
    assert (
        _read_argv(("/opt/other/fc-mcp", "--stdio"), shipped).family == "unrecognised"
    )
    assert _read_argv(("timeout", "600", "npx", "-y", "x"), shipped).problem
    assert _read_argv(("npx.js", "-y", "x@1.0.0"), frozenset({"npx.js"})).problem
    assert "does not recognise" in (_read_argv(("fc-mcp",)).problem or "")
    # The real shipped manifest names only modelled launchers (npx, uvx).
    assert set(handlers_module._shipped_manifest_commands().values()) <= {"npx", "uvx"}


# ---------------------------------------------------------------------------
# Board round 11 (rev 13): shipped relaxers decide whether the warning applies
# ---------------------------------------------------------------------------


def _firecrawl_overlay(**changes: Any) -> ServerConfig:
    """An overlay's whole-entry replacement of the SHIPPED `firecrawl`."""
    server = ServerConfig(
        name="firecrawl",
        description="x",
        keywords=["x"],
        install={p: ["npx", "-y", "firecrawl-mcp"] for p in PLATFORMS},
        command="npx",
        args=["-y", "firecrawl-mcp"],
        requires_api_key=True,
        env_var="FIRECRAWL_API_KEY",
        api_key_optional_when=["FIRECRAWL_API_URL"],
        extra_env={"FIRECRAWL_API_URL": "http://ai:3002"},
    )
    for field_name, value in changes.items():
        setattr(server, field_name, value)
    return server


@pytest.mark.parametrize(
    "changes",
    [
        {"api_key_optional_when": []},
        {"requires_api_key": False, "api_key_optional_when": []},
        {"requires_api_key": False},
        {"api_key_optional_when": [], "env_var": None},
    ],
    ids=["drops-relaxer", "not-required-no-relaxer", "not-required", "older-copy"],
)
@pytest.mark.asyncio
async def test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer(
    changes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 11: the relaxer keys were read from the overlay-applied entry in
    three places (the update wrapper, the health filter, and relaxed_by), so an
    overlay replacing `firecrawl` without the key silenced the warning while
    FIRECRAWL_API_URL was set and latest ran. The shipped manifest's keys now
    decide whether the warning applies (UNION the overlay's)."""
    server = _firecrawl_overlay(**changes)
    gateway = _gateway(
        monkeypatch, tmp_path, {"firecrawl": server}, online=["firecrawl"]
    )
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    health = await gateway.health()
    update = await gateway.update_server({"server_name": "firecrawl"})

    (info,) = [s for s in health.servers if s.name == "firecrawl"]
    assert len(info.warnings) == 1
    assert "FIRECRAWL_API_URL is set" in info.warnings[0]
    assert "is unpinned" in info.warnings[0]
    assert len(update.warnings) == 1


def test_the_warning_relaxers_are_shipped_union_overlay() -> None:
    from pmcp.tools.handlers import _warning_relaxers

    assert _warning_relaxers("firecrawl", None) == ("FIRECRAWL_API_URL",)
    overlay = _firecrawl_overlay(api_key_optional_when=["EXTRA_URL"])
    assert _warning_relaxers("firecrawl", overlay) == ("FIRECRAWL_API_URL", "EXTRA_URL")
    # The documented limit: an overlay-only name has only its own keys.
    assert _warning_relaxers("overlay-only", None) == ()


# ---------------------------------------------------------------------------
# Board round 12 (rev 14): a relative launcher path is never silent
# ---------------------------------------------------------------------------

_RELATIVE_LAUNCHERS = {
    "docker-deep": (
        "../" * 12 + "usr/bin/docker",
        ["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
    ),
    "docker-sub": (
        "bin/docker",
        ["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
    ),
    "npx-dot": ("./npx", ["-y", "fc-mcp@3.25.5"]),
    "npx-parent": ("../bin/npx", ["-y", "fc-mcp@3.25.5"]),
    "npm-sub": ("bin/npm", ["exec", "-y", "fc-mcp@3.25.5"]),
    "uvx-sub": ("bin/uvx", ["fc-mcp==1.2.3"]),
    "cargo-sub": ("bin/cargo", ["install", "fc", "--version", "1.2.3"]),
}


@pytest.mark.parametrize(
    ("command", "args"),
    list(_RELATIVE_LAUNCHERS.values()),
    ids=list(_RELATIVE_LAUNCHERS),
)
@pytest.mark.parametrize("with_cwd", [True, False], ids=["entry-cwd", "no-cwd"])
@pytest.mark.asyncio
async def test_a_relative_launcher_path_is_never_the_launcher(
    command: str,
    args: list[str],
    with_cwd: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 12 B-1: a relative path was realpath'd from pmcp's cwd, but the OS
    runs it from the CHILD's (entry-set) cwd. docker is exempt from the cwd
    rule, so `../../..../usr/bin/docker` + a deep entry cwd was silent and
    [PINNED] while a planted `<cwd>/../../usr/bin/docker` ran."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    deep = tmp_path.joinpath(*["d"] * 12)
    deep.mkdir(parents=True)
    configured = ResolvedServerConfig(
        name="sh1",
        source="user",
        config=LocalMcpServerConfig(
            command=command,
            args=args,
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(deep) if with_cwd else None,
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"sh1": _launch("sh1", "npx", ["-y", "fc-mcp"])},
        configured=[configured],
        online=["sh1"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "sh1"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert "as the child's PATH resolves it" in info.warnings[0]


@pytest.mark.parametrize(
    ("path", "loud"),
    [
        (".:/usr/bin:/bin", True),
        ("/usr/bin::/bin", True),
        ("bin:/usr/bin", True),
        ("/usr/bin:/bin", False),
    ],
    ids=["dot-first", "empty-entry", "relative-entry", "absolute-only"],
)
@pytest.mark.asyncio
async def test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd(
    path: str,
    loud: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 12 N-2 (decided, not only disclosed): docker is exempt from the
    cwd rule only while the child's PATH cannot find `docker` inside that cwd."""
    monkeypatch.setenv("PATH", path)
    configured = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command="docker",
            args=["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(tmp_path),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"dk": _docker_server("example/client:latest")},
        configured=[configured],
        online=["dk"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "dk"]
    if loud:
        assert len(info.warnings) == 1
        assert "would look for the launcher inside it" in info.warnings[0]
    else:
        assert info.warnings == []


@pytest.mark.parametrize("launcher", ["npx", "docker"])
@pytest.mark.asyncio
async def test_a_relative_path_to_the_resolved_launcher_is_still_not_the_launcher(
    launcher: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 12 B-1, host-independent: even a relative path that, from pmcp's
    own cwd, names exactly the binary the child's PATH resolves is refused --
    the OS resolves it from the child's cwd, which pmcp does not compare."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / launcher).write_text("#!/bin/sh\n")
    (bindir / launcher).chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    relative = os.path.relpath(bindir / launcher, Path.cwd())
    args = (
        ["-y", "fc-mcp@3.25.5"]
        if launcher == "npx"
        else ["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"]
    )
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("sh1", relative, args))

    assert len(warnings) == 1
    assert "as the child's PATH resolves it" in warnings[0]


@pytest.mark.parametrize("launcher", ["npx", "docker"])
@pytest.mark.asyncio
async def test_a_relative_path_equal_to_a_relative_path_search_is_not_the_launcher(
    launcher: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rev 15: with no realpath, the literal comparison alone does not refuse a
    relative spelling when the child's PATH has a RELATIVE entry, because then
    `which` returns that same relative spelling. Both are resolved from a cwd,
    so the relative-path rule must still refuse it."""
    rbin = Path.cwd() / "rbin"
    rbin.mkdir()
    (rbin / launcher).write_text("#!/bin/sh\n")
    (rbin / launcher).chmod(0o755)
    monkeypatch.setenv("PATH", "rbin")
    command = os.path.join("rbin", launcher)
    import shutil

    assert shutil.which(launcher, path="rbin") == command
    args = (
        ["-y", "fc-mcp@3.25.5"]
        if launcher == "npx"
        else ["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"]
    )
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("sh1", command, args))

    assert len(warnings) == 1
    assert "as the child's PATH resolves it" in warnings[0]


# ---------------------------------------------------------------------------
# Board round 13 (rev 15): cwd exemptions only for bare launchers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "with_cwd", "named"),
    [
        ("/proc/self/cwd/{rel}", True, "PATH resolves it"),
        (
            "/proc/thread-self/cwd/{rel}",
            True,
            "PATH resolves it",
        ),
        ("/proc/self/cwd/{rel}", False, "PATH resolves it"),
        ("{bindir}/docker", True, "its launcher is a path"),
    ],
    ids=[
        "proc-self-cwd",
        "proc-thread-self-cwd",
        "proc-self-cwd-no-cwd",
        "resolved-path-with-cwd",
    ],
)
@pytest.mark.asyncio
async def test_a_path_launcher_under_an_entry_cwd_is_never_exempt(
    command: str,
    with_cwd: bool,
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 13 B-1: an ABSOLUTE path can depend on cwd (`/proc/self/cwd/..`),
    and realpath resolved it in pmcp's cwd, not the child's. Nothing is
    resolved in pmcp's process now (the path must equal the PATH search's
    result literally), and docker's cwd exemption holds only for the bare
    `docker` spelling."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text("#!/bin/sh\n")
    (bindir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    deep = tmp_path.joinpath(*["d"] * 12)
    deep.mkdir(parents=True)
    configured = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command=command.format(
                bindir=bindir,
                # From pmcp's own cwd, `/proc/self/cwd/<rel>` resolves to exactly
                # the binary the PATH search finds -- the seat's shape, made
                # host-independent. The child, after chdir, would resolve it
                # inside the entry's cwd.
                rel=os.path.relpath(bindir / "docker", Path.cwd()),
            ),
            args=["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(deep) if with_cwd else None,
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"dk": _docker_server("example/client:latest")},
        configured=[configured],
        online=["dk"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "dk"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert named in info.warnings[0]


@pytest.mark.asyncio
async def test_the_resolved_absolute_path_without_an_entry_cwd_is_the_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Control: the literal PATH-search result, with no entry cwd, is docker."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text("#!/bin/sh\n")
    (bindir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    server = _launch(
        "ok1",
        str(bindir / "docker"),
        ["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.asyncio
async def test_a_proc_path_entry_counts_as_searching_the_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "/proc/self/cwd/bin:/usr/bin:/bin")
    configured = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command="docker",
            args=["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(tmp_path),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"dk": _docker_server("example/client:latest")},
        configured=[configured],
        online=["dk"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "dk"]
    assert len(info.warnings) == 1
    assert "would look for the launcher inside it" in info.warnings[0]


@pytest.mark.parametrize(
    ("command", "args", "loud"),
    [
        ("npx.cmd", ["-y", "fc-mcp@3.25.5", "x|npx", "-y", "fc-mcp@latest"], True),
        ("npx.cmd", ["-y", "fc-mcp@3.25.5", "a&b"], True),
        ("npx", ["-y", "fc-mcp@3.25.5", "%PATH%"], True),
        ("npm.cmd", ["exec", "-y", "fc-mcp@3.25.5", "--", "a b"], True),
        ("npx.cmd", ["-y", "fc-mcp@3.25.5", "--port", "3000"], False),
    ],
    ids=["pipe", "ampersand", "percent", "space", "safe-args"],
)
@pytest.mark.asyncio
async def test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters(
    command: str,
    args: list[str],
    loud: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 13 N-1 (simulated Windows): npx/npm and any `.cmd`/`.bat` run
    through cmd.exe, which does not escape `&`, `|`, `%`; such an argv could
    pipe into an unpinned `npx`. Chosen: only the pin grammar's characters
    and flag spellings are accepted; anything else is "cannot verify"."""
    monkeypatch.setattr(handlers_module, "_is_windows", lambda: True)
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("sh1", command, args))

    if loud:
        assert len(warnings) == 1
        assert "cmd.exe would interpret" in warnings[0]
    else:
        assert warnings == []


@pytest.mark.asyncio
async def test_windows_searches_the_parents_cwd_not_the_entrys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 13 N-3 (simulated): on Windows subprocess resolves the executable
    with the parent's cwd and PATH, so an entry-set cwd does not move a bare
    `docker`; round 12's "always loud on Windows" is corrected."""
    monkeypatch.setattr(handlers_module, "_is_windows", lambda: True)
    monkeypatch.setenv("PATH", ".;C:\\\\tools")
    configured = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command="docker",
            args=["run", "-e", "SELFHOST_API_URL", f"example/client@{_DIGEST}"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(tmp_path),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"dk": _docker_server("example/client:latest")},
        configured=[configured],
        online=["dk"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "dk"]
    assert info.warnings == []


@pytest.mark.parametrize(
    ("args", "expect"),
    [
        (
            [
                "run",
                "-e",
                "SELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        (
            [
                "run",
                "--env=SELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        (
            ["run", "-e", "SELFHOST_API_URL", "example/client:latest"],
            "unpinned-or-floats",
        ),
        (["run", "--env-file", "/srv/env", "example/client:latest"], "env-file"),
        (["run", "example/client:latest"], "silent"),
        # Round 14 B-1 row 3: pflag shorthand, as docker 29.8.1 reads it.
        (
            [
                "run",
                "-eSELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        (
            [
                "run",
                "-ie",
                "SELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        (
            [
                "run",
                "-ieSELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        (
            [
                "run",
                "-ie=SELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        (
            [
                "run",
                "-e=SELFHOST_API_URL=http://self-hosted:3002",
                "example/client:latest",
            ],
            "unpinned-or-floats",
        ),
        # The last assignment wins: a later bare `-e KEY` with nothing in
        # docker's env unsets it, and a later empty value is not usable.
        (
            [
                "run",
                "-e",
                "SELFHOST_API_URL=http://self-hosted:3002",
                "-e",
                "SELFHOST_API_URL",
                "example/client:latest",
            ],
            "silent",
        ),
        (
            [
                "run",
                "-e",
                "SELFHOST_API_URL=http://self-hosted:3002",
                "-eSELFHOST_API_URL=",
                "example/client:latest",
            ],
            "silent",
        ),
        # A cluster pmcp does not read is "may" (the container env is unknown).
        (
            ["run", "-iw", "/x", "example/client:latest"],
            "env-file",
        ),
    ],
    ids=[
        "e-key-value",
        "env-equals",
        "e-inherit",
        "env-file",
        "not-passed-to-container",
        "e-attached",
        "cluster-ie-separate",
        "cluster-ie-attached",
        "cluster-ie-equals",
        "e-equals",
        "last-wins-unset",
        "last-wins-empty",
        "unread-cluster",
    ],
)
@pytest.mark.asyncio
async def test_docker_judges_the_relaxer_on_the_containers_env(
    args: list[str],
    expect: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 13 N-2: the MCP client runs INSIDE the container, so the relaxer
    that makes the warning apply is the one the container receives: `-e
    KEY=VAL`, `--env=KEY=VAL`, `-e KEY` (passed through from docker's env).
    An `--env-file` pmcp cannot read might carry it: the warning applies and
    says so. A URL that stays in docker's own env never reaches the client."""
    server = _launch("dk", "docker", args)
    if "SELFHOST_API_URL=" in " ".join(args):
        server.extra_env.pop("SELFHOST_API_URL", None)

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    if expect == "silent":
        assert warnings == []
    elif expect == "env-file":
        assert len(warnings) == 1
        assert "may talk to a self-hosted backend" in warnings[0]
        assert "cannot verify" in warnings[0]
    else:
        assert len(warnings) == 1
        assert "SELFHOST_API_URL is set" in warnings[0]
        assert "floats on" in warnings[0] or "is unpinned" in warnings[0]


# ---------------------------------------------------------------------------
# Board round 14 (rev 16): the relaxer is judged per spawn member by the one
# parser
# ---------------------------------------------------------------------------


def _docker_args_npx_install() -> ServerConfig:
    """Round 14 B-1 row 1: an overlay `command: docker` + digest, the shipped
    unpinned `install: npx -y fc-mcp` kept, the relaxer in `extra_env`."""
    server = _docker_server(f"example/client@{_DIGEST}")
    server.args = ["run", "-i", "--rm", f"example/client@{_DIGEST}"]
    server.install = {p: ["npx", "-y", "fc-mcp"] for p in PLATFORMS}
    return server


@pytest.mark.asyncio
async def test_a_docker_args_member_does_not_switch_off_an_install_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 14 B-1 row 1: the args container never sees the URL, but
    gateway.provision spawns and adopts the unpinned npx install argv WITH it.
    The warning applies if ANY member applies."""
    warnings = await _one_warning(monkeypatch, tmp_path, _docker_args_npx_install())

    assert len(warnings) == 1
    assert "talks to a self-hosted backend (SELFHOST_API_URL is set)" in warnings[0]
    assert "cannot verify" in warnings[0]
    assert "its linux install argv" in warnings[0]


@pytest.mark.asyncio
async def test_update_server_warns_about_a_docker_args_member_with_an_npx_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 14 B-1 row 1, on update_server's surface."""
    server = _docker_args_npx_install()
    gateway = _gateway(monkeypatch, tmp_path, {"dk": server})
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "dk"})

    assert result.pinned_version is None
    assert len(result.warnings) == 1
    assert "SELFHOST_API_URL is set" in result.warnings[0]
    assert "its linux install argv" in result.warnings[0]


@pytest.mark.parametrize("source", ["entry-env", "host-export"])
@pytest.mark.parametrize("spelling", ["absolute", "dot-slash", "upper", "exe-on-linux"])
@pytest.mark.asyncio
async def test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env(
    spelling: str,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 14 B-1 row 2: a binary named like docker is not docker, so it has
    no "container env"; the relaxer is in its OWN env, where it runs."""
    fake = tmp_path / "evil" / "docker"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    command = {
        "absolute": str(fake),
        "dot-slash": "./docker",
        "upper": "DOCKER",
        "exe-on-linux": "docker.exe",
    }[spelling]
    server = _launch("dk", command, ["run", "-i", "--rm", "example/client:latest"])
    if source == "host-export":
        server.extra_env.pop("SELFHOST_API_URL")
        monkeypatch.setenv("SELFHOST_API_URL", "http://self-hosted.internal:3002")

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "talks to a self-hosted backend (SELFHOST_API_URL is set)" in warnings[0]
    assert "cannot verify" in warnings[0]


@pytest.mark.asyncio
async def test_a_clean_docker_run_without_the_url_in_the_container_stays_quiet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The precision revision 15 wanted still holds for the clean case: a real
    docker run whose container never receives the URL, and no other member."""
    monkeypatch.setenv("SELFHOST_API_URL", "http://self-hosted.internal:3002")
    server = _launch("dk", "docker", ["run", "-i", "--rm", "example/client:latest"])
    server.extra_env.pop("SELFHOST_API_URL")

    assert await _one_warning(monkeypatch, tmp_path, server) == []


def test_the_one_docker_reader_returns_the_container_env_in_order() -> None:
    """No second parser: `_read_docker` returns the assignments it walks."""
    reading = handlers_module._read_docker(
        ["run", "-ie", "A=1", "-eB", "--env=C=3", "-e=A=4", f"img@{_DIGEST}"]
    )
    assert reading.problem is None
    assert reading.container_env == (("A", "1"), ("B", None), ("C", "3"), ("A", "4"))
    assert reading.env_keys == ("A", "B", "C", "A")
    assert not hasattr(handlers_module, "_docker_container_env")


@pytest.mark.asyncio
async def test_a_failure_while_deciding_whether_the_warning_applies_is_loud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reading the members now happens BEFORE the verdict; a failure there is
    contained to this server and never reads as "does not apply"."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(handlers_module, "_read_argv", boom)
    warnings = await _one_warning(
        monkeypatch, tmp_path, _launch("dk", "docker", ["run", "example/client:latest"])
    )

    assert len(warnings) == 1
    assert "may talk to a self-hosted backend" in warnings[0]
    assert "evaluating its argvs failed (RuntimeError)" in warnings[0]


# ---------------------------------------------------------------------------
# Board round 15 (rev 17): the container env counts only for a member that
# passed the per-member judge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "loud"),
    [
        ("entry-path", True),
        ("entry-path-digest", True),
        ("entry-dot-path-planted-cwd", True),
        ("host-dot-path-entry-cwd", True),
        ("declared-entry-url-no-e", False),
        ("host-url-no-e", False),
    ],
)
@pytest.mark.asyncio
async def test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env(
    case: str,
    loud: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 15 B-1: a bare `docker` is docker only after the member passed the
    per-member judge. An entry `PATH`, or an entry cwd that the `PATH` searches,
    can pick another `docker`, which receives the URL in its OWN env; then the
    process env counts as well as the container env. The controls (a declared
    URL in the entry env, a host export, neither passed with `-e`) stay quiet."""
    url = "http://self-hosted.internal:3002"
    evil_bin = tmp_path / "evil" / "bin"
    evil_bin.mkdir(parents=True)
    (evil_bin / "docker").write_text("#!/bin/sh\n")
    (evil_bin / "docker").chmod(0o755)
    evil_cwd = tmp_path / "evilcwd"
    evil_cwd.mkdir()
    (evil_cwd / "docker").write_text("#!/bin/sh\n")
    (evil_cwd / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    image = "example/client:latest"
    env: dict[str, str] = {}
    cwd: str | None = None
    if case == "entry-path":
        env = {"PATH": f"{evil_bin}:/usr/bin:/bin", "SELFHOST_API_URL": url}
    elif case == "entry-path-digest":
        env = {"PATH": f"{evil_bin}:/usr/bin:/bin", "SELFHOST_API_URL": url}
        image = f"example/client@{_DIGEST}"
    elif case == "entry-dot-path-planted-cwd":
        env = {"PATH": ".:/usr/bin:/bin", "SELFHOST_API_URL": url}
        cwd = str(evil_cwd)
    elif case == "host-dot-path-entry-cwd":
        monkeypatch.setenv("PATH", ".:/usr/bin:/bin")
        monkeypatch.setenv("SELFHOST_API_URL", url)
        cwd = str(evil_cwd)
        image = f"example/client@{_DIGEST}"
    elif case == "declared-entry-url-no-e":
        env = {"SELFHOST_API_URL": url}
    else:
        monkeypatch.setenv("SELFHOST_API_URL", url)
    configured = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command="docker", args=["run", "-i", "--rm", image], env=env, cwd=cwd
        ),
    )
    manifest = _docker_server(image)
    manifest.extra_env.pop("SELFHOST_API_URL")
    gateway = _gateway(
        monkeypatch, tmp_path, {"dk": manifest}, configured=[configured], online=["dk"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "dk"]
    if loud:
        assert len(info.warnings) == 1
        assert "SELFHOST_API_URL is set" in info.warnings[0]
    else:
        assert info.warnings == []


# ---------------------------------------------------------------------------
# Board round 16 (rev 18): an unreadable spawn member may talk to the backend
# ---------------------------------------------------------------------------

_URL = "http://self-hosted.internal:3002"
_UNREADABLE_MEMBERS = {
    # The seat's key-mention rows: the argv sets the relaxer on the client.
    "a-env-prefix": ("env", [f"SELFHOST_API_URL={_URL}", "npx", "-y", "fc-mcp"]),
    "b-sh-c": ("sh", ["-c", f"SELFHOST_API_URL={_URL} exec npx -y fc-mcp"]),
    "c-npx-c": ("npx", ["-y", "-c", f"SELFHOST_API_URL={_URL} fc-mcp"]),
    "d-docker-path-not-which": (
        "/usr/bin/docker",
        [
            "run",
            "-i",
            "--rm",
            "-e",
            f"SELFHOST_API_URL={_URL}",
            "example/client:latest",
        ],
    ),
    "e-podman": (
        "podman",
        [
            "run",
            "-i",
            "--rm",
            "-e",
            f"SELFHOST_API_URL={_URL}",
            "example/client:latest",
        ],
    ),
    "f-env-prefix-exact-pin": (
        "env",
        [f"SELFHOST_API_URL={_URL}", "npx", "-y", "fc-mcp@3.25.5"],
    ),
    # Stronger than a key mention: ANY argv pmcp cannot fully read may set it
    # (a wrapper script, an env file, a shell rc), named or not.
    "no-key-mention-sh-c": ("sh", ["-c", "exec npx -y fc-mcp"]),
    "no-key-mention-unknown-launcher": ("my-launcher", ["fc-mcp"]),
}


@pytest.mark.parametrize(
    ("command", "args"),
    list(_UNREADABLE_MEMBERS.values()),
    ids=list(_UNREADABLE_MEMBERS),
)
@pytest.mark.asyncio
async def test_an_unreadable_member_may_talk_to_the_self_hosted_backend(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 16 B-1: only docker's unreadable argv was "may"; an `env K=V`
    prefix, `sh -c`, `npx -c`, a docker path that is not the PATH's, or podman
    set the relaxer on the client itself and the warning was None (verdict
    cannot_verify, never shown). Any member pmcp cannot fully read, any
    launcher, any reason, now MAY talk to the backend, named in the warning.
    No relaxer is in the host env or the entry's env."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text("#!/bin/sh\n")
    (bindir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.delenv("SELFHOST_API_URL", raising=False)
    server = _launch("dk", command, args)
    server.extra_env.pop("SELFHOST_API_URL")

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "'dk' may talk to a self-hosted backend (its args (`" in warnings[0]
    assert "which pmcp cannot fully read, can set SELFHOST_API_URL" in warnings[0]
    assert "cannot verify" in warnings[0]
    assert _URL not in warnings[0]


@pytest.mark.asyncio
async def test_an_unreadable_install_member_may_talk_to_the_self_hosted_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """The rule is per spawn member: a clean pinned args with an install argv
    pmcp cannot read (which provision spawns and adopts) is named."""
    monkeypatch.delenv("SELFHOST_API_URL", raising=False)
    server = _server("fc", ["-y", "fc-mcp@3.25.5"], relaxer_value=None)
    server.install = {
        **server.install,
        "linux": ["sh", "-c", f"SELFHOST_API_URL={_URL} exec npx -y fc-mcp"],
    }

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "may talk to a self-hosted backend (its linux install argv" in warnings[0]
    assert "cannot verify" in warnings[0]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("npx", ["-y", "fc-mcp"]),
        ("npx", ["-y", "fc-mcp@3.25.5"]),
        ("docker", ["run", "-i", "--rm", f"example/client@{_DIGEST}"]),
        ("docker", ["run", "-i", "--rm", "example/client:latest"]),
        ("fc-mcp", []),
    ],
    ids=["npx-unpinned", "npx-pinned", "docker-digest", "docker-latest", "local"],
)
@pytest.mark.asyncio
async def test_a_fully_read_member_without_the_relaxer_stays_quiet(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Controls: a vendor-hosted client read fully by the one parser, with no
    relaxer anywhere, is not warned about, pinned or not."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SELFHOST_API_URL", raising=False)
    server = _launch("dk", command, args)
    server.extra_env.pop("SELFHOST_API_URL")

    assert await _one_warning(monkeypatch, tmp_path, server) == []


# ---------------------------------------------------------------------------
# Board round 17 (rev 19): any member problem makes the relaxer may-talk
# ---------------------------------------------------------------------------

_NODE_URL = (
    "--import=data:text/javascript,process.env.SELFHOST_API_URL="
    "'http://self-hosted.internal:3002'"
)


@pytest.mark.parametrize(
    ("command", "args", "env", "with_cwd", "named"),
    [
        (
            "npx",
            ["-y", "fc-mcp@3.25.5"],
            {"NODE_OPTIONS": _NODE_URL},
            False,
            "the entry's env sets NODE_OPTIONS",
        ),
        (
            "npx",
            ["-y", "fc-mcp"],
            {"NODE_OPTIONS": _NODE_URL},
            False,
            "the entry's env sets NODE_OPTIONS",
        ),
        (
            "npx",
            ["-y", "fc-mcp@3.25.5"],
            {"npm_config_node_options": _NODE_URL},
            False,
            "the entry's env sets npm_config_node_options",
        ),
        (
            "docker",
            ["run", "-i", "--rm", "-e", f"NODE_OPTIONS={_NODE_URL}", f"img@{_DIGEST}"],
            {},
            False,
            "its argv sets container env NODE_OPTIONS",
        ),
        (
            "npx",
            ["-y", "fc-mcp@3.25.5"],
            {},
            True,
            "the entry sets a cwd",
        ),
    ],
    ids=[
        "g-node-options-pinned",
        "g2-unpinned",
        "g3-npm-config",
        "i-docker-e",
        "h-cwd",
    ],
)
@pytest.mark.asyncio
async def test_a_fully_read_member_with_a_judge_problem_may_talk(
    command: str,
    args: list[str],
    env: dict[str, str],
    with_cwd: bool,
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 17 B-1: a member the parser reads fully can still set the relaxer
    inside the client through what the judge already flags -- `NODE_OPTIONS
    --import=data:...`, `npm_config_node_options`, a docker `-e NODE_OPTIONS`,
    an entry cwd whose `.env` the client's own dotenv reads. The relaxer
    decision reads the SAME judged member, so any problem is "may talk",
    naming it. No relaxer is in the host env or the entry's env."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SELFHOST_API_URL", raising=False)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / ".env").write_text("SELFHOST_API_URL=http://self-hosted.internal:3002\n")
    configured = ResolvedServerConfig(
        name="fc",
        source="user",
        config=LocalMcpServerConfig(
            command=command,
            args=args,
            env=env,
            cwd=str(cwd) if with_cwd else None,
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"], relaxer_value=None)},
        configured=[configured],
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "'fc' may talk to a self-hosted backend (its args (`" in info.warnings[0]
    assert named in info.warnings[0]
    assert "so it can set SELFHOST_API_URL for the client" in info.warnings[0]
    assert "self-hosted.internal" not in info.warnings[0]


@pytest.mark.asyncio
async def test_an_overlay_env_key_problem_may_talk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Round 17 overlay row: `server_env: {NODE_OPTIONS: ...}` with a pin."""
    monkeypatch.delenv("SELFHOST_API_URL", raising=False)
    server = _server("fc", ["-y", "fc-mcp@3.25.5"], relaxer_value=None)
    server.extra_env = {"NODE_OPTIONS": _NODE_URL}

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "may talk to a self-hosted backend" in warnings[0]
    assert "the entry's env sets NODE_OPTIONS" in warnings[0]


@pytest.mark.parametrize(
    ("command", "args", "env"),
    [
        ("npx", ["-y", "fc-mcp@3.25.5"], {}),
        ("npx", ["-y", "fc-mcp"], {"SELFHOST_API_KEY": "k-123"}),
        ("npx", ["-y", "fc-mcp@3.25.5"], {"LANG": "C.UTF-8"}),
        (
            "docker",
            ["run", "-i", "--rm", "-e", "SELFHOST_API_KEY", f"img@{_DIGEST}"],
            {},
        ),
    ],
    ids=["pinned-no-env", "declared-key", "locale-key", "docker-declared-e"],
)
@pytest.mark.asyncio
async def test_a_member_with_no_judge_problem_and_no_relaxer_stays_quiet(
    command: str,
    args: list[str],
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Controls: no problem at all, no relaxer -- vendor-hosted, no warning."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SELFHOST_API_URL", raising=False)
    configured = ResolvedServerConfig(
        name="fc",
        source="user",
        config=LocalMcpServerConfig(command=command, args=args, env=env),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"], relaxer_value=None)},
        configured=[configured],
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_the_env_file_reason_keeps_its_specific_wording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 17 N-3: docker's --env-file keeps its own text."""
    server = _launch("dk", "docker", ["run", "--env-file", "/srv/env", "img:latest"])
    server.extra_env.pop("SELFHOST_API_URL")

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert (
        "may talk to a self-hosted backend (its docker --env-file, which pmcp cannot "
        "read, can set SELFHOST_API_URL in the container)"
    ) in warnings[0]


def test_the_relaxer_and_the_verdict_read_one_judged_member() -> None:
    """No second path: `_member_relaxer` takes the judge's `_Judged`."""
    import inspect

    params = list(inspect.signature(handlers_module._member_relaxer).parameters)
    assert params[0] == "judged"
    assert "declared" not in params and "path_var" not in params


# ---------------------------------------------------------------------------
# Board round 18 (rev 20): uv interpreter requests are never an exact pin
# ---------------------------------------------------------------------------

_UV_INTERPRETER_FORMS = {
    # The three forms (codex B1), then every uv 0.12.19 spelling measured.
    "uvx": ("uvx", ["python==3.10", "-c", "pass"]),
    "uvx-from": ("uvx", ["--from", "python==3.10", "python"]),
    "uv-tool-run": ("uv", ["tool", "run", "python==3.10"]),
    "upper": ("uvx", ["PYTHON==3.10"]),
    "cpython": ("uvx", ["CPython==3.10"]),
    "pypy": ("uvx", ["pypy==3.10"]),
    "graalpy": ("uvx", ["GraalPy==3.10"]),
    "pyodide": ("uvx", ["pyodide==3.10"]),
    "pythonw": ("uvx", ["pythonw==3.10"]),
    "suffix-version": ("uvx", ["python3.10"]),
    "suffix-nodot": ("uvx", ["python310"]),
    "suffix-pypy3": ("uvx", ["pypy3"]),
    "specifier": ("uvx", ["python>=3.10,<3.12"]),
    "at-version": ("uvx", ["cpython@3.10"]),
    # Round 19: uv skips whitespace after the name; packaging accepts a space
    # or tab there, and refuses a vertical tab that uv still runs (3.10.21).
    "space": ("uvx", ["python ==3.10"]),
    "from-space": ("uvx", ["--from", "python == 3.10", "python"]),
    "tool-space": ("uv", ["tool", "run", "cpython ==3.10"]),
    "vertical-tab": ("uvx", ["python\x0b==3.10"]),
    # A suffixed spelling packaging reads as ANOTHER name (`Python3.10`), which
    # only the case-insensitive text check catches (uv ran it: 3.10.21).
    "upper-suffix": ("uvx", ["Python3.10"]),
}


@pytest.mark.parametrize(
    ("command", "args"),
    list(_UV_INTERPRETER_FORMS.values()),
    ids=list(_UV_INTERPRETER_FORMS),
)
@pytest.mark.asyncio
async def test_a_uv_interpreter_request_is_never_an_exact_pin(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 18 codex B1: uv reads `python==3.10` (and every implementation
    name, any case, with a version request) as an INTERPRETER of that series,
    whose patch floats (`uvx python==3.10` ran 3.10.21), not the PyPI package
    `python`. The one parser says so; the warning and `[PINNED]` never do."""
    server = _launch("uv", command, args)
    assert handlers_module._read_argv((command, *args)).problem is not None

    warnings = await _one_warning(monkeypatch, tmp_path, server)
    gateway = _gateway(monkeypatch, tmp_path, {"uv": server})
    _latest(monkeypatch, "3.11")
    _no_probe(monkeypatch, gateway)
    result = await gateway.update_server({"server_name": "uv"})

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert "as an interpreter request (" in warnings[0]
    assert result.pinned_version is None


@pytest.mark.parametrize(
    "requirement",
    [
        "python-dotenv==1.0.1",
        "python3-openid==3.2.0",
        "pythonnet==3.0.3",
        "pypylon==4.0.0",
    ],
)
def test_a_package_named_like_an_interpreter_is_still_a_package(
    requirement: str,
) -> None:
    """Controls: uv reads a `-` suffix, and other names, as packages (measured)."""
    reading = handlers_module._read_argv(("uvx", requirement))
    assert (reading.family, reading.problem, reading.exact) == ("pypi", None, True)


# ---------------------------------------------------------------------------
# Board round 19 (rev 21): the interpreter check on the parsed name; [PINNED]
# only from the judge
# ---------------------------------------------------------------------------

# The round-19 seat's generator: names x separators x version tails.
_UV_NAMES = [
    "python", "pythonw", "cpython", "pypy", "graalpy", "pyodide", "PYTHON", "PyPy",
    "CPython", "GraalPy", "Pyodide", "pYthon", "py", "cp", "pp", "gp", "python3",
    "python310", "pypy3", "cpython3", "pythonv", "python-dotenv", "pythonnet",
    "pypylon", "python_x", "python.x", "cpython-x", "pyodide_kit",
]  # fmt: skip
_UV_SEPS = ["", " ", "  ", "\t", "\n", " ("]
_UV_TAILS = [
    "==3.10", "== 3.10", "===3.10", "==3.10.21", "==3.10.0", "==3", "==310",
    "==3.10t", "==3.10+debug", "==3.10.*", ">=3.10", "~=3.10", "!=3.9", "==v3.10",
    "==3.10rc1", "==3.10,<4", "==3.10 ; python_version>'3'", "[x]==3.10",
]  # fmt: skip
# Real uv 0.12.19 on the plan's host, offline, no cache, cwd /tmp, for each
# (name, separator, tail) in that order: I = uv runs (or needs) an INTERPRETER,
# P = it resolves a package, E = it rejects the spelling. `uvx <c>`,
# `uvx --from <c> python` and `uv tool run <c>` measured identical
# (measure_uv.py, plan appendix).
_UV_MEASURED = {
    "python": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "pythonw": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "cpython": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "pypy": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "graalpy": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "pyodide": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "PYTHON": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "PyPy": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "CPython": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "GraalPy": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "Pyodide": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "pYthon": "IIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPIIIIIIIIIIIIIIIIPPPPPPPPPEPPPPPPPPEE",
    "py": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "cp": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "pp": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "gp": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "python3": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "python310": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "pypy3": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "cpython3": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "pythonv": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "python-dotenv": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "pythonnet": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "pypylon": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "python_x": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "python.x": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "cpython-x": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
    "pyodide_kit": "PPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPPPPPPPPPPEPPPPPPPPEE",
}


def _uv_component(name: str, sep: str, tail: str) -> str:
    return name + sep + tail + (")" if sep == " (" else "")


def test_no_argv_real_uv_runs_as_an_interpreter_is_read_as_a_pin() -> None:
    """Round 19 B1, differential: `uvx 'python ==3.10'` (whitespace after the
    name) ran 3.10.21 in uv but pmcp read an exact PyPI pin. For every cell uv
    measured as an interpreter, in all three launch forms, the one parser now
    refuses (checked on the parsed requirement name as well as the text)."""
    assert sum(v.count("I") for v in _UV_MEASURED.values()) == 880
    exact_interpreters = []
    for name in _UV_NAMES:
        for j, sep in enumerate(_UV_SEPS):
            for k, tail in enumerate(_UV_TAILS):
                if _UV_MEASURED[name][j * len(_UV_TAILS) + k] != "I":
                    continue
                c = _uv_component(name, sep, tail)
                for argv in (
                    ("uvx", c),
                    ("uvx", "--from", c, name),
                    ("uv", "tool", "run", c),
                ):
                    reading = handlers_module._read_argv(argv)
                    if reading.exact or reading.problem is None:
                        exact_interpreters.append(argv)
    assert exact_interpreters == []


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("uvx", ["python ==3.10"]),
        ("uvx", ["--from", "python == 3.10", "python"]),
        ("uvx", ["python\t==3.10"]),
        ("uvx", ["python[x]==3.10"]),
        ("npx", ["-y", "fc-mcp@3.25.5"]),  # refused below by its env, not its argv
    ],
    ids=["ws", "from-ws", "tab", "extra", "env-problem"],
)
@pytest.mark.asyncio
async def test_an_argv_the_judge_refuses_never_shows_pinned(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 19: main's own pin reader (`_detect_effective_version_pin`) still
    decides that update_server STOPS, but `[PINNED]` and `pinned_version` come
    only from the one judge's `silent` verdict."""
    from pmcp.cli import _format_update_result

    server = _launch("uv", command, args)
    if command == "npx":
        server.extra_env["NODE_OPTIONS"] = "--require=/srv/x.js"
    gateway = _gateway(monkeypatch, tmp_path, {"uv": server})
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "uv"})

    assert result.pinned_version is None
    assert not _format_update_result(result.model_dump())[0].startswith("[PINNED]")


# ---------------------------------------------------------------------------
# Board round 20 (rev 22): every judge diagnostic through the secret-safe
# renderer or a closed grammar
# ---------------------------------------------------------------------------

_SENTINEL = "SYNTHETIC_REVIEW_TOKEN"
_U = f"https://user:{_SENTINEL}@example.test/x"  # URL userinfo
_Q = f"https://example.test/x?token={_SENTINEL}"  # query-string token
_D = f"example/client@{_DIGEST}"
# (command, args, entry env, cwd, windows, install) -- one or more per
# problem-producing branch of the one parser and the per-member judge. The
# sentinel rides in a VALUE position: URL userinfo, a query string, a
# `--flag=value` / `--flag value`, an env value, a cwd.
_LEAK_CASES = [
    ("npx", ["-y", f"--registry={_U}", "fc-mcp@1.0.0"], {}, None, False, None),
    ("npx", ["-y", f"fc-mcp@{_U}"], {}, None, False, None),
    ("npx", ["-y"], {"SELFHOST_API_URL": _Q}, None, False, None),
    ("npm", ["install", _U], {}, None, False, None),
    (
        "npm",
        ["exec", "-y", "fc-mcp@1.0.0", f"--token={_SENTINEL}"],
        {},
        None,
        False,
        None,
    ),
    ("uvx", ["--from", "fc-mcp", "--from", _U, "fc-mcp"], {}, None, False, None),
    ("uvx", ["--from"], {"SELFHOST_API_URL": _U}, None, False, None),
    ("uvx", [f"--index-url={_U}", "fc-mcp==1.0"], {}, None, False, None),
    ("uvx", ["--from", f"python @ {_U}", "python"], {}, None, False, None),
    ("uvx", ["--from", f"python @ {_Q}", "python"], {}, None, False, None),
    ("uvx", [f"fc mcp @@ {_U}"], {}, None, False, None),
    ("uvx", ["--from", f"fc-mcp @ {_U}", "other"], {}, None, False, None),
    ("uvx", ["-q"], {"SELFHOST_API_URL": _Q}, None, False, None),
    ("uvx", [f"--{_U}", "fc-mcp==1.0"], {}, None, False, None),
    ("cargo", [f"+{_U}", "install", "fc"], {}, None, False, None),
    ("cargo", ["build", f"--config={_U}"], {}, None, False, None),
    (
        "cargo",
        ["install", "--version", "1.0.0", "--version", _U, "fc"],
        {},
        None,
        False,
        None,
    ),
    ("cargo", ["install", "fc", "--jobs"], {"SELFHOST_API_URL": _U}, None, False, None),
    ("cargo", ["install", f"--git={_U}", "fc"], {}, None, False, None),
    ("cargo", ["install", "fc", _U], {}, None, False, None),
    (
        "cargo",
        ["install", "fc@1.0.0", "--version", "1.0.0"],
        {"SELFHOST_API_URL": _Q},
        None,
        False,
        None,
    ),
    ("docker", ["pull", _U], {}, None, False, None),
    ("docker", ["run", "--env"], {"SELFHOST_API_URL": _U}, None, False, None),
    ("docker", ["run", "--name", "a", "--name", _U, _D], {}, None, False, None),
    ("docker", ["run", f"--env-file={_U}", _D], {}, None, False, None),
    ("docker", ["run", "-iP", _D], {"SELFHOST_API_URL": _Q}, None, False, None),
    ("docker", ["run", "-e"], {"SELFHOST_API_URL": _Q}, None, False, None),
    ("docker", ["run", _D, "sh", "-c", f"curl {_U}"], {}, None, False, None),
    ("docker", ["run", "-e", f"K={_U}"], {}, None, False, None),
    ("npx", ["-y", "fc-mcp@1.0.0"], {}, None, False, ["npx", 5, _U]),
    ("npx", ["-y", "fc-mcp@1.0.0", f"x|curl {_U}"], {}, None, True, None),
    ("./npx", ["-y", _U], {}, None, False, None),
    ("sh", ["-c", f"curl {_U}"], {}, None, False, None),
    ("npx.js", [_U], {}, None, False, None),
    ("my-launcher", ["--token", _SENTINEL], {}, None, False, None),
    (
        "npx",
        ["-y", "fc-mcp@1.0.0"],
        {"NODE_OPTIONS": f"--require={_U}"},
        None,
        False,
        None,
    ),
    ("docker", ["run", "-e", f"NODE_OPTIONS={_U}", _D], {}, None, False, None),
    ("npx", ["-y", "fc-mcp@1.0.0"], {}, f"/srv/{_SENTINEL}", False, None),
    ("docker", ["run", _D], {}, f"/srv/{_SENTINEL}", False, None),
    ("npx", ["-y", "fc-mcp@1.0.0"], {}, None, False, ["sh", "-c", f"curl {_U}"]),
    ("npx", ["-y", "fc-mcp@1.0.0"], {}, None, False, ["npx", "-y", "other-mcp@2.0.0"]),
    ("fc-mcp", [], {}, None, False, ["fc-mcp", f"--token={_SENTINEL}"]),
]


def _problem_call_sites() -> list[tuple[int, int]]:
    """Every `_Reading(..., problem=...)` call in handlers.py, by line span."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(handlers_module))
    return sorted(
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_Reading"
        and any(kw.arg == "problem" for kw in node.keywords)
    )


@pytest.mark.asyncio
async def test_no_credential_reaches_a_warning_an_update_message_or_a_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 20 codex B1, as a property: for EVERY problem-producing branch of
    the one parser (found from the source, so a new branch needs a case) and
    every member-problem kind, an input carrying a credential-shaped sentinel
    never shows it in the health warning, update_server's message and
    warnings, or any log line."""
    import sys

    hits: set[int] = set()
    base = handlers_module._Reading

    class _Recorded(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if self.problem is not None:
                hits.add(sys._getframe(1).f_lineno)

    monkeypatch.setattr(handlers_module, "_Reading", _Recorded)
    monkeypatch.setenv("PATH", ".:/usr/bin:/bin")
    caplog.set_level(logging.DEBUG)
    leaks, quiet, texts = [], [], []
    for command, args, env, cwd, windows, install in _LEAK_CASES:
        monkeypatch.setattr(handlers_module, "_is_windows", lambda w=windows: w)
        server = _server("fc", ["-y", "fc-mcp@1.0.0"])
        if install is not None:
            server.install = {p: list(install) for p in PLATFORMS}
            server.command, server.args = command, list(args)
            configured = None
        else:
            configured = ResolvedServerConfig(
                name="fc",
                source="user",
                config=LocalMcpServerConfig(
                    command=command, args=list(args), env=dict(env), cwd=cwd
                ),
            )
        gateway = _gateway(
            monkeypatch,
            tmp_path,
            {"fc": server},
            configured=[configured] if configured else None,
            online=["fc"],
        )
        _latest(monkeypatch, "9.9.9")
        _no_probe(monkeypatch, gateway)
        caplog.clear()
        health = await gateway.health()
        result = await gateway.update_server({"server_name": "fc"})
        (info,) = [s for s in health.servers if s.name == "fc"]
        shown = " ".join(
            [*info.warnings, result.message, *result.warnings, caplog.text]
        )
        texts.extend(info.warnings)
        if _SENTINEL in shown:
            leaks.append((command, args))
        if not info.warnings:
            quiet.append((command, args))
    assert leaks == []
    assert quiet == []
    # The empty-spawn-set branch (fixed text) is unreachable from a config.
    handlers_module._judge_spawn_set([], frozenset())
    sites = _problem_call_sites()
    uncovered = [s for s in sites if not any(s[0] <= h <= s[1] for h in hits)]
    assert uncovered == [], "a problem branch has no sentinel case"
    joined = " ".join(texts)
    for kind in (
        "the entry's env sets",
        "its argv sets container env",
        "whose project configuration",
        "the child's PATH would look for the launcher inside it",
    ):
        assert kind in joined, kind


def test_every_judge_diagnostic_interpolates_only_allowed_sources() -> None:
    """Round 20, statically: every `{...}` in a string the parser or the judge
    builds is a sanitiser call (closed grammar or the one renderer), a value
    from a closed set, or a text another checked function built. A raw argv,
    requirement, cwd or env value cannot be interpolated without failing."""
    import ast
    import inspect
    import textwrap

    calls = {
        "diag_name", "diag_flag", "diag_selector", "_render_argv", "_safe_token",
        "_may_talk_reason", "_floating_reason",
    }  # fmt: skip
    exprs = {
        "type(exc).__name__", "reading.problem", "verdict.detail", "problems[0]",
        "judged.problems[0]", "reading.family", "_CWD_CONFIG_FAMILIES[reading.family]",
        "self.label", "server_name",
    }  # fmt: skip
    names = {"flag", "which", "rendered", "head", "state", "where"}

    def allowed(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.IfExp):
            return allowed(node.body) and allowed(node.orelse)
        if isinstance(node, ast.JoinedStr):
            return all(
                allowed(v.value)
                for v in node.values
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(node, ast.Call):
            text = ast.unparse(node.func)
            return (
                text in calls
                or text.endswith(".where")
                or (text.endswith(".rstrip") and ".where()" in text)
            )
        if isinstance(node, ast.Name) and node.id in names:
            return True
        return ast.unparse(node) in exprs

    bad = []
    targets = [
        "_read_npx_slot", "_read_npm", "_read_uvx", "_read_cargo",
        "_docker_short_cluster", "_read_docker", "_read_argv", "_member_problems",
        "_verdict_of", "_may_talk_reason", "_unpinned_self_hosted_warning",
        "_unverifiable_warning", "_floating_reason", "_install_members",
    ]  # fmt: skip
    for fn in [*targets, "_Spawn.where"]:
        obj = (
            handlers_module._Spawn.where
            if fn == "_Spawn.where"
            else getattr(handlers_module, fn)
        )
        tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
        for node in ast.walk(tree):
            if isinstance(node, ast.FormattedValue) and not allowed(node.value):
                bad.append((fn, ast.unparse(node.value)))
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in tgts:
                    if (
                        isinstance(t, ast.Name)
                        and t.id in names
                        and not allowed(node.value)
                    ):
                        bad.append((fn, f"{t.id} = {ast.unparse(node.value)}"))
    assert "_operator_safe" not in inspect.getsource(handlers_module)
    assert bad == []


def test_the_interpreter_diagnostic_names_only_the_interpreter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 20 codex B1 repro, plus the loader's pin logs and N1 (`v` prefix)."""
    from pmcp.manifest import loader as loader_module

    reading = handlers_module._read_argv(("uvx", "--from", f"python @ {_U}", "python"))
    assert reading.problem == (
        "uv reads this --from as an interpreter request (python), not a package"
    )
    caplog.set_level(logging.DEBUG)
    loader_module._parse_version_pin("fc", _U, "server_version")
    assert _SENTINEL not in caplog.text
    assert handlers_module._uv_interpreter_request("pythonv3.10") == "python"
    assert handlers_module._uv_interpreter_request("cpythonV3") == "cpython"
```

## Documentation impact

- `README.md`: **modify**. Add a subsection, "Pinning a client version", right after
  the `server_env` / `api_key_optional_when` paragraph (search for
  `FIRECRAWL_API_URL: "http://localhost:3002"`). It covers:
  - the `server_version:` YAML shown in D2;
  - the exact-version-only rule and why ranges and dist-tags are refused;
  - npx only, and the `.mcp.json`/`.pmcp.json` explicit-args escape hatch for
    uvx/cargo/docker;
  - "a whole-entry `servers:` replace in a higher-precedence overlay drops a
    lower-precedence pin";
  - what `pmcp update` prints for a pinned server (`[PINNED] ...`), and for a range or tag (`[FLOATING] ...`);
  - that a new pin reaches a running server only on respawn, so run `gateway.refresh` after adding one;
  - that aliases, URLs, git/file specs and ranges in an entry's slot are refused (the pin could change the package);
  - the `gateway.health` warning for an unpinned self-hosted client;
  - (rev 6) that a docker tag floats and only an `@sha256` digest pins (`[FLOATING]` / `[PINNED]`);
  - (rev 6, narrowed in rev 7) that the warning says it **cannot verify** an exact pin when the server's own config injects a setting that can change what runs, and names the setting;
  - (rev 7) the trust boundary, as the paragraph below.

  The README subsection **must** include this trust-boundary paragraph (verbatim, or
  equivalent):

  > **What the pin warning checks, and what it trusts.** The `gateway.health` warning
  > judges what the server's config controls. It is silent for a self-hosted server
  > only when all of these are **proven** inert; anything it does not recognise makes it
  > say it *cannot verify* the pin:
  >
  > - **Whether it applies.** It applies to a server that talks to a self-hosted backend:
  >   one of its self-hosting variables (for firecrawl, `FIRECRAWL_API_URL`) is set for the
  >   client. pmcp takes those variable names from its own shipped manifest, so an overlay
  >   that drops them does not switch the warning off. It is decided for **every argv that
  >   can start the server** (below), and it applies if any of them applies. For a
  >   `docker run` launched as `docker` itself, the variable must reach the container
  >   (`-e KEY=value`, `-e KEY`, `--env=`, or a short cluster such as `-ie KEY=value`, read
  >   as docker reads it). A URL left in docker's own environment does not count, because
  >   the client never sees it. **Any argv pmcp cannot fully read** (a docker `--env-file`
  >   or unknown flag, `env K=V ...`, `sh -c`, `npx -c`, a wrapper, `podman`, docker by
  >   another path) makes the warning apply, naming that argv, because it may set the
  >   variable for the client. For any other argv pmcp reads fully, the variable counts
  >   when it is in that process's own environment, unless pmcp's checks flag anything
  >   about that argv (an env key not known to be harmless, such as `NODE_OPTIONS` or a
  >   proxy; a `cwd`; a container env key). Then it may set the variable for the client,
  >   and the warning applies, naming the problem. A URL in your project `.env` is
  >   removed from the client's environment, so pmcp does not see it, but firecrawl-mcp's
  >   own dotenv reads it back from the working directory it inherits. A self-hosted URL passed some other way, for example as a
  >   client command-line flag, is not detected.
  > - **The launch shape.** pmcp recognises `npx [-y] pkg@X [args]`, `npm exec`, `uvx
  >   pkg==X [args]` or `uvx --from pkg==X pkg [args]` (also `uv tool run`), `cargo
  >   install crate --version X`, and `docker run [-i -t --rm ...] [-e KEY] image@sha256:...`
  >   with **nothing after the image**. A docker digest pins the *image*, not what runs
  >   in it: a container command, `--entrypoint`, a mount or an env file can run
  >   anything. A docker tag is never a pin.
  > - **The `env` the config injects** (for example an overlay's `server_env`). Only
  >   the keys pmcp's **shipped** manifest declares for that server (its credential and
  >   self-hosting variables; a declaration in your overlay or `.pmcp.json` does not
  >   count), locale/terminal keys, and a few npm/uv/cargo logging, timing and
  >   credential keys are allowed. Anything else, including keys pmcp does not know, is
  >   "cannot verify".
  > - **A `cwd` the config sets**, for an npx, uvx or cargo client: it picks which
  >   project's `.npmrc`, `uv.toml` or `.cargo/config.toml` applies.
  > - **Every argv that can start the server.** For a manifest server that means its
  >   `args` *and* each platform's `install` argv, which `gateway.provision` runs and keeps
  >   as the live server; each must run the same exact pin in a recognised shape.
  > - **The launcher.** `bunx`, `pnpx`, `pnpm dlx`, `yarn dlx`, `uv run`, pip, `node`,
  >   shells and `env` wrappers are "cannot verify", pinned or not. A launcher is the
  >   launcher only when it is spelled as itself: the bare name (`npx`; on Windows also
  >   `npx.cmd`/`.exe`), or the exact absolute path the client's `PATH` resolves that
  >   name to (compared as written, never resolved through links). `/tmp/x/npx`,
  >   `./npx`, `../bin/docker`, `/proc/self/cwd/...`, `node_modules/.bin/npx` or `NPX` are
  >   "cannot verify". A docker client with a `cwd` set in its config must be launched as
  >   bare `docker`, through a `PATH` that cannot look inside that directory (no relative,
  >   empty or `/proc` entries); otherwise it is "cannot verify". On Windows, where npx and
  >   npm are `.cmd` files run through `cmd.exe`, an argument containing a `cmd.exe`
  >   metacharacter (`& | < > ^ % ! " ( )` or a space) is "cannot verify". On Windows
  >   `which` spells the extension as `PATHEXT` does (`npx.CMD`), so an absolute launcher
  >   path matches only when written exactly that way; launch with bare `npx` there. A locally installed server binary has no version for pmcp to check, so the
  >   pin requirement is waived, but only when **pmcp's own shipped manifest names that
  >   exact command for the server**, only when it is the *only* thing that can run as
  >   the server, and its env and `cwd` are still judged as above. Any other command
  >   (a wrapper such as `timeout`, `nice`, `sudo`, `busybox`, a version manager such as
  >   `mise` or `volta`, `corepack`, `pipx`, `python -m ...`, `go run`, or a custom
  >   binary in your own config) is "cannot verify", named in the warning.
  >   If the entry's `install` argv is something else, `gateway.provision` would run that
  >   instead, so the warning says it cannot verify.
  > - **pmcp's reading of the argv.** pmcp reads each launcher's options the way that
  >   launcher does. An option that takes one value and is given twice (`uvx --from a
  >   --from b`, `cargo --version` twice) is "cannot verify" rather than guessed. Argvs
  >   shown in these warnings and in the pinned report are redacted, so a `--token` value
  >   never appears; executable names, env key names and paths are shown escaped.
  >
  > It trusts your machine: npmrc files at any level, your shell environment,
  > version-manager shims (asdf, Volta, mise), the npx cache and global bin, proxy/CA
  > settings, and uv, cargo and docker configuration are yours to control, and pmcp does
  > not inspect them. A pin holds the top-level package; its dependencies still resolve
  > from their ranges, and an injected `npm_config_@<scope>:registry` for a
  > *dependency's* scope is allowed and not reported. A uv `==X` pin is a *version*,
  > not an artifact: PEP 440 lets it match a local version `X+tag`, or a zero-padded
  > equal release (`X.0`), on a non-PyPI index (measured with uv 0.12.19; never a post
  > release, another epoch or a later version), and PyPI lets new files be added to an
  > existing release. `uvx python==3.10` (or `cpython`, `pypy`, `graalpy`, `pyodide`, any
  > case, with a version) is not a package pin at all: uv runs an interpreter of that
  > series, so pmcp says it cannot verify it. Both depend on your index
  > or on PyPI, which pmcp trusts. An
  > overlay-only self-hosted server (one pmcp does not ship) always warns "cannot verify":
  > its own variables are not declared by the shipped manifest.

  In the gateway-tools table row for `gateway.update_server`, mention that a pinned
  server is reported with the newer version that is available. The existing README
  sentence that `.mcp.json` pinning is "the supported override channel" stays true.
  Add "or `server_version` for a manifest server" to it.
- `CHANGELOG.md`: **modify**. Under `## [Unreleased]`, add an `### Added` entry in the
  house style (bold lead sentence, then detail, then
  `See [#294](https://github.com/Consiliency/pmcp/issues/294).`). Suggested text:

  > - **Pin a built-in server's client version with one overlay line, and see when
  >   a newer one exists.** A manifest entry takes `version: "X.Y.Z"`, and an overlay's
  >   new `server_version:` map sets it on a shipped entry without restating its install
  >   matrix. The pin is written into the npx package slot of `args` and of every
  >   `install` argv, keeping the entry's own package name, so an overlay can choose a
  >   version and never a different package. Only one exact SemVer version is accepted:
  >   ranges and dist-tags such as `latest` re-resolve at every spawn and are refused.
  >   uvx/pip/cargo/docker and remote entries are refused with a message pointing at
  >   `.mcp.json` explicit args. `pmcp update` prints a pinned server as `[PINNED]
  >   firecrawl: pinned at 3.25.5, newer available: 3.26.0` instead of `[FAILED]`, and
  >   `gateway.update_server` returns `pinned_version`, `latest_available` and
  >   `latest_comparison`. `gateway.health` and `gateway.update_server` now also carry a
  >   `warnings` list, which warns when an entry's `api_key_optional_when` relaxer is
  >   active (a self-hosted backend is configured) and its client is unpinned. See
  >   [#294](https://github.com/Consiliency/pmcp/issues/294).
- `CONTRIBUTING.md`: **modify**. In the manifest-entry example (the
  `api_key_optional_when` comment block around line 70), add one commented line:
  `# Optional: version: "1.2.3"   # exact client pin; npx entries only (see README)`.
- `src/pmcp/manifest/manifest.yaml`: **no change**. No shipped entry is pinned by this
  plan. Shipping a pin for firecrawl would pin every vendor-hosted user too. See open
  question Q1.
- No `docs/**`, `llms*.txt`, `SECURITY.md` or openapi footprint: the new fields are
  optional output fields, and no input schema changes.

## Dependencies & order

1. `types.py` fields first. `handlers.py` constructs them, and without them `mypy src/`
   fails.
2. `loader.py`: grammar, materialiser and overlay map. This is independent of handlers.
3. `handlers.py`: the warning helper, health, the update wrapper and the pinned branch.
4. `cli.py`: the renderer.
5. `tests/test_version_pin.py`, then the docs.

External blockers: none. There is no migration, and the new fields are additive and
optional.

## Follow-up slices (sequenced after this plan; design notes only, not planned)

### Slice 3: optional post-update smoke probe (proposal 3)

- **Manifest shape.** An optional
  `smoke_probe: {tool: "firecrawl_search", arguments: {query: "example domain", limit: 1}}`
  on an entry, which an overlay can set via a `server_smoke_probe:` patch map. It follows
  the same "patch an existing server, cannot create" rule as `server_env`/`server_version`.
- **When it runs.** Only after `gateway.update_server` has actually restarted the server
  onto a new version (`restart_result.ok`), and never on a pinned no-op.
- **What counts as a failure (from the 2026-09-26 measurement).** The self-hosted search
  returned an empty `success:true` in **1 of 6** identical requests with no client
  involved. So the probe must assert only "the call did not return a JSON-RPC error, an
  `isError: true` tool result, or an HTTP 4xx/5xx in its text". It must **never** assert
  that results are non-empty. On failure it retries once, and only a second failure is
  attributed to the version change.
- **Report.** The message names the previous and the new client versions. The previous
  version comes from what the update resolved before the probe. For a manifest npm
  entry, a failure message can suggest the exact
  `server_version: {name: <previous>}` line, because this plan makes that a one-line
  rollback. Automatic rollback is out of scope: it would rewrite the operator's overlay
  file.

### Slice 4: client version in error hints (proposal 4)

- **What exists, measured.** There is none. `grep -rn "installed_version\|resolve_spawned_version" src/pmcp` finds nothing. The
  spawn-time-provenance plan
  (`.consiliency/plans/detailed-spawn-time-version-provenance-20260817-1800.md`) was
  not implemented: automatic update notices were removed instead (Consiliency/pmcp#150,
  quoted at `handlers.py` in `update_server`: "pmcp cannot observe which artifact a
  running server executes").
- **What this plan makes possible.** For a **pinned** npx argv, the argv itself names
  the version that ran. No inference is needed, because `_detect_effective_version_pin`
  on the spawned config is the answer. Slice 4 can therefore record, at connect time on
  `ManagedClient`, the `(package, pinned_version | "unpinned")` pair read from the argv
  it spawned. When a downstream tool result is a 4xx/schema-validation error (the
  `400 Invalid request body` class), it adds `client firecrawl-mcp@3.25.5 (pinned)` or
  `client firecrawl-mcp (unpinned: whatever npx resolved at spawn)` to
  `gateway.invoke`'s `errors`/`feedback_hint`. Naming an *unpinned* client's exact
  version is the #150 problem again and stays out of scope. The hint says so and names
  the `server_version` remedy.

## Verification

**Prerequisites** (memory: `worktree-needs-uv-sync-p310`):

- Run `uv sync -p 3.10 --all-extras` in the worktree. A bare `uv sync` leaves out
  ruff, mypy and pytest-timeout, and resolves to the system Python.
- Run **`unset npm_config_cache npm_config_store_dir pnpm_config_store_dir` in the same
  shell command** as every pytest invocation. With them set, the npm resolver is
  DISABLED by design, and every npm server reads as `unknown`. The pin tests would then
  go red for the wrong reason.

All steps below were run this session against the spike (the diff and test file above),
on Python 3.10.12 via `uv run`. The expected results are the measured ones.

```bash
cd "$WORKTREE"   # a fresh worktree off origin/main, with the diff + test file applied

# 0. The new tests exist.
uv run pytest tests/test_version_pin.py --collect-only -q --cov-fail-under=0 | tail -1
#   -> 493 tests collected   (rev 22; rev 21: 490; rev 20: 479; rev 19: 461; rev 18: 449; rev 17: 435; rev 16: 429; rev 15: 407; rev 14: 388; rev 13: 368; rev 12: 358; rev 11: 263; rev 10: 236; rev 9: 230; rev 8: 219; rev 7: 190; first rev-7 cut: 186; rev 6: 153; rev 5: 112)

# 1. Red on HEAD (before the diff; the test file alone).
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 --tb=line | tail -1
#   -> ImportError: cannot import name 'manifest_sources_fingerprint' from 'pmcp.manifest.loader' -- 1 error during collection
#      (rev 3: the file imports manifest_sources_fingerprint and split_plain_registry_spec,
#      which HEAD lacks, so collection itself is red; the per-test red evidence is the
#      rev-1/rev-2 spike runs below and in the Revision tables)
#   rev 2 was: 56 failed, 1 passed   (the 1 is test_explicit_config_args_win_over_the_manifest_pin,
#      an inertness guard that is green on HEAD by design)
#   Against the REVISION-1 spike: 19 failed, 38 passed; exactly the 19 new rev-2 cases
#   (Revision 2 table).
#   Rev 22: against the REVISION-21 code (a49dc28): 28 failed, 465 passed (3 new tests + 25 deliberate wording changes).
#   Rev 21: against the REVISION-20 code (23e1e44): 8 failed, 482 passed.
#   Rev 20: against the REVISION-19 code (a9e53b8): 14 failed, 465 passed.
#   Rev 19: against the REVISION-18 code (98b367e): 9 failed, 452 passed.
#   Rev 18: against the REVISION-17 code (9d0a73f): 9 failed, 440 passed.
#   Rev 17: against the REVISION-16 code (56dfca4): 4 failed, 431 passed.
#   Rev 16: against the REVISION-15 code (6086712): 20 failed, 409 passed.
#   Rev 15: against the REVISION-14 code (78b21f4): 15 failed, 392 passed.
#   Rev 14: against the REVISION-13 code (03edb0a): 7 failed, 381 passed.
#   Rev 13: against the REVISION-12 code (791380c): 16 failed, 352 passed.
#   Rev 12: against the REVISION-11 code (ba58ade): 95 failed, 263 passed.
#   Rev 11: against the REVISION-10 code (0a63441): 34 failed, 229 passed.
#   Rev 10: against the REVISION-9 code (bdc13ca): 5 failed, 231 passed.
#   Rev 9: against the REVISION-8 code (253445a): 11 failed, 219 passed.
#   Rev 8: against the REVISION-7 code (15dae94): 24 failed, 195 passed.
#   Rev 7 (cwd ruling): against the FIRST rev-7 cut (1073923): 3 failed, 187 passed.
#   Rev 7: the first rev-7 file against the REVISION-6 code: 54 failed, 132 passed (Rev 6
#   board findings section).
#   Rev 6: the revision-6 file against the REVISION-5 code: 41 failed, 112 passed (the 39
#   new red cases plus the two changed NB-2 texts; Revision 6 section).
#   Rev 5 on 959d4d4 WITHOUT the patch: collection ImportError; with a two-symbol import
#   shim, 111 failed, 1 passed (test_explicit_config_args_win_over_the_manifest_pin).
#   Against the REVISION-3 spike: 18 failed, 89 passed -- the 18 rev-4 cases red, and
#   every rev-3 test green on the buggy code (Revision 4 table).
#   Against the REVISION-2 spike: 15 failed, 74 passed (Revision 3 table; measured with a
#   one-line import shim, because rev 2 has no public split_plain_registry_spec).

# 2. Green with the diff.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 | tail -1
#   -> 493 passed   (rev 22; rev 21: 490; rev 20: 479; rev 19: 461; rev 18: 449; rev 17: 435; rev 16: 429; rev 15: 407; rev 14: 388; rev 13: 368; rev 12: 358; rev 11: 263; rev 10: 236; rev 9: 230; rev 8: 219; rev 7: 190; first rev-7 cut: 186; rev 6: 153; rev 5: 112)

# 3. CI gates (all three are in .github/workflows).
uv run ruff check src/ tests/                 # -> All checks passed!
uv run ruff format --check src/ tests/        # -> 171 files already formatted   (rev 15 on 7680445; 166 on 4d2790f and from rev 7 on 876fd33; 164 on 959d4d4)
uv run mypy src/                              # -> Success: no issues found in 52 source files   (rev 15 on 7680445; 51 on 4d2790f and from rev 7 on 876fd33; 50 on 959d4d4)

# 4. The neighbouring suites that own the touched contracts.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py tests/test_manifest_overlay.py \
    tests/test_project_source_consent_manifest.py tests/test_pkgid_panel_fixes.py \
    tests/test_package_identity_gate.py tests/test_credential_gates_handlers.py \
    tests/test_credential_gates_startup.py tests/test_credential_optionality_e2e.py \
    tests/test_manifest_provision.py tests/test_version_checker.py \
    tests/test_credential_predicate_guard.py tests/test_package_identity.py \
    tests/test_policy_package_identifiers.py tests/test_package_approvals.py \
    tests/test_gateway_tool_schemas.py -q --cov-fail-under=0 | tail -1
#   -> 2048 passed, 19 deselected   (rev 7; rev 6: 2015; rev 5: 1974, on 959d4d4, adding #299's and #300's test files; rev 4: 1686, which ALSO runs tests/test_package_identity.py and
#      tests/test_policy_package_identifiers.py -- the other callers of is_valid_package_version;
#      rev 3: 1620; rev 2: 1588)

# 5. Whole suite: Bash run_in_background, wait for the notification. Do not detach with
#    nohup/disown. Use a lane-unique log path.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/ -q --cov-fail-under=0 -p no:cacheprovider -m 'not live and not slow' > "$LOGDIR/294-suite.log" 2>&1
#   -> 5200 passed, 3 skipped, 80 deselected in 762.82s (0:12:42)   (rev 22 on origin/main 7680445, with -x --timeout=600, after the mutants: exit 0; rev 21: 5197 passed, 3 skipped, 80 deselected in 1043.84s (0:17:23) on origin/main 7680445, with -x --timeout=600, after the mutants: exit 0; rev 20: 5186 passed, 3 skipped, 80 deselected in 713.43s (0:11:53) on origin/main 7680445, with -x --timeout=600, after the mutants: exit 0; rev 19: 5168 passed, 3 skipped, 80 deselected in 575.23s (0:09:35) on origin/main 7680445, with -x --timeout=600, after the mutants: exit 0; rev 18: 5156 passed, 3 skipped, 80 deselected in 588.49s (0:09:48) on origin/main 7680445, with -x --timeout=600, run after the mutants, not concurrently: exit 0; rev 17: 5142 passed, 3 skipped, 80 deselected in 567.08s (0:09:27) on origin/main 7680445, run with -x --timeout=600; a first -x run under mutant load stopped on main's test_monitor_reads_stderr, see the Revision 17 section; rev 16: 5136 passed, 3 skipped, 80 deselected in 778.11s (0:12:58) on origin/main 7680445, run concurrently with the mutants; rev 15: 5114 passed, 3 skipped, 80 deselected in 543.96s on origin/main 7680445 with the 407-test file; 7680445 adds Consiliency/pmcp#310's tests; rev 15 on 4d2790f, first 405-test cut: 4908 passed, 3 skipped, 25 deselected in 522.35s, run concurrently with the mutants; rev 14: 4891 passed, 3 skipped, 25 deselected in 491.31s (0:08:11) on origin/main 260cc1a, run concurrently with the mutants; rev 13: 4871 passed in 532.86s; rev 12: 4861 passed in 508.65s; rev 11: 4766 passed in 515.95s; rev 10: 4739 passed in 483.40s; on 876fd33: 4589 passed in 643.93s, run concurrently with the mutants; rev 9: 4583 passed in 464.14s; rev 8: 4572 passed in 729.81s, run concurrently with the mutants; rev 7 incl. the cwd ruling on 876fd33: 4543 passed in 457.95s; first rev-7 cut on 876fd33: 4539 passed in 453.66s; on 959d4d4: 4522 passed in 453.77s; marker `-m 'not live and not slow'`; rev 6: 4489 passed in 456.55s; rev 5: 4448 passed in 443.28s; board revision 4: 4178 passed in 427.75s; revision 3: 4160 passed in 442.33s; revision 2: 4128 passed, 3 skipped, 25 deselected in 422.12s; earlier: 4108 passed; the first spike was 1 failed / 4107 passed -- see the mutation-table note;
#      revision 3 adds only the offline stub in test_pkgid_panel_fixes.py, re-measured by step 5b)

# 5b. Hermeticity: no update_server/health test may reach a real registry. The plugin
#     makes every registry lookup raise; run it over the files that drive either tool.
mkdir -p "$LOGDIR/plug" && cat > "$LOGDIR/plug/nonet.py" <<'PY'
import pmcp.manifest.version_checker as vc
async def _boom(*a, **k):
    raise RuntimeError("NETWORK-LOOKUP")
for n in ("get_npm_version", "get_pypi_version", "get_cargo_version", "get_docker_version"):
    setattr(vc, n, _boom)
PY
F=$(grep -rln --include='*.py' "update_server(\|\.health()\|gateway.health" tests/ | grep -v harness.py | sort | tr '\n' ' ')
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  PYTHONPATH="$LOGDIR/plug" uv run pytest ${=F} -q --cov-fail-under=0 -p nonet -p no:cacheprovider | tail -1
#   (zsh: ${=F} word-splits; in bash use $F)
#   -> 1227 passed, 1 deselected over 20 files (rev 7; rev 6: 1194; rev 5: 1153 on 959d4d4; board revision 4: 928; 910 in revision 3, 878 in revision 2, 858 in revision 1, measured with the stub). The spike WITHOUT the panel-fixes stub gave
#      "1 failed, 857 passed, 1 deselected" (the one offender named above); with the stub,
#      the offender and tests/test_version_pin.py pass under the plugin (84 passed for those
#      two files). On HEAD, the 6-file update_server subset gives 357 passed, 0 lookups.

# 6. The pre-existing pinned-refusal assertion still holds (substring kept; now stubbed offline).
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest "tests/test_pkgid_panel_fixes.py::test_update_server_still_refuses_a_pinned_manifest_server_as_before" -q --cov-fail-under=0 | tail -1
#   -> 1 passed

# 7. Coverage of the shipped manifest (read-only; shows which built-ins can be pinned).
uv run python - <<'EOF'
from dataclasses import replace
from pathlib import Path
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
m = load_manifest(Path("src/pmcp/manifest/manifest.yaml"))
res = {n: _materialize_version_pin(replace(s, version="1.0.0")).version for n, s in m.servers.items()}
print(sum(v is not None for v in res.values()), "pinnable,", sum(v is None for v in res.values()), "refused")
assert res["firecrawl"] == "1.0.0"
EOF
#   -> 77 pinnable, 30 refused   (re-measured rev 22: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; rev 21: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; 0 of 510 shipped argvs read as a uv interpreter request; rev 20: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; 0 of 510 shipped argvs read as a uv interpreter request; rev 19: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; firecrawl as shipped with no relaxer `None`; operator_cost19.py: 13 of 17 vendor-hosted setups warn (rev 18: 10); rev 18: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; operator_cost.py: firecrawl as shipped with no relaxer `None`, a configured `sh -c`/wrapper firecrawl with no relaxer now `may talk`; rev 17: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; rev 16: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; rev 15: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; rev 14: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; rev 13: shipped_cost.py 0 of 77, 97 shipped entries `unpinned`; rev 12: shipped_cost.py with the shipped local commands, 0 of 77 on rev 11 and rev 12, 97 shipped entries `unpinned` on both; rev 11: shipped_cost.py over _judge_spawn_set, 0 of 77 not silent once pinned; all 97 local shipped entries read as `unpinned` unpinned; rev 10: shipped_cost.py walks _spawn_set, 0 of 77, and 0 local-command entries with a differing install; rev 9: shipped_cost.py incl. install argvs and shipped-only declarations, 0 of 77 on rev 9 and on rev 8; rev 8: shipped_cost.py: 0 of 77 exact pins not silent on rev 8 and on rev 7; rev 7 after the cwd ruling, rev 7 and rev 6; 19 uvx, 9 remote/empty command, cloudflare (url), context7 (windows `cmd /c npx`))

# 8. Mutation table below: each mutant applied to the implemented tree, then
#    `uv run pytest tests/test_version_pin.py -q --tb=line`, then restored and `cmp`-verified.

# 9. Plan-only commit check (for THIS planning commit, not the implementation PR).
git diff --stat 9ca081e -- src/ tests/          # -> empty
uv run python ~/code/pmcp/scripts/check_plan_consistency.py .consiliency/plans/detailed-294-version-pin-*.md
#   -> lane-contracted: 0   EC-proved node ids: 0 / consistent / blocking inconsistencies: 0
#      (vacuous for a detailed plan: it has no lane table or EC ids; run for the record)
```

```bash
# 10. GENERATED conformance against npm's own classifier, run ONCE PER npm-package-arg
#     release (rev 5: 12.0.2 = npm 10, 13.0.2 = npm 11; needs node; not a CI gate).
#     The generator is adapted from the board-round-3 seat's; both files are below.
#     Rev 5 adds core parts at and past 2**53-1 and the npm-10 `tar-gz`/`tarXgz` suffixes.
for NPA in <npm10-root>/node_modules/npm-package-arg <npm11-root>/node_modules/npm-package-arg; do
  NPA=$NPA uv run python corpus_conformance.py <dir-with-npa_classify.js>   # exits nonzero on any violation
done
#   npa 12.0.2 -> SLOT corpus: 1617455 slots; accepted 14651 {'range': 25, 'tag': 14250, 'version': 376}
#                 refused 1602804; SLOT VIOLATIONS: 0
#                 PIN-VALUE corpus: 41472 values; accepted 12; PIN VIOLATIONS: 0          exit 0
#   npa 13.0.2 -> SLOT corpus: 1617455 slots; accepted 14651 {'range': 25, 'tag': 14250, 'version': 376}
#                 refused 1602804; SLOT VIOLATIONS: 0
#                 PIN-VALUE corpus: 41472 values; accepted 12; PIN VIOLATIONS: 0          exit 0
#   (The per-class refused counts differ between the two npa releases, because npa
#    classifies refused slots differently; the accepted set and both violation counts do not.)
#   The REVISION-4 rule (its own tarball regex, no 2**53-1 bound) on the same corpus:
#     npa 12.0.2: SLOT VIOLATIONS 4525 (4475 `file`, all `tar-gz`-shaped; 50 invalid), PIN 13
#     npa 13.0.2: SLOT VIOLATIONS 50 (EINVALIDTAGNAME: oversized core + build), PIN 10
#   Main's rule (#299) closes both; the pin grammar inherits it.
#   ~25 s per run (one node process classifies all slots).
#   Rev 6, rev 7 and rev 8 re-ran both (loader.py is unchanged from rev 5): identical lines, exit 0 on both.

# 11. Embedding proof (rev 6). Extract the reference patch (the first ```diff block after
#     "### Production diff") and the test file (the first ```python block after "## Test
#     bodies") from THIS file, then:
git -C <clone at origin/main 959d4d4> apply --check <extracted.patch>   # -> clean
cmp <extracted.patch> <(git -C <spike> diff -- src/ tests/test_pkgid_panel_fixes.py)   # -> identical
cmp <extracted test file> <spike>/tests/test_version_pin.py              # -> identical
#   Measured rev 22: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main; gates, 493 pin tests and the full suite pass there.
#   Measured rev 21: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main; gates, 490 pin tests and the full suite pass there.
#   Measured rev 20: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main; gates, 479 pin tests and the full suite pass there.
#   Measured rev 19: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main; gates, 461 pin tests and the full suite pass there.
#   Measured rev 18: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main; gates, 449 pin tests and the full suite pass there.
#   Measured rev 17: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main; gates, 435 pin tests and the full suite pass there.
#   Measured rev 16: extracted patch and test file `cmp`-identical to the spike (7680445); `git apply
#   --check` clean on the re-fetched origin/main 7680445; gates, 429 pin tests and the full suite pass there.
#   Measured rev 15: extracted patch and test file `cmp`-identical to the spike (4d2790f); `git apply
#   --check` clean on the re-fetched origin/main 7680445, whose applied `git diff` is byte-identical to
#   the patch; gates, 407 pin tests and the full suite pass there.
#   Measured rev 10: `git apply --check` clean on the re-fetched origin/main 260cc1a; applied there,
#   its `git diff` is byte-identical to the patch, and gates, 236 pin tests and the full suite pass.
#   Measured rev 7: all three clean on 959d4d4, and `git apply --check` clean on the re-fetched
#   origin/main 876fd33, where the applied tree's `git diff` is byte-identical to the patch.

# 12. Board-round-4 before/after (rev 6): repro_c1.py / repro_c2.py (appendix) against a
#     959d4d4 + rev-5 tree and a 959d4d4 + rev-6 tree; results in the
#     "Rev 5 board findings -- before/after, measured" section.
```

`npa_classify.js`:

```javascript
// Classify each slot with npm's own npm-package-arg: [type, name, registry, fetchSpec].
// NPA must point at the host npm's node_modules/npm-package-arg.
const npa = require(process.env.NPA);
const slots = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = [];
for (const s of slots) {
  try {
    const r = npa(s, "/tmp");
    out.push([r.type, r.name || null, !!r.registry, r.fetchSpec == null ? null : String(r.fetchSpec)]);
  } catch (e) {
    out.push(["ERROR:" + (e.code || e.message.slice(0, 40)), null, false, null]);
  }
}
console.log(JSON.stringify(out));
```

`corpus_conformance.py`:

```python
"""Generated corpus vs npm's real classifier (npm-package-arg).

Adapted from the board-round-3 claude seat's generator (Consiliency/pmcp#295).
Run it once per npm-package-arg release (NPA=<path>); rev 5 runs 12.0.2 (npm 10,
unescaped `tar.gz` in isFileType) and 13.0.2 (npm 11). Includes core parts at and
past 2**53-1, which node-semver refuses (npa then reads a dist-tag).
names x (prefixes x cores x suffixes) sampling npa's grammar -- every spec class,
versions with prerelease/build/tarball tails, case and unicode variants, any run
of leading v/=, reserved names, whitespace, fragments. Two checks, both must be 0:

1. SLOT: every slot split_plain_registry_spec accepts is fetched by npm from the
   registry as the SAME name, and is npa `version` or `tag` (or a bare name,
   npa range `*`). A range with a selector counts as a violation too.
2. PIN VALUE: every value _parse_version_pin accepts, placed as `x@<value>`, is npa
   `version` from the registry with fetchSpec == value (so the pin names exactly
   that registry version).
"""
import itertools, json, os, subprocess, sys
from collections import Counter
from pmcp.manifest.loader import _parse_version_pin, split_plain_registry_spec

here = sys.argv[1]
names = ["firecrawl-mcp", "t", "x", "v1", "latest", "Foo", "a.b", "a_b", "0x",
         "corp.tgz", "corp.TGZ", "x.tar", "a.TAR.GZ", "a.tar.gzz", "atgz", "a.tgz.x",
         "@s/p", "@S/P", "@s/p.tgz", "@s.tgz/p", "@s/p.tar.gz",
         "node_modules", "Node_Modules", "favicon.ico", "FAVICON.ICO", "http", "a..b",
         "ａ", "é", "a b", "-y", "_x", ".x", "github", "npm", "file", "git", "C", "c"]
cores = ["", "1", "1.2", "1.2.3", "3.25.5", "01.2.3", "1.2.3.4",
         "9007199254740991.0.0", "9007199254740992.0.0", "1.9007199254740992.0", "x", "X", "*", "x.x", "1.x",
         "1.X.x", "latest", "next", "beta", "rc", "a", "tgz", "tar", "corp", "corp-mcp", "v",
         "vnext", "vx", "Ｘ", "ｘ", "вeta", "é", "ß", "İ", "K", "ﬁ"]
pre = ["", "v", "V", "vv", "vvv", "=", "=v", "v=", "~", "^", ">=", "<", "npm:", "file:", "./",
       "../", "~/", "/", "C:", "c:", "github:", "gitlab:", "git+", "git+https://", "https://",
       "http://", "git@h.com:", "a/", " ", "\t"]
suf = ["", "-rc", "-rc.1", "-corp.tgz", "-x.TAR", "-a.tar.gz", "-X.Tar.Gz", "+b", "+b.tgz",
       "+b.tar.gz", "-x.tar-gz", ".tar-gz", ".tarXgz", ".tgz", ".TGZ", ".Tgz", ".tar", ".tar.gz", ".TAR.GZ", ".tar.gzip", ".tgzx",
       "beta", "-beta", "xbeta", ".xbeta", "#frag", ".tgz#frag", "#semver:1", "/x", "\n", " ",
       ":x", ".git", "@1", "%20", "!", "(1)", "\u200b", "İ"]
sels = sorted({p + c + s for p, c, s in itertools.product(pre, cores, suf)})
slots = set()
for n in names:
    slots.add(n)
    for sel in sels:
        slots.add(f"{n}@{sel}")
slots |= {"x@", "@@x", "@s/p@", "x@@1", "x@1@2", "x@npm:y@1", "y@npm:x", "x@1.2.3 ",
          " x@1.2.3", "x@1.2.3\n", "x@\n", "X@1.0.0", "x@1.0.0-ｘ.tgz", "x@1.0.0-x.tgz\n"}
slots = sorted(slots)
pins = sels  # every generated selector is also tried as a pin VALUE

env = dict(os.environ, NPA=os.environ["NPA"])
def classify(items):
    out = subprocess.run(["node", f"{here}/npa_classify.js"], input=json.dumps(items),
                         capture_output=True, text=True, check=True, env=env).stdout
    return json.loads(out)

res = classify(slots)
acc, ref, bad = Counter(), Counter(), []
for s, (t, nm, reg, _fetch) in zip(slots, res):
    ours = split_plain_registry_spec(s)
    if ours is None:
        ref[t] += 1
        continue
    acc[t] += 1
    ok = reg and nm == ours[0] and (t in ("version", "tag") or (t == "range" and ours[1] is None))
    if not ok:
        bad.append((s, t, nm, ours))
print(f"SLOT corpus: {len(slots)} slots; accepted {sum(acc.values())} {dict(sorted(acc.items()))}")
print(f"  refused {sum(ref.values())} by npa class {dict(sorted(ref.items()))}")
print(f"  SLOT VIOLATIONS (accepted, not a same-name registry version/tag/bare name): {len(bad)}")
for b in bad[:40]:
    print("    ", repr(b))

import logging
logging.disable(logging.WARNING)
accepted_pins = [v for v in pins if _parse_version_pin("x", v, "server_version") is not None]
pres = classify([f"x@{v}" for v in accepted_pins])
pbad = [(v, r) for v, r in zip(accepted_pins, pres) if not (r[0] == "version" and r[2] and r[3] == v)]
print(f"PIN-VALUE corpus: {len(pins)} values; accepted {len(accepted_pins)}; "
      f"PIN VIOLATIONS (accepted, not npa registry `version` == value): {len(pbad)}")
for b in pbad[:40]:
    print("    ", repr(b))
sys.exit(1 if bad or pbad else 0)
```

### Live check (optional, network, not a gate)

The operator can confirm the report end to end against the real registry:

```bash
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; uv run python - <<'EOF'
import asyncio
from pmcp.manifest.version_checker import get_package_version, compare_versions
v, t = asyncio.run(get_package_version("npx", ["-y", "firecrawl-mcp@3.25.4"], None, None, timeout=10))
print(v, t, compare_versions("3.25.4", v, t))
EOF
#   measured 2026-09-26: 3.25.5 npm newer
```

### Mutation table

Each mutant was applied to the spike with an exact one-occurrence string replace, then
confirmed by `diff` against the saved spike copy (the `diff` hunk header names the mutated
line). `tests/test_version_pin.py` was run with `--tb=line`, and the file was restored and
checked with `cmp` (`restored=True` for every row). The driver script is
`mutants.py`, embedded at the end of this plan.

| # | mutation | applied (file:diff hunk) | result | first `E` line (truncated at 160) / failing tests |
|---|---|---|---|---|
| M1 | grammar accepts any string | `loader.py:616c616` | **20 failed, 473 passed** (restored=True) | `AssertionError: assert '^3.25.5' is None`; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_server_version_refuses_anything_but_one_exact_version["*"]`, `test_server_version_refuses_anything_but_one_exact_version["../../tmp/x"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-a.tar.gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.TAR"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.tar-gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.2.3-X.Tar.Gz"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5 --registry=http://evil.test"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5-evil.tgz"]` (+10 more) |
| M2 | install argv not pinned | `loader.py:780c780` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert {'mac': ['npx...recrawl-mcp']} == {'mac': ['npx...-mcp@3.25.5']}`; `test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns`, `test_version_key_on_a_whole_servers_entry_is_materialised`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M3 | existing tag not replaced | `loader.py:725c725` | **7 failed, 486 passed** (restored=True) | `AssertionError: assert ['-y', '@play...latest@1.2.3'] == ['-y', '@play...ht/mcp@1.2.3']`; `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-rc.1-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@beta-2.tgzx-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@next-t@3.25.5]`, `test_version_replaces_a_dist_tag_slot`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M4 | servers: version: key ignored | `loader.py:900c900` | **52 failed, 441 passed** (restored=True) | `AssertionError: assert ['-y', 'custo...port', '3000'] == ['-y', 'custo...port', '3000']`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: "npx"\n    args: ["-y", "ok-mcp"]\n    install:\n      linux: ["npx", "-y", 123]]`, `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: "npx"\n    args: ["-y", 123]]`, `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]`, `test_version_key_on_a_whole_servers_entry_is_materialised`, `test_version_pins_every_plain_registry_class[@s/p-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[@s/p.tgz-@s/p.tgz@3.25.5]`, `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-rc.1-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-t@3.25.5]` (+42 more) |
| M5 | unapproved project overlay applied | `loader.py:1175c1175` | **2 failed, 491 passed** (restored=True) | `AssertionError: assert '3.25.5' is None`; `test_fingerprint_changes_when_the_project_overlay_is_approved`, `test_unapproved_project_server_version_contributes_nothing` |
| M6 | non-npx command accepted | `loader.py:753c753` | **2 failed, 491 passed** (restored=True) | `assert False`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]`, `test_version_on_a_uvx_server_is_refused_with_the_escape_hatch` |
| M7 | install may name another package | `loader.py:775c775` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert '1.0.0' is None`; `test_version_is_refused_when_an_install_argv_names_another_package` |
| M8 | comparison arguments swapped | `handlers.py:6725c6725` | **2 failed, 491 passed** (restored=True) | `AssertionError: assert 'not_newer' == 'newer'`; `test_update_server_labels_build_metadata_with_what_npm_runs`, `test_update_server_reports_a_newer_version_for_a_pinned_server` |
| M9 | relaxer not required for the warning | `handlers.py:1950c1950` | **28 failed, 465 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[declared-entry-url-no-e-False]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-url-no-e-False]`, `test_a_clean_docker_run_without_the_url_in_the_container_stays_quiet`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g2-unpinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_a_fully_read_member_without_the_relaxer_stays_quiet[docker-latest]`, `test_a_fully_read_member_without_the_relaxer_stays_quiet[npx-unpinned]` (+18 more) |
| M11 | health judges the manifest, not the connected config | `handlers.py:3902c3902` | **42 failed, 451 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-dot-path-planted-cwd-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-url-no-e-False]`, `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g2-unpinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]` (+32 more) |
| M12 | update_server drops the warning | `handlers.py:6549,6550d6548` | **6 failed, 487 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[drops-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required-no-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[older-copy]`, `test_update_server_carries_the_unpinned_self_hosted_warning`, `test_update_server_warns_about_a_docker_args_member_with_an_npx_install` |
| M13 | CLI keys the status off ok | `cli.py:1033c1033` | **2 failed, 491 passed** (restored=True) | `assert False`; `test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed`, `test_update_server_reports_a_docker_tag_as_floating` |
| M14 | health never attaches warnings | `handlers.py:3819d3818` | **306 failed, 187 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-dot-path-planted-cwd-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[ampersand]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[percent]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[pipe]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[space]`, `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_failure_while_deciding_whether_the_warning_applies_is_loud` (+296 more) |
| M15 | P1: any selector accepted (alias/url/git/file/dir/range) | `loader.py:699c699` | **22 failed, 471 passed** (restored=True) | `AssertionError: assert ('myalias', 'npm:firecrawl-mcp@3.25.5') is None`; `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[absolute-dir]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[alias]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[file-prefix]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-ssh]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-url]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[home-dir]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-path]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-shortcut]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[not-uri-safe-tag]` (+12 more) |
| M16 | P1: dist-tag slots refused | `loader.py:695c695` | **5 failed, 488 passed** (restored=True) | `AssertionError: assert ['-y', '@play...t/mcp@latest'] == ['-y', '@play...ht/mcp@1.2.3']`; `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t@beta-2.tgzx-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@next-t@3.25.5]`, `test_version_replaces_a_dist_tag_slot`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M17 | P2: any npm selector counts as exact | `handlers.py:496c496` | **2 failed, 491 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@next]` |
| M19 | P3: materialisation not contained per entry | `loader.py:1244c1244` | **1 failed, 492 passed** (restored=True) | `TypeError: expected str, bytes or os.PathLike object, not int`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]` |
| M20 | F2: inherited env ignored | `handlers.py:1846c1846` | **6 failed, 487 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[absolute-host-export]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[dot-slash-host-export]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[exe-on-linux-host-export]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[upper-host-export]`, `test_health_warns_when_the_relaxer_comes_from_the_gateway_environment` |
| M21 | F3: no cache | `handlers.py:3870c3870` | **1 failed, 492 passed** (restored=True) | `assert 3 == 1`; `test_health_loads_the_manifest_once_until_a_source_changes` |
| M22 | F3: fingerprint misses the user overlay | `loader.py:825d824` | **1 failed, 492 passed** (restored=True) | `assert 1 == 2`; `test_health_loads_the_manifest_once_until_a_source_changes` |
| M23 | N2: build metadata accepted | `loader.py:616c616` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert '3.25.5+evil' is None`; `test_server_version_refuses_anything_but_one_exact_version["3.25.5+evil"]` |
| M24 | CLI labels a range [FAILED] | `cli.py:1027c1027` | **4 failed, 489 passed** (restored=True) | `assert False`; `test_pmcp_update_renders_a_range_as_floating`, `test_update_server_never_labels_a_container_command_pinned`, `test_update_server_never_labels_a_divergent_install_argv_pinned`, `test_update_server_reports_a_docker_tag_as_floating` |
| M25 | B1: selector file check removed from split | `loader.py:691,692d690` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert ('t', 'corp.tgz') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-TGZ-mixed]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-npm10-tar-gz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-tgz]` |
| M26 | B1: bare-tarball / tarball-NAME slot accepted | `loader.py:683,684d682` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert ('corp.tgz', None) is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[bare-tarball-TAR]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[bare-tarball-tgz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-name-with-version]` |
| M27 | N4: x/X/v1.2.x range words accepted as tags | `loader.py:695,697c695` | **7 failed, 486 passed** (restored=True) | `AssertionError: assert ('t', 'x') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-X]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-v-partial]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-v-xbeta]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vvX]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vv]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-x]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[version-vv]` |
| M28 | N4: match instead of fullmatch (trailing newline) | `loader.py:695c695` | **9 failed, 484 passed** (restored=True) | `AssertionError: assert ('myalias', 'npm:firecrawl-mcp@3.25.5') is None`; `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[alias]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[file-prefix]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-ssh]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-url]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-path]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-shortcut]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[not-uri-safe-tag]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tag-trailing-newline]` |
| M31 | N3: label keeps +metadata | `handlers.py:6715c6715` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert ('3.25.5+evil', 'newer') == ('3.25.5', 'newer')`; `test_update_server_labels_build_metadata_with_what_npm_runs` |
| M32 | N1: fingerprint misses the project overlay | `loader.py:828c828` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert (('/mnt/workspace/worktrees/viperjuice/pmcp-294-rev22-spike/src/pmcp/manifest/manifest.yaml', 1790662375343248285, 782...', None, None), `; `test_fingerprint_changes_when_a_project_overlay_appears` |
| M33 | N1: fingerprint misses the trust store | `loader.py:834c834` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert (('/mnt/workspace/worktrees/viperjuice/pmcp-294-rev22-spike/src/pmcp/manifest/manifest.yaml', 1790662375343248285, 782...viperjuice/pytes`; `test_fingerprint_changes_when_the_project_overlay_is_approved` |
| M34 | B1': pin value checked with the bare SemVer grammar instead of main's npm-aware is_valid_package_version | `loader.py:616c616 loader.py:17a18` | **7 failed, 486 passed** (restored=True) | `AssertionError: assert '3.25.5-evil.tgz' is None`; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-a.tar.gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.TAR"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.tar-gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.2.3-X.Tar.Gz"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5-evil.tgz"]`, `test_server_version_refuses_anything_but_one_exact_version["9007199254740992.0.0"]` |
| M35 | B1': the REV-3 ORDER restored in the split (bare SemVer accepted before the file check) | `loader.py:690a691,692 loader.py:17a18` | **8 failed, 485 passed** (restored=True) | `AssertionError: assert ('firecrawl-mcp', '3.25.5-corp.tgz') is None`; `test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes`, `test_the_floating_label_claims_only_what_every_npm_reads`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[oversized-core-is-a-tag]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-TAR]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-build]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-npm10-tar-gz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-prerelease]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-scoped]` |
| M40 | B1': _is_exact_pin trusts the bare SemVer grammar | `handlers.py:496c496 handlers.py:72a73` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert True is False`; `test_a_tarball_shaped_version_is_never_an_exact_pin` |
| M36 | N-c: excluded names accepted | `loader.py:685,686d684` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert ('node_modules', None) is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[excluded-Node_Modules-versioned]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[excluded-favicon]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[excluded-node_modules]` |
| M37 | N-a: only one leading v | `loader.py:658c658` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert ('t', 'vv1') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vvX]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vv]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[version-vv]` |
| M43 | C2: the generic SemVer check restored ahead of the launcher branches | `handlers.py:494a495,496` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert True is False`; `test_exactness_is_decided_per_launcher`, `test_health_warns_on_a_docker_tag_however_version_like`, `test_update_server_reports_a_docker_tag_as_floating` |
| M51 | C1: a matching @scope:registry counted harmless | `handlers.py:775c775` | **1 failed, 492 passed** (restored=True) | `AssertionError: npm_config_@corp:registry`; `test_the_entry_env_allowlist` |
| M52 | NB-2: the old floating label | `handlers.py:532c532` | **4 failed, 489 passed** (restored=True) | `assert 'not one exact version on every npm release' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client npm:fc-mcp floats on '^3.25`; `test_health_warns_on_a_range_or_dist_tag[fc-mcp@3.x]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@^3.25.5]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@next]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@~3.25.5]` |
| M53 | C2: any sha256: prefix counted a digest | `handlers.py:498c498` | **2 failed, 491 passed** (restored=True) | `AssertionError: assert True is False`; `test_docker_digest_labels_and_the_entrys_docker_env`, `test_exactness_is_decided_per_launcher` |
| M69 | N5: a malformed digest labelled a tag | `handlers.py:514c514` | **1 failed, 492 passed** (restored=True) | `assert 'a malformed content digest' in "'du' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client docker:example/client floats on '<redact...`; `test_docker_digest_labels_and_the_entrys_docker_env` |
| M75 | ENV allowlist: an unknown key counted inert | `handlers.py:806c806` | **33 failed, 460 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g2-unpinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_a_local_command_does_not_waive_the_env_check[manifest-empty-path]`, `test_a_local_command_does_not_waive_the_env_check[manifest-ld-preload]`, `test_a_local_command_does_not_waive_the_env_check[manifest-node-options]`, `test_a_local_command_does_not_waive_the_env_check[manifest-openssl-conf]`, `test_a_local_command_does_not_waive_the_env_check[user-empty-path]` (+23 more) |
| M77 | ENV: the server's declared keys not inert | `handlers.py:791,792d790` | **105 failed, 388 passed** (restored=True) | `AssertionError: assert (None, '3.26.0') == ('3.25.5', '3.26.0')`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[declared-entry-url-no-e-False]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[safe-args]`, `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_launcher_path_the_childs_path_resolves_is_the_launcher`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_local_command_does_not_waive_the_cwd_check` (+95 more) |
| M78 | ENV: locale/terminal keys not inert | `handlers.py:794c794` | **4 failed, 489 passed** (restored=True) | `assert ["'fc' talks ...ned version."] == []`; `test_a_member_with_no_judge_problem_and_no_relaxer_stays_quiet[locale-key]`, `test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent[identity-off]`, `test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent[identity-on]`, `test_the_entry_env_allowlist` |
| M79 | ENV: every npm_config_* key counted inert | `handlers.py:797c797` | **14 failed, 479 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-cache]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-call]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-package]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-registry-uppercase]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-tag]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-userconfig]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-cache]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-call]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-package]` (+4 more) |
| M80 | ENV: every key counted inert for uv | `handlers.py:799c799` | **9 failed, 484 passed** (restored=True) | `AssertionError: UV_OVERRIDE`; `test_an_entry_key_not_proven_inert_is_unverifiable[empty-path]`, `test_an_entry_key_not_proven_inert_is_unverifiable[ld-preload]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-cache-home]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-config-dirs]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[env-index]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[env-override]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[env-xdg]`, `test_the_entry_env_allowlist` |
| M81 | ENV: every key counted inert for cargo | `handlers.py:803c803` | **3 failed, 490 passed** (restored=True) | `AssertionError: CARGO_HOME`; `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[env-registry-index]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[env-rustc-wrapper]`, `test_the_entry_env_allowlist` |
| M87 | SHAPE cargo: a +toolchain / non-install argv not refused up front | `handlers.py:1152c1152` | **2 failed, 491 passed** (restored=True) | `assert 'is not 'cargo install ...'' in "'sh1' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: its ar`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[cargo-not-install]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M103 | N2: npx --yes=true not modelled | `handlers.py:817c817` | **1 failed, 492 passed** (restored=True) | `assert ["'ok1' talks... recognises."] == []`; `test_common_inert_spellings_are_recognised[npx-yes-true]` |
| M10 | pin not consulted for the warning | `handlers.py:1774c1774` | **33 failed, 460 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_malformed_member_is_contained_to_its_own_server`, `test_a_uvx_url_requirement_is_never_an_exact_pin`, `test_an_exception_judging_one_server_never_hides_another`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[drops-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required-no-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[older-copy]`, `test_docker_digest_labels_and_the_entrys_docker_env`, `test_docker_judges_the_relaxer_on_the_containers_env[cluster-ie-attached]`, `test_docker_judges_the_relaxer_on_the_containers_env[cluster-ie-equals]` (+23 more) |
| M18 | P2: update reports a non-pin as pinned | `handlers.py:6690c6690` | **8 failed, 485 passed** (restored=True) | `AssertionError: assert ('3.25.5', None) == (None, '3.25.5')`; `test_a_malformed_member_is_contained_to_its_own_server`, `test_an_argv_the_judge_refuses_never_shows_pinned[env-problem]`, `test_update_server_never_labels_a_container_command_pinned`, `test_update_server_never_labels_a_divergent_install_argv_pinned`, `test_update_server_never_labels_a_repeated_from_pinned`, `test_update_server_reports_a_docker_tag_as_floating`, `test_update_server_reports_a_range_as_floating_not_pinned`, `test_update_server_warns_about_a_docker_args_member_with_an_npx_install` |
| M57 | C1/B2: an exact argv suppresses whatever the entry sets | `handlers.py:1779c1779` | **37 failed, 456 passed** (restored=True) | `assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_a_local_command_does_not_waive_the_cwd_check`, `test_a_local_command_does_not_waive_the_env_check[manifest-empty-path]`, `test_a_local_command_does_not_waive_the_env_check[manifest-ld-preload]`, `test_a_local_command_does_not_waive_the_env_check[manifest-node-options]` (+27 more) |
| M66 | N5: a uvx URL requirement read as its fragment's version | `handlers.py:1123c1123` | **1 failed, 492 passed** (restored=True) | `assert 'a URL requirement names a file' in "'uv' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client pypi:fc-mcp is unpinned, so a spawn or `; `test_a_uvx_url_requirement_is_never_an_exact_pin` |
| M68 | trust boundary: the HOST environment judged too | `handlers.py:1656c1656` | **145 failed, 348 passed** (restored=True) | `AssertionError: assert (None, '3.26.0') == ('3.25.5', '3.26.0')`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[declared-entry-url-no-e-False]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-url-no-e-False]`, `test_a_clean_docker_run_without_the_url_in_the_container_stays_quiet`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[safe-args]`, `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g2-unpinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]` (+135 more) |
| M72 | cwd: an entry-set cwd ignored | `handlers.py:1667c1667` | **7 failed, 486 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]`, `test_a_local_command_does_not_waive_the_cwd_check`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[cargo]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[npx]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[uvx]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_the_judge_checks_every_members_own_env_and_cwd` |
| M73 | cwd: docker counted cwd-sensitive | `handlers.py:1525a1526` | **9 failed, 484 passed** (restored=True) | `assert False`; `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[resolved-path-with-cwd]`, `test_a_proc_path_entry_counts_as_searching_the_cwd`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[absolute-only]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[dot-first]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[empty-entry]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[relative-entry]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_windows_searches_the_parents_cwd_not_the_entrys` |
| M74 | cwd: the inherited (host) cwd judged too | `handlers.py:1667c1667` | **67 failed, 426 passed** (restored=True) | `AssertionError: assert (None, '3.26.0') == ('3.25.5', '3.26.0')`; `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[safe-args]`, `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_fully_read_member_without_the_relaxer_stays_quiet[local]`, `test_a_fully_read_member_without_the_relaxer_stays_quiet[npx-pinned]`, `test_a_fully_read_member_without_the_relaxer_stays_quiet[npx-unpinned]`, `test_a_launcher_path_the_childs_path_resolves_is_the_launcher`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]` (+57 more) |
| M82 | SHAPE npx: an unknown flag counted inert | `handlers.py:943,944c943` | **4 failed, 489 passed** (restored=True) | `assert 'cannot verify' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client npm:other is unpinned, so a spawn or 'pmcp update' can m`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[npx-package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]`, `test_health_says_it_cannot_verify_an_unreadable_slot_without_identity`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M84 | SHAPE uvx: an unknown flag counted inert | `handlers.py:1084c1084,1085` | **6 failed, 487 passed** (restored=True) | `assert 'argv passes --index-url' in "'uv' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: its ... a `; `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[uvx-unknown-flag]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[uvx-python]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[argv-index]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[argv-overrides]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[argv-with]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M85 | SHAPE uvx: --from with another command counted the package | `handlers.py:1114c1114` | **2 failed, 491 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[uvx-from-then-sh]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M86 | SHAPE cargo: an unknown flag counted inert | `handlers.py:1190,1192c1190,1191` | **3 failed, 490 passed** (restored=True) | `assert 'argv passes --git' in "'cg' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: its ...tall exac`; `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-git]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-registry]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M88 | SHAPE docker: a container command after the image counted inert | `handlers.py:1303c1303` | **3 failed, 490 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-command-after-image]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_update_server_never_labels_a_container_command_pinned` |
| M89 | SHAPE docker: an unknown flag (--entrypoint, -v...) counted inert | `handlers.py:1290,1295c1290,1291` | **5 failed, 488 passed** (restored=True) | `assert 'cannot verify' in "'uv' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client docker:example/client floats on '3.25.5'... can move it `; `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[docker-unknown-flag]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-entrypoint]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-file]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_the_env_file_reason_keeps_its_specific_wording` |
| M90 | SHAPE docker: -e KEY not judged | `handlers.py:1662c1662` | **3 failed, 490 passed** (restored=True) | `assert 0 == 1`; `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-node-options]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M91 | N2: an unmodelled runner or wrapper silent (read as a local binary) | `handlers.py:1485c1485` | **8 failed, 485 passed** (restored=True) | `assert 'a package runner or wrapper pmcp does not model' in "'rn1' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its cli`; `test_an_unmodelled_runner_or_wrapper_is_unverifiable[bunx]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[env]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[node]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[pnpm-dlx]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[pnpx]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[sh-c]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[uv-run]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M92 | N2: `uv tool run` not judged as uvx | `handlers.py:1473c1473` | **3 failed, 490 passed** (restored=True) | `assert ["'ut1' talks... recognises."] == []`; `test_a_uv_interpreter_request_is_never_an_exact_pin[tool-space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[uv-tool-run]`, `test_uv_tool_run_is_judged_as_uvx` |
| M93 | B1/X1: update_server labels a pin the judge did not read [PINNED] | `handlers.py:6691c6691` | **1 failed, 492 passed** (restored=True) | `assert '3.25.5' is None`; `test_update_server_never_labels_a_pin_its_judge_did_not_read` |
| M94 | X1: the verdict ignores the args' shape | `handlers.py:1771,1772d1770` | **201 failed, 292 passed** (restored=True) | `assert 'cannot verify' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client npm:None is unpinned, so a spawn or 'pmcp update' can mo`; `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[ampersand]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[percent]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[pipe]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[space]`, `test_a_launcher_must_be_itself_not_named_like_it[abs-npx-not-on-path]`, `test_a_launcher_must_be_itself_not_named_like_it[dot-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[drive-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[node-modules-bin-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[npm.cmd]`, `test_a_launcher_must_be_itself_not_named_like_it[npx.cmd]` (+191 more) |
| M95 | ENV: the entry's env block not judged | `handlers.py:1657c1657` | **53 failed, 440 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g2-unpinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_a_local_command_does_not_waive_the_env_check[manifest-empty-path]`, `test_a_local_command_does_not_waive_the_env_check[manifest-ld-preload]`, `test_a_local_command_does_not_waive_the_env_check[manifest-node-options]`, `test_a_local_command_does_not_waive_the_env_check[manifest-openssl-conf]`, `test_a_local_command_does_not_waive_the_env_check[user-empty-path]` (+43 more) |
| M96 | B2: an overlay declaration exempts a key | `handlers.py:1934c1934` | **3 failed, 490 passed** (restored=True) | `assert 0 == 1`; `test_an_overlay_declaration_exempts_no_key[env-var-openssl-conf]`, `test_an_overlay_declaration_exempts_no_key[env-var-target-cc]`, `test_an_overlay_declaration_exempts_no_key[relaxer-openssl-conf]` |
| M97 | B1: an unreadable install argv counted fine | `handlers.py:1795,1800d1794` | **3 failed, 490 passed** (restored=True) | `assert 'runs something pmcp cannot read' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: th`; `test_a_malformed_member_is_contained_to_its_own_server`, `test_a_repeated_from_in_an_install_argv_is_unverifiable`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]` |
| M99 | B1: an install argv may name another package/version | `handlers.py:1801c1801` | **7 failed, 486 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[unpinned]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_no_diagnostic_renders_a_token_from_an_argv`, `test_update_server_never_labels_a_divergent_install_argv_pinned`, `test_update_server_warns_about_a_docker_args_member_with_an_npx_install` |
| M101 | B1: install argv env/cwd not judged | `handlers.py:1816c1816` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert 'silent' == 'cannot_verify'`; `test_the_judge_checks_every_members_own_env_and_cwd` |
| M102 | N2: combined docker short flags not modelled | `handlers.py:1221a1222,1223` | **3 failed, 490 passed** (restored=True) | `assert 'argv passes -P' in "'sh1' may talk to a self-hosted backend (its args ('docker <redacted> <redacted> <redacted> <redacted> <redacted>'), ...ts client is`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-unknown-letter-in-cluster]`, `test_common_inert_spellings_are_recognised[docker-it]`, `test_the_one_parser_models_common_spellings[docker-container-run]` |
| M104 | B-1: the local-binary exemption skips the other spawn members | `handlers.py:1784c1784` | **6 failed, 487 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_no_diagnostic_renders_a_token_from_an_argv` |
| M105 | B-1: install argvs left out of the spawn set | `handlers.py:1571c1571` | **17 failed, 476 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_malformed_member_is_contained_to_its_own_server`, `test_a_repeated_from_in_an_install_argv_is_unverifiable`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]` (+7 more) |
| M106 | the spawn set judged only in part | `handlers.py:1781c1781` | **15 failed, 478 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_repeated_from_in_an_install_argv_is_unverifiable`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[unpinned]` (+5 more) |
| M107 | a spawning site dropped from _SERVER_SPAWN_SITES (drops its members) | `handlers.py:1607d1606` | **18 failed, 475 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_malformed_member_is_contained_to_its_own_server`, `test_a_repeated_from_in_an_install_argv_is_unverifiable`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]` (+8 more) |
| M108 | grok B1: a repeated --from read (last-wins guess) instead of refused | `handlers.py:1058c1058` | **5 failed, 488 passed** (restored=True) | `assert 0 == 1`; `test_a_repeated_from_in_an_install_argv_is_unverifiable`, `test_a_repeated_single_valued_flag_is_not_a_recognised_shape[uvx-from-evil-then-real]`, `test_a_repeated_single_valued_flag_is_not_a_recognised_shape[uvx-from-skew]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_update_server_never_labels_a_repeated_from_pinned` |
| M109 | grok B1: a repeated cargo --version accepted | `handlers.py:1164c1164` | **2 failed, 491 passed** (restored=True) | `assert 0 == 1`; `test_a_repeated_single_valued_flag_is_not_a_recognised_shape[cargo-version-twice]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M110 | grok B1: a repeated docker single-valued flag accepted | `handlers.py:1277c1277` | **2 failed, 491 passed** (restored=True) | `assert 0 == 1`; `test_a_repeated_single_valued_flag_is_not_a_recognised_shape[docker-name-twice]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M111 | npm exec: flags after the package ignored | `handlers.py:979c979` | **2 failed, 491 passed** (restored=True) | `assert 0 == 1`; `test_a_repeated_single_valued_flag_is_not_a_recognised_shape[npm-exec-trailing-flag]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M112 | grok B2: the local-binary exemption waives env/cwd too | `handlers.py:1772a1773,1774` | **15 failed, 478 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_local_command_does_not_waive_the_cwd_check`, `test_a_local_command_does_not_waive_the_env_check[manifest-empty-path]`, `test_a_local_command_does_not_waive_the_env_check[manifest-ld-preload]`, `test_a_local_command_does_not_waive_the_env_check[manifest-node-options]`, `test_a_local_command_does_not_waive_the_env_check[manifest-openssl-conf]`, `test_a_local_command_does_not_waive_the_env_check[user-empty-path]` (+5 more) |
| M113 | codex 1: argvs rendered raw in diagnostics | `handlers.py:858c858` | **9 failed, 484 passed** (restored=True) | `AssertionError: ["'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the...hich can change what ru`; `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[a-env-prefix]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[b-sh-c]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[c-npx-c]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[d-docker-path-not-which]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[e-podman]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[f-env-prefix-exact-pin]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_no_diagnostic_renders_a_token_from_an_argv` |
| M114 | codex 2: a malformed argv element not refused | `handlers.py:1439c1439` | **2 failed, 491 passed** (restored=True) | `assert 'not a list of strings' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the ...which`; `test_a_malformed_member_is_contained_to_its_own_server`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M115 | codex 2: one server's failure escapes its containment | `handlers.py:3908,3909c3908,3909` | **1 failed, 492 passed** (restored=True) | `KeyError: 'boom'`; `test_health_contains_a_failure_outside_the_judge_per_server` |
| M116 | B-1: an unrecognised command treated as a local binary | `handlers.py:1504,1505c1504` | **92 failed, 401 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[e-podman]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[no-key-mention-unknown-launcher]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-bare-unshipped-command]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+npx-pinned]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+npx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+pnpm-dlx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+uvx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-corepack+npx-pinned]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-corepack+npx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-corepack+pnpm-dlx]` (+82 more) |
| M117 | B-1: a launcher with an unstripped extension (npx.js) may be local | `handlers.py:1495c1495` | **2 failed, 491 passed** (restored=True) | `AssertionError: assert None`; `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_only_the_shipped_command_is_a_local_binary` |
| M118 | B-1: the entry's own command taken as evidence that it is local | `handlers.py:1933c1933` | **91 failed, 402 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[e-podman]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[no-key-mention-unknown-launcher]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-bare-unshipped-command]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+npx-pinned]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+npx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+pnpm-dlx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-busybox-sh+uvx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-corepack+npx-pinned]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-corepack+npx]`, `test_an_unrecognised_command_is_never_a_local_binary[manifest-corepack+pnpm-dlx]` (+81 more) |
| M119 | relaxer read from the overlay only | `handlers.py:683c683` | **4 failed, 489 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[drops-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required-no-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[older-copy]`, `test_the_warning_relaxers_are_shipped_union_overlay` |
| M120 | update wrapper: relaxer read from the overlay only | `handlers.py:3846,3848c3846` | **3 failed, 490 passed** (restored=True) | `assert 0 == 1`; `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[drops-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required-no-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[older-copy]` |
| M121 | health filter: relaxer read from the overlay only | `handlers.py:3875c3875` | **3 failed, 490 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[drops-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[not-required-no-relaxer]`, `test_an_overlay_cannot_switch_the_warning_off_by_dropping_the_relaxer[older-copy]` |
| M122 | a launcher judged by its basename | `handlers.py:1462c1462` | **42 failed, 451 passed** (restored=True) | `assert 0 == 1`; `test_a_launcher_must_be_itself_not_named_like_it[abs-npx-not-on-path]`, `test_a_launcher_must_be_itself_not_named_like_it[dot-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[drive-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[node-modules-bin-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[npm.cmd]`, `test_a_launcher_must_be_itself_not_named_like_it[npx.cmd]`, `test_a_launcher_must_be_itself_not_named_like_it[tmp-npx]`, `test_a_launcher_must_be_itself_not_named_like_it[upper-NPX]`, `test_a_launcher_must_be_itself_not_named_like_it[uvx.exe]`, `test_a_launcher_must_be_itself_not_named_like_it[windows-npx]` (+32 more) |
| M123 | any path named like the launcher counts | `handlers.py:1408c1408` | **9 failed, 484 passed** (restored=True) | `assert 0 == 1`; `test_a_launcher_must_be_itself_not_named_like_it[abs-npx-not-on-path]`, `test_a_launcher_must_be_itself_not_named_like_it[tmp-npx]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[absolute-entry-env]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[absolute-host-export]`, `test_a_launcher_path_the_childs_path_resolves_is_the_launcher`, `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[proc-self-cwd-no-cwd]`, `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[proc-self-cwd]`, `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[proc-thread-self-cwd]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[d-docker-path-not-which]` |
| M124 | a relative launcher path accepted (equal to a relative PATH search) | `handlers.py:1405,1406d1404` | **2 failed, 491 passed** (restored=True) | `assert 0 == 1`; `test_a_relative_path_equal_to_a_relative_path_search_is_not_the_launcher[docker]`, `test_a_relative_path_equal_to_a_relative_path_search_is_not_the_launcher[npx]` |
| M125 | docker with an entry cwd silent while PATH searches the cwd | `handlers.py:1677c1677` | **6 failed, 487 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_proc_path_entry_counts_as_searching_the_cwd`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[dot-first]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[empty-entry]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[relative-entry]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M126 | a relative PATH entry not counted as searching the cwd | `handlers.py:1642c1642` | **4 failed, 489 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[dot-first]`, `test_docker_with_an_entry_cwd_is_loud_when_path_searches_the_cwd[relative-entry]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M127 | B-1: docker's cwd exemption for a path spelling | `handlers.py:1676,1677c1676` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[resolved-path-with-cwd]` |
| M128 | B-1: a launcher path resolved in pmcp's process (realpath) | `handlers.py:1408c1408` | **3 failed, 490 passed** (restored=True) | `assert 'PATH resolves it' in "'dk' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the ...which can `; `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[proc-self-cwd-no-cwd]`, `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[proc-self-cwd]`, `test_a_path_launcher_under_an_entry_cwd_is_never_exempt[proc-thread-self-cwd]` |
| M129 | a /proc PATH entry not counted as cwd-dependent | `handlers.py:1642c1642` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_proc_path_entry_counts_as_searching_the_cwd` |
| M130 | N-1: cmd.exe metacharacters accepted for a .cmd launcher | `handlers.py:1449c1449` | **5 failed, 488 passed** (restored=True) | `assert 0 == 1`; `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[ampersand]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[percent]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[pipe]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[space]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M133 | N-3: Windows treated as searching the entry's cwd | `handlers.py:1637c1637` | **1 failed, 492 passed** (restored=True) | `assert ["'dk' talks ...ned version."] == []`; `test_windows_searches_the_parents_cwd_not_the_entrys` |
| M131 | N-2: docker's own env used for the relaxer, not the container's | `handlers.py:1870c1870` | **4 failed, 489 passed** (restored=True) | `assert ["'dk' talks ...launches it."] == []`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[declared-entry-url-no-e-False]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-url-no-e-False]`, `test_a_clean_docker_run_without_the_url_in_the_container_stays_quiet`, `test_docker_judges_the_relaxer_on_the_containers_env[not-passed-to-container]` |
| M132 | an unreadable docker argv (--env-file) not treated as 'may' | `handlers.py:1864c1864` | **11 failed, 482 passed** (restored=True) | `assert 0 == 1`; `test_a_repeated_single_valued_flag_is_not_a_recognised_shape[docker-name-twice]`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[docker-unknown-flag]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-command-after-image]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-entrypoint]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-file]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-unknown-letter-in-cluster]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-volume]`, `test_docker_judges_the_relaxer_on_the_containers_env[env-file]`, `test_docker_judges_the_relaxer_on_the_containers_env[unread-cluster]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` (+1 more) |
| M134 | B-1: relaxer from the args member only | `handlers.py:1939c1939` | **3 failed, 490 passed** (restored=True) | `assert 0 == 1`; `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_an_unreadable_install_member_may_talk_to_the_self_hosted_backend`, `test_update_server_warns_about_a_docker_args_member_with_an_npx_install` |
| M135 | B-1: docker (no own env) decided by basename | `handlers.py:1862c1862` | **8 failed, 485 passed** (restored=True) | `assert 'talks to a self-hosted backend (SELFHOST_API_URL is set)' in "'dk' may talk to a self-hosted backend (its args ('/tmp/pytest-of-viperjuice/pytest-6315/t`; `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[absolute-entry-env]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[absolute-host-export]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[dot-slash-entry-env]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[dot-slash-host-export]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[exe-on-linux-entry-env]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[exe-on-linux-host-export]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[upper-entry-env]`, `test_a_launcher_named_docker_that_is_not_docker_uses_its_own_env[upper-host-export]` |
| M136 | B-1: a second parser reads the container env | `handlers.py:1854c1854` | **10 failed, 483 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_docker_digest_labels_and_the_entrys_docker_env`, `test_docker_judges_the_relaxer_on_the_containers_env[cluster-ie-attached]`, `test_docker_judges_the_relaxer_on_the_containers_env[cluster-ie-equals]`, `test_docker_judges_the_relaxer_on_the_containers_env[e-attached]`, `test_docker_judges_the_relaxer_on_the_containers_env[e-equals]`, `test_docker_judges_the_relaxer_on_the_containers_env[e-inherit]`, `test_docker_judges_the_relaxer_on_the_containers_env[env-equals]`, `test_docker_judges_the_relaxer_on_the_containers_env[last-wins-empty]`, `test_docker_judges_the_relaxer_on_the_containers_env[last-wins-unset]`, `test_health_warns_on_a_docker_tag_however_version_like` |
| M137 | B-1 row 3: a value attached to a shorthand cluster ignored | `handlers.py:1228c1228` | **4 failed, 489 passed** (restored=True) | `assert 0 == 1`; `test_docker_judges_the_relaxer_on_the_containers_env[cluster-ie-attached]`, `test_docker_judges_the_relaxer_on_the_containers_env[e-attached]`, `test_docker_judges_the_relaxer_on_the_containers_env[last-wins-empty]`, `test_the_one_docker_reader_returns_the_container_env_in_order` |
| M138 | B-1 row 3: pflag's leading '=' kept (-e=K=V) | `handlers.py:1226c1226` | **3 failed, 490 passed** (restored=True) | `assert 'SELFHOST_API_URL is set' in "'dk' may talk to a self-hosted backend (its args ('docker <redacted> <redacted> <redacted>'): its argv sets container... ca`; `test_docker_judges_the_relaxer_on_the_containers_env[cluster-ie-equals]`, `test_docker_judges_the_relaxer_on_the_containers_env[e-equals]`, `test_the_one_docker_reader_returns_the_container_env_in_order` |
| M139 | a later bare -e KEY does not unset (last one wins) | `handlers.py:1860c1860` | **1 failed, 492 passed** (restored=True) | `assert ["'dk' talks ...launches it."] == []`; `test_docker_judges_the_relaxer_on_the_containers_env[last-wins-unset]` |
| M140 | B-1: applies only when EVERY member applies | `handlers.py:1948c1948` | **2 failed, 491 passed** (restored=True) | `assert 0 == 1`; `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_update_server_warns_about_a_docker_args_member_with_an_npx_install` |
| M141 | a failure deciding whether the warning applies reads as 'does not apply' | `handlers.py:1941,1942c1941` | **1 failed, 492 passed** (restored=True) | `assert 0 == 1`; `test_a_failure_while_deciding_whether_the_warning_applies_is_loud` |
| M142 | B-1 row 3: any letter accepted in a docker shorthand cluster | `handlers.py:1232c1232` | **3 failed, 490 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-unknown-letter-in-cluster]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-volume]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M143 | B-1: the container env used despite member problems | `handlers.py:1865c1865` | **6 failed, 487 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-dot-path-planted-cwd-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M144 | the relaxer's member judge run without the shipped declarations | `handlers.py:1937c1937` | **96 failed, 397 passed** (restored=True) | `assert ["'fc' talks ...ned version."] == []`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[declared-entry-url-no-e-False]`, `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[safe-args]`, `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_launcher_path_the_childs_path_resolves_is_the_launcher`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_local_command_does_not_waive_the_cwd_check` (+86 more) |
| M145 | B-1: a docker member with problems judged on its container env only | `handlers.py:1867c1867` | **4 failed, 489 passed** (restored=True) | `assert 'SELFHOST_API_URL is set' in "'dk' may talk to a self-hosted backend (its args ('docker <redacted> <redacted> <redacted> <redacted>'): the entry's ... ca`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-dot-path-planted-cwd-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]` |
| M146 | B-1: an unreadable non-docker member judged by its process env | `handlers.py:1871c1871` | **10 failed, 483 passed** (restored=True) | `assert 0 == 1`; `test_an_unreadable_install_member_may_talk_to_the_self_hosted_backend`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[a-env-prefix]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[b-sh-c]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[c-npx-c]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[d-docker-path-not-which]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[e-podman]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[f-env-prefix-exact-pin]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[no-key-mention-sh-c]`, `test_an_unreadable_member_may_talk_to_the_self_hosted_backend[no-key-mention-unknown-launcher]`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M147 | an unreadable docker member's own env taken as the container's | `handlers.py:1864c1864` | **2 failed, 491 passed** (restored=True) | `assert 'may talk to a self-hosted backend' in "'dk' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: `; `test_docker_judges_the_relaxer_on_the_containers_env[env-file]`, `test_docker_judges_the_relaxer_on_the_containers_env[unread-cluster]` |
| M148 | B-1: the relaxer ignores env/cwd problems | `handlers.py:1861c1861` | **11 failed, 482 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-dot-path-planted-cwd-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[entry-path-digest-True]`, `test_a_bare_docker_the_entry_can_redirect_is_judged_on_its_own_env[host-dot-path-entry-cwd-True]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g-node-options-pinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g2-unpinned]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[g3-npm-config]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]`, `test_a_fully_read_member_with_a_judge_problem_may_talk[i-docker-e]`, `test_an_overlay_env_key_problem_may_talk` (+1 more) |
| M149 | N-3: the --env-file reason loses its specific wording | `handlers.py:1877c1877` | **1 failed, 492 passed** (restored=True) | `assert 'may talk to a self-hosted backend (its docker --env-file, which pmcp cannot read, can set SELFHOST_API_URL in the container)' in "'dk' may talk to a sel`; `test_the_env_file_reason_keeps_its_specific_wording` |
| M150 | a second path: the verdict recomputes member problems apart from the relaxer's | `handlers.py:1960c1960` | **90 failed, 403 passed** (restored=True) | `assert ["'fc' talks ...ned version."] == []`; `test_a_cmd_launcher_on_windows_refuses_cmd_metacharacters[safe-args]`, `test_a_docker_args_member_does_not_switch_off_an_install_member`, `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_launcher_path_the_childs_path_resolves_is_the_launcher`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[absolute-path]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-npx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[local-command-uvx-install]`, `test_a_local_command_does_not_exempt_an_install_argv_provision_adopts[pinned-install]`, `test_a_local_command_does_not_waive_the_cwd_check`, `test_a_local_command_is_exempt_only_when_it_is_the_whole_spawn_set` (+80 more) |
| M151 | B1: an interpreter name read as a package | `handlers.py:1090,1092c1090` | **25 failed, 468 passed** (restored=True) | `AssertionError: assert None is not None`; `test_a_uv_interpreter_request_is_never_an_exact_pin[at-version]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[cpython]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[from-space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[graalpy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pyodide]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pypy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pythonw]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[specifier]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[suffix-nodot]` (+15 more) |
| M152 | B1: interpreter names matched case-sensitively | `handlers.py:1027c1027` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert None is not None`; `test_a_uv_interpreter_request_is_never_an_exact_pin[upper-suffix]` |
| M153 | B1: only python, not the implementation names | `handlers.py:1007,1010d1006` | **9 failed, 484 passed** (restored=True) | `AssertionError: assert None is not None`; `test_a_uv_interpreter_request_is_never_an_exact_pin[at-version]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[cpython]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[graalpy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pyodide]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pypy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[suffix-pypy3]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[tool-space]`, `test_no_argv_real_uv_runs_as_an_interpreter_is_read_as_a_pin`, `test_the_interpreter_diagnostic_names_only_the_interpreter` |
| M154 | B1: a version request after the name ignored | `handlers.py:1032c1032` | **6 failed, 487 passed** (restored=True) | `AssertionError: assert None is not None`; `test_a_uv_interpreter_request_is_never_an_exact_pin[suffix-nodot]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[suffix-pypy3]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[suffix-version]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[upper-suffix]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[vertical-tab]`, `test_the_interpreter_diagnostic_names_only_the_interpreter` |
| M155 | a '-' suffix (python3-openid) read as an interpreter | `handlers.py:1018c1018` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert ('pypi', 'uv ...ckage', False) == ('pypi', None, True)`; `test_a_package_named_like_an_interpreter_is_still_a_package[python3-openid==3.2.0]` |
| M156 | B1: the parsed requirement name not checked | `handlers.py:1091c1091` | **1 failed, 492 passed** (restored=True) | `assert '3.10' is None`; `test_an_argv_the_judge_refuses_never_shows_pinned[extra]` |
| M157 | B1: whitespace after the name not skipped | `handlers.py:1018c1018` | **1 failed, 492 passed** (restored=True) | `assert 'as an interpreter request (' in "'uv' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: its ..`; `test_a_uv_interpreter_request_is_never_an_exact_pin[vertical-tab]` |
| M158 | [PINNED] from main's separate pin reader, not the judge | `handlers.py:6706c6706` | **24 failed, 469 passed** (restored=True) | `AssertionError: assert ('3.25.5', None) == (None, '3.25.5')`; `test_a_malformed_member_is_contained_to_its_own_server`, `test_a_uv_interpreter_request_is_never_an_exact_pin[cpython]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[from-space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[graalpy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pyodide]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pypy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pythonw]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[upper]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[uvx-from]` (+14 more) |
| M159 | B1: the interpreter diagnostic quotes the raw requirement (rev 21) | `handlers.py:1104,1105c1104,1105` | **22 failed, 471 passed** (restored=True) | `assert 'as an interpreter request (' in "'uv' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: uv r..`; `test_a_uv_interpreter_request_is_never_an_exact_pin[at-version]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[cpython]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[from-space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[graalpy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pyodide]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pypy]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[pythonw]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[space]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[specifier]`, `test_a_uv_interpreter_request_is_never_an_exact_pin[suffix-nodot]` (+12 more) |
| M160 | the cwd path shown raw | `handlers.py:1669c1669` | **8 failed, 485 passed** (restored=True) | `assert 'the entry sets a cwd (not shown)' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: t`; `test_a_fully_read_member_with_a_judge_problem_may_talk[h-cwd]`, `test_a_local_command_does_not_waive_the_cwd_check`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[cargo]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[npx]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[uvx]`, `test_every_judge_diagnostic_interpolates_only_allowed_sources`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log`, `test_the_judge_checks_every_members_own_env_and_cwd` |
| M161 | the cargo toolchain shown raw | `handlers.py:1150c1150` | **2 failed, 491 passed** (restored=True) | `AssertionError: assert [('cargo', ['...tall', 'fc'])] == []`; `test_every_judge_diagnostic_interpolates_only_allowed_sources`, `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M162 | diag_selector admits anything | `loader.py:55c55` | **1 failed, 492 passed** (restored=True) | `assert 'SYNTHETIC_REVIEW_TOKEN' not in 'WARNING  pm...s unpinned\n'`; `test_the_interpreter_diagnostic_names_only_the_interpreter` |
| M163 | diag_name admits anything | `loader.py:43c43` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert [('cargo', ['...tall', 'fc'])] == []`; `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M164 | diag_flag admits anything | `loader.py:49c49` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert [('uvx', ['--...c-mcp==1.0'])] == []`; `test_no_credential_reaches_a_warning_an_update_message_or_a_log` |
| M165 | N1: a `v`-prefixed version request not read as an interpreter | `handlers.py:1018c1018` | **1 failed, 492 passed** (restored=True) | `AssertionError: assert None == 'python'`; `test_the_interpreter_diagnostic_names_only_the_interpreter` |

**134 of 134 mutants red** on the revision-22 spike (origin/main `7680445` + patch). Each ran
against the 493-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 22 adds
M159 (the revision-21 `_operator_safe(requirement_text)` interpreter message restored),
M160 (the cwd path shown), M161 (the cargo toolchain shown raw), M162-M164 (`diag_selector`,
`diag_name`, `diag_flag` admit anything) and M165 (N1: a `v`-prefixed request not read as an
interpreter). It re-targets M82, M84, M86, M89, M142, M151 and M155-M157. The first full run
stopped at M52 when one pytest run exceeded the driver's 600 s timeout, with the spike
restored (`cmp`-clean). Re-run alone, M52 finished in 23 s, red (4 failed). The driver was
resumed from M52, and the two logs are concatenated in order.

Revision 21 (for the record): 127 of 127 mutants red on the revision-21 spike (origin/main `7680445` + patch). Each ran
against the 490-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 21 adds
M156 (the parsed requirement name not checked), M157 (whitespace after the name not skipped)
and M158 (`[PINNED]` from main's separate pin reader, not the judge). It re-targets M151 and
M155, and M152 is killed by the new `upper-suffix` id.

Revision 20 (for the record): 124 of 124 mutants red on the revision-20 spike (origin/main `7680445` + patch). Each ran
against the 479-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 20 adds
M151 (a uv interpreter name read as a package), M152 (matched case-sensitively), M153 (only
`python`, not the implementation names), M154 (a version-request suffix ignored) and M155 (a
`-` suffix read as an interpreter).

Revision 19 (for the record): 119 of 119 mutants red on the revision-19 spike (origin/main `7680445` + patch). Each ran
against the 461-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 19 adds
M148 (the relaxer ignores env/cwd problems), M149 (the `--env-file` reason loses its wording)
and M150 (a second path: the verdict recomputes member problems apart from the relaxer's).
It re-targets M89, M131, M132, M134, M135, M140 and M143-M147 at `_Judged`/`_member_relaxer`.

Revision 18 (for the record): 116 of 116 mutants red on the revision-18 spike (origin/main `7680445` + patch). Each ran
against the 449-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 18 adds
M146 (an unreadable non-docker member judged by its process env) and M147 (an unreadable
docker member's own env taken as the container's). It re-targets M132 (an unreadable argv,
any launcher, not "may"), M140 (the members tuple now carries the spawn) and M135 (docker
decided by basename; revision 17's target became equivalent, see the Revision 18 section).

Revision 17 (for the record): 114 of 114 mutants red on the revision-17 spike (origin/main `7680445` + patch). Each ran
against the 435-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 17 adds
M143 (the container env used despite member problems), M144 (the relaxer's member judge run
without the shipped declarations) and M145 (a member with problems judged on its container
env only). It re-targets M96, M131, M132, M135, M136 and M139 at the restructured
`_member_relaxer`.

Revision 16 (for the record): 111 of 111 mutants red on the revision-16 spike (origin/main `7680445` + patch). Each ran
against the 429-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 16 adds
M134 (the relaxer from the args member only), M135 (the container env chosen by basename),
M136 (a second parser reads the container env), M137 (a value attached to a shorthand
cluster ignored), M138 (pflag's leading `=` kept), M139 (a later bare `-e KEY` does not
unset), M140 (applies only when every member applies), M141 (a failure deciding the relaxer
reads as "does not apply") and M142 (any letter accepted in a docker cluster). It re-targets
M131 and M132 at `_member_relaxer` (their revision-15 code is deleted), and M20, M89, M96,
M102, M110 and M118 at the moved code. M20 survived the first full run because its old line
no longer decides the relaxer; re-targeted at `_member_relaxer`'s process env, it is red
(5 failed), and the row above is that re-run.

Revision 15 (for the record): 102 of 102 mutants red on the revision-15 spike (origin/main `4d2790f` + patch). Each ran
against the 407-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 15 adds
M127 (docker's cwd exemption for a path spelling), M128 (`realpath` restored in the launcher
comparison), M129 (a `/proc` `PATH` entry not counted), M130 (the Windows `.cmd` charset
dropped), M131 (docker's own env used for the relaxer), M132 (`--env-file` ignored) and M133
(Windows treated as searching the entry's cwd), and re-targets M9, M123, M125 and M126. M124
survived the first 405-test cut and is killed by the new relative-`PATH` test (revision 15
section).

Revision 14 (for the record): 95 of 95 mutants red on the revision-14 spike (origin/main `260cc1a` + patch). Each ran
against the 388-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 14 adds
M124 (a relative launcher path accepted), M125 (docker with an entry cwd silent while `PATH`
searches the cwd) and M126 (a relative `PATH` entry not counted).

Revision 13 (for the record): 92 of 92 mutants red on the revision-13 spike (origin/main `260cc1a` + patch). Each ran
against the 368-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 13 adds
M119 (relaxer read from the overlay only), M120 (the same in the update wrapper), M121 (the
same in the health filter), M122 (a launcher judged by its basename) and M123 (any path named
like the launcher counts), and re-targets M118.

Revision 12 (for the record): 87 of 87 mutants red on the revision-12 spike (origin/main `260cc1a` + patch). Each ran
against the 358-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 12 adds
M116 (an unrecognised command treated as a local binary), M117 (a launcher with an unstripped
extension may be local) and M118 (the entry's own command taken as evidence), and re-targets
M96 at the new judge call.

Revision 11 (for the record): 84 of 84 mutants red on the revision-11 spike (origin/main `260cc1a` + patch). Each ran
against the final 263-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 11
adds M108-M115 (a repeated single-valued flag accepted, for uv, cargo and docker; `npm exec`
trailing flags ignored; the local exemption waiving env/cwd; argvs rendered raw; a malformed
element accepted; health containment removed). It re-targets 29 mutants at the one
parser/judge (M10, M18, M57, M66, M68, M72-M74, M82, M84-M86, M88-M97, M99, M101, M102,
M104-M107), including M107, which now drops members. It retires M29, M30, M38, M39 and M44
with the identity code, and M67, M98 and M100, whose rules the one judge now covers
(M97 for an unreadable member, M93 for the label). The first run had 5 survivors, closed by tests before the final run:
M93, M101, M115, M52 and M87.

Revision 10 (for the record): 84 of 84 mutants red on the revision-10 spike (origin/main `876fd33` + patch). Each ran
against the 236-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 10 adds
M104 (an early return skips the spawn set), M105 (install argvs left out of the set), M106
(the set judged only in part) and M107 (a spawning site dropped from `_SERVER_SPAWN_SITES`), and
re-targets M29 at the rewritten local-binary branch.

Revision 9 (for the record): 80 of 80 mutants red on the revision-9 spike (origin/main `876fd33` + patch). Each ran
against the 230-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 9 adds
M96 (an overlay declaration exempts a key), M97-M101 (install argvs not judged, or judged
weakly) and M102-M103 (N2 spellings). It retires M76 with the namespace guard and
re-targets M92.

Revision 8 (for the record): 73 of 73 mutants red on the revision-8 spike (origin/main `876fd33` + patch). Each ran
against the 219-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 8 adds
M75-M95 (every allowlist has an "unknown key/flag counted inert" mutant: M75, M79-M82, M84,
M86, M89) and retires the rev-7 denylist mutants M58-M65, M70 and M71. M57, M68 and M74 are
re-targeted at the new `_entry_redirect` call.

Revision 7 (for the record): 63 of 63 mutants red on the final revision-7 spike (origin/main `876fd33` + patch, cwd
ruling included). Each ran against the 190-test file, was restored from the pristine copy and `cmp`-checked (`restored=True` for
every row, 0 tracebacks in the log). Revision 7 adds M57-M71 for board round 5 and
retires the rev-6 host-discovery mutants M41, M42, M45-M50 and M54-M56 with their code. The
cwd ruling adds M72-M74.
M10 and M18 are re-targeted at `_argv_pin_is_exact`.

The first rev-7 run had **2 survivors**, which were fixed before the final run:

- **M61** (the npm env prefix matched case-sensitively) survived because an upper-case
  key still hit the case-insensitive family prefix. The control test now injects
  `NPM_CONFIG_UPDATE_NOTIFIER`, which is allowlisted only after npm's normalisation.
- **M66** (the URL guard deleted) survived because PEP 508 forbids a specifier beside a
  URL, so the single-`==` rule refuses a URL requirement too. The guard is kept for
  readability, and the mutant is stated as the inverted guard (`return True`).

Rev 6 (for the record): 56 of 56 mutants red on the revision-6 spike (`959d4d4` + patch), each run against the 153-test file, restored from the pristine copy and `cmp`-checked (`restored=True` for every row, no traceback in the log). Revision 6 adds M41-M56 for board round 4 (C1: M41, M42, M45-M51, M54-M56; C2: M43, M53; NB-1: M44; NB-2: M52) and re-targets three mutants at the new per-launcher `_is_exact_pin`: M10 (exactness not consulted), M17 (npm branch always exact) and M40 (npm branch on the bare grammar). Revision 5 had 40 of 40 red on the revision-5 spike (`959d4d4` + patch). M34/M35 were re-targeted at the plan's own layers now that the version rule lives on main, and M40 is new: each makes pin code use the bare SemVer grammar (`matches_package_version_grammar`) instead of main's `is_valid_package_version`. **M35 restores revision 3's order in the split** (a bare-grammar version accepted before the file check) and is red on `semver-tarball-prerelease`, `-TAR`, `-build`, `-scoped`, `-npm10-tar-gz`, `oversized-core-is-a-tag` and the health test. M25 (the split's selector clause only) stays red for the letter-led tarballs. Board revision 4 had 39 of 39. Earlier rounds: 33 of 33 on the board-revision-3 spike (M1-M14 and M17-M24 re-run; M15/M16 rewritten for the new grammar; M25-M33 new for board round 2), each for the named reason. **M26 (bare-tarball clause) is red on `bare-tarball-tgz`, `bare-tarball-TAR` and `tarball-name-with-version`.** After each run the file was restored
from the spike copy, and `cmp` confirmed it identical (`restored=True`).

**A finding from the first full-suite run (fixed in the diff above).** The first spike
made `update_server` a wrapper and moved the long docstring onto the inner
`_update_server_unwarned`. `tests/test_tools.py::test_update_server_docstring_states_both_probe_window_env_contracts`
went red (`1 failed, 4107 passed`), because it asserts that `GatewayTools.update_server.__doc__`
states both probe-window environment contracts. The fix, which is what the diff shows,
keeps the full contract docstring on the **public** `update_server`, adds the #294
paragraph to it, and gives the inner body a one-line pointer docstring. **Implementers
must not move that docstring.**


## Acceptance criteria

- [ ] `tests/test_version_pin.py` passes: 493 tests (rev 22), Verification step 2.
- [ ] A user overlay line `server_version: {firecrawl: "3.25.5"}` makes
  `load_manifest().servers["firecrawl"].args == ["-y", "firecrawl-mcp@3.25.5"]`, every
  `install` argv equal to `["npx", "-y", "firecrawl-mcp@3.25.5"]`, and
  `provision_gate._config_runs_exactly(entry, "firecrawl-mcp@3.25.5")` true. Proven by
  `test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns`.
- [ ] Every non-exact pin (the 16 parametrized values) leaves the entry's
  `args`/`install` byte-identical to shipped, with `version is None`, and logs a WARNING
  naming `server_version`. Proven by `test_server_version_refuses_anything_but_one_exact_version`.
- [ ] `gateway.update_server` on a pinned server returns `ok=False`, runs no probe, and
  returns `pinned_version="3.25.5"`, `latest_available="3.26.0"`,
  `latest_comparison="newer"`, with a message containing both
  `is pinned to '3.25.5' in the manifest entry` and `newer available: 3.26.0`.
  `pmcp update` renders it as `[PINNED] firecrawl: pinned at 3.25.5, newer available: 3.26.0`.
  Proven by `test_update_server_reports_a_newer_version_for_a_pinned_server` and
  `test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed`.
- [ ] `gateway.health` returns exactly one warning for a relaxer-active, unpinned (or
  `@latest`) client, and `[]` when the relaxer is inactive, when the client is pinned,
  or when a configured entry pins it. `gateway.update_server` carries the same warning
  without being blocked. Proven by the five `test_health_*` tests and
  `test_update_server_carries_the_unpinned_self_hosted_warning`.
- [ ] CI gates are clean (`ruff check`, `ruff format --check`, `mypy src/`), and the
  whole suite shows no failure that is not also present on `9ca081e`
  (Verification steps 3 and 5).

- [ ] Board revision 2: a non-plain npx slot (alias, URL, git, file, range) is never
  pinned; a range or tag warns and reports `[FLOATING]`; a malformed overlay entry
  costs only its own pin (`len(servers) == shipped + 1`); an inherited relaxer warns
  while `credential_requirement(...).required` stays `True`; and health loads the
  manifest once per source change. Proven by the tests named in the Revision 2 table.

- [ ] Board revision 3: no slot that npa classifies as file, directory, git, remote, alias,
  range or invalid is ever pinned. That includes every tarball form, bare or after `@`,
  in any case (`test_version_refuses_a_slot_that_is_not_a_plain_registry_spec`, 25 ids,
  plus Verification step 10: 0 violations). Both fingerprint components have tests. With
  npm identity disabled, the warning still fires or says it cannot verify. A configured
  `+metadata` pin is labelled with what npm runs.

- [ ] Board revision 22 (round 20): no credential-shaped value in an argv, a
  requirement, an env value or a cwd reaches a warning, an update message or a log; every
  problem branch (found by `ast`) is exercised with a sentinel, and every interpolation in
  the parser and judge is a closed grammar, the renderer or a checked text. Shipped cost 0
  of 77. Proven by the tests and mutants M159-M165.

- [ ] Board revision 21 (round 19): a uv interpreter request is refused on the parsed
  requirement name as well as on uv's own text reading (whitespace included); no argv real
  uv runs as an interpreter (880 measured cells × 3 forms) is read as a pin; `[PINNED]` and
  `pinned_version` come only from the judge. Shipped cost 0 of 77. Proven by the tests and
  mutants M156-M158.

- [ ] Board revision 20 (round 18): a uv interpreter request (`python==3.10`, `--from
  python==3.10`, `uv tool run python==3.10`, `cpython`/`pypy`/`graalpy`/`pyodide`, any case,
  any version spelling) is "cannot verify", never an exact pin or `[PINNED]`. Shipped cost
  0 of 77 (0 of 510 shipped argvs affected). Proven by the tests and mutants M151-M155.

- [ ] Board revision 19 (round 17): the relaxer decision and the per-member judge read one
  `_judge_members` result; any member problem (argv, env key, container env key, cwd,
  launcher identity) is "may talk", naming it. Shipped cost 0 of 77. Proven by the tests
  and mutants M148-M150.

- [ ] Board revision 18 (round 16): for a relaxer-bearing server, any spawn member the
  one parser cannot fully read makes the warning apply as "may talk", naming the member;
  only fully read members are judged by their env. Shipped cost 0 of 77. Proven by the
  tests and mutants M146, M147 (and the re-targeted M132, M135).

- [ ] Board revision 17 (round 15): the container env decides the relaxer only for a
  docker member that passed the per-member judge; a bare `docker` the entry can redirect
  (entry `PATH`, entry cwd under a cwd-searching `PATH`) is judged on its process env too.
  Shipped cost 0 of 77. Proven by the tests and mutants M143-M145.

- [ ] Board revision 16 (round 14): whether the warning applies is judged per spawn
  member from the one parser's reading (the container env only for a clean docker run
  launched as docker itself; "may" for a docker argv pmcp cannot fully read; the member's
  own env otherwise), and it applies if any member applies. Shipped cost 0 of 77. Proven
  by the tests and mutants M131, M132, M134-M142.

- [ ] Board revision 15 (round 13): docker's cwd exemption holds only for the bare
  `docker` spelling; launcher paths are never resolved in pmcp's process; Windows `.cmd`
  argvs refuse cmd metacharacters; docker's relaxer is the container's env. Shipped cost
  0 of 77. Proven by the tests and mutants M127-M133.

- [ ] Board revision 14 (round 12): a relative launcher path is never the launcher, on
  any platform. docker with an entry-set cwd is "cannot verify" when the child's `PATH`
  searches that cwd. Shipped cost 0 of 77. Proven by the tests and mutants M124-M126.

- [ ] Board revision 13 (round 11): whether the warning applies rests on the shipped
  relaxers (∪ the overlay's) in all three places, so an overlay cannot switch it off by
  dropping the key. A modelled launcher must be spelled as itself: the bare platform
  name, or the child `PATH`'s resolution. The input sweep is recorded in the Revision 13
  section. Shipped cost 0 of 77. Proven by the tests and mutants M119-M123.

- [ ] Board revision 12 (round 10): a command that is neither a modelled launcher nor a
  runner is a local binary (pin waived) only if pmcp's shipped manifest names that exact
  command for the server. Every other command, and every known launcher name with an
  unstripped extension, is "cannot verify" and named. Shipped cost 0 of 77. Proven by
  the 94 generated wrapper × launcher ids and mutants M116-M118.

- [ ] Board revision 11 (round 9): one parser per launcher is the only reader of a
  spawning argv in this feature. A repeated single-valued flag is "cannot verify", and
  `npm exec` flags after the spec are loud. One judge checks every spawn member's shape,
  pin, env and cwd before any verdict; the local-binary exemption waives only the pin.
  Every argv in a diagnostic is redacted by the one renderer. A malformed member or an
  exception is contained to its server's own "cannot verify". The AST spawn-site test
  resolves aliases, counts references per site, and finds all 22 synthetic variants.
  `_spawn_set` is built from `_SERVER_SPAWN_SITES`. Shipped cost 0 of 77. Proven by the
  tests and mutants M108-M115 (and the re-targeted set) in the Revision 11 table.

- [ ] Board revision 10 (round 8): the warning computes the whole spawn set (args, and for
  a manifest server every install argv) before any verdict. The local-binary exemption
  holds only when every member is the entry's own argv. `test_every_spawn_site_is_classified`
  fails on any unclassified spawn call site in `src/pmcp`. Shipped cost 0 of 77. Proven by
  the tests and mutants M104-M107 in the Revision 10 table.

- [ ] Board revision 9 (round 7): for a manifest-sourced server, every non-empty install
  argv runs the same exact pin in a recognised shape with an inert env, or the warning
  and `update_server` say which argv does not. Only the shipped manifest's declarations
  exempt a key, so an overlay declaring `OPENSSL_CONF`/`TARGET_CC` gets "cannot verify".
  `docker run -it` and `npx --yes=true` are recognised. Shipped cost is 0 of 77. Proven by
  the tests and mutants M96-M103 named in the Revision 9 table.

- [ ] Board revision 8 (round 6; its declared-key rule replaced by revision 9): both entry rules are allowlists. An exact pin is
  silent only when the argv matches a recognised shape in which the pinned package runs
  (Revision 8 table), every key of the entry's env block is proven inert (declared server
  keys outside tool namespaces, locale/terminal keys, the per-launcher
  logging/timing/credential lists; no value special-cased), and no configuration-bearing
  cwd is set. Unmodelled runners and wrappers are "cannot verify". `uv tool run` is judged
  as uvx. `update_server` never labels an unrecognised shape `[PINNED]`. Shipped cost is
  0 of 77. Proven by the tests and mutants M75-M95 named in the Revision 8 table.

- [ ] Board revision 7 (round 5, trust boundary; its denylists replaced by revision 8): an exact pin is silent only when the
  entry itself sets nothing that can change what runs. This covers its env block, per
  launcher (npm `npm_config_*`/`NODE_*`/`PREFIX`, uv `UV_*`/`PIP_*`, cargo
  `CARGO_*`/`RUSTC*`, docker `DOCKER_*`, and `PATH`/`HOME`/proxy/CA for every launcher,
  outside the small allowlists), its uvx/cargo argv flags, and a `cwd` it sets (npm/uv/cargo;
  docker exempt). The same settings exported
  by the **host**, host npmrc files, a local project and shims are trusted and stay silent,
  and the README states that boundary. A uvx URL requirement is never exact. A malformed
  docker digest is labelled as such. An argv of a read launcher that pmcp cannot read
  fails loud. Proven by the tests and mutants named in the Revision 7 table.

- [ ] Board revision 6 (round 4; host-reading parts superseded by revision 7): an exact npm argv is silent only when nothing besides
  the argv can change what npm runs for it. A redirecting `npm_config_*` variable (any
  case), `NODE_OPTIONS`, a redirecting overlay key, a local prefix, or a redirecting key in
  the project, user, global or builtin npmrc gives "cannot verify" naming the source, in
  both identity modes. dev0's `npm_config_cache` and `npm_config_store_dir` stay silent.
  A docker tag is never exact (warning, `floating_selector`, `[FLOATING]`), while a digest
  is (`[PINNED]`). A launcher that identity does not read is named as the cause. The
  floating text claims only what holds on every npm release. Proven by the tests named in
  the Revision 6 table and mutants M41-M56.

- [ ] Board revision 4 / rev 5: no pin value or pinned argv is a version npm reads as a
  local tarball or as a tag, because the pin grammar uses main's `is_valid_package_version`
  and `NPM_FILE_TYPE_RE`. The generated npm conformance run (1,617,455 slots and 41,472
  pin values) has **0 violations on both npm-package-arg 12.0.2 and 13.0.2** (Verification
  step 10). An `npm exec` launch without
  identity warns rather than going silent, and every fallback names its actual cause.

## Non-goals (explicit)

- **The host's own launcher configuration (trust boundary, revision 7).** The warning
  judges only what a manifest entry or overlay controls: its argv, its launcher, and the
  env block it injects. The following are the **operator's trusted environment**, like
  `PATH`, and are out of scope:
  - npmrc files at any level (project, user, global, builtin), and a local
    `package.json`/`node_modules` above the working directory;
  - the shell environment pmcp itself runs in, including a host-exported
    `npm_config_*`, `UV_*` or `CARGO_*` variable;
  - version-manager shims (asdf, Volta, mise, corepack, wrapper scripts);
  - the npx cache (`_npx/<hash>`, which npm does not re-verify) and the global bin
    directory;
  - proxy and CA settings (`HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`, ...);
  - uv configuration (`uv.toml`, `pyproject.toml` `[tool.uv]`, `pip.conf`) and cargo
    configuration (`~/.cargo/config.toml`, `[source]` replacement);
  - docker daemon and registry-mirror configuration.

  Revision 8 adds three statements. (a) A docker digest pins the **image**, not what runs
  in it; the warning therefore requires the recognised `docker run ... image@digest`
  shape with nothing after the image. (b) Package runners and wrappers pmcp does not
  model (`bunx`, `pnpx`, `pnpm`/`yarn dlx`, `uv run`, `node`, shells, `env`) are "cannot
  verify". Any other command, such as a locally installed server binary, is **not
  judged**: what version that binary is belongs to the host. (c) An operator's own
  config that injects undeclared application keys next to a pin gets "cannot verify"
  (R13). That is the price of an allowlist.

  Two scope notes. pmcp's **own** cwd, which a child inherits when its entry sets none,
  is the host's. A `cwd` that an entry **sets** is not: it chooses which project
  configuration npm, uv or cargo reads, so an exact pin with an entry-set `cwd` is
  "cannot verify" (docker excepted). A pin holds the **top-level** package only:
  dependencies resolve fresh from their ranges. Revision 6 read part of this list, and
  round 5 (B1) showed that such discovery can be fooled by a shim. It was removed rather
  than extended.
- **Proposals 3 and 4.** These are follow-up slices with design notes above.
- **Pinning a shipped entry in `manifest.yaml`.** A pin shipped for firecrawl would pin
  vendor-hosted users too (see Q1).
- **uvx/pip/cargo/docker `version:`.** Refused with a message pointing at the existing
  explicit-args pin (D5).
- **Merge semantics for `servers:`.** Whole-entry replace is unchanged (Q2).
- **Refreshing the descriptions cache version for a pinned server.** `refresher.py`
  (around line 305 and 340) labels a server's generated descriptions with
  `get_package_version`, which is the registry's **latest**, even when the argv is
  pinned. The tools are listed from the pinned binary but labelled with latest. This is
  pre-existing for `.mcp.json` pins, and this plan makes it more common. The freshness
  short-circuit then regenerates on every upstream release, which costs work but gives
  no wrong answer to `catalog_search`. It is listed as risk R2 and left for a follow-up.
- **Exit codes.** `pmcp update` stays exit 0 for per-server outcomes (Q3).
- **`cmd /c npx` Windows wrappers** (`context7`'s windows install). Refused, consistent
  with `_config_runs_exactly`.

## Risks

- **R1: a pin to a version that does not exist.** It is not caught at load, because
  load is offline by design (D1). The spawn fails loudly (npx: `No matching version
  found`), and `update_server` reports `latest_available` next to the bad pin. This is
  acceptable, and better than a network call in `load_manifest`.
- **R2: the descriptions-cache version label** (see Non-goals). It wastes regeneration
  work, and nothing reads it as the running version (#150 removed the notices that did).
- **R3: `pmcp init` snapshots pinned args.** `cli.py` (around line 1703) writes
  `server.args` into the generated `.mcp.json`. With a manifest pin active, the generated
  entry carries `pkg@X` explicitly, and a later `server_version` change no longer
  reaches it, because explicit args win. That is the same snapshot semantics `init`
  already has for `@latest` entries. The README subsection should say it in one line.
- **R4: health cost (superseded by revision 2, measured).** Now one manifest load per source change, and no config I/O: 1.3 ms steady (was 220.7 ms). Original text follows.  It is one `load_manifest()` per `gateway.health` call. When a
  relaxer-declaring server is in the list, it adds one `load_configs` and one resolver
  query per such server (~0.5 ms each, `npm_resolver.py:460`). There is no config I/O
  when no entry declares a relaxer. The work is wrapped so a failure can never cost
  health its answer.
- **R5: the warning's judgement is only as good as `credential_requirement`.** A
  self-hosted backend that is selected by some *other* variable (not declared in
  `api_key_optional_when`) is not detected. That is deliberate: the relaxer declaration
  is the manifest's only structured statement that "this entry can be self-hosted".
- **R6: the `_OverlayDocument` 4-tuple.** Any out-of-tree caller that unpacks the
  3-tuple breaks. There are none in-tree (grep), and it is a private name.

- **R20 (rev 13; widened in rev 14): a server under a name pmcp does not ship.** A server
  defined only in an overlay, or only in `.mcp.json`/`.pmcp.json`, has no shipped relaxer
  to fall back on. If it declares none, the warning cannot apply to it.
- **R22 (rev 17): a relaxer baked into a docker image's own `ENV` is not seen.** pmcp
  models the container env from the argv (`-e`, `--env`, pflag clusters) and does not call
  docker to read the image's config, so an image built with `ENV FIRECRAWL_API_URL=...` is
  judged as if the URL were unset. Pin the image by digest regardless.
- **R21 (rev 13; narrowed in rev 18): self-hosting is detected only through the relaxer
  variables.** A self-hosted URL passed to the client another way (a CLI flag in its args,
  a config file) does not make the warning apply. This is the manifest's only structured
  statement that an entry can be self-hosted (see R5). A relaxer variable set **through
  the environment by a command pmcp cannot fully read** (`env K=V`, `sh -c`, `npx -c`, a
  wrapper, a non-docker container runtime, docker by another path) **is** covered since
  revision 18: any such member makes the warning apply as "may talk".
- **R18 (rev 12; widened in revs 18 and 19): on a relaxer-bearing server, any member with
  a judge problem warns, even vendor-hosted.** Only a command pmcp's shipped manifest names
  is exempt from the pin, and since revision 19 any judge problem makes the relaxer "may
  talk". Measured over 17 legitimate vendor-hosted `firecrawl` setups (`operator_cost19.py`),
  **13 warn** (10 on revision 18):
  - an absolute npx that is not the child `PATH`'s (macOS GUI clients);
  - `cmd /c npx` (Windows Claude Desktop);
  - a global `firecrawl-mcp`;
  - `node …/dist/index.js`;
  - `bunx`;
  - `pnpm dlx`;
  - `mise exec -- npx`;
  - `npx --package=`;
  - `npx.cmd` on Linux;
  - docker `--env-file`;
  - and, new in revision 19, an entry `HTTPS_PROXY`, `FIRECRAWL_RETRY_MAX_ATTEMPTS` or
    `NODE_OPTIONS=--max-old-space-size`.

  These are **advisory warnings on relaxer-bearing servers only** (today `firecrawl`), and
  nothing fails or refuses. Shipped cost is 0 of 77. To silence one: bare `npx` (or the
  exact path `which` returns) with an exact pin, and no extra entry env beyond the declared
  keys and locale.
- **R19 (rev 12): main's unknown-package message names the raw command line.** It is
  outside this plan's change (`tests/test_tools.py` asserts it names `npm run mcp`), so
  the redaction claim covers this feature's diagnostics only. Follow-up: render it.
- **R17 (rev 11): the warning reads argvs with pmcp's own parsers, not npm identity.**
  Each parser accepts only a narrow recognised shape and refuses a repeated
  single-valued flag. Inside that shape, the launcher's reading is the same token, so no
  second reader is needed. Anything outside it is "cannot verify". The cost is that a
  harmless spelling pmcp has not modelled warns until it is modelled.
- **R14 (rev 9) is withdrawn by revision 11:** the listed spellings are now read.
- **R15 (rev 10): the adopted process's recorded config is re-read at handoff.**
  `_finalize_server_ready` records `manifest_server_to_config(load_manifest()...)` for the
  process `start_install` spawned earlier. An overlay edit during the install window can
  make health judge a config other than the one that ran, until the next restart. This is
  pre-existing and needs an edit mid-install. Follow-up: snapshot the `ServerConfig` at
  `start_install` and adopt with it.
- **R16 (rev 10): the descriptions refresh runs the manifest argv.** For a configured
  server, `refresh_server` spawns the manifest's `command`/`args`, with no entry env, to
  list tools. It is not adopted and never serves, so it cannot move the served client;
  it can cache descriptions from another version (R2).
- **R13 (rev 9 restatement): only the shipped manifest's declarations exempt a key.** An
  overlay or config that injects an application key warns "cannot verify" unless pmcp's
  shipped entry of that name declares it. An overlay-only self-hosted server always warns,
  because its relaxer is in its env. Shipped cost: 0 of 77. The remedy for an operator is
  to ask for the key to be declared in pmcp's manifest (a reviewed change), or to accept
  the advisory warning.
- **R14 (rev 9): known false positives from main's parser.** `docker container run`,
  `uvx -qq`, `cargo install -fq` and `cargo install fc@1.2.3` warn although they are
  harmless, because `detect_package_type` does not read those spellings. This fails
  closed, and the README names the spellings to use instead.
- **R13 (rev 8): the env allowlist warns on undeclared application keys.** A config
  that injects, for example, `FIRECRAWL_RETRY_MAX_ATTEMPTS` next to its pin gets "cannot
  verify", because pmcp cannot prove a key it does not know is inert for the launcher.
  Measured cost on the shipped manifest: **0 of 77** pinnable entries (the shipped
  manifest injects no `extra_env` at all; the only relaxer entry, `firecrawl`, declares
  its keys). The remedy is to declare the key on the entry, or to accept the advisory
  warning.
- **R8-R10 (rev 6) are superseded by R11 (revision 7).** They are kept for the record.
- **R11 (rev 7): the trust boundary is a stated limit, not a gap.** A host that redirects
  its own launcher (an npmrc, a shim, a cache, a proxy, uv/cargo config) is not warned
  about. The README says so. The warning's claim is scoped to "what the server's config
  controls".
- **R12 (rev 7): the entry allowlists are keyed to npm 10/11, uv 0.12 and current cargo.**
  A new harmless key an entry sets is loud until reviewed. That errs toward "cannot
  verify".
- **R8 (rev 6): the npm context check covers npm only.** For uvx/pip and cargo, pmcp does
  not read the package index configuration (`UV_INDEX_URL`, `pip.conf`, a cargo
  `[source]` replacement), so their exact pins are judged on the argv alone. This is a
  named limit, not a claim of equivalence: a follow-up could apply the same
  "what else can redirect" question to those launchers.
- **R9 (rev 6): the global and builtin npmrc are located best effort.** They are found
  from the child's `PATH` (`node` for the global prefix, the launcher for the npm root), as
  npm finds them. If neither binary is on that `PATH`, those two files are not read. The
  user file, the project file, every environment variable and the local prefix do not
  depend on `PATH`. Cost: per health call and per connected exact-pinned self-hosted npm
  server, two `which` lookups and up to four small file reads.
- **R10 (rev 6): the allowlist is keyed to npm 10 and 11.** A key a later npm defines is
  loud until it is reviewed and added. That errs toward "cannot verify", never toward
  silence.

- **R7 (rev 2): the fingerprint is `stat`-based.** An edit that preserves both mtime_ns
  and size is not seen until the next change. For health's advisory list that is
  acceptable; `update_server` always re-reads.

## Open questions for the maintainer

- **Q1.** Should the shipped `firecrawl` entry pin a default version? This plan says
  **no**: vendor-hosted users should keep following the vendor, and the self-hosted
  operator is warned (D7) and pins with one line.
- **Q2.** Should a partial `servers:` entry (only `version:`) be allowed to merge? This
  plan says **no** and uses `server_version:` instead, because changing `servers:` from
  replace to merge changes every existing overlay's meaning.
- **Q3.** Should `pmcp update --all` exit nonzero when any server FAILED? This is
  unchanged here: today it is always 0, and `[PINNED]` is not a failure either way.
- **Q4.** Should the health warning also fire for a server that exists **only** in
  `.pmcp.json`, with no manifest entry? Without a manifest entry there is no
  `api_key_optional_when` declaration, so there is no structured way to know it is
  self-hosted. This plan says no.
- **Q5.** Should `context7`'s windows install (`cmd /c npx ...`) be normalised so that
  context7 becomes pinnable? That would also touch `_config_runs_exactly`'s notion of an
  npx argv, so it is out of scope here.

## Execution Policy

- execute: effort=high, reason=security-adjacent argv rewriting (npm identity, overlay
  consent, credential-gate reads); every invariant has a named test and mutant.

## Handoff

- Branch: implement on a fresh worktree from `origin/main`. Apply the production diff
  and the test file verbatim, add the three doc edits, and run Verification 0-7.
- Commit subject: `feat(manifest): first-class client version pins and an unpinned
  self-hosted warning (see Consiliency/pmcp#294)`. The body references
  Consiliency/pmcp#294 and must not use a closing keyword, because proposals 3 and 4
  remain open.
- PR: cross-vendor panel CR plus reconcile before merge (repo rule).

## Appendix: mutation driver (`mutants.py`; revision 22 runs it in a spike worktree off origin/main `7680445`, with pristine copies of the spike files under `src22/`)

```python
"""Apply one mutant at a time to the spike, run the pin tests, restore, cmp.

Each mutant is (id, [(file, old, new), ...], description): one or more exact
one-occurrence replacements, applied together (M35 spans two files).
"""
import filecmp, shutil, subprocess, sys
from pathlib import Path

WT = Path("/home/viperjuice/workspace/worktrees/pmcp-294-rev22-spike")  # the rev-22 spike, off origin/main 7680445
S = Path(sys.argv[0]).parent
SPIKE = S / "src22"  # pristine copies of the spike files
L, H, C, V = (
    "src/pmcp/manifest/loader.py",
    "src/pmcp/tools/handlers.py",
    "src/pmcp/cli.py",
    "src/pmcp/validation.py",
)
MUTANTS = [
    ("M1", [(L, 'is_valid_package_version(raw) and "+" not in raw:', 'raw and "+" not in raw:')], "grammar accepts any string"),
    ("M2", [(L, "install[platform] = [argv[0], *pinned_install[0]]", "install[platform] = argv")], "install argv not pinned"),
    ("M3", [(L, 'return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name', 'return [*args[:index], f"{arg}@{version}", *args[index + 1 :]], name')], "existing tag not replaced"),
    ("M4", [(L, 'version=_parse_version_pin(name, data.get("version"), "version"),', "version=None,")], "servers: version: key ignored"),
    ("M5", [(L, "                    log_refusal(decision, logger)\n                    continue", "                    log_refusal(decision, logger)\n                    content = overlay_path.read_bytes()")], "unapproved project overlay applied"),
    ("M6", [(L, "    if not _is_npx(server.command):\n        return refuse(", "    if False:\n        return refuse(")], "non-npx command accepted"),
    ("M7", [(L, "        if pinned_install is None or pinned_install[1] != package:", "        if pinned_install is None:")], "install may name another package"),
    ("M8", [(H, "compare_versions(pinned_to, latest_available, package_type)", "compare_versions(latest_available, pinned_to, package_type)")], "comparison arguments swapped"),
    ("M9", [(H, "    if relaxed_by is not None:\n        head = f\"'{server_name}' talks", "    if True:\n        head = f\"'{server_name}' talks")], "relaxer not required for the warning"),
    ("M11", [(H, "                resolved = connected.get(info.name)\n", "                resolved = manifest_server_to_config(relaxable[info.name])\n")], "health judges the manifest, not the connected config"),
    ("M12", [(H, "        if warning:\n            result.warnings.append(warning)\n        return result", "        return result")], "update_server drops the warning"),
    ("M13", [(C, "    elif pinned:\n", "    elif False:\n")], "CLI keys the status off ok"),
    ("M14", [(H, "        self._attach_version_pin_warnings(servers)\n", "")], "health never attaches warnings"),
    ("M15", [(L, "        return name, selector\n    return None\n", "        return name, selector\n    return name, selector\n")], "P1: any selector accepted (alias/url/git/file/dir/range)"),
    ("M16", [(L, "    if _TAG_WORD_RE.fullmatch(selector) and not", "    if False and not")], "P1: dist-tag slots refused"),
    ("M17", [(H, '    if package_type == "npm":\n        return is_valid_package_version(pin)\n', '    if package_type == "npm":\n        return True\n')], "P2: any npm selector counts as exact"),
    ("M19", [(L, "name: _materialize_version_pin_soft(entry) for", "name: _materialize_version_pin(entry) for")], "P3: materialisation not contained per entry"),
    ("M20", [(H, "    process_env = sanitized_subprocess_env(member.env, project_root)", "    process_env = dict(member.env or {})")], "F2: inherited env ignored"),
    ("M21", [(H, "        if cached is not None and cached[0] == key:", "        if False:")], "F3: no cache"),
    ("M22", [(L, '        stat(Path.home() / ".pmcp" / "manifest.yaml"),\n', "")], "F3: fingerprint misses the user overlay"),
    ("M23", [(L, ' and "+" not in raw:', ":")], "N2: build metadata accepted"),
    ("M24", [(C, "    if floating:\n", "    if False:\n")], "CLI labels a range [FAILED]"),
    ("M25", [(L, "    if _NPM_FILE_TYPE_RE.search(selector):\n        return None\n", "")], "B1: selector file check removed from split"),
    ("M26", [(L, '    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):\n        return None\n', "")], "B1: bare-tarball / tarball-NAME slot accepted"),
    ("M27", [(L, " and not _PARTIAL_VERSION_WORD_RE.fullmatch(\n        selector\n    ):", ":")], "N4: x/X/v1.2.x range words accepted as tags"),
    ("M28", [(L, "    if _TAG_WORD_RE.fullmatch(selector) and not", "    if _TAG_WORD_RE.match(selector) and not")], "N4: match instead of fullmatch (trailing newline)"),
    ("M31", [(H, '            if exact and package_type in ("npm", "cargo") and "+" in pinned_to:', "            if False:")], "N3: label keeps +metadata"),
    ("M32", [(L, "    parts.append(stat(project) if project is not None else None)", "    parts.append(None)")], "N1: fingerprint misses the project overlay"),
    ("M33", [(L, "        parts.append(stat(trust_store_path()))", "        parts.append(None)")], "N1: fingerprint misses the trust store"),
    # --- board round 3 -------------------------------------------------------
    ("M34", [(L, 'if isinstance(raw, str) and is_valid_package_version(raw) and "+" not in raw:', 'if isinstance(raw, str) and matches_package_version_grammar(raw) and "+" not in raw:'), (L, "from pmcp.validation import (\n", "from pmcp.validation import (\n    matches_package_version_grammar,\n")], "B1': pin value checked with the bare SemVer grammar instead of main's npm-aware is_valid_package_version"),
    ("M35", [(L, "    if _NPM_FILE_TYPE_RE.search(selector):\n        return None\n    if is_valid_package_version(selector):\n        return name, selector\n",
            "    if matches_package_version_grammar(selector):\n        return name, selector\n    if _NPM_FILE_TYPE_RE.search(selector):\n        return None\n"), (L, "from pmcp.validation import (\n", "from pmcp.validation import (\n    matches_package_version_grammar,\n")], "B1': the REV-3 ORDER restored in the split (bare SemVer accepted before the file check)"),
    ("M40", [(H, '    if package_type == "npm":\n        return is_valid_package_version(pin)\n', '    if package_type == "npm":\n        return matches_package_version_grammar(pin)\n'), (H, "    is_valid_package_version,\n    normalized_executable_name,\n", "    is_valid_package_version,\n    matches_package_version_grammar,\n    normalized_executable_name,\n")], "B1': _is_exact_pin trusts the bare SemVer grammar"),
    ("M36", [(L, '    if name.lower() in _NPM_EXCLUDED_NAMES:\n        return None\n', "")], "N-c: excluded names accepted"),
    ("M37", [(L, 'r"[vV=]*(?:[0-9]+|[xX*])', 'r"[vV]?(?:[0-9]+|[xX*])')], "N-a: only one leading v"),
    # --- board round 4 (rev 6) ---------------------------------------------
    ("M43", [(H, '    if package_type == "npm":\n        return is_valid_package_version(pin)\n    if package_type == "docker":', '    if is_valid_package_version(pin):\n        return True\n    if package_type == "npm":\n        return is_valid_package_version(pin)\n    if package_type == "docker":')], "C2: the generic SemVer check restored ahead of the launcher branches"),
    ("M51", [(H, '        return package.lower().startswith(f"{scope.lower()}/")', "        return False")], "C1: a matching @scope:registry counted harmless"),
    ("M52", [(H, '    return "not one exact version on every npm release"', '    return "a range or tag, not one exact version"')], "NB-2: the old floating label"),
    ("M53", [(H, "        return _DOCKER_DIGEST_RE.fullmatch(pin) is not None", '        return pin.startswith("sha256:")')], "C2: any sha256: prefix counted a digest"),
    # --- board round 5 (rev 7): the entry-controlled boundary ----------------
    # (rev-6 M41, M42, M45-M50, M54-M56 are retired with the host discovery)
    ("M69", [(H, "        if _DOCKER_DIGEST_LIKE_RE.fullmatch(pin):", "        if False:")], "N5: a malformed digest labelled a tag"),
    # --- rev 7, maintainer ruling: an entry-set cwd is entry-controlled -------
    # --- board round 6 (rev 8): allowlists for the entry's env and argv shapes --
    # (rev-7 M58-M65, M70, M71 are retired with the denylist code they mutated)
    ("M75", [(H, "            _CARGO_CREDENTIAL_KEY_RE.fullmatch(upper)\n        )\n    return False\n", "            _CARGO_CREDENTIAL_KEY_RE.fullmatch(upper)\n        )\n    return True\n")], "ENV allowlist: an unknown key counted inert"),
    ("M77", [(H, "    if key in declared:\n        return True\n", "")], "ENV: the server's declared keys not inert"),
    ("M78", [(H, "    if upper in _ENTRY_ENV_INERT_KEYS or upper.startswith(_ENTRY_ENV_INERT_PREFIXES):", "    if False:")], "ENV: locale/terminal keys not inert"),
    ("M79", [(H, "        return not _npm_key_can_redirect(_npm_env_config_key(key), package)", "        return True")], "ENV: every npm_config_* key counted inert"),
    ("M80", [(H, "        return upper in _UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(", "        return True or bool(")], "ENV: every key counted inert for uv"),
    ("M81", [(H, "        return upper in _CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(", "        return True or bool(")], "ENV: every key counted inert for cargo"),
    ("M87", [(H, '    if not args or args[0] != "install":', "    if False:")], "SHAPE cargo: a +toolchain / non-install argv not refused up front"),
    # --- board round 7 (rev 9): every spawning argv; shipped declarations only ----
    # (rev-8 M76, the namespace-guard mutant, is retired with the guard)
    ("M103", [(H, '_NPX_INERT_FLAGS = frozenset({"-y", "--yes", "--yes=true", "-q", "--quiet"})', '_NPX_INERT_FLAGS = frozenset({"-y", "--yes", "-q", "--quiet"})')], "N2: npx --yes=true not modelled"),
    # --- board round 8 (rev 10): the whole spawn set before any exemption -----
    # --- rev 11: every surviving rule re-targeted at the ONE parser/judge ------
    # (M29, M30, M38, M39, M44 are retired: the warning no longer reads npm
    # identity, so the identity-refusal causes they mutated are gone)
    ("M10", [(H, '    if args_reading.family != "local":\n        if args_reading.selector is None:', '    if False:\n        if args_reading.selector is None:')], "pin not consulted for the warning"),
    ("M18", [(H, '                if verdict.kind == "silent" and verdict.reading.family != "local":', '                if True:')], "P2: update reports a non-pin as pinned"),
    ("M57", [(H, "    if pin_state is None and args_problems:\n", "    if False:\n")], "C1/B2: an exact argv suppresses whatever the entry sets"),
    ("M66", [(H, "        if requirement.url:\n", "        if False:\n")], "N5: a uvx URL requirement read as its fragment's version"),
    ("M68", [(H, "    for key in sorted(str(k) for k in (member.env or {})):", "    for key in sorted(str(k) for k in ({**os.environ, **(member.env or {})})):")], "trust boundary: the HOST environment judged too"),
    ("M72", [(H, "    if member.cwd and reading.family in _CWD_CONFIG_FAMILIES:", "    if False:")], "cwd: an entry-set cwd ignored"),
    ("M73", [(H, '    "local": "a local command",\n', '    "local": "a local command",\n    "docker": "docker",\n')], "cwd: docker counted cwd-sensitive"),
    ("M74", [(H, "    if member.cwd and reading.family in _CWD_CONFIG_FAMILIES:", "    if (member.cwd or os.getcwd()) and reading.family in _CWD_CONFIG_FAMILIES:")], "cwd: the inherited (host) cwd judged too"),
    ("M82", [(H, '        if arg.startswith("-"):\n            flag = diag_flag(_split_flag(arg)[0])\n            return _Reading("npm", launcher, problem=f"its argv passes {flag}"), index', '        if arg.startswith("-"):\n            continue')], "SHAPE npx: an unknown flag counted inert"),
    ("M84", [(H, '            return _Reading("pypi", "uvx", problem=f"its argv passes {diag_flag(name)}")', "            index += 1\n            continue")], "SHAPE uvx: an unknown flag counted inert"),
    ("M85", [(H, "        if from_req is not None and _pep503_name(arg) != _pep503_name(requirement.name):", "        if False:")], "SHAPE uvx: --from with another command counted the package"),
    ("M86", [(H, '            return _Reading(\n                "cargo", "cargo", problem=f"its argv passes {diag_flag(name)}"\n            )', "            index += 1\n            continue")], "SHAPE cargo: an unknown flag counted inert"),
    ("M88", [(H, "        if index + 1 < len(rest):", "        if False:")], "SHAPE docker: a container command after the image counted inert"),
    ("M89", [(H, '            else:\n                return _Reading(\n                    "docker",\n                    "docker",\n                    problem=f"its argv passes {diag_flag(name)}",\n                    env_file=name == "--env-file",\n                )', "            else:\n                index += 1\n                continue")], "SHAPE docker: an unknown flag (--entrypoint, -v...) counted inert"),
    ("M90", [(H, "    for key in reading.env_keys:", "    for key in ():")], "SHAPE docker: -e KEY not judged"),
    ("M91", [(H, "    if launcher in _UNMODELLED_RUNNERS:", "    if False:")], "N2: an unmodelled runner or wrapper silent (read as a local binary)"),
    ("M92", [(H, '    if launcher == "uv" and args[:2] == ["tool", "run"]:', "    if False:")], "N2: `uv tool run` not judged as uvx"),
    ("M93", [(H, '                    if verdict.reading.selector == pinned_to:', "                    if True:")], "B1/X1: update_server labels a pin the judge did not read [PINNED]"),
    ("M94", [(H, "    if args_reading.problem is not None:\n        return _Verdict(\"cannot_verify\", args_reading, args_reading.problem)\n", "")], "X1: the verdict ignores the args' shape"),
    ("M95", [(H, "        if not _entry_env_key_is_inert(reading.family, key, \"\", package, declared):", "        if False:")], "ENV: the entry's env block not judged"),
    ("M96", [(H, "    declared = _declared_env_keys(server_name)\n", "    declared = frozenset(k for k in (manifest_server.env_var, *manifest_server.api_key_optional_when) if k)\n")], "B2: an overlay declaration exempts a key"),
    ("M97", [(H, "        if reading.problem is not None:\n            return _Verdict(\n                \"cannot_verify\",\n                args_reading,\n                f\"{where} runs something pmcp cannot read ({reading.problem})\",\n            )\n", "")], "B1: an unreadable install argv counted fine"),
    ("M99", [(H, "        if (reading.family, reading.package, reading.selector) != (", "        if False and (reading.family, reading.package, reading.selector) != (")], "B1: an install argv may name another package/version"),
    ("M101", [(H, "        if problems:\n            return _Verdict(", "        if False:\n            return _Verdict(")], "B1: install argv env/cwd not judged"),
    ("M102", [(H, "    for position, letter in enumerate(cluster):\n", "    if len(cluster) > 1 and 'e' not in cluster:\n        return None, False, 'x'\n    for position, letter in enumerate(cluster):\n")], "N2: combined docker short flags not modelled"),
    ("M104", [(H, "            if member.argv != args_member.argv:", "            if False:")], "B-1: the local-binary exemption skips the other spawn members"),
    ("M105", [(H, '        resolved.source != "manifest"', '        True')], "B-1: install argvs left out of the spawn set"),
    ("M106", [(H, "    for member, reading, problems in judged[1:]:", "    for member, reading, problems in judged[2:]:")], "the spawn set judged only in part"),
    ("M107", [(H, '    "manifest/installer.py:JobManager.start_install": _install_members,\n', "")], "a spawning site dropped from _SERVER_SPAWN_SITES (drops its members)"),
    # --- board round 9 (rev 11) -------------------------------------------------
    ("M108", [(H, '            if name == "--from" or name in _UVX_INERT_VALUE_FLAGS:\n                if name in seen:', '            if name == "--from" or name in _UVX_INERT_VALUE_FLAGS:\n                if False:')], "grok B1: a repeated --from read (last-wins guess) instead of refused"),
    ("M109", [(H, '            key = "--version" if name in ("--version", "--vers") else name\n            if key == "--version" or name in _CARGO_INERT_VALUE_FLAGS:\n                if key in seen:', '            key = "--version" if name in ("--version", "--vers") else name\n            if key == "--version" or name in _CARGO_INERT_VALUE_FLAGS:\n                if False:')], "grok B1: a repeated cargo --version accepted"),
    ("M110", [(H, "            elif name in _DOCKER_INERT_VALUE_FLAGS:\n                if name in seen:", "            elif name in _DOCKER_INERT_VALUE_FLAGS:\n                if False:")], "grok B1: a repeated docker single-valued flag accepted"),
    ("M111", [(H, "    if reading.problem is None and rest and rest[0] != \"--\":", "    if False:")], "npm exec: flags after the package ignored"),
    ("M112", [(H, "    pin_state: str | None = None\n    if args_reading.family != \"local\":", "    if args_reading.family == \"local\":\n        return _Verdict(\"silent\", args_reading)\n    pin_state: str | None = None\n    if args_reading.family != \"local\":")], "grok B2: the local-binary exemption waives env/cwd too"),
    ("M113", [(H, "    return _render_install_argv([str(part) for part in argv])", "    return \" \".join(str(part) for part in argv)")], "codex 1: argvs rendered raw in diagnostics"),
    ("M114", [(H, "    if not argv or not all(isinstance(part, str) for part in argv):", "    if not argv:")], "codex 2: a malformed argv element not refused"),
    ("M115", [(H, "            except Exception as exc:\n                warning = _unverifiable_warning(info.name, exc)", "            except Exception:\n                raise")], "codex 2: one server's failure escapes its containment"),
    # --- board round 10 (rev 12): positive evidence for the local exemption --
    ("M116", [(H, '    if words[0] in local_commands:\n        return _Reading("local", launcher)\n    return _Reading(\n        "unrecognised",', '    return _Reading("local", launcher)\n    return _Reading(\n        "unrecognised",')], "B-1: an unrecognised command treated as a local binary"),
    ("M117", [(H, "    if stem in _MODELLED_LAUNCHERS or stem in _UNMODELLED_RUNNERS:", "    if False:")], "B-1: a launcher with an unstripped extension (npx.js) may be local"),
    ("M118", [(H, "    local_commands = _shipped_local_commands(server_name)\n", "    local_commands = frozenset({str(resolved.config.command)})\n")], "B-1: the entry's own command taken as evidence that it is local"),
    # --- board round 11 (rev 13) --------------------------------------------
    ("M119", [(H, "    keys = list(_shipped_manifest_relaxers().get(server_name, ()))", "    keys: list[str] = []")], "relaxer read from the overlay only"),
    ("M120", [(H, "        if manifest_server is None or not _warning_relaxers(\n            server_name, manifest_server\n        ):", "        if manifest_server is None or not manifest_server.api_key_optional_when:")], "update wrapper: relaxer read from the overlay only"),
    ("M121", [(H, "            if _warning_relaxers(name, server)\n", "            if server.api_key_optional_when\n")], "health filter: relaxer read from the overlay only"),
    ("M122", [(H, "    if launcher in _MODELLED_LAUNCHERS and not _launcher_spelled_as_itself(", "    if False and not _launcher_spelled_as_itself(")], "a launcher judged by its basename"),
    ("M123", [(H, "        return resolved is not None and command == resolved", "        return True")], "any path named like the launcher counts"),
    # --- board round 12 (rev 14) --------------------------------------------
    ("M124", [(H, "        if not os.path.isabs(command):\n            return False\n", "")], "a relative launcher path accepted (equal to a relative PATH search)"),
    ("M125", [(H, "            or _path_searches_the_cwd(path_var)\n", "            or False\n")], "docker with an entry cwd silent while PATH searches the cwd"),
    ("M126", [(H, '        not entry or not os.path.isabs(entry) or entry.startswith("/proc/")', '        not entry or entry.startswith("/proc/")')], "a relative PATH entry not counted as searching the cwd"),
    # --- board round 13 (rev 15) --------------------------------------------
    ("M127", [(H, '            not _is_bare_launcher(str(member.argv[0]), "docker")\n            or _path_searches_the_cwd(path_var)', "            _path_searches_the_cwd(path_var)")], "B-1: docker's cwd exemption for a path spelling"),
    ("M128", [(H, "        return resolved is not None and command == resolved", "        return resolved is not None and os.path.realpath(command) == os.path.realpath(resolved)")], "B-1: a launcher path resolved in pmcp's process (realpath)"),
    ("M129", [(H, '        not entry or not os.path.isabs(entry) or entry.startswith("/proc/")', "        not entry or not os.path.isabs(entry)")], "a /proc PATH entry not counted as cwd-dependent"),
    ("M130", [(H, "        and not all(_CMD_SAFE_ARG_RE.fullmatch(arg) for arg in args)", "        and False")], "N-1: cmd.exe metacharacters accepted for a .cmd launcher"),
    ("M133", [(H, "    if _is_windows():\n        return False\n    entries =", "    if _is_windows():\n        return True\n    entries =")], "N-3: Windows treated as searching the entry's cwd"),
    # --- board round 14 (rev 16): the relaxer per spawn member, by the one parser --
    ("M131", [(H, "        return first_usable(container), False\n", "        return first_usable({**process_env, **container}), False\n")], "N-2: docker's own env used for the relaxer, not the container's"),
    ("M132", [(H, "        if reading.problem is not None:\n            return None, bool(relaxers)\n", "        if reading.problem is not None:\n            return None, False\n")], "an unreadable docker argv (--env-file) not treated as 'may'"),
    ("M134", [(H, "for j in judged]\n", "for j in judged[:1]]\n")], "B-1: relaxer from the args member only"),
    ("M135", [(H, '    if reading.family == "docker":\n        if reading.problem is not None:', '    if normalized_executable_name(str(member.argv[0])) == "docker":\n        if reading.problem is not None:')], "B-1: docker (no own env) decided by basename"),
    ("M136", [(H, "    for key, value in reading.container_env:\n", "    for key, value in [(a.partition('=')[0], a.partition('=')[2]) for a in (str(x) for x in member.argv) if '=' in a and not a.startswith('-')]:\n")], "B-1: a second parser reads the container env"),
    ("M137", [(H, "            if attached:\n                return attached, False, None\n", "            if attached:\n                return None, False, None\n")], "B-1 row 3: a value attached to a shorthand cluster ignored"),
    ("M138", [(H, "                return attached[1:], False, None\n", "                return attached, False, None\n")], "B-1 row 3: pflag's leading '=' kept (-e=K=V)"),
    ("M139", [(H, "            container.pop(key, None)\n", "            pass\n")], "a later bare -e KEY does not unset (last one wins)"),
    ("M140", [(H, "    relaxed_by = next((key for _j, key, _may in members if key is not None), None)\n", "    relaxed_by = members[-1][1] if all(key is not None for _j, key, _may in members) else None\n")], "B-1: applies only when EVERY member applies"),
    ("M141", [(H, "        if not relaxers:\n            return None\n        return (\n            f\"'{server_name}' may talk", "        return None\n        return (\n            f\"'{server_name}' may talk")], "a failure deciding whether the warning applies reads as 'does not apply'"),
    ("M142", [(H, '        if "-" + letter not in _DOCKER_INERT_BOOLEAN_FLAGS:\n', "        if False:\n")], "B-1 row 3: any letter accepted in a docker shorthand cluster"),
    # --- board round 15 (rev 17): the container env only after the member judge --
    ("M143", [(H, "        if problems:\n            return (\n", "        if False:\n            return (\n")], "B-1: the container env used despite member problems"),
    ("M144", [(H, "            _spawn_set(manifest_server, resolved), declared, local_commands, path_var\n", "            _spawn_set(manifest_server, resolved), frozenset(), local_commands, path_var\n")], "the relaxer's member judge run without the shipped declarations"),
    ("M145", [(H, "                first_usable(process_env) or first_usable(container),\n", "                first_usable(container),\n")], "B-1: a docker member with problems judged on its container env only"),
    # --- board round 16 (rev 18): an unreadable member may talk to the backend --
    ("M146", [(H, "    return first_usable(process_env), bool(problems) and bool(relaxers)\n", "    return first_usable(process_env), bool(judged.problems) and bool(relaxers)\n")], "B-1: an unreadable non-docker member judged by its process env"),
    ("M147", [(H, "        if reading.problem is not None:\n            return None, bool(relaxers)\n", "        if reading.problem is not None:\n            return first_usable(process_env), bool(relaxers)\n")], "an unreadable docker member's own env taken as the container's"),
    # --- board round 17 (rev 19): any member problem makes the relaxer may-talk --
    ("M148", [(H, "    problems = judged.all_problems\n", "    problems = (judged.reading.problem,) if judged.reading.problem is not None else ()\n")], "B-1: the relaxer ignores env/cwd problems"),
    ("M149", [(H, "    if judged.reading.env_file:\n", "    if False:\n")], "N-3: the --env-file reason loses its specific wording"),
    ("M150", [(H, "        verdict = _verdict_of(judged)\n", "        verdict = _judge_spawn_set([j.member for j in judged], frozenset(), local_commands, path_var)\n")], "a second path: the verdict recomputes member problems apart from the relaxer's"),
    # --- board round 18 (rev 20): uv interpreter requests are never a pin ------
    ("M151", [(H, "        interpreter = _uv_interpreter_request(requirement_text) or (\n            parsed_name if parsed_name in _UV_INTERPRETER_PREFIXES else None\n        )\n", "        interpreter = None\n")], "B1: an interpreter name read as a package"),
    ("M152", [(H, "    lowered = component.lower()\n", "    lowered = component\n")], "B1: interpreter names matched case-sensitively"),
    ("M153", [(H, '    "pythonw",\n    "python",\n    "cpython",\n    "pypy",\n    "graalpy",\n    "pyodide",\n)\n', '    "pythonw",\n    "python",\n)\n')], "B1: only python, not the implementation names"),
    ("M154", [(H, "        if rest and _UV_VERSION_REQUEST_RE.fullmatch(rest):\n", "        if rest and False:\n")], "B1: a version request after the name ignored"),
    ("M155", [(H, 'r"\\s*(?:@.*|[vV]?[0-9][^-]*|[=<>~!][^-]*)"', 'r"\\s*(?:@.*|[vV]?[0-9].*|[=<>~!].*)"')], "a '-' suffix (python3-openid) read as an interpreter"),
    # --- board round 19 (rev 21): the parsed name; [PINNED] only from the judge --
    ("M156", [(H, "            parsed_name if parsed_name in _UV_INTERPRETER_PREFIXES else None\n", "            None\n")], "B1: the parsed requirement name not checked"),
    ("M157", [(H, 'r"\\s*(?:@.*|[vV]?[0-9][^-]*|[=<>~!][^-]*)"', 'r"(?:@.*|[vV]?[0-9][^-]*|[=<>~!][^-]*)"')], "B1: whitespace after the name not skipped"),
    ("M158", [(H, "            exact = judge_pin is not None and shape_problem is None\n", "            exact = pinned_to is not None and not str(shape_problem or '').startswith('evaluating')\n")], "[PINNED] from main's separate pin reader, not the judge"),
    # --- board round 20 (rev 22): every judge diagnostic through a closed grammar or the renderer --
    ("M159", [(H, '                    f"uv reads this {which} as an interpreter request "\n                    f"({diag_name(interpreter)}), not a package"\n', '                    f"uv reads {__import__(\'pmcp.manifest.installer\', fromlist=[\'_\'])._operator_safe(requirement_text)!r} as an "\n                    "interpreter request, not a package"\n')], "B1: the interpreter diagnostic quotes the raw requirement (rev 21)"),
    ("M160", [(H, '            "the entry sets a cwd (not shown), whose project configuration "\n', '            f"the entry sets cwd {member.cwd!r}, whose project configuration "\n')], "the cwd path shown raw"),
    ("M161", [(H, 'problem=f"its argv selects toolchain +{diag_name(args[0][1:])}",', 'problem=f"its argv selects toolchain +{args[0][1:]}",')], "the cargo toolchain shown raw"),
    ("M162", [(L, "    return text if _DIAG_SELECTOR_RE.fullmatch(text) else _DIAG_REDACTED\n", "    return text\n")], "diag_selector admits anything"),
    ("M163", [(L, "    return text if _DIAG_NAME_RE.fullmatch(text) else _DIAG_REDACTED\n", "    return text\n")], "diag_name admits anything"),
    ("M164", [(L, "    return text if _DIAG_FLAG_RE.fullmatch(text) else _DIAG_REDACTED\n", "    return text\n")], "diag_flag admits anything"),
    ("M165", [(H, 'r"\\s*(?:@.*|[vV]?[0-9][^-]*|[=<>~!][^-]*)"', 'r"\\s*(?:@.*|[0-9][^-]*|[=<>~!][^-]*)"')], "N1: a `v`-prefixed version request not read as an interpreter"),
]
only = set(sys.argv[1:])
for mid, edits, desc in MUTANTS:
    if only and mid not in only:
        continue
    touched = []
    headers = []
    try:
        for rel, old, new in edits:
            path = WT / rel
            text = path.read_text()
            assert text.count(old) == 1, (mid, rel, old)
            path.write_text(text.replace(old, new, 1))
            touched.append(rel)
            diff = subprocess.run(["diff", str(SPIKE / rel), str(path)], capture_output=True, text=True).stdout.splitlines()
            headers.append(f"{Path(rel).name}:{diff[0] if diff else 'NO DIFF'}")
        r = subprocess.run(
            ["uv", "run", "pytest", "tests/test_version_pin.py", "-q", "--cov-fail-under=0", "--tb=line", "-p", "no:cacheprovider"],
            cwd=WT, capture_output=True, text=True, timeout=600,
        )
        out = r.stdout.splitlines()
        summary = out[-1] if out else r.stderr[-300:]
        fails = [l for l in out if l.startswith("FAILED")]
        elines = [l for l in out if "Error" in l or l.startswith("E ")][:1]
    finally:
        for rel in touched:
            shutil.copyfile(SPIKE / rel, WT / rel)
    same = all(filecmp.cmp(SPIKE / rel, WT / rel, shallow=False) for rel, _o, _n in edits)
    print(f"{mid} | {desc} | applied {' '.join(headers)} | {summary} | restored={same}")
    for f in fails:
        print(f"    {f}")
    for e in elines:
        print(f"    first: {e[:220]}")
```

## Appendix: board-round-4 reproductions (`repro_c1.py`, `repro_c2.py`; revision 6)

Run from a spike worktree with `uv run python <script> <scratch> <case>`, with
`PYTHONPATH` pointing at a tree that holds the revision under test. `<scratch>` holds
`localpkg/` (a `firecrawl-mcp@0.0.1` package whose bin prints `LOCAL firecrawl-mcp 0.0.1`),
`nodeproj/package.json`, `fakehome/.npmrc` and `rcdir/.npmrc` (both `package=file:<scratch>/localpkg`).

```python
"""C1/NB-1 repro: real resolver, the warning function under test, several contexts."""
import os, sys
from pathlib import Path
from pmcp.manifest.loader import ServerConfig
from pmcp.tools.handlers import _unpinned_self_hosted_warning
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
from pmcp.manifest.npm_resolver import get_resolver
R = Path(sys.argv[1])
case = sys.argv[2]
P = ["mac", "linux", "wsl", "windows"]
srv = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={p: ["npx", "-y", "firecrawl-mcp"] for p in P},
    command="npx", args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
    api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
env = {"FIRECRAWL_API_URL": "http://ai:3002"}
cmd, args, cwd = "npx", ["-y", "firecrawl-mcp@3.25.5"], None
if case == "overlay-package":
    env["npm_config_package"] = f"file:{R}/localpkg"
elif case == "overlay-registry":
    env["NPM_CONFIG_REGISTRY"] = "http://127.0.0.1:9/"
elif case == "gateway-package":
    os.environ["npm_config_package"] = f"file:{R}/localpkg"
elif case == "cwd-prefix":
    cwd = str(R / "nodeproj")
elif case == "npx.cmd":
    cmd = "npx.cmd"
elif case == "abs-npx":
    cmd = "/usr/bin/npx"
elif case == "plain":
    pass
elif case == "dev0-cache":
    os.environ["npm_config_cache"] = "/tmp/x-cache"; os.environ["npm_config_store_dir"] = "/tmp/x-store"
elif case == "user-npmrc":
    os.environ["HOME"] = str(R / "fakehome")
elif case == "cwd-npmrc":
    cwd = str(R / "rcdir")
elif case == "npx.cmd-unpinned":
    cmd = "npx.cmd"; args = ["-y", "firecrawl-mcp"]
if case.startswith("warm-"):
    # start the child first with an ordinary lookup, so status reads "active"
    from pmcp.manifest.version_checker import detect_package_type
    print("warmup:", detect_package_type("npx", ["-y", "left-pad"], None, None))
    if case == "warm-overlay-package":
        env["npm_config_package"] = f"file:{R}/localpkg"
    if case == "warm-npx.cmd":
        cmd = "npx.cmd"
resolved = ResolvedServerConfig(name="firecrawl", source="user", config=LocalMcpServerConfig(command=cmd, args=args, env=env, cwd=cwd))
w = _unpinned_self_hosted_warning("firecrawl", srv, resolved, None)
print(f"{case}: status={get_resolver().status_summary()!r}\n  warning={w!r}")
```

```python
"""C2/NB-2 repro on the tree under test."""
from pmcp.manifest.loader import ServerConfig
from pmcp.tools.handlers import _is_exact_pin, _unpinned_self_hosted_warning
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
for t, p in [("docker", "3.25.5"), ("docker", "latest-3"), ("docker", "sha256:" + "a" * 64), ("pypi", "1.0.0-x.tgzx"), ("cargo", "1.0.0-x.tgz"), ("npm", "3.25.5")]:
    print(f"_is_exact_pin({t!r}, {p!r}) = {_is_exact_pin(t, p)}")
P = ["mac", "linux", "wsl", "windows"]
def srv(cmd, args):
    return ServerConfig(name="c", description="x", keywords=["x"], install={}, command=cmd, args=args, requires_api_key=True,
        env_var="C_KEY", api_key_optional_when=["C_URL"], extra_env={})
for cmd, args in [("docker", ["run", "--pull=always", "example/client:3.25.5"]), ("docker", ["run", "example/client@sha256:" + "a" * 64]),
                  ("npx", ["-y", "firecrawl-mcp@1.0.0-x.tar-gz"]), ("npx", ["-y", "firecrawl-mcp@9007199254740992.0.0"])]:
    r = ResolvedServerConfig(name="c", source="user", config=LocalMcpServerConfig(command=cmd, args=args, env={"C_URL": "http://h:1"}))
    print(cmd, args[-1], "->", _unpinned_self_hosted_warning("c", srv(cmd, args), r, None))
```

## Appendix: board-round-5 reproduction (`repro_r7.py`; revision 7)

Run one case per process with `PYTHONPATH` pointing at a tree that holds the revision under
test, e.g. `PYTHONPATH=<tree> python repro_r7.py "uvx-entry-UV_OVERRIDE (B2)"`.

```python
"""Round-5 findings: the warning on the tree under test, one case per process.

usage: repro_r7.py <case>. Host settings go into os.environ (the gateway's own
environment); entry settings go into the resolved config's env block.
"""
import os, sys
from pmcp.manifest.loader import ServerConfig
from pmcp.tools.handlers import _unpinned_self_hosted_warning
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

CASES = {
    # name: (command, args, entry_env, host_env)
    "npm-entry-package": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"npm_config_package": "file:/srv/local"}, {}),
    "npm-host-package": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {}, {"npm_config_package": "file:/srv/local"}),
    "npm-entry-cache (N1)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"npm_config_cache": "/srv/shared"}, {}),
    "npm-entry-https-proxy (N3)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"HTTPS_PROXY": "http://p.test:3128"}, {}),
    "npm-host-https-proxy (N3)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {}, {"HTTPS_PROXY": "http://p.test:3128"}),
    "uvx-entry-UV_OVERRIDE (B2)": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {"UV_OVERRIDE": "/srv/ovr.txt"}, {}),
    "uvx-host-UV_OVERRIDE (B2)": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {}, {"UV_OVERRIDE": "/srv/ovr.txt"}),
    "uvx-entry-UV_INDEX_URL (B2)": ("uvx", ["cowsay==6.1"], {"UV_INDEX_URL": "http://evil.test/simple"}, {}),
    "uvx-argv---index-url (B2)": ("uvx", ["--index-url", "http://evil.test/simple", "cowsay==6.1"], {}, {}),
    "cargo-argv---git (B2)": ("cargo", ["install", "--git", "https://example.test/evil", "--version", "1.2.3", "fc"], {}, {}),
    "cargo-entry-CARGO_REGISTRIES_X_INDEX (B2)": ("cargo", ["install", "fc", "--version", "1.2.3"], {"CARGO_REGISTRIES_X_INDEX": "https://m.test"}, {}),
    "uvx-url-requirement (N5)": ("uvx", ["--from", "firecrawl-py @ https://example.test/firecrawl_py-9.9.9-py3-none-any.whl#x==1.0.0", "fc"], {}, {}),
    "docker-uppercase-digest (N5)": ("docker", ["run", "example/client@sha256:" + "B" * 64], {}, {}),
    "docker-unknown-flag (N5)": ("docker", ["run", "--some-future-flag", "example/client:3.25.5"], {}, {}),
    "uvx-unknown-flag (N5)": ("uvx", ["--some-future-flag", "cowsay==6.1"], {}, {}),
    "npm-plain (control)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {}, {}),
    "uvx-plain (control)": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {}, {}),
}
name = sys.argv[1]
command, args, entry_env, host_env = CASES[name]
os.environ.update(host_env)
server = ServerConfig(name="c", description="c", keywords=["c"], install={}, command=command, args=args,
                      requires_api_key=True, env_var="C_KEY", api_key_optional_when=["C_URL"], extra_env={})
resolved = ResolvedServerConfig(name="c", source="user", config=LocalMcpServerConfig(
    command=command, args=args, env={"C_URL": "http://self-hosted:3002", **entry_env}))
w = _unpinned_self_hosted_warning("c", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-6 reproduction and shipped cost (`repro_r8.py`, `shipped_cost.py`; revision 8)

Run each with `PYTHONPATH` pointing at a tree that holds the revision under test, e.g.
`PYTHONPATH=<tree> python repro_r8.py "X1 docker cmd after image"` and
`MANIFEST=src/pmcp/manifest/manifest.yaml PYTHONPATH=<tree> python shipped_cost.py`.

```python
"""Round-6 findings: the warning on the tree under test, one case per process.
usage: repro_r8.py <case>. Entry settings go into the resolved config (argv, env, cwd).
npm identity is read with the node-less tables so the result is host-independent."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
D = "sha256:1de022d8aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CASES = {
    "X1 docker cmd after image": ("docker", ["run", "--rm", "--network", "host", f"node@{D}", "npx", "-y", "semver", "--help"], {}),
    "X1 docker --entrypoint": ("docker", ["run", "--entrypoint", "/bin/sh", f"node@{D}", "-c", "npx -y firecrawl-mcp"], {}),
    "X1 docker -v": ("docker", ["run", "-v", "/srv/evil:/app", f"node@{D}"], {}),
    "X1 docker -e NODE_OPTIONS": ("docker", ["run", "-e", "NODE_OPTIONS=--require=/x", f"node@{D}"], {}),
    "X1 npx -p X sh -c": ("npx", ["-y", "-p", "semver@7.6.0", "sh", "-c", "echo RAN-SH"], {}),
    "X1 uvx --from X sh -c": ("uvx", ["--from", "cowsay==6.1", "sh", "-c", "echo RAN-SH"], {}),
    "X2 uvx entry XDG_CONFIG_DIRS": ("uvx", ["cowsay==6.1"], {"XDG_CONFIG_DIRS": "/srv/xdg3"}),
    "X2 uvx entry XDG_CACHE_HOME": ("uvx", ["cowsay==6.1"], {"XDG_CACHE_HOME": "/srv/cache"}),
    "N1 npx entry PATH=''": ("npx", ["-y", "semver@7.6.0"], {"PATH": ""}),
    "N2 uv tool run (unpinned)": ("uv", ["tool", "run", "cowsay"], {}),
    "N2 bunx": ("bunx", ["semver@7.6.0"], {}),
    "N2 pnpm dlx": ("pnpm", ["dlx", "semver@7.6.0"], {}),
    "N2 sh -c npx": ("sh", ["-c", "npx -y semver@7.6.0"], {}),
    "N3 uvx --python": ("uvx", ["--python", "/srv/python", "cowsay==6.1"], {}),
    "N3 cargo +nightly": ("cargo", ["+nightly", "install", "fc", "--version", "1.2.3"], {}),
    "N4 npx entry LD_PRELOAD": ("npx", ["-y", "semver@7.6.0"], {"LD_PRELOAD": "/srv/hook.so"}),
    "N4 cargo entry CC": ("cargo", ["install", "fc", "--version", "1.2.3"], {"CC": "/srv/cc"}),
    "control firecrawl self-hosted, declared keys": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"FIRECRAWL_API_KEY": "k"}),
    "control docker digest + declared -e": ("docker", ["run", "-i", "--rm", "-e", "FIRECRAWL_API_URL", f"node@{D}"], {}),
    "control uvx --from X X": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {}),
}
name = sys.argv[1]
command, args, env = CASES[name]
server = ServerConfig(name="c", description="c", keywords=["c"], install={}, command=command, args=args,
                      requires_api_key=True, env_var="FIRECRAWL_API_KEY", api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
resolved = ResolvedServerConfig(name="c", source="user", config=LocalMcpServerConfig(
    command=command, args=args, env={"FIRECRAWL_API_URL": "http://self-hosted:3002", **env}))
w = h._unpinned_self_hosted_warning("c", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Shipped-coverage cost: for every shipped manifest entry that a `version:` pin can
reach (step 7's 77), pin it at 1.0.0, build the config the gateway would spawn, and ask
the tree under test whether an EXACT pin there is silent. npm identity is read with the
node-less tables, so the result does not depend on the host's npm."""
import inspect, os
from dataclasses import replace
from pathlib import Path
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest import version_checker as vc
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
import logging; logging.disable(logging.WARNING)
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
m = load_manifest(Path(os.environ["MANIFEST"]))
sig = inspect.signature(h._entry_redirect).parameters
pinned = loud = 0; relaxer_loud = []; reasons = {}
for name, s in m.servers.items():
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    cfg = manifest_server_to_config(p).config
    ptype, pkg = vc.detect_package_type(cfg.command, list(cfg.args), cfg.env, cfg.cwd)
    kw = dict(package_type=ptype, command=cfg.command, args=list(cfg.args), package=pkg,
              config_env=cfg.env)
    if "pin" in sig: kw["pin"] = "1.0.0"
    if "cwd" in sig: kw["cwd"] = cfg.cwd
    if "declared" in sig: kw["declared"] = h._declared_env_keys(p)
    r = h._entry_redirect(**kw)
    if r is not None:
        loud += 1; reasons[name] = r
        if p.api_key_optional_when: relaxer_loud.append(name)
print(f"{h.__file__}\n  pinnable {pinned}; exact pin NOT silent for {loud}; of those with a relaxer: {relaxer_loud}")
for n, r in sorted(reasons.items())[:10]:
    print("   ", n, "->", r[:140])
```

## Appendix: board-round-7 reproduction and shipped cost (`repro_r9.py`, `shipped_cost.py`; revision 9)

Run with `PYTHONPATH` pointing at a tree that holds the revision under test. `shipped_cost.py`
now also judges every install argv, with shipped-only declarations when the tree has them.

```python
"""Round-7 findings: the warning on the tree under test, one case per process.
usage: repro_r9.py <case>. The entry is a manifest entry (source "manifest"), so its
install argvs are spawning argvs; npm identity is read with the node-less tables."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import ServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
P = ["mac", "linux", "wsl", "windows"]
D = "sha256:" + "a" * 64
FC = dict(env_var="FIRECRAWL_API_KEY", api_key_optional_when=["FIRECRAWL_API_URL"])
CASES = {
    # name: (server name, command, args, install, env_var, relaxers, extra_env)
    "B1 args pinned, linux install unpinned": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"],
        {"linux": ["npx", "-y", "firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B1 install via sh -c": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"],
        {"linux": ["sh", "-c", "npx -y firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B2 overlay env_var OPENSSL_CONF": ("overlay-only", "npx", ["-y", "semver@7.6.0"], None,
        "OPENSSL_CONF", ["FIRECRAWL_API_URL"], {"OPENSSL_CONF": "/srv/evil.cnf"}),
    "B2 overlay relaxer OPENSSL_CONF": ("overlay-only", "npx", ["-y", "semver@7.6.0"], None,
        "FIRECRAWL_API_KEY", ["OPENSSL_CONF"], {"OPENSSL_CONF": "/srv/evil.cnf"}),
    "B2 overlay env_var TARGET_CC (cargo)": ("overlay-only", "cargo", ["install", "fc", "--version", "1.2.3"], None,
        "TARGET_CC", ["FIRECRAWL_API_URL"], {"TARGET_CC": "/srv/cc"}),
    "N2 docker run -it": ("firecrawl", "docker", ["run", "-it", "--rm", f"example/client@{D}"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "N2 npx --yes=true": ("firecrawl", "npx", ["--yes=true", "firecrawl-mcp@3.25.5"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "cost: overlay-only self-hosted entry, app relaxer": ("overlay-only", "npx", ["-y", "semver@7.6.0"], None,
        "SEMVER_API_KEY", ["SEMVER_API_URL"], {"SEMVER_API_URL": "http://self-hosted:1"}),
    "control shipped firecrawl, all argvs pinned": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
}
name = sys.argv[1]
server_name, command, args, install, env_var, relaxers, extra = CASES[name]
if install is None:
    install = {p: [command, *args] for p in P}
else:
    install = {p: install.get(p, [command, *args]) for p in P}
relaxer_env = {k: "http://self-hosted:3002" for k in relaxers if k not in extra}
server = ServerConfig(name=server_name, description="x", keywords=["x"], install=install, command=command,
                      args=args, requires_api_key=True, env_var=env_var, api_key_optional_when=relaxers,
                      extra_env={**relaxer_env, **extra})
resolved = manifest_server_to_config(server)
w = h._unpinned_self_hosted_warning(server_name, server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Shipped-coverage cost (rev 9: also every install argv, and only shipped declarations): for every shipped manifest entry that a `version:` pin can
reach (step 7's 77), pin it at 1.0.0, build the config the gateway would spawn, and ask
the tree under test whether an EXACT pin there is silent. npm identity is read with the
node-less tables, so the result does not depend on the host's npm."""
import inspect, os
from dataclasses import replace
from pathlib import Path
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest import version_checker as vc
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
import logging; logging.disable(logging.WARNING)
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
m = load_manifest(Path(os.environ["MANIFEST"]))
sig = inspect.signature(h._entry_redirect).parameters
pinned = loud = 0; relaxer_loud = []; reasons = {}
for name, s in m.servers.items():
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    cfg = manifest_server_to_config(p).config
    ptype, pkg = vc.detect_package_type(cfg.command, list(cfg.args), cfg.env, cfg.cwd)
    kw = dict(package_type=ptype, command=cfg.command, args=list(cfg.args), package=pkg,
              config_env=cfg.env)
    if "pin" in sig: kw["pin"] = "1.0.0"
    if "cwd" in sig: kw["cwd"] = cfg.cwd
    if "declared" in sig:
        kw["declared"] = (h._declared_env_keys(name) if hasattr(h, "_shipped_manifest_declarations")
                          else h._declared_env_keys(p))
    r = h._entry_redirect(**kw)
    if r is None and hasattr(h, "_install_argv_problem"):
        r = h._install_argv_problem(p, ptype, pkg, "1.0.0", cfg.env, kw["declared"])
    if r is not None:
        loud += 1; reasons[name] = r
        if p.api_key_optional_when: relaxer_loud.append(name)
print(f"{h.__file__}\n  pinnable {pinned}; exact pin NOT silent for {loud}; of those with a relaxer: {relaxer_loud}")
for n, r in sorted(reasons.items())[:10]:
    print("   ", n, "->", r[:140])
```

## Appendix: board-round-8 reproduction and shipped cost (`repro_r10.py`, `shipped_cost.py`; revision 10)

```python
"""Round-8 findings (rev 10 adds the B-1 local-command cases): the warning on the tree under test, one case per process.
usage: repro_r9.py <case>. The entry is a manifest entry (source "manifest"), so its
install argvs are spawning argvs; npm identity is read with the node-less tables."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import ServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
P = ["mac", "linux", "wsl", "windows"]
D = "sha256:" + "a" * 64
FC = dict(env_var="FIRECRAWL_API_KEY", api_key_optional_when=["FIRECRAWL_API_URL"])
CASES = {
    # name: (server name, command, args, install, env_var, relaxers, extra_env)
    "B-1 command firecrawl-mcp, install.linux npx -y firecrawl-mcp": ("firecrawl", "firecrawl-mcp", [],
        {"linux": ["npx", "-y", "firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B-1 command firecrawl-mcp, install.linux uvx firecrawl-mcp": ("firecrawl", "firecrawl-mcp", [],
        {"linux": ["uvx", "firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B-1 command /opt/fc/bin/firecrawl-mcp --stdio, npx install": ("firecrawl", "/opt/fc/bin/firecrawl-mcp", ["--stdio"],
        {"linux": ["npx", "-y", "firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B-1 command firecrawl-mcp, install pinned npx": ("firecrawl", "firecrawl-mcp", [],
        {"linux": ["npx", "-y", "firecrawl-mcp@3.25.5"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "control local command, every install argv the same": ("firecrawl", "/opt/fc/bin/firecrawl-mcp", ["--stdio"],
        None, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "control shipped firecrawl, all argvs pinned": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
}
name = sys.argv[1]
server_name, command, args, install, env_var, relaxers, extra = CASES[name]
if install is None:
    install = {p: [command, *args] for p in P}
else:
    install = {p: install.get(p, [command, *args]) for p in P}
relaxer_env = {k: "http://self-hosted:3002" for k in relaxers if k not in extra}
server = ServerConfig(name=server_name, description="x", keywords=["x"], install=install, command=command,
                      args=args, requires_api_key=True, env_var=env_var, api_key_optional_when=relaxers,
                      extra_env={**relaxer_env, **extra})
resolved = manifest_server_to_config(server)
w = h._unpinned_self_hosted_warning(server_name, server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Shipped-coverage cost (rev 9: also every install argv, and only shipped declarations): for every shipped manifest entry that a `version:` pin can
reach (step 7's 77), pin it at 1.0.0, build the config the gateway would spawn, and ask
the tree under test whether an EXACT pin there is silent. npm identity is read with the
node-less tables, so the result does not depend on the host's npm."""
import inspect, os
from dataclasses import replace
from pathlib import Path
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest import version_checker as vc
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
import logging; logging.disable(logging.WARNING)
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
m = load_manifest(Path(os.environ["MANIFEST"]))
sig = inspect.signature(h._entry_redirect).parameters
pinned = loud = 0; relaxer_loud = []; reasons = {}
for name, s in m.servers.items():
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    cfg = manifest_server_to_config(p).config
    ptype, pkg = vc.detect_package_type(cfg.command, list(cfg.args), cfg.env, cfg.cwd)
    kw = dict(package_type=ptype, command=cfg.command, args=list(cfg.args), package=pkg,
              config_env=cfg.env)
    if "pin" in sig: kw["pin"] = "1.0.0"
    if "cwd" in sig: kw["cwd"] = cfg.cwd
    if "declared" in sig:
        kw["declared"] = (h._declared_env_keys(name) if hasattr(h, "_shipped_manifest_declarations")
                          else h._declared_env_keys(p))
    r = h._entry_redirect(**kw)
    if r is None and hasattr(h, "_spawn_set"):
        r = h._install_argv_problem(h._spawn_set(p, manifest_server_to_config(p)), ptype, pkg, "1.0.0", kw["declared"])
    elif r is None and hasattr(h, "_install_argv_problem"):
        r = h._install_argv_problem(p, ptype, pkg, "1.0.0", cfg.env, kw["declared"])
    if r is not None:
        loud += 1; reasons[name] = r
        if p.api_key_optional_when: relaxer_loud.append(name)
print(f"{h.__file__}\n  pinnable {pinned}; exact pin NOT silent for {loud}; of those with a relaxer: {relaxer_loud}")
for n, r in sorted(reasons.items())[:10]:
    print("   ", n, "->", r[:140])

# Rev 10: shipped entries whose command pmcp does not model (a local binary) and
# whose install argvs differ from [command, *args] -- the local-binary exemption
# would no longer apply to them (only matters for a relaxer entry).
local = []
for name, s in m.servers.items():
    if s.url or not s.command:
        continue
    launcher = h.normalized_executable_name(s.command)
    if launcher in ("npx", "npm", "uvx", "pip", "pip3", "cargo", "docker", "uv") or launcher in getattr(h, "_UNMODELLED_RUNNERS", ()):
        continue
    own = [s.command, *s.args]
    if any(argv and list(argv) != own for argv in s.install.values()):
        local.append((name, bool(s.api_key_optional_when)))
print(f"  local-command entries whose install differs: {local}")
```

## Appendix: board-round-9 reproduction and shipped cost (`repro_r11.py`, `shipped_cost.py`; revision 11)

```python
"""Round-9 findings: the warning (and, where named, update_server's label reading)
on the tree under test, one case per process. The entry is a manifest entry named
`firecrawl` with its shipped declarations and the relaxer set; npm identity is read
with the node-less tables (rev 11 no longer consults it)."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import ServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
P = ["mac", "linux", "wsl", "windows"]
T = "SYNTHETIC_REVIEW_TOKEN_0123456789"
CASES = {
    # name: (command, args, install override or None, extra_env)
    "grok B1 uvx --from evil==1.0.0 --from cowsay==6.0 cowsay": ("uvx", ["--from", "evil==1.0.0", "--from", "cowsay==6.0", "cowsay"], None, {}),
    "grok B1 uvx --from cowsay==6.1 --from cowsay==6.0 cowsay": ("uvx", ["--from", "cowsay==6.1", "--from", "cowsay==6.0", "cowsay"], None, {}),
    "grok B2 local command + NODE_OPTIONS": ("/opt/fc/bin/firecrawl-mcp", ["--stdio"], None, {"NODE_OPTIONS": "--require /srv/hook.js"}),
    "grok B2 local command + LD_PRELOAD": ("/opt/fc/bin/firecrawl-mcp", ["--stdio"], None, {"LD_PRELOAD": "/srv/hook.so"}),
    "grok B2 local command + PATH=''": ("/opt/fc/bin/firecrawl-mcp", ["--stdio"], None, {"PATH": ""}),
    "codex 1 token in install argv": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"linux": ["npx", "-y", "firecrawl-mcp", "--token", T]}, {}),
    "codex 2 install.mac: [echo, 42]": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"mac": ["echo", 42]}, {}),
    "N2 cargo install fc@1.2.3": ("cargo", ["install", "fc@1.2.3"], None, {}),
    "N2 docker container run -it img@digest": ("docker", ["container", "run", "-it", "example/client@sha256:" + "a" * 64], None, {}),
    "npm exec -y pkg@X --package=evil": ("npm", ["exec", "-y", "firecrawl-mcp@3.25.5", "--package=evil"], None, {}),
    "control shipped firecrawl, all argvs pinned": ("npx", ["-y", "firecrawl-mcp@3.25.5"], None, {}),
    "control local command, same argv everywhere, declared env": ("/opt/fc/bin/firecrawl-mcp", ["--stdio"], None, {"FIRECRAWL_API_KEY": "k"}),
}
name = sys.argv[1]
command, args, install_override, extra = CASES[name]
install = {p: [command, *args] for p in P}
install.update(install_override or {})
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install=install, command=command,
                      args=args, requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"],
                      extra_env={"FIRECRAWL_API_URL": "http://self-hosted:3002", **extra})
try:
    w = h._unpinned_self_hosted_warning("firecrawl", server, manifest_server_to_config(server), None)
except Exception as exc:
    w = f"RAISED {type(exc).__name__}: {exc}"
w = "None" if w is None else w
print(f"{name}\t{'TOKEN-LEAKED ' if T in w else ''}{w}")
```

```python
"""Shipped-coverage cost (rev 11): pin every shipped entry a `version:` pin can
reach at 1.0.0, build the config the gateway spawns, and ask the ONE judge
(`_judge_spawn_set` over `_spawn_set`, with shipped declarations) whether that
exact pin is silent. Also judge every shipped entry UNPINNED-as-shipped to count
what the judge calls "cannot verify" (as opposed to merely unpinned)."""
import collections, os
from dataclasses import replace
from pathlib import Path
import logging; logging.disable(logging.WARNING)
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
m = load_manifest(Path(os.environ["MANIFEST"]))
pinned = 0; not_silent = {}; shipped_kinds = collections.Counter()
for name, s in m.servers.items():
    if s.url or not s.command:
        continue
    v = h._judge_spawn_set(h._spawn_set(s, manifest_server_to_config(s)), h._declared_env_keys(name))
    shipped_kinds[v.kind] += 1
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    v = h._judge_spawn_set(h._spawn_set(p, manifest_server_to_config(p)), h._declared_env_keys(name))
    if v.kind != "silent":
        not_silent[name] = (v.kind, v.detail)
print(f"pinnable {pinned}; exact pin NOT silent for {len(not_silent)}: {not_silent}")
print(f"every local shipped entry as shipped (no pin): {dict(shipped_kinds)}")
```

## Appendix: board-round-10 reproduction and shipped cost (`repro_r12.py`, `shipped_cost.py`; revision 12)

```python
"""Round-10 findings (rev 12: the local-binary exemption needs positive evidence): the warning (and, where named, update_server's label reading)
on the tree under test, one case per process. The entry is a manifest entry named
`firecrawl` with its shipped declarations and the relaxer set; npm identity is read
with the node-less tables (rev 11 no longer consults it)."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import ServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
P = ["mac", "linux", "wsl", "windows"]
T = "SYNTHETIC_REVIEW_TOKEN_0123456789"
CASES = {
    'timeout 600 npx -y firecrawl-mcp': ('timeout', ['600', 'npx', '-y', 'firecrawl-mcp'], None, {}),
    'nice npx -y firecrawl-mcp': ('nice', ['npx', '-y', 'firecrawl-mcp'], None, {}),
    'stdbuf -oL npx -y firecrawl-mcp': ('stdbuf', ['-oL', 'npx', '-y', 'firecrawl-mcp'], None, {}),
    'nohup npx -y firecrawl-mcp': ('nohup', ['npx', '-y', 'firecrawl-mcp'], None, {}),
    'setsid npx -y firecrawl-mcp': ('setsid', ['npx', '-y', 'firecrawl-mcp'], None, {}),
    'sudo -E npx -y firecrawl-mcp': ('sudo', ['-E', 'npx', '-y', 'firecrawl-mcp'], None, {}),
    "busybox sh -c 'npx -y firecrawl-mcp'": ('busybox', ['sh', '-c', 'npx -y firecrawl-mcp'], None, {}),
    'mise exec -- npx -y firecrawl-mcp': ('mise', ['exec', '--', 'npx', '-y', 'firecrawl-mcp'], None, {}),
    'volta run npx -y firecrawl-mcp': ('volta', ['run', 'npx', '-y', 'firecrawl-mcp'], None, {}),
    'corepack pnpm dlx firecrawl-mcp': ('corepack', ['pnpm', 'dlx', 'firecrawl-mcp'], None, {}),
    'pipx run firecrawl-mcp': ('pipx', ['run', 'firecrawl-mcp'], None, {}),
    'python3 -m uv tool run firecrawl-mcp': ('python3', ['-m', 'uv', 'tool', 'run', 'firecrawl-mcp'], None, {}),
    'go run example.com/fc@latest': ('go', ['run', 'example.com/fc@latest'], None, {}),
    'npx.js -y firecrawl-mcp@3.25.5': ('npx.js', ['-y', 'firecrawl-mcp@3.25.5'], None, {}),
    'npx.ps1 -y firecrawl-mcp@3.25.5': ('npx.ps1', ['-y', 'firecrawl-mcp@3.25.5'], None, {}),
    'control npx -y firecrawl-mcp@3.25.5': ('npx', ['-y', 'firecrawl-mcp@3.25.5'], None, {}),
}
name = sys.argv[1]
command, args, install_override, extra = CASES[name]
install = {p: [command, *args] for p in P}
install.update(install_override or {})
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install=install, command=command,
                      args=args, requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"],
                      extra_env={"FIRECRAWL_API_URL": "http://self-hosted:3002", **extra})
try:
    w = h._unpinned_self_hosted_warning("firecrawl", server, manifest_server_to_config(server), None)
except Exception as exc:
    w = f"RAISED {type(exc).__name__}: {exc}"
w = "None" if w is None else w
print(f"{name}\t{'TOKEN-LEAKED ' if T in w else ''}{w}")
```

```python
"""Shipped-coverage cost (rev 11; rev 12 passes the shipped local commands): pin every shipped entry a `version:` pin can
reach at 1.0.0, build the config the gateway spawns, and ask the ONE judge
(`_judge_spawn_set` over `_spawn_set`, with shipped declarations) whether that
exact pin is silent. Also judge every shipped entry UNPINNED-as-shipped to count
what the judge calls "cannot verify" (as opposed to merely unpinned)."""
import collections, os
from dataclasses import replace
from pathlib import Path
import logging; logging.disable(logging.WARNING)
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
m = load_manifest(Path(os.environ["MANIFEST"]))
pinned = 0; not_silent = {}; shipped_kinds = collections.Counter()
for name, s in m.servers.items():
    if s.url or not s.command:
        continue
    v = h._judge_spawn_set(h._spawn_set(s, manifest_server_to_config(s)), h._declared_env_keys(name), *([h._shipped_local_commands(name)] if hasattr(h, "_shipped_local_commands") else []))
    shipped_kinds[v.kind] += 1
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    v = h._judge_spawn_set(h._spawn_set(p, manifest_server_to_config(p)), h._declared_env_keys(name), *([h._shipped_local_commands(name)] if hasattr(h, "_shipped_local_commands") else []))
    if v.kind != "silent":
        not_silent[name] = (v.kind, v.detail)
print(f"pinnable {pinned}; exact pin NOT silent for {len(not_silent)}: {not_silent}")
print(f"every local shipped entry as shipped (no pin): {dict(shipped_kinds)}")
```

## Appendix: board-round-11 reproduction (`repro_r13.py`; revision 13)

```python
"""Round-11 findings: the warning on the tree under test, one case per process.
Overlay cases replace the SHIPPED `firecrawl` entry (unpinned npx, FIRECRAWL_API_URL
set). Launcher cases pin `firecrawl-mcp@3.25.5` with a launcher spelled another way;
PATH is set so bare `npx` is not found anywhere odd."""
import os, sys
os.environ["PATH"] = "/usr/bin:/bin"
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import ServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
P = ["mac", "linux", "wsl", "windows"]
PIN = ["-y", "firecrawl-mcp@3.25.5"]
CASES = {
    # name: (command, args, requires_api_key, relaxers, env_var)
    "overlay drops api_key_optional_when": ("npx", ["-y", "firecrawl-mcp"], True, [], "FIRECRAWL_API_KEY"),
    "overlay requires_api_key: false": ("npx", ["-y", "firecrawl-mcp"], False, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "overlay older copy (no key, no env_var)": ("npx", ["-y", "firecrawl-mcp"], True, [], None),
    "launcher /tmp/anything/npx": ("/tmp/anything/npx", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "launcher ./npx": ("./npx", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "launcher node_modules/.bin/npx": ("node_modules/.bin/npx", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "launcher NPX": ("NPX", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "launcher C:npx": ("C:npx", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "launcher npx.cmd (on Linux)": ("npx.cmd", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
    "control npx pinned": ("npx", PIN, True, ["FIRECRAWL_API_URL"], "FIRECRAWL_API_KEY"),
}
name = sys.argv[1]
command, args, required, relaxers, env_var = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"],
                      install={p: [command, *args] for p in P}, command=command, args=args,
                      requires_api_key=required, env_var=env_var, api_key_optional_when=relaxers,
                      extra_env={"FIRECRAWL_API_URL": "http://ai:3002"})
w = h._unpinned_self_hosted_warning("firecrawl", server, manifest_server_to_config(server), None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-12 reproduction (`repro_r14.py`; revision 14)

```python
"""Round-12 findings: a configured (`.mcp.json`) docker entry with an entry-set cwd,
under the shipped `firecrawl` relaxer. One case per process; PATH is set per case."""
import os, sys, tempfile
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
D = "example/client@sha256:" + "a" * 64
deep = os.path.join(tempfile.mkdtemp(), *["d"] * 12); os.makedirs(deep)
CASES = {
    "relative ../x12/usr/bin/docker + deep cwd": ("../" * 12 + "usr/bin/docker", deep, "/usr/bin:/bin"),
    "bare docker + cwd, PATH=.:/usr/bin:/bin": ("docker", deep, ".:/usr/bin:/bin"),
    "bare docker + cwd, PATH=/usr/bin::/bin": ("docker", deep, "/usr/bin::/bin"),
    "control bare docker + cwd, PATH=/usr/bin:/bin": ("docker", deep, "/usr/bin:/bin"),
}
name = sys.argv[1]
command, cwd, path = CASES[name]
os.environ["PATH"] = path
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="npx",
                      args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command=command, args=["run", D], env={"FIRECRAWL_API_URL": "http://ai:3002"}, cwd=cwd))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-13 reproduction (`repro_r15.py`; revision 15)

```python
"""Round-13 findings: a configured docker entry under the shipped `firecrawl`
relaxer. PATH=/usr/bin:/bin (this host has /usr/bin/docker). One case per process."""
import os, sys, tempfile
os.environ["PATH"] = "/usr/bin:/bin"
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
D = "example/client@sha256:" + "a" * 64
deep = os.path.join(tempfile.mkdtemp(), *["d"] * 12); os.makedirs(deep)
URL = {"FIRECRAWL_API_URL": "http://ai:3002"}
PROC = "/proc/self/cwd/" + os.path.relpath("/usr/bin/docker", os.getcwd())
CASES = {
    # name: (command, args, entry env, cwd)
    "B-1 /proc/self/cwd/<rel>/usr/bin/docker + deep cwd, -e URL": (PROC, ["run", "-e", "FIRECRAWL_API_URL", D], URL, deep),
    "B-1 /usr/bin/docker (absolute) + deep cwd, -e URL": ("/usr/bin/docker", ["run", "-e", "FIRECRAWL_API_URL", D], URL, deep),
    "N-2 -e FIRECRAWL_API_URL=... , :latest, URL not in docker env": ("docker", ["run", "-e", "FIRECRAWL_API_URL=http://ai:3002", "example/client:latest"], {}, None),
    "N-2 --env-file, :latest": ("docker", ["run", "--env-file", "/srv/env", "example/client:latest"], {}, None),
    "N-2 URL only in docker's env (not passed), :latest": ("docker", ["run", "example/client:latest"], URL, None),
    "control bare docker digest, -e URL, cwd": ("docker", ["run", "-e", "FIRECRAWL_API_URL", D], URL, deep),
}
name = sys.argv[1]
command, args, env, cwd = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="npx",
                      args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command=command, args=args, env=env, cwd=cwd))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-14 reproduction (`repro_r16.py`; revision 16)

```python
"""Round-14 findings, under the shipped `firecrawl` relaxer. PATH=/usr/bin:/bin
(this host has /usr/bin/docker). One case per process."""
import os, sys, tempfile
os.environ["PATH"] = "/usr/bin:/bin"
os.environ.pop("FIRECRAWL_API_URL", None)
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
D = "example/client@sha256:" + "a" * 64
URL = "http://ai:3002"
fake = os.path.join(tempfile.mkdtemp(), "docker")
open(fake, "w").write("#!/bin/sh\n"); os.chmod(fake, 0o755)
NPX = {p: ["npx", "-y", "firecrawl-mcp"] for p in ("mac", "linux", "wsl", "windows")}
CASES = {
    # name: (source, command, args, entry env, host-exported URL, install)
    "row1 docker+digest args, shipped npx install, URL in entry env": ("manifest", "docker", ["run", "-i", "--rm", D], {"FIRECRAWL_API_URL": URL}, False, NPX),
    "row2 absolute fake docker, host-exported URL": ("project", fake, ["run", "-i", "--rm", "example/client:latest"], {}, True, {}),
    "row2 ./docker, host-exported URL": ("project", "./docker", ["run", "-i", "--rm", "example/client:latest"], {}, True, {}),
    "row2 DOCKER, host-exported URL": ("project", "DOCKER", ["run", "-i", "--rm", "example/client:latest"], {}, True, {}),
    "row2 docker.exe on Linux, host-exported URL": ("project", "docker.exe", ["run", "-i", "--rm", "example/client:latest"], {}, True, {}),
    "row3 -eK=V, :latest": ("project", "docker", ["run", f"-eFIRECRAWL_API_URL={URL}", "example/client:latest"], {}, False, {}),
    "row3 -ie K=V, :latest": ("project", "docker", ["run", "-ie", f"FIRECRAWL_API_URL={URL}", "example/client:latest"], {}, False, {}),
    "row3 -ieK=V, :latest": ("project", "docker", ["run", f"-ieFIRECRAWL_API_URL={URL}", "example/client:latest"], {}, False, {}),
    "row3 -ie=K=V, :latest": ("project", "docker", ["run", f"-ie=FIRECRAWL_API_URL={URL}", "example/client:latest"], {}, False, {}),
    "control clean docker, URL only in docker's env, :latest": ("project", "docker", ["run", "-i", "--rm", "example/client:latest"], {}, True, {}),
}
name = sys.argv[1]
source, command, args, env, host, install = CASES[name]
if host:
    os.environ["FIRECRAWL_API_URL"] = URL
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install=install, command=command,
                      args=args, requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source=source, config=LocalMcpServerConfig(
    command=command, args=args, env=env))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-15 reproduction (`repro_r17.py`; revision 17)

```python
"""Round-15 findings: a bare `docker` whose PATH or cwd the entry controls, under
the shipped `firecrawl` relaxer. One case per process."""
import os, sys, tempfile
os.environ["PATH"] = "/usr/bin:/bin"
os.environ.pop("FIRECRAWL_API_URL", None)
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
D = "mendable/firecrawl-mcp@sha256:" + "a" * 64
L = "mendable/firecrawl-mcp:latest"
URL = "http://ai:3002"
root = tempfile.mkdtemp()
evil = os.path.join(root, "evil", "bin"); os.makedirs(evil)
cwd = os.path.join(root, "evilcwd"); os.makedirs(cwd)
for d in (evil, cwd):
    open(os.path.join(d, "docker"), "w").write("#!/bin/sh\n"); os.chmod(os.path.join(d, "docker"), 0o755)
CASES = {
    # name: (image, entry env, entry cwd, host PATH, host URL)
    "R2 entry PATH=<evil>/bin:..., entry URL, :latest": (L, {"PATH": f"{evil}:/usr/bin:/bin", "FIRECRAWL_API_URL": URL}, None, None, False),
    "R2b the same, digest": (D, {"PATH": f"{evil}:/usr/bin:/bin", "FIRECRAWL_API_URL": URL}, None, None, False),
    "R3 entry PATH=.:..., entry cwd with ./docker, entry URL, :latest": (L, {"PATH": ".:/usr/bin:/bin", "FIRECRAWL_API_URL": URL}, cwd, None, False),
    "R1 host PATH=.:..., host URL, entry cwd with ./docker, digest": (D, {}, cwd, ".:/usr/bin:/bin", True),
    "control entry URL (declared), no -e, :latest": (L, {"FIRECRAWL_API_URL": URL}, None, None, False),
    "control host URL, no -e, :latest": (L, {}, None, None, True),
}
name = sys.argv[1]
image, env, entry_cwd, host_path, host_url = CASES[name]
if host_path:
    os.environ["PATH"] = host_path
if host_url:
    os.environ["FIRECRAWL_API_URL"] = URL
args = ["run", "-i", "--rm", image]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="docker",
                      args=args, requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command="docker", args=args, env=env, cwd=entry_cwd))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-16 reproduction and operator cost (`repro_r18.py`, `operator_cost.py`; revision 18)

```python
"""Round-16 findings: a configured entry under the shipped `firecrawl` relaxer
whose argv pmcp cannot fully read. No FIRECRAWL_API_URL in the host env or in
the entry's env block. PATH=<tmp>/bin:/usr/bin:/bin with <tmp>/bin/docker, so
`/usr/bin/docker` is not what the PATH resolves. One case per process."""
import os, sys, tempfile
os.environ.pop("FIRECRAWL_API_URL", None)
b = os.path.join(tempfile.mkdtemp(), "bin"); os.makedirs(b)
open(os.path.join(b, "docker"), "w").write("#!/bin/sh\n"); os.chmod(os.path.join(b, "docker"), 0o755)
os.environ["PATH"] = f"{b}:/usr/bin:/bin"
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
U = "FIRECRAWL_API_URL=http://ai:3002"
CASES = {
    "A env K=V npx -y firecrawl-mcp": ("env", [U, "npx", "-y", "firecrawl-mcp"]),
    "B sh -c 'K=V exec npx -y firecrawl-mcp'": ("sh", ["-c", f"{U} exec npx -y firecrawl-mcp"]),
    "C npx -y -c 'K=V firecrawl-mcp'": ("npx", ["-y", "-c", f"{U} firecrawl-mcp"]),
    "D /usr/bin/docker run -e K=V (not the PATH's docker)": ("/usr/bin/docker", ["run", "-i", "--rm", "-e", U, "mendable/firecrawl-mcp:latest"]),
    "E podman run -e K=V": ("podman", ["run", "-i", "--rm", "-e", U, "mendable/firecrawl-mcp:latest"]),
    "F env K=V npx -y firecrawl-mcp@3.25.5": ("env", [U, "npx", "-y", "firecrawl-mcp@3.25.5"]),
    "control npx -y firecrawl-mcp, no URL anywhere": ("npx", ["-y", "firecrawl-mcp"]),
    "control docker run -e K=V :latest (fully read)": ("docker", ["run", "-i", "--rm", "-e", U, "mendable/firecrawl-mcp:latest"]),
}
name = sys.argv[1]
command, args = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="npx",
                      args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command=command, args=args, env={}))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Rev 18 operator cost: shipped relaxer-bearing entries, as shipped, with NO
relaxer set anywhere -- does the new "may" rule warn? And the same entries
under a custom unreadable command (the cost the decision accepts)."""
import os
from pathlib import Path
os.environ.pop("FIRECRAWL_API_URL", None)
from pmcp.manifest.loader import load_manifest
from pmcp.tools.handlers import manifest_server_to_config
import pmcp.tools.handlers as h
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
m = load_manifest(Path(os.environ["MANIFEST"]))
relaxing = {n: s for n, s in m.servers.items() if h._warning_relaxers(n, s)}
print("shipped servers with relaxers:", sorted(relaxing))
for n, s in relaxing.items():
    r = manifest_server_to_config(s)
    w = h._unpinned_self_hosted_warning(n, s, r, None)
    print(f"  {n} as shipped, no relaxer set: {'None' if w is None else w[:90]}")
    for cmd, args in (("sh", ["-c", "exec npx -y firecrawl-mcp"]), ("/opt/bin/fc-wrapper", [])):
        c = ResolvedServerConfig(name=n, source="project", config=LocalMcpServerConfig(command=cmd, args=args))
        w = h._unpinned_self_hosted_warning(n, s, c, None)
        print(f"  {n} configured as {cmd!r}, no relaxer set: {'None' if w is None else w[:110]}")
```

## Appendix: board-round-17 reproduction and operator cost (`repro_r19.py`, `operator_cost19.py`; revision 19)

```python
"""Round-17 findings: a FULLY READ member whose env keys or cwd the judge flags,
under the shipped `firecrawl` relaxer. No FIRECRAWL_API_URL in the host env or
in the entry's env block. One case per process."""
import os, sys, tempfile
os.environ["PATH"] = "/usr/bin:/bin"
os.environ.pop("FIRECRAWL_API_URL", None)
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
NODE = "--import=data:text/javascript,process.env.FIRECRAWL_API_URL='http://ai:3002'"
D = "mendable/firecrawl-mcp@sha256:" + "a" * 64
cwd = tempfile.mkdtemp()
open(os.path.join(cwd, ".env"), "w").write("FIRECRAWL_API_URL=http://ai:3002\n")
CASES = {
    # name: (source, command, args, entry env, cwd, overlay extra_env)
    "G npx exact pin + NODE_OPTIONS --import data:": ("project", "npx", ["-y", "firecrawl-mcp@3.25.5"], {"NODE_OPTIONS": NODE}, None, {}),
    "G2 npx unpinned + NODE_OPTIONS": ("project", "npx", ["-y", "firecrawl-mcp"], {"NODE_OPTIONS": NODE}, None, {}),
    "G3 npx pin + npm_config_node_options": ("project", "npx", ["-y", "firecrawl-mcp@3.25.5"], {"npm_config_node_options": NODE}, None, {}),
    "overlay server_env NODE_OPTIONS + pin": ("manifest", "npx", ["-y", "firecrawl-mcp@3.25.5"], {"NODE_OPTIONS": NODE}, None, {"NODE_OPTIONS": NODE}),
    "I docker -e NODE_OPTIONS=... digest": ("project", "docker", ["run", "-i", "--rm", "-e", f"NODE_OPTIONS={NODE}", D], {}, None, {}),
    "H npx pin + entry cwd holding .env": ("project", "npx", ["-y", "firecrawl-mcp@3.25.5"], {}, cwd, {}),
    "control npx pin, no env, no cwd": ("project", "npx", ["-y", "firecrawl-mcp@3.25.5"], {}, None, {}),
    "control docker digest, -e FIRECRAWL_API_KEY": ("project", "docker", ["run", "-i", "--rm", "-e", "FIRECRAWL_API_KEY", D], {"FIRECRAWL_API_KEY": "fc-1"}, None, {}),
}
name = sys.argv[1]
source, command, args, env, entry_cwd, extra = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"],
                      install={p: [command, *args] for p in ("mac", "linux", "wsl", "windows")} if source == "manifest" else {},
                      command=command, args=args, requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"], extra_env=extra)
resolved = ResolvedServerConfig(name="firecrawl", source=source, config=LocalMcpServerConfig(
    command=command, args=args, env=env, cwd=entry_cwd))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Rev 19 operator cost: legitimate VENDOR-HOSTED configured `firecrawl` entries
(FIRECRAWL_API_KEY only, no URL anywhere), judged with the real shipped manifest.
Which now warn? PATH=<tmp>/bin:/usr/bin:/bin with <tmp>/bin/npx."""
import os, tempfile
from pathlib import Path
os.environ.pop("FIRECRAWL_API_URL", None)
b = os.path.join(tempfile.mkdtemp(), "bin"); os.makedirs(b)
open(os.path.join(b, "npx"), "w").write("#!/bin/sh\n"); os.chmod(os.path.join(b, "npx"), 0o755)
os.environ["PATH"] = f"{b}:/usr/bin:/bin"
from pmcp.manifest.loader import load_manifest
import pmcp.tools.handlers as h
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
m = load_manifest(Path(os.environ["MANIFEST"]))
s = m.servers["firecrawl"]
K = {"FIRECRAWL_API_KEY": "fc-0123456789"}
CASES = [
    ("1 npx -y firecrawl-mcp", "npx", ["-y", "firecrawl-mcp"], {}),
    ("2 exact pin", "npx", ["-y", "firecrawl-mcp@3.25.5"], {}),
    ("3 absolute npx == which", os.path.join(b, "npx"), ["-y", "firecrawl-mcp"], {}),
    ("4 absolute npx != which (macOS GUI)", "/usr/local/bin/npx", ["-y", "firecrawl-mcp"], {}),
    ("5 cmd /c npx (Windows Claude Desktop)", "cmd", ["/c", "npx", "-y", "firecrawl-mcp"], {}),
    ("6 global firecrawl-mcp", "firecrawl-mcp", [], {}),
    ("7 node .../dist/index.js", "node", ["/opt/fc/dist/index.js"], {}),
    ("8 bunx", "bunx", ["firecrawl-mcp"], {}),
    ("9 pnpm dlx", "pnpm", ["dlx", "firecrawl-mcp"], {}),
    ("10 mise exec -- npx", "mise", ["exec", "--", "npx", "-y", "firecrawl-mcp"], {}),
    ("11 docker -e FIRECRAWL_API_KEY", "docker", ["run", "-i", "--rm", "-e", "FIRECRAWL_API_KEY", "mendable/firecrawl-mcp"], {}),
    ("12 docker --env-file", "docker", ["run", "-i", "--rm", "--env-file", ".env", "mendable/firecrawl-mcp"], {}),
    ("13 npx --package=", "npx", ["--package=firecrawl-mcp", "firecrawl-mcp"], {}),
    ("14 + FIRECRAWL_RETRY_MAX_ATTEMPTS", "npx", ["-y", "firecrawl-mcp"], {"FIRECRAWL_RETRY_MAX_ATTEMPTS": "5"}),
    ("15 + HTTPS_PROXY", "npx", ["-y", "firecrawl-mcp"], {"HTTPS_PROXY": "http://proxy:3128"}),
    ("16 npx.cmd on Linux", "npx.cmd", ["-y", "firecrawl-mcp"], {}),
    ("17 + NODE_OPTIONS=--max-old-space-size", "npx", ["-y", "firecrawl-mcp"], {"NODE_OPTIONS": "--max-old-space-size=4096"}),
]
warned = 0
for name, cmd, args, extra in CASES:
    r = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(command=cmd, args=args, env={**K, **extra}))
    w = h._unpinned_self_hosted_warning("firecrawl", s, r, None)
    warned += w is not None
    print(f"{name}\t{'None' if w is None else w[:150]}")
print(f"WARN {warned} of {len(CASES)}")
```

## Appendix: board-round-18 reproduction (`repro_r20.py`; revision 20)

```python
"""Round-18 codex B1: a configured `firecrawl` entry launching a uv INTERPRETER
request, FIRECRAWL_API_URL set in the entry env. Prints the warning and the
verdict that feeds `[PINNED]`. One case per process."""
import os, sys
os.environ["PATH"] = "/usr/bin:/bin"
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
CASES = {
    "uvx python==3.10 -c ...": ("uvx", ["python==3.10", "-c", "pass"]),
    "uvx --from python==3.10 python": ("uvx", ["--from", "python==3.10", "python"]),
    "uv tool run python==3.10": ("uv", ["tool", "run", "python==3.10"]),
    "uvx CPython==3.10": ("uvx", ["CPython==3.10"]),
    "uvx pypy3": ("uvx", ["pypy3"]),
    "control uvx python-dotenv==1.0.1": ("uvx", ["python-dotenv==1.0.1"]),
}
name = sys.argv[1]
command, args = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="npx",
                      args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command=command, args=args, env={"FIRECRAWL_API_URL": "http://ai:3002"}))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
v = h._judge_spawn_set(h._spawn_set(server, resolved), frozenset({"FIRECRAWL_API_URL"}))
print(f"{name}\t{'None' if w is None else w}\tverdict={v.kind} {v.reading.package}@{v.reading.selector} exact={v.reading.exact}")
```

## Appendix: board-round-19 reproduction and uv measurement (`repro_r21.py`, `measure_uv.py`; revision 21)

```python
"""Round-19 findings: a configured `firecrawl` entry launching a uv interpreter
request spelled with whitespace (or an extra), FIRECRAWL_API_URL in the entry
env. Prints the warning, the judge's verdict, and update_server's label input
(main's pin reader). One case per process."""
import os, sys
os.environ["PATH"] = "/usr/bin:/bin"
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
CASES = {
    "uvx 'python ==3.10'": ("uvx", ["python ==3.10"]),
    "uvx --from 'python == 3.10' python": ("uvx", ["--from", "python == 3.10", "python"]),
    "uv tool run 'cpython ==3.10'": ("uv", ["tool", "run", "cpython ==3.10"]),
    "uvx $'python\\t==3.10'": ("uvx", ["python\t==3.10"]),
    "uvx 'python[x]==3.10'": ("uvx", ["python[x]==3.10"]),
    "control uvx python-dotenv==1.0.1": ("uvx", ["python-dotenv==1.0.1"]),
}
name = sys.argv[1]
command, args = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="npx",
                      args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command=command, args=args, env={"FIRECRAWL_API_URL": "http://ai:3002"}))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None)
v = h._judge_spawn_set(h._spawn_set(server, resolved), frozenset({"FIRECRAWL_API_URL"}))
pin = h._detect_effective_version_pin("pypi", command, args, {}, None) if command == "uvx" else None
print(f"{name}\t{'None' if w is None else w}\tverdict={v.kind} exact={v.reading.exact}\tmain-reader={pin!r}")
```

```python
"""Measure real uv's reading of every (name, separator, tail) the round-19 seat
generated: I = uv runs (or needs) an interpreter, P = uv resolves a package,
E = uv rejects the spelling. Bare `uvx` form, offline, no cache, cwd /tmp."""
import itertools, json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
NAMES = ["python","pythonw","cpython","pypy","graalpy","pyodide","PYTHON","PyPy","CPython","GraalPy","Pyodide","pYthon",
         "py","cp","pp","gp","python3","python310","pypy3","cpython3","pythonv","python-dotenv","pythonnet","pypylon","python_x","python.x","cpython-x","pyodide_kit"]
SEPS = [""," ","  ","\t","\n"," ("]
TAILS = ["==3.10","== 3.10","===3.10","==3.10.21","==3.10.0","==3","==310","==3.10t","==3.10+debug","==3.10.*",">=3.10",
         "~=3.10","!=3.9","==v3.10","==3.10rc1","==3.10,<4","==3.10 ; python_version>'3'","[x]==3.10"]
def comp(n, s, t): return n + s + t + (")" if s == " (" else "")
def classify(c, form):
    if form == "bare": cmd = ["uvx", "--offline", "--no-cache", c, "-c", "print('RAN-INTERP')"]
    elif form == "tool": cmd = ["uv", "tool", "run", "--offline", "--no-cache", c, "-c", "print('RAN-INTERP')"]
    else: cmd = ["uvx", "--offline", "--no-cache", "--from", c, "python", "-c", "print('RAN-INTERP')"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd="/tmp")
    t = p.stdout + p.stderr
    if "RAN-INTERP" in t or "managed Python download is available" in t or "No interpreter found" in t:
        return "I"
    if "network was disabled" in t or "not found in the package registry" in t or "offline mode" in t or "No solution found when resolving tool dependencies" in t:
        return "P"
    return "E"
form = sys.argv[1]
keys = list(itertools.product(range(len(NAMES)), range(len(SEPS)), range(len(TAILS))))
with ThreadPoolExecutor(16) as ex:
    res = list(ex.map(lambda k: classify(comp(NAMES[k[0]], SEPS[k[1]], TAILS[k[2]]), form), keys))
table = {NAMES[i]: "".join(res[(i * len(SEPS) + j) * len(TAILS) + k] for j in range(len(SEPS)) for k in range(len(TAILS))) for i in range(len(NAMES))}
json.dump(table, open(f"uv_table_{form}.json", "w"), indent=0)
print(form, "I", res.count("I"), "P", res.count("P"), "E", res.count("E"))
```

## Appendix: board-round-20 reproduction (`repro_r22.py`; revision 22)

```python
"""Round-20 codex B1 and its class: a configured `firecrawl` entry whose argv or
cwd carries a credential-shaped sentinel, FIRECRAWL_API_URL set in the entry
env. Prints whether the sentinel reaches the warning. One case per process."""
import os, sys
os.environ["PATH"] = "/usr/bin:/bin"
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
import pmcp.tools.handlers as h
T = "SYNTHETIC_REVIEW_TOKEN"
U = f"https://user:{T}@example.test/python.whl"
Q = f"https://example.test/python.whl?token={T}"
CASES = {
    "uvx --from 'python @ <userinfo URL>' python": ("uvx", ["--from", f"python @ {U}", "python"], None),
    "uvx --from 'python @ <query URL>' python": ("uvx", ["--from", f"python @ {Q}", "python"], None),
    "cargo +<userinfo URL> install fc": ("cargo", [f"+{U}", "install", "fc"], None),
    "npx -y fc-mcp@1.0.0, cwd /srv/<token>": ("npx", ["-y", "firecrawl-mcp@1.0.0"], f"/srv/{T}"),
    "uvx --<userinfo URL> fc-mcp==1.0": ("uvx", [f"--{U}", "fc-mcp==1.0"], None),
}
name = sys.argv[1]
command, args, cwd = CASES[name]
server = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={}, command="npx",
                      args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
                      api_key_optional_when=["FIRECRAWL_API_URL"])
resolved = ResolvedServerConfig(name="firecrawl", source="project", config=LocalMcpServerConfig(
    command=command, args=args, env={"FIRECRAWL_API_URL": "http://ai:3002"}, cwd=cwd))
w = h._unpinned_self_hosted_warning("firecrawl", server, resolved, None) or ""
cut = w.split("pinned: ", 1)[-1][:120]
print(f"{name}\tsentinel {'LEAKS' if T in w else 'absent'}\t{cut}")
```
