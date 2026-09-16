"""The operator's opt-in to outbound submission, and the startup loads that must record.

Consiliency/pmcp#230, EGRESS lane SL-3. Two things live here, and they are one
lane because both answer the same question -- *under whose authority does pmcp
post?*

* `GuidanceConfig.enable_feedback_submission` is the switch the operator flips.
  It defaults **off**, and "off" has to hold in more than one place: the pydantic
  default, and a `~/.claude/gateway-guidance.yaml` written before this phase
  existed, which has no such key. (The third place -- a `GatewayTools` built with
  `guidance_config=None` -- is SL-4's line and SL-4's test,
  `test_a_gateway_with_no_guidance_config_refuses_to_submit`; `_telemetry_enabled`
  returns **True** for `None`, so a lane copying its shape would ship this flag on
  for every embedder that omits the config.)

* `cli.load_startup_env` loads PMCP's *own* credential stores into PMCP's own
  `os.environ`. Before this lane it recorded nothing, and that is a provenance
  hole rather than an omission: a token a *previous* process's `auth_connect`
  wrote into the store is loaded here with no record, and any later
  `auth_connect` -- for any unrelated server -- can drop it from the store file,
  because `set_env_value` rewrites the whole file from a `read_env_file` that
  returns `{}` for a file it cannot read. All three provenance sources would then
  say "the operator exported this" and the egress gate would honour an
  agent-plantable credential.

The distinction the startup test pins is **provenance, not name**: the delta
around `load_dotenv(..., override=False)` is what separates a key the store
introduced from one the operator exported that merely shares its name. It also
pins that the store loads record into `pmcp_introduced_keys` and **not** into
`dotenv_sourced_keys` -- widening the latter would change
`sanitized_subprocess_env`, which strips its keys from every spawned child, and
that is behaviour outside this phase.
"""

from __future__ import annotations

import os
import socket
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from pmcp import cli
from pmcp.config.guidance import (
    GuidanceConfig,
    create_default_guidance_config,
    load_guidance_config,
    set_feedback_submission_enabled,
)
from pmcp.env_store import (
    dotenv_sourced_keys,
    pmcp_introduced_keys,
    reset_pmcp_introduced_keys,
)

PREFIX = "PMCP_TEST_SL3_"
STORE_KEY = f"{PREFIX}USER_STORE"
PROJECT_KEY = f"{PREFIX}PROJECT_STORE"
DOTENV_KEY = f"{PREFIX}PLAIN_DOTENV"
EXPORTED_KEY = f"{PREFIX}EXPORTED"

# Resolved at import, BEFORE any test redirects HOME: the operator's real
# guidance file. Nothing here may create or modify it.
_REAL_GUIDANCE_FILE = Path.home() / ".claude" / "gateway-guidance.yaml"


def _real_guidance_fingerprint() -> tuple[bool, float, int] | None:
    """Existence, mtime and size of the operator's real guidance file."""
    try:
        stat = _REAL_GUIDANCE_FILE.stat()
    except FileNotFoundError:
        return None
    return (True, stat.st_mtime, stat.st_size)


