# Postgres + PostGIS, initialized with the schema and fictional mock data.
# Build context is the repo root (see docker-compose.yml).
#
# Scripts in /docker-entrypoint-initdb.d run once, in name order, on an empty data
# directory: migrations 001-011, the image's own 10_postgis.sh, then 999_seed.sql.
# To re-seed after regenerating data: `make db-reset`.
#
# Base image: the official postgis/postgis tags are amd64-only, so on Apple Silicon
# they run under emulation. imresamu/postgis is the same build published for amd64
# and arm64 by a docker-postgis maintainer. Override with
#   docker compose build --build-arg POSTGIS_IMAGE=postgis/postgis:16-3.4 db
ARG POSTGIS_IMAGE=imresamu/postgis:16-3.4
FROM ${POSTGIS_IMAGE}

COPY migrations/*.sql /docker-entrypoint-initdb.d/
COPY api/seed/seed.sql /docker-entrypoint-initdb.d/999_seed.sql
