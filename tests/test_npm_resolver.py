"""Tests for the nopt-backed npm identity resolver (Consiliency/pmcp#195).

The subject is a *refusal contract*, so most of these assert that something
does NOT happen: a refusal never reaches the flag tables, a poisoned argv never
kills the child, a failed self-test never degrades to guessing.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pmcp.manifest import version_checker
from pmcp.manifest.npm_resolver import (
    _gate_relevant_env,
    _has_local_prefix,
    NpmResolution,
    NpmResolver,
)
from pmcp.manifest.version_checker import (
    _npm_package_arg,
    detect_package_type,
    get_package_version,
)

HELPER = Path(version_checker.__file__).with_name("_npm_resolve.js")


def _node_available() -> bool:
    return shutil.which("node") is not None


requires_node = pytest.mark.skipif(
    not _node_available(), reason="the resolver needs node on PATH"
)


@pytest.fixture
def resolver() -> NpmResolver:
    """A private resolver, so a test cannot poison the process-wide singleton."""
    instance = NpmResolver()
    yield instance
    instance.close()


def _live(resolver: NpmResolver) -> None:
    """Skip when npm is absent; FAIL when it is present but refusing."""
    probe = resolver.resolve("npx", ["-y", "left-pad"], {}, None)
    if probe.is_unavailable:
        pytest.skip(f"npm resolver unavailable: {probe.reason}")
    if not probe.is_identity:
        pytest.fail(f"the resolver refused a plain form: {probe.reason}")


# ---------------------------------------------------------------------------
# The child's wire protocol
# ---------------------------------------------------------------------------


class TestChildProtocol:
    """The NDJSON contract, driven directly rather than through the parent."""

    @staticmethod
    def _run(requests: list[dict[str, object]]) -> list[dict[str, object]]:
        payload = "".join(json.dumps(r) + "\n" for r in requests)
        proc = subprocess.run(
            ["node", str(HELPER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]

    @requires_node
    def test_handshake_precedes_every_answer(self) -> None:
        """The handshake must be the FIRST line, always.

        The self-test runs at spawn over this same stdout, so without a
        handshake the parent's first `resolve()` could read a self-test line as
        its own answer -- a wrong `Identity`, the same mis-attribution class as
        an unmatched in-flight response.
        """
        lines = self._run([{"id": 1, "command": "npx", "args": ["-y", "left-pad"]}])
        assert lines, "the child produced no output at all"
        assert lines[0].get("handshake") == 1, lines[0]
        assert "handshake" not in lines[1]

    @requires_node
    def test_ids_are_echoed_so_answers_cannot_be_misattributed(self) -> None:
        lines = self._run(
            [
                {"id": 7, "command": "npx", "args": ["-y", "aaa"]},
                {"id": 8, "command": "npx", "args": ["-y", "bbb"]},
            ]
        )
        answers = [line for line in lines if "handshake" not in line]
        assert [(a["id"], a.get("spec")) for a in answers] == [(7, "aaa"), (8, "bbb")]

    @requires_node
    def test_a_poisoned_argv_is_contained(self) -> None:
        """`--__proto__=evil` throws inside nopt; real npx DIES on it.

        The per-request try/catch must contain it, and -- this is the half that
        matters -- the very next query must still be answered correctly. A
        containment that leaves the process wedged is not containment.
        """
        lines = self._run(
            [
                {"id": 1, "command": "npx", "args": ["--__proto__=evil", "pkg"]},
                {"id": 2, "command": "npx", "args": ["--constructor", "pkg"]},
                {"id": 3, "command": "npx", "args": ["-y", "left-pad"]},
            ]
        )
        answers = {line["id"]: line for line in lines if "handshake" not in line}
        assert answers[1]["status"] == "REFUSED"
        assert answers[2]["status"] == "REFUSED"
        assert answers[3] == {
            "id": 3,
            "status": "IDENTITY",
            "spec": "left-pad",
            "name": "left-pad",
            "npaType": "range",
        }

    @requires_node
    def test_malformed_input_does_not_kill_the_child(self) -> None:
        proc = subprocess.run(
            ["node", str(HELPER)],
            input='not json\n{"id":1,"command":"npx","args":["-y","left-pad"]}\n',
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
        assert lines[-1]["status"] == "IDENTITY"

    @requires_node
    def test_the_child_exits_when_stdin_closes(self) -> None:
        """A parent crash must not orphan the child."""
        proc = subprocess.Popen(
            ["node", str(HELPER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0


# ---------------------------------------------------------------------------
# Step 1 -- the gates
# ---------------------------------------------------------------------------

# Every step-1 / step-2 / step-3 refusal path for which the 2.5.2 tables return
# a NON-None -- and therefore wrong -- answer. Verified: each `tables` value
# below is what `_npm_package_arg_from_tables` actually returns today.
#
# The two-distinct-`--package` gate is deliberately absent: the tables already
# return `("unknown", None)` there, so there is no wrong answer to suppress and
# no implementation can satisfy a "tables would be wrong" assertion for it.
GATE_CASES: list[tuple[str, str, list[str], dict[str, str] | None, bool, str]] = [
    # (id, command, args, env overlay, needs_local_prefix_cwd, table answer)
    (
        "env-npm_config_package",
        "npx",
        ["-y", "probe"],
        {"npm_config_package": "evil"},
        False,
        "probe",
    ),
    (
        "env-NPM_CONFIG_REGISTRY",
        "npx",
        ["-y", "probe"],
        {"NPM_CONFIG_REGISTRY": "http://x"},
        False,
        "probe",
    ),
    ("env-PATH", "npx", ["-y", "probe"], {"PATH": "/x"}, False, "probe"),
    ("env-HOME", "npx", ["-y", "probe"], {"HOME": "/x"}, False, "probe"),
    (
        "env-NODE_OPTIONS",
        "npx",
        ["-y", "probe"],
        {"NODE_OPTIONS": "--require /x"},
        False,
        "probe",
    ),
    ("env-NODE_PATH", "npx", ["-y", "probe"], {"NODE_PATH": "/x"}, False, "probe"),
    ("env-PREFIX", "npx", ["-y", "probe"], {"PREFIX": "/x"}, False, "probe"),
    ("env-NVM_DIR", "npx", ["-y", "probe"], {"NVM_DIR": "/x"}, False, "probe"),
    ("cwd-local-prefix", "npx", ["-y", "probe"], None, True, "probe"),
    (
        "flag-userconfig",
        "npx",
        ["--userconfig", "/tmp/rc", "probe"],
        None,
        False,
        "probe",
    ),
    ("flag-registry", "npx", ["--registry", "http://x", "probe"], None, False, "probe"),
    ("flag-prefix", "npx", ["--prefix", "/dir", "probe"], None, False, "probe"),
    ("flag-cache", "npx", ["--cache", "/dir", "probe"], None, False, "probe"),
    ("flag-workspace", "npm", ["exec", "-w", "ws", "probe"], None, False, "probe"),
    ("flag-call", "npx", ["--package=p", "--call=echo hi"], None, False, "p"),
    (
        "npa-alias",
        "npx",
        ["-y", "myalias-zz@npm:left-pad"],
        None,
        False,
        "myalias-zz@npm:left-pad",
    ),
    ("npa-git", "npx", ["-y", "github:owner/repo"], None, False, "github:owner/repo"),
    ("npm-dlx", "npm", ["dlx", "x"], None, False, "x"),
]


class TestARefusalNeverReachesTheTables:
    """The single most important property in this change.

    A refusal that falls through to the flag tables re-mints exactly the
    confident wrong answers the resolver exists to remove: `--userconfig` and
    `--registry` both scan to `probe` while npm fetches something else, and an
    alias scans to a name npm never runs.

    **Asserted on the RESULT, parametrized over every gate.** An earlier draft
    specified a monkeypatched call counter and dropped the result assertion; an
    implementation whose REFUSED path called the scan through a def-time alias
    then passed it -- the counter saw zero because the monkeypatch has exactly
    the def-time blindness the counter was meant to close, and nothing checked
    what came back. A second draft scoped the criterion to three named forms,
    and an implementation that refused those three and fell back on the env gate
    passed that too. Hence: every gate, and the result.

    The counter below only SUPPLEMENTS the result assertion, and it is
    instrumented inside `_npm_package_arg_from_tables` itself rather than
    monkeypatched, so a def-time binding cannot hide from it.
    """

    @pytest.mark.parametrize(
        ("command", "args", "env", "needs_prefix_cwd", "table_answer"),
        [pytest.param(*case[1:], id=case[0]) for case in GATE_CASES],
    )
    @requires_node
    def test_gate_refuses_where_the_tables_would_guess(
        self,
        resolver: NpmResolver,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        command: str,
        args: list[str],
        env: dict[str, str] | None,
        needs_prefix_cwd: bool,
        table_answer: str,
    ) -> None:
        _live(resolver)
        monkeypatch.setattr(version_checker, "get_resolver", lambda: resolver)

        cwd = None
        if needs_prefix_cwd:
            (tmp_path / "package.json").write_text("{}")
            cwd = str(tmp_path)

        # 1. The tables really would answer, and answer WRONGLY. Without this
        #    the case proves nothing: a gate whose input the tables also refuse
        #    passes trivially.
        assert version_checker._npm_package_arg_from_tables(args, command) == (
            table_answer
        )

        before = version_checker._table_scan_calls

        # 2. The result. This is the assertion that bites.
        assert _npm_package_arg(args, command, env, cwd) is None
        assert detect_package_type(command, args, env, cwd) == ("unknown", None)

        # 3. And the scan was not reached. Supplementary; see the class
        #    docstring for why it cannot stand alone. `before` is captured after
        #    the deliberate call above, so that one is not counted.
        assert version_checker._table_scan_calls == before

    @requires_node
    def test_the_gateways_own_environment_is_gated_too(
        self, resolver: NpmResolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An `npm_config_*` in pmcp's OWN environment redirects every server.

        Verified against the real binary:
        `env npm_config_package=evil-pkg npm_config_registry=http://127.0.0.1:9
        npx -y probe` fetches `http://127.0.0.1:9/evil-pkg`. The overlay gate
        cannot see it, so it is checked separately and is sticky for the
        process.
        """
        _live(resolver)
        monkeypatch.setattr(version_checker, "get_resolver", lambda: resolver)
        monkeypatch.setenv("npm_config_package", "evil-pkg")

        before = version_checker._table_scan_calls
        assert _npm_package_arg(["-y", "probe"], "npx", None, None) is None
        assert detect_package_type("npx", ["-y", "probe"], None, None) == (
            "unknown",
            None,
        )
        assert version_checker._table_scan_calls == before

        # Sticky: it stays refused for this resolver's lifetime even after the
        # variable goes away, because the resolver keeps no child to re-check
        # with and a silent recovery would be indistinguishable from never
        # having noticed.
        monkeypatch.delenv("npm_config_package")
        assert resolver.resolve("npx", ["-y", "probe"], None, None).is_refused

    def test_the_env_gate_reads_the_overlay_not_a_merged_environment(self) -> None:
        """The rev-5 defect, pinned.

        `sanitized_subprocess_env` returns `os.environ` plus the server's own
        keys, and every process has PATH and HOME -- so gating the merged
        environment refuses every npm server on every host. Only the overlay is
        gated.
        """
        assert _gate_relevant_env({"MY_TOKEN": "x"}) == {}
        assert _gate_relevant_env(None) == {}
        assert _gate_relevant_env({"PATH": "/x"}) == {"PATH": "/x"}
        assert _gate_relevant_env({"npm_config_registry": "r"}) == {
            "npm_config_registry": "r"
        }
        # Case-insensitive, because npm matches `/^npm_config_/i`.
        assert _gate_relevant_env({"NPM_CONFIG_PACKAGE": "p"}) == {
            "NPM_CONFIG_PACKAGE": "p"
        }

    def test_the_local_prefix_walk_starts_at_the_effective_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """npm resolves from the PROCESS cwd when a server declares none.

        Reading only the server's own `cwd` was wrong, and left the whole
        project-`.npmrc` class open for every server in the manifest (none of
        which sets `cwd` at all).

        Asserted as *which* directory the walk reports rather than as a bare
        present/absent, because the absent case is not testable under an
        arbitrary temp root: pytest puts `tmp_path` under `/tmp`, and a stray
        `/tmp/package.json` -- which this suite does not control and which this
        very host has -- makes npm set a local prefix for every path under it.
        That is the gate being CORRECT, not a flake, so the test states the
        nearest-prefix property, which holds either way.
        """
        project = tmp_path / "proj"
        (project / "deep").mkdir(parents=True)
        (project / "package.json").write_text("{}")
        clean = tmp_path / "clean"
        clean.mkdir()

        deep = _has_local_prefix(str(project / "deep"))
        assert deep is not None
        # The NEAREST ancestor wins, so it is `proj` and not some directory
        # further up that happens to contain a package.json.
        assert str(project) in deep

        # No prefix at or below `clean` itself. Anything reported must come
        # from strictly above the temp root, i.e. from ambient host state.
        for probe in (_has_local_prefix(str(clean)), None):
            if probe is not None:
                assert str(clean) not in probe
                assert str(tmp_path) not in probe

        monkeypatch.chdir(project / "deep")
        from_process_cwd = _has_local_prefix(None)
        assert from_process_cwd is not None
        assert str(project) in from_process_cwd

    def test_node_modules_alone_sets_a_local_prefix(self, tmp_path: Path) -> None:
        """npm's rule is `package.json` OR `node_modules`, not just the former.

        With `node_modules/.bin/<name>` present, npx runs the LOCAL binary with
        no registry fetch at all -- so the registry name is not the package that
        runs.
        """
        (tmp_path / "node_modules").mkdir()
        assert _has_local_prefix(str(tmp_path)) is not None


