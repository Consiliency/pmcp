"""Tests for the shared credential-requirement predicate (P5, Consiliency/pmcp#114).

Pins IF-0-P5-1 (CredentialRequirement / credential_requirement / requires_credential)
and IF-0-P5-2 (ServerConfig.api_key_optional_when parsing) exactly as frozen in
plans/phase-plan-v11-P5.md. Every clause here is load-bearing for the seven
downstream gate consumers (SL-2, SL-3) — do not relax an assertion to make a
call site convenient; fix the call site instead.
"""

from __future__ import annotations

import pytest

from pmcp.manifest.loader import (
    CredentialRequirement,
    ServerConfig,
    _parse_server_config,
    credential_requirement,
    requires_credential,
)


def _server(**overrides: object) -> ServerConfig:
    base: dict[str, object] = dict(
        name="firecrawl",
        description="",
        keywords=[],
        install={},
        command="firecrawl-mcp",
        args=[],
        requires_api_key=True,
        env_var="FIRECRAWL_API_KEY",
    )
    base.update(overrides)
    return ServerConfig(**base)  # type: ignore[arg-type]


class TestDeclaredFalseShortCircuit:
    def test_declared_false_never_required(self) -> None:
        server = _server(requires_api_key=False, api_key_optional_when=[])
        result = credential_requirement(server)
        assert result == CredentialRequirement(
            required=False, declared=False, relaxed_by=None
        )

    def test_declared_false_ignores_optional_when(self) -> None:
        # Clause 3: declared False short-circuits; api_key_optional_when is
        # irrelevant and must not be consulted.
        server = _server(
            requires_api_key=False,
            api_key_optional_when=["SOME_URL"],
            extra_env={"SOME_URL": "http://host"},
        )
        result = credential_requirement(server)
        assert result.required is False
        assert result.relaxed_by is None


