FROM python:3.12-slim

WORKDIR /app

# Build dependencies for packages that compile native code
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HEADLESS=true
ENV PYTHONUNBUFFERED=1

# Railway injects these via its dashboard; defaults shown for local dev:
#   BROKER_TYPE       paper|dxtrade|binance|mt5  (mt5 requires Windows host)
#   TRADING_DATA_DIR  /app/data                   (mount a Railway Volume here)
#   MT5_LOGIN
#   MT5_PASSWORD
#   MT5_SERVER
#   DXTRADE_API_URL
#   DXTRADE_USERNAME
#   DXTRADE_PASSWORD
#   DXTRADE_DOMAIN
#   BINANCE_API_KEY
#   BINANCE_API_SECRET
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#   TRADING_INTERVAL_MINUTES  60
#   WATCHED_SYMBOLS           BTC/USD,ETH/USD,SOL/USD,...

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8000').strip(); sys.exit(0) if p=='0' else urllib.request.urlopen('http://localhost:'+p+'/health')"

CMD ["python", "prod_run.py"]
