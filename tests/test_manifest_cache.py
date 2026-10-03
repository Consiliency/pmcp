"""The parsed-manifest cache (Consiliency/pmcp#233).

``load_manifest()`` is cached by the bytes of every source it reads, each
project overlay's consent decision, the ignored-redirect notice and the
platform. These tests pin the four rules a cache can break:

* never stale -- an overlay edit (even one that keeps size and mtime), a
  consent change, an env change, a cwd/HOME change or a platform change is a
  miss;
* never shared -- every caller gets its own deep copy;
* never caches a failure -- an exception, an unreadable source or an
  unparseable overlay is recomputed on the next call;
* warnings once per miss -- a hit is silent, a changed state warns again.

Plus the parser rule: libyaml only for pmcp's own shipped manifest, the
pure-Python SafeLoader for every overlay.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from pmcp import trust_store
from pmcp.env_store import record_pmcp_introduced_keys
from pmcp.manifest import loader
from pmcp.manifest.loader import load_manifest

_SHIPPED = loader._SHIPPED_MANIFEST_PATH


def _user_overlay(text: str) -> Path:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _pin(version: str) -> str:
    return f'server_version:\n  firecrawl: "{version}"\n'


def _firecrawl_version() -> str | None:
    return load_manifest().servers["firecrawl"].version


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count cache misses: every miss, and only a miss, builds a manifest."""
    calls: list[int] = []
    real = loader._build_manifest

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(loader, "_build_manifest", counted)
    return calls


# ---------------------------------------------------------------------------
# A hit does no work; the shipped manifest goes through libyaml
# ---------------------------------------------------------------------------


