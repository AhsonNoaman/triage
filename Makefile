.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

# Pinned explicitly. This machine's /etc/pip.conf points at a corporate mirror that nobody
# outside one network can reach, which would make the README's install step a lie. See D20.
INDEX   := https://pypi.org/simple/

.PHONY: help setup fetch sample quality premise eval-smoke eval eval-resume eval-replay plot test typecheck lint check clean

help:
	@echo "setup      create $(VENV) and install from public PyPI"
	@echo "fetch      fetch the full in-scope slice into data/raw (about 400k complaints)"
	@echo "sample     rebuild the committed offline sample from data/raw"
	@echo "quality    regenerate docs/data-quality.md from data/complaints.parquet"
	@echo "premise    regenerate docs/premise.md -- the baselines the agent has to beat"
	@echo "eval-smoke   ten complaints, about a dollar. Run this before spending the rest"
	@echo "eval       run the agent over a sampled split. NEEDS A KEY AND COSTS MONEY"
	@echo "eval-resume  continue an interrupted run; already-recorded episodes are not re-bought"
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

# Ten complaints against the real API for about a dollar. Everything the full run touches --
# the key, the rate limit, the tool loop, the schema, the transcript, the scorer -- runs here
# first. Finding a bug at complaint 480 of 500 is the expensive way to find it.
eval-smoke:
	$(PY) scripts/run_eval.py --split validation --limit 10 --yes

# The target that spends money: roughly $60 per split at the default 250 per class, a couple
# of hours at eight workers. Quotes the bill and asks before starting. The transcript is
# written as it goes, so an interrupted run resumes rather than restarts.
eval:
	$(PY) scripts/run_eval.py --split validation

eval-resume:
	$(PY) scripts/run_eval.py --split validation --resume

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
