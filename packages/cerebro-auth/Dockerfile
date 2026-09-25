# cerebro-auth API - multi-stage image, mirrors cerebro-flows's (Dockerfile).
# No model download step and no Redis: cerebro-auth is a plain Postgres-backed
# CRUD service (users/groups/tokens), no execution engine or retrieval. The
# runtime stage does install the PostgreSQL 17 client (pg_dump) for POST
# /backup -- matched to the server's major version (pgvector/pgvector:pg17 in
# compose.yaml), since Debian bookworm's own postgresql-client package (v15)
# is older than the server and not guaranteed to dump newer catalog features
# correctly.

# ------------------------------------------------------------------------- builder
FROM python:3.13-slim AS builder

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml ./
COPY src ./src
COPY db ./db

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# ------------------------------------------------------------------------- runtime
FROM python:3.13-slim AS runtime

RUN groupadd --gid 1000 cerebro-auth \
    && useradd --uid 1000 --gid cerebro-auth --create-home --shell /usr/sbin/nologin cerebro-auth

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gnupg curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && apt-get purge -y --auto-remove gnupg curl \
    && rm -rf /var/lib/apt/lists/*

RUN chown -R cerebro-auth:cerebro-auth /app
USER cerebro-auth

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "cerebro_auth.main"]
