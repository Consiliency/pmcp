#!/usr/bin/env python3
"""Live compatibility check for 1Password secret references (Consiliency/pmcp#487 P2).

OPERATOR-RUN ONLY. This is the P2 merge gate of plan section 3.10: it talks to
the real ``op`` CLI and the real 1Password service account, so it never runs
in CI or the test suite. Its output is VALUE-FREE: it prints check names,
pass/fail, exit classes and short SHA-256 digest prefixes -- never a secret
value, the token, or ``op``'s stderr.

What it checks, with pmcp's own code paths (``pmcp.secret_refs``):

1. the token is found where pmcp looks for it (plan 3.1), and its source;
2. the ``op`` pmcp would execute passes the trust check (plan 3.4), and that
   binary documents every flag pmcp passes (``op read --help``):
   ``--no-newline``, ``--no-color``, ``--cache``, ``--config``;
3. ``op read --no-newline --no-color --cache=false --config <dir> -- <ref>``
   resolves the given reference through pmcp's resolver (bounded, typed);
4. ``--cache=false`` reads the CURRENT value after an item edit: the script
   resolves, asks the operator to change the field in 1Password, resolves
   again, and compares digests (they must differ). ``--skip-rotation``
   leaves this step out (then the gate is incomplete and the script says so).

Usage (on the target host, as the gateway's user)::

    uv run --locked python scripts/op_live_compat.py \\
        --ref 'op://PMCP Gateway/<item>/<field>' [--op-path /usr/bin/op]

Exit status: 0 when every check passed (gate met), 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
import time

REQUIRED_FLAGS = ("--no-newline", "--no-color", "--cache", "--config")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _result(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(': ' + detail) if detail else ''}")
    return ok


async def _resolve(reference: str) -> tuple[str | None, str | None]:
    from pmcp.credential_state import CredentialUnavailableError
    from pmcp.secret_refs import SPAWN_RESOLUTION_SECONDS, resolve_reference

    try:
        value = await resolve_reference(
            reference, time.monotonic() + SPAWN_RESOLUTION_SECONDS
        )
    except CredentialUnavailableError as exc:
        return None, exc.reason
    return value, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", required=True, help="a supported op:// reference")
    parser.add_argument("--op-path", default=None, help="explicit op executable")
    parser.add_argument(
        "--skip-rotation",
        action="store_true",
        help="skip the --cache=false rotation check (gate then incomplete)",
    )
    args = parser.parse_args()

    from pmcp import cli, secret_refs
    from pmcp.credential_state import CredentialUnavailableError

    # The same startup steps the gateway runs: capture CREDENTIALS_DIRECTORY,
    # drop the op names from the environment, load the stores.
    cli.load_startup_env()
    if args.op_path:
        secret_refs.set_operator_op_path(args.op_path)

    ok = True
    if secret_refs.parse_reference(args.ref) is None:
        _result("reference grammar (plan 3.3)", False, "reference_unsupported")
        return 1
    ok &= _result("reference grammar (plan 3.3)", True)

    state, source = secret_refs.token_status()
    ok &= _result(
        "service-account token (plan 3.1)",
        state == "usable",
        f"state={state} source={source}",
    )
    for entry in secret_refs.token_path_report():
        print(f"       searched: {entry}")

    try:
        executable = secret_refs.trusted_op_executable()
    except CredentialUnavailableError as exc:
        _result("trusted op executable (plan 3.4)", False, exc.reason)
        return 1
    ok &= _result("trusted op executable (plan 3.4)", True, executable)

    version = subprocess.run(
        [executable, "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": secret_refs.OP_FIXED_PATH, "HOME": os.path.expanduser("~")},
    )
    print(f"       op --version: {version.stdout.strip()[:40]}")
    help_text = subprocess.run(
        [executable, "read", "--help"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": secret_refs.OP_FIXED_PATH, "HOME": os.path.expanduser("~")},
    ).stdout
    missing = [flag for flag in REQUIRED_FLAGS if flag not in help_text]
    ok &= _result(
        "op read documents pmcp's flags",
        not missing,
        f"missing={missing}" if missing else "",
    )

    first, reason = asyncio.run(_resolve(args.ref))
    ok &= _result(
        "resolve through pmcp (op read ... --cache=false)",
        first is not None,
        reason or f"digest={_digest(first or '')}",
    )
    if first is None:
        return 1

    if args.skip_rotation:
        print("[SKIP] --cache=false rotation check: the P2 gate is INCOMPLETE")
        return 1 if not ok else 2
    input(
        "Now change the referenced field in 1Password (keep the old value to "
        "restore it), then press Enter... "
    )
    second, reason = asyncio.run(_resolve(args.ref))
    rotated = second is not None and _digest(second) != _digest(first)
    ok &= _result(
        "--cache=false reads the current value after an edit",
        rotated,
        reason or f"before={_digest(first)} after={_digest(second or '')}",
    )
    del first, second
    print("GATE MET" if ok else "GATE NOT MET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
