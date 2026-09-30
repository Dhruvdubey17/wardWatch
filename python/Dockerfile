# One image definition for the Python services; SERVICE picks the package.
# Build context is the repository root, because the services read contracts/.
#   docker build -f python/Dockerfile --build-arg SERVICE=wardwatch-fhir -t wardwatch-fhir .

FROM python:3.12-slim AS build
ARG SERVICE
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /usr/local/bin/uv
WORKDIR /build/python
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY python/pyproject.toml python/uv.lock ./
COPY python/simulator ./simulator
COPY python/ml ./ml
COPY python/fhir_service ./fhir_service
COPY python/scorer ./scorer
# --no-editable installs the workspace packages into the environment itself,
# so the runtime stage needs only the .venv directory.
RUN uv sync --frozen --no-dev --no-editable --package "$SERVICE"

FROM python:3.12-slim
ARG SERVICE
RUN useradd --system --uid 10001 wardwatch
COPY --from=build /build/python/.venv /opt/wardwatch/.venv
COPY contracts /opt/wardwatch/contracts
ENV PATH=/opt/wardwatch/.venv/bin:$PATH \
    WARDWATCH_CONTRACTS_DIR=/opt/wardwatch/contracts \
    PYTHONUNBUFFERED=1
USER wardwatch
WORKDIR /opt/wardwatch
