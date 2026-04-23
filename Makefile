VENDOR := engine/freeorion

.PHONY: all bootstrap venv run test test-api perf playtest clean

all: bootstrap venv

bootstrap: $(VENDOR)/.git
$(VENDOR)/.git:
	@echo "==> fetching FreeOrion source (~340 MB, one time)"
	@mkdir -p engine
	git clone --depth=1 https://github.com/freeorion/freeorion.git $(VENDOR)
	@echo "==> bootstrap complete"

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -e .

run: venv
	.venv/bin/python freeorion.py

test: venv
	.venv/bin/python -m tests.qa

test-only: venv
	.venv/bin/python -m tests.qa $(PAT)

test-api: venv
	.venv/bin/python -m tests.api_qa

perf: venv
	.venv/bin/python -m tests.perf

playtest: venv
	.venv/bin/python -m tests.playtest

clean:
	rm -rf .venv freeorion_tui.egg-info __pycache__ */__pycache__
