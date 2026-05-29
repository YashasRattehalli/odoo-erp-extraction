# ── The Enterprise ERP Extraction ──────────────────────────────────────────
# Convenience targets. Override the interpreter with `make PY=python3 ...`.

PY  ?= .venv/bin/python
PIP ?= $(PY) -m pip

.PHONY: help setup seed extract demo deidentify test spec clean

help:
	@echo "Targets:"
	@echo "  setup       create .venv, install deps, Chromium, spaCy model"
	@echo "  seed        populate the instance with a task-faithful dataset (Part 1)"
	@echo "  extract     run the extraction headless  -> output/invoice_extract.json"
	@echo "  demo        run headed with slow-mo for a live demo"
	@echo "  deidentify  run with the HIPAA Safe Harbor pass"
	@echo "  test        run the unit test suite"
	@echo "  spec        build the premium specification PDF (also copied to ~/Desktop)"
	@echo "  clean       remove caches, browser profile, and generated outputs"

setup:
	python3 -m venv .venv
	$(PIP) install --upgrade pip wheel
	$(PIP) install -e .
	$(PIP) install pytest
	$(PY) -m playwright install chromium
	$(PY) -m spacy download en_core_web_sm

seed:
	$(PY) scripts/seed_demo_data.py

extract:
	$(PY) -m odoo_extractor

demo:
	$(PY) -m odoo_extractor --headed --slow-mo 600 --log-level INFO

deidentify:
	$(PY) -m odoo_extractor --deidentify --output output/invoice_extract.deidentified.json

test:
	$(PY) -m pytest

spec:
	$(PY) spec/build_spec.py

clean:
	rm -rf .pw-profile output/*.json .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
