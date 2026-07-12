"""Tests for PMCP credential env-store helpers, focused on the subprocess
environment sanitization that prevents cross-server secret bleed (#96)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pmcp.env_store import managed_secret_keys, sanitized_subprocess_env


def _write_user_store(home: Path, body: str) -> None:
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "pmcp" / "pmcp.env").write_text(body)


def test_managed_secret_keys_reads_user_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_user_store(tmp_path, "BRIGHTDATA_API_TOKEN=bd\nOPENAI_API_KEY=oai\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert managed_secret_keys() >= {"BRIGHTDATA_API_TOKEN", "OPENAI_API_KEY"}


def test_subprocess_env_strips_other_servers_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server receives its OWN credential but NOT another server's stored one,
    and non-secret ambient vars survive."""
    _write_user_store(tmp_path, "BRIGHTDATA_API_TOKEN=bd\nOPENAI_API_KEY=oai\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "bd")
    monkeypatch.setenv("OPENAI_API_KEY", "oai")
    monkeypatch.setenv("PATH", "/usr/bin")

    # brightdata's own resolved env injects the runtime var API_TOKEN.
    env = sanitized_subprocess_env({"API_TOKEN": "bd"})

    assert env["API_TOKEN"] == "bd"  # own credential present
    assert "OPENAI_API_KEY" not in env  # other server's secret stripped
    assert "BRIGHTDATA_API_TOKEN" not in env  # own storage key stripped (runtime-only)
    assert env["PATH"] == "/usr/bin"  # ambient non-secret preserved


def test_subprocess_env_readds_own_var_that_collides_with_managed_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server whose runtime env_var IS a managed key (e.g. browser-use's
    OPENAI_API_KEY) gets its own value back after the strip, while another
    server's secret is still removed."""
    _write_user_store(tmp_path, "BRIGHTDATA_API_TOKEN=bd\nOPENAI_API_KEY=oai\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "bd")
    monkeypatch.setenv("OPENAI_API_KEY", "oai")

    env = sanitized_subprocess_env({"OPENAI_API_KEY": "oai"})

    assert env["OPENAI_API_KEY"] == "oai"  # own var re-added
    assert "BRIGHTDATA_API_TOKEN" not in env  # other server's secret stripped


def test_subprocess_env_does_not_mutate_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripping happens on the COPY — os.environ itself is untouched, so upstream
    credential resolution that reads os.environ.get(storage_key) is unaffected by
    a later spawn."""
    import os

    _write_user_store(tmp_path, "BRIGHTDATA_API_TOKEN=bd\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "bd")

    _ = sanitized_subprocess_env({"API_TOKEN": "bd"})

    # The storage key must still be resolvable from os.environ for the NEXT
    # server's credential resolution.
    assert os.environ.get("BRIGHTDATA_API_TOKEN") == "bd"


def test_subprocess_env_no_own_env_still_strips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_user_store(tmp_path, "SOME_TOKEN=x\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SOME_TOKEN", "x")
    monkeypatch.setenv("PATH", "/bin")

    env = sanitized_subprocess_env()

    assert "SOME_TOKEN" not in env
    assert env["PATH"] == "/bin"
