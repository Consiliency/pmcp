"""The guarded publisher refuses unless every phase holds (G T5). No network:
the GitHub API, the registry and skopeo are fakes."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import stat
import tarfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "image_publish", ROOT / "scripts/image_publish.py"
)
assert spec and spec.loader
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)

HEAD = "c" * 40
OTHER = "d" * 40
TOKEN = "ghs_publisher_canary_token"


# --- CLI and gate -----------------------------------------------------------


def test_top_level_help_exits_zero_without_a_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        ip.main(["--help"])
    assert exc.value.code == 0
    assert "promote" in capsys.readouterr().out


GATE_MATRIX = [
    (None, False),
    (b"", False),
    (b"true", False),
    (b"1", False),
    (b"Enabled", False),
    (b"ENABLED", False),
    (b" enabled", False),
    (b"enabled\n", False),
    (b"enabled", True),
]


@pytest.mark.parametrize(("value", "opens"), GATE_MATRIX)
def test_phase_one_gate_is_byte_exact(value: bytes | None, opens: bool) -> None:
    env = {} if value is None else {b"PMCP_IMAGE_PUBLISH": value}
    assert ip.publishing_enabled(env) is opens


# --- upstream ---------------------------------------------------------------

RUN_ID, ATTEMPT = 555, 2


def upstream_run() -> dict[str, Any]:
    return {
        "id": RUN_ID,
        "repository": {"id": ip.REPOSITORY_ID},
        "head_repository": {"id": ip.REPOSITORY_ID},
        "workflow_id": ip.TEST_WORKFLOW_ID,
        "path": ".github/workflows/test.yml",
        "event": "push",
        "head_branch": "main",
        "run_attempt": ATTEMPT,
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD,
    }


def job(name: str, conclusion: str = "success") -> dict[str, Any]:
    return {
        "name": name,
        "conclusion": conclusion,
        "run_id": RUN_ID,
        "run_attempt": ATTEMPT,
    }


def jobs_fixture(count: int = 130) -> list[dict[str, Any]]:
    return [job(f"j{i}") for i in range(count - 1)] + [job("ci-required")]


def api_for(
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    total: int | None = None,
    fail: bool = False,
) -> Any:
    calls: list[str] = []

    def get(path: str) -> Any:
        calls.append(path)
        if fail:
            raise ip.Refusal("GitHub API read failed")
        base = f"repos/Consiliency/pmcp/actions/runs/{RUN_ID}/attempts/{ATTEMPT}"
        if path == base:
            return copy.deepcopy(run)
        prefix = base + "/jobs?per_page=100&page="
        assert path.startswith(prefix), path
        page = int(path[len(prefix) :])
        return {
            "total_count": len(jobs) if total is None else total,
            "jobs": copy.deepcopy(jobs[(page - 1) * 100 : page * 100]),
        }

    get.calls = calls  # type: ignore[attr-defined]
    return get


def test_a_valid_upstream_run_yields_its_head_sha() -> None:
    get = api_for(upstream_run(), jobs_fixture())
    assert ip.verify_upstream(get, RUN_ID, ATTEMPT) == HEAD
    assert get.calls[0].endswith(f"/actions/runs/{RUN_ID}/attempts/{ATTEMPT}")
    assert sum("jobs?per_page=100&page=" in c for c in get.calls) == 2


FIELD_MUTATIONS = {
    "id": RUN_ID + 1,
    "repository": {"id": 1},
    "head_repository": {"id": 99},
    "workflow_id": 1,
    "path": ".github/workflows/windows.yml",
    "event": "pull_request",
    "head_branch": "release",
    "run_attempt": ATTEMPT + 1,
    "status": "in_progress",
    "conclusion": "failure",
    "head_sha": "C" * 40,
}


@pytest.mark.parametrize("field", sorted(FIELD_MUTATIONS))
def test_each_upstream_field_mutated_alone_refuses(field: str) -> None:
    run = upstream_run()
    run[field] = FIELD_MUTATIONS[field]
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(api_for(run, jobs_fixture()), RUN_ID, ATTEMPT)


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch", "pull_request"])
def test_other_upstream_events_refuse(event: str) -> None:
    run = dict(upstream_run(), event=event)
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(api_for(run, jobs_fixture()), RUN_ID, ATTEMPT)


def test_a_fork_head_repository_refuses() -> None:
    run = dict(upstream_run(), head_repository={"id": 424242, "fork": True})
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(api_for(run, jobs_fixture()), RUN_ID, ATTEMPT)


@pytest.mark.parametrize(
    "jobs",
    [
        [job("lint"), job("test")],
        [job("ci-required"), job("ci-required")],
        [job("ci-required", "failure")],
        [job("ci-required", "skipped")],
    ],
)
def test_ci_required_must_be_exactly_one_success(jobs: list[dict[str, Any]]) -> None:
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(api_for(upstream_run(), jobs), RUN_ID, ATTEMPT)


def test_a_job_from_another_attempt_refuses() -> None:
    jobs = jobs_fixture(5)
    jobs[0]["run_attempt"] = ATTEMPT - 1
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(api_for(upstream_run(), jobs), RUN_ID, ATTEMPT)


@pytest.mark.parametrize("total", [131, 129, 0])
def test_a_total_count_mismatch_refuses(total: int) -> None:
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(
            api_for(upstream_run(), jobs_fixture(130), total=total), RUN_ID, ATTEMPT
        )


def test_an_api_failure_refuses() -> None:
    with pytest.raises(ip.Refusal):
        ip.verify_upstream(api_for(upstream_run(), [], fail=True), RUN_ID, ATTEMPT)


def test_the_github_reader_is_get_only_and_bounded_to_the_repository() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def transport(url: str, headers: dict[str, str]) -> Any:
        seen.append((url, headers))
        return ip.Response(200, {}, b"{}")

    github = ip.GitHub(TOKEN, transport)
    github.get("repos/Consiliency/pmcp/git/ref/heads/main")
    for path in ["repos/other/repo/git/ref/heads/main", "repos/Consiliency/pmcp/../x"]:
        with pytest.raises(ip.Refusal):
            github.get(path)
    assert [u for u, _ in seen] == [
        "https://api.github.com/repos/Consiliency/pmcp/git/ref/heads/main"
    ]
    with pytest.raises(ip.Refusal):
        ip.urllib_get("https://evil.example/x", {})


# --- OCI layout -------------------------------------------------------------


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_archive(path: Path, mutation: str = "") -> dict[str, Any]:
    layer = b"layer-bytes"
    config = json.dumps(
        {"rootfs": {"type": "layers", "diff_ids": [_digest(b"uncompressed")]}}
    ).encode()
    config_type = ip.CONFIG_TYPE if mutation != "config-type" else "application/json"
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": ip.MANIFEST_TYPE,
            "config": {
                "mediaType": config_type,
                "digest": _digest(config),
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": _digest(layer),
                    "size": len(layer),
                }
            ],
        }
    ).encode()
    descriptor = {
        "mediaType": ip.MANIFEST_TYPE,
        "digest": _digest(manifest),
        "size": len(manifest),
    }
    blobs = {_digest(b): b for b in (layer, config, manifest)}
    manifests = [descriptor]
    if mutation == "two-descriptors":
        manifests = [descriptor, dict(descriptor)]
    if mutation == "nested-index":
        nested = json.dumps({"schemaVersion": 2, "manifests": [descriptor]}).encode()
        blobs[_digest(nested)] = nested
        manifests = [
            {"mediaType": ip.INDEX_TYPE, "digest": _digest(nested), "size": len(nested)}
        ]
    if mutation == "manifest-type":
        manifests = [dict(descriptor, mediaType="application/json")]
    index = json.dumps({"schemaVersion": 2, "manifests": manifests}).encode()
    files = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}', "index.json": index}
    for digest, data in blobs.items():
        name = "blobs/sha256/" + digest.split(":")[1]
        files[name] = data
    if mutation == "blob-hash":
        files["blobs/sha256/" + _digest(layer).split(":")[1]] = b"tampered"
    with tarfile.open(path, "w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return {
        "manifest_digest": descriptor["digest"],
        "config_digest": _digest(config),
        "diff_ids": [_digest(b"uncompressed")],
    }


def test_a_single_manifest_archive_is_accepted(tmp_path: Path) -> None:
    expected = build_archive(tmp_path / "image.oci.tar")
    assert ip.inspect_archive(tmp_path / "image.oci.tar") == expected


@pytest.mark.parametrize(
    "mutation",
    ["two-descriptors", "nested-index", "manifest-type", "config-type", "blob-hash"],
)
def test_layout_violations_refuse(tmp_path: Path, mutation: str) -> None:
    build_archive(tmp_path / "image.oci.tar", mutation)
    with pytest.raises(ip.Refusal):
        ip.inspect_archive(tmp_path / "image.oci.tar")


def test_a_symlink_member_refuses(tmp_path: Path) -> None:
    path = tmp_path / "image.oci.tar"
    build_archive(path)
    with tarfile.open(path, "a") as tar:
        info = tarfile.TarInfo("blobs/sha256/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(ip.Refusal):
        ip.inspect_archive(path)


# --- promote phases ---------------------------------------------------------


class FakeRegistry:
    def __init__(self, tags: dict[str, str]):
        self.tags = dict(tags)
        self.writes: list[tuple[str, str]] = []
        self.archive_digest = ""

    def digest(self, tag: str) -> str | None:
        return self.tags.get(tag)

    def copy(self, source: str, tag: str) -> None:
        self.writes.append((source, tag))
        if source == "archive":
            self.tags[tag] = self.archive_digest
        else:
            self.tags[tag] = self.tags[source.rsplit(":", 1)[1]]


class FakeMain:
    def __init__(self, shas: list[str]):
        self.shas = list(shas)
        self.reads = 0

    def get(self, path: str) -> Any:
        assert path == "repos/Consiliency/pmcp/git/ref/heads/main"
        sha = self.shas[min(self.reads, len(self.shas) - 1)]
        self.reads += 1
        return {"ref": "refs/heads/main", "object": {"sha": sha, "type": "commit"}}


ENABLED = {b"PMCP_IMAGE_PUBLISH": b"enabled"}


def run_promote(
    tmp_path: Path,
    tags: dict[str, str],
    main_shas: list[str],
    *,
    environb: Any = None,
) -> tuple[Any, FakeRegistry, FakeMain]:
    archive = tmp_path / "image.oci.tar"
    candidate = build_archive(archive)
    registry = FakeRegistry(tags)
    registry.archive_digest = candidate["manifest_digest"]
    main = FakeMain(main_shas)
    result: Any
    try:
        result = ip.promote(
            archive=archive,
            manifest_digest=candidate["manifest_digest"],
            head_sha=HEAD,
            api_get=main.get,
            registry_digest=registry.digest,
            copy=registry.copy,
            environb=ENABLED if environb is None else environb,
            log=lambda line: None,
        )
    except ip.Refusal as exc:
        result = exc
    return result, registry, main


def test_absent_sha_tag_is_written_read_back_then_main_moves(tmp_path: Path) -> None:
    result, registry, main = run_promote(
        tmp_path, {"main": "sha256:" + "0" * 64}, [HEAD]
    )
    assert not isinstance(result, ip.Refusal) and result["sha_state"] == "written"
    assert registry.writes == [("archive", f"sha-{HEAD}"), ("archive", "main")]
    assert registry.tags["main"] == registry.tags[f"sha-{HEAD}"]
    assert main.reads == 2


def test_identical_sha_tag_moves_only_the_alias(tmp_path: Path) -> None:
    candidate = build_archive(tmp_path / "probe.tar")
    result, registry, _ = run_promote(
        tmp_path, {f"sha-{HEAD}": candidate["manifest_digest"]}, [HEAD]
    )
    assert not isinstance(result, ip.Refusal) and result["sha_state"] == "identical"
    assert registry.writes == [("archive", "main")]


def test_a_different_sha_tag_refuses_with_zero_alias_writes(tmp_path: Path) -> None:
    other = "sha256:" + "e" * 64
    result, registry, _ = run_promote(
        tmp_path, {f"sha-{HEAD}": other, "main": other}, [HEAD]
    )
    assert isinstance(result, ip.Refusal) and "phase 4" in str(result)
    assert registry.writes == [] and registry.tags["main"] == other


def test_stale_remote_main_refuses_with_zero_copies(tmp_path: Path) -> None:
    result, registry, _ = run_promote(tmp_path, {}, [OTHER])
    assert isinstance(result, ip.Refusal) and "phase 3" in str(result)
    assert registry.writes == []


def test_the_stale_check_never_trusts_a_local_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import types

    # A local checkout that claims to be at the verified head must not matter.
    def local_head(*args: Any, **kwargs: Any) -> Any:
        return types.SimpleNamespace(stdout=HEAD + "\n", returncode=0)

    monkeypatch.setattr(subprocess, "run", local_head)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: HEAD.encode())
    result, registry, _ = run_promote(tmp_path, {}, [OTHER])
    assert isinstance(result, ip.Refusal) and registry.writes == []


def test_main_moving_between_phase_four_and_six_stops_before_the_alias(
    tmp_path: Path,
) -> None:
    result, registry, _ = run_promote(tmp_path, {}, [HEAD, OTHER])
    assert isinstance(result, ip.Refusal) and "phase 5" in str(result)
    assert registry.writes == [("archive", f"sha-{HEAD}")]
    assert "main" not in registry.tags


@pytest.mark.parametrize("value", [None, b"", b"Enabled", b"enabled\n", b"true"])
def test_promote_rechecks_the_gate_before_anything(
    tmp_path: Path, value: bytes | None
) -> None:
    env = {} if value is None else {b"PMCP_IMAGE_PUBLISH": value}
    result, registry, main = run_promote(tmp_path, {}, [HEAD], environb=env)
    assert isinstance(result, ip.Refusal) and "phase 1" in str(result)
    assert registry.writes == [] and main.reads == 0


def test_an_archive_that_differs_from_build_smoke_refuses(tmp_path: Path) -> None:
    archive = tmp_path / "image.oci.tar"
    build_archive(archive)
    registry = FakeRegistry({})
    with pytest.raises(ip.Refusal, match="phase 2"):
        ip.promote(
            archive=archive,
            manifest_digest="sha256:" + "f" * 64,
            head_sha=HEAD,
            api_get=FakeMain([HEAD]).get,
            registry_digest=registry.digest,
            copy=registry.copy,
            environb=ENABLED,
            log=lambda line: None,
        )
    assert registry.writes == []


def test_a_failed_readback_refuses(tmp_path: Path) -> None:
    archive = tmp_path / "image.oci.tar"
    candidate = build_archive(archive)
    registry = FakeRegistry({})
    registry.archive_digest = "sha256:" + "9" * 64
    with pytest.raises(ip.Refusal, match="phase 4"):
        ip.promote(
            archive=archive,
            manifest_digest=candidate["manifest_digest"],
            head_sha=HEAD,
            api_get=FakeMain([HEAD]).get,
            registry_digest=registry.digest,
            copy=registry.copy,
            environb=ENABLED,
            log=lambda line: None,
        )


# --- credentials ------------------------------------------------------------


def test_the_token_reaches_skopeo_only_through_a_0600_authfile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "work" / "image.oci.tar"
    archive.parent.mkdir()
    archive.write_bytes(b"x")
    seen: list[list[str]] = []
    modes: list[int] = []

    def run(argv: list[str], timeout: int = 0) -> Any:
        seen.append(argv)
        mount = argv[argv.index("-v") + 1].split(":")[0]
        modes.append(stat.S_IMODE(os.stat(mount).st_mode))
        return None

    auth_path = tmp_path / "auth.json"
    with ip.authfile(auth_path, TOKEN) as auth:
        copier = ip.make_copier(auth, archive, run=run)
        copier("archive", f"sha-{HEAD}")
        copier(f"docker://ghcr.io/consiliency/pmcp:sha-{HEAD}", "3.0.1")
        content = auth.read_text()
    assert not auth_path.exists()
    assert modes == [0o600, 0o600]
    import base64

    encoded = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert TOKEN not in content and encoded in content
    for argv in seen:
        joined = " ".join(argv)
        assert TOKEN not in joined and encoded not in joined
        assert ip.SKOPEO_IMAGE in argv and "--preserve-digests" in argv
        assert "--dest-authfile" in argv
        assert not any(
            a.startswith(("--creds", "--dest-creds", "--src-creds")) for a in argv
        )
    assert TOKEN not in capsys.readouterr().out


def test_the_authfile_is_removed_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    with pytest.raises(RuntimeError):
        with ip.authfile(path, TOKEN):
            raise RuntimeError("write failed")
    assert not path.exists()


def test_the_authfile_must_be_fresh(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("planted")
    with pytest.raises(FileExistsError):
        with ip.authfile(path, TOKEN):
            pass
    assert path.read_text() == "planted"


def test_cli_promote_refuses_without_the_gate_and_writes_no_authfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PMCP_IMAGE_PUBLISH", raising=False)
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    auth = tmp_path / "auth.json"
    code = ip.main(
        [
            "promote",
            "--archive",
            str(tmp_path / "missing.tar"),
            "--manifest-digest",
            "sha256:" + "0" * 64,
            "--head-sha",
            HEAD,
            "--authfile",
            str(auth),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1 and not auth.exists()
    assert TOKEN not in captured.out + captured.err


# --- tag mode ---------------------------------------------------------------

SRC = "sha256:" + "1" * 64


def run_tag(tags: dict[str, str], version: str) -> tuple[Any, FakeRegistry]:
    registry = FakeRegistry(tags)
    try:
        result: Any = ip.tag_version(
            version=version,
            source_sha=HEAD,
            registry_digest=registry.digest,
            copy=registry.copy,
            environb=ENABLED,
        )
    except ip.Refusal as exc:
        result = exc
    return result, registry


def test_tag_mode_refuses_when_the_sha_tag_is_absent() -> None:
    result, registry = run_tag({}, "3.0.1")
    assert isinstance(result, ip.Refusal) and registry.writes == []


@pytest.mark.parametrize(
    "target", ["latest", "main", "3.0", "sha-" + HEAD, "abcdef1", "v3.0.1", "3"]
)
def test_tag_mode_refuses_non_version_targets(target: str) -> None:
    result, registry = run_tag({f"sha-{HEAD}": SRC}, target)
    assert isinstance(result, ip.Refusal) and registry.writes == []


def test_tag_mode_absent_version_is_written_and_read_back() -> None:
    result, registry = run_tag({f"sha-{HEAD}": SRC}, "3.0.1")
    assert result == "written"
    assert registry.writes == [
        (f"docker://ghcr.io/consiliency/pmcp:sha-{HEAD}", "3.0.1")
    ]
    assert registry.tags["3.0.1"] == SRC


def test_tag_mode_same_digest_is_a_no_op() -> None:
    result, registry = run_tag({f"sha-{HEAD}": SRC, "3.0.1": SRC}, "3.0.1")
    assert result == "identical" and registry.writes == []


def test_tag_mode_occupied_version_with_another_digest_refuses() -> None:
    legacy = "sha256:" + "2" * 64
    result, registry = run_tag({f"sha-{HEAD}": SRC, "3.0.0": legacy}, "3.0.0")
    assert isinstance(result, ip.Refusal) and registry.writes == []
    assert registry.tags["3.0.0"] == legacy


def test_tag_mode_rechecks_the_gate() -> None:
    registry = FakeRegistry({f"sha-{HEAD}": SRC})
    with pytest.raises(ip.Refusal):
        ip.tag_version(
            version="3.0.1",
            source_sha=HEAD,
            registry_digest=registry.digest,
            copy=registry.copy,
            environb={b"PMCP_IMAGE_PUBLISH": b"Enabled"},
        )
    assert registry.writes == []


# --- registry reader --------------------------------------------------------


def test_registry_reader_checks_digest_and_reports_absence() -> None:
    manifest = b'{"schemaVersion":2}'
    good = _digest(manifest)

    def transport(url: str, headers: dict[str, str]) -> Any:
        if "/token" in url:
            assert headers["Authorization"].startswith("Basic ")
            return ip.Response(200, {}, b'{"token":"bearer"}')
        assert headers["Authorization"] == "Bearer bearer"
        if url.endswith("/manifests/missing"):
            return ip.Response(404, {}, b"")
        if url.endswith("/manifests/bad"):
            return ip.Response(200, {"docker-content-digest": SRC}, manifest)
        return ip.Response(200, {"docker-content-digest": good}, manifest)

    registry = ip.Registry(TOKEN, transport)
    assert registry.digest("main") == good
    assert registry.digest("missing") is None
    with pytest.raises(ip.Refusal):
        registry.digest("bad")


# --- smoke comparison -------------------------------------------------------


def test_smoke_refuses_a_loaded_image_that_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "image.oci.tar"
    candidate = build_archive(archive)
    monkeypatch.setattr(
        ip,
        "loaded_identity",
        lambda ref, run=None: {"config_digest": SRC, "diff_ids": candidate["diff_ids"]},
    )
    monkeypatch.setattr(
        ip, "smoke_gateway", lambda *a, **k: pytest.fail("must not run the gateway")
    )
    with pytest.raises(ip.Refusal):
        ip.smoke(archive, "pmcp-image-smoke:candidate", tmp_path / "probe.py")
