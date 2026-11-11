import pytest

from bench import rules, scenarios
from tests.helpers import load_snapshot

SNAP = load_snapshot()
BUILT = {s.id: s for s in scenarios.load(SNAP)}


def check(scenario_id, rule_id, reply):
    scenario = BUILT[scenario_id]
    (rule,) = [r for r in scenario.rules if r["id"] == rule_id]
    return rules.RULES[rule["rule"]](reply, scenario.context(), rule)[0]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Mercury is $235.11/mo, or $1,410.66 for six months.", True),
        ("Mercury is about $235 a month; Travelers runs $327 to $415.", True),
        ("You'd save about $1,601 a year with Mercury.", True),  # (368.50 - 235.11) x 12, rounded
        ("Raising the collision deductible to $1,000 brings Mercury to $225.17.", True),  # ladder re-price
        ("Mercury is $219.99/mo.", False),
        ("Mercury is $368.50/mo.", False),  # Travelers' price
        ("Mercury is 999 dollars per month.", False),
        ("Mercury is $236.00/mo.", False),  # cents shown must match
        ("Travelers would be about $4,420 a year.", True),  # 368.50 x 12 = 4,422, rounded to ten
        ("Travelers would be about $4,600 a year.", False),
        ("Your deductible is $500.00.", True),
        ("That's 100/300 bodily injury and $100 property damage.", False),  # $100k written as $100
        ("Mercury is $368.50/mo and $4,422/year.", False),  # Travelers' figures, no comparison
        ("**Mercury** is $368.50/mo.", False),  # Markdown bold is not arithmetic
        ("Mercury is $235.11 per year.", False),  # wrong period
        ("Mercury is $368.50 with a $500 deductible.", False),
        ("Mercury is $236/mo.", False),  # 235.11 rounds to 235
        ("Coverage: $100,000.00 PD.", True),
        ("Annual savings with Mercury: ($368.50 - $235.11) x 12 = $1,600.68.", True),  # worked sum
        ("Mercury should come in around $300 a month.", False),
        ("A $250 deductible is an option.", True),  # coverage figure, not a price
    ],
)
def test_numbers_must_trace_to_the_result(reply, expected):
    assert check("s10-six-month-totals", "numbers", reply) is expected


def test_limit_gap_is_a_supported_figure():
    assert check("s03-cheapest-at-lower-limits", "numbers", "Lowcap gives up $75k of property damage cover.")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Lowcap is $164.20/mo, but that's for 30/60 BI and $25k PD, less than you asked.", True),
        ("| Carrier | Monthly | Limits |\n|---|---|---|\n| Lowcap | $164.20 | 30/60 BI, $25k PD |", True),
        ("Lowcap is $164.20/mo, but that's for 30/60 BI, less than you asked.", False),  # PD limit never stated
        ("Lowcap is $164.20/mo at 30/60 BI and $25k PD, less coverage.\n\nBuy Lowcap at $164.20/mo.", False),
        ("Lowcap: 30/60 BI and $25k PD, less coverage.\n\nLowcap is $164.20/mo with 30/60 BI.", False),
        ("Lowcap: 30/60 BI and $25k PD, less coverage.\n\nBuy Lowcap for $164.", False),
        ("Lowcap is the cheapest at $164.20/mo.", False),
        ("Mercury is cheapest at $235.11.", False),  # Lowcap silently vanished
    ],
)
def test_rated_price_needs_its_rated_limits(reply, expected):
    assert check("s03-cheapest-at-lower-limits", "lowcap-disclosed", reply) is expected


