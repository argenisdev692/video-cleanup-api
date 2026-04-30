FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY app /app/app

EXPOSE 8080

# Default command: API service (uvicorn).
# Railway injects $PORT=8080 by default; falls back to 8080 locally.
# On Railway, the worker service overrides this with:
#   arq app.worker.WorkerSettings
# Both services share this same image and environment (REDIS_URL, R2_*, etc).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
