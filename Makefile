VENV := $(CURDIR)/.venv/bin

.PHONY: fmt lint test check

## Auto-fix formatting
fmt:
	cd src && $(VENV)/black . && $(VENV)/isort --profile black .

## Check formatting and linting (mirrors CI lint workflow)
lint:
	cd src && $(VENV)/black --check . && $(VENV)/isort --check-only --profile black . && $(VENV)/ruff check .

## Run unit tests (mirrors CI test-python workflow)
test:
	cd src && $(VENV)/pytest tests/ -q

## Run everything CI runs — use this before pushing
check: lint test
