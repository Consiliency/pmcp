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


def _phase_aliases(plan_text, plan_path):
    """The roadmap's phase aliases (TRUST, CONSENT, ...), read from its headings.

    Used to recognise a cross-phase lane reference such as "(TRUST SL-2)". Reading
    the real aliases, rather than treating any word before a lane id as an alias,
    matters: a generic rule would also discard "from SL-2" or "via SL-2" in an
    interfaces line and so HIDE a genuine in-phase dependency.
    """
    m = re.search(r"^roadmap:\s*(\S+)$", plan_text, re.M)
    if not m:
        return set()
    roadmap = pathlib.Path(plan_path).parent.parent / m.group(1)
    if not roadmap.exists():
        return set()
    return {
        a.upper()
        for a in re.findall(
            r"^### Phase \S+ — .*\(([A-Za-z0-9]+)\)\s*$", roadmap.read_text(), re.M
        )
    }


def check_dag(path):
    """The Lane Index must agree with the dependencies each lane actually has.

    A plan states a lane's dependencies in three places: the Lane Index (which the
    executor reads to schedule waves), the lane's task table, and its "Interfaces
    consumed". They drifted: PKGID's SL-3 was corrected to consume
    `is_valid_package_version` from SL-2 in its task table and interfaces, while
    its Lane Index still said "Depends on: (none)" and SL-2's "Blocks" omitted
    SL-3 -- so an executor would have dispatched SL-3 before its validator existed.

    Checks, each independently:
      * every in-phase lane a lane consumes is under its Lane Index Depends on;
      * every Depends on edge has the matching Blocks edge, and vice versa, so a
        spurious Blocks entry is caught as well as a missing one;
      * a plan that has lane sections but no Lane Index is reported, rather than
        passing silently with nothing checked.

    Known limit, deliberately: a dependency mentioned only in free prose (Scope,
    Execution Notes) is not read, because reading prose would report lane ids that
    merely appear in a sentence.
    """
    s = pathlib.Path(path).read_text()
    name = pathlib.Path(path).name
    has_lanes = bool(re.search(r"^### SL-\d+\b", s, re.M))

    index, cur = {}, None
    for ln in s.splitlines():
        if ln.startswith("## "):
            cur = None
        m = re.match(r"^(SL-\d+) —", ln)
        if m:
            cur = m.group(1)
            index[cur] = {"depends": set(), "blocks": set()}
            continue
        if cur and (d := re.match(r"^\s+Depends on:\s*(.*)$", ln)):
            index[cur]["depends"] = set(re.findall(r"\bSL-\d+\b", d.group(1)))
        if cur and (b := re.match(r"^\s+Blocks:\s*(.*)$", ln)):
            index[cur]["blocks"] = set(re.findall(r"\bSL-\d+\b", b.group(1)))

    if has_lanes and not index:
        print(
            f"  [BLOCKING] {name}: has lane sections but no Lane Index, so no"
            " dependency could be checked and the executor has nothing to schedule"
        )
        return 1
    if not index:
        return 0

    aliases = _phase_aliases(s, path)
    alias_ref = (
        re.compile(r"\b(?:" + "|".join(sorted(aliases)) + r")\b[\s,;:/]*SL-\d+\b", re.I)
        if aliases
        else None
    )

    def in_phase_refs(text):
        if alias_ref:
            text = alias_ref.sub("", text)
        return set(re.findall(r"\bSL-\d+\b", text))

    needed = {lane: set() for lane in index}
    section, in_interfaces = None, False
    for ln in s.splitlines():
        m = re.match(r"^### (SL-\d+)\b", ln)
        if m:
            section, in_interfaces = m.group(1), False
            continue
        if ln.startswith("## "):
            section, in_interfaces = None, False
        if section not in needed:
            continue
        if "**Interfaces consumed**" in ln:
            in_interfaces = True
            needed[section] |= in_phase_refs(ln)
            continue
        if in_interfaces:
            # A wrapped interfaces line continues until the next bullet, table
            # row, heading or blank line.
            if not ln.strip() or re.match(r"^\s*(- \*\*|\||#)", ln):
                in_interfaces = False
            else:
                needed[section] |= in_phase_refs(ln)
                continue
        row = re.match(r"^\|\s*(SL-\d+)\.\d+\s*\|[^|]*\|([^|]*)\|", ln)
        if row and row.group(1) in needed:
            needed[row.group(1)] |= in_phase_refs(row.group(2))

    bad = 0
    for lane, req in sorted(needed.items()):
        req.discard(lane)
        for m in sorted(req - index[lane]["depends"]):
            print(
                f"  [BLOCKING] {name}: {lane} consumes {m} but its Lane Index"
                f" does not list {m} under Depends on"
            )
            bad += 1
    for lane, edges in sorted(index.items()):
        for dep in sorted(edges["depends"] & set(index)):
            if lane not in index[dep]["blocks"]:
                print(
                    f"  [BLOCKING] {name}: {lane} depends on {dep} but {dep}'s"
                    f" Lane Index does not list {lane} under Blocks"
                )
                bad += 1
        for blocked in sorted(edges["blocks"] & set(index)):
            if lane not in index[blocked]["depends"]:
                print(
                    f"  [BLOCKING] {name}: {lane} lists {blocked} under Blocks but"
                    f" {blocked} does not depend on {lane}"
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
