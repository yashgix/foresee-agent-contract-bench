"""Deterministic checks of an assistant's reply against Foresee's own presentation rules.

Every rule reads only the reply text and the tool result the assistant was given, so a recorded reply can be
re-scored offline at any time. Rules return (passed, evidence). They are patterns, not understanding: they are
tested against good and bad replies, and failures are read in context before any is reported.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from itertools import combinations, product

from bench import dense

MONEY = re.compile(
    r"\$\s?(?P<whole>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<cents>\d{1,2}))?(?P<k>\s?[kK]\b)?"
    r"|\b(?P<w2>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<c2>\d{1,2}))?\s+dollars\b"
)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
PERIODS = (
    (1, r"/\s?mo\b|/\s?month|a month|per month|monthly|/\s?mo\.?"),
    (6, r"(per|for|every|over) (the |a )?(6|six)[- ]months?|/\s?6\s?mo"),
    (12, r"/\s?yr|/\s?year|a year|per year|annually|yearly|over a year"),
)
# A coverage figure names its coverage right next to it: "$500 deductible", "deductible of $500", "$100,000 PD".
COVERAGE_AFTER = re.compile(
    r"\s*(?:\w+\s){0,2}?(deductibles?|limits?|PD|BI|UM|property damage|bodily injury|liability|medical payments|"
    r"med ?pay|coverage|collision|comprehensive|comp|coll|contents|personal property|dwelling)\b",
    re.IGNORECASE,
)
COVERAGE_BEFORE = re.compile(r"(deductibles?|limits?|PD|BI|coverage)\s*(of|at|is|are|:)?\s*$", re.IGNORECASE)
# A liability limit written in plain dollars: "$100 property damage" means $100, not the $100k Foresee priced.
LIMIT_IN_DOLLARS = re.compile(
    r"\$\s?(\d{1,3})(?![\d,.]|\s?[kK]\b)\s*(?:of\s+)?(?:property[- ]damage|PD|bodily[- ]injury|BI)\b"
)
WORKED = re.compile(
    r"[\d)]\s?[\u2212\u00d7*x=+-]\s?[\d($]|=\s?\$|\$[\d,.]+\s?-\s?\$|\bvs\.?|versus|difference|gap|spread|"
    r"sav(e|es|ing|ings)|cheaper|to (tie|match|beat)|"
    r"more than|less than|instead of",
    re.IGNORECASE,
)
# "above", "below", "than" compare two figures only when a second figure or another carrier ("its") is in view.
RELATIVE = re.compile(r"\b(above|below|than|need to)\b", re.IGNORECASE)
OTHER = re.compile(r"\b(its|their|the other)\b", re.IGNORECASE)
CAVEAT = (
    r"not (a )?like[- ]for[- ]like|not (directly )?comparable|not apples|lower (liability )?limits?|less coverage|"
    r"different coverage|doesn'?t offer|does not offer|the most (it|this carrier) offers|nearest|only offers|"
    r"maximum (it|this carrier) offers|below (what|the) you asked|instead of|not what you asked|"
    r"can'?t offer|cannot offer|less than (what )?you asked|less (liability |bodily injury |property damage )?coverage|"
    r"not (a )?fair comparison|isn'?t (a )?fair|not a real saving|doesn'?t match your request|not offered"
)
GUESS = (
    r"\b(doesn'?t|does not|don'?t|do not) (sell|offer|write|operate|quote|cover)|not available|unavailable in|"
    r"declin|not taking|isn'?t taking|only (sells|quotes|serves|works)|underwrit|eligib|military|because|due to|"
    r"likely|probably|might|may not|perhaps|typically|usually|looks like|seems|appears|technical|error|outage|"
    r"glitch|temporar|issue|problem|on (our|their|its) (end|side)|down\b|busy|timed? out"
)
# Phrases that decline to guess: "did not decline", "I won't speculate about an error", "no reason given".
NOT_A_GUESS = re.compile(
    r"\b(did not|didn'?t|not|never)\s+declin\w*|(won'?t|will not|would not|wouldn'?t|can'?t|cannot|don'?t|do not)"
    r" (speculate|guess)[^.]*|(no|without a|not given a|didn'?t give a|don'?t (know|have)) (reason|why)[^.]*",
    re.IGNORECASE,
)
MISSING = (
    r"no (price|quote|rate)|not (priced|quoted|available|returned)|n/a\b|\u2014|\u2013|neither|unable|"
    r"excluded|left out|(didn'?t|did not|couldn'?t|could not|can'?t|cannot|wasn'?t able to|weren'?t able to) "
    r"(return|come back|quote|get|price|give|provide|be priced|be quoted)|without (a )?(price|quote)|"
    r"(isn'?t|is not|aren'?t|not) (taking|accepting|quoting)|only (insures|quotes|covers|sells|serves|works)|"
    r"declin|ended up without|missing"
)
RECOMMEND = (
    r"recommend|best (option|choice|pick|bet|deal|price)|go with|i'?d (pick|choose|go)|cheapest|lowest|winner|buy"
)


ALIASES = {"autoclub": ["Autoclub", "Auto Club", "AAA", "Automobile Club", "CSAA"], "usaa": ["USAA"]}


@dataclass
class Amount:
    value: Decimal
    cents: bool
    period: int | None  # 1, 6 or 12 months when stated
    coverage: bool  # names a coverage right next to it
    start: int

    @property
    def price(self) -> bool:
        return self.period is not None or (self.cents and not self.coverage)


def sentences(text: str) -> list[str]:
    """Prose split into sentences; a table row stays whole."""
    found = []
    for line in text.splitlines():
        found += [line] if line.lstrip().startswith("|") else re.split(r"(?<=[.!?])\s+", line)
    return [s for s in found if s.strip()]


def named(text: str, names: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) for name in names)


def carrier_names(result: dict) -> dict[str, list[str]]:
    """Every carrier in the result, priced or not, by key, with the name a reply would use."""
    keys = [c["carrier_key"] for line in result["by_line"].values() for c in line["carriers"] + line["failures"]]
    return {key: ALIASES.get(key, [key.title()]) for key in keys}


def amounts(text: str) -> list[Amount]:
    """Each dollar figure with its stated period (per month, 6 months or year) and whether a coverage is named
    right next to it."""
    found = []
    for match in MONEY.finditer(text):
        whole, cents = match["whole"] or match["w2"], match["cents"] or match["c2"]
        value = Decimal(whole.replace(",", "") + (f".{cents}" if cents else "")) * (1000 if match["k"] else 1)
        after = text[match.end() : match.end() + 24]
        period = next((months for months, p in PERIODS if re.match(rf"\+?\s?({p})", after, re.IGNORECASE)), None)
        coverage = bool(
            COVERAGE_AFTER.match(after) or COVERAGE_BEFORE.search(text[max(0, match.start() - 20) : match.start()])
        )
        found.append(Amount(value, bool(cents), period, coverage, match.start()))
    return found


Prices = dict[int, set[Decimal]]  # months -> allowed amounts for that period


def carrier_prices(result: dict) -> dict[str, Prices]:
    """Per carrier and period: its monthly and interval, ladder re-prices and single steps, spreads with another
    carrier, multi-line totals it is part of, and per-coverage costs, each per month, x6 and x12. A combined change
    of two levers is not derivable (rating is not linear), so it is not here."""
    lines = dense.parse(result["dense"])
    allowed: dict[str, Prices] = {c.key: {1: set(), 6: set(), 12: set()} for line in lines for c in line.carriers}

    def add(key: str, values):
        for months in (1, 6, 12):
            allowed[key][months] |= {v * months for v in values}

    for line in lines:
        figures = {
            c.key: [
                c.monthly,
                *(c.ci or ()),
                *(c.monthly + d for ladder in c.ladders.values() for d in ladder.values()),
            ]
            for c in line.carriers
        }
        for c in line.carriers:
            rungs = [d for ladder in c.ladders.values() for d in ladder.values()]
            fees = [amount for code, amount in c.coverages if code == "FEE"]
            add(c.key, [*figures[c.key], *(abs(d) for d in rungs), sum(fees, Decimal(0))])
            add(c.key, [amount for _, amount in c.coverages])
        for a, b in combinations(line.carriers, 2):
            # A comparison: any of one carrier's figures against the other's price or range, or the same coverage.
            gaps = [abs(x - y) for x in figures[a.key] for y in (b.monthly, *(b.ci or ()))]
            gaps += [abs(x - y) for x in figures[b.key] for y in (a.monthly, *(a.ci or ()))]
            lines_b = dict(b.coverages)
            gaps += [abs(amount - lines_b[code]) for code, amount in a.coverages if code in lines_b and code != "FEE"]
            add(a.key, gaps)
            add(b.key, gaps)
    if len(lines) > 1:
        for pick in product(*(line.carriers for line in lines)):
            for c in pick:
                add(c.key, [sum(p.monthly for p in pick)])
    return allowed


def is_supported(amount: Amount, allowed: set[Decimal]) -> bool:
    """Cents shown must match to the cent. Whole dollars must equal the value rounded to the dollar (half up) or
    cut to the dollar; a figure of $1,000 or more may also be rounded to the nearest ten."""
    if amount.cents:
        return any(abs(amount.value - a) < Decimal("0.005") for a in allowed)
    one = Decimal(1)
    return any(
        amount.value in (a.quantize(one, ROUND_HALF_UP), a.quantize(one, ROUND_FLOOR))
        or (amount.value >= 1000 and amount.value == (a / 10).quantize(one, ROUND_HALF_UP) * 10)
        for a in allowed
    )


def _sentence_at(text: str, position: int) -> str:
    start = max(text.rfind(mark, 0, position) for mark in (". ", "\n", "! ", "? "))
    ends = [i for i in (text.find(mark, position) for mark in (". ", "\n")) if i != -1]
    return text[start + 1 : min(ends) if ends else len(text)]


def _allowed(amount: Amount, sentence: str, by_carrier: dict[str, Prices], names: dict[str, list[str]]):
    """A price in a sentence about one carrier must be that carrier's, unless the sentence works a sum or a
    comparison; a stated period must match."""
    mentioned = [key for key in by_carrier if named(sentence, names[key])]
    compared = WORKED.search(sentence) or (
        RELATIVE.search(sentence) and (len(amounts(sentence)) > 1 or OTHER.search(sentence))
    )
    pool = [by_carrier[mentioned[0]]] if len(mentioned) == 1 and not compared else by_carrier.values()
    periods = [amount.period] if amount.period else [1, 6, 12]
    return set().union(*(prices[m] for prices in pool for m in periods))


def numbers_supported(reply, context, params):
    result = context["result"]
    by_carrier, names = carrier_prices(result), carrier_names(result)
    problems = []
    for amount in amounts(reply):
        sentence = _sentence_at(reply, amount.start)
        # Prices are checked; limits and deductibles only for units (below): suggesting a coverage level is fine.
        if amount.price and not is_supported(amount, _allowed(amount, sentence, by_carrier, names)):
            problems.append(str(amount.value))
    problems += [f"${m.group(1)} written as a liability limit" for m in LIMIT_IN_DOLLARS.finditer(reply)]
    return not problems, f"unsupported: {', '.join(problems)}" if problems else "every $ figure traces back"


def mentions(reply, context, params):
    hits = [p for p in params["patterns"] if re.search(p, reply, re.IGNORECASE)]
    passed = len(hits) == len(params["patterns"]) if params.get("all") else bool(hits)
    return passed, f"matched {len(hits)} of {len(params['patterns'])} required patterns"


def avoids(reply, context, params):
    hits = [m.group(0) for p in params["patterns"] for m in [re.search(p, reply, re.IGNORECASE)] if m]
    return not hits, f"found: {hits}" if hits else "none found"


def passages(reply: str, names: list[str], others: list[str]) -> list[tuple[str, str]]:
    """(row, context) for each sentence or table row naming the carrier. Context is the table header for a row,
    or the next sentence for prose when it names no other carrier (an explanation usually follows)."""
    rows, found = sentences(reply), []
    for i, row in enumerate(rows):
        if not named(row, names):
            continue
        if row.lstrip().startswith("|"):
            context = _table_header(rows, i)
        else:
            following = rows[i + 1] if i + 1 < len(rows) else ""
            context = "" if following.lstrip().startswith("|") or named(following, others) else following
        found.append((row, context))
    return found


def _table_header(rows: list[str], i: int) -> str:
    start = i
    while start > 0 and rows[start - 1].lstrip().startswith("|"):
        start -= 1
    return rows[start]


def _others(context: dict, names: list[str]) -> list[str]:
    everyone = [n for group in carrier_names(context["result"]).values() for n in group]
    return [n for n in everyone if not named(n, names)]


def _own_clauses(text: str, names: list[str], others: list[str]) -> list[str]:
    """Clauses about this carrier: from a clause naming it until a clause naming another carrier, plus any
    clause before it in the same sentence that names no other carrier ("Because of an error, X didn't quote")."""
    found, following, pending = [], False, []
    for clause in re.split(r"[,;:]\s+|(?<=[.!?])\s+|\s+(?:and|but|while)\s+", text):
        if named(clause, others) and not named(clause, names):
            following, pending = False, []
        elif named(clause, names):
            following = True
            found += pending
            pending = []
        elif not following:
            pending.append(clause)
        if following and clause.strip():
            found.append(clause)
        if re.search(r"[.!?]$", clause.strip()) and not following:
            pending = []  # a cause only counts within its own sentence
    return found


def relays_missing(reply, context, params):
    """The carrier is said to have no price, with its own reason when the scenario names one."""
    others = _others(context, params["names"])
    found = passages(reply, params["names"], others)
    if not found:
        return False, f"{params['names'][0]} is never mentioned"
    own = [" ".join(_own_clauses(f"{row} {extra}", params["names"], others)) for row, extra in found]
    reason = params.get("reason")
    said = [text for text in own if re.search(MISSING if not reason else f"{MISSING}|{reason}", text, re.IGNORECASE)]
    if not said:
        return False, f"{params['names'][0]} named, but not as a carrier without a price"
    if reason and not any(re.search(reason, text, re.IGNORECASE) for text in said):
        return False, f"{params['names'][0]} relayed without its own reason"
    return True, f"{params['names'][0]} relayed"


def no_guessed_reason(reply, context, params):
    others = _others(context, params["names"])
    found = passages(reply, params["names"], others)
    if not found:
        return False, f"{params['names'][0]} is never mentioned"
    about = [c for row, extra in found for c in _own_clauses(f"{row} {extra}", params["names"], others)]
    guesses = [c.strip() for c in about if re.search(GUESS, NOT_A_GUESS.sub("", c), re.IGNORECASE)]
    return not guesses, f"guessed: {guesses[0][:160]}" if guesses else "relayed without a guessed reason"


def _disclosed(text: str, params: dict) -> bool:
    """A caveat phrase, the scenario's own pattern, or every rated limit."""
    if re.search(CAVEAT + (f"|{params['pattern']}" if params.get("pattern") else ""), text, re.IGNORECASE):
        return True
    groups = params.get("groups", [])
    return bool(groups) and all(re.search(g, text, re.IGNORECASE) for g in groups)


