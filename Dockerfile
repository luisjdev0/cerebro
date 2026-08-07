# KnowledgeOS API - multi-stage image (plan_v2.md SS10 "Despliegue").
#
# Stage 1 (builder) installs the project (editable, so `knowledgeos.db.REPO_ROOT` -
# computed as two parents up from src/knowledgeos/db.py - keeps resolving to /app in
# the runtime image too, matching how the same code runs from source in dev) into an
# isolated venv, then pre-downloads the embeddings model AT BUILD TIME so the first
# real container start doesn't pay that cost (plan_v2.md SS10: "arranque rapido").
#
# Stage 2 (runtime) copies only the venv + app source + pre-warmed model cache into a
# fresh slim image, and drops to a non-root user before serving.
#
# Build-time knobs (must match EMBEDDING_MODEL/EMBEDDING_DIMENSION in .env - see
# .env.example - or the pre-downloaded model won't be the one the app actually loads
# at runtime, and it will silently re-download instead):
#   docker build --build-arg EMBEDDING_MODEL=... --build-arg EMBEDDING_DIMENSION=... .

# ------------------------------------------------------------------------- builder
FROM python:3.13-slim AS builder

# libgomp1: onnxruntime (fastembed's backend) needs OpenMP at runtime, not just here -
# reinstalled in the runtime stage below too.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
COPY src ./src
COPY db ./db

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ARG EMBEDDING_DIMENSION=384
# fastembed ignores HF_HOME for its own model snapshots - it keys off
# FASTEMBED_CACHE_PATH (defaulting to a $TMPDIR path that would NOT survive the COPY
# into the runtime stage below). Pin it to a stable, absolute path instead.
ENV FASTEMBED_CACHE_PATH=/opt/model-cache
RUN python -c "from knowledgeos.embeddings import build_embedding_provider; build_embedding_provider('${EMBEDDING_MODEL}', ${EMBEDDING_DIMENSION})"

# ------------------------------------------------------------------------- runtime
FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 knowledgeos \
    && useradd --uid 1000 --gid knowledgeos --create-home --shell /usr/sbin/nologin knowledgeos

ENV PATH="/opt/venv/bin:${PATH}" \
    FASTEMBED_CACHE_PATH=/opt/model-cache \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/model-cache /opt/model-cache
COPY --from=builder /app /app

RUN chown -R knowledgeos:knowledgeos /app /opt/model-cache
USER knowledgeos

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "knowledgeos.main"]
