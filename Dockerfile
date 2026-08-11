# The console API, containerised for Cloud Run.
#
# A Dockerfile rather than buildpacks, deliberately. This repository is a uv
# WORKSPACE with two member packages, which is not a shape the Python buildpack
# detects: it looks for a requirements.txt or a single-project pyproject at the
# root, finds a workspace root with no dependencies of its own, and produces an
# image that installs nothing. Being explicit costs a file and removes a class of
# silent deploy failure.
#
# The lockfile is installed FROZEN. This repository sits untouched through a month
# of judging, and a resolver that picks up whatever shipped since would turn a green
# deploy red with nobody touching the code.

FROM python:3.13-slim AS base

# uv from its own distroless image rather than curl-to-shell: pinned by digest-able
# tag, no network fetch inside the build, and nothing to verify by hand.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Manifests first, so a code change does not invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/pyproject.toml
COPY agents/pyproject.toml agents/pyproject.toml

# The sources the workspace members declare. Without these the sync fails on a
# missing package rather than silently producing an empty install.
COPY core/src core/src
COPY agents/src agents/src

RUN uv sync --frozen --no-dev --package curtail-agents

# The generated fact sheet travels INSIDE the package, which is why /api/facts works
# from an image at all. That was a review finding: the endpoint used to resolve a
# repository path, and a container has no repository.

ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run injects PORT and it is not always 8080. Reading it rather than hardcoding
# is the difference between a service that starts and one that fails its health check
# for a reason nobody can see from the logs.
ENV PORT=8080
EXPOSE 8080

# Exec form, so uvicorn is PID 1 and receives SIGTERM directly. Under the shell form
# the shell is PID 1, the signal never reaches the server, and Cloud Run kills the
# instance after the grace period on every single revision change.
# --proxy-headers is not optional behind Cloud Run, and its absence was visible in
# production only. Without it uvicorn ignores X-Forwarded-Proto, so every redirect
# it generates is http://, and a browser following one to a .run.app host lands on
# a Google error page. FastAPI's own trailing-slash redirect made that reachable
# from the first request.
CMD ["sh", "-c", "exec uvicorn curtail_agents.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]
