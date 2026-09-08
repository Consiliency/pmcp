"""Name the package an npm spec would fetch, at a RESOLVED version, or refuse.

Consiliency/pmcp#230 (IF-0-TRUST-2). Policy today checks the agent-chosen
*server name* while ``provision`` runs the agent-chosen *package* with
``npx -y``, so an allowlist entry for ``internal-approved-tool`` happily
executes ``totally-arbitrary-evil-package`` (2026-09-01 review, finding S-01).
An approval is only worth something if it names the thing that actually runs.
This module supplies that name; TRUST wires no caller, by design.

**Why a resolved version is the whole point.** ``npx -y pkg`` re-resolves
``latest`` at every spawn, so approving the spec ``pkg`` approves code that has
not been published yet -- the approval is a label, not an identity. A spec this
module cannot pin to one concrete version *in the registry's own document* is
therefore not an identity at all, and returns ``None``.

**Relationship to the #195 resolver.** ``npm_resolver`` answers a different
question -- "which package does this argv name" -- by driving npm's own parser
in a node child. This module consumes that answer's TYPE (a package spec
string) and copies its conventions (a frozen result dataclass, refusal rather
than a confident guess, a module-level seam tests patch). It deliberately does
**not** call it: identity here must be obtainable with no subprocess at all,
because every process this code could spawn is one more thing to trust, and
because ``npx``'s own resolution runs the package it is resolving.

**Everything fails closed.** A refusal is ``None``, never an exception: a caller
that gates provisioning on identity would otherwise need a ``try`` around every
call, and one bare ``except Exception`` there reads as consent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

import semver

from pmcp.validation import is_valid_package_name

logger = logging.getLogger(__name__)

# The ecosystem, not the endpoint. `version_checker` already discriminates
# `npm:` / `pypi:` / `cargo:` in its cache keys, and this field is the slot
# those adapters would fill; keying it on a URL instead would make the same
# package resolved through a mirror unequal to itself.
NPM_REGISTRY = "npm"
NPM_REGISTRY_URL = "https://registry.npmjs.org"

# npm's abbreviated packument: same `dist-tags`, `versions` and `dist.integrity`
# this module reads, without the README and the full metadata history. The full
# document for a popular package runs to tens of megabytes.
_PACKUMENT_ACCEPT = "application/vnd.npm.install-v1+json"
_FETCH_TIMEOUT = 10.0
# Generous on purpose. A cap sized for the auth-metadata fetches (256 KiB) would
# truncate the abbreviated packument of any package with a long release history
# -- and the tests are offline, so that would ship green and fail only in
# production, on exactly the popular packages this is meant to gate.
_MAX_PACKUMENT_BYTES = 8 * 1024 * 1024

# A dist-tag is anything npm did not parse as a version. Restricting it to a
# letter-led identifier keeps range syntax (`^1.0.0`, `>=1.0 <2.0`, `1.x`, `*`)
# out of the tag lookup, where a stray hit would pin something the caller never
# asked for.
_DIST_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse HTTP redirects so the registry cannot 3xx to another host."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        raise HTTPError(newurl, code, "Redirects are not allowed.", headers, fp)


_OPENER = build_opener(_NoRedirectHandler)


@dataclass(frozen=True)
class PackageIdentity:
    """What a spec resolves to. Frozen: an identity that can be edited after a
    policy check is not an identity."""

    registry: str
    name: str
    resolved_version: str
    integrity: str | None


def _packument_url(name: str) -> str:
    """The registry document for *name*, as a single escaped path component.

    ``safe=''`` is load-bearing: a scoped ``@scope/name`` must become
    ``%40scope%2Fname`` rather than two path segments, so no package name can
    steer the request to another registry path.
    """
    return f"{NPM_REGISTRY_URL}/{quote(name, safe='')}"


def _fetch_packument(name: str) -> dict[str, Any] | None:
    """The registry's document for *name*, or ``None``.

    The seam the tests patch, so identity resolution is provable offline. *name*
    is already validated by ``resolve_package_identity``; this function never
    builds a URL from an unvalidated string.
    """
    request = Request(_packument_url(name), headers={"Accept": _PACKUMENT_ACCEPT})
    try:
        with _OPENER.open(request, timeout=_FETCH_TIMEOUT) as response:  # nosec B310
            body = response.read(_MAX_PACKUMENT_BYTES)
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.debug("npm packument fetch failed for %r: %s", name, exc)
        return None
    return data if isinstance(data, dict) else None


def _split_spec(spec: str) -> tuple[str, str] | None:
    """Split ``name[@requested]``, or ``None`` if the name is not a package name.

    The leading ``@`` of a scope is not a separator, so the search starts at
    index 1 for a scoped spec. A trailing bare ``@`` is refused rather than read
    as "no version requested": treating ``pkg@`` as ``pkg@latest`` would mint an
    identity for a version the caller never named.
    """
    if not spec:
        return None
    at = spec.find("@", 1) if spec.startswith("@") else spec.find("@")
    if at == -1:
        name, requested = spec, ""
    else:
        name, requested = spec[:at], spec[at + 1 :]
        if not requested:
            return None
    if not is_valid_package_name(name):
        # Rejects leading dashes, whitespace, path separators and the
        # `npm:` / `file:` / `github:` spec forms whose name is not a registry
        # name at all -- none of which this module can name honestly.
        return None
    return name, requested


def _resolve_version(requested: str, packument: dict[str, Any]) -> str | None:
    """The one concrete version *requested* names, or ``None``.

    Three cases and no fourth: nothing requested resolves through
    ``dist-tags.latest``; a dist-tag resolves through ``dist-tags``; an exact
    SemVer 2.0.0 version is taken literally. A range names a set, and a set is
    not something an approval can be about.
    """
    if requested == "" or _DIST_TAG_RE.match(requested):
        dist_tags = packument.get("dist-tags")
        if not isinstance(dist_tags, dict):
            return None
        version = dist_tags.get(requested or "latest")
        return version if isinstance(version, str) else None
    # `semver`, not a hand-rolled parse: every hand-written version comparison
    # in this package has been wrong at least once (see the dependency's note
    # in pyproject.toml).
    if semver.Version.is_valid(requested):
        return requested
    return None


def resolve_package_identity(spec: str) -> PackageIdentity | None:
    """The identity *spec* resolves to right now, or ``None``.

    Resolves from the registry's metadata document only -- it never installs,
    spawns, or executes the package, so the answer is available *before* the
    decision to run it is made.

    ``None`` is returned for a name that is not a package name, a package the
    registry does not describe, a version or dist-tag it does not carry, a range
    (which pins nothing), a document that names a different package, and any I/O
    or parse failure along the way.
    """
    split = _split_spec(spec)
    if split is None:
        return None
    name, requested = split

    try:
        packument = _fetch_packument(name)
    except Exception as exc:  # pragma: no cover - the fetch handles its own
        logger.debug("npm packument lookup raised for %r: %s", name, exc)
        return None
    if not isinstance(packument, dict):
        return None
    if packument.get("name") != name:
        # A mirror, a cache, or a redirect that answered with some other
        # package's document must not be minted as this package's identity.
        return None
    versions = packument.get("versions")
    if not isinstance(versions, dict):
        return None

    version = _resolve_version(requested, packument)
    if version is None:
        return None
    entry = versions.get(version)
    if not isinstance(entry, dict):
        # A dist-tag pointing at a version the document does not describe. There
        # is no `dist` to read an integrity from, so there is nothing to approve.
        return None

    dist = entry.get("dist")
    integrity = dist.get("integrity") if isinstance(dist, dict) else None
    if not isinstance(integrity, str) or not integrity:
        # Absent, not fabricated: a registry entry may predate subresource
        # integrity, and inventing a digest would be worse than reporting none.
        integrity = None

    return PackageIdentity(
        registry=NPM_REGISTRY,
        name=name,
        resolved_version=version,
        integrity=integrity,
    )
