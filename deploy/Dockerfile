# syntax=docker/dockerfile:1.7
# R6 Tactical Intelligence Engine -- server image (CPU only).
# Build context is the repo root (see docker-compose.yml); Dockerfile.dockerignore
# is an allow-list, so credentials and local data can't end up in the image.

# ---- r6-dissect: Linux build of the Go replay parser -------------------------
FROM golang:1.23-bookworm AS dissect
WORKDIR /src
COPY r6-dissect/go.mod r6-dissect/go.sum ./
RUN go mod download
COPY r6-dissect/ ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags "-s -w" -o /out/r6-dissect .

# ---- Python dependencies ------------------------------------------------------
FROM python:3.12-slim-bookworm AS pydeps
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --upgrade pip wheel setuptools
# CPU-only torch: the default wheel drags in ~3 GB of CUDA libraries.
RUN pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
COPY deploy/requirements-container.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
# --no-deps: whisper would pull in triton (GPU-only) and resemblyzer the
# Python-2 "typing" backport; their real dependencies are in requirements.txt.
RUN pip install --no-deps openai-whisper==20250625 Resemblyzer==0.1.4

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin r6

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    NUMBA_CACHE_DIR=/tmp/numba \
    R6_SERVER_DATA_DIR=/data \
    R6_SERVER_HOST=0.0.0.0 \
    R6_SERVER_PORT=8000

COPY --from=pydeps /opt/venv /opt/venv
WORKDIR /app
COPY server/ server/
COPY app/ app/
COPY analysis/ analysis/
COPY database/ database/
COPY integration/*.py integration/
COPY models/ models/
COPY server_main.py .
COPY --from=dissect /out/r6-dissect integration/bin/r6-dissect

# /data is the only writable place (a volume); the rest of the filesystem is
# read-only at runtime.
RUN mkdir /data && chown r6:r6 /data
USER r6
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
CMD ["python", "-u", "server_main.py"]
