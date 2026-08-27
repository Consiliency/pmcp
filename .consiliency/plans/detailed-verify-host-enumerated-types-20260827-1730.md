# Detailed plan: stop `--verify` reporting host-enumerated types as drift

> **Revision 2 (2026-08-27).** Rev 1 boarded **3 DISAGREE** and they were right on
> all three counts. Rev 1's detector could never have fired, its normalization
> produced UNINTERPRETABLE on the very host that filed the bug, its headline test
> was vacuous, and it specified two mutually contradictory strategies. Corrected
> below; rev 1's errors are kept as `WAS WRONG` because each one is a trap.

## Task

Close Consiliency/pmcp#193. `.consiliency/notes/derive_npm_flags.py --verify` is green on
one machine and red on another with identical npm and identical source, because
some npm config types are **enumerated from the host**. A drift check with false
positives gets ignored, and an ignored check is how real drift ships.

## Research summary

Measured on this host (npm 11.19.0):

- `local-address`'s declared `type` has **51 members** — `null` plus this
  machine's own IPv4/IPv6 addresses and interface addresses
  (`127.0.0.1`, `::1`, `178.156.237.237`, `2a01:4ff:f0:f633::1`, `fe80::…`, …).
  npm builds it from `os.networkInterfaces()`, so it is different on every
  machine and changes when an interface appears or a lease renews.
- The only other definitions with more than six type members are
  **`audit-level` (7), `lockfile-version` (7), `loglevel` (8)** — all *fixed*
  enumerations that are byte-identical on every host.

