from bench import dense


def test_rated_segment_and_foresee_failure():
    text = "\n".join(
        [
            'Q CA auto sel={"bi":"100/300"}',
            "not_priced: northstar:Foresee couldn't get a quote.",
            "C lowcap|Lowcap (synthetic)|164.20|rated bi=30/60(asked 100/300,less) pd=25(asked 100,less)",
            "W no online quote page",
            "L BI:100.00,PD:64.20",
        ]
    )
    (line,) = dense.parse(text)
    carrier = line.carriers[0]
    assert [(r.axis, r.rated, r.asked, r.coverage) for r in carrier.rated] == [
        ("bi", "30/60", "100/300", "less"),
        ("pd", "25", "100", "less"),
    ]
    assert carrier.warnings == ["no online quote page"]
    assert line.missing[0].by == "foresee"


def test_grouped_failures_and_informational_rows():
    text = "\n".join(
        [
            "skipped home,renters: Foresee quotes auto in CA today.",
            'Q CA auto sel={"bi":"100/300"}',
            "input_flags: dob and age disagree",
            "not_priced: geico,progressive:Foresee couldn't get a quote. ; usaa:USAA: military families only",
            "not_rated: rideshare_use",
        ]
    )
    (line,) = dense.parse(text)
    assert [(m.key, m.by) for m in line.missing] == [
        ("geico", "foresee"),
        ("progressive", "foresee"),
        ("usaa", "carrier"),
    ]
    assert line.other == {"input_flags:": "dob and age disagree", "not_rated:": "rideshare_use"}
