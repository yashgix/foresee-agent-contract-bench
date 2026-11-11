# Agent Contract Bench

How well do AI assistants keep the important parts of an insurance quote when they summarise it for a person, and
how much do the provider's own instructions help?

**Write-up: [yashgix.github.io/foresee-agent-contract-bench](https://yashgix.github.io/foresee-agent-contract-bench/)**

[Foresee](https://go-foresee.com) gives AI assistants structured insurance estimates for auto, home and renters (auto
in California is live today) through MCP, the standard way assistants call outside tools. For each carrier it supports
it returns a monthly estimate with its range, per-coverage costs, a price ladder showing how the price changes for each
listed limit or deductible (one change at a time), and the assumptions behind it; for carriers it couldn't price, it
gives the reason. It also publishes detailed instructions for how assistants should present all this.

This bench measures what survives when an assistant turns that result into a few sentences, and checks that
Foresee's agent-facing documents stay consistent with each other. It evaluates assistant replies, not the accuracy of
Foresee's prices.

An independent project, not affiliated with Foresee. No call is made to Foresee's quoting tools; scoring runs offline
from recorded replies.

## Results

276 recorded replies: 23 scenarios × 4 instruction setups × Claude Haiku 5.5 (twice) and Sonnet 5.5 (once), scored
by 714 deterministic checks taken from Foresee's own guidance. Every failing check was read in context; 3 verdicts
were set by hand where the pattern is known to be wrong (`results/v3/review.json`).

| Setup | Replies passing all selected checks | Limits in wrong units | Unsupported prices | Guessed reasons |
|---|---|---|---|---|
| No instructions | 47/69 | 12 | 6 | 3 |
| Foresee's tool description | 53/69 | 0 | 9 | 1 |
| + website skill | 65/69 | 0 | 3 | 0 |
| + repository skill | 62/69 | 0 | 3 | 0 |

- **Generally preserved:** every check on showing different limits beside a lower price, relaying carriers without a
  price with their own reason, naming assumptions, state notes and price ranges passed in every setup.
- **Errors observed:** replies that never said the prices are estimates (12 of the 84 where it was checked); prices
  worked out from the price ladder or coverage lines in ways Foresee says aren't valid, and a few arithmetic slips;
  without instructions, a $100,000 limit written as "$100" and guessed reasons for a missing quote.
- **With Foresee's instructions:** the tool description removed the unit errors, though on its own it left more
  unsupported prices (9) than no instructions (6); with either skill, guessed reasons went to zero and unsupported
  prices to 3.
- **Grader check:** on 94 checks labelled during development by reading each full reply (not by an independent
  evaluator), the grader agreed on 91: 1 false failure among 48 failures, no misses in a fixed-seed sample of 44 passes,
  and 2 misses found outside the sample. The grader is a set of pattern rules, so it can miss unusual wording.
- The consent-disclosure check applies only to the two skill setups, the only ones that describe live confirmation.

Small samples: read gaps of a few replies as direction, not precision.

**Contract checks:** 10 consistency observations about Foresee's published documents (texts that differ from each
other or from the live schema, two notes about one example, one comparison with the MCP specification), e.g. the docs suggest `auto.drivers[].years_licensed`, which the live input schema doesn't name. These
are consistency observations, not pricing defects. `make regression-demo` shows the check catching a schema rename the
docs still advertise, and clearing once the docs match.

## Data

| Evidence | Count |
|---|---|
| Live Foresee quotes | 0: Foresee's terms allow quotes only with your own information, by a person actually shopping, and don't allow republishing quote data |
| Published Foresee results (auto, home, renters, auto + renters examples in its docs) | 4 |
| Scenarios on unchanged published results | 17 |
| Scenarios with one labelled synthetic change (fictional carriers marked "(synthetic)", a state note, a coverage ask) | 6 |

Foresee's documents aren't redistributed here. `snapshot/2026-11-10/MANIFEST.json` lists each source and its
sha256; `scripts/fetch_snapshot.py` downloads them (public pages, the GitHub repository at a pinned commit, and one
standard MCP handshake: initialize and tools/list) and checks the hashes.

## Run it

Python 3.14 and `ruff` 0.17.4.

```sh
make setup            # .venv with pinned dependencies (requirements.lock)
make snapshot         # fetch Foresee's public documents and verify them against the manifest
make check            # ruff + pytest (88 tests; without the snapshot, the tests that need it skip)
make score            # re-score the 276 recorded replies offline -> results/v3/summary.json
make contract         # documentation observations -> results/contract.json
make regression-demo  # rename a schema field the docs advertise; the check fails, then passes
make eval             # new model calls: Claude Code CLI on a subscription login, capped and resumable
```

`python scripts/build_site.py v3` rebuilds the write-up in `docs/` from the recorded results, so every number on the
page comes from the data.

## How it works

- `bench/contract.py` compares the live MCP schema, the docs, both copies of the `quote-insurance` skill and the
  agent-skills index, plus quoted statement pairs in `contract/observations.json`.
- `bench/scenarios.py` builds each scenario from a published example (plus at most one labelled change) and the
  prompt for each setup; `bench/claude_cli.py` asks Claude through the Claude Code CLI with every tool off, no API
  keys, and a weekly-usage stop; every reply is stored with its prompt hash in `results/<run>/answers.jsonl`.
- `bench/rules.py` grades replies against Foresee's guidance; `bench/score.py` summarises by setup, model, scenario
  category and evidence type; `bench/validate.py` compares the grader with hand labels.

Design, trade-offs and known gaps: [ARCHITECTURE.md](ARCHITECTURE.md).

## Limits

No live quotes; the published examples may predate the current server. Claude models only. The tool result reaches
the model as text in the prompt, without the `presentation` field. "No instructions" is a bare baseline; real MCP
clients always receive Foresee's tool description. Pattern rules miss some paraphrases and some subtle errors.

## About

An independent engineering project by Yash Gupta, exploring agent-response reliability, automated evaluations and
regression testing. The grading code was independently reviewed by OpenAI Codex (final re-check: GO).

Shared to show the work; not licensed for reuse.
