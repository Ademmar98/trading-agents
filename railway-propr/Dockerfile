FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default = beta (walls 5%/10%, halts 3%/7%). The 5k service overrides the
# start command with its own walls. No HTTP port: this is a long-running
# worker, not a web service.
CMD ["python", "fund_bot.py", "--live"]