So "many members" is **not** the signal; "members that are host facts" is. A
name-based exemption for `local-address` would work today and silently fail the
next time npm adds a host-derived type, which is the same
fix-the-instance-not-the-class error this project has hit repeatedly (see the
`WAS WRONG` history in the #195 plan).

`--verify`'s comparison is a set difference per class
(`derive_npm_flags.py:558-585`): `live - committed` → "MISSING from table",
`committed - live` → "in table, absent from live npm". The reviewer's host
produced `value: --local-address in table, absent from live npm`, which means
`classify()` did not put it in `VALUE` there. The member list is what varies;
the *classification* (`local-address` takes a value) is stable everywhere.

## Changes

### `.consiliency/notes/derive_npm_flags.py` (modify)

**WAS WRONG (rev 1), three ways:**

1. *The predicate was blind.* Rev 1 detected host-enumerated types in Python by
   parsing members with `ipaddress`. But `read_schema`'s node-side serializer maps
   **every string or number member to the literal `'<literal>'`**
   (`derive_npm_flags.py:~134`), so no real IP string ever reaches Python. The
   predicate could not have fired once, and synthetic tests feeding it raw IP
   strings would have bypassed the production contract entirely.
2. *It targeted the wrong case.* npm's `getLocalAddresses()` **catches and returns
   `[null]`** when `networkInterfaces()` throws
   (`@npmcli/config/lib/definitions/definitions.js:65-71`). The host that filed
   this bug therefore has `local-address: [null]` — **no addresses at all** — which
   an IP-presence test cannot see. Worse, rev 1's "drop the address members and
   classify the remainder" also leaves `[null]`, and `classify()` strips `null` and
   returns UNINTERPRETABLE — so rev 1 would have produced the reported failure
   rather than fixing it, and the tables could not regenerate byte-identical.
3. *It specified two contradictory strategies.* Normalizing `classify()` to a
   stable `VALUE` makes an exemption unnecessary; exempting the flag from
   `--verify` removes it from the comparison and would **hide a future genuine
   arity change** — the opposite of this check's purpose. Rev 1 did both.

**Corrected design — detect where the raw members live, normalize, and keep
verifying:**

- `read_schema`'s node-side script (`:~128-165`) — modify — emit a per-definition
  boolean `hostEnumerated`, computed in **JavaScript, where the raw type array is
  still intact**, as either of:
  - `typeDescription === 'IP Address'` — npm's own label for this class. Verified
    stable and host-independent, and verified *not* to match the fixed
    enumerations: `audit-level`, `loglevel` and `lockfile-version` all carry
    enumerated descriptions listing their literals.
  - a `net.isIP()` scan over the raw members returning true for any of them —
    a second, independent signal for a future host-derived type npm does not
    label.

  Either alone is sufficient; both are cheap. If npm renames the label *and* the
  member list is `[null]`, detection stops and the flag falls back to member-based
  classification, which on that host is UNINTERPRETABLE → a **loud red verify**,
  not a silent wrong answer. That is the correct failure direction and is stated
  here so the next reader does not mistake it for a gap.
- `classify(type_labels, *, host_enumerated=False)` (`:217`) — modify — when
  `host_enumerated` is true, return `VALUE` **regardless of members, including the
  bare `[null]` case**. `local-address` takes a value on every host; the member
  list is a host fact and carries no classification information.
- `--verify` — modify — **`local-address` stays in the comparison.** No skip list,
  no exemption. What is printed instead is a one-line note that the flag was
  *normalized* as host-enumerated, so the exemption-shaped behaviour is visible
  without removing anything from the check.
  **WAS WRONG (rev 1):** a `skipped_host_enumerated` list. Once classification is
  stable there is nothing to skip, and skipping would blind the check to a real
  arity change on the one flag most likely to drift.
- Module docstring — modify — the host-independence discussion belongs at
  `:22-24` / `:42-53`; rev 1 cited `:86-95`, which is the nullable-spelling
  paragraph.

### `tests/test_version_checker.py` (modify)

`tests/test_version_checker.py` does **not** import the generator; load it with
the `importlib.util.spec_from_file_location` pattern already used in
`tests/test_workflow_guards.py`. **CI has no npm**, so every test here must mock
`read_schema()` rather than shelling out — otherwise it is a maintainer-only
check that never runs.

- `TestVerifyIsHostIndependent` — add:
  - `classify(..., host_enumerated=True)` returns `VALUE` for **`[null]`** (the
    enumeration-failure host), for a one-address list, and for a
    fifty-address list. All three must be `VALUE` — not merely equal to each
    other. **WAS WRONG (rev 1):** the test compared two non-empty address sets,
    which the *current* code already classifies identically; it would have passed
    against unchanged code.
  - `hostEnumerated` is true for `local-address` and **false** for `audit-level`,
    `loglevel` and `lockfile-version`, using their real `typeDescription` strings.
  - `--verify` output names the normalized flag.

## Documentation impact

- `CHANGELOG.md` — add — a `### Fixed` entry under `[Unreleased]`: `--verify` no
  longer reports host-enumerated config types as table drift, and now prints what
  it exempted.
- No README change: `derive_npm_flags.py` is a maintainer tool, not user-facing.

## Dependencies & order

1. `_is_host_enumerated` + `classify` change.
2. Tests, including the two-fake-interface-sets case.
3. `--verify` reporting.
4. Regenerate the tables and confirm they are **byte-identical** to what is
   committed — if the classification change alters any committed table, that is a
   real finding and must be investigated before proceeding, not absorbed.

## Verification

```bash
uv run python .consiliency/notes/derive_npm_flags.py --verify     # exit 0, prints exemptions
uv run pytest -q tests/test_version_checker.py -k host_independent

# The real test: simulate another machine. Monkeypatch the definitions so
# local-address carries a DIFFERENT address set, and assert --verify still
# passes and the committed tables are unchanged.
uv run python - <<'PY'   # (spelled out in the test, this is the manual form)
# ... patch definitions -> local-address type = [null, "10.0.0.5", "fe80::1"]
# ... assert classify() == VALUE and no drift failure is emitted
PY

uv run pytest -q                      # full suite
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

Edge cases: a type with **only** `null` and addresses; a host with no network
interfaces at all (the enumeration collapses to `[null]`); an IPv6 address with a
zone suffix (`fe80::1%eth0`) — Python's `ipaddress` rejects the zone form, so the
predicate must strip a `%…` suffix before parsing or it will miss link-local
members and re-open the false positive.

## Acceptance criteria

- [ ] With `local-address`'s type patched to **`[null]`**, to one address, and to
      fifty addresses, `classify` returns **`VALUE`** in all three and `--verify`
      exits 0 with **identical** output. The `[null]` case is the one that filed
      this bug and is non-negotiable; rev 1's two-non-empty-lists test passed
      against unchanged code.
- [ ] `local-address` is still **present in the `--verify` comparison** — proven
      by patching its live classification to `BOOLEAN` and asserting `--verify`
      goes red. An exemption would pass a naive "no false positives" check while
      blinding the tool to real drift.
- [ ] `hostEnumerated` is false for `audit-level`, `loglevel` and
      `lockfile-version` with their real `typeDescription` values.
- [ ] Regenerating produces output **byte-identical** to the four committed
      tables.
- [ ] Every test mocks `read_schema()` and passes with **no npm on PATH**, so the
      checks run in CI rather than only on a maintainer's machine.

## Non-goals

- Changing what the committed tables contain. This fixes the *comparison*, not
  the data.
- Making the member lists themselves reproducible — they cannot be; they are
  host facts by construction.

## Execution Policy

- execute: effort=low, reason=one predicate plus a reporting change in a
  maintainer script; the subtlety is entirely in the test that simulates a
  second machine
