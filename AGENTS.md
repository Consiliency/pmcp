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

## Shared-host test limits (dev0)

dev0 is a shared team host used by many concurrent agent sessions. Uncapped
test runs there have made it unusable for everyone, so these rules are firm.

**Allowed on dev0:** editing, `ruff`, `mypy` on single files, and a few
affected test files or node IDs of ordinary unit tests whose footprint is
known to be small, at most `-n 2` (serial is fine). Run them one batch at a
time, never several in parallel.

**Never on dev0, at any worker count (including `-n 0`), in one run or split
into batches:**
- `-n auto` / `--numprocesses=auto`, whether through
  `scripts/pytest_temp.py` or raw `pytest`;
- the full or a broad suite (`tests/`), from one worktree or several;
- `tests/test_workflow_guards.py` and any other workflow-guard or
  shell-parser test, stress test or differential;
- the `_mutate_workflow.sh` mutant loop, negative-control batteries and
  mutation testing;
- live-oracle batteries (bash, curl or wget against a loopback server);
- anything whose memory use is unmeasured on the current code.

The full suite belongs to the PR or closeout gate, and runs on `ai` or in
hosted CI. The `CONTRIBUTING.md` full-suite command (`-n auto`) is for those
hosts only.

**Before any test run on dev0,** check the host and defer or move the run if
`MemAvailable` in `/proc/meminfo` is below 6 GiB or the 1-minute load (field 1
of `/proc/loadavg`) is at or above `nproc`:

```bash
awk '/^MemAvailable:/ {print $2/1048576 " GiB available"}' /proc/meminfo
echo "load $(cut -d' ' -f1 /proc/loadavg) / cores $(nproc)"
```

**Running on `ai`.** Use `ssh ai`, `~/code/pmcp` or a sibling worktree synced
through GitHub (push your branch first). Run detached, with a log, under a
memory cap above the job's measured footprint (for example 4G for a single
guard file, 16G for the full suite at `-n 8`):

```bash
mkdir -p "/tmp/pmcp-ai-$USER" && chmod 700 "/tmp/pmcp-ai-$USER"
nohup systemd-run --user --scope -p MemoryMax=16G -p MemorySwapMax=0 \
  nice -n 10 uv run --locked python scripts/pytest_temp.py -- tests/ -q -n 8 \
  > "/tmp/pmcp-ai-$USER/full-suite.log" 2>&1 &
disown
```

If `systemd-run` cannot apply `MemoryMax`, do not run the job uncapped.
Poll the log for pytest's final summary line; `pgrep -f` over ssh matches its
own command line.

**Unexpected memory growth is a bug, not a capacity problem.** For the banned
workloads, fewer workers is not a fix: first establish off-host, under a cap,
that the work is bounded. Growth in a classifier or parser is a
resource-exhaustion defect in the code; find the input that causes it, bound
the work (a guard's work bound must fail closed and flag when exceeded), and
add a regression test.

**An interrupted run is not a result.** If a run is killed, OOM-terminated,
timed out or interrupted by an operator, report it as "interrupted, no
result", never as passing or partial evidence. Do not relaunch it on dev0;
keep its log and command line, mark them invalid in the evidence and PR body,
and rerun it on `ai` or in CI.
