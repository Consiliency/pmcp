#!/usr/bin/env python3
"""Cross-check two registries in a phase plan that MUST agree:
  (a) lane task tables, 'Tests owned' column -> what a lane is contracted to write
  (b) EC-<ALIAS>-<N> proving commands, ::node ids -> what phase close actually runs
A node id in (b) with no lane contracted to write it => phase close fails collection.
A name in (a) that no EC runs => a security property nobody is required to prove.

Why this exists: three consecutive advisor-board rounds on Consiliency/pmcp#239 each
found the same defect class in `plans/phase-plan-v13-*.md` — a safety rule corrected in
the interface freeze and in the acceptance criteria, but not in the lane task table that
tells an executing lane which tests to write. The result is a plan whose own phase-close
command fails collection, or worse, a lane contracted to write a test named after the
unsafe rule that was replaced. Careful reading did not catch it three times; this does.

It also verifies each plan's `roadmap_sha256:` frontmatter pin against the actual
digest of the roadmap it names. That pin is the plan's trust contract; a stale one
means the plan was written against a roadmap that no longer exists. This check was
added after the registry cross-check above shipped WITHOUT it and duly reported
"0 blocking" while all three pins were stale — the exact bug it was meant to prevent.

It can also EMIT a lane's required node ids, which is the other half of the same
problem. Lane briefs were hand-transcribed from the plan five times across two
phases and dropped a node id every time; once, the dropped test was the only
falsifier for a security rule, so that rule would have shipped unproven had the
lane trusted the brief over the document. Deriving the list removes the step
where a human retypes it.

Usage:
    python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md
    python3 scripts/check_plan_consistency.py --brief SL-3 plans/phase-plan-v13-CONSENT.md
Exits non-zero if any EC runs a node id no lane is contracted to write, or if any
roadmap_sha256 pin is stale.
"""

import hashlib
import re
import sys
import pathlib


def check_pin(path):
    """Verify the plan's roadmap_sha256 against the roadmap it names."""
    p = pathlib.Path(path)
    s = p.read_text()
    m = re.search(r"^roadmap:\s*(\S+)$", s, re.M)
    d = re.search(r"^roadmap_sha256:\s*(\w+)$", s, re.M)
    if not m or not d:
        return 0  # not a pinned phase plan
    roadmap = p.parent.parent / m.group(1)
    if not roadmap.exists():
        print(f"  [BLOCKING] {p.name} pins roadmap {m.group(1)} — file not found")
        return 1
    actual = hashlib.sha256(roadmap.read_bytes()).hexdigest()
    if actual != d.group(1):
        print(
            f"  [BLOCKING] {p.name} roadmap_sha256 is STALE "
            f"(pinned {d.group(1)[:12]}…, actual {actual[:12]}…)"
        )
        return 1
    return 0


def check_dag(path):
    """The Lane Index must declare every in-phase dependency a lane actually has.

    A plan states a lane's dependencies in three places: the Lane Index (which the
    executor reads to schedule waves), the lane's task table, and its "Interfaces
    consumed". They drifted: PKGID's SL-3 was corrected to consume
    `is_valid_package_version` from SL-2 in its task table and interfaces, but its
    Lane Index still said "Depends on: (none)" -- and SL-2's "Blocks" still omitted
    SL-3. An executor would have dispatched SL-3 in the first wave, before the
    validator it needs existed.

    References qualified by another phase's alias (e.g. "(TRUST SL-2)") name that
    phase's lanes, not this plan's, and are ignored.
    """
    s = pathlib.Path(path).read_text()
    index = {}
    cur = None
    for ln in s.splitlines():
        m = re.match(r"^(SL-\d+) —", ln)
        if m:
            cur = m.group(1)
            index[cur] = {"depends": set(), "blocks": set()}
            continue
        if cur and (d := re.match(r"^\s+Depends on:\s*(.*)$", ln)):
            index[cur]["depends"] = set(re.findall(r"\bSL-\d+\b", d.group(1)))
        if cur and (b := re.match(r"^\s+Blocks:\s*(.*)$", ln)):
            index[cur]["blocks"] = set(re.findall(r"\bSL-\d+\b", b.group(1)))
        if ln.startswith("## ") and index:
            cur = None
    if not index:
        return 0

    def in_phase_refs(text):
        # drop anything like "TRUST SL-2" / "CONSENT SL-1": another phase's lane
        text = re.sub(r"\b[A-Z]{2,}\s+SL-\d+\b", "", text)
        return set(re.findall(r"\bSL-\d+\b", text))

    needed = {lane: set() for lane in index}
    section = None
    for ln in s.splitlines():
        m = re.match(r"^### (SL-\d+)\b", ln)
        if m:
            section = m.group(1)
            continue
        if ln.startswith("## "):
            section = None
        if section in needed and "**Interfaces consumed**" in ln:
            needed[section] |= in_phase_refs(ln)
        row = re.match(r"^\|\s*(SL-\d+)\.\d+\s*\|[^|]*\|([^|]*)\|", ln)
        if row and row.group(1) in needed:
            needed[row.group(1)] |= in_phase_refs(row.group(2))

    bad = 0
    name = pathlib.Path(path).name
    for lane, req in sorted(needed.items()):
        req.discard(lane)
        missing = sorted(req - index[lane]["depends"])
        for m in missing:
            print(
                f"  [BLOCKING] {name}: {lane} consumes {m} but its Lane Index"
                f" does not list {m} under Depends on"
            )
            bad += 1
        for dep in sorted(index[lane]["depends"] & set(index)):
            if lane not in index[dep]["blocks"]:
                print(
                    f"  [BLOCKING] {name}: {lane} depends on {dep} but {dep}'s"
                    f" Lane Index does not list {lane} under Blocks"
                )
                bad += 1
    return bad


