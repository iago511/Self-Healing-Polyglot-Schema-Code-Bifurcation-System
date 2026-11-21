# VS Code extension

The extension adds a Schema Guardian dashboard to the Activity Bar. It drives the existing Python CLI, graph, SQLite event store, and approval flow; the backend remains available from the terminal.

## Run the extension from source

1. Install VS Code, Node.js 20 or later, Git, Python 3.11 or later, PostgreSQL, Ollama, and Docker.
2. From the repository root, run `npm install`.
3. Open the repository in VS Code and press `F5` to launch an Extension Development Host.
4. Open a project folder in the new window. Use the SH-PSCBS Activity Bar icon or `Ctrl+Alt+S` (`Cmd+Alt+S` on macOS).
5. Select **Set Up Runtime**. This creates an isolated Python environment in VS Code's global extension storage and installs the existing `requirements.txt` dependencies.
6. Run **Set PostgreSQL Connection** and **Set Groq API Key** from the dashboard or Command Palette. Values are stored in VS Code SecretStorage, not in the workspace `.env` file.
7. Set project and database IDs, schema, model, polling, SQLite, proposal, and test preferences in **Preferences**. Use the same optional IDs as an existing CLI setup. Install Ollama and select a test image containing your project's dependencies.
8. Select **Start monitoring** or **Scan now**. Events appear in Activity, with code lenses on changed files and native side-by-side diffs for proposals.

The extension never edits the monitored project during proposal generation. The diff viewer compares the workspace with the isolated proposal copy. Approval and rejection call the existing CLI, and approval is available only after tests pass and the reviewer returns a favorable result. The backend creates the approved commit on a separate proposal branch.

## Commands and shortcuts

| Command | Action |
| --- | --- |
| `SH-PSCBS: Open Dashboard` | Open Schema Guardian (`Ctrl+Alt+S` / `Cmd+Alt+S`). |
| `SH-PSCBS: Turn Monitor On or Off` | Start or stop background schema polling (`Ctrl+Alt+M` / `Cmd+Alt+M`). |
| `SH-PSCBS: Scan for Schema Changes Now` | Run one monitor cycle. |
| `SH-PSCBS: Review Event` | Open event details from the dashboard or select an event. |
| `SH-PSCBS: Set PostgreSQL Connection` | Store the database URI securely. |
| `SH-PSCBS: Set Groq API Key` | Store the API key securely. |
| `SH-PSCBS: Open CLI in Integrated Terminal` | Run pending-event, one-time scan, or help commands in a configured terminal. |

Event cards also support retrying failures, answering planner questions, requesting a revised proposal, and publishing an approved branch. Publishing asks for confirmation before pushing to `origin`.

Other preferences are available under **Settings → Extensions → SH-PSCBS**. The Python backend output is available in **View → Output → SH-PSCBS**.

## Build and validate

```sh
npm test
npm run package
```

`npm test` type-checks the extension and runs its platform-independent logic tests. `npm run package` builds a VSIX for local installation. Python backend tests remain available with `python -m pytest -q`.

The extension needs network access to PostgreSQL, Groq, and the configured Ollama service. Docker is used by the existing isolated validation runner. The VS Code extension does not bundle Python, Ollama, PostgreSQL, or Docker.
