PYTHON   := python3
VENV     := .venv
PIP      := $(VENV)/bin/pip
PYTHON_V := $(VENV)/bin/python

.PHONY: venv install install-api install-dev test lint format run-api up down db-up db-reset seed-data seed-db playwright-install run-etl clean

## Create virtual environment
venv:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

## Install ETL dependencies (includes seleniumbase + playwright)
install: venv
	$(PIP) install -r etl/requirements.txt playwright
	$(VENV)/bin/playwright install chromium

## Install API dependencies
install-api: venv
	$(PIP) install -r api/requirements.txt

## Install API + test dependencies
install-dev: venv
	$(PIP) install -r api/requirements-dev.txt

## Run API tests against the compose database (start it with `make db-up`); pass ARGS="-k search" to filter
test:
	cd api && ../$(VENV)/bin/pytest $(ARGS)

## Check lint and formatting (what CI runs)
lint:
	cd api && ../$(VENV)/bin/ruff check . && ../$(VENV)/bin/ruff format --check .

## Auto-fix lint issues and format code
format:
	cd api && ../$(VENV)/bin/ruff check --fix . && ../$(VENV)/bin/ruff format .

## Run the API with auto-reload on http://localhost:8000
run-api:
	cd api && ../$(VENV)/bin/uvicorn src.main:app --reload

## Start the database and API in Docker (API on http://localhost:8000)
up:
	docker compose up --build -d

## Stop the Docker stack (keeps the database volume)
down:
	docker compose down

## Start only the database (localhost:5433), for running the API with `make run-api`
db-up:
	docker compose up --build -d db

## Delete the database volume and rebuild, re-running migrations and seed data
db-reset:
	docker compose down -v
	docker compose up --build -d db

## Regenerate mock data (api/seed/data/*.json and api/seed/seed.sql)
seed-data:
	$(PYTHON) api/seed/generate.py

## Apply migrations + mock data to a hosted Postgres (e.g. Neon). Use a direct (non-pooled) URL:
##   read -rs DATABASE_URL && export DATABASE_URL && make seed-db
seed-db:
	@test -n "$$DATABASE_URL" || { echo "Set DATABASE_URL first (see Makefile comment)"; exit 1; }
	$(PYTHON_V) api/db/seed_remote.py

## Re-install Playwright Chromium browser only
playwright-install:
	$(VENV)/bin/playwright install chromium

## Run the ETL pipeline (pass STEPS= to run specific steps, e.g. STEPS="mrf_links_scrape mrf_prices")
run-etl:
	$(PYTHON_V) -m etl.run_pipeline $(if $(STEPS),--steps $(STEPS),)

## Remove venv
clean:
	rm -rf $(VENV)
