"""Package identity: WHICH package a spec names, and at which resolved version.

Consiliency/pmcp#230, phase TRUST lane SL-2 (IF-0-TRUST-2). The 2026-09-01
review's finding S-01 is that policy checks the agent-chosen SERVER NAME while
``provision`` runs the agent-chosen PACKAGE with ``npx -y`` -- so allowlisting
``internal-approved-tool`` executes ``totally-arbitrary-evil-package``. This
module's job is to supply the thing a later phase can actually gate on.

Two properties carry the whole lane and both are asserted here:

* **Resolution must not execute the package.** ``npx -y pkg`` resolves *by
  running it*; an identity obtained that way is obtained too late. So the tests
  block ``subprocess.Popen`` and ``socket.socket`` and still require an answer.
* **A resolved version, or nothing.** An unpinned spec re-resolves to whatever
  is latest at every spawn, so an approval of a range would approve code that
  does not exist yet. Anything this module cannot pin to one concrete version
  in the registry's own document is ``None``.

The packument is inlined rather than kept under ``tests/fixtures/`` because
SL-2 owns exactly two files; the shape is npm's abbreviated packument
(``application/vnd.npm.install-v1+json``).
"""

from __future__ import annotations

import socket
import subprocess
from typing import Any
from urllib.error import HTTPError

import pytest

from pmcp.manifest import package_identity
from pmcp.manifest.package_identity import PackageIdentity, resolve_package_identity

_LATEST_INTEGRITY = "sha512-cGFja2FnZS1pZGVudGl0eS1sYXRlc3Q="
_PINNED_INTEGRITY = "sha512-cGFja2FnZS1pZGVudGl0eS1waW5uZWQ="


def _packument(name: str = "example-mcp") -> dict[str, Any]:
    """An abbreviated packument, trimmed to the keys this module reads."""
    return {
        "name": name,
        "dist-tags": {"latest": "1.4.2", "next": "2.0.0-rc.1"},
        "versions": {
            "1.0.0": {
                "name": name,
                "version": "1.0.0",
                "dist": {
                    "tarball": f"https://registry.npmjs.org/{name}/-/pkg-1.0.0.tgz",
                    "integrity": _PINNED_INTEGRITY,
                },
            },
            "1.4.2": {
                "name": name,
                "version": "1.4.2",
                "dist": {
                    "tarball": f"https://registry.npmjs.org/{name}/-/pkg-1.4.2.tgz",
                    "integrity": _LATEST_INTEGRITY,
                },
            },
            # A registry entry may predate subresource integrity. The identity
            # must say so rather than invent a digest.
            "2.0.0-rc.1": {
                "name": name,
                "version": "2.0.0-rc.1",
                "dist": {
                    "tarball": f"https://registry.npmjs.org/{name}/-/pkg-2.0.0.tgz"
                },
            },
        },
    }


