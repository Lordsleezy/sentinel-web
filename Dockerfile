FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV OLLAMA_ENABLED=false
ENV AI_HELPER_ENABLED=true
ENV AI_HELPER_REQUIRED=true
ENV AI_HELPER_PROVIDER=ollama
ENV AI_HELPER_MODEL=llama3.2:1b
ENV AI_HELPER_HOST=http://127.0.0.1:11434
ENV AI_HELPER_TIMEOUT_S=10
ENV HEADLESS=true

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates zstd \
    && curl -fsSL https://ollama.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
