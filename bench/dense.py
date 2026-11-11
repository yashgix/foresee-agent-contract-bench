"""Parse Foresee's `dense` quote text (format published at go-foresee.com/docs) into plain data."""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

FORESEE_FAILURE = "foresee couldn't get a quote"
# Rows the tool description documents that carry no price data this bench scores.
INFORMATIONAL = {"input_flags:", "not_rated:", "skipped", "skipped:"}
RATED = re.compile(r"(\w+)=([^\s(]+)\(asked ([^,]+),(less|more)\)")


@dataclass
class Rated:
    axis: str
    rated: str
    asked: str
    coverage: str


@dataclass
class Carrier:
    key: str
    entity: str
    monthly: Decimal
    ci: tuple[Decimal, Decimal] | None = None
    rated: list[Rated] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverages: list[tuple[str, Decimal]] = field(default_factory=list)
    ladders: dict[str, dict[str, Decimal]] = field(default_factory=dict)


@dataclass
class Missing:
    key: str
    reason: str

    @property
    def by(self):
        return "foresee" if FORESEE_FAILURE in self.reason.lower() else "carrier"


@dataclass
class Line:
    state: str
    line: str
    selection: dict
    state_notes: list[str] = field(default_factory=list)
    assumptions: list[dict] = field(default_factory=list)
    tighten_by: list[str] = field(default_factory=list)
    missing: list[Missing] = field(default_factory=list)
    carriers: list[Carrier] = field(default_factory=list)
    other: dict[str, str] = field(default_factory=dict)


def parse(text: str) -> list[Line]:
    lines: list[Line] = []
    for raw in text.strip().splitlines():
        prefix, _, rest = raw.partition(" ")
        if prefix == "Q":
            state, line, sel = rest.split(" ", 2)
            lines.append(Line(state, line, json.loads(sel.removeprefix("sel="))))
            continue
        if prefix in INFORMATIONAL:
            if lines:
                lines[-1].other[prefix] = rest
            continue
        current = lines[-1]
        if prefix == "state_notes:":
            current.state_notes = [note["text"] for note in json.loads(rest)]
        elif prefix == "assumptions:":
            current.assumptions = json.loads(rest)
        elif prefix == "tighten_by:":
            current.tighten_by = rest.split(",")
        elif prefix == "not_priced:":
            current.missing += _missing(rest)
        elif prefix == "C":
            current.carriers.append(_carrier(rest))
        elif prefix == "W":
            current.carriers[-1].warnings.append(rest)
        elif prefix == "L":
            current.carriers[-1].coverages = [_pair(item, ":") for item in rest.split(",")]
        elif prefix == "D":
            axis, _, ladder = rest.partition(" ")
            _, _, rungs = ladder.partition("=")
            current.carriers[-1].ladders[axis] = dict(_pair(r, ":") for r in rungs.rstrip(",").split(","))
        else:
            raise ValueError(f"unknown dense row: {raw[:60]!r}")
    return lines


def _missing(rest: str) -> list[Missing]:
    """`a,b:reason ; c:reason`: several carriers can share one reason."""
    found = []
    for group in rest.split(" ; "):
        keys, _, reason = group.partition(":")
        found += [Missing(key.strip(), reason.strip()) for key in keys.split(",")]
    return found


def _pair(item: str, sep: str) -> tuple[str, Decimal]:
    name, _, value = item.rpartition(sep)
    return name, Decimal(value)


def _carrier(rest: str) -> Carrier:
    key, entity, monthly, *segments = rest.split("|")
    carrier = Carrier(key, entity, Decimal(monthly))
    for segment in segments:
        kind, _, value = segment.partition(" ")
        if kind == "ci":
            low, high = value.split("-")
            carrier.ci = (Decimal(low), Decimal(high))
        elif kind == "rated":
            carrier.rated = [Rated(*match) for match in RATED.findall(value)]
    return carrier


def structured(text: str) -> dict:
    """A tool result shaped like the live output schema, rebuilt from published dense text."""
    lines = parse(text)
    return {
        "state": lines[0].state,
        "serviceable": True,
        "by_line": {line.line: _by_line(line) for line in lines},
        "dense": text.strip(),
    }


def _by_line(line: Line) -> dict:
    return {
        "coverage_selection": line.selection,
        "state_notes": [{"text": note} for note in line.state_notes],
        "assumptions": line.assumptions,
        "tighten_by": line.tighten_by,
        "carriers": [_carrier_entry(c) for c in line.carriers],
        "failures": [{"carrier_key": m.key, "by": m.by, "error": m.reason} for m in line.missing],
    }


def _carrier_entry(carrier: Carrier) -> dict:
    entry = {
        "carrier_key": carrier.key,
        "carrier": carrier.entity,
        "monthly": float(carrier.monthly),
        "confidence_interval": {"low": float(carrier.ci[0]), "high": float(carrier.ci[1])}
        if carrier.ci
        else {"confidence": "unmeasured"},
        "warnings": carrier.warnings,
    }
    if carrier.rated:
        entry["rated_vs_asked"] = [
            {"axis": r.axis, "asked": r.asked, "rated": r.rated, "coverage": r.coverage} for r in carrier.rated
        ]
    return entry