class _OfflineRegistry:
    """A packument source that never leaves the process, and counts its reads."""

    def __init__(self) -> None:
        self.packuments: dict[str, dict[str, Any]] = {
            "example-mcp": _packument(),
            "@scope/example": _packument("@scope/example"),
        }
        self.calls: list[str] = []

    def fetch(self, name: str) -> dict[str, Any] | None:
        self.calls.append(name)
        return self.packuments.get(name)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> _OfflineRegistry:
    """Serve packuments from memory.

    ``raising=False`` is deliberate: it makes a failing run fail on BEHAVIOUR --
    a resolver that reaches for the network anyway -- rather than on the absence
    of an attribute. That distinction is what made the pre-implementation
    falsification runs mean something.
    """
    source = _OfflineRegistry()
    monkeypatch.setattr(
        package_identity, "_fetch_packument", source.fetch, raising=False
    )
    return source


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every spawn and every socket an error, and record the attempts.

    ``subprocess.Popen`` is patched at the class level because that is what
    ``subprocess.run``/``check_output`` and ``NpmResolver._spawn`` all go
    through -- patching ``run`` alone would leave the interesting path open.
    """
    attempts: list[str] = []

    def _no_spawn(*args: Any, **kwargs: Any) -> Any:
        attempts.append(f"subprocess.Popen({args!r})")
        raise AssertionError("resolving a package identity must not spawn a process")

    def _no_socket(*args: Any, **kwargs: Any) -> Any:
        attempts.append(f"socket.socket({args!r})")
        raise AssertionError("resolving a package identity must not open a socket")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    monkeypatch.setattr(socket, "socket", _no_socket)
    return attempts


# ---------------------------------------------------------------------------
# SL-2.1: identity resolves offline from a fixture
# ---------------------------------------------------------------------------


def test_identity_resolves_offline_from_a_fixture(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    identity = resolve_package_identity("example-mcp")

    assert identity == PackageIdentity(
        registry="npm",
        name="example-mcp",
        resolved_version="1.4.2",
        integrity=_LATEST_INTEGRITY,
    )
    assert registry.calls == ["example-mcp"]


def test_an_exact_pin_resolves_to_that_version(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    identity = resolve_package_identity("example-mcp@1.0.0")

    assert identity is not None
    # Not `dist-tags.latest`: a pinned spec must not silently drift upward, and
    # the integrity must be the pinned entry's own.
    assert identity.resolved_version == "1.0.0"
    assert identity.integrity == _PINNED_INTEGRITY


def test_a_dist_tag_resolves_through_dist_tags(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    identity = resolve_package_identity("example-mcp@next")

    assert identity is not None
    assert identity.resolved_version == "2.0.0-rc.1"


def test_a_scoped_spec_splits_at_the_version_separator(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    identity = resolve_package_identity("@scope/example@1.0.0")

    assert identity is not None
    # The leading `@` of a scope is not a version separator.
    assert identity.name == "@scope/example"
    assert identity.resolved_version == "1.0.0"
    assert registry.calls == ["@scope/example"]


def test_a_missing_integrity_is_none_not_a_fabrication(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    identity = resolve_package_identity("example-mcp@2.0.0-rc.1")

    assert identity is not None
    assert identity.integrity is None


# ---------------------------------------------------------------------------
# SL-2.1: an unresolvable spec returns None rather than raising
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        "",  # no package at all
        "unknown-package",  # the registry has no such document
        "example-mcp@9.9.9",  # a real package, a version it never published
        "example-mcp@nightly",  # a dist-tag the registry does not carry
        "example-mcp@^1.0.0",  # a range: no single version to approve
        "example-mcp@>=1.0 <2.0",  # ditto, in npa's whitespace spelling
        "example-mcp@1.x",  # ditto
        "example-mcp@*",  # ditto
        "example-mcp@",  # a separator with nothing after it
        "-evil",  # npx would read this as a flag
        "../../etc/passwd",  # path traversal into the registry URL
        "example mcp",  # whitespace
        "npm:alias@1.0.0",  # an alias spec: the name is not the name
        "file:../local",  # not a registry package at all
        "github:owner/repo",  # ditto
    ],
)
def test_an_unresolvable_spec_returns_none_rather_than_raising(
    registry: _OfflineRegistry, no_network: list[str], spec: str
) -> None:
    # `None`, never an exception: a caller that gates on identity would have to
    # wrap every call, and one unwrapped call site turns a refusal into a crash
    # -- or, worse, into an `except Exception: pass` that reads as consent.
    assert resolve_package_identity(spec) is None


def test_an_invalid_package_name_is_refused_before_any_fetch(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    for spec in ("-evil", "../../etc/passwd", "example mcp", ""):
        assert resolve_package_identity(spec) is None

    # Refused by `validation.is_valid_package_name` before a URL is ever built,
    # so a hostile spec never reaches the network layer at all.
    assert registry.calls == []


def test_a_packument_naming_a_different_package_is_refused(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    registry.packuments["example-mcp"] = _packument("something-else")

    # A mirror, a redirect, or a cache-poisoning answer that serves another
    # package's document must not be minted as this package's identity.
    assert resolve_package_identity("example-mcp") is None


@pytest.mark.parametrize(
    "packument",
    [
        None,
        [],
        {},
        {"name": "example-mcp"},
        {"name": "example-mcp", "dist-tags": {"latest": "1.4.2"}},
        {"name": "example-mcp", "dist-tags": "latest", "versions": {}},
        {"name": "example-mcp", "dist-tags": {"latest": 142}, "versions": {}},
        # `latest` points at a version the document does not describe: there is
        # no `dist` entry to take an integrity from, so there is no identity.
        {"name": "example-mcp", "dist-tags": {"latest": "1.4.2"}, "versions": {}},
        {
            "name": "example-mcp",
            "dist-tags": {"latest": "1.4.2"},
            "versions": {"1.4.2": "not-an-object"},
        },
    ],
)
def test_a_malformed_packument_fails_closed(
    registry: _OfflineRegistry, no_network: list[str], packument: Any
) -> None:
    registry.packuments["example-mcp"] = packument

    assert resolve_package_identity("example-mcp") is None


def test_a_registry_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch, no_network: list[str]
) -> None:
    def _boom(name: str) -> dict[str, Any] | None:
        raise OSError("connection reset by peer")

    monkeypatch.setattr(package_identity, "_fetch_packument", _boom, raising=False)

    # Fail closed: an exception escaping here would reach a caller that may well
    # treat "no refusal" as permission.
    assert resolve_package_identity("example-mcp") is None


# ---------------------------------------------------------------------------
# SL-2.1 / EC-TRUST-1: no subprocess is spawned
# ---------------------------------------------------------------------------


def test_no_subprocess_is_spawned(
    registry: _OfflineRegistry, no_network: list[str]
) -> None:
    # The negative control for EC-TRUST-1. `npm view` / `npx -y` would both
    # answer the question -- the second by running the package, which is the
    # bug -- so the identity must come from the registry document alone.
    identity = resolve_package_identity("example-mcp@1.0.0")

    assert identity is not None
    assert no_network == []
    # Belt and braces: the #195 resolver is a process-spawner and this module
    # deliberately does not drive it (it consumes the SPEC STRING that resolver
    # produces, which is a different relationship).
    assert package_identity.__dict__.get("get_resolver") is None


# ---------------------------------------------------------------------------
# Fetch hardening (only the pure parts; the fetch itself is never exercised)
# ---------------------------------------------------------------------------


def test_the_packument_url_escapes_the_package_name() -> None:
    url = package_identity._packument_url("@scope/example")

    # One path component: a scoped name must not introduce a `/` that could
    # redirect the request to another registry path.
    assert url == "https://registry.npmjs.org/%40scope%2Fexample"
    assert url.startswith(package_identity.NPM_REGISTRY_URL + "/")
    assert "/" not in url[len(package_identity.NPM_REGISTRY_URL) + 1 :]


def test_the_packument_fetch_refuses_redirects() -> None:
    handler = package_identity._NoRedirectHandler()

    with pytest.raises(HTTPError):
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
        )

    assert any(
        isinstance(h, package_identity._NoRedirectHandler)
        for h in package_identity._OPENER.handlers
    )
