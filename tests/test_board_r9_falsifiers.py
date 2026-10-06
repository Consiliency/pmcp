"""Round-9 board falsifier (claude F001), as written."""

import argparse
import asyncio
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="symlink fixture")
def test_a_chain_the_kernel_refuses_is_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.cli_commands.secrets import run_secrets_set

    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    real = tmp_path / "dot" / "real"
    real.mkdir(parents=True)
    target = real / "pmcp.env"
    target.write_bytes(b"KEEP1=alpha\nKEEP2=beta\n")
    os.symlink("real", tmp_path / "dot" / "via")
    prev = tmp_path / "dot" / "via" / "pmcp.env"
    for i in range(25):  # each hop also passes `via`: 50 links for the kernel
        os.symlink(prev, real / f"c{i}")
        prev = tmp_path / "dot" / "via" / f"c{i}"
    store = home / ".config" / "pmcp" / "pmcp.env"
    os.symlink(prev, store)
    with pytest.raises(OSError):
        open(store, "rb").close()  # control: the kernel refuses (ELOOP)
    monkeypatch.setenv("HOME", str(home))
    asyncio.run(
        run_secrets_set(
            argparse.Namespace(scope="user", key="NEW", value="v", project=tmp_path)
        )
    )
    assert target.read_bytes() == b"KEEP1=alpha\nKEEP2=beta\n", (
        "a store the kernel cannot resolve was rewritten with only the new key"
    )
