# syntax=docker/dockerfile:1.7
#
# The hosted tradefloor trading session server.
#
#   docker build -f deploy/Dockerfile -t tradefloor-hosted .        (from the repo root)
#
# Two stages. BUILD compiles the Rust engine into a wheel with maturin. RUNTIME
# is python:3.11-slim with that wheel and the server's few dependencies: no
# compiler, no Rust toolchain, no source tree, no goldens (the ignore file next
# to this Dockerfile keeps the 135 MB parity corpus out of the build context).
#
# No RUSTFLAGS here, on purpose: rust/Cargo.toml forbids target-cpu=native and
# anything else that could change float results between CPUs. The image is
# built for ONE architecture per deployment (linux/arm64 for Graviton in the
# AWS path); sessions are resumed on the architecture that wrote them.

ARG PYTHON_VERSION=3.11
ARG RUST_TOOLCHAIN=1.98.1

# ---------------------------------------------------------------- build ----
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
ARG RUST_TOOLCHAIN
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain ${RUST_TOOLCHAIN}
RUN pip install --no-cache-dir "maturin>=1.5,<2.0"

WORKDIR /src
# Manifests first, so a Python-only change does not refetch every crate.
COPY rust/Cargo.toml rust/Cargo.lock rust/
COPY rust/README.md rust/LICENSE-APACHE rust/LICENSE-MIT rust/NOTICE rust/
RUN mkdir -p rust/src && echo "" > rust/src/lib.rs \
 && cd rust && cargo fetch --locked && rm -rf src
COPY pyproject.toml README.md LICENSE-APACHE LICENSE-MIT NOTICE ./
COPY rust/src rust/src
COPY python python
RUN --mount=type=cache,target=/src/rust/target \
    maturin build --release --locked --out /wheels

# -------------------------------------------------------------- runtime ----
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    TRADEFLOOR_HOSTED_DATA=/data
COPY deploy/requirements-hosted.txt /tmp/requirements-hosted.txt
COPY --from=build /wheels /tmp/wheels
RUN pip install --no-cache-dir -r /tmp/requirements-hosted.txt /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels /tmp/requirements-hosted.txt \
 && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin tradefloor \
 && mkdir -p /data && chown tradefloor /data
USER 10001
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["python", "-m", "tradefloor.serve.hosted"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
