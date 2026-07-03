PYTHON ?= python
PIP ?= $(PYTHON) -m pip

.PHONY: lint format-check isort-check type-check test cli-check integration package all

lint:
	flake8 lightnow_cli --count --select=E9,F63,F7,F82 --show-source --statistics
	flake8 lightnow_cli --count --extend-ignore=C901 --max-line-length=127 --statistics

format-check:
	black --check lightnow_cli tests

isort-check:
	isort --check-only lightnow_cli tests

type-check:
	mypy lightnow_cli --ignore-missing-imports

test:
	pytest --cov=lightnow_cli --cov-report=term-missing --cov-fail-under=85

cli-check:
	$(PIP) install -e .
	lightnow --help
	lightnow --version

integration:
	$(PIP) install -e .
	lightnow --help
	lightnow --version
	tmp=$$(mktemp -d); \
	trap 'rm -rf "$$tmp"' EXIT; \
	printf '%s\n' '{"name": "io.lightnow/test-server", "version": "1.0.0", "description": "Test server for CI"}' > "$$tmp/server.json"; \
	printf '%s\n' '# Test Server' '' 'This is test documentation.' '' 'More content.' > "$$tmp/docs.md"; \
	lightnow validate --server "$$tmp/server.json" --docs "$$tmp/docs.md"
	! lightnow status || true
	@echo "Integration tests passed!"

package:
	$(PIP) install -e ".[dev]"
	rm -rf dist
	$(PYTHON) -m build
	$(PYTHON) -m twine check dist/*

all: lint format-check isort-check type-check test cli-check integration package
