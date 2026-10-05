# Production image for the SurgiCAL API. Build context is the repo root.
#
# Two stages: "build" installs dependencies into a virtualenv; the final stage
# copies only that virtualenv and the app code, so pip's caches and build
# tooling never reach the image that ships.

# ---- build stage ----
FROM python:3.12-slim AS build

# All dependencies ship as prebuilt wheels (psycopg2-binary bundles libpq), so no
# compiler or system packages are needed.
# The venv is created without pip; the base image's pip installs into it
# (--python), so pip itself never ends up in the runtime image.
RUN python -m venv --without-pip /opt/venv

COPY api/requirements.txt /tmp/requirements.txt
RUN pip --python /opt/venv/bin/python install --no-cache-dir --disable-pip-version-check \
    -r /tmp/requirements.txt

# ---- runtime stage ----
FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Run as an unprivileged user: a compromised process can't modify the image or system files.
RUN useradd --system --no-create-home --uid 10001 app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=app:app api/ ./api/

# Set by CI (--build-arg GIT_COMMIT=<sha>) and reported by /health. Declared last so
# a new commit doesn't invalidate the cached dependency layers above.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

USER app
EXPOSE 8000

# Python's stdlib does the request, since slim images don't include curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

# Hosts like Render set $PORT; locally it defaults to 8000. --proxy-headers makes the
# app see the original https scheme and client IP behind the host's load balancer.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port $PORT --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
