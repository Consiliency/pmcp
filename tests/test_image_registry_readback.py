"""The registry readback is GET-only, complete or refused, and its contracts stop
every unexplained registry change (CLR1-03). Every transport here is fake."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rb = _load("image_registry_readback", "scripts/image_registry_readback.py")
inv = _load(
    "image_cutover_inventory_for_readback", "scripts/image_cutover_inventory.py"
)

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
SECRET = "ghs_registry_canary_value"
SHA_A = "a" * 40
SHA_B = "b" * 40


def body(seed: str) -> bytes:
    return json.dumps({"schemaVersion": 2, "seed": seed}).encode()


def digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(body(seed)).hexdigest()


class FakeNet:
    """ghcr.io and api.github.com, in memory. Records every request."""

    def __init__(
        self,
        tags: dict[str, str],
        *,
        page: int = 2,
        compare: dict[str, Any] | None = None,
    ) -> None:
        self.tags = dict(tags)
        self.page = page
        self.compare = compare or {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.override: dict[str, Any] = {}

    def __call__(self, method: str, url: str, headers: dict[str, str]) -> Any:
        import urllib.parse

        self.calls.append((method, url, dict(headers)))
        parts = urllib.parse.urlsplit(url)
        if parts.path == "/token":
            return rb.Response(
                200, {}, json.dumps({"token": "registry-bearer"}).encode()
            )
        if parts.path == rb.TAGS_PATH:
            query = dict(urllib.parse.parse_qsl(parts.query))
            names = list(self.tags)
            start = names.index(query["last"]) + 1 if "last" in query else 0
            chunk = names[start : start + self.page]
            headers_out = {}
            if start + self.page < len(names):
                link = self.override.get(
                    "link", f'<{rb.TAGS_PATH}?last={chunk[-1]}&n=100>; rel="next"'
                )
                headers_out["link"] = link
            if self.override.get("duplicate") and start:
                chunk = chunk + [names[0]]
            return rb.Response(
                200, headers_out, json.dumps({"name": rb.IMAGE, "tags": chunk}).encode()
            )
        if parts.path.startswith(rb.MANIFEST_PREFIX):
            tag = parts.path[len(rb.MANIFEST_PREFIX) :]
            seed = self.tags[tag]
            data = body(seed)
            out = {
                "docker-content-digest": digest(seed),
                "content-type": OCI_MANIFEST,
            }
            if self.override.get("status"):
                return rb.Response(self.override["status"], out, data)
            if self.override.get("no-digest"):
                out.pop("docker-content-digest")
            if self.override.get("wrong-digest"):
                out["docker-content-digest"] = digest("other")
            if self.override.get("media-type"):
                out["content-type"] = "text/html"
            if self.override.get("oversize"):
                data = b" " * (rb.MAX_MANIFEST + 1)
            if self.override.get("timeout"):
                raise TimeoutError("timed out")
            return rb.Response(200, out, data)
        match = rb.COMPARE_PATH.fullmatch(parts.path)
        if parts.hostname == rb.API_HOST and match:
            status = self.compare.get(match[1], "missing")
            if status == "missing":
                return rb.Response(404, {}, b'{"message":"Not Found"}')
            return rb.Response(
                200,
                {},
                json.dumps({"status": status, "ahead_by": 3, "behind_by": 0}).encode(),
            )
        raise AssertionError(f"unexpected request {url}")


def reader(net: FakeNet, token: str | None = None) -> Any:
    return rb.Reader(net, token)


TAGS = {"latest": "v300", "3.0.0": "v300", "3.0": "v300", "main": "m1", "abc1234": "m1"}


# --- transport --------------------------------------------------------------


def test_multi_page_listing_is_complete_and_get_only() -> None:
    net = FakeNet(TAGS, page=2)
    doc = rb.readback(reader(net))
    assert doc["complete"] and set(doc["tags"]) == set(TAGS)
    assert doc["tags"]["latest"] == {
        "digest": digest("v300"),
        "media_type": OCI_MANIFEST,
    }
    assert {method for method, _, _ in net.calls} == {"GET"}
    assert {c[1].split("/")[2] for c in net.calls} == {"ghcr.io"}
    assert doc["legacy_trusted"] is False and doc["activation_allowed"] is False
    accept = [c[2]["Accept"] for c in net.calls if rb.MANIFEST_PREFIX in c[1]]
    assert accept and all(a == ", ".join(rb.ACCEPT) for a in accept)
    assert len(rb.ACCEPT) == 4


@pytest.mark.parametrize(
    "override",
    [
        {
            "link": '<https://evil.example/v2/consiliency/pmcp/tags/list?n=100>; rel="next"'
        },
        {"link": '<http://ghcr.io/v2/consiliency/pmcp/tags/list?n=100>; rel="next"'},
        {"link": '</v2/consiliency/other/tags/list?n=100>; rel="next"'},
        {"duplicate": True},
        {"no-digest": True},
        {"wrong-digest": True},
        {"media-type": True},
        {"status": 404},
        {"status": 500},
        {"timeout": True},
        {"oversize": True},
    ],
)
def test_transport_failures_are_incomplete(override: dict[str, Any]) -> None:
    net = FakeNet(TAGS, page=2)
    net.override = override
    doc = rb.readback(reader(net, SECRET))
    assert doc["complete"] is False and doc["tags"] == {}
    assert doc["error"] == rb.INCOMPLETE_TEXT


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/v2/consiliency/pmcp/tags/list",
        "https://ghcr.io/v2/consiliency/other/tags/list",
        "https://api.github.com/repos/Consiliency/pmcp/git/refs",
        "https://api.github.com/repos/Consiliency/pmcp/compare/main...main",
        "http://ghcr.io/token",
        "https://user@ghcr.io/token",
        "https://ghcr.io:8443/token",
    ],
)
def test_other_hosts_and_paths_are_refused(url: str) -> None:
    net = FakeNet(TAGS)
    with pytest.raises(rb.ReadbackError):
        reader(net).request("GET", url, {})
    assert net.calls == []


@pytest.mark.parametrize("method", ["PUT", "POST", "PATCH", "DELETE", "HEAD"])
def test_a_non_get_is_refused(method: str) -> None:
    net = FakeNet(TAGS)
    with pytest.raises(rb.ReadbackError):
        reader(net).request(method, rb.TOKEN_URL, {})
    with pytest.raises(rb.ReadbackError):
        rb.urllib_transport(method, rb.TOKEN_URL, {})
    assert net.calls == []


def test_cli_never_prints_or_writes_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    net = FakeNet(TAGS)
    output = tmp_path / "r0.json"
    assert rb.main(["readback", "--output", str(output)], transport=net) == 0
    text = output.read_text() + capsys.readouterr().out
    assert SECRET not in text
    import base64

    assert base64.b64encode(f"token:{SECRET}".encode()).decode() not in text
    doc = json.loads(output.read_text())
    assert "latest" in doc["tags"] and doc["complete"]


def test_cli_output_has_timestamps_and_must_be_fresh(tmp_path: Path) -> None:
    output = tmp_path / "r0.json"
    assert rb.main(["readback", "--output", str(output)], transport=FakeNet(TAGS)) == 0
    doc = json.loads(output.read_text())
    started, finished = (
        rb.parse_time(doc["started_at"]),
        rb.parse_time(doc["finished_at"]),
    )
    assert started <= finished
    with pytest.raises(FileExistsError):
        rb.main(["readback", "--output", str(output)], transport=FakeNet(TAGS))


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        rb.main(["--help"])
    assert exc.value.code == 0


# --- contract fixtures ------------------------------------------------------


def snapshot(tags: dict[str, str], started_at: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "complete": True,
        "legacy_trusted": False,
        "activation_allowed": False,
        "started_at": started_at,
        "finished_at": started_at,
        "tags": {
            t: {"digest": digest(s), "media_type": OCI_MANIFEST}
            for t, s in tags.items()
        },
    }


def inventory(rows: list[dict[str, Any]], finished_at: str) -> dict[str, Any]:
    return {
        "complete": True,
        "stability_verified": True,
        "finished_at": finished_at,
        "runs": rows,
    }


def run(
    index: int,
    status: str,
    *,
    sha: str = SHA_A,
    event: str = "push",
    branch: str = "main",
) -> dict[str, Any]:
    return {
        "id": index,
        "status": status,
        "event": event,
        "head_branch": branch,
        "head_sha": sha,
    }


T_B0 = "2026-11-02T00:00:00+00:00"
T_R0 = "2026-11-02T00:10:00+00:00"
T_B1 = "2026-11-03T00:00:00+00:00"
T_R1 = "2026-11-03T00:10:00+00:00"


def drain(
    r0_tags: dict[str, str],
    r1_tags: dict[str, str],
    b0_rows: list[dict[str, Any]],
    b1_rows: list[dict[str, Any]],
    **times: str,
) -> Any:
    return rb.contract(
        "drain",
        snapshot(r0_tags, times.get("r0", T_R0)),
        snapshot(r1_tags, times.get("r1", T_R1)),
        b0=inventory(b0_rows, times.get("b0", T_B0)),
        b1=inventory(b1_rows, times.get("b1", T_B1)),
    )


def accepted(result: Any) -> bool:
    return result["complete"] is True and result["stops"] == []


def stopped(result: Any) -> bool:
    return result["complete"] is True and bool(result["stops"])


R0 = {"latest": "v300", "3.0.0": "v300", "3.0": "v300", "main": "m0", "0000aaa": "m0"}


# --- contract 1: drain -------------------------------------------------------


def test_drain_normal_write_is_accepted() -> None:
    r1 = dict(R0, main="m1", aaaaaaa="m1")
    assert accepted(drain(R0, r1, [run(1, "in_progress")], [run(1, "completed")]))


def test_drain_frozen_no_write_is_accepted() -> None:
    assert accepted(drain(R0, dict(R0), [run(1, "completed")], [run(1, "completed")]))


def test_drain_explaining_run_that_failed_before_pushing_is_accepted() -> None:
    assert accepted(drain(R0, dict(R0), [run(1, "queued")], [run(1, "completed")]))


def test_drain_two_explaining_runs_with_main_on_the_later_digest() -> None:
    r1 = dict(R0, main="m2", aaaaaaa="m1", bbbbbbb="m2")
    b0 = [run(1, "in_progress"), run(2, "queued", sha=SHA_B)]
    b1 = [run(1, "completed"), run(2, "completed", sha=SHA_B)]
    assert accepted(drain(R0, r1, b0, b1))


def test_drain_main_on_neither_new_digest_stops() -> None:
    r1 = dict(R0, main="m9", aaaaaaa="m1", bbbbbbb="m2")
    b0 = [run(1, "in_progress"), run(2, "queued", sha=SHA_B)]
    b1 = [run(1, "completed"), run(2, "completed", sha=SHA_B)]
    assert stopped(drain(R0, r1, b0, b1))


def test_drain_readback_before_inventory_finished_is_incomplete() -> None:
    result = drain(
        R0,
        dict(R0),
        [run(1, "completed")],
        [run(1, "completed")],
        r0="2026-11-01T23:59:59+00:00",
    )
    assert result["complete"] is False and result["stops"] == []
    result = drain(R0, dict(R0), [run(1, "completed")], [run(1, "completed")], r1=T_B1)
    assert result["complete"] is False


@pytest.mark.parametrize("field", ["started_at", "finished_at"])
@pytest.mark.parametrize("which", ["baseline", "current"])
def test_drain_readback_missing_a_timestamp_is_incomplete(
    field: str, which: str
) -> None:
    baseline, current = snapshot(R0, T_R0), snapshot(dict(R0), T_R1)
    (baseline if which == "baseline" else current).pop(field)
    result = rb.contract(
        "drain",
        baseline,
        current,
        b0=inventory([run(1, "completed")], T_B0),
        b1=inventory([run(1, "completed")], T_B1),
    )
    assert result["complete"] is False and result["stops"] == []


def test_drain_main_change_without_an_explaining_run_stops() -> None:
    r1 = dict(R0, main="m1", aaaaaaa="m1")
    assert stopped(drain(R0, r1, [run(1, "completed")], [run(1, "completed")]))


@pytest.mark.parametrize(
    "legacy_run",
    [
        run(1, "in_progress", event="push", branch="v3.0.1"),
        run(1, "in_progress", event="workflow_dispatch"),
        run(1, "in_progress", event="push", branch="feature"),
    ],
)
def test_drain_seven_hex_from_a_tag_push_or_non_main_run_stops(
    legacy_run: dict[str, Any],
) -> None:
    done = dict(legacy_run, status="completed")
    r1 = dict(R0, aaaaaaa="m1")
    assert stopped(drain(R0, r1, [legacy_run], [done]))


def test_drain_latest_change_from_a_v_tag_run_stops() -> None:
    tag_run = run(1, "in_progress", branch="v3.0.1")
    r1 = dict(R0, latest="v301", aaaaaaa="v301")
    r1["3.0.1"] = "v301"
    assert stopped(drain(R0, r1, [tag_run], [dict(tag_run, status="completed")]))


@pytest.mark.parametrize("new_tag", ["sha-" + SHA_A, "foo", "3.0.1", "1234567"])
def test_drain_other_new_tags_stop(new_tag: str) -> None:
    r1 = dict(R0, **{new_tag: "x"})
    assert stopped(drain(R0, r1, [run(1, "in_progress")], [run(1, "completed")]))


def test_drain_disappeared_tag_stops() -> None:
    r1 = dict(R0)
    r1.pop("0000aaa")
    assert stopped(drain(R0, r1, [run(1, "completed")], [run(1, "completed")]))


# --- contract 2: pre-enable --------------------------------------------------


def test_pre_enable_identical_is_accepted() -> None:
    assert accepted(
        rb.contract("pre-enable", snapshot(R0, T_R0), snapshot(dict(R0), T_R1))
    )


@pytest.mark.parametrize(
    "change",
    [
        {"sha-" + SHA_A: "m0"},
        {"aaaaaaa": "m1"},
        {"main": "m1"},
        {"foo": "m0"},
    ],
)
def test_pre_enable_any_new_tag_or_change_stops(change: dict[str, str]) -> None:
    result = rb.contract(
        "pre-enable", snapshot(R0, T_R0), snapshot(dict(R0, **change), T_R1)
    )
    assert stopped(result)


# --- contract 3: watch -------------------------------------------------------


def watch_contract(
    baseline: dict[str, str], current: dict[str, str], compare: dict[str, str]
) -> Any:
    net = FakeNet({}, compare=compare)
    return rb.contract(
        "watch",
        snapshot(baseline, T_R0),
        snapshot(current, T_R1),
        on_main=reader(net).compare_status,
    )


R2 = dict(R0)


def test_watch_first_promotion_is_accepted() -> None:
    current = dict(R2, main="c1", **{"sha-" + SHA_A: "c1"})
    assert accepted(watch_contract(R2, current, {SHA_A: "identical"}))


def test_watch_second_promotion_after_a_baseline_refresh_is_accepted() -> None:
    refreshed = dict(R2, main="c1", **{"sha-" + SHA_A: "c1"})
    current = dict(refreshed, main="c2", **{"sha-" + SHA_B: "c2"})
    assert accepted(
        watch_contract(refreshed, current, {SHA_A: "ahead", SHA_B: "identical"})
    )


@pytest.mark.parametrize(
    ("status", "ok"),
    [
        ("ahead", True),
        ("identical", True),
        ("diverged", False),
        ("behind", False),
        ("missing", False),
    ],
)
def test_watch_on_main_rule(status: str, ok: bool) -> None:
    current = dict(R2, **{"sha-" + SHA_A: "c1"})
    result = watch_contract(R2, current, {SHA_A: status})
    assert accepted(result) if ok else stopped(result)


@pytest.mark.parametrize("new_tag", ["foo", "aaaaaaa", "3.0.1", "pr-12"])
def test_watch_new_tag_outside_the_allowed_classes_stops(new_tag: str) -> None:
    assert stopped(watch_contract(R2, dict(R2, **{new_tag: "c1"}), {}))


def test_watch_main_must_match_an_on_main_sha_tag() -> None:
    assert stopped(watch_contract(R2, dict(R2, main="c1"), {}))
    current = dict(R2, main="c1", **{"sha-" + SHA_A: "c1"})
    assert stopped(watch_contract(R2, current, {SHA_A: "diverged"}))


def test_watch_existing_sha_tag_is_immutable() -> None:
    refreshed = dict(R2, **{"sha-" + SHA_A: "c1"})
    assert stopped(
        watch_contract(
            refreshed, dict(refreshed, **{"sha-" + SHA_A: "c2"}), {SHA_A: "ahead"}
        )
    )


def test_watch_compare_api_failure_is_incomplete() -> None:
    def transport(method: str, url: str, headers: dict[str, str]) -> Any:
        return rb.Response(500, {}, b"{}")

    result = rb.contract(
        "watch",
        snapshot(R2, T_R0),
        snapshot(dict(R2, **{"sha-" + SHA_A: "c1"}), T_R1),
        on_main=rb.Reader(transport).compare_status,
    )
    assert result["complete"] is False and result["stops"] == []


def test_compare_fixture_copies_the_recorded_response_shape() -> None:
    net = FakeNet({}, compare={SHA_A: "ahead"})
    assert reader(net).compare_status(SHA_A) == "ahead"
    method, url, _ = net.calls[0]
    assert method == "GET"
    assert (
        url == f"https://api.github.com/repos/Consiliency/pmcp/compare/{SHA_A}...main"
    )


# --- protected tags in every contract ---------------------------------------


@pytest.mark.parametrize("mode", ["drain", "pre-enable", "watch"])
@pytest.mark.parametrize("tag", ["3.0.0", "3.0", "latest", "2.9.1-rc1"])
@pytest.mark.parametrize("change", ["digest", "deleted"])
def test_protected_tag_change_stops_in_each_contract(
    mode: str, tag: str, change: str
) -> None:
    before = dict(R0, **{"2.9.1-rc1": "v291"})
    after = dict(before)
    if change == "digest":
        after[tag] = "moved"
    else:
        after.pop(tag)
    kwargs: dict[str, Any] = {}
    if mode == "drain":
        kwargs = {
            "b0": inventory([run(1, "completed")], T_B0),
            "b1": inventory([run(1, "completed")], T_B1),
        }
    if mode == "watch":
        kwargs = {"on_main": reader(FakeNet({})).compare_status}
    result = rb.contract(mode, snapshot(before, T_R0), snapshot(after, T_R1), **kwargs)
    assert stopped(result)
    assert any("protected tag" in stop for stop in result["stops"])


# --- watch driver -----------------------------------------------------------


COMMIT = "c" * 40


class FakeInventory:
    """The inventory module surface the driver uses."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []
        self.load_retention = inv.load_retention

    def baseline_refusals(self, doc: Any, mode: str) -> list[str]:
        return inv.baseline_refusals(doc, mode)

    def run_comparison(self, path: Path, mode: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((path.name, mode, kwargs.get("retention_path")))
        return dict(self.result)


def baseline_set(tmp_path: Path, r2_tags: dict[str, str]) -> Path:
    directory = tmp_path / "baselines"
    directory.mkdir()
    b1 = inv.inventory(
        lambda endpoint: (
            {"id": inv.REPOSITORY_ID}
            if endpoint == f"repos/{inv.REPOSITORY}"
            else {"id": inv.WORKFLOW_ID, "path": inv.WORKFLOW_PATH}
            if "?" not in endpoint
            else {"total_count": 0, "workflow_runs": []}
        )
    )
    assert b1["complete"] and b1["stability_verified"]
    (directory / "inventory-B1.json").write_text(json.dumps(b1))
    (directory / "registry-R2.json").write_text(json.dumps(snapshot(r2_tags, T_R0)))
    (directory / "retention.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "source": inv.RETENTION_SOURCE,
                "read_at": "2026-11-01T00:00:00Z",
                "days": 90,
                "maximum_allowed_days": 90,
            }
        )
    )
    return directory


