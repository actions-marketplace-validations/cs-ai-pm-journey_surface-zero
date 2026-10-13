# Image for the GitHub Action (see action.yml). The hosted service uses ./Dockerfile; this one
# is separate on purpose so a change to the service build cannot change what tenants run.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY README.md ./

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Actions mounts the checked-out workspace at /github/workspace and sets GITHUB_* variables.
ENTRYPOINT ["python", "-m", "surface_zero.action"]
