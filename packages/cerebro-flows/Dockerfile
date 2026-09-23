# cerebro-flows API - imagen multi-stage, espejo de la de cerebro-docs (Dockerfile).
# Sin paso de descarga de modelo: cerebro-flows no hace retrieval, solo el motor de
# ejecucion (Postgres + Redis).

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

RUN groupadd --gid 1000 cerebro-flows \
    && useradd --uid 1000 --gid cerebro-flows --create-home --shell /usr/sbin/nologin cerebro-flows

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN chown -R cerebro-flows:cerebro-flows /app
USER cerebro-flows

EXPOSE 8020

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8020/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "cerebro_flows.main"]