def check(path):
    s = pathlib.Path(path).read_text()
    lane, lane_of = set(), {}
    for ln in s.splitlines():
        m = re.match(r"\|\s*(SL-[\w.]+)\s*\|", ln)
        if not m:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        for n in re.findall(r"`(test_[a-z0-9_]+)`", cells[4]):
            lane.add(n)
            lane_of.setdefault(n, m.group(1))
    ec, ec_of = set(), {}
    for ln in s.splitlines():
        m = re.match(r"- \[[ x]\] (EC-[A-Z]+-\d+) —", ln)
        if not m:
            continue
        for n in re.findall(r"::(test_[a-z0-9_]+)", ln):
            ec.add(n)
            ec_of.setdefault(n, m.group(1))
    print(f"\n=== {pathlib.Path(path).name}")
    print(f"  lane-contracted: {len(lane)}   EC-proved node ids: {len(ec)}")
    bad = 0
    for n in sorted(ec - lane):
        print(f"  [BLOCKING] {ec_of[n]} runs ::{n} — no lane is contracted to write it")
        bad += 1
    unproven = sorted(lane - ec)
    for n in unproven:
        print(f"  [UNPROVEN] {lane_of[n]} must write {n}, but no EC runs it")
    if unproven:
        print(
            f"  -> {len(unproven)} lane-contracted test(s) that phase close never"
            " executes. This is not a style nit: delete them and the phase still"
            " reports green. On CONSENT this set included the TOCTOU one-read"
            " guard, the fail-closed cases and the redaction-widening falsifier —"
            " i.e. the security properties. Every EC should name the tests that"
            " prove its rule."
        )
    if not bad and lane >= ec:
        print("  consistent")
    return bad + check_pin(path) + check_dag(path)


def brief(lane, path):
    """Print the node ids a lane must produce, derived from the plan.

    Source of truth is the acceptance criteria: whatever EC proving commands run
    against the test files this lane OWNS is what the lane must deliver, because
    that is the command phase close actually executes.
    """
    s = pathlib.Path(path).read_text()
    owned = set()
    current = None
    for ln in s.splitlines():
        m = re.match(r"### (SL-[\w.-]+)", ln)
        if m:
            current = m.group(1)
        if current == lane and "**Owned files**" in ln:
            owned |= set(re.findall(r"`(tests/[\w./-]+\.py)`", ln))
    if not owned:
        print(f"no owned test files found for {lane} in {path}", file=sys.stderr)
        return 1
    required = {}
    for ln in s.splitlines():
        m = re.match(r"- \[[ x]\] (EC-[A-Z]+-\d+) —", ln)
        if not m:
            continue
        for f, n in re.findall(r"(tests/[\w./-]+\.py)::(test_[a-z0-9_]+)", ln):
            if f in owned:
                required.setdefault((f, n), []).append(m.group(1))
    print(f"# {lane} owns: {', '.join(sorted(owned))}")
    print(f"# {len(required)} node id(s) the acceptance criteria run verbatim:")
    for (f, n), ecs in sorted(required.items()):
        print(f"  {n}    [{', '.join(sorted(set(ecs)))}]")
    return 0


if len(sys.argv) > 2 and sys.argv[1] == "--brief":
    sys.exit(brief(sys.argv[2], sys.argv[3]))

total = sum(check(p) for p in sys.argv[1:])
print(f"\nblocking inconsistencies: {total}")
sys.exit(1 if total else 0)