LISTED = [f"{rb.BASELINE_DIR}/{name}" for name in rb.BASELINE_FILES]
CLEAN_INVENTORY = {"complete": True, "stops": [], "non_pr_not_completed": []}


def drive(
    tmp_path: Path,
    *,
    registry: dict[str, str],
    r2: dict[str, str] | None = None,
    listed: list[str] | None = None,
    inventory_result: dict[str, Any] | None = None,
    compare: dict[str, str] | None = None,
) -> tuple[str, list[str], FakeInventory, FakeNet]:
    directory = baseline_set(tmp_path, r2 if r2 is not None else registry)
    fake_inv = FakeInventory(inventory_result or CLEAN_INVENTORY)
    net = FakeNet(registry, compare=compare)
    outcome, details = rb.watch(
        COMMIT,
        directory,
        tmp_path / "work",
        reader=reader(net),
        ls_tree=lambda commit: LISTED if listed is None else listed,
        inventory_module=fake_inv,
    )
    return outcome, details, fake_inv, net


def test_watch_with_baselines_runs_both_comparisons(tmp_path: Path) -> None:
    outcome, _, fake_inv, net = drive(tmp_path, registry=TAGS)
    assert outcome == "COMPLETE"
    assert fake_inv.calls == [
        ("inventory-B1.json", "drained", tmp_path / "baselines" / "retention.json")
    ]
    assert any(rb.MANIFEST_PREFIX in url for _, url, _ in net.calls)
    assert (tmp_path / "work" / "inventory.json").exists()
    assert (tmp_path / "work" / "registry.json").exists()


