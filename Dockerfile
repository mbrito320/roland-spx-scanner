FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY market_data.py .
COPY ticket_formatter.py .
COPY ai_engine.py .
COPY bot.py .

# Railway sets PORT env var but our bot doesn't need a web server
# We just run the Telegram polling bot
CMD ["python", "bot.py"]
