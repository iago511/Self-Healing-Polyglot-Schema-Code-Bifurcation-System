FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 agent && mkdir /data && chown agent:agent /data

COPY main.py prompts.py snapshot.py tools.py watcher.py ./

USER agent

ENV PROJECT_PATH=/workspace SNAPSHOTS_DIR=/data/schema_snapshots

CMD ["python", "main.py", "--watch-only"]