def test_watch_registry_discrepancy_is_red(tmp_path: Path) -> None:
    outcome, details, fake_inv, _ = drive(
        tmp_path, registry=dict(TAGS, latest="moved"), r2=TAGS
    )
    assert outcome == "DISCREPANCY" and fake_inv.calls
    assert any("latest" in line for line in details)


def test_watch_inventory_discrepancy_is_red_and_registry_still_runs(
    tmp_path: Path,
) -> None:
    outcome, _, _, net = drive(
        tmp_path,
        registry=TAGS,
        inventory_result={"complete": True, "stops": ["missing run 9"]},
    )
    assert outcome == "DISCREPANCY"
    assert any(rb.MANIFEST_PREFIX in url for _, url, _ in net.calls)


def test_watch_discrepancy_alongside_incomplete_read_is_discrepancy(
    tmp_path: Path,
) -> None:
    outcome, _, _, _ = drive(
        tmp_path,
        registry=dict(TAGS, latest="moved"),
        r2=TAGS,
        inventory_result={"complete": False, "stops": [], "error": "unstable"},
    )
    assert outcome == "DISCREPANCY"


def test_watch_incomplete_read_is_incomplete(tmp_path: Path) -> None:
    outcome, _, _, _ = drive(
        tmp_path,
        registry=TAGS,
        inventory_result={"complete": False, "stops": [], "error": "unstable"},
    )
    assert outcome == "INCOMPLETE"


