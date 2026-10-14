PYTHON   := python3
VENV     := .venv
PIP      := $(VENV)/bin/pip
PYTHON_V := $(VENV)/bin/python

.PHONY: venv install install-api install-dev test lint format run-api up down db-up db-reset seed-data seed-db etl-migrate run-etl test-etl lint-etl etl-check clean

## Create virtual environment
venv:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

## Install ETL + test dependencies
install: venv
	$(PIP) install -r etl/requirements-dev.txt

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

## Apply migrations to the ETL's database (DATABASE_URL or etl/.env), without loading mock data
etl-migrate:
	$(PYTHON_V) -m etl.migrate

## Run the ETL pipeline (pass STEPS= to run specific steps, e.g. STEPS="hospitals hospital_quality";
## ARGS= for other options, e.g. ARGS="--mrf-limit 10")
run-etl:
	$(PYTHON_V) -m etl.run_pipeline $(if $(STEPS),--steps $(STEPS),) $(ARGS)

## Run ETL tests (database tests use the compose database: `make db-up`)
test-etl:
	cd etl && ../$(VENV)/bin/pytest $(ARGS)

## Lint the ETL (what CI runs)
lint-etl:
	cd etl && ../$(VENV)/bin/ruff check .

## Row-count and storage checks on the ETL's database; BASELINE_URL= compares with another database
etl-check:
	$(PYTHON_V) -m etl.checks $(if $(BASELINE_URL),--baseline-url "$(BASELINE_URL)",)

## Remove venv
clean:
	rm -rf $(VENV)
