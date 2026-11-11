from decimal import Decimal

from bench import contract, dense
from tests.helpers import load_snapshot

SNAP = load_snapshot()
AUTO = contract.examples(SNAP, "response")[0]


def test_published_auto_example_parses():
    (line,) = dense.parse(AUTO)
    assert (line.state, line.line) == ("CA", "auto")
    assert [c.key for c in line.carriers] == ["mercury", "travelers"]
    assert line.missing[0].key == "usaa" and line.missing[0].by == "carrier"
    travelers = line.carriers[1]
    assert travelers.monthly == Decimal("368.50")
    assert travelers.ci == (Decimal("327.21"), Decimal("415.00"))
    assert travelers.ladders["bi"]["30/60"] == Decimal("-32.83")
    assert "auto.drivers[].age_first_licensed" in line.tighten_by


def test_structured_result_validates_against_live_output_schema():
    from jsonschema import Draft202012Validator

    result = dense.structured(AUTO)
    Draft202012Validator(SNAP.quote_tool()["outputSchema"]).validate(result)
    assert result["by_line"]["auto"]["failures"][0]["by"] == "carrier"
