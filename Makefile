PYTHON ?= python3.12
UV ?= uv

.PHONY: install install-web test lint typecheck web-build demo

install:
	$(UV) pip install --python $(PYTHON) -e ".[dev]"
	cd apps/web && npm install

install-web:
	cd apps/web && npm install

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check packages apps/api tests
	cd apps/web && npm run lint

typecheck:
	$(PYTHON) -m mypy
	cd apps/web && npm run typecheck

web-build:
	cd apps/web && npm run build

demo:
	./scripts/dev_up.sh
