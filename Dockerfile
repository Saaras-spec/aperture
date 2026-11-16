# TODO: completed in Phase 5 (GitHub Action, Docker, CI)
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

ENTRYPOINT ["migrationguard"]
CMD ["check"]
