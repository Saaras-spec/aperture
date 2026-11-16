FROM python:3.12-slim

# Create a non-root user and group
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -m -s /bin/bash appuser

WORKDIR /app

# Copy dependency files and source code
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY entrypoint.sh /app/entrypoint.sh

# Install migrationguard package and ensure entrypoint script is executable
RUN chmod +x /app/entrypoint.sh && \
    pip install --no-cache-dir .

# Switch to non-root user
USER appuser

ENTRYPOINT ["migrationguard"]
CMD ["--help"]