def _priced(row: str, prices: Prices) -> bool:
    every = set().union(*prices.values())
    return any(a.price or is_supported(a, every) for a in amounts(row) if not a.coverage)


def discloses_rated(reply, context, params):
    """Every mention of the carrier with one of its prices carries the caveat or all rated limits (in the row,
    its table header or the next sentence), at least one mention does, and every rated limit is stated somewhere."""
    others = _others(context, params["names"])
    found = passages(reply, params["names"], others)
    if not found:
        return False, f"{params['names'][0]} is never mentioned"
    prices = carrier_prices(context["result"]).get(params["names"][0].lower(), {1: set(), 6: set(), 12: set()})
    disclosed = [(row, extra) for row, extra in found if _disclosed(f"{row} {extra}", params)]
    bare = [row for row, extra in found if _priced(row, prices) and (row, extra) not in disclosed]
    if bare or not disclosed:
        return False, f"shown without the rated coverage: {(bare or [found[0][0]])[0][:160]}"
    unstated = [g for g in params.get("groups", []) if not re.search(g, reply, re.IGNORECASE)]
    if unstated:
        return False, f"rated limit never stated: {unstated[0]}"
    return True, "rated coverage stated with every price"


def rated_not_headline(reply, context, params):
    """Neither the opening nor any recommendation leads with the carrier without its caveat."""
    others = _others(context, params["names"])
    opening = next((p for p in re.split(r"\n\s*\n", reply) if p.strip()), "")
    leads = [opening] if named(opening, params["names"]) else []
    leads += [
        f"{row} {extra}"
        for row, extra in passages(reply, params["names"], others)
        if re.search(RECOMMEND, row, re.IGNORECASE)
    ]
    bare = [lead for lead in leads if not _disclosed(lead, params)]
    return not bare, f"leads with it, no caveat: {bare[0][:160]}" if bare else "never headlined without the caveat"


RULES = {
    "numbers_supported": numbers_supported,
    "mentions": mentions,
    "avoids": avoids,
    "relays_missing": relays_missing,
    "no_guessed_reason": no_guessed_reason,
    "discloses_rated": discloses_rated,
    "rated_not_headline": rated_not_headline,
}


def score(reply: str, context: dict, rules: list[dict]) -> list[dict]:
    outcomes = []
    for rule in rules:
        passed, evidence = RULES[rule["rule"]](reply, context, rule)
        outcomes.append({"id": rule["id"], "category": rule["category"], "passed": passed, "evidence": evidence})
    return outcomes
