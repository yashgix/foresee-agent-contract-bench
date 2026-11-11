import json

import pytest

from bench import claude_cli, run, scenarios, score, validate
from tests.helpers import load_snapshot

SNAP = load_snapshot()


def test_prompts_are_deterministic_and_conditions_differ():
    first = scenarios.load(SNAP)[0]
    prompts = {c: scenarios.system_prompt(SNAP, c) for c in scenarios.CONDITIONS}
    assert len(set(prompts.values())) == len(scenarios.CONDITIONS)
    assert "Foresee" not in prompts["no-instructions"]
    assert scenarios.user_prompt(first) == scenarios.user_prompt(scenarios.load(SNAP)[0])


def test_published_scenarios_carry_the_docs_example_verbatim():
    docs = SNAP.text("docs.md")
    for scenario in scenarios.load(SNAP):
        if scenario.origin == "published":
            assert scenario.result["dense"] in docs


def test_synthetic_carriers_are_labelled():
    for scenario in scenarios.load(SNAP):
        published = {c["carrier_key"] for c in scenarios.load(SNAP)[0].result["by_line"]["auto"]["carriers"]}
        for line in scenario.result["by_line"].values():
            for carrier in line["carriers"]:
                if scenario.origin == "variant" and carrier["carrier_key"] not in published | {"farmers"}:
                    assert "(synthetic)" in carrier["carrier"]


def fake_ask(windows):
    def ask(model, system, user):
        return claude_cli.Reply("Mercury is $235.11, an estimate.", model, {}, windows, 1)

    return ask


def test_run_is_resumable_and_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    jobs = run.plan(["s01-published-auto"], {"m": 2})
    first = run.execute(SNAP, "r", jobs, run.Budget(3, 95), 1, fake_ask({"seven_day": 50}))
    assert first["recorded"] == 3 and first["stopped"] == "call cap reached"
    second = run.execute(SNAP, "r", jobs, run.Budget(10, 95), 1, fake_ask({"seven_day": 50}))
    assert second["recorded"] == len(jobs) and second["already_done"] == 3


def test_run_stops_at_the_weekly_line(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    jobs = run.plan(["s01-published-auto"], {"m": 2})
    outcome = run.execute(SNAP, "r", jobs, run.Budget(10, 95), 1, fake_ask({"seven_day": 96}))
    assert outcome["recorded"] == 1 and outcome["stopped"].startswith("weekly use at 96")


def test_scores_are_recomputed_from_recorded_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    run.execute(
        SNAP, "r", run.plan(["s08-estimate-not-offer"], {"m": 1}), run.Budget(10, 95), 1, fake_ask({"seven_day": 50})
    )
    summary = score.write(SNAP, "r")
    assert {row["condition"] for row in summary["replies_passing_all_selected_checks"]} == set(scenarios.CONDITIONS)
    assert (tmp_path / "r" / "scores.jsonl").exists()


def test_a_hand_review_overturns_one_check_and_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    run.execute(
        SNAP, "r", run.plan(["s08-estimate-not-offer"], {"m": 1}), run.Budget(1, 95), 1, fake_ask({"seven_day": 50})
    )
    key = run.recorded(tmp_path / "r" / "answers.jsonl")[0]["key"]
    verdict = {"key": key, "check": "estimate", "passed": False, "note": "reason"}
    (tmp_path / "r" / "review.json").write_text(json.dumps([verdict]))
    (item,) = score.score_run(SNAP, "r")
    (estimate,) = [o for o in item["outcomes"] if o["id"] == "estimate"]
    assert (estimate["passed"], estimate["automatic"], estimate["review"]) == (False, True, "reason")


def test_replies_to_other_prompts_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    jobs = run.plan(["s08-estimate-not-offer"], {"m": 1})
    run.execute(SNAP, "r", jobs, run.Budget(1, 95), 1, fake_ask({"seven_day": 50}))
    path = tmp_path / "r" / "answers.jsonl"
    record = {**run.recorded(path)[0], "prompt_sha256": "other"}
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="other prompts"):
        run.execute(SNAP, "r", jobs, run.Budget(5, 95), 1, fake_ask({"seven_day": 50}))
    with pytest.raises(ValueError, match="other prompts"):
        score.score_run(SNAP, "r")


def test_reuse_copies_only_unchanged_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    jobs = run.plan(["s08-estimate-not-offer"], {"m": 1})
    run.execute(SNAP, "old", jobs, run.Budget(4, 95), 1, fake_ask({"seven_day": 50}))
    old = tmp_path / "old" / "answers.jsonl"
    records = run.recorded(old)
    records[0]["prompt_sha256"] = "changed since"
    old.write_text("".join(json.dumps(r) + "\n" for r in records))
    assert run.reuse(SNAP, "new", "old") == len(jobs) - 1


def test_a_resumed_run_stops_if_last_usage_was_over_the_line(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    jobs = run.plan(["s08-estimate-not-offer"], {"m": 1})
    run.execute(SNAP, "r", jobs, run.Budget(1, 95), 1, fake_ask({"seven_day": 97}))
    outcome = run.execute(SNAP, "r", jobs, run.Budget(10, 95), 1, fake_ask({"seven_day": 50}))
    assert outcome["recorded"] == 1 and outcome["stopped"].startswith("weekly use at 97")


def test_grader_agreement_counts_each_cell(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    run.execute(SNAP, "r", run.plan(["s08-estimate-not-offer"], {"m": 1}), run.Budget(1, 95), 1, fake_ask({}))
    score.write(SNAP, "r")
    key = run.recorded(tmp_path / "r" / "answers.jsonl")[0]["key"]
    labels = [{"key": key, "check": "estimate", "human": True}, {"key": key, "check": "no-guarantee", "human": False}]
    (tmp_path / "r" / "labels.jsonl").write_text("".join(json.dumps(label) + "\n" for label in labels))
    found = validate.agreement("r")
    assert (found["labelled"], found["grader_pass_human_pass"], found["grader_pass_human_fail"]) == (2, 1, 1)
