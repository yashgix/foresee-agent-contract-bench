"""Run the evaluation matrix and record every reply, so scoring never needs a model call."""

import json
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from bench import claude_cli, scenarios
from bench.snapshot import Snapshot

RESULTS = Path(__file__).resolve().parent.parent / "results"


@dataclass(frozen=True)
class Job:
    scenario: str
    condition: str
    model: str
    repeat: int

    @property
    def key(self) -> str:
        return f"{self.scenario}|{self.condition}|{self.model}|{self.repeat}"


def plan(scenario_ids: list[str], models: dict[str, int]) -> list[Job]:
    return [
        Job(s, c, m, r)
        for s in scenario_ids
        for c in scenarios.CONDITIONS
        for m, n in models.items()
        for r in range(1, n + 1)
    ]


def recorded(path: Path) -> list[dict]:
    """Records in a JSONL file. An unterminated last line (an interrupted append) is dropped; any other invalid
    line raises."""
    if not path.exists():
        return []
    text = path.read_text()
    lines = text.splitlines()
    records = []
    for number, line in enumerate(lines, 1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if number != len(lines) or text.endswith("\n"):
                raise ValueError(f"{path}:{number} is not valid JSON") from None
    return records


def prepare(path: Path) -> list[dict]:
    """Load records and leave the file ready to append: cut-off tail removed, last line terminated."""
    records = recorded(path)
    if path.exists() and path.read_text() != "".join(json.dumps(r) + "\n" for r in records):
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return records


def last_weekly(records: list[dict]) -> dict:
    return next(
        ({"seven_day": r["windows"]["seven_day"]} for r in reversed(records) if "seven_day" in r.get("windows", {})), {}
    )


def prompts(snap: Snapshot) -> dict[tuple[str, str], str]:
    """The prompt hash for every (scenario, condition) as the code builds it now."""
    return {
        (s.id, c): scenarios.prompt_hash(scenarios.system_prompt(snap, c), scenarios.user_prompt(s))
        for s in scenarios.load(snap)
        for c in scenarios.CONDITIONS
    }


def check_prompts(records: list[dict], current: dict[tuple[str, str], str]):
    """Refuse to mix replies to different prompts in one run."""
    stale = [r["key"] for r in records if current.get((r["scenario"], r["condition"])) != r["prompt_sha256"]]
    if stale:
        raise ValueError(f"{len(stale)} recorded replies were made with other prompts (e.g. {stale[0]}); use a new run")


class Budget:
    """Stops new calls at a call cap or once weekly subscription use reaches *stop_at* percent. Calls already in
    flight when the line is crossed still finish (at most workers - 1 more). With no usage known yet, the first
    replies probe it; if they carry none, the run stops rather than spend blind."""

    def __init__(self, max_calls: int, stop_at: float, last_seen: dict | None = None):
        self.left, self.stop_at, self.reason, self.known = max_calls, stop_at, "", False
        self.lock = threading.Lock()
        self.observe(last_seen or {}, probe=False)

    def take(self) -> bool:
        with self.lock:
            if self.reason:
                return False
            if self.left <= 0:
                self.reason = "call cap reached"
                return False
            self.left -= 1
            return True

    def observe(self, windows: dict, probe: bool = True):
        if "seven_day" in windows:
            self.known = True
            if windows["seven_day"] >= self.stop_at:
                self.reason = f"weekly use at {windows['seven_day']}% (stop at {self.stop_at}%)"
        elif probe and not self.known:
            self.reason = "weekly use unknown: the reply carried no usage data"

    def stop(self, reason: str):
        self.reason = reason


def reuse(snap: Snapshot, run: str, source: str) -> int:
    """Copy replies from another run whose prompts are unchanged, so only changed scenarios are paid for again."""
    current, out = prompts(snap), RESULTS / run / "answers.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    have = {r["key"] for r in prepare(out)}
    same = [
        r
        for r in recorded(RESULTS / source / "answers.jsonl")
        if current.get((r["scenario"], r["condition"])) == r["prompt_sha256"] and r["key"] not in have
    ]
    with out.open("a") as answers:
        answers.writelines(json.dumps({**r, "reused_from": source}) + "\n" for r in same)
    return len(same)


def execute(
    snap: Snapshot, run: str, jobs: list[Job], budget: Budget, workers: int, ask: Callable = claude_cli.ask
) -> dict:
    out = RESULTS / run / "answers.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = prepare(out)
    current = prompts(snap)
    check_prompts(existing, current)
    budget.observe(last_weekly(existing), probe=False)
    done = {record["key"] for record in existing}
    todo = [job for job in jobs if job.key not in done]
    built = {s.id: s for s in scenarios.load(snap)}
    write = threading.Lock()

    def one(job: Job):
        if not budget.take():
            return
        scenario = built[job.scenario]
        system, user = scenarios.system_prompt(snap, job.condition), scenarios.user_prompt(scenario)
        started = time.time()
        try:
            reply = ask(job.model, system, user)
        except (claude_cli.LimitReached, claude_cli.UnsafeRun) as exc:
            budget.stop(str(exc))
            return
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:  # logged; retried on the next run
            record = {"key": job.key, **asdict(job), "error": str(exc)[:300]}
            with write, (RESULTS / run / "errors.jsonl").open("a") as errors:
                errors.write(json.dumps(record) + "\n")
            return
        budget.observe(reply.windows)
        record = {
            "key": job.key,
            **asdict(job),
            "prompt_sha256": current[(job.scenario, job.condition)],
            "reply": reply.text,
            "reported_model": reply.model,
            "usage": reply.usage,
            "windows": reply.windows,
            "duration_ms": reply.duration_ms,
            "started": round(started),
        }
        with write, out.open("a") as answers:
            answers.write(json.dumps(record) + "\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    return {
        "planned": len(jobs),
        "already_done": len(done & {j.key for j in jobs}),
        "attempted": len(todo),
        "recorded": len(recorded(out)),
        "stopped": budget.reason,
    }
