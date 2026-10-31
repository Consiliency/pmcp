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
