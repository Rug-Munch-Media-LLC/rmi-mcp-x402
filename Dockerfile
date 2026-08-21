# ─────────────────────────────────────────────────────────────
# Coffer — self-hosted x402 payment facilitator (deployable image)
# Slim Python 3.12, non-root, installs the `coffer` package (app/ + coffer/
# + vendor/ per [tool.hatch.build.targets.wheel]) and serves the /v1/x402
# HTTP surface via uvicorn.
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
# Install build deps and the wheel (avoids leaving build tooling in final image).
RUN pip install --upgrade pip setuptools wheel hatchling
COPY pyproject.toml ./
COPY app ./app
COPY coffer ./coffer
COPY vendor ./vendor
# Local source CLI args copied on first build; refresh with --build-arg BUILDKIT.
RUN pip wheel --no-deps --wheel-dir /wheels .

# ── Runtime image ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    X402_HOST=0.0.0.0 X402_PORT=8001

# Run as a non-root user — no root in the serving container.
RUN groupadd --system coffer && useradd --system --gid coffer --home-dir /srv/coffer coffer \
    && mkdir -p /srv/coffer && chown -R coffer:coffer /srv/coffer

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

USER coffer
WORKDIR /srv/coffer
EXPOSE 8001

# Fail-closed wallet validation runs inside create_app(); an unset X402_WALLET
# aborts startup with a clear error instead of serving an unconfigured 402.
CMD ["uvicorn", "--factory", "app.domain.x402.config:create_app", \
     "--host", "0.0.0.0", "--port", "8001"]