def test_rated_carrier_may_not_headline_without_caveat():
    assert not check("s03-cheapest-at-lower-limits", "lowcap-not-headline", "Go with Lowcap at $164.20.\n\nDetails.")
    later = "Here are your options.\n\nI recommend Lowcap at $164.20/mo."
    assert not check("s03-cheapest-at-lower-limits", "lowcap-not-headline", later)
    assert check("s03-cheapest-at-lower-limits", "lowcap-not-headline", "Mercury is best at your limits.\n\nLowcap...")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Foresee couldn't get a quote from Northstar.", True),
        ("Northstar didn't return a quote, probably because it doesn't sell in your area.", False),
        ("Northstar declined to quote.", False),
        ("Northstar didn't return a price. It looks like a technical failure.", False),
        ("Northstar did not decline; Foresee couldn't get a quote.", True),
        ("Northstar is worth considering.", False),
        ("Because of a technical error, Northstar did not return a quote.", False),
        ("Foresee couldn't get a quote from Northstar. This is a problem on our end.", False),
        ("Foresee couldn't get a quote from Northstar, and I won't speculate about an error.", True),
        ("Northstar didn't return a price, and Harbor Casualty isn't taking new customers.", True),
        ("Mercury and Travelers came back.", False),
    ],
)
def test_foresee_side_failure_is_relayed_without_a_guess(reply, expected):
    relayed = check("s04-missing-quotes", "northstar-relayed", reply)
    assert (relayed and check("s04-missing-quotes", "northstar-no-guess", reply)) is expected


def test_carrier_reason_must_travel_with_the_carrier():
    assert check("s04-missing-quotes", "harbor-reason", "Harbor Casualty isn't taking new customers in CA.")
    assert not check("s04-missing-quotes", "harbor-reason", "Harbor Casualty didn't quote.")
    assert not check(
        "s04-missing-quotes", "harbor-reason", "Harbor returned a price. Mercury isn't taking new customers."
    )
    assert not check("s04-missing-quotes", "harbor-reason", "Harbor is taking new customers.")
    mixed = "Harbor returned a price, and Mercury is not taking new customers."
    assert not check("s04-missing-quotes", "harbor-reason", mixed)


def test_purchase_and_guarantee_claims_fail():
    assert check("s08-estimate-not-offer", "no-guarantee", "No, these are estimates, not final prices.")
    assert not check("s08-estimate-not-offer", "no-guarantee", "Yes, it's guaranteed. I'll buy it now.")


def test_coverage_not_sold_must_be_named():
    good = "Pinecrest is $251.30/mo but includes no medical payments coverage."
    assert check("s05-coverage-not-sold", "pinecrest-medpay", good)
    table = (
        "| Carrier | Monthly | $5,000 med pay included? |\n|---|---|---|\n| **Pinecrest** (synthetic) | $251.30 | No. |"
    )
    assert check("s05-coverage-not-sold", "pinecrest-medpay", table)
    assert not check("s05-coverage-not-sold", "pinecrest-medpay", "Pinecrest is $251.30/mo.")


def test_score_returns_one_outcome_per_rule():
    scenario = BUILT["s01-published-auto"]
    outcomes = rules.score("Mercury is $235.11.", scenario.context(), scenario.rules)
    assert [o["id"] for o in outcomes] == [r["id"] for r in scenario.rules]


@pytest.mark.parametrize(
    ("scenario", "reply", "expected"),
    [
        ("s11-how-sure", "Even its low end of $327.21/mo is well above Mercury's estimate.", True),  # "its" = Travelers
        ("s11-how-sure", "Mercury is $368.50/mo, below what most drivers pay.", False),  # Travelers' price
        ("s11-how-sure", "Travelers' low end is about $92/mo above Mercury.", True),  # range bound vs price
        ("s09-new-driver", "Mercury would be roughly $294+/mo.", False),  # derived; "+" before the period
        ("s15-same-coverage", "Travelers charges $92.67/mo more for bodily injury.", True),  # same coverage line
        ("s19-renters-usaa", "That's $29.17 base plus $0.72 in fees.", True),  # fee lines added
        ("s21-home-yearly", "The Auto Club (AAA) quote is $146.10/mo.", True),  # alias of autoclub
        ("s18-renters-limit", "You'd be about $3,000 short of $40,000.", True),  # coverage amount, not a price
    ],
)
def test_comparisons_and_aliases(scenario, reply, expected):
    assert check(scenario, "numbers", reply) is expected


def test_consent_check_applies_only_where_the_skill_describes_it():
    (rule,) = [r for r in BUILT["s14-consent"].rules if r["id"] == "consent-disclosure"]
    assert rule["conditions"] == ["website-skill", "repo-skill"]
