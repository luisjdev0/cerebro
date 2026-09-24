# cerebro-auth API - multi-stage image, mirrors cerebro-flows's (Dockerfile).
# No model download step and no Redis: cerebro-auth is a plain Postgres-backed
# CRUD service (users/groups/tokens), no execution engine or retrieval.

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

RUN chown -R cerebro-auth:cerebro-auth /app
USER cerebro-auth

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "cerebro_auth.main"]
