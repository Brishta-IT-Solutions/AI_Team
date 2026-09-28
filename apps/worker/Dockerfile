# The AI team's worker: Claude Code and Codex CLIs, plus Python for the orchestration loop.
FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Pinned CLI versions the adapters were tested against (FRD: pin tested versions).
RUN npm install -g @anthropic-ai/claude-code@2.1.284 @openai/codex@0.158.0 && npm cache clean --force
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
RUN python3 -m venv /opt/venv
WORKDIR /app
COPY pyproject.toml ./
COPY aitc_worker ./aitc_worker
RUN pip install --no-cache-dir .
RUN mkdir -p /workspace /home/node/.codex && chown node:node /workspace /home/node/.codex
USER node
ENV HOME=/home/node AITC_WORK_DIR=/workspace
CMD ["aitc-worker"]
