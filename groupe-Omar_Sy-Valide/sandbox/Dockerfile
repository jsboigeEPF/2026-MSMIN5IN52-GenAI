# Sandbox image for SANDBOX_MODE=docker. Runs with --network none, so tools are baked in.
# Build once: docker build -t mini-opencode-sandbox sandbox/
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==9.1.1 ruff==0.17.2
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