@pytest.fixture(autouse=True)
def _no_github_egress(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test in this module that reaches out to GitHub.

    Modelled on `tests/conftest.py`'s `_no_live_npm_registry`: record the
    attempt, raise so the caller sees a transport failure rather than a silent
    `None`, and assert at teardown that nothing was recorded. Nothing in SL-3's
    surface should open a socket at all -- this file is about a config flag and
    a dotenv load -- so the guard is a tripwire on the whole module, not a stub
    for a call a test expects.
    """
    attempts: list[str] = []

    def _refuse_urlopen(request: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(getattr(request, "full_url", str(request)))
        raise OSError("outbound HTTP is disabled in this module's tests")

    def _refuse_connect(address: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(str(address))
        raise OSError("outbound sockets are disabled in this module's tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse_urlopen)
    monkeypatch.setattr(socket, "create_connection", _refuse_connect)
    yield attempts
    assert attempts == [], f"a test reached the network: {attempts}"


@pytest.fixture(autouse=True)
def _reset_pmcp_introduced() -> Iterator[None]:
    """`_PMCP_INTRODUCED_KEYS` is process-global and deliberately additive.

    Without a reset on both sides, a key one test records still reads as
    PMCP-introduced in every later test -- an order-dependent failure in tests
    that have nothing to do with this lane. `tests/conftest.py` does the same
    for `dotenv_sourced_keys`; it is CONSENT's file, so this module declares its
    own.
    """
    reset_pmcp_introduced_keys()
    yield
    reset_pmcp_introduced_keys()


@pytest.fixture(autouse=True)
def _drop_test_keys() -> Iterator[None]:
    """`load_dotenv` writes `os.environ` directly, so monkeypatch teardown does
    not restore it -- these tests introduce keys for real. Every key this module
    can introduce shares one prefix; drop them before and after each test."""
    for key in [k for k in os.environ if k.startswith(PREFIX)]:
        del os.environ[key]
    yield
    for key in [k for k in os.environ if k.startswith(PREFIX)]:
        del os.environ[key]


@pytest.fixture(autouse=True)
def _the_operators_real_guidance_file_is_untouched() -> Iterator[None]:
    """The proof, per test, that nothing here wrote to `~/.claude/`.

    Every test that persists a flag redirects HOME to a `tmp_path` or passes an
    explicit `config_path`. This asserts it: a test that forgot would rewrite the
    operator's real settings, and the assertion fires in the test that did it
    rather than in a later bug report.
    """
    before = _real_guidance_fingerprint()
    yield
    assert _real_guidance_fingerprint() == before, (
        f"a test created or modified {_REAL_GUIDANCE_FILE}"
    )


def _redirect_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `Path.home()` at an empty directory and return the guidance path.

    `load_guidance_config`, `set_feedback_submission_enabled` and
    `create_default_guidance_config` all default to
    `Path.home() / ".claude" / "gateway-guidance.yaml"`.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home / ".claude" / "gateway-guidance.yaml"


def _run_guidance_cli(argv: list[str]) -> None:
    """Drive the real CLI: parse `argv` with the production parser, then run it.

    Building the `Namespace` by hand would pass even if the flag were never
    added to the parser, or added with the wrong name or the wrong `choices`.
    """
    with patch("sys.argv", argv):
        args = cli.parse_args()
    assert args.command == "guidance"
    cli.run_guidance(args)


def test_feedback_submission_defaults_to_off() -> None:
    """The pydantic default. Posting is an act an operator opts into."""
    assert GuidanceConfig().enable_feedback_submission is False
    assert GuidanceConfig.model_fields["enable_feedback_submission"].default is False
    # The sibling switch keeps its own, opposite default: the feedback
    # *workflow* is on by default, the outbound *act* is not.
    assert GuidanceConfig().enable_telemetry is True


def test_a_config_without_the_key_reads_as_off(tmp_path: Path) -> None:
    """Default off in the second place: every guidance file written before this
    phase. An operator who configured pmcp last year has no such key, and must
    not find that an upgrade turned posting on."""
    config_file = tmp_path / "gateway-guidance.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "guidance": {
                    "level": "standard",
                    "layers": {"mcp_instructions": True, "code_hints": True},
                    "enable_telemetry": True,
                }
            }
        )
    )

    config = load_guidance_config(config_file)

    assert config.enable_feedback_submission is False
    # The rest of the pre-existing file is still honoured -- this is a file that
    # loaded, not a file that fell back to defaults.
    assert config.level == "standard"
    assert config.enable_telemetry is True


def test_a_generated_default_config_states_the_submission_default(
    tmp_path: Path,
) -> None:
    """A freshly generated file must *state* the default rather than rely on the
    model's, so an operator reading their own config can see the switch exists
    and which way it points."""
    config_path = tmp_path / "gateway-guidance.yaml"

    create_default_guidance_config(output_path=config_path)

    data = yaml.safe_load(config_path.read_text())
    assert "enable_feedback_submission" in data["guidance"], (
        "the generated file must name the flag, not leave it implicit"
    )
    assert data["guidance"]["enable_feedback_submission"] is False
    assert load_guidance_config(config_path).enable_feedback_submission is False


def test_the_cli_verb_persists_the_submission_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pmcp guidance --feedback-submission on` writes the operator's decision,
    and writes *only* it.

    The pre-seeded file carries an unrelated setting and a non-default
    `enable_telemetry`; both must survive, which is what pins the
    read-modify-write of the whole YAML document. Asserting on the raw mapping
    rather than on the loaded model is deliberate: a writer that put the value
    under a different key would still round-trip through `GuidanceConfig`'s
    defaults and read as "off" here, which is the wrong-key mutant.
    """
    config_file = _redirect_home(tmp_path, monkeypatch)
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        yaml.dump(
            {
                "guidance": {"level": "standard", "enable_telemetry": False},
                "some_other_section": {"kept": True},
            }
        )
    )

    _run_guidance_cli(["pmcp", "guidance", "--feedback-submission", "on"])

    data = yaml.safe_load(config_file.read_text())
    assert data["guidance"]["enable_feedback_submission"] is True
    assert data["guidance"]["enable_telemetry"] is False
    assert data["guidance"]["level"] == "standard"
    assert data["some_other_section"] == {"kept": True}
    assert load_guidance_config(config_file).enable_feedback_submission is True


