FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# git checks that a local pilot project's repository and branch are reachable.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY control_api ./control_api
RUN pip install .
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home app
USER app
EXPOSE 8000
# Migrate, optionally load demo data, then serve.
CMD ["sh", "-c", "alembic upgrade head && if [ \"$AITC_SEED\" = \"true\" ]; then python -m control_api.seed; fi && exec uvicorn control_api.main:app --host 0.0.0.0 --port 8000"]
