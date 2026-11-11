import json
import shutil

import pytest

from bench import contract, regression, snapshot
from tests.helpers import load_snapshot

SNAP = load_snapshot()


def ids(findings):
    return {f.id for f in findings}


def test_snapshot_refuses_a_changed_file(tmp_path):
    copy = tmp_path / "snap"
    shutil.copytree(snapshot.DEFAULT, copy)
    (copy / "docs.md").write_text("edited")
    with pytest.raises(ValueError, match=r"docs\.md"):
        snapshot.load(copy)


def test_published_requests_validate_and_name_only_schema_fields():
    assert contract.check_example_requests(SNAP) == []


def test_licensing_paths_are_flagged_and_schema_paths_are_not():
    found = ids(contract.check_advertised_paths(SNAP))
    assert {"path-auto.drivers[].years_licensed", "path-auto.drivers[].age_first_licensed"} <= found
    assert "path-auto.drivers[].violations" not in found
    assert "path-auto.vehicles[].annual_mileage" not in found


def test_unnamed_keys_are_reported_with_their_path():
    schema = SNAP.quote_tool()["inputSchema"]
    request = {"profile": {"zip_code": "90066", "age": 30, "auto": {"drivers": [{"years_licensed": 2}]}}}
    assert contract.Schema(schema).unnamed_keys(request, schema) == ["profile.auto.drivers[].years_licensed"]


def test_same_version_different_skill_text():
    assert ids(contract.check_skill_copies(SNAP)) == {"skill-version", "skill-index"}


def test_coverage_sums_report_each_cent_gap():
    (finding,) = contract.check_coverage_sums(SNAP)
    assert any("mercury: C 235.11 vs L sum 235.12" in e["quote"] for e in finding.evidence)


def test_observation_resolves_once_a_quote_is_gone(tmp_path):
    registry = tmp_path / "observations.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "id": "kept",
                    "title": "t",
                    "detail": "d",
                    "quotes": [{"file": "docs.md", "quote": "Nothing is priced"}],
                },
                {
                    "id": "gone",
                    "title": "t",
                    "detail": "d",
                    "quotes": [{"file": "docs.md", "quote": "not in the docs"}],
                },
            ]
        )
    )
    assert ids(contract.check_observations(SNAP, registry)) == {"kept"}


def test_every_recorded_observation_is_still_published():
    recorded = {item["id"] for item in json.loads(contract.OBSERVATIONS.read_text())}
    assert ids(contract.check_observations(SNAP)) == recorded


def test_a_server_rename_the_docs_still_advertise_is_caught_and_cleared():
    outcome = regression.demo()
    assert outcome["new_findings"] == ["path-auto.vehicles[].garaging_zip"]
    assert outcome["after_docs_fix"] == []