def test_watch_without_baselines_is_no_baseline_only_while_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PMCP_IMAGE_PUBLISH", raising=False)
    outcome, _, fake_inv, net = drive(tmp_path, registry=TAGS, listed=[])
    assert outcome == "NO_BASELINE" and not fake_inv.calls and not net.calls
    monkeypatch.setenv("PMCP_IMAGE_PUBLISH", "enabled")
    (tmp_path / "x").mkdir()
    outcome, _, _, _ = drive(tmp_path / "x", registry=TAGS, listed=[])
    assert outcome == "INCOMPLETE"


@pytest.mark.parametrize("value", ["", "Enabled", "enabled ", "true"])
def test_watch_non_exact_enable_values_still_allow_no_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PMCP_IMAGE_PUBLISH", value)
    outcome, _, _, _ = drive(tmp_path, registry=TAGS, listed=[])
    assert outcome == "NO_BASELINE"


@pytest.mark.parametrize(
    "listed", [LISTED[:2], LISTED + [f"{rb.BASELINE_DIR}/extra.json"]]
)
def test_watch_partial_or_extra_baseline_set_is_incomplete(
    tmp_path: Path, listed: list[str]
) -> None:
    outcome, _, fake_inv, _ = drive(tmp_path, registry=TAGS, listed=listed)
    assert outcome == "INCOMPLETE" and not fake_inv.calls


