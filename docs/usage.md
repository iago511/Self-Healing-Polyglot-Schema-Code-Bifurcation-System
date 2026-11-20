# Usage guide

## Try the demo

Run the [README quick start](../README.md#quick-start) with Docker Compose. The demo uses a disposable PostgreSQL instance, a synthetic project, pytest inside the demo container, and a local Git remote. It simulates Groq and Ollama responses and needs no API credentials or Docker socket.

## Monitor a project locally

You need Python 3.11 or later, Git, Docker, PostgreSQL, and Ollama. Use a read-only PostgreSQL account with visibility into the schema and columns you want to monitor.

1. Copy `.env.example` to `.env` and set `PROJECT_PATH`, `DB_URL`, and `GROQ_API_KEY`.
2. Set `PG_SCHEMA` if you monitor a schema other than `public`. Set `OLLAMA_BASE_URL` if Ollama runs elsewhere.
3. Set `IMAGEM_TESTE` to a local Docker image containing Python, pytest, and your project's dependencies. The executor runs tests without network access.

```sh
python -m pip install -r requirements.txt
ollama pull qwen2.5-coder:3b
python main.py monitor --uma-vez
python main.py monitor
```

The first cycle records a baseline. Later cycles record schema changes and advance open events. By default, SQLite and proposal artifacts live in the user's data directory. To put them elsewhere, set `EVENTOS_DB_PATH` and `PROPOSTAS_DIR` to locations outside the monitored project.

## Review and publish a proposal

The CLI and its argument names currently use Portuguese.

```sh
python main.py pendentes
python main.py pendentes --falhas
python main.py evento EVENT_ID
python main.py aprovar EVENT_ID --proposta PROPOSAL_ID --autor YOUR_NAME
python main.py publicar EVENT_ID --remoto origin
```

`evento` shows the analysis, diff path, test results, technical review, and history. Approval requires passing tests, a favorable review, and a clean Git repository with an author identity. It creates a commit on `sh-pscbs/PROPOSAL_ID` while preserving the original checkout. `publicar` pushes that branch. Review and merge it in the destination repository.

```sh
python main.py esclarecer EVENT_ID --resposta "The migration renamed the column."
python main.py reprocessar EVENT_ID
python main.py ajustar EVENT_ID --proposta PROPOSAL_ID --autor YOUR_NAME --motivo "Preserve the API response."
python main.py rejeitar EVENT_ID --proposta PROPOSAL_ID --autor YOUR_NAME
python main.py git-status
python main.py copiar /path/to/a/new-directory
```

Use `reprocessar` after configuring a test image or fixing an infrastructure error. Use `ajustar` to give the correction agent specific feedback. The copy command excludes Git metadata, `.env` files, symbolic links, virtual environments, and caches.

## Deploy with Docker Compose

Set `TARGET_PROJECT_PATH` to your repository's path on the host. If PostgreSQL or Ollama runs on the host, use `host.docker.internal` in its URL and make the service reachable from the container.

```sh
docker compose up --build -d monitor
docker compose run --rm monitor pendentes
```

Compose mounts the project read-only by default and uses the `eventos` volume for SQLite and proposals. To run validation through a trusted Docker daemon, set `DOCKER_SOCKET_GID` to the group with access to the socket and use the override below. Mounting the socket grants the monitor control over that daemon.

```sh
docker compose -f compose.yaml -f compose.testes.yaml up --build -d monitor
docker compose -f compose.yaml -f compose.testes.yaml run --rm monitor reprocessar EVENT_ID
```

To approve through Compose, set `TARGET_PROJECT_READ_ONLY=false`, give the container user write access to `.git`, and configure Git author and committer identities. Publishing also requires credentials for the remote.

## Run the test suite

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

To test with a disposable PostgreSQL server and a real Docker executor, build the test image with `docker build --target test -t sh-pscbs:pytest .`. Point `SH_TEST_POSTGRES_DSN` to the disposable database, set `SH_TEST_DOCKER_IMAGE=sh-pscbs:pytest`, and run `python -m pytest -q testes/test_integracao_grafo.py`. Set `DOCKER_TEST_STORAGE=volume` to exercise the storage mode used by Compose.