class TestExtraEnvRelaxation:
    def test_relaxed_by_extra_env(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        result = credential_requirement(server)
        assert result == CredentialRequirement(
            required=False, declared=True, relaxed_by="FIRECRAWL_API_URL"
        )
        assert requires_credential(server) is False

    def test_first_usable_name_wins_in_declared_order(self) -> None:
        server = _server(
            api_key_optional_when=["FIRST_URL", "SECOND_URL"],
            extra_env={"SECOND_URL": "http://host"},
        )
        # FIRST_URL absent, SECOND_URL usable -> relaxed_by SECOND_URL.
        result = credential_requirement(server)
        assert result.relaxed_by == "SECOND_URL"

    def test_absent_from_extra_env_fails_closed(self) -> None:
        server = _server(api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
        result = credential_requirement(server)
        assert result.required is True
        assert result.relaxed_by is None


class TestRemoteServersNeverRelax:
    """Board review finding 2: extra_env is carried to a spawned local
    subprocess's environment; a remote (url-based) server has no such
    subprocess and authenticates via headers, so a relaxer set in extra_env
    can never actually reach the connection. A remote entry must stay
    required regardless of api_key_optional_when/extra_env."""

    def test_remote_server_with_usable_relaxer_still_required(self) -> None:
        server = _server(
            url="https://mcp.example.com/sse",
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        result = credential_requirement(server)
        assert result.required is True
        assert result.relaxed_by is None

    def test_remote_server_with_usable_relaxer_in_child_env_still_required(
        self,
    ) -> None:
        server = _server(url="https://mcp.example.com/sse")
        result = credential_requirement(
            server, child_env={"FIRECRAWL_API_URL": "http://localhost:3002"}
        )
        assert result.required is True
        assert result.relaxed_by is None

    def test_local_server_unaffected_by_url_check(self) -> None:
        # Sanity: a server with no url is unaffected by this clause.
        server = _server(
            url=None,
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        assert credential_requirement(server).required is False


class TestOsEnvironInversionGuard:
    """Clause 2: the predicate must never read os.environ. This is the guard
    against the env-strip inversion documented in the plan: sanitized_subprocess_env
    strips managed secret keys from os.environ before the child spawns, so a
    predicate that relaxed on os.environ would open the gate while the child
    never receives the variable."""

    def test_monkeypatch_setenv_does_not_relax(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIRECRAWL_API_URL", "http://localhost:3002")
        server = _server(api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
        result = credential_requirement(server)
        assert result.required is True
        assert result.relaxed_by is None

    def test_credential_requirement_has_no_env_kwarg(self) -> None:
        import inspect

        sig = inspect.signature(credential_requirement)
        assert "env" not in sig.parameters


class TestPlaceholderValuesRejected:
    def test_empty_string_rejected(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": ""},
        )
        assert credential_requirement(server).required is True

    def test_whitespace_only_rejected(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "   "},
        )
        assert credential_requirement(server).required is True

    def test_unexpanded_placeholder_braced_rejected(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "${FIRECRAWL_API_URL}"},
        )
        assert credential_requirement(server).required is True

    def test_unexpanded_placeholder_bare_rejected(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "$FIRECRAWL_API_URL"},
        )
        assert credential_requirement(server).required is True


class TestSelfRelaxationImpossible:
    def test_self_relax_via_env_var_ignored(self) -> None:
        server = _server(
            env_var="FIRECRAWL_API_KEY",
            api_key_optional_when=["FIRECRAWL_API_KEY"],
            extra_env={"FIRECRAWL_API_KEY": "some-value"},
        )
        result = credential_requirement(server)
        assert result.required is True
        assert result.relaxed_by is None

    def test_self_relax_via_secret_key_ignored(self) -> None:
        server = _server(
            env_var="FIRECRAWL_API_KEY",
            secret_key="FIRECRAWL_NAMESPACED_KEY",
            api_key_optional_when=["FIRECRAWL_NAMESPACED_KEY"],
            extra_env={"FIRECRAWL_NAMESPACED_KEY": "some-value"},
        )
        result = credential_requirement(server)
        assert result.required is True
        assert result.relaxed_by is None

    def test_self_relax_dropped_at_parse_time(self) -> None:
        data = {
            "description": "",
            "keywords": [],
            "install": {},
            "command": "firecrawl-mcp",
            "args": [],
            "requires_api_key": True,
            "env_var": "FIRECRAWL_API_KEY",
            "api_key_optional_when": ["FIRECRAWL_API_KEY", "FIRECRAWL_API_URL"],
        }
        server = _parse_server_config("firecrawl", data)
        assert server.api_key_optional_when == ["FIRECRAWL_API_URL"]


class TestChildEnvClause2a:
    def test_child_env_overrides_extra_env_source(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://host-from-manifest"},
        )
        # child_env represents the configured-duplicate override: the value the
        # child will actually receive is a dead placeholder, so the predicate
        # must judge that value, not the manifest's extra_env.
        result = credential_requirement(
            server, child_env={"FIRECRAWL_API_URL": "${FIRECRAWL_API_URL}"}
        )
        assert result.required is True
        assert result.relaxed_by is None

    def test_child_env_none_falls_back_to_extra_env(self) -> None:
        server = _server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        result = credential_requirement(server, child_env=None)
        assert result.required is False
        assert result.relaxed_by == "FIRECRAWL_API_URL"

    def test_child_env_relaxes_when_extra_env_empty(self) -> None:
        server = _server(api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
        result = credential_requirement(
            server, child_env={"FIRECRAWL_API_URL": "http://localhost:3002"}
        )
        assert result.required is False
        assert result.relaxed_by == "FIRECRAWL_API_URL"


class TestFieldParsing:
    def _parse(self, **data_overrides: object) -> ServerConfig:
        data: dict[str, object] = dict(
            description="",
            keywords=[],
            install={},
            command="firecrawl-mcp",
            args=[],
            requires_api_key=True,
            env_var="FIRECRAWL_API_KEY",
        )
        data.update(data_overrides)
        return _parse_server_config("firecrawl", data)

    def test_absent_key_yields_empty_list(self) -> None:
        server = self._parse()
        assert server.api_key_optional_when == []

    def test_non_list_yields_empty_list_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            server = self._parse(api_key_optional_when="FIRECRAWL_API_URL")
        assert server.api_key_optional_when == []
        assert "api_key_optional_when" in caplog.text

    def test_non_string_members_dropped(self) -> None:
        server = self._parse(api_key_optional_when=["FIRECRAWL_API_URL", 123, None])
        assert server.api_key_optional_when == ["FIRECRAWL_API_URL"]

    def test_absent_field_matches_default_behaviour(self) -> None:
        # Byte-for-byte today's behaviour: requires_api_key True, no relaxer.
        server = self._parse()
        assert requires_credential(server) is True


class TestDuckTypingAndNone:
    def test_none_server_not_required(self) -> None:
        result = credential_requirement(None)
        assert result == CredentialRequirement(
            required=False, declared=False, relaxed_by=None
        )
        assert requires_credential(None) is False

    def test_duck_typed_object_without_api_key_optional_when(self) -> None:
        class Discovered:
            requires_api_key = True
            env_var = "SOME_KEY"
            secret_key = None
            # No api_key_optional_when / extra_env attributes at all.

        result = credential_requirement(Discovered())
        assert result.required is True
        assert result.relaxed_by is None

    def test_duck_typed_object_with_extra_env(self) -> None:
        class Discovered:
            requires_api_key = True
            env_var = "SOME_KEY"
            secret_key = None
            api_key_optional_when = ["SOME_URL"]
            extra_env = {"SOME_URL": "http://host"}

        result = credential_requirement(Discovered())
        assert result.required is False
        assert result.relaxed_by == "SOME_URL"