def test_watch_invalid_baseline_file_is_incomplete(tmp_path: Path) -> None:
    directory = baseline_set(tmp_path, TAGS)
    (directory / "retention.json").write_text('{"days": 90}')
    outcome, _ = rb.watch(
        COMMIT,
        directory,
        tmp_path / "work",
        reader=reader(FakeNet(TAGS)),
        ls_tree=lambda commit: LISTED,
        inventory_module=FakeInventory(CLEAN_INVENTORY),
    )
    assert outcome == "INCOMPLETE"


def test_watch_listing_failure_is_incomplete(tmp_path: Path) -> None:
    def broken(commit: str) -> list[str]:
        raise OSError("git unavailable")

    outcome, _ = rb.watch(
        COMMIT,
        tmp_path,
        tmp_path / "work",
        reader=reader(FakeNet(TAGS)),
        ls_tree=broken,
    )
    assert outcome == "INCOMPLETE"


@pytest.mark.parametrize("outcome", rb.OUTCOMES)
def test_each_outcome_writes_exactly_one_marker(tmp_path: Path, outcome: str) -> None:
    summary = tmp_path / "summary.md"
    rb.write_marker(summary, outcome, [f"{rb.MARKER}=COMPLETE smuggled", "detail"])
    text = summary.read_text()
    assert text.count(rb.MARKER) == 1
    assert f"{rb.MARKER}={outcome}\n" in text


