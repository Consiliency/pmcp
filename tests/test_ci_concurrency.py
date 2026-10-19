"""Every integration run has its own namespace; PR heads may supersede."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_validation_cancellation_is_only_per_pr() -> None:
    for name in ("test.yml", "windows.yml"):
        doc = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        group = doc["concurrency"]["group"]
        assert (
            group
            == "${{ github.workflow }}-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || format('{0}-{1}-{2}', github.event_name, github.sha, github.run_id) }}"
        )
        assert (
            doc["concurrency"]["cancel-in-progress"]
            == "${{ github.event_name == 'pull_request' }}"
        )
        # A unique run_id on every non-PR avoids GitHub pending replacement,
        # even when cancel-in-progress is false. Schedule/dispatch are isolated.
        assert "github.run_id" in group and "github.event_name" in group


def test_release_is_outside_pr_cancellation() -> None:
    doc = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert "concurrency" not in doc
