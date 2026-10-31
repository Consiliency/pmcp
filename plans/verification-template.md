# Verification commands for new plans

Use this section when writing a new implementation/review plan. Historical plans
are evidence, not templates for fixed scratch directories. Follow `AGENTS.md` and
`CONTRIBUTING.md`; do not generate persistent `--basetemp` commands.

Record the checkout/head, machine, prepared interpreter/dependency limits, exact
selectors and one declared run sequence. Run heavy validation on `ai`. Preserve
the existing HOME/config isolation requirements before Python imports or pytest
collection. Do not silently retry a failed validation.

```bash
# Prepared environment; no fixed --basetemp. Each invocation owns unique scratch.
uv run --locked python scripts/pytest_temp.py -- tests/test_policy.py -q

# Put required evidence OUTSIDE scratch, in an owned durable directory.
# Set this to the plan's actual evidence directory before executing.
test_evidence_dir=/path/to/owned/run-evidence
mkdir -p "$test_evidence_dir"
uv run --locked python scripts/pytest_temp.py -- tests/test_policy.py -q \
  --junitxml="$test_evidence_dir/junit.xml" \
  >"$test_evidence_dir/pytest.log" 2>&1
```

The runner removes scratch after passing or failing tests, and after interrupt,
termination or hangup, once pytest exits. On POSIX it forwards those signals to
pytest's whole process group. Before deleting anything it stops every process
pytest left behind: on Linux it registers as a child subreaper, so descendants that
started their own session are adopted and stopped too; on macOS only pytest's
process group is covered; on Windows only pytest itself. If a left-over process
will not exit, the scratch is retained and named on stderr. It propagates pytest's
exit status. If scratch cannot be
removed, the runner names the retained path on stderr and still returns pytest's
status. Never pipe through `tee` without preserving the test exit status.

If raw scratch must be reviewed, explicitly opt in before the declared run:

```bash
uv run --locked python scripts/pytest_temp.py --keep-temp -- tests/test_policy.py -q
```

In that case the completion receipt must name the retained path printed by the
runner, evidence purpose, owner and retention deadline. Archive the needed files
and then remove only that owned path when no running/open thread or PR needs it.
Do not retain whole virtualenvs or repeat a full suite just to collect evidence
that the plan could have exported on the first run. Crashes/SIGKILL require an
explicit later liveness/ownership check; there is no unconditional global sweep.
