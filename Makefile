.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

# Pinned explicitly. This machine's /etc/pip.conf points at a corporate mirror that nobody
# outside one network can reach, which would make the README's install step a lie. See D20.
INDEX   := https://pypi.org/simple/

.PHONY: help setup fetch sample quality premise eval eval-replay plot test typecheck lint check clean

help:
	@echo "setup      create $(VENV) and install from public PyPI"
	@echo "fetch      fetch the full in-scope slice into data/raw (about 400k complaints)"
	@echo "sample     rebuild the committed offline sample from data/raw"
	@echo "quality    regenerate docs/data-quality.md from data/complaints.parquet"
	@echo "premise    regenerate docs/premise.md -- the baselines the agent has to beat"
	@echo "eval       run the agent over a sampled split. NEEDS A KEY AND COSTS MONEY"
	@echo "eval-replay  re-score the recorded run. free, no key, no network"
	@echo "plot       redraw docs/frontier.png from whatever has been measured"
	@echo "test       pytest"
	@echo "typecheck  mypy --strict"
	@echo "lint       ruff"
	@echo "check      lint + typecheck + test"

setup:
	python3 -m venv $(VENV)
	$(PIP) install --quiet --index-url $(INDEX) --upgrade pip
	$(PIP) install --quiet --index-url $(INDEX) -e '.[dev,analysis]'
	@echo "ready. run 'make check'"

fetch:
	$(PY) scripts/fetch_cfpb.py

sample:
	$(PY) scripts/build_sample.py

quality:
	$(PY) scripts/quality_report.py

premise:
	$(PY) scripts/premise_test.py

# The only target that spends money. Roughly $30-60 per split at the default 250 per class.
# Writes data/transcripts/<split>.jsonl as it goes, so a run that dies is re-scorable and
# resumable rather than lost.
eval:
	$(PY) scripts/run_eval.py --split validation

# Everything downstream of the model's confidence, recomputed from the committed transcript.
# This is the target that makes the eval auditable: anyone can reproduce every number in
# docs/eval.md without a key.
eval-replay:
	$(PY) scripts/run_eval.py --split validation --replay data/transcripts/validation.jsonl

plot:
	$(PY) scripts/plot_frontier.py

test:
	$(VENV)/bin/pytest

typecheck:
	$(VENV)/bin/mypy

lint:
	$(VENV)/bin/ruff check .

check: lint typecheck test

clean:
	rm -rf $(VENV) .mypy_cache .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
