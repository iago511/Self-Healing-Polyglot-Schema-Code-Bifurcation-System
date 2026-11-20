FROM docker:27-cli AS docker_cli
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 agent && mkdir /data && chown agent:agent /data

COPY main.py ./
COPY sh_pscbs/ ./sh_pscbs/
COPY tools/ ./tools/

USER agent

ENV PROJECT_PATH=/workspace EVENTOS_DB_PATH=/data/eventos.db PROPOSTAS_DIR=/data/propostas DOCKER_TEST_STORAGE=volume

ENTRYPOINT ["python", "main.py"]
CMD ["monitor"]

FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER agent

FROM test AS demo
COPY testes/test_integracao_grafo.py ./testes/
ENTRYPOINT ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "testes/test_integracao_grafo.py"]
CMD []

FROM runtime AS production