# ---------------------------------------------------------------------------
# Steps 2 and 3 -- what resolves and what refuses
# ---------------------------------------------------------------------------


class TestTheRequiredToResolveList:
    """The plain shapes MUST resolve. Refusing everything is not a fix."""

    @pytest.mark.parametrize(
        ("command", "args", "spec"),
        [
            ("npx", ["-y", "left-pad"], "left-pad"),
            ("npx", ["left-pad"], "left-pad"),
            ("npm", ["exec", "left-pad"], "left-pad"),
            ("npm", ["exec", "--package=left-pad", "--", "bin"], "left-pad"),
            ("npx", ["-y", "left-pad@1.2.3"], "left-pad@1.2.3"),
            ("npx", ["-y", "@scope/pkg"], "@scope/pkg"),
            # The one manifest entry with a flag AFTER the package. The npx
            # pre-scan inserts `--` before the first positional, so
            # `--project-ref` lands in `remain` rather than in the parsed config
            # -- the cheapest regression canary for that insertion.
            (
                "npx",
                ["-y", "@supabase/mcp-server-supabase", "--project-ref", "X"],
                "@supabase/mcp-server-supabase",
            ),
        ],
    )
    @requires_node
    def test_form_resolves(
        self, resolver: NpmResolver, command: str, args: list[str], spec: str
    ) -> None:
        _live(resolver)
        result = resolver.resolve(command, args, {}, None)
        assert result.status == "IDENTITY", result.reason
        assert result.spec == spec

    @requires_node
    def test_the_spec_comes_back_raw_with_its_tag_intact(
        self, resolver: NpmResolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_strip_npm_tag` stays in `detect_package_type`.

        gateway.update_server's pin detection needs the suffix to tell a pinned
        server from an unpinned one, so the two functions answer differently by
        design and the criterion names which is which.
        """
        _live(resolver)
        monkeypatch.setattr(version_checker, "get_resolver", lambda: resolver)
        assert _npm_package_arg(["exec", "pkg@1.2.3"], "npm", None, None) == "pkg@1.2.3"
        assert detect_package_type("npm", ["exec", "pkg@1.2.3"], None, None) == (
            "npm",
            "pkg",
        )

    @pytest.mark.parametrize(
        "args",
        [
            ["run", "mcp"],
            ["start"],
            ["test"],
            ["-y", "server-pkg"],
            ["dlx", "x"],
        ],
    )
    @requires_node
    def test_npm_forms_without_a_package_operand_refuse(
        self, resolver: NpmResolver, monkeypatch: pytest.MonkeyPatch, args: list[str]
    ) -> None:
        """Consiliency/pmcp#183, preserved through the resolver.

        `dlx` is new here: it is pnpm/yarn spelling (`npm dlx probe` is
        `Unknown command`), and it sat in the tables' subcommand allowlist, so
        the tables mint an identity for a server that can never launch.
        """
        _live(resolver)
        monkeypatch.setattr(version_checker, "get_resolver", lambda: resolver)
        assert detect_package_type("npm", args, None, None) == ("unknown", None)

    @pytest.mark.parametrize(
        "args",
        [
            ["run", "--package=pkg-a", "--", "bin"],
            ["start", "--package=pkg-a"],
            ["test", "--package=pkg-a"],
            ["create", "--package=pkg-a", "--", "bin"],
            ["dlx", "--package=pkg-a", "--", "bin"],
            ["rum", "--package=pkg-a", "--", "bin"],
            ["--package=pkg-a"],
        ],
    )
    @requires_node
    def test_package_does_not_short_circuit_the_subcommand_allowlist(
        self, resolver: NpmResolver, monkeypatch: pytest.MonkeyPatch, args: list[str]
    ) -> None:
        """Consiliency/pmcp#183, reopened by the `--package` rule and closed again.

        The subcommand check used to run only in the `--package`-ABSENT branch,
        so `--package` bypassed it entirely. Measured on the broken code, every
        one of these minted `('npm', 'pkg-a')` -- and `handlers.py` would then
        have fetched and executed `pkg-a@latest --help` off the public registry
        for a server whose command line runs a package.json SCRIPT (board review
        on the diff, correctness seat).

        `--package` says which package a command comes FROM. It cannot turn a
        script runner into a package installer, so the two questions -- "does
        this subcommand name a package at all" and "where does the name come
        from" -- are independent, and the first gates the second.
        """
        _live(resolver)
        monkeypatch.setattr(version_checker, "get_resolver", lambda: resolver)
        before = version_checker._table_scan_calls
        assert detect_package_type("npm", args, None, None) == ("unknown", None)
        assert version_checker._table_scan_calls == before

    @requires_node
    def test_npx_has_no_subcommand_so_package_still_wins(
        self, resolver: NpmResolver
    ) -> None:
        """The guard is npm-scoped: npx takes the package directly."""
        _live(resolver)
        result = resolver.resolve("npx", ["--package=pkg-a", "--", "bin"], {}, None)
        assert result.status == "IDENTITY", result.reason
        assert result.spec == "pkg-a"

    @requires_node
    def test_the_package_flag_outranks_the_positional(
        self, resolver: NpmResolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Consiliency/pmcp#182: the positional is the BINARY, not a package."""
        _live(resolver)
        monkeypatch.setattr(version_checker, "get_resolver", lambda: resolver)
        assert detect_package_type(
            "npm", ["exec", "--package=pkg", "--", "bin"], None, None
        ) == ("npm", "pkg")

    @requires_node
    def test_the_leading_exec_token_is_not_the_package(
        self, resolver: NpmResolver
    ) -> None:
        """`npx-cli.js` splices `exec` into argv, so `remain[0]` is `exec`.

        `exec` is a real published package (the registry returns HTTP 200), so
        reading `remain[0]` would have made gateway.update_server probe
        `npx -y exec@latest --help` for all 79 npx servers in the manifest.
        """
        _live(resolver)
        assert resolver.resolve("npx", ["-y", "probe-xyz"], {}, None).spec == (
            "probe-xyz"
        )
        assert resolver.resolve("npx", ["-y"], {}, None).is_refused

    @pytest.mark.parametrize(
        ("command", "args"),
        [
            ("npm", ["exec", "--", "--flag-thing"]),
            ("npm", ["exec", "--", "-x"]),
            ("npx", ["--", "--flag-thing"]),
        ],
    )
    @requires_node
    def test_a_leading_dash_operand_refuses_on_every_npm(
        self, resolver: NpmResolver, command: str, args: list[str]
    ) -> None:
        """The npm majors DISAGREE about what this names, so it cannot be named.

        Measured against both real binaries with a dead registry:

            npm 10.9.4   `npm exec -- --flag-thing` fetches `/--flag-thing`
                         (npa 12: `{type: 'range', name: '--flag-thing'}`)
            npm 11.19.0  the same argv fetches `/undefined`
                         (npa 13: `{type: 'tag', name: undefined}`)

        Two installed npms, two different packages, one command line. Refusing
        is the only answer that is true on both -- and it is what lets the
        spawn-time self-test be an INVARIANT rather than a description of
        whichever npm the author had. Without this rule the self-test passed on
        npm 11 and failed on npm 10, which disabled the whole resolver on every
        npm 10 host (caught by CI, not by this machine).
        """
        _live(resolver)
        result = resolver.resolve(command, args, {}, None)
        assert result.is_refused, result
        assert 'starts with "-"' in (result.reason or "")

    @pytest.mark.parametrize(
        ("command", "args"),
        [
            ("npm", ["exec", "--", "--flag-thing"]),  # npm fetches `/undefined`
            ("npx", ["--package="]),  # ditto, and `undefined` exists
            ("npx", ["-y"]),
            ("npx", ["--"]),
            ("npx", []),
        ],
    )
    @requires_node
    def test_a_degenerate_spec_refuses(
        self, resolver: NpmResolver, command: str, args: list[str]
    ) -> None:
        """An invalid spec makes npm fetch the package `undefined`, which EXISTS.

        Minting `--flag-thing` or `""` here would collapse every such server
        onto one degenerate key.
        """
        _live(resolver)
        assert resolver.resolve(command, args, {}, None).is_refused


# ---------------------------------------------------------------------------
# Tri-state and lifecycle
# ---------------------------------------------------------------------------


class TestTriState:
    def test_the_three_states_are_distinct(self) -> None:
        identity = NpmResolution(status="IDENTITY", spec="p")
        refused = NpmResolution(status="REFUSED", reason="r")
        unavailable = NpmResolution(status="UNAVAILABLE", reason="r")
        assert [identity.is_identity, identity.is_refused, identity.is_unavailable] == [
            True,
            False,
            False,
        ]
        assert [refused.is_identity, refused.is_refused, refused.is_unavailable] == [
            False,
            True,
            False,
        ]
        assert [
            unavailable.is_identity,
            unavailable.is_refused,
            unavailable.is_unavailable,
        ] == [False, False, True]

    def test_unavailable_falls_through_to_the_tables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ONE state that may consult the tables, and only it."""

        class _Unavailable:
            def resolve(self, command, args, env, cwd):  # type: ignore[no-untyped-def]
                return NpmResolution(status="UNAVAILABLE", reason="no node")

        monkeypatch.setattr(version_checker, "get_resolver", lambda: _Unavailable())
        before = version_checker._table_scan_calls
        assert _npm_package_arg(["-y", "probe"], "npx", None, None) == "probe"
        assert version_checker._table_scan_calls == before + 1

    def test_refused_does_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Refused:
            def resolve(self, command, args, env, cwd):  # type: ignore[no-untyped-def]
                return NpmResolution(status="REFUSED", reason="gate")

        monkeypatch.setattr(version_checker, "get_resolver", lambda: _Refused())
        before = version_checker._table_scan_calls
        assert _npm_package_arg(["-y", "probe"], "npx", None, None) is None
        assert version_checker._table_scan_calls == before


class TestSpawnFailure:
    """A node-less host: sticky UNAVAILABLE, one attempt, no spawn storm."""

    @pytest.fixture
    def nodeless(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NpmResolver:
        empty = tmp_path / "emptybin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        instance = NpmResolver()
        yield instance
        instance.close()

    def test_one_spawn_attempt_across_fifty_resolves(
        self, nodeless: NpmResolver
    ) -> None:
        results = [
            nodeless.resolve("npx", ["-y", f"pkg-{i}"], {}, None) for i in range(50)
        ]
        assert all(r.is_unavailable for r in results), results[0]
        # Attempts, not successes: on a node-less host `Popen` raises and no
        # child is ever created, so counting successes would make this true
        # whatever the code did.
        assert nodeless.spawn_attempts == 1
        assert nodeless.spawn_count == 0

    def test_a_node_less_host_uses_the_tables(
        self, nodeless: NpmResolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(version_checker, "get_resolver", lambda: nodeless)
        assert detect_package_type("npx", ["-y", "probe"], None, None) == (
            "npm",
            "probe",
        )


class TestHungChild:
    """A hung child stalls a caller at most once, bounded by the 1.0 s timeout.

    Split from the node-less criterion deliberately: on a node-less host there
    is no child, so "a hung child stalls a caller at most once" is vacuous
    there. This runs on a node-ful host with a helper that answers the
    handshake and then never speaks again.
    """

    @pytest.fixture
    def hung_helper(self, tmp_path: Path) -> Path:
        helper = tmp_path / "hung.js"
        helper.write_text(
            'process.stdout.write(JSON.stringify({handshake:1,status:"OK",'
            'npmVersion:"1.2.3",npmRoot:"/x",npxCliSha256:"z"})+"\\n");\n'
            "setInterval(() => {}, 1 << 30);\n"
        )
        return helper

    @requires_node
    def test_the_first_caller_stalls_and_no_later_one_does(
        self, hung_helper: Path
    ) -> None:
        instance = NpmResolver(helper=hung_helper)
        try:
            start = time.monotonic()
            first = instance.resolve("npx", ["-y", "p0"], {}, None)
            first_elapsed = time.monotonic() - start
            assert first.is_refused, first
            assert 0.5 < first_elapsed < 5.0, first_elapsed

            start = time.monotonic()
            for i in range(1, 20):
                assert instance.resolve("npx", ["-y", f"p{i}"], {}, None).is_refused
            rest_elapsed = time.monotonic() - start
            # 19 further callers, none of which may wait on the child again --
            # the cooldown answers them immediately.
            assert rest_elapsed < 0.5, rest_elapsed
            assert instance.spawn_attempts == 1
        finally:
            instance.close()

    @requires_node
    def test_a_hung_child_never_poisons_the_tables(
        self, hung_helper: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = NpmResolver(helper=hung_helper)
        try:
            monkeypatch.setattr(version_checker, "get_resolver", lambda: instance)
            before = version_checker._table_scan_calls
            assert detect_package_type("npx", ["-y", "probe"], None, None) == (
                "unknown",
                None,
            )
            assert version_checker._table_scan_calls == before
        finally:
            instance.close()


# ---------------------------------------------------------------------------
# Drift -- the self-test and the hash tripwire
# ---------------------------------------------------------------------------


def _fixture_npm_root(
    tmp_path: Path, nopt_source: str | None = None, real_parser: bool = False
) -> Path:
    """Build a real-shaped npm installation whose `nopt` is *nopt_source*.

    Proven end-to-end against an npm root, not by injecting a flag into a
    response: the child discovers this npm through `PATH` exactly as it would a
    real one, hashes its `npx-cli.js`, `createRequire`s its `node_modules`, and
    runs its self-test corpus against them.
    """
    root = tmp_path / "npmroot" / "npm"
    (root / "bin").mkdir(parents=True)
    # `name: "npm"` matters: `isNpmRoot` checks it, so a directory that merely
    # has a `package.json` and a `bin/npx-cli.js` is not mistaken for npm.
    (root / "package.json").write_text(json.dumps({"name": "npm", "version": "99.0.0"}))
    real = Path(
        subprocess.run(
            [
                "node",
                "-e",
                "const fs=require('fs'),cp=require('child_process');"
                "process.stdout.write(fs.realpathSync("
                "cp.execSync('command -v npx',{shell:'/bin/bash'}).toString().trim()))",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    # Copy the REAL npx-cli.js so the hash tripwire is satisfied and the
    # self-test is what fails.
    shutil.copy(real, root / "bin" / "npx-cli.js")

    if real_parser:
        # Symlink the HOST npm's own node_modules, so this fixture is a fully
        # working npm whose only fiction is its `package.json` version -- which
        # is exactly the input the drift re-stat reads.
        os.symlink(real.parent.parent / "node_modules", root / "node_modules")
        return root

    assert nopt_source is not None, "a fake root needs a nopt_source"
    modules = root / "node_modules"
    (modules / "nopt").mkdir(parents=True)
    (modules / "nopt" / "package.json").write_text(
        json.dumps({"name": "nopt", "version": "9.0.0", "main": "index.js"})
    )
    (modules / "nopt" / "index.js").write_text(nopt_source)

    definitions = modules / "@npmcli" / "config" / "lib" / "definitions"
    definitions.mkdir(parents=True)
    (modules / "@npmcli" / "config" / "package.json").write_text(
        json.dumps({"name": "@npmcli/config", "version": "0.0.0"})
    )
    (definitions / "index.js").write_text(
        "module.exports = {definitions: {yes: {type: Boolean, default: false}}, "
        "shorthands: {y: ['--yes']}}\n"
    )

    npa = modules / "npm-package-arg"
    npa.mkdir(parents=True)
    (npa / "package.json").write_text(
        json.dumps({"name": "npm-package-arg", "version": "0.0.0", "main": "index.js"})
    )
    (npa / "index.js").write_text(
        "module.exports = (spec) => ({type: 'range', name: spec})\n"
    )
    return root


def _bin_with_fixture_npm(tmp_path: Path, root: Path) -> Path:
    """A PATH directory holding only `node` and this fixture's `npx`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    node = shutil.which("node")
    assert node is not None
    os.symlink(node, bindir / "node")
    os.symlink(root / "bin" / "npx-cli.js", bindir / "npx")
    return bindir


# A nopt that parses nothing correctly. The self-test corpus expects
# `npx -y left-pad` -> `left-pad`; this yields `WRONG` for everything.
_LYING_NOPT = (
    "module.exports = function () { return {argv: {remain: ['exec', 'WRONG']}} }\n"
)


class TestSelfTestFailureRefuses:
    """A failed self-test REFUSES. It does not degrade to the tables.

    A self-test failure is precisely the evidence that the host's parser
    behaves in a way this code does not model. Responding by consulting the
    known-incomplete 2.5.2 tables is the worst available choice -- it is
    fail-OPEN in exactly the situation that proves the model is wrong. An
    earlier revision of the design specified that fallback; it is the defect
    this test exists to keep out.
    """

    @requires_node
    def test_refuses_and_warns_exactly_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        root = _fixture_npm_root(tmp_path, _LYING_NOPT)
        monkeypatch.setenv("PATH", str(_bin_with_fixture_npm(tmp_path, root)))
        instance = NpmResolver()
        try:
            monkeypatch.setattr(version_checker, "get_resolver", lambda: instance)
            before = version_checker._table_scan_calls
            with caplog.at_level(logging.WARNING, logger="pmcp.manifest.npm_resolver"):
                assert _npm_package_arg(["-y", "probe"], "npx", None, None) is None
                assert detect_package_type("npx", ["-y", "probe"], None, None) == (
                    "unknown",
                    None,
                )
                # ...and it stays refused, still without touching the tables.
                for i in range(5):
                    assert instance.resolve("npx", ["-y", f"p{i}"], {}, None).is_refused

            assert version_checker._table_scan_calls == before
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert len(warnings) == 1, [r.getMessage() for r in warnings]
            assert "self-test failed" in warnings[0].getMessage()
        finally:
            instance.close()

    @requires_node
    def test_an_unrecognised_npx_cli_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hash tripwire has a specified firing action: the REFUSED path.

        The self-test is tautological with respect to the ported pre-scan --
        port and expectations were frozen by the same author -- so the hash is
        the only thing that can detect pre-scan drift. `bin/npx-cli.js` is
        byte-identical across npm 10.8.2 .. 11.19.0, so this never fires on a
        supported npm; when it does fire, the port is unverified against that
        npm and refusing is the honest answer.
        """
        root = _fixture_npm_root(tmp_path, _LYING_NOPT)
        (root / "bin" / "npx-cli.js").write_text("// not npm's npx-cli.js\n")
        monkeypatch.setenv("PATH", str(_bin_with_fixture_npm(tmp_path, root)))
        instance = NpmResolver()
        try:
            result = instance.resolve("npx", ["-y", "probe"], {}, None)
            assert result.is_refused, result
            assert "hash not recognised" in (result.reason or "")
        finally:
            instance.close()

    @requires_node
    def test_a_parser_that_will_not_load_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """npm is HERE but this code cannot model it -- so it refuses.

        Distinct from `UNAVAILABLE`, which is reserved for learning nothing
        about npm at all. Answering from tables generated against a DIFFERENT
        npm would be the same fail-open as the self-test case.
        """
        root = _fixture_npm_root(tmp_path, _LYING_NOPT)
        shutil.rmtree(root / "node_modules" / "nopt")
        monkeypatch.setenv("PATH", str(_bin_with_fixture_npm(tmp_path, root)))
        instance = NpmResolver()
        try:
            result = instance.resolve("npx", ["-y", "probe"], {}, None)
            assert result.is_refused, result
            assert "cannot load npm's parser" in (result.reason or "")
        finally:
            instance.close()


# ---------------------------------------------------------------------------
# Memoisation must not defeat the drift defence
# ---------------------------------------------------------------------------


class TestNoMemoisation:
    """Every query reaches the child, so the drift defence is unconditional.

    An earlier version cached answers. The cache was dropped with the child and
    on a handshake npm-version change -- but both fire only at lifecycle
    boundaries, and a cache HIT never reaches the child, so the child's
    per-resolve re-stat of npm's own `package.json` could not fire either. An
    in-place npm upgrade mid-session would then have left a stale identity
    served indefinitely from cache, by a resolver whose entire purpose is not
    answering from a stale model (board review on the diff).
    """

    def test_the_resolver_holds_no_cache_at_all(self) -> None:
        instance = NpmResolver()
        try:
            assert not hasattr(instance, "_memo")
        finally:
            instance.close()

    @requires_node
    def test_a_repeated_query_is_asked_again(self, resolver: NpmResolver) -> None:
        """Same argv twice: two requests on the wire, not one answered from cache."""
        _live(resolver)
        first = resolver._next_id
        assert resolver.resolve("npx", ["-y", "aaa"], {}, None).spec == "aaa"
        assert resolver.resolve("npx", ["-y", "aaa"], {}, None).spec == "aaa"
        assert resolver._next_id == first + 2

    @requires_node
    def test_an_in_place_npm_upgrade_is_noticed_on_the_next_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The end-to-end proof, and the reason the cache had to go.

        Resolve once against a fixture npm; bump that npm's own `package.json`
        version in place; resolve the SAME argv again. The child re-stats per
        resolve, so the second query comes back `STALE` and the child is torn
        down. With a cache in front of it the second query never reached the
        child and the stale answer stood.
        """
        root = _fixture_npm_root(tmp_path, real_parser=True)
        monkeypatch.setenv("PATH", str(_bin_with_fixture_npm(tmp_path, root)))
        instance = NpmResolver()
        try:
            first = instance.resolve("npx", ["-y", "left-pad"], {}, None)
            assert first.status == "IDENTITY", first.reason

            manifest = root / "package.json"
            manifest.write_text(json.dumps({"name": "npm", "version": "99.0.1"}))

            second = instance.resolve("npx", ["-y", "left-pad"], {}, None)
            assert second.is_refused, second
            assert "npm changed under the resolver" in (second.reason or "")
        finally:
            instance.close()


class TestOnlyConfirmedAbsenceIsUnavailable:
    """`UNAVAILABLE` is the ONLY state that may consult the 2.5.2 tables.

    So it must mean exactly one thing: node/npm is not here. Two failures used
    to be misfiled under it, and both were reproduced falling through to the
    tables and resolving `npx --registry https://private.invalid probe` to
    `probe` -- a private-registry name that gateway.update_server would then
    probe against public npmjs.org, where it is squattable (board review on the
    diff).
    """

    PRIVATE = ["--registry", "https://private.invalid", "probe"]

    def _assert_refuses_without_tables(
        self, instance: NpmResolver, monkeypatch: pytest.MonkeyPatch
    ) -> NpmResolution:
        monkeypatch.setattr(version_checker, "get_resolver", lambda: instance)
        result = instance.resolve("npx", self.PRIVATE, {}, None)
        assert result.is_refused, result
        before = version_checker._table_scan_calls
        assert detect_package_type("npx", self.PRIVATE, None, None) == ("unknown", None)
        assert version_checker._table_scan_calls == before
        return result

    def test_a_missing_packaged_helper_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken install is not a node-less host."""
        instance = NpmResolver(helper=tmp_path / "not-shipped.js")
        try:
            result = self._assert_refuses_without_tables(instance, monkeypatch)
            assert "helper script is missing" in (result.reason or "")
        finally:
            instance.close()

    def test_node_present_but_unspawnable_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Permission denied, a resource limit, ETXTBSY -- none means npm is absent."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        node = bindir / "node"
        node.write_text("#!/bin/sh\nexit 0\n")
        node.chmod(0o644)  # present, not executable
        monkeypatch.setenv("PATH", str(bindir))
        instance = NpmResolver()
        try:
            result = self._assert_refuses_without_tables(instance, monkeypatch)
            assert "could not be spawned" in (result.reason or "")
        finally:
            instance.close()

    def test_npm_on_path_but_unlocatable_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """npm we can SEE but cannot locate is not npm that is absent.

        Found on the GitHub Actions runner's bundled node20 tree: it ships
        `<prefix>/bin/npx` as a *copy* of `npx-cli.js` rather than a symlink, so
        walking up from it never reaches the npm package. The resolver reported
        `UNAVAILABLE` and fell straight through to the flag tables -- the same
        fail-open as the missing-helper case. `rootFromEntryPoint` now also
        checks npm's own global layout (`<prefix>/lib/node_modules/npm`), and
        when even that fails with an npm entry point on PATH, it REFUSES.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir()
        node = shutil.which("node")
        assert node is not None
        os.symlink(node, bindir / "node")
        # An `npx` that is a plain file with no npm package anywhere near it.
        (bindir / "npx").write_text("#!/usr/bin/env node\n")
        (bindir / "npx").chmod(0o755)
        monkeypatch.setenv("PATH", str(bindir))
        instance = NpmResolver()
        try:
            result = self._assert_refuses_without_tables(instance, monkeypatch)
            assert "could not be located" in (result.reason or "")
        finally:
            instance.close()

    def test_node_genuinely_absent_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """...and this one, alone, may use the tables."""
        empty = tmp_path / "emptybin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        instance = NpmResolver()
        try:
            monkeypatch.setattr(version_checker, "get_resolver", lambda: instance)
            result = instance.resolve("npx", self.PRIVATE, {}, None)
            assert result.is_unavailable, result
            assert "node is not installed" in (result.reason or "")
            before = version_checker._table_scan_calls
            assert detect_package_type("npx", self.PRIVATE, None, None) == (
                "npm",
                "probe",
            )
            assert version_checker._table_scan_calls == before + 1
        finally:
            instance.close()


# ---------------------------------------------------------------------------
# The signature contract
# ---------------------------------------------------------------------------


class TestIdentityInputsAreRequired:
    """`env`/`cwd` must have no defaults anywhere on the identity path.

    A habitual `= None` would vacate the whole point: an unconverted call site
    would type-check and run, and quietly ask for an answer it cannot trust.
    This pins it in CI; `mypy` failing on a deliberately reverted caller is the
    other half of the proof and is run by hand.
    """

    @pytest.mark.parametrize(
        "func",
        [detect_package_type, _npm_package_arg, get_package_version],
    )
    def test_env_and_cwd_have_no_default(self, func: object) -> None:
        parameters = inspect.signature(func).parameters  # type: ignore[arg-type]
        for name in ("env", "cwd"):
            assert name in parameters, f"{func!r} lost its {name} parameter"
            assert parameters[name].default is inspect.Parameter.empty, (
                f"{func!r}'s {name} acquired a default, which lets an "
                "unconverted call site type-check"
            )

    def test_the_pin_detector_too(self) -> None:
        from pmcp.tools.handlers import _detect_effective_version_pin

        parameters = inspect.signature(_detect_effective_version_pin).parameters
        for name in ("env", "cwd"):
            assert parameters[name].default is inspect.Parameter.empty


class TestHealthReportsResolverState:
    """A fleet-wide loss of npm identity must be answerable, not just logged.

    Sticky refusal is announced by exactly one WARNING at whatever moment the
    first npm server was resolved. In a gateway that runs for days that line is
    long gone from anyone's attention, so `gateway.health` carries the state.
    """

    def test_summary_never_spawns(self, tmp_path: Path) -> None:
        instance = NpmResolver(helper=tmp_path / "does-not-exist.js")
        try:
            assert "not started" in instance.status_summary()
            assert instance.spawn_attempts == 0
        finally:
            instance.close()

    def test_summary_names_a_sticky_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("npm_config_package", "evil-pkg")
        instance = NpmResolver()
        try:
            assert instance.resolve("npx", ["-y", "p"], {}, None).is_refused
            summary = instance.status_summary()
            assert summary.startswith("DISABLED")
            assert "npm_config_package" in summary
        finally:
            instance.close()

    def test_summary_names_the_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "emptybin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        instance = NpmResolver()
        try:
            assert instance.resolve("npx", ["-y", "p"], {}, None).is_unavailable
            assert instance.status_summary().startswith("fallback to flag tables")
        finally:
            instance.close()

    @requires_node
    def test_summary_names_the_npm_version_when_active(
        self, resolver: NpmResolver
    ) -> None:
        _live(resolver)
        assert resolver.status_summary().startswith("active (npm ")


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_the_helper_ships_beside_the_python_module() -> None:
    """The resolver is dead without its `.js`, and a wheel is what ships."""
    assert HELPER.is_file()
    assert HELPER.name == "_npm_resolve.js"
    assert sys.version_info >= (3, 10)
