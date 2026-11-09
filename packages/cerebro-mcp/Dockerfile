# cerebro-mcp over streamable HTTP (the gateway's /mcp). Unlike the APIs, this image is built
# from the REPO ROOT as context, because it needs the sibling `cerebro-clients` package:
#   docker build -f packages/cerebro-mcp/Dockerfile .      (compose.yaml does exactly this)
# It holds no secret and no database access: it only forwards each caller's own bearer token to
# the cerebro APIs over the compose network (see cerebro_mcp/http.py).

FROM python:3.13-slim AS builder

WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY packages/cerebro-clients ./packages/cerebro-clients
COPY packages/cerebro-mcp ./packages/cerebro-mcp

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ./packages/cerebro-clients ./packages/cerebro-mcp

FROM python:3.13-slim AS runtime

RUN groupadd --gid 1000 cerebro-mcp \
    && useradd --uid 1000 --gid cerebro-mcp --create-home --shell /usr/sbin/nologin cerebro-mcp

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv
USER cerebro-mcp

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "cerebro_mcp.http"]
