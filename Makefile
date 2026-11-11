PY := .venv/bin/python

snapshot:
	$(PY) scripts/fetch_snapshot.py

setup:
	python3.14 -m venv .venv && .venv/bin/pip install -r requirements.lock

lint:
	ruff check . && ruff format --check .

test:
	$(PY) -m pytest

check: lint test

regression-demo:
	$(PY) -m bench regression-demo

contract:
	$(PY) -m bench contract

# Model calls on a Claude subscription; capped, resumable, stops at 95% weekly use.
eval:
	$(PY) -m bench eval v3 --models claude-haiku-5-5:2,claude-sonnet-5-5:1 --max-calls 300 --stop-at 95

score:
	$(PY) -m bench score v3

.PHONY: snapshot setup lint test check regression-demo contract eval score
