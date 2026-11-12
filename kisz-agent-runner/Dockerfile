FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY kisz-agent-runner/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY kisz-agent-runner/app /app/app

RUN useradd --uid 10001 --no-create-home runner
USER 10001

EXPOSE 8080

CMD ["uvicorn", "app.main:build", "--factory", "--host", "0.0.0.0", "--port", "8080"]
