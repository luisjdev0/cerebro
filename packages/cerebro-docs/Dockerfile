# cerebro-docs API - multi-stage image, mirrors cerebro-memory's (Dockerfile)
# but without the embeddings model download step: cerebro-docs doesn't do
# semantic retrieval, only simple full-text (see ecosistema-cerebro.md SS2/SS7).
#
# Stage 1 (builder) installs the project (editable, so
# `cerebro_docs.db.PACKAGE_ROOT` - computed two levels up from
# src/cerebro_docs/db.py - keeps resolving to /app in the runtime image too)
# into an isolated venv.
#
# Stage 2 (runtime) copies only the venv + source + migrations into a clean
# image, and drops to a non-root user before serving.

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

RUN groupadd --gid 1000 cerebro-docs \
    && useradd --uid 1000 --gid cerebro-docs --create-home --shell /usr/sbin/nologin cerebro-docs

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN chown -R cerebro-docs:cerebro-docs /app
USER cerebro-docs

EXPOSE 8010

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8010/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "cerebro_docs.main"]
