"""A parse error never echoes the structured text it rejected
(Consiliency/pmcp#297, rev 6; the rev 5 board's codex finding).

PyYAML renders a snippet of the input around the error mark (and a
constructor error names the input's tag), so a policy, manifest, overlay,
guidance or cache file with a secret on a malformed line put that line into
the log, and -- for an explicit policy -- into the raised error's chain.
`exception_text` now renders every parse error of every structured-text
parser pmcp uses as `could not parse <FORMAT> (<Class>) at line L, column
C`, and `safe_exc_info` / the scrubber / the excepthook withhold such a
traceback.

Two halves:
- **static**: every parse call in `src/pmcp` is listed below with its
  disposition, so a new one fails this test until classified;
- **dynamic**: a sentinel in each parser's rejected input at every
  file-reading site pmcp exposes as a function, checked in the log (every
  record at DEBUG), the raised message, and the traceback the gateway
  would print uncaught; plus a fresh interpreter for the fatal policy path.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text

_SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"

#: Every parse call in `src/pmcp`, by `file::function`, with how its failure
#: is rendered. A new parse call fails `test_every_parse_site_is_classified`.
_PARSE_SITES: dict[str, str] = {
    # -- files pmcp reads: errors reach logs / raised text; swept below --
    "policy/policy.py::_parse_policy": "swept (YAML and JSON; warn and fatal)",
    "manifest/loader.py::_parse_overlay_document": "swept (YAML overlay warning)",
    "manifest/loader.py::load_manifest": "swept (YAML; raised)",
    "config/loader.py::parse_config_bytes": "swept (JSON warning)",
    "config/loader.py::_config_object_from_bytes": "swept (JSON diagnostic string)",
    "config/guidance.py::load_guidance_config": "swept (YAML; printed warning)",
    "config/guidance.py::set_telemetry_enabled": "error swallowed (`except Exception: data = {}`)",
    "config/guidance.py::set_feedback_submission_enabled": "error swallowed (as above)",
    "manifest/code_patterns_loader.py::_load_patterns": "swept (YAML; printed warning)",
    "templates/code_snippets_loader.py::_load_snippets": "swept (YAML; printed warning)",
    "manifest/refresher.py::load_descriptions_cache": "swept (YAML cache warning)",
    "manifest/registry.py::load_registry_cache": "swept (JSON cache)",
    "trust_store.py::_read_store": "swept (JSON; raised TrustStoreError)",
    "package_approvals.py::_read_store_and_stale": "swept (JSON; raised PackageApprovalError)",
    "tools/handlers.py::_load_provisioned_registry": "logged via exception_text (static guard)",
    "cli.py::run_setup": "operator CLI; printed via exception_text (static guard)",
    "cli.py::_load_local_mcp_json": "operator CLI; error swallowed (`except Exception`)",
    "cli.py::_extract_tool_payload": "a gateway response to the CLI; error swallowed",
    # -- python-dotenv: never raises on bad input; it logs "could not parse
    #    statement starting at line N" (line number only, measured) --
    "env_store.py::read_env_file": "python-dotenv (dotenv_values)",
    "cli.py::load_startup_env": "python-dotenv (load_dotenv)",
    "tools/handlers.py::_check_api_key_available": "python-dotenv (load_dotenv)",
    # -- downstream / network payloads: JSONDecodeError's own text is fixed
    #    vocabulary, and every one of these renders a fixed message --
    "client/manager.py::_handle_stdout_line": "stdio frame; the non-JSON line is logged as the downstream's output (non-goal)",
    "auth.py::_fetch": "JWKS body; raised with a fixed message `from exc`",
    "auth.py::fetch_json_metadata": "discovery document; fixed diagnostics, else sanitize_auth_diagnostic (-> exception_text)",
    "auth.py::parse_url_elicitation_error": "parses an error payload; returns structured URLs",
    "manifest/package_identity.py::_fetch_packument": "npm packument; logged via exception_text",
    "manifest/npm_resolver.py::_spawn": "resolver child's line; refused with fixed text",
    "manifest/npm_resolver.py::_query_locked": "resolver child's line; refused with fixed text",
    "manifest/registry.py::_fetch_registry_servers_uncached": "registry HTTP body; the caller's except renders via exception_text (static guard)",
    "feedback_egress.py::_classify_response": "GitHub API body; fixed `unparseable_body`",
    "feedback_egress.py::_probe_repository_visibility": "GitHub API body; fixed `unknown`",
    "transport/http.py::handle_mcp": "caller's HTTP body; only `.get('method')` read, error swallowed",
    "tools/handlers.py::tasks_result": "downstream result payload; decoded opportunistically, error swallowed",
    "policy/policy.py::process_output": "a result string being truncated; error swallowed",
    "redaction_additive.py::_clip_to_json_strings": "a probe; error swallowed",
    "scoped_advisor_audit.py::validate_scoped_advisor_audit": "pmcp's own audit file; raised from a fixed message",
}

_PARSERS = {
    ("yaml", "safe_load"),
    ("yaml", "load"),
    ("yaml", "full_load"),
    ("yaml", "safe_load_all"),
    ("json", "loads"),
    ("json", "load"),
    ("tomllib", "loads"),
    ("tomllib", "load"),
}
_DOTENV = {"dotenv_values", "load_dotenv"}


def _parse_sites() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        functions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = (
                (func.value.id, func.attr)
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                else None
            )
            if name not in _PARSERS and not (
                isinstance(func, ast.Name) and func.id in _DOTENV
            ):
                continue
            owner = min(
                (
                    f
                    for f in functions
                    if f.lineno <= call.lineno <= (f.end_lineno or f.lineno)
                ),
                key=lambda f: (f.end_lineno or f.lineno) - f.lineno,
                default=None,
            )
            key = f"{path.relative_to(_SRC)}::{owner.name if owner else '<module>'}"
            found[key] = found.get(key, 0) + 1
    return found


def test_every_parse_site_is_classified() -> None:
    sites = _parse_sites()
    assert len(sites) > 20, sites
    assert set(sites) == set(_PARSE_SITES), (
        sorted(set(sites) - set(_PARSE_SITES)),
        sorted(set(_PARSE_SITES) - set(sites)),
    )


# --- dynamic ---------------------------------------------------------------------


def _yaml_bad(s: str) -> str:
    """Four ways PyYAML rejects input, each quoting it in its message."""
    return f"servers: [{s}}}\n"


def _yaml_bads(s: str) -> list[str]:
    return [
        f"servers: [{s}}}\n",  # ParserError (flow sequence)
        f"a: b\n  c: {s}: d\n",  # ScannerError (mapping values)
        f'k: "{s}\n',  # ScannerError (unterminated quote)
        f"k: !{s} v\n",  # ConstructorError (the tag is the input)
    ]


def _json_bads(s: str) -> list[str]:
    return [f'{{"k": {s}}}', f'{{"{s}": 1,}}', f"[1, 2, {s}"]


def _run(fn: Callable[[], Any]) -> tuple[str, str]:
    """(the raised error as pmcp renders it, the traceback the gateway would
    print uncaught) for `fn`. pmcp renders an exception only through
    `exception_text` -- the static guard pins that at every sink -- so that,
    not the library's own `str()`, is what reaches a caller; a message pmcp
    builds itself (`ValueError(f"... {exception_text(e)}")`) is covered by
    the same call."""
    try:
        from pmcp.argument_errors import exception_text, safe_traceback_text
    except ImportError:  # a tree without the renderer (main, for the red run)
        import traceback

        def exception_text(error: BaseException) -> str:
            return str(error)

        def safe_traceback_text(error: BaseException) -> str:
            return "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )

    try:
        fn()
    except BaseException as error:  # noqa: BLE001 -- inspected
        return exception_text(error), safe_traceback_text(error)
    return "", ""


def _rejects(parser: str, text: str) -> bool:
    import yaml

    try:
        (yaml.safe_load if parser == "yaml" else json.loads)(text)
    except (yaml.YAMLError, ValueError):
        return True
    return False


def _policy(tmp: Path, content: str, suffix: str, fatal: bool) -> Callable[[], Any]:
    from pmcp.policy.policy import PolicyManager

    path = tmp / f"policy{suffix}"
    path.write_text(content)
    return lambda: PolicyManager()._parse_policy(content, path, fatal=fatal)


def _write(tmp: Path, name: str, content: str) -> Path:
    path = tmp / name
    path.write_text(content)
    return path


def _cases() -> list[tuple[str, str, Callable[[Path, str], Callable[[], Any]]]]:
    """(label, parser, builder(tmp, bad text) -> call)."""
    from pmcp import package_approvals, trust_store
    from pmcp.config import guidance
    from pmcp.config import loader as config_loader
    from pmcp.manifest import loader as manifest_loader
    from pmcp.manifest import refresher, registry
    from pmcp.manifest.code_patterns_loader import CodePatternsLoader
    from pmcp.templates.code_snippets_loader import CodeSnippetsLoader

    return [
        ("policy yaml, warn", "yaml", lambda t, c: _policy(t, c, ".yaml", False)),
        ("policy yaml, fatal", "yaml", lambda t, c: _policy(t, c, ".yaml", True)),
        ("policy json, warn", "json", lambda t, c: _policy(t, c, ".json", False)),
        ("policy json, fatal", "json", lambda t, c: _policy(t, c, ".json", True)),
        (
            "manifest overlay",
            "yaml",
            lambda t, c: lambda: manifest_loader._parse_overlay_document(
                t / "overlay.yaml", c.encode()
            ),
        ),
        (
            "manifest",
            "yaml",
            lambda t, c: lambda: manifest_loader.load_manifest(_write(t, "m.yaml", c)),
        ),
        (
            "config file",
            "json",
            lambda t, c: lambda: config_loader.parse_config_bytes(
                c.encode(), t / ".mcp.json"
            ),
        ),
        (
            "config object",
            "json",
            lambda t, c: lambda: _returned(
                config_loader._config_object_from_bytes(c.encode())
            ),
        ),
        (
            "guidance",
            "yaml",
            lambda t, c: lambda: guidance.load_guidance_config(_write(t, "g.yaml", c)),
        ),
        (
            "code patterns",
            "yaml",
            lambda t, c: lambda: CodePatternsLoader(_write(t, "p.yaml", c)),
        ),
        (
            "code snippets",
            "yaml",
            lambda t, c: lambda: CodeSnippetsLoader(_write(t, "s.yaml", c)),
        ),
        (
            "descriptions cache",
            "yaml",
            lambda t, c: lambda: refresher.load_descriptions_cache(
                _write(t, "d.yaml", c)
            ),
        ),
        (
            "registry cache",
            "json",
            lambda t, c: lambda: registry.load_registry_cache(_write(t, "r.json", c)),
        ),
        (
            "trust store",
            "json",
            lambda t, c: lambda: trust_store._read_store(_write(t, "trust.json", c)),
        ),
        (
            "package approvals",
            "json",
            lambda t, c: lambda: package_approvals._read_store(_write(t, "a.json", c)),
        ),
    ]


def _returned(value: Any) -> None:
    """A site that returns its diagnostic instead of raising: make the
    returned text the 'raised' text."""
    raise RuntimeError(repr(value))


@pytest.mark.parametrize("label", [c[0] for c in _cases()])
@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_no_parse_site_echoes_its_input(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    label: str,
    family: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    (case,) = [c for c in _cases() if c[0] == label]
    _, parser, build = case
    s = _FAMILIES[family][1]
    # Only inputs the parser rejects: a digits-only sentinel makes `{"k": 123}`
    # valid JSON, which is data, not a parse error.
    bads = [
        bad
        for bad in (_yaml_bads(s) if parser == "yaml" else _json_bads(s))
        if _rejects(parser, bad)
    ]
    assert bads, (label, family)
    for bad in bads:
        start = len(caplog.records)
        capfd.readouterr()
        raised, shown = _run(build(tmp_path, bad))
        streams = capfd.readouterr()
        logged = "\n".join(_record_text(r) for r in caplog.records[start:])
        for channel, text in (
            ("raised", raised),
            ("traceback", shown),
            ("log", logged),
            ("stdout/stderr", streams.out + streams.err),
        ):
            assert not any(form in text for form in _forbidden(s)), (
                label,
                bad,
                channel,
                text,
            )
        # No vacuous pass: pmcp said it could not parse, or said nothing at
        # all (a site that falls back silently, e.g. a cache miss).
        everything = raised + shown + logged + streams.out + streams.err
        assert "could not parse" in everything or not (
            raised or shown or streams.out.strip() or streams.err.strip()
        ), (label, bad, everything)


def test_an_uncaught_fatal_policy_error_prints_no_input(tmp_path: Path) -> None:
    """An explicit policy that does not parse is fatal: the gateway exits with
    the error uncaught, and the interpreter prints it. Its chain holds the
    YAML error, whose text quotes the file; the excepthook `import pmcp`
    installs prints it structurally."""
    s = _FAMILIES["token"][1]
    policy = tmp_path / "policy.yaml"
    policy.write_text(_yaml_bad(s))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pmcp.policy.policy import PolicyManager; "
            "PolicyManager(policy_path=__import__('pathlib').Path(sys.argv[1]))",
            str(policy),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "HOME": str(tmp_path)},
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "could not parse YAML (ParserError) at line 1" in result.stderr, (
        result.stderr
    )
    assert "Traceback (most recent call last)" in result.stderr
    assert not any(form in result.stderr + result.stdout for form in _forbidden(s)), (
        result.stderr
    )


def test_parse_text_names_only_format_class_and_position() -> None:
    import yaml

    from pmcp.argument_errors import exception_text

    s = _FAMILIES["alpha"][1]
    for bad, expected in (
        (f"servers: [{s}}}", "could not parse YAML (ParserError) at line 1, column "),
        (f"k: !{s} v", "could not parse YAML (ConstructorError) at line 1, column "),
    ):
        try:
            yaml.safe_load(bad)
        except yaml.YAMLError as error:
            # PyYAML's snippet truncates long lines; any window is the leak.
            assert any(form in str(error) for form in _forbidden(s)), str(error)
            assert exception_text(error).startswith(expected), exception_text(error)
    try:
        json.loads(f'{{"k": {s}}}')
    except json.JSONDecodeError as error:
        assert (
            exception_text(error)
            == "could not parse JSON (JSONDecodeError) at line 1, column 7"
        )
