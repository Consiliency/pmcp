# Detailed plan: stop `--verify` reporting host-enumerated types as drift

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

- `_is_host_enumerated(type_labels)` — add — returns True when a type's members
  include a **host fact**, detected by property rather than by name:
  any member that parses as an IPv4 or IPv6 address via Python's `ipaddress`
  module. Today that selects exactly `local-address` and leaves `audit-level`,
  `lockfile-version` and `loglevel` alone — verified above. Detecting by
  *shape* means a future npm type built from hostnames or interface names is
  caught by extending this one predicate, not by editing a name list.
- `classify(type_labels)` (`:217`) — modify — when `_is_host_enumerated` is
  true, classify from the **stable** part of the type only: drop the
  address-shaped members, then classify what remains. `local-address` is a value
  flag on every host; this makes that answer machine-independent instead of
  depending on which interfaces happen to be up.
- `--verify` comparison (`:558-585`) — modify — record host-enumerated flags in a
  `skipped_host_enumerated` list and **print them**, with the reason and the
  member count seen on this host. Silence is what made the original failure
  confusing; the generator already prints its conditional-arity exemptions, so
  this matches an existing convention rather than inventing one.
- Module docstring (`:86-95`) — modify — state that member lists are not
  comparable across hosts and that classification is what `--verify` pins.

### `tests/test_version_checker.py` (modify)

- `TestVerifyIsHostIndependent` — add:
  - `_is_host_enumerated` is True for a synthetic type carrying IPv4 and IPv6
    literals, and **False** for `audit-level`, `lockfile-version`, `loglevel`
    member lists copied verbatim from npm — so a future edit cannot quietly
    start exempting fixed enumerations.
  - `classify` returns the same class for a `local-address`-shaped type built
    with **two different fake interface sets**. This is the actual bug: same
    npm, different machine, different answer.
  - The exemption is **reported**, not silent — assert the printed output names
    the skipped flag.

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

- [ ] `--verify` exits 0 **and produces identical failure output** under two
      different simulated interface sets — proven by running it twice with the
      `local-address` type patched to different address lists, and diffing.
      Running it once on this machine proves nothing, which is the whole bug.
- [ ] `_is_host_enumerated` is False for `audit-level`, `lockfile-version` and
      `loglevel` with their real member lists — so the exemption cannot silently
      widen to fixed enumerations.
- [ ] Regenerating the tables produces output **byte-identical** to the committed
      `_NPM_VALUE_FLAGS` / `_NPM_BOOLEAN_FLAGS` / `_NPM_SKIP_FLAGS` /
      `_NPM_NULLABLE_BOOLEAN_FLAGS`.
- [ ] `--verify` **prints** every exempted flag with its reason; asserted by a
      test on the output, not by reading the code.
- [ ] Link-local members with a `%zone` suffix are still detected as host facts.

## Non-goals

- Changing what the committed tables contain. This fixes the *comparison*, not
  the data.
- Making the member lists themselves reproducible — they cannot be; they are
  host facts by construction.

## Execution Policy

- execute: effort=low, reason=one predicate plus a reporting change in a
  maintainer script; the subtlety is entirely in the test that simulates a
  second machine
