# SH-PSCBS launch kit

Draft copy for the maintainer. These drafts do not mean posts have been published.

Repository: https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System

## Hacker News

**Title:** Show HN: PostgreSQL schema drift to reviewed Python repair proposals

**URL:** https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System

**Introductory comment:**

I built SH-PSCBS to explore a specific maintenance problem: a database schema changes, but application code still references the old columns.

It compares PostgreSQL schema snapshots, finds potentially affected code, and uses a LangGraph workflow to plan a repair, edit an isolated copy, run pytest in Docker, and review the diff. Passing tests and a favorable review allow a human to approve a commit on a separate branch. Publishing is a separate explicit command. SQLite stores event state and decisions. Groq handles planning/review; Ollama handles code generation.

The current scope is PostgreSQL and Python. The Docker Compose demo uses a disposable database and simulated model responses, so it needs no API keys. It demonstrates the workflow, not live-model accuracy.

The difficult cases include ambiguous renames, incomplete test coverage, and schema changes reverted between polls. I would appreciate concrete examples of schema changes that broke application code, and feedback on the review workflow.

## DEV article

**Title:** From PostgreSQL schema drift to a Python repair proposal with LangGraph

**Tags:** python, postgresql, opensource, langgraph

A column rename can leave application queries referring to a name that no longer exists. A migration changes the database, but the application still needs a compatible change.

SH-PSCBS is my experimental tool for connecting those two sides. It detects PostgreSQL schema differences and prepares code repair proposals for review.

### Try the workflow

```sh
git clone https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System.git sh-pscbs
cd sh-pscbs
docker compose -f compose.demo.yaml up --build --exit-code-from demo --attach demo
```

The demo renames a column, shows a SQL diff, runs pytest, and verifies an approved commit against a disposable local Git remote. Model responses are simulated, so no API key or Ollama installation is required for this example.

### Why a graph?

Each detected change becomes an event. The workflow can need clarification, a correction, another validation attempt, or a human decision. LangGraph expresses those routes, while SQLite stores snapshots, event state, and decisions.

Analysis identifies schema changes and code evidence. Planning uses Groq with read-only tools. Correction uses Ollama in an isolated project copy. Validation checks integrity and syntax and runs pytest in a network-isolated Docker container. Review compares the plan, diff, and results.

Approval creates a commit on a separate branch only after the required checks. A separate publish command pushes that branch. The destination repository still needs review and merge.

### Where it needs work

This is experimental and currently focuses on PostgreSQL and Python. The CLI uses Portuguese command names. A dropped column and an added column may indicate a rename, but that is an inference. Polling can miss short-lived changes. A green test suite only covers what the tests exercise.

Groq receives relevant code and context for planning and review; generation uses Ollama. Review that data flow before monitoring private projects.

### Help shape the tool

The repository includes an MIT license, architecture and usage guides, and a contribution guide. Feedback on ambiguous migrations, repair evaluation, and the first-run experience would help. Try the demo and report the smallest example that reproduces a problem.

[Source and demo](https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System).

## Brazilian community post

**Title:** Fiz uma ferramenta open source para propor correções no Python quando o schema PostgreSQL muda

Estou desenvolvendo o SH-PSCBS para um problema bem específico: mudar uma coluna do banco e deixar consultas antigas no código.

O fluxo detecta diferenças de schema, procura código afetado, planeja uma correção e altera uma cópia isolada. Depois roda pytest em Docker e revisa o diff. Com os testes e a revisão aprovados, uma pessoa decide se cria o commit em outra branch. Publicar essa branch exige outro comando.

Usei LangGraph, SQLite, Groq e Ollama. O foco atual é PostgreSQL e Python; ainda é experimental. Há uma demo com Docker Compose sem chave de API, usando respostas simuladas dos modelos.

Queria feedback de quem já teve código quebrado por mudanças de schema: quais casos seriam bons para avaliar essa abordagem?

Código e demo: https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System

## LinkedIn caption

A PostgreSQL column changes. Which application queries still reference the old name?

I am building SH-PSCBS to turn schema drift into a reviewable Python code repair proposal: detect the change, identify affected code, propose a diff, run tests, and ask for human approval before creating a Git commit.

Built with LangGraph, SQLite, Groq, Ollama, and Docker. The project is experimental. Its Docker demo uses simulated model responses and needs no API keys.

Try it and share a schema-change case you would want it to handle:
https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System

#PostgreSQL #Python #OpenSource #LangGraph

## X caption

PostgreSQL schema drift -> Python repair proposal -> tests -> human approval -> Git branch.

I am building SH-PSCBS with LangGraph. Experimental; Docker demo needs no API keys (simulated models).

https://github.com/iago511/Self-Healing-Polyglot-Schema-Code-Bifurcation-System

## 30-second video storyboard

Record the actual demo with readable terminal text and captions. Do not present simulated responses as a live-model benchmark.

| Time | Screen | Caption / narration |
| --- | --- | --- |
| 0-4s | Old query beside renamed database column | A database rename can break application code. |
| 4-10s | Demo schema-change event | SH-PSCBS detects the schema difference. |
| 10-17s | Generated diff | It prepares a repair in an isolated copy. |
| 17-23s | pytest result and review | Tests and review come before approval. |
| 23-27s | Approved commit on separate branch | A person approves the Git commit. |
| 27-30s | Repository URL and demo command | Try the open source Docker demo. |

Keep a visible caption: “Workflow demo · simulated model responses”. Record a clean sample repository with no credentials or private data.

## Distribution and follow-up

- Check each community's current rules and required flair before posting. Adapt the post to its audience.
- Publish once per suitable community; avoid repeated submissions, bulk tagging, or requests to coordinate votes.
- Use creator mentions only when there is a direct connection to their work.
- Answer technical questions with reproducible commands, limitations, and fixes.
- Track the public post URL, date, referral traffic in GitHub Insights, demo problems, and actionable feedback. Stars are an outcome, not a guaranteed target.
- Do not claim users, production adoption, benchmarks, or reliability measurements that have not been demonstrated.

## Awesome list submission

Submitted to Awesome LangGraph: https://github.com/vonzosten/awesome-LangGraph/pull/151 (awaiting maintainer review).

Awesome Postgres requires production use and reasonably GA quality. Submit there only after the project can provide that evidence.
