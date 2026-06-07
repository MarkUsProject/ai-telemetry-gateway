# LiteLLM proxy with psycopg added for the Phase 5 telemetry adapter.
# The upstream image uses Prisma for its own DB and does not ship psycopg;
# lib/telemetry_adapter.py needs it to write usage_logs rows.

FROM ghcr.io/berriai/litellm:main-stable

# The image strips pip out of the venv. Re-seed it from Python's stdlib
# ensurepip, then install psycopg. psycopg[binary] ships a self-contained
# libpq wheel so no apk packages are needed.
RUN python3 -m ensurepip --upgrade \
 && python3 -m pip install --no-cache-dir "psycopg[binary]>=3.2"
