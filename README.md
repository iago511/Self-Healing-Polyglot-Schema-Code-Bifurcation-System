# SH-PSCBS

**Languages:** English · [Português (Brasil)](README.pt-BR.md) · [Español](README.es.md)

![SH-PSCBS demo showing a PostgreSQL column rename, the proposed SQL fix, a passing test, and an approved Git commit](docs/assets/workflow-demo.gif)

**Catch code that breaks when your PostgreSQL schema changes.** SH-PSCBS finds affected code, proposes a fix in an isolated copy, and prepares a Git commit after tests, review, and human approval.

## Quick start

Use Git and Docker Compose to clone the branch with the integrated workflow and run the demo.

```sh
git clone --branch feat/validacao-e-revisao https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System.git sh-pscbs
cd sh-pscbs
docker compose -f compose.demo.yaml up --build --exit-code-from demo --attach demo
```

The demo renames a PostgreSQL column, prints the generated SQL diff, runs pytest, and checks that the approved commit reaches a disposable local Git remote. It uses sample code and simulated model responses, so you can run it without API keys or an Ollama installation.

```sh
docker compose -f compose.demo.yaml down
```

## Monitor your own project

Copy `.env.example` to `.env`. Set `PROJECT_PATH`, `DB_URL`, and `GROQ_API_KEY`, start Ollama, and select a Docker test image that contains pytest and your project's dependencies.

```sh
python -m pip install -r requirements.txt
ollama pull qwen2.5-coder:3b
python main.py monitor --uma-vez
```

The first run records a baseline. Run `python main.py monitor` to keep watching for changes. Events and decisions are stored in SQLite. The current validation runner uses pytest.

See the [usage guide](docs/usage.md) for Docker deployment, review, and publishing, and the [architecture overview](docs/architecture.md) for the full workflow.
