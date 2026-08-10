.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

# Pinned explicitly. This machine's /etc/pip.conf points at a corporate mirror that nobody
# outside one network can reach, which would make the README's install step a lie. See D20.
INDEX   := https://pypi.org/simple/

.PHONY: help setup fetch sample quality premise test typecheck lint check clean

help:
	@echo "setup      create $(VENV) and install from public PyPI"
	@echo "fetch      fetch the full in-scope slice into data/raw (about 400k complaints)"
	@echo "sample     rebuild the committed offline sample from data/raw"
	@echo "quality    regenerate docs/data-quality.md from data/complaints.parquet"
	@echo "premise    regenerate docs/premise.md -- the baselines the agent has to beat"
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