def test_the_cli_verb_turns_the_submission_flag_off_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revocation is the half that matters. `off` must write an explicit
    `False`, not delete the key and not leave the earlier `True` standing."""
    config_file = _redirect_home(tmp_path, monkeypatch)
    set_feedback_submission_enabled(True, config_file)
    assert load_guidance_config(config_file).enable_feedback_submission is True

    _run_guidance_cli(["pmcp", "guidance", "--feedback-submission", "off"])

    data = yaml.safe_load(config_file.read_text())
    assert data["guidance"]["enable_feedback_submission"] is False
    assert load_guidance_config(config_file).enable_feedback_submission is False


def test_the_guidance_status_shows_the_submission_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pmcp guidance` reports the switch beside the telemetry one.

    The two are set to **opposite** values on purpose: a status block that read
    `enable_telemetry` for both lines -- the copy-paste mutant -- prints two
    ticks or two crosses and fails here, while a fixture with both flags on
    would let it pass.
    """
    config_file = _redirect_home(tmp_path, monkeypatch)
    set_feedback_submission_enabled(True, config_file)
    data = yaml.safe_load(config_file.read_text())
    data["guidance"]["enable_telemetry"] = False
    config_file.write_text(yaml.dump(data))

    _run_guidance_cli(["pmcp", "guidance"])

    out = capsys.readouterr().out
    assert "Feedback Telemetry: ✗" in out
    assert "Feedback Submission: ✓" in out


def test_startup_store_loads_are_recorded_as_pmcp_introduced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provenance hole this lane closes.

    `load_startup_env` loads PMCP's own credential stores -- the user store at
    `~/.config/pmcp/pmcp.env` and the project store at `$CWD/.env.pmcp` -- into
    PMCP's own environment. Both deltas must reach `pmcp_introduced_keys`, the
    registry the egress gate consults, because the store file is not durable
    evidence: a later `auth_connect` for an unrelated server rewrites it and can
    drop the entry while the variable it planted stays in `os.environ`.

    Three properties, each a separate way to get this wrong:

    * **Both** stores record, not just the user one.
    * The record is by *provenance*, not by name: `EXPORTED_KEY` is in the user
      store **and** in the operator's shell, and `override=False` means the file
      did not introduce it -- so it is in neither registry and its shell value
      stands.
    * The store keys go to `pmcp_introduced_keys` and **not** to
      `dotenv_sourced_keys`. Widening the latter would make
      `sanitized_subprocess_env` strip them from every spawned child, which is
      behaviour this phase does not change.
    """
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)

    monkeypatch.setenv(EXPORTED_KEY, "from-the-operators-shell")
    (home / ".config" / "pmcp" / "pmcp.env").write_text(
        f"{STORE_KEY}=planted-by-a-previous-process\n{EXPORTED_KEY}=from-the-store\n"
    )
    (project / ".env.pmcp").write_text(f"{PROJECT_KEY}=planted-in-the-project\n")
    (project / ".env").write_text(f"{DOTENV_KEY}=from-a-plain-dotenv\n")

    cli.load_startup_env(project / ".env")

    introduced = pmcp_introduced_keys()
    sourced = dotenv_sourced_keys()

    assert STORE_KEY in introduced, "the user store's delta was not recorded"
    assert PROJECT_KEY in introduced, "the project store's delta was not recorded"

    assert EXPORTED_KEY not in introduced
    assert EXPORTED_KEY not in sourced
    assert os.environ[EXPORTED_KEY] == "from-the-operators-shell"

    assert STORE_KEY not in sourced
    assert PROJECT_KEY not in sourced

    # The plain `.env` keeps the registry it always had (Consiliency/pmcp#229).
    assert DOTENV_KEY in sourced
    assert DOTENV_KEY not in introduced

    # The loads themselves are unchanged: the gateway still sees the values.
    assert os.environ[STORE_KEY] == "planted-by-a-previous-process"
    assert os.environ[PROJECT_KEY] == "planted-in-the-project"
