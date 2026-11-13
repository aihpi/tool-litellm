FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY kiseno-slack-bot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY kiseno-slack-bot/app /app/app

# Socket Mode only opens outbound connections, so the bot listens on no port.
USER 10001:10001

CMD ["python", "-m", "app.main"]