def test_a_hit_parses_nothing(
    builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _user_overlay(_pin("3.25.5"))
    first = load_manifest()
    parses: list[int] = []
    monkeypatch.setattr(yaml, "load", lambda *a, **k: parses.append(1))
    monkeypatch.setattr(yaml, "safe_load", lambda *a, **k: parses.append(1))
    second = load_manifest()
    assert builds == [1] and parses == []
    assert second == first and second is not first


def _typed(value: Any) -> Any:
    """``value`` with every scalar paired with its exact type: ``==`` treats
    ``1 == True == 1.0``, so a plain comparison would miss an int/bool/float
    divergence between the two parsers."""
    if isinstance(value, dict):
        return ("dict", [(_typed(k), _typed(v)) for k, v in value.items()])
    if isinstance(value, list):
        return ("list", [_typed(v) for v in value])
    return (type(value).__name__, value)


def test_the_shipped_manifest_is_identical_under_both_loaders() -> None:
    content = _SHIPPED.read_bytes()
    assert _typed(loader._parse_trusted_yaml(content)) == _typed(
        yaml.load(content, Loader=yaml.SafeLoader)
    )


@pytest.mark.skipif(not yaml.__with_libyaml__, reason="PyYAML built without libyaml")
def test_the_shipped_manifest_uses_libyaml_when_available() -> None:
    assert loader._trusted_yaml_loader() is yaml.CSafeLoader


def test_without_libyaml_the_shipped_manifest_falls_back_to_safeloader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    monkeypatch.setattr(loader, "_trusted_document", None)
    assert loader._trusted_yaml_loader() is yaml.SafeLoader
    assert load_manifest().servers["firecrawl"].command == "npx"
    # The second shipped parse: its lru_cache is never reset, so clear it to
    # make the fallback actually run, and clear it again afterwards.
    loader._shipped_manifest_entries.cache_clear()
    try:
        assert "firecrawl" in loader._shipped_manifest_entries()
    finally:
        loader._shipped_manifest_entries.cache_clear()


def test_an_overlay_is_still_parsed_by_the_pure_python_safeloader(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """libyaml accepts a tab after `key:` that SafeLoader rejects (measured).
    An overlay must keep main's accept/reject behaviour: skipped, warned."""
    _user_overlay('server_version:\n  firecrawl:\t"3.25.5"\n')
    with caplog.at_level(logging.WARNING):
        assert _firecrawl_version() is None
    assert any("Skipping unreadable manifest overlay" in m for m in _warnings(caplog))


def test_the_parsed_shipped_document_is_keyed_by_its_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = tmp_path / "manifest.yaml"
    shipped.write_bytes(_SHIPPED.read_bytes())
    monkeypatch.setattr(loader, "_SHIPPED_MANIFEST_PATH", shipped)
    assert "firecrawl" in load_manifest().servers
    data = yaml.safe_load(shipped.read_bytes())
    data["servers"]["firecrawl"]["description"] = "edited in place"
    shipped.write_text(yaml.safe_dump(data))
    assert load_manifest().servers["firecrawl"].description == "edited in place"


# ---------------------------------------------------------------------------
# Never stale
# ---------------------------------------------------------------------------


def _rewrite_keeping_stat(path: Path, text: str) -> None:
    """Same size, same mtime: what a stat-keyed cache cannot tell apart."""
    before = path.stat()
    path.write_text(text)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def test_a_user_overlay_edit_is_seen_even_with_size_and_mtime_unchanged() -> None:
    path = _user_overlay(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"
    _rewrite_keeping_stat(path, _pin("3.25.6"))
    assert _firecrawl_version() == "3.25.6"


def test_an_env_overlay_edit_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    assert _firecrawl_version() == "1.0.1"
    _rewrite_keeping_stat(explicit, _pin("1.0.2"))
    assert _firecrawl_version() == "1.0.2"


def test_an_overlay_appearing_and_disappearing_is_seen() -> None:
    assert _firecrawl_version() is None
    path = _user_overlay(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"
    path.unlink()
    assert _firecrawl_version() is None


def test_a_consent_change_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    monkeypatch.chdir(tmp_path / "proj")

    assert _firecrawl_version() is None  # unapproved: contributes nothing
    approve_project_file(project)
    assert _firecrawl_version() == "2.0.1"  # approval alone is a miss
    assert trust_store.revoke(project)
    assert _firecrawl_version() is None  # revocation alone is a miss
    approve_project_file(project)
    assert _firecrawl_version() == "2.0.1"
    _rewrite_keeping_stat(project, _pin("2.0.2"))
    assert _firecrawl_version() is None  # edited bytes are not the approved bytes


def test_an_env_change_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text(_pin("1.0.1"))
    b.write_text(_pin("1.0.2"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(a))
    assert _firecrawl_version() == "1.0.1"
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(b))
    assert _firecrawl_version() == "1.0.2"
    monkeypatch.delenv("PMCP_MANIFEST_PATH")
    assert _firecrawl_version() is None


def test_an_env_provenance_change_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Same value, but now introduced by pmcp rather than exported: ignored."""
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    assert _firecrawl_version() == "1.0.1"
    record_pmcp_introduced_keys({"PMCP_MANIFEST_PATH"})
    with caplog.at_level(logging.WARNING):
        assert _firecrawl_version() is None
    assert any("PMCP_MANIFEST_PATH" in m for m in _warnings(caplog))


def test_a_cwd_change_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    for name, version in (("one", "1.0.1"), ("two", "1.0.2")):
        project = tmp_path / name / ".pmcp" / "manifest.yaml"
        project.parent.mkdir(parents=True)
        project.write_text(_pin(version))
        approve_project_file(project)
    monkeypatch.chdir(tmp_path / "one")
    assert _firecrawl_version() == "1.0.1"
    monkeypatch.chdir(tmp_path / "two")
    assert _firecrawl_version() == "1.0.2"


def test_a_home_change_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _user_overlay(_pin("1.0.1"))
    assert _firecrawl_version() == "1.0.1"
    other = tmp_path / "other-home"
    monkeypatch.setenv("HOME", str(other))
    _user_overlay(_pin("1.0.2"))
    assert _firecrawl_version() == "1.0.2"


def test_a_platform_change_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_on_windows()` decides which launcher spellings are pinned."""
    _user_overlay(
        "servers:\n"
        "  winpin:\n"
        "    description: d\n"
        "    command: npx.cmd\n"
        "    args: ['-y', 'winpin-mcp']\n"
        "    version: '1.0.0'\n"
    )
    monkeypatch.setattr(loader, "_on_windows", lambda: False)
    assert load_manifest().servers["winpin"].version is None
    monkeypatch.setattr(loader, "_on_windows", lambda: True)
    assert load_manifest().servers["winpin"].version == "1.0.0"


def test_the_bytes_hashed_are_the_bytes_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overlay rewritten between the read and the parse must not be parsed:
    the result (and its cache entry) belongs to the bytes that were keyed."""
    path = _user_overlay(_pin("3.25.5"))
    real = loader._gather_overlay_sources

    def gather_then_rewrite(notices: list[str]) -> Any:
        sources = real(notices)
        path.write_text(_pin("9.9.9"))
        return sources

    monkeypatch.setattr(loader, "_gather_overlay_sources", gather_then_rewrite)
    assert _firecrawl_version() == "3.25.5"


# ---------------------------------------------------------------------------
# Never shared
# ---------------------------------------------------------------------------


def _mutable_ids(obj: Any, seen: set[int] | None = None) -> set[int]:
    """ids of every list/dict/set reachable from obj (dataclasses walked)."""
    seen = set() if seen is None else seen
    if isinstance(obj, (list, dict, set)):
        if id(obj) in seen:
            return seen
        seen.add(id(obj))
        items = obj.items() if isinstance(obj, dict) else enumerate(obj)
        for _k, v in items:
            _mutable_ids(v, seen)
    elif hasattr(obj, "__dataclass_fields__"):
        seen.add(id(obj))
        for name in obj.__dataclass_fields__:
            _mutable_ids(getattr(obj, name), seen)
    return seen


def test_no_two_callers_share_a_mutable_object() -> None:
    _user_overlay(_pin("3.25.5"))
    first, second = load_manifest(), load_manifest()  # miss, then hit
    third = load_manifest()  # hit
    a, b, c = _mutable_ids(first), _mutable_ids(second), _mutable_ids(third)
    assert not (a & b) and not (b & c) and not (a & c)


def test_mutating_a_result_never_reaches_the_next_caller() -> None:
    first = load_manifest()
    server = first.servers["firecrawl"]
    server.args.append("--evil")
    server.extra_env["NODE_OPTIONS"] = "--require /tmp/x.js"
    server.keywords.clear()
    for argv in server.install.values():
        argv.append("--evil")
    server.version = "0.0.1"
    first.servers.pop("github", None)
    first.cli_alternatives.clear()

    fresh = load_manifest()
    assert "--evil" not in fresh.servers["firecrawl"].args
    assert "NODE_OPTIONS" not in fresh.servers["firecrawl"].extra_env
    assert fresh.servers["firecrawl"].keywords
    assert all("--evil" not in a for a in fresh.servers["firecrawl"].install.values())
    assert fresh.servers["firecrawl"].version is None
    assert "github" in fresh.servers and fresh.cli_alternatives


# ---------------------------------------------------------------------------
# Never caches a failure
# ---------------------------------------------------------------------------


def test_an_exception_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = loader._parse_server_config
    state = {"fail": True}

    def flaky(name: str, data: dict[str, Any]) -> Any:
        if state["fail"]:
            raise RuntimeError("boom")
        return real(name, data)

    monkeypatch.setattr(loader, "_parse_server_config", flaky)
    with pytest.raises(RuntimeError):
        load_manifest()
    state["fail"] = False
    assert "firecrawl" in load_manifest().servers


def test_a_missing_explicit_manifest_is_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    with pytest.raises(FileNotFoundError):
        load_manifest(path)
    path.write_text("servers: {}\ncli_alternatives: {}\n")
    assert load_manifest(path).servers == {}


@pytest.mark.parametrize(
    "broken",
    ["server_version: [unclosed\n", "- a list, not a mapping\n"],
    ids=["yaml-error", "not-a-mapping"],
)
def test_an_unparseable_overlay_is_recomputed_every_call(
    broken: str, builds: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    path = _user_overlay(broken)
    with caplog.at_level(logging.WARNING):
        load_manifest()
        load_manifest()
    skipped = [m for m in _warnings(caplog) if "manifest overlay" in m]
    assert builds == [1, 1] and len(skipped) == 2  # not cached: warns each call
    path.write_text(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"


def test_an_unreadable_overlay_is_recomputed_every_call(builds: list[int]) -> None:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.mkdir(parents=True)  # exists, but read_bytes() raises IsADirectoryError
    load_manifest()
    load_manifest()
    assert builds == [1, 1]
    path.rmdir()
    _user_overlay(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"


# ---------------------------------------------------------------------------
# Warnings: once per miss
# ---------------------------------------------------------------------------


def test_warnings_are_emitted_once_per_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _user_overlay('server_version:\n  firecrawl: "^3"\n')
    with caplog.at_level(logging.WARNING):
        load_manifest()
    assert len([m for m in _warnings(caplog) if m.startswith("Ignoring a '")]) == 1

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        load_manifest()  # hit: the operator has already been told
    assert _warnings(caplog) == []

    caplog.clear()
    path.write_text('server_version:\n  firecrawl: "~3"\n')
    with caplog.at_level(logging.WARNING):
        load_manifest()  # a new state is a new miss: told again
    assert len([m for m in _warnings(caplog) if m.startswith("Ignoring a '")]) == 1


def test_a_consent_refusal_is_logged_once_per_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    monkeypatch.chdir(tmp_path / "proj")
    with caplog.at_level(logging.WARNING):
        load_manifest()
        load_manifest()
    refusals = [m for m in _warnings(caplog) if "pmcp trust approve" in m]
    assert len(refusals) == 1


def test_an_ignored_redirect_is_logged_once_per_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    record_pmcp_introduced_keys({"PMCP_MANIFEST_PATH"})
    with caplog.at_level(logging.WARNING):
        load_manifest()
        load_manifest()
    assert len([m for m in _warnings(caplog) if "PMCP_MANIFEST_PATH" in m]) == 1


def test_an_ignored_redirect_after_a_clean_load_is_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unset, then set by pmcp itself: the overlay list is the same (empty)
    both times, so only the notice in the key makes the second load a miss."""
    load_manifest()
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    record_pmcp_introduced_keys({"PMCP_MANIFEST_PATH"})
    with caplog.at_level(logging.WARNING):
        assert _firecrawl_version() is None
    assert any("PMCP_MANIFEST_PATH" in m for m in _warnings(caplog))


def test_clearing_the_cache_warns_again(caplog: pytest.LogCaptureFixture) -> None:
    _user_overlay('server_version:\n  firecrawl: "^3"\n')
    load_manifest()
    loader.clear_manifest_cache()
    with caplog.at_level(logging.WARNING):
        load_manifest()
    assert any(m.startswith("Ignoring a '") for m in _warnings(caplog))


def _refusals(caplog: pytest.LogCaptureFixture) -> int:
    return len([m for m in _warnings(caplog) if "pmcp trust approve" in m])


def _count_per_load(caplog: pytest.LogCaptureFixture, count: Any, step: Any) -> int:
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        step()
        load_manifest()
    return int(count(caplog))


def test_every_consent_transition_warns_even_when_the_state_is_still_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Any,
) -> None:
    """Round 1 (Consiliency/pmcp#331, codex B2 / claude F1): unapproved ->
    approve -> revoke reused the first state's LRU entry and was silent. One
    warning per transition into a refusal, none while it stays (C-12)."""
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    monkeypatch.chdir(tmp_path / "proj")

    def nothing() -> None:
        return None

    def approve() -> None:
        approve_project_file(project)

    def revoke() -> None:
        assert trust_store.revoke(project)

    steps = [nothing, nothing, approve, revoke, nothing, approve, revoke]
    counts = [_count_per_load(caplog, _refusals, step) for step in steps]
    assert counts == [1, 0, 0, 1, 0, 0, 1]


def test_retargeting_a_project_overlay_symlink_is_a_new_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1 (codex B2): one unapproved target, then another. The refusal
    names the resolved file, so its identity must be part of the key."""
    pmcp_dir = tmp_path / "proj" / ".pmcp"
    pmcp_dir.mkdir(parents=True)
    (pmcp_dir / "one.yaml").write_text(_pin("2.0.1"))
    (pmcp_dir / "two.yaml").write_text(_pin("2.0.1"))
    link = pmcp_dir / "manifest.yaml"
    link.symlink_to(pmcp_dir / "one.yaml")
    monkeypatch.chdir(tmp_path / "proj")

    def nothing() -> None:
        return None

    def retarget(name: str) -> Any:
        def step() -> None:
            link.unlink()
            link.symlink_to(pmcp_dir / name)

        return step

    named: list[str] = []

    def refusals_naming(caplog: pytest.LogCaptureFixture) -> int:
        lines = [m for m in _warnings(caplog) if "pmcp trust approve" in m]
        named.extend(lines)
        return len(lines)

    steps = [nothing, retarget("two.yaml"), nothing, retarget("one.yaml")]
    counts = [_count_per_load(caplog, refusals_naming, step) for step in steps]
    assert counts == [1, 1, 0, 1]
    assert "one.yaml" in named[0] and "two.yaml" in named[1]


def test_breaking_fixing_and_breaking_again_warns_each_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 1 (claude F1): the same broken bytes twice, with a fix between."""
    bad, good = 'server_version:\n  firecrawl: "^3"\n', _pin("3.25.5")
    path = _user_overlay(bad)

    def bad_pins(caplog: pytest.LogCaptureFixture) -> int:
        return len([m for m in _warnings(caplog) if m.startswith("Ignoring a '")])

    def write(text: str) -> Any:
        return lambda: path.write_text(text)

    steps = [write(bad), write(bad), write(good), write(bad), write(bad)]
    assert [_count_per_load(caplog, bad_pins, step) for step in steps] == [
        1,
        0,
        0,
        1,
        0,
    ]


def _deep_alias_overlay(depth: int) -> str:
    """Round 1 (codex B1): chained anchors. 11 KB at depth 500; parses with
    SafeLoader, but the result nests deeper than pickle can recurse."""
    text = "d0: &d0 {v: 0}\n" + "".join(
        f"d{i}: &d{i} {{v: *d{i - 1}}}\n" for i in range(1, depth + 1)
    )
    return text + (
        "servers:\n"
        "  custom:\n"
        "    description: deep\n"
        "    command: echo\n"
        f"    discovery_metadata: *d{depth}\n"
    )


@pytest.mark.parametrize("depth", [300, 500, 2000])
def test_a_deeply_aliased_overlay_loads_as_on_main(
    depth: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1 (codex B1): 500 chained anchors made pickle raise on 3.10/3.11.
    Whether the result is cached depends on the interpreter (3.12 pickles it),
    so this asserts only what main guarantees: the load succeeds, twice, and
    nothing logged carries a value."""
    _user_overlay(_deep_alias_overlay(depth))
    with caplog.at_level(logging.DEBUG, logger="pmcp.manifest.loader"):
        first, second = load_manifest(), load_manifest()
    for result in (first, second):
        assert "custom" in result.servers and "firecrawl" in result.servers
    # Not `==`: comparing a 2000-deep structure recurses too.
    for result in (first, second):
        assert result.servers["custom"].command == "echo"
        assert result.servers["custom"].description == "deep"
    assert not [m for m in _warnings(caplog) if "Manifest cache" in m]
    assert all("'v'" not in r.getMessage() for r in caplog.records)


def _fail_with(exc: BaseException) -> Any:
    def fail(*_a: Any, **_k: Any) -> Any:
        raise exc

    return fail


@pytest.mark.parametrize(
    "exc",
    [RecursionError("maximum recursion depth exceeded"), TypeError("secret-xyz")],
    ids=["recursion", "other"],
)
def test_a_result_that_cannot_be_stored_is_returned_uncached(
    exc: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    builds: list[int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deterministic on every Python: the store itself is made to fail."""
    _user_overlay(_pin("3.25.5"))
    monkeypatch.setattr(loader.pickle, "dumps", _fail_with(exc))
    with caplog.at_level(logging.DEBUG, logger="pmcp.manifest.loader"):
        assert _firecrawl_version() == "3.25.5"
        assert _firecrawl_version() == "3.25.5"
    assert builds == [1, 1] and len(loader._manifest_cache) == 0
    assert all("secret-xyz" not in r.getMessage() for r in caplog.records)


def test_a_store_failure_other_than_recursion_warns_once_per_process(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 2 (claude nit): a cache that is off must be visible to an
    operator -- once, by exception class only. Recursion stays at DEBUG."""
    _user_overlay(_pin("3.25.5"))
    monkeypatch.setattr(loader.pickle, "dumps", _fail_with(RecursionError("deep")))
    with caplog.at_level(logging.WARNING):
        load_manifest()
    assert not [m for m in _warnings(caplog) if "Manifest cache" in m]

    caplog.clear()
    monkeypatch.setattr(loader.pickle, "dumps", _fail_with(TypeError("secret-xyz")))
    with caplog.at_level(logging.WARNING):
        for text in (_pin("3.25.6"), _pin("3.25.7"), _pin("3.25.8")):
            _user_overlay(text)
            load_manifest()
    lines = [m for m in _warnings(caplog) if "Manifest cache" in m]
    assert len(lines) == 1 and "TypeError" in lines[0] and "secret-xyz" not in lines[0]


@pytest.mark.parametrize(
    "exc",
    [RecursionError("maximum recursion depth exceeded"), TypeError("secret-xyz")],
    ids=["recursion", "other"],
)
def test_a_cached_result_that_cannot_be_read_back_is_rebuilt(
    exc: BaseException, monkeypatch: pytest.MonkeyPatch, builds: list[int]
) -> None:
    _user_overlay(_pin("3.25.5"))
    load_manifest()
    monkeypatch.setattr(loader.pickle, "loads", _fail_with(exc))
    assert _firecrawl_version() == "3.25.5"
    assert builds == [1, 1]


@pytest.mark.parametrize("reason", ["no_record", "content_changed"])
def test_editing_a_refused_project_overlay_is_a_new_refusal(
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Any,
) -> None:
    """Round 2 (codex B2): the gate hands back no bytes for a refused file, so
    the key carries the digest of the bytes it read. Every edit of a refused
    file -- never approved, or changed since approval -- warns once."""
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    if reason == "content_changed":
        approve_project_file(project)
        project.write_text(_pin("2.0.2"))
    monkeypatch.chdir(tmp_path / "proj")

    def nothing() -> None:
        return None

    def edit(version: str) -> Any:
        return lambda: project.write_text(_pin(version))

    steps = [nothing, nothing, edit("2.0.3"), nothing, edit("2.0.4"), edit("2.0.4")]
    counts = [_count_per_load(caplog, _refusals, step) for step in steps]
    assert counts == [1, 0, 1, 0, 1, 0]
    assert _firecrawl_version() is None  # never applied


def test_the_gate_reports_the_digest_of_the_bytes_it_refused(tmp_path: Path) -> None:
    """The refused file's bytes are never handed over; their digest is."""
    import hashlib

    from pmcp.project_consent import read_and_gate

    path = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(_pin("2.0.1"))
    content, decision = read_and_gate(path, "project_manifest")
    assert content is None and not decision.allowed
    assert decision.content_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_unreadable_project_overlay_warns_on_every_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The third refusal reason: never cached (D4.2), so it warns each load,
    exactly as main does, until it is fixed."""
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.mkdir(parents=True)  # exists; reading it raises
    monkeypatch.chdir(tmp_path / "proj")

    def nothing() -> None:
        return None

    counts = [_count_per_load(caplog, _refusals, nothing) for _ in range(3)]
    assert counts == [1, 1, 1]


def test_explicit_path_calls_do_not_turn_default_loads_into_transitions(
    tmp_path: Path, builds: list[int]
) -> None:
    """Round 2 (claude nit): each kind of call has its own last-served slot."""
    explicit = tmp_path / "m.yaml"
    explicit.write_text("servers: {}\ncli_alternatives: {}\n")
    for _ in range(3):
        load_manifest()
        load_manifest(explicit)
    assert builds == [1, 1]


# ---------------------------------------------------------------------------
# Threads, bound, and the request path
# ---------------------------------------------------------------------------


def test_concurrent_cold_callers_build_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = loader._build_manifest
    calls: list[int] = []

    def slow(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        time.sleep(0.2)  # widen the window a missing lock would race in
        return real(*args, **kwargs)

    monkeypatch.setattr(loader, "_build_manifest", slow)
    barrier = threading.Barrier(6)
    results: list[Any] = []

    def worker() -> None:
        barrier.wait()
        results.append(load_manifest())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]
    assert len(results) == 6 and all(r == results[0] for r in results)
    assert len({id(r) for r in results}) == 6


def test_the_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit.yaml"
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    for i in range(loader._MANIFEST_CACHE_SLOTS + 5):
        explicit.write_text(_pin(f"1.0.{i}"))
        load_manifest()
    assert len(loader._manifest_cache) == loader._MANIFEST_CACHE_SLOTS


@pytest.mark.asyncio
async def test_a_catalog_search_builds_the_manifest_at_most_once(
    builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's shape: one search used to parse the manifest up to 13 times
    (2 direct, 1 via load_configs, 1 per manifest and registry candidate)."""
    from unittest.mock import MagicMock

    from pmcp.manifest.registry import RegistryCache, RegistryServerEntry
    from pmcp.tools.handlers import GatewayTools

    entries = [
        RegistryServerEntry(name=f"io.example/{w}", description=f"{w} search database")
        for w in ("alpha", "beta", "gamma", "delta", "eps")
    ]

    async def registry(self: Any) -> RegistryCache:
        return RegistryCache(
            schema_version="v1", source_endpoint="x", fetched_at="now", servers=entries
        )

    monkeypatch.setattr(GatewayTools, "_load_registry_candidates", registry)
    calls: list[int] = []
    real_load = loader.load_manifest

    def counted() -> Any:
        calls.append(1)
        return real_load()

    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", counted)
    monkeypatch.setattr(loader, "load_manifest", counted)
    cm = MagicMock()
    cm.get_all_tools.return_value = []
    cm.is_server_online.return_value = False
    cm.get_all_server_statuses.return_value = []
    pm = MagicMock()
    pm.is_tool_allowed.return_value = True
    pm.is_server_allowed.return_value = True
    pm.scoped_advisor_active = False
    tools = GatewayTools(client_manager=cm, policy_manager=pm)
    query = {"query": "search database browser web git", "include_offline": True}
    await tools.catalog_search(query)
    await tools.catalog_search(query)
    assert len(calls) >= 12  # still called per site (8 per search here) ...
    assert builds == [1]  # ... but built once
