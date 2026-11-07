# PMCP agent instructions

## Test scratch and evidence

- Follow the test commands and temp lifecycle in `CONTRIBUTING.md`. Use
  `uv run --locked python scripts/pytest_temp.py -- <pytest arguments>` in a
  prepared environment, or the prepared interpreter directly. Do not install or
  change shared tools/environments to run a test.
- Every new plan's verification commands must use this runner (see
  `plans/verification-template.md`). It creates a unique private directory and
  removes it after success, failure, SIGINT, SIGTERM or SIGHUP, once pytest and
  the processes it left behind have exited (platform coverage is in
  `plans/verification-template.md`). It returns pytest's own exit status.
- Do not copy fixed `--basetemp` paths from old plans into new runs, including
  `/var/tmp/pmcp-*-bt*`, issue-numbered scratch folders, or `$WORKTREE_ROOT` paths.
  These old commands do not define the current convention.
- Put required logs, JUnit/coverage reports and review evidence at explicit
  durable paths outside scratch. If raw scratch itself must be kept, declare
  `--keep-temp` before the run; record its printed path, purpose, owner and
  retention deadline in the run receipt. Remove only that owned directory after
  the evidence is archived and no open thread/PR or active process needs it.
- Do not run blanket cleanup on other runs, caches, shared environments, or
  worktrees. SIGKILL, power loss and host crashes cannot execute cleanup: record
  the printed scratch path so an owner can check and reclaim it later.
- Preserve existing test selectors, oracles and evidence requirements. Heavy
  suites run on `ai`; use an isolated process HOME before project imports and
  collection when a suite can read operator configuration. Scratch cleanup is
  not HOME isolation or permission to use real credentials/services.

## Shared-host resource limits (dev0): owner rule, 2026-11-03

dev0 is a shared team host. Do not run memory- or CPU-heavy pmcp tests there.

**Incident.** On 2026-11-03 the #472 implementation agent ran
`scripts/pytest_temp.py -- tests/test_workflow_guards.py -q -n 2` in a T3
worktree on dev0 (`t3-72097568`). That worktree held an uncommitted
workflow-guard lexer rewrite.
- One xdist worker reached about 12 GiB RSS (about 40% of host RAM).
- Load passed 117, memory PSI was about 99%, T3 health timed out and dev0
  dropped off the network.
- Root had to interrupt the coordinator (SIGINT) and terminate the worker
  (SIGTERM).
- The record is at
  `~/workspace/tmp/pmcp-472-impl-20261103/evidence/incident-2026-11-03-dev0-oom/`.

**Never run these on dev0, at any worker count, including `-n 0`:**
- `tests/test_workflow_guards.py`, and any other workflow-guard or
  shell-parser test, stress test or differential;
- the `_mutate_workflow.sh` mutant loop and negative-control batteries;
- live-oracle batteries (bash, curl or wget against a loopback server);
- full or broad suites, mutation testing, and anything whose memory use is
  unmeasured on the current code.

**Where to run them.**
- On `ai`: run `ssh ai`, use `~/code/pmcp` or a sibling worktree, and sync
  through GitHub.
- Or on hosted CI.
- On ai, always run detached, with logs, under an explicit cap:

  ```bash
  nohup systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0 \
    nice -n 10 <command> > /tmp/pmcp-ai-$USER/<job>.log 2>&1 & disown
  ```

  `prlimit --as=` or `ulimit -v` are fallbacks.
- Poll for a sentinel line in the log. `pgrep -f` over ssh matches its own
  command line.

**Fewer workers on dev0 is not a fix.** First establish off-host, under a cap,
that the workload is bounded. Unexpected memory growth in a classifier or
parser is a defect in the code, a resource-exhaustion bug in the guard
itself. Find the input that causes it, bound the work, and add a regression
test. A work bound in a guard must fail closed: it flags when exceeded.

**What dev0 is for:** editing, `ruff`, `mypy` on single files, and a few
selected node IDs of ordinary unit tests whose footprint is known to be
small.

**An interrupted test is not a result.** If a run is killed, OOM-terminated,
timed out or interrupted by an operator, report it as "interrupted, no
result". Never report it as passing or as partial evidence. Keep its logs and
the command line, mark them invalid in the evidence and PR body, and rerun it
off dev0 under a cap.

## Shared-host test load (dev0): owner direction, 2026-11-07

dev0 is shared by about 40 agent sessions. On 2026-11-07 a #478 delegate ran
`pytest tests/ -q -n auto --dist loadfile` in a dev0 worktree. It started 24
xdist workers (about 6 GB) plus pmcp, gcloud and aws subprocesses. RAM filled
up, 8 GB of swap was in use, load reached 280 on 24 cores, and dev0's T3
server stopped answering. The run was killed twice and relaunched both times.

On dev0, for any test run:
- **Never use `pytest -n auto`.** Cap workers at `-n 2` to `-n 4`.
- **Don't run the full suite from several worktrees at once.**
- **While iterating, run only the affected test files.** Run the full suite
  only for the PR or closeout gate, and run it on `ai` or in hosted CI (see
  above), not on dev0.
- **Check the host before any heavy run:** `free -h` and `/proc/loadavg`. If
  available memory is under about 6 GB or the 1-minute load is over about 24,
  defer the run or move it.
- If a run has to stay on dev0, split it into small sequential batches.
- A killed relaunch is a signal to stop, not to retry. Report the run as
  "interrupted, no result" and move it off dev0.
