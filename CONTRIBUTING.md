# Contributing to SH-PSCBS

Documentation fixes, reproducible bug reports, and small improvements are welcome.

## Start here

Try the Docker demo in [README.md](README.md#quick-start). It needs no model API keys. Read the [architecture](docs/architecture.md) and [usage guide](docs/usage.md). Check existing issues before opening one. Discuss larger changes in an issue first.

## Development setup

Use Python 3.11 or later and Git. Fork the repository and clone your fork. From its root:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

See the usage guide for optional PostgreSQL and Docker integration tests. Use disposable databases and sample repositories. Report skipped tests and any services required by your change.

## Report a bug

Include the commit or version, OS, Python/Docker versions, exact command, expected result, actual result, and a minimal reproduction. For schema problems, provide a synthetic before/after schema and the smallest affected code example. Remove credentials, connection strings, private code, and customer data from logs.

## Submit a pull request

1. Create a focused branch from the latest main.
2. Keep changes small and explain the user-visible problem.
3. Add a regression test for a behavior change and run relevant tests. For documentation, check links and commands.
4. Describe what changed, why, and how you verified it. Include skipped checks and limitations.
5. Write new documentation, commit messages, and PR descriptions in English. Keep existing Portuguese identifiers and CLI commands consistent with the codebase.

Preserve human approval before committing generated repairs, explicit publishing, event history, and test isolation. Explain new dependencies and avoid unrelated refactors. Never commit .env files, API keys, generated private proposals, or real database data.

## First contributions

Look for issues labeled `good first issue`. Documentation troubleshooting, small reproducible fixtures, and focused test coverage are useful starting points. Comment with what you plan to try.

## Working together

Be respectful and give actionable feedback. Reviews are handled as maintainer time permits; there is no guaranteed response time. Contributions are provided under the repository's [MIT license](LICENSE).
