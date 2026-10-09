"""Write the bytes a repository would SHIP as its own approval store.

Since Consiliency/pmcp#372 round 22 pmcp refuses every home-scoped file while a
checkout controls the home directory -- including writing the trust store
there -- so a test cannot produce a checkout-resident store by calling
``trust_store.record`` with HOME inside the checkout. These helpers record a
genuine approval under a throwaway operator home outside every checkout and
copy the bytes to where the repository would ship them.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from pmcp import home_identity, trust_store


def ship_approval(target: Path, home: Path) -> Path:
    """A genuine, matching approval of ``target`` at ``home/.config/pmcp/trust.json``."""
    previous = os.environ.get("HOME")
    writer_home = Path(tempfile.mkdtemp(prefix="pmcp-writer-home-"))
    try:
        os.environ["HOME"] = str(writer_home)
        trust_store.record(target, target.read_bytes(), "project", trust_store.APPROVED)
        written = writer_home / ".config" / "pmcp" / "trust.json"
        destination = home / ".config" / "pmcp" / "trust.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(written, destination)
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous
        shutil.rmtree(writer_home, ignore_errors=True)
        trust_store.reset_launch_directory()
        home_identity.reset_home_warning()
    return destination


def ship_package_approval(identity: object, home: Path) -> Path:
    """A genuine package approval at ``home/.config/pmcp/package_approvals.json``."""
    from pmcp.package_approvals import approve_package

    previous = os.environ.get("HOME")
    writer_home = Path(tempfile.mkdtemp(prefix="pmcp-writer-home-"))
    try:
        os.environ["HOME"] = str(writer_home)
        approve_package(identity)  # type: ignore[arg-type]
        written = writer_home / ".config" / "pmcp" / "package_approvals.json"
        destination = home / ".config" / "pmcp" / "package_approvals.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(written, destination)
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous
        shutil.rmtree(writer_home, ignore_errors=True)
        trust_store.reset_launch_directory()
        home_identity.reset_home_warning()
    return destination
