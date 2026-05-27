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
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        zstd \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-unifont \
        fonts-ubuntu \
        libasound2t64 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
        xdg-utils \
    && curl -fsSL https://ollama.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
