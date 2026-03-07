FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir "uv>=0.8,<0.9"

COPY pyproject.toml uv.lock README.md .python-version ./
COPY LICENSE ./
COPY config ./config
COPY src ./src

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["bot"]
CMD ["--help"]
