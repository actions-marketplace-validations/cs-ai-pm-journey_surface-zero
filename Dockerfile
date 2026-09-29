FROM python:3.14-slim

# uv is a static binary, copied straight from its own image rather than pip-installed into
# this one, keeps the build simple and avoids needing a second package manager just to get uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY data ./data
COPY README.md ./

# --frozen: fail rather than silently drift from uv.lock. --no-dev: pytest and friends have
# no reason to be in the image that actually runs.
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["python", "-m", "surface_zero.main"]
