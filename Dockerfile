FROM python:3.12-slim

# ffmpeg/ffprobe для видео + зависимости Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Браузер для Playwright + его системные зависимости
RUN playwright install --with-deps chromium

COPY . .

# Папки под данные и логи
RUN mkdir -p data logs

# Healthcheck: процесс python жив
HEALTHCHECK --interval=2m --timeout=15s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "bot.py"]