def test_watch_cli_writes_the_marker_and_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.delenv("PMCP_IMAGE_PUBLISH", raising=False)
    monkeypatch.setattr(rb, "_ls_tree", lambda commit: [])
    code = rb.main(
        [
            "watch",
            "--commit",
            COMMIT,
            "--baseline-dir",
            str(tmp_path / "none"),
            "--work-dir",
            str(tmp_path / "work"),
            "--summary",
            str(summary),
        ],
        transport=FakeNet(TAGS),
    )
    assert code == 0 and f"{rb.MARKER}=NO_BASELINE" in summary.read_text()

    def crash(commit: str) -> list[str]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(rb, "_ls_tree", crash)
    monkeypatch.setattr(
        rb, "watch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    summary2 = tmp_path / "summary2.md"
    code = rb.main(
        [
            "watch",
            "--commit",
            COMMIT,
            "--baseline-dir",
            str(tmp_path / "none"),
            "--work-dir",
            str(tmp_path / "work2"),
            "--summary",
            str(summary2),
        ],
        transport=FakeNet(TAGS),
    )
    assert code == 1 and summary2.read_text().count(f"{rb.MARKER}=INCOMPLETE") == 1


def test_readback_cli_with_a_contract_exits_one_on_a_stop(tmp_path: Path) -> None:
    baseline = tmp_path / "r1.json"
    baseline.write_text(json.dumps(snapshot(dict(TAGS, latest="old"), T_R0)))
    output = tmp_path / "r2.json"
    code = rb.main(
        [
            "readback",
            "--output",
            str(output),
            "--baseline",
            str(baseline),
            "--mode",
            "pre-enable",
        ],
        transport=FakeNet(TAGS),
    )
    doc = json.loads(output.read_text())
    assert code == 1 and doc["comparison"]["stops"]
    assert doc["activation_allowed"] is False
