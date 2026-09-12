"""The single gate every project-supplied configuration source passes through.

This module publishes ``IF-0-CONSENT-1`` (Consiliency/pmcp#230): the one decision surface
the manifest overlay, the project ``.mcp.json`` readers and project policy
discovery all call, so that no loader hashes content, reads a file twice, or
formats a refusal on its own.

It exists because a checkout's own files currently reconfigure PMCP: a
repository-supplied ``.pmcp/manifest.yaml`` replaces a shipped server's
``command`` wholesale (S-03) and a repository-supplied
``.mcp-gateway-policy.yaml`` shadows the operator's global policy (S-11). Both
turn "clone a repo and use a server" into code execution the operator never
approved. The gate answers one question -- *has the operator approved these
exact bytes?* -- and answers it the same way for all three sources.

Three properties are load-bearing, and each exists because the obvious
alternative is exploitable:

* **One read, gated, handed back.** ``read_and_gate`` performs *exactly one*
  read and returns those bytes; the caller parses what it was given and MUST
  NOT re-open the path. A gate that checked one read and let the caller open
  the file again would be approving bytes nobody parses -- the file can change
  in between, which is precisely the window IF-0-TRUST-1's
  ``is_approved(path, content)`` was shaped to close. Callers already holding
  bytes use ``gate_bytes`` instead, and must have obtained them from a single
  read for the same reason.
* **Every failure is a refusal.** An unreadable source, a store that cannot be
  read, any exception out of the trust store: all return ``allowed=False``.
  Nothing here raises to a caller, because a loader that saw an exception might
  treat it as permission -- and then a corrupt store would *grant* trust.
* **Refusal is actionable, once.** A refusal carries ``remediation``, the exact
  runnable ``pmcp trust approve <absolute path>``, and ``log_refusal`` emits it
  as a single WARNING. The path is resolved, so the command works from whatever
  directory the operator happens to be standing in.

The gate never records anything. Approving is an operator action through
``pmcp trust approve``; a loader that could approve on the operator's behalf
would make consent decorative.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pmcp import trust_store

#: Which repository-supplied source is being gated. Closed: every project-scoped
#: reader in the codebase is one of these three, and a fourth would need its own
#: refusal wording rather than silently borrowing another's.
ProjectSourceKind = Literal["project_manifest", "project_mcp_json", "project_policy"]

#: Why the gate decided what it did. Closed vocabulary -- callers branch on
#: these strings, so a fifth value would arrive at a caller as an unhandled
#: case. ``no_record`` is the catch-all refusal: nothing recorded, an explicit
#: ``denied`` record, or a store that could not be consulted at all.
ConsentReason = Literal["approved", "no_record", "content_changed", "unreadable"]

_APPROVE_COMMAND = "pmcp trust approve"
# The path is shell-quoted because a REPOSITORY can choose it. The project
# sources have fixed names, but `.mcp.json` may be a symlink and the decision
# path is resolved, so a repo shipping `payload$(id).json` plus a symlink to it
# makes the refusal print `pmcp trust approve /checkout/payload$(id).json`. That
# line exists to be copied into a shell, so an unquoted path turns a security
# warning into command substitution from repository-controlled content --
# reproduced before this was added. shlex.quote leaves ordinary paths untouched.

_KIND_LABELS: dict[ProjectSourceKind, str] = {
    "project_manifest": "project manifest overlay",
    "project_mcp_json": "project .mcp.json",
    "project_policy": "project gateway policy",
}

_REASON_LABELS: dict[ConsentReason, str] = {
    "no_record": "it has not been approved",
    "content_changed": "it changed since it was approved",
    "unreadable": "it could not be read",
    "approved": "it is approved",
}


@dataclass(frozen=True)
class ConsentDecision:
    """One verdict about one project-supplied file's exact bytes.

    Frozen: loaders pass this around and branch on it, and a decision that could
    be edited after the fact is a decision a later line of code could flip.

    ``path`` is always absolute and resolved -- the same key the trust store
    records under, so the path in a refusal message is the path an operator
    approves. ``remediation`` is empty only when ``allowed``.
    """

    allowed: bool
    path: Path
    kind: ProjectSourceKind
    reason: ConsentReason
    remediation: str


def _resolve(path: Path) -> Path:
    """Absolute, symlink-resolved path, without raising for a missing file.

    Matches how ``trust_store`` keys its records, so a gate decision and an
    operator's ``pmcp trust approve`` name the same file. Resolution can still
    fail on a pathological path (a symlink loop); that is a refusal like any
    other, so fall back to a plain absolute path and let the gate answer no.
    """
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(path).absolute()


def _operator_safe(value: str) -> str:
    """Render a repository-controlled path for an operator-facing line.

    Two distinct hazards, both reproduced before this existed:

    * SHELL. The path reaches a line ending "To use it, run: ...", and operators
      copy whole lines. An unquoted `payload$(id).json` is substituted by the
      shell BEFORE the line fails as a command, so `id` runs either way.
    * TERMINAL. `shlex.quote` is shell-correct but passes control characters
      through untouched. A name carrying CR plus an erase-line sequence can
      overwrite the real warning as it is printed and display a different,
      attacker-chosen instruction.

    A path containing control characters cannot be rendered both faithfully and
    safely on one line, so honesty wins over pasteability for that case: it is
    shown as an escaped Python literal. Ordinary paths are shell-quoted and
    otherwise unchanged, which is why every existing assertion still holds.
    """
    if any(ch < " " or ch == "\x7f" for ch in value):
        return repr(value)
    return shlex.quote(value)


def _refusal(
    path: Path, kind: ProjectSourceKind, reason: ConsentReason
) -> ConsentDecision:
    return ConsentDecision(
        allowed=False,
        path=path,
        kind=kind,
        reason=reason,
        remediation=f"{_APPROVE_COMMAND} {_operator_safe(str(path))}",
    )


def _why_refused(path: Path) -> ConsentReason:
    """Name the refusal for an operator, without changing it.

    Called only after the store has already said no, and it cannot make an
    answer more permissive: it only chooses between two refusal wordings.

    No re-hashing happens here. ``is_approved`` returns True exactly when an
    ``approved`` record exists for this path *and* its digest matches, so an
    ``approved`` record that is present while the answer was False can only mean
    the bytes differ -- the operator edited a file they had approved, which is
    the one refusal that needs different words from "never approved".

    Any failure reading the store collapses to ``no_record``: a store we cannot
    consult has told us nothing, and this function must never raise into a
    refusal path that is already correct.
    """
    try:
        for rec in trust_store.list_records():
            if rec.absolute_path == path and rec.decision == trust_store.APPROVED:
                return "content_changed"
    except Exception:  # noqa: BLE001 - a store we cannot read recorded nothing
        return "no_record"
    return "no_record"


def gate_bytes(path: Path, content: bytes, kind: ProjectSourceKind) -> ConsentDecision:
    """Has the operator approved exactly ``content`` being applied as ``path``?

    For callers that already hold the bytes -- those bytes are what is judged,
    not whatever is on disk now. The caller must have obtained them from a
    single read; re-reading after this returns reopens the window this closes.

    Never raises. Every failure answers ``allowed=False``.
    """
    target = _resolve(path)
    try:
        approved = trust_store.is_approved(target, content)
    except Exception:  # noqa: BLE001 - see module docstring: failures are refusals
        # `is_approved` promises never to raise. This is defence in depth: if
        # that promise is ever broken, the break must not read as permission.
        approved = False

    if approved:
        return ConsentDecision(
            allowed=True,
            path=target,
            kind=kind,
            reason="approved",
            remediation="",
        )
    return _refusal(target, kind, _why_refused(target))


def read_and_gate(
    path: Path, kind: ProjectSourceKind
) -> tuple[bytes | None, ConsentDecision]:
    """Read ``path`` **once**, gate those bytes, and hand them back if allowed.

    The returned bytes are the ones that were judged. Parse *these*; opening
    ``path`` again would parse bytes the operator never approved, which is the
    whole failure this gate exists to prevent.

    Returns ``(None, refusal)`` for every refusal, including an unreadable
    source, so a caller that checks the bytes and a caller that checks
    ``decision.allowed`` cannot disagree.
    """
    target = _resolve(path)
    try:
        content = target.read_bytes()
    except Exception:  # noqa: BLE001 - unreadable is a refusal, not an error
        # Missing, a directory, no permission, a race that deleted it between
        # discovery and here: an absent answer is never assent.
        return None, _refusal(target, kind, "unreadable")

    decision = gate_bytes(target, content, kind)
    return (content if decision.allowed else None), decision


def log_refusal(decision: ConsentDecision, logger: logging.Logger) -> None:
    """Emit the single WARNING for a refusal, naming the remediation.

    One line, one format, from every call site: an operator who clones a
    repository shipping all three project files should recognise the second
    message from the first. An allowed decision logs nothing -- there is nothing
    to act on, and a WARNING per approved file would train operators to ignore
    the warning that matters.
    """
    if decision.allowed:
        return
    logger.warning(
        "Ignoring %s at %s: %s. To use it, run: %s",
        _KIND_LABELS.get(decision.kind, "project configuration"),
        # Same treatment as the remediation: this path is repository-controlled
        # and sits on the same line the operator is invited to copy.
        _operator_safe(str(decision.path)),
        _REASON_LABELS.get(decision.reason, "it was refused"),
        decision.remediation,
    )
