# Architecture

Two independent parts share one snapshot of Foresee's public, agent-facing documents.

```
snapshot/2026-11-10/        Foresee's documents (fetched, not stored), each with source URL and sha256 (MANIFEST.json)
        │
        ├── bench/contract.py ──► results/contract.json      Part 1: do the documents agree with each other?
        │      uses contract/observations.json (quoted statement pairs, re-checked on every run)
        │
        └── bench/scenarios.py ─► prompts ─► bench/claude_cli.py ─► results/<run>/answers.jsonl
               scenarios/scenarios.json         (subscription, tool-free)        │
                                                                                 ▼
                                              bench/rules.py + bench/score.py ─► scores.jsonl, summary.json
                                                                               Part 2: do assistants relay it faithfully?
```

## Part 1: contract checks (`bench/contract.py`)

Foresee's contract with an assistant lives in several places at once: the MCP `tools/list` schema and description
the server returns, the `/docs` page, the skill on the website, the skill in the plugin repository, the OpenAPI
document and the agent-skills index. Each check compares two of them:

| Check | Compares | Why it matters |
|---|---|---|
| `check_example_requests` | every request in `/docs` against the live input schema (JSON Schema validation plus a strict key walk) | the schema says a key it doesn't name "is carried but never rated" |
| `check_advertised_paths` | every profile path the docs, skills and example responses tell agents to send, against the live schema | the same: an agent that follows the guidance should send a rated field |
| `check_coverage_sums` | per-coverage monthly costs (`L`) against the headline monthly (`C`) in published examples | agents that add the lines should reach the headline price |
| `check_skill_copies` | the two skill copies' text and version labels, and the agent-skills index digest | installed copies should be able to tell which text they have |
| `check_observations` | hand-recorded pairs of statements, each quoted verbatim | an observation drops out by itself once Foresee changes either text |

The snapshot loader refuses any file whose hash differs from the manifest, so a finding always refers to exactly
what was published on 10 Nov 2026. A new capture is a new folder; running the same checks on it shows which
observations Foresee has already resolved.

## Part 2: relay evaluation

**Scenarios** (`scenarios/scenarios.json`, built by `bench/scenarios.py`). 23 scenarios on Foresee's four published
example results: 17 use a result unchanged (a test asserts its dense text is a substring of the docs) with different
consumer questions; 6 add one labelled change: fictional carriers (priced ones marked "(synthetic)"), a state note, or
a different coverage ask with the real carriers removed. No invented price is attributed to a real insurer. Each
scenario has a category (price comparison, coverage equivalence, uncertainty, assumptions, missing carriers,
discounts, calculations, consent and purchase, state rules, confirmation). The tool result is rebuilt from the dense
text into the live output schema's shape and validated against that schema in the tests. A rule may name the setups
it applies to: the consent disclosure applies only where a skill describes live confirmation.

**Conditions** (`scenarios.CONDITIONS`). The same reply is requested four ways: no Foresee text at all; Foresee's
server instructions and tool description (what every MCP client receives); plus the website skill; plus the
repository skill. The last two are the two published versions of `quote-insurance` 0.8.6.

**Runner** (`bench/claude_cli.py`, `bench/run.py`). One Claude Code CLI call per reply, on a subscription login,
with every tool, MCP server, setting and slash command off, in an empty directory and a minimal environment with no
API keys. The stream is refused if it reports an API key, any tool, paid overage or a rejected limit. The binary must
be signed by Anthropic. This follows the pattern of Arcturus's subscription worker. Calls run in parallel, the run is
resumable (completed keys are skipped), and it stops at a call cap or once weekly plan use reaches a set line.
Every reply is recorded with its prompt hash, so scoring never needs a model. A run refuses to mix replies made
with different prompts; `--reuse` copies replies from an earlier run only where the prompt hash still matches (run
`v2` reused 96 of `main`'s replies and re-ran the 24 whose scenarios changed after review; `v3` reused all 120 of
`v2` and added 156 for 13 new scenarios).

**Rules** (`bench/rules.py`). Deterministic, so a recorded reply always gets the same score:

| Rule | Foresee's guidance it encodes |
|---|---|
| `numbers_supported` | "State only numbers the result contains — never interpolate." A $ figure read as a price must trace to a monthly price, its interval, a ladder re-price or step, a per-coverage cost, or a spread between carriers, per month, 6 months or year (the skill asks for 6-month totals and the annual spread). Coverage amounts and bare whole-dollar figures are checked only for units. Whole dollars may round (to the dollar, or to ten for $1,000 or more); cents must match. |
| `discloses_rated` | "Put the rated limits beside that price every time you show it." Every priced mention needs the caveat in its row, table header or next sentence, and every rated limit must be stated. |
| `rated_not_headline` | "never lead with it as the best option without saying so" |
| `relays_missing` | "a carrier never silently vanishes", with the carrier's own reason |
| `no_guessed_reason` | "`foresee` means Foresee couldn't get that quote — say exactly that, and never guess or add why." |
| `mentions` / `avoids` | scenario-specific patterns: "estimate", the state note, the understated-price warning, no purchase or guarantee claims |

## Choices and trade-offs

- **Deterministic rules over an LLM judge.** Reproducible and free to re-run; the cost is that patterns can miss a
  correct paraphrase or accept a wrong one. Mitigation: unit tests with good and bad replies for each rule (including
  every case the independent reviewer raised), and reading every failing reply. A documented hand verdict
  (`results/<run>/review.json`) can overturn a check; run `v3` has three.
- **Text replay, not a live MCP session.** The assistant receives the tool result inside the prompt, not through a
  real client. This isolates what the instructions change, but it isn't how Claude.ai or Cursor render tool results.
- **Published examples, not live quotes.** No call was made to Foresee's quoting tools. The examples' numbers are
  Foresee's own and may predate the current server.
- **Claude models only.** The available independent reviewer (Codex) was reserved for review; paid OpenAI calls were
  not authorised. Results say nothing about ChatGPT or Gemini.

## Grader validation

`results/v3/labels.jsonl` holds labels for 94 checks, made by reading each full reply during the build (by Claude,
the build assistant, not an independent annotator): every check the grader failed, a fixed-seed sample of four
passing checks per rule category (`bench/validate.sample`, seed 11), and three misses found while auditing.
`bench/validate.agreement` compares them with the grader's own verdict (before any hand override): 91 agree, 1 false
failure, 2 false passes (both outside the random sample).

## Known gaps

From three independent reviews and the hand labels, kept visible rather than hidden:
- A guess two or more sentences after the carrier's name is not attributed to it.
- A repeated price with partial limits plus a phrase such as "less coverage" passes.
- Price figures are checked against a wide set of derivable values; a wrong figure that coincides with another
  derivable value after rounding passes (one such case is recorded). Coverage amounts and bare whole-dollar figures
  (no cents, no period) are checked only for units, so "Mercury would be about $300" is not caught.
- Saying a price is not final in unusual words ("unverified", "could be somewhat lower") can be missed.
- An unterminated last record is dropped silently on resume; the first calls of a fresh run go out before usage is
  known; `skipped` rows before the first line are ignored.
