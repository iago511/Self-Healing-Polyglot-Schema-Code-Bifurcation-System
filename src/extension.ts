import * as cp from "node:child_process";
import { randomBytes } from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { displayStage, isApprovalReady, type EventSummary } from "./core";

type DetailedEvent = { estado: any; historico?: any[] };
const DB_SECRET = "shPscbs.databaseUrl";
const GROQ_SECRET = "shPscbs.groqApiKey";

class Dashboard implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  constructor(private readonly extension: Extension) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = dashboardHtml(view.webview, this.extension.extensionPath);
    view.webview.onDidReceiveMessage((message) => void this.receive(message));
    void this.extension.refresh();
    this.sendStatus();
  }

  post(message: unknown): void { this.view?.webview.postMessage(message); }
  sendStatus(): void { this.post({ type: "status", running: this.extension.running, configured: this.extension.configured }); }

  private async receive(message: any): Promise<void> {
    try {
      switch (message?.type) {
        case "refresh": await this.extension.refresh(); break;
        case "toggle": await this.extension.toggleMonitor(); break;
        case "once": await this.extension.runOnce(); break;
        case "setup": await this.extension.setupRuntime(); break;
        case "settings": await this.extension.openSettings(); break;
        case "database": await this.extension.setDatabase(); break;
        case "groq": await this.extension.setGroqKey(); break;
        case "terminal": await this.extension.openTerminal(); break;
        case "event": await this.extension.showEvent(message.id); break;
        case "diff": await this.extension.openProposalDiff(message.id, message.file); break;
        case "approve": await this.extension.decide(message.id, "aprovar"); break;
        case "reject": await this.extension.decide(message.id, "rejeitar"); break;
        case "retry": await this.extension.reprocess(message.id); break;
        case "clarify": await this.extension.clarify(message.id); break;
        case "adjust": await this.extension.requestAdjustments(message.id); break;
        case "publish": await this.extension.publish(message.id); break;
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(`SH-PSCBS: ${detail}`);
    }
  }
}

class Extension implements vscode.Disposable {
  readonly output = vscode.window.createOutputChannel("SH-PSCBS");
  readonly dashboard = new Dashboard(this);
  private readonly context: vscode.ExtensionContext;
  private monitor?: cp.ChildProcessWithoutNullStreams;
  private hasDatabase = false;
  private hasGroq = false;
  private summaries: EventSummary[] = [];
  private details = new Map<string, DetailedEvent>();
  private readonly codeLensChanged = new vscode.EventEmitter<void>();
  readonly onCodeLensChanged = this.codeLensChanged.event;

  constructor(context: vscode.ExtensionContext) { this.context = context; }
  get running(): boolean { return Boolean(this.monitor && this.monitor.exitCode === null); }
  get configured(): { project: string; database: boolean; groq: boolean } {
    return { project: this.projectPath, database: this.hasDatabase, groq: this.hasGroq };
  }
  get extensionPath(): string { return this.context.extensionPath; }
  get projectPath(): string {
    const configured = this.setting<string>("projectPath").trim();
    const first = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
    return configured || first ? path.resolve(configured || first) : "";
  }
  get proposalsRoot(): string {
    const configured = this.setting<string>("proposalsDirectory").trim();
    if (configured) return path.resolve(configured);
    const database = this.setting<string>("eventsDatabasePath").trim();
    if (database) return path.join(path.dirname(path.resolve(database)), "propostas");
    const base = process.platform === "win32"
      ? (process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"))
      : process.platform === "darwin" ? path.join(os.homedir(), "Library", "Application Support")
        : (process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"));
    return path.join(base, "SH-PSCBS", "propostas");
  }
  private setting<T>(name: string): T { return vscode.workspace.getConfiguration("shPscbs").get<T>(name)!; }

  async refresh(): Promise<void> {
    await this.loadCredentialState();
    if (!(await this.ensurePrerequisites(false))) { this.sendData(); return; }
    try {
      const rows = JSON.parse(await this.cli(["pendentes", "--todos", "--json"])) as Array<{ id: string; stage: string; proposalId: string; detail: unknown }>;
      this.summaries = rows.map((row) => ({ id: row.id, stage: row.stage, proposalId: row.proposalId }));
      this.details.clear();
      for (const row of rows) this.details.set(row.id, { estado: row.detail });
      this.output.appendLine(`Refreshed ${this.summaries.length} open event(s).`);
    } catch (error) {
      this.output.appendLine(`Refresh failed: ${String(error)}`);
      vscode.window.showWarningMessage(`SH-PSCBS could not refresh events: ${error instanceof Error ? error.message : String(error)}`);
    }
    this.sendData();
  }

  async toggleMonitor(): Promise<void> {
    if (this.running) { this.stopMonitor(); return; }
    if (!(await this.ensurePrerequisites(true))) return;
    const child = await this.startProcess(["monitor"]);
    this.monitor = child;
    this.output.appendLine("Schema monitor started.");
    child.stdout.on("data", (chunk: Buffer) => this.output.append(chunk.toString()));
    child.stderr.on("data", (chunk: Buffer) => this.output.append(chunk.toString()));
    child.on("error", (error) => this.output.appendLine(`Monitor error: ${error.message}`));
    child.on("close", (code) => {
      if (this.monitor === child) this.monitor = undefined;
      this.output.appendLine(`Schema monitor stopped (exit ${code ?? "unknown"}).`);
      this.dashboard.sendStatus();
      void this.refresh();
    });
    this.dashboard.sendStatus();
  }

  private stopMonitor(): void {
    if (!this.monitor) return;
    this.monitor.kill();
    this.output.appendLine("Stopping schema monitor…");
  }

  async runOnce(): Promise<void> {
    if (!(await this.ensurePrerequisites(true))) return;
    vscode.window.setStatusBarMessage("SH-PSCBS: scanning PostgreSQL schema…", 5000);
    const result = await this.cli(["monitor", "--uma-vez"], 10 * 60_000);
    this.output.append(result);
    this.output.show(true);
    await this.refresh();
    vscode.window.showInformationMessage("SH-PSCBS schema scan completed. See the dashboard for events.");
  }

  async setupRuntime(): Promise<void> {
    const configured = this.setting<string>("pythonPath").trim();
    const basePython = configured || (process.platform === "win32" ? "py" : "python3");
    const venv = path.join(this.context.globalStorageUri.fsPath, "runtime");
    const python = pythonInVenv(venv);
    await fs.promises.mkdir(path.dirname(venv), { recursive: true });
    const create = await this.runExternal(basePython, ["-m", "venv", venv], this.extensionPath, {}, 120_000);
    this.output.appendLine(create);
    const requirements = path.join(this.extensionPath, "requirements.txt");
    if (!fs.existsSync(requirements)) throw new Error("The extension package is missing requirements.txt.");
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Installing SH-PSCBS Python dependencies…", cancellable: false }, async () => {
      const install = await this.runExternal(python, ["-m", "pip", "install", "--disable-pip-version-check", "-r", requirements], this.extensionPath, {}, 15 * 60_000);
      this.output.appendLine(install);
    });
    await vscode.workspace.getConfiguration("shPscbs").update("pythonPath", python, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage("SH-PSCBS Python runtime is ready.");
    await this.refresh();
  }

  async setDatabase(): Promise<void> {
    const value = await vscode.window.showInputBox({ title: "PostgreSQL connection", prompt: "Connection URI, stored in VS Code SecretStorage", password: true, ignoreFocusOut: true,
      placeHolder: "postgresql://user:password@host:5432/database", value: await this.context.secrets.get(DB_SECRET) ?? "" });
    if (value === undefined) return;
    if (!value.trim().startsWith("postgresql://") && !value.trim().startsWith("postgres://")) throw new Error("Enter a PostgreSQL connection URI.");
    await this.context.secrets.store(DB_SECRET, value.trim());
    await this.loadCredentialState();
    this.output.appendLine("PostgreSQL connection saved to VS Code SecretStorage.");
    this.dashboard.sendStatus();
  }

  async setGroqKey(): Promise<void> {
    const value = await vscode.window.showInputBox({ title: "Groq API key", prompt: "Stored in VS Code SecretStorage", password: true, ignoreFocusOut: true,
      value: await this.context.secrets.get(GROQ_SECRET) ?? "" });
    if (value === undefined) return;
    if (!value.trim()) throw new Error("The API key cannot be empty.");
    await this.context.secrets.store(GROQ_SECRET, value.trim());
    await this.loadCredentialState();
    this.output.appendLine("Groq key saved to VS Code SecretStorage.");
    this.dashboard.sendStatus();
  }

  async testGroqConnection(): Promise<void> {
    if (!(await this.context.secrets.get(GROQ_SECRET))) {
      vscode.window.showWarningMessage("No Groq API key is stored. Set it first, then run this test again.");
      return;
    }
    const script = [
      "import sys",
      "from sh_pscbs.modelos.llms import criar_modelos_groq",
      "try:",
      "    principal, _ = criar_modelos_groq()",
      "    principal.invoke('Reply with only OK.')",
      "    print('GROQ_MODEL_CALL_OK')",
      "except Exception as error:",
      "    response = getattr(error, 'response', None)",
      "    status = getattr(response, 'status_code', None)",
      "    print(type(error).__name__ + '; HTTP=' + str(status if status is not None else 'none'))",
      "    sys.exit(1)",
    ].join("\n");
    try {
      const result = await this.runExternal(this.pythonExecutable(), ["-c", script], this.extensionPath, await this.environment(), 45_000);
      if (!result.includes("GROQ_MODEL_CALL_OK")) throw new Error("Groq did not confirm a model response.");
      this.output.appendLine("Groq diagnostic: stored key authenticated and the configured specialist model returned a response through the Python SDK.");
      vscode.window.showInformationMessage("Groq works: the stored API key and configured specialist model returned a response.");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      const failure = detail.match(/([A-Za-z]+Error); HTTP=(none|[0-9]+)/)?.[0];
      const runtimeFailure = detail.match(/(ModuleNotFoundError|ImportError|SyntaxError|FileNotFoundError)/)?.[0];
      const safeDetail = failure ?? runtimeFailure ?? "unknown runtime error";
      if (/AuthenticationError|HTTP=401|HTTP 401/.test(detail)) {
        this.output.appendLine("Groq diagnostic: the Python SDK received HTTP 401; the stored key was rejected.");
        vscode.window.showErrorMessage("Groq rejected the stored API key (HTTP 401). Check whether it is active and not revoked.");
      } else if (/RateLimitError|HTTP=429|HTTP 429/.test(detail)) {
        this.output.appendLine("Groq diagnostic: the Python SDK received HTTP 429; check quota and rate limits.");
        vscode.window.showWarningMessage("Groq authenticated but rate limited the model request (HTTP 429). Check quota and rate limits.");
      } else if (/BadRequestError|HTTP=400|HTTP 400|model_not_found/.test(detail)) {
        this.output.appendLine("Groq diagnostic: the model request was rejected; check the configured model and request options.");
        vscode.window.showErrorMessage("Groq received the request but rejected the configured model or request options. Check the SH-PSCBS model settings.");
      } else if (/APIConnectionError|ConnectError|Timeout|HTTP=none/.test(detail)) {
        this.output.appendLine("Groq diagnostic: the Python SDK received no HTTP response; check Python's network, DNS, TLS, or proxy path.");
        vscode.window.showErrorMessage("The Python SDK could not get an HTTP response from Groq. This points to network/TLS/proxy access, not a rejected API key.");
      } else {
        this.output.appendLine(`Groq diagnostic: model probe failed (${safeDetail}); inspect configured runtime and model settings.`);
        vscode.window.showErrorMessage(`Groq model test failed (${safeDetail}). See SH-PSCBS Output for details.`);
      }
    }
  }

  async clearCredentials(): Promise<void> {
    await this.context.secrets.delete(DB_SECRET);
    await this.context.secrets.delete(GROQ_SECRET);
    await this.loadCredentialState();
    vscode.window.showInformationMessage("SH-PSCBS saved credentials cleared.");
    this.dashboard.sendStatus();
  }

  async openSettings(): Promise<void> { await vscode.commands.executeCommand("workbench.action.openSettings", "@ext:iago511.sh-pscbs-vscode"); }

  async openTerminal(): Promise<void> {
    const options = [
      { label: "$(eye) Review pending events", args: ["pendentes"] },
      { label: "$(beaker) Scan once", args: ["monitor", "--uma-vez"] },
      { label: "$(question) Show CLI help", args: ["--help"] },
    ];
    const picked = await vscode.window.showQuickPick(options, { title: "SH-PSCBS CLI", placeHolder: "Choose a command to run in the integrated terminal" });
    if (!picked) return;
    if (!(await this.ensurePrerequisites(true))) return;
    const terminal = vscode.window.createTerminal({
      name: "SH-PSCBS CLI", cwd: this.extensionPath, env: await this.environment(),
      ...(process.platform === "win32" ? { shellPath: path.join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe") } : {}),
    });
    terminal.show();
    const python = this.pythonExecutable();
    const command = `${process.platform === "win32" ? "& " : ""}${quoteForTerminal(python)} ${quoteForTerminal(path.join(this.extensionPath, "main.py"))} ${picked.args.map(quoteForTerminal).join(" ")}`;
    terminal.sendText(command);
  }

  async showEvent(id?: string): Promise<void> {
    await this.refresh();
    const selected = id || (await vscode.window.showQuickPick(this.summaries.map((item) => {
      const detail = this.details.get(item.id)?.estado;
      return { label: displayStage(item.stage), description: detail?.evento?.mudancas?.map((change: any) => `${change.tabela}.${change.coluna ?? "*"}`).join(", "), id: item.id };
    }), { title: "Choose a schema event" }))?.id;
    const detail = selected ? this.details.get(selected) : undefined;
    if (detail) await this.extensionDashboardEvent(selected!, detail);
  }

  private async extensionDashboardEvent(id: string, detail: DetailedEvent): Promise<void> {
    await vscode.commands.executeCommand("workbench.view.extension.shPscbs");
    this.dashboard.post({ type: "selectEvent", id });
  }

  private async chooseEvent(): Promise<string | undefined> {
    if (!this.summaries.length) await this.refresh();
    const item = await vscode.window.showQuickPick(this.summaries.map((summary) => ({
      label: displayStage(summary.stage), description: summary.id, id: summary.id,
    })), { title: "Choose a SH-PSCBS event" });
    return item?.id;
  }

  async decide(id: string | undefined, decision: "aprovar" | "rejeitar"): Promise<void> {
    await this.refresh();
    const eventId = id || await this.chooseEvent();
    if (!eventId) return;
    const detail = this.details.get(eventId)?.estado;
    if (!detail?.proposta || detail.etapa_atual !== "aguardando_aprovacao") throw new Error("This event is not waiting for a human proposal decision.");
    if (decision === "aprovar" && !isApprovalReady(detail)) throw new Error("Approval requires passing tests and a favorable review.");
    const configuredName = this.setting<string>("reviewerName").trim();
    const author = configuredName || await vscode.window.showInputBox({ title: decision === "aprovar" ? "Approve proposal" : "Reject proposal", prompt: "Name recorded in the proposal history", value: os.userInfo().username, ignoreFocusOut: true });
    if (!author?.trim()) return;
    const label = decision === "aprovar" ? "Create approved Git commit" : "Reject this proposal";
    const confirm = await vscode.window.showWarningMessage(`${label}? This decision will be recorded for event ${eventId}.`, { modal: true }, decision === "aprovar" ? "Approve" : "Reject");
    if (!confirm) return;
    const args = [decision, eventId, "--proposta", detail.proposta.id_proposta, "--autor", author.trim()];
    const output = await this.cli(args, 10 * 60_000);
    this.output.append(output);
    await this.refresh();
    vscode.window.showInformationMessage(decision === "aprovar" ? "Proposal approved and committed on its isolated Git branch." : "Proposal rejection recorded.");
  }

  async reprocess(id?: string): Promise<void> {
    const eventId = id || await this.chooseEvent();
    if (!eventId) return;
    const confirm = await vscode.window.showWarningMessage(`Retry processing event ${eventId}?`, { modal: true }, "Retry");
    if (!confirm) return;
    this.output.append(await this.cli(["reprocessar", eventId], 10 * 60_000));
    await this.refresh();
  }

  async clarify(id?: string): Promise<void> {
    const eventId = id || await this.chooseEvent();
    if (!eventId) return;
    const answer = await vscode.window.showInputBox({ title: "Clarify the schema change", prompt: "The planner needs more context to prepare a safe proposal.", ignoreFocusOut: true });
    if (!answer?.trim()) return;
    this.output.append(await this.cli(["esclarecer", eventId, "--resposta", answer.trim()], 10 * 60_000));
    await this.refresh();
  }

  async requestAdjustments(id?: string): Promise<void> {
    const eventId = id || await this.chooseEvent();
    if (!eventId) return;
    const state = (this.details.get(eventId) ?? await this.loadEvent(eventId)).estado;
    const proposal = state?.proposta?.id_proposta;
    if (!proposal) throw new Error("This event has no proposal to revise.");
    const reason = await vscode.window.showInputBox({ title: "Request proposal changes", prompt: "Describe what the correction agent should change.", ignoreFocusOut: true });
    if (!reason?.trim()) return;
    const author = this.setting<string>("reviewerName").trim() || os.userInfo().username;
    this.output.append(await this.cli(["ajustar", eventId, "--proposta", proposal, "--autor", author, "--motivo", reason.trim()], 10 * 60_000));
    await this.refresh();
  }

  async publish(id?: string): Promise<void> {
    const eventId = id || await this.chooseEvent();
    if (!eventId) return;
    const state = (this.details.get(eventId) ?? await this.loadEvent(eventId)).estado;
    if (state?.etapa_atual !== "aplicado") throw new Error("Only an approved and committed proposal can be published.");
    const confirm = await vscode.window.showWarningMessage(`Push ${state.branch_git} to origin?`, { modal: true }, "Publish branch");
    if (!confirm) return;
    this.output.append(await this.cli(["publicar", eventId, "--remoto", "origin"], 10 * 60_000));
    vscode.window.showInformationMessage(`Published ${state.branch_git}.`);
    await this.refresh();
  }

  async openProposalDiff(eventId: string, relativeFile?: string): Promise<void> {
    const detail = this.details.get(eventId) ?? (await this.loadEvent(eventId));
    const state = detail.estado;
    const proposal = state?.proposta;
    if (!proposal) throw new Error("This event has no code proposal to preview.");
    const diffPath = path.resolve(proposal.referencia_diff);
    const expected = path.join(this.proposalsRoot, proposal.id_proposta);
    if (path.basename(diffPath) !== "mudancas.diff" || path.dirname(diffPath) !== expected || !fs.existsSync(diffPath)) {
      throw new Error("The proposal diff is missing or outside the configured proposal directory.");
    }
    const relative = relativeFile || proposal.arquivos[0];
    if (!relative || path.isAbsolute(relative) || relative.split(/[\\/]/).includes("..")) throw new Error("Invalid proposal file path.");
    const originalRoot = path.resolve(this.projectPath);
    const candidateRoot = path.join(expected, "projeto");
    const original = path.resolve(originalRoot, relative);
    const candidate = path.resolve(candidateRoot, relative);
    if (!isWithin(originalRoot, original) || !isWithin(candidateRoot, candidate)) throw new Error("Proposal file path escapes its project folder.");
    const [before, after] = await Promise.all([readTextOrEmpty(original), readTextOrEmpty(candidate)]);
    const originalUri = vscode.Uri.from({ scheme: "shpscbs-original", path: `/${relative}`, query: `event=${encodeURIComponent(eventId)}` });
    const candidateUri = vscode.Uri.from({ scheme: "shpscbs-candidate", path: `/${relative}`, query: `event=${encodeURIComponent(eventId)}` });
    contentDocuments.set(originalUri.toString(), before);
    contentDocuments.set(candidateUri.toString(), after);
    const title = `${path.basename(relative)} · ${eventId.slice(0, 8)} · SH-PSCBS proposal`;
    await vscode.commands.executeCommand("vscode.diff", originalUri, candidateUri, title, { preview: true });
  }

  getCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const result: vscode.CodeLens[] = [];
    const file = path.relative(this.projectPath, document.uri.fsPath).split(path.sep).join("/");
    for (const [id, detail] of this.details) {
      const state = detail.estado;
      if (state?.etapa_atual !== "aguardando_aprovacao" || !state?.proposta?.arquivos?.includes(file)) continue;
      const evidence = state.relatorio_impacto?.evidencias?.find((item: any) => item.arquivo === file);
      const line = Math.max(0, Math.min(document.lineCount - 1, Number(evidence?.linha ?? 1) - 1));
      const range = new vscode.Range(line, 0, line, 0);
      result.push(new vscode.CodeLens(range, { title: `$(diff) Review SH-PSCBS proposal`, command: "shPscbs.showEvent", arguments: [id] }));
      if (isApprovalReady(state)) result.push(new vscode.CodeLens(range, { title: `$(check) Approve`, command: "shPscbs.approveEvent", arguments: [id] }));
      if (state?.proposta) result.push(new vscode.CodeLens(range, { title: `$(close) Reject`, command: "shPscbs.rejectEvent", arguments: [id] }));
    }
    return result;
  }

  async loadEvent(id: string): Promise<DetailedEvent> {
    const detail = JSON.parse(await this.cli(["evento", id])) as DetailedEvent;
    this.details.set(id, detail);
    return detail;
  }

  private async ensurePrerequisites(prompt: boolean): Promise<boolean> {
    await this.loadCredentialState();
    const python = this.pythonExecutable();
    if (!fs.existsSync(path.join(this.extensionPath, "main.py"))) throw new Error("The SH-PSCBS Python backend is not included in this extension package.");
    try {
      await this.runExternal(python, ["-c", "import langgraph, psycopg2, pydantic, dotenv"], this.extensionPath, {}, 15_000);
    } catch {
      if (!prompt) return false;
      const choice = await vscode.window.showWarningMessage("SH-PSCBS Python dependencies are missing. Set up its isolated runtime now?", "Set Up Runtime", "Later");
      if (choice === "Set Up Runtime") await this.setupRuntime();
      return false;
    }
    if (!this.projectPath || !fs.existsSync(this.projectPath)) throw new Error("Open a project folder or configure shPscbs.projectPath.");
    if (!(await this.context.secrets.get(DB_SECRET))) {
      if (prompt) await vscode.window.showWarningMessage("Set the PostgreSQL connection using SH-PSCBS: Set PostgreSQL Connection.");
      return false;
    }
    if (!(await this.context.secrets.get(GROQ_SECRET))) {
      if (prompt) await vscode.window.showWarningMessage("Set the Groq API key using SH-PSCBS: Set Groq API Key.");
      return false;
    }
    return true;
  }

  private pythonExecutable(): string {
    const configured = this.setting<string>("pythonPath").trim();
    if (configured) return configured;
    const managed = pythonInVenv(path.join(this.context.globalStorageUri.fsPath, "runtime"));
    if (fs.existsSync(managed)) return managed;
    return process.platform === "win32" ? "py" : "python3";
  }

  private async environment(): Promise<NodeJS.ProcessEnv> {
    const groqKey = await this.context.secrets.get(GROQ_SECRET);
    const dbUrl = await this.context.secrets.get(DB_SECRET);
    if (!groqKey || !dbUrl) throw new Error("Configure PostgreSQL and Groq credentials first.");
    const project = this.projectPath;
    if (!fs.existsSync(project)) throw new Error(`Project folder does not exist: ${project}`);
    const env: NodeJS.ProcessEnv = { ...process.env, PROJECT_PATH: project, DB_URL: dbUrl, GROQ_API_KEY: groqKey };
    const values: Array<[string, string]> = [
      ["PG_SCHEMA", this.setting("databaseSchema")], ["WATCH_INTERVAL_SECONDS", String(this.setting("watchIntervalSeconds"))],
      ["PROJECT_ID", this.setting("projectId")], ["BANCO_ID", this.setting("databaseId")],
      ["GROQ_MODELO_ESPECIALISTA", this.setting("groqModel")], ["GROQ_MODELO_RAPIDO", this.setting("fastGroqModel")],
      ["OLLAMA_MODELO", this.setting("ollamaModel")], ["OLLAMA_BASE_URL", this.setting("ollamaBaseUrl")],
      ["OLLAMA_TIMEOUT_SECONDS", String(this.setting("ollamaTimeoutSeconds"))],
      ["IMAGEM_TESTE", this.setting("testImage")], ["TEST_TIMEOUT_SECONDS", String(this.setting("testTimeoutSeconds"))],
      ["MAX_TENTATIVAS", String(this.setting("maxAttempts"))], ["EVENTOS_DB_PATH", this.setting("eventsDatabasePath")],
      ["PROPOSTAS_DIR", this.setting("proposalsDirectory")],
    ];
    for (const [key, value] of values) { if (value) env[key] = value; else delete env[key]; }
    return env;
  }

  private async startProcess(args: string[]): Promise<cp.ChildProcessWithoutNullStreams> {
    const child = cp.spawn(this.pythonExecutable(), [path.join(this.extensionPath, "main.py"), ...args], {
      cwd: this.extensionPath, env: await this.environment(), windowsHide: true,
    });
    return child;
  }

  private async loadCredentialState(): Promise<void> {
    this.hasDatabase = Boolean(await this.context.secrets.get(DB_SECRET));
    this.hasGroq = Boolean(await this.context.secrets.get(GROQ_SECRET));
  }

  private async cli(args: string[], timeout = 120_000): Promise<string> {
    const env = await this.environment();
    return this.runExternal(this.pythonExecutable(), [path.join(this.extensionPath, "main.py"), ...args], this.extensionPath, env, timeout);
  }

  private runExternal(command: string, args: string[], cwd: string, env: NodeJS.ProcessEnv, timeout: number): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = cp.spawn(command, args, { cwd, env: { ...process.env, ...env }, windowsHide: true });
      let output = "";
      const collect = (data: Buffer) => { output += data.toString(); if (output.length > 5_000_000) child.kill(); };
      child.stdout.on("data", collect); child.stderr.on("data", collect);
      const timer = setTimeout(() => child.kill(), timeout);
      child.on("error", (error) => { clearTimeout(timer); reject(error); });
      child.on("close", (code) => {
        clearTimeout(timer);
        if (code === 0) resolve(output);
        else reject(new Error(output.trim().slice(-4000) || `Python command exited with code ${code}.`));
      });
    });
  }

  private sendData(): void {
    const events = this.summaries.map((summary) => ({ summary, detail: this.details.get(summary.id)?.estado }));
    this.dashboard.post({ type: "data", events, running: this.running, configured: this.configured });
    this.dashboard.sendStatus();
    this.codeLensChanged.fire();
  }

  dispose(): void {
    this.monitor?.kill();
    this.output.dispose();
    this.codeLensChanged.dispose();
  }
}

const contentDocuments = new Map<string, string>();

export function activate(context: vscode.ExtensionContext): void {
  const extension = new Extension(context);
  context.subscriptions.push(extension, extension.output);
  context.subscriptions.push(vscode.window.registerWebviewViewProvider("shPscbs.dashboard", extension.dashboard, { webviewOptions: { retainContextWhenHidden: true } }));
  for (const scheme of ["shpscbs-original", "shpscbs-candidate"]) {
    context.subscriptions.push(vscode.workspace.registerTextDocumentContentProvider(scheme, {
      provideTextDocumentContent: (uri) => contentDocuments.get(uri.toString()) ?? "",
    }));
  }
  const lenses: vscode.CodeLensProvider = {
    onDidChangeCodeLenses: extension.onCodeLensChanged,
    provideCodeLenses: (document) => extension.getCodeLenses(document),
  };
  context.subscriptions.push(vscode.languages.registerCodeLensProvider({ scheme: "file" }, lenses));
  const command = (id: string, handler: (...args: any[]) => unknown) => context.subscriptions.push(vscode.commands.registerCommand(id, handler));
  command("shPscbs.openDashboard", () => vscode.commands.executeCommand("workbench.view.extension.shPscbs"));
  command("shPscbs.toggleMonitor", () => extension.toggleMonitor());
  command("shPscbs.runOnce", () => extension.runOnce());
  command("shPscbs.refresh", () => extension.refresh());
  command("shPscbs.setupRuntime", () => extension.setupRuntime());
  command("shPscbs.setDatabase", () => extension.setDatabase());
  command("shPscbs.setGroqKey", () => extension.setGroqKey());
  command("shPscbs.testGroqConnection", () => extension.testGroqConnection());
  command("shPscbs.clearCredentials", () => extension.clearCredentials());
  command("shPscbs.openSettings", () => extension.openSettings());
  command("shPscbs.openTerminal", () => extension.openTerminal());
  command("shPscbs.showEvent", (id?: string) => extension.showEvent(id));
  command("shPscbs.approveEvent", (id: string) => extension.decide(id, "aprovar"));
  command("shPscbs.rejectEvent", (id: string) => extension.decide(id, "rejeitar"));
  command("shPscbs.reprocessEvent", (id: string) => extension.reprocess(id));
  command("shPscbs.clarifyEvent", (id: string) => extension.clarify(id));
  command("shPscbs.adjustProposal", (id: string) => extension.requestAdjustments(id));
  command("shPscbs.publishEvent", (id: string) => extension.publish(id));
  void extension.refresh();
}

export function deactivate(): void { /* VS Code disposes the extension context and its child monitor. */ }

function pythonInVenv(venv: string): string {
  return process.platform === "win32" ? path.join(venv, "Scripts", "python.exe") : path.join(venv, "bin", "python");
}

function isWithin(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

async function readTextOrEmpty(file: string): Promise<string> {
  try { return await fs.promises.readFile(file, "utf8"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return ""; throw error; }
}

function quoteForTerminal(value: string): string {
  if (process.platform === "win32") return `"${value.replace(/"/g, "\\\"")}"`;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function dashboardHtml(webview: vscode.Webview, extensionPath: string): string {
  const nonce = randomBytes(24).toString("base64");
  const csp = `default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';`;
  const script = fs.readFileSync(path.join(extensionPath, "media", "dashboard.js"), "utf8");
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${csp}"><title>SH-PSCBS</title>
<style>
:root{color-scheme:light dark;--teal:#76d6c1;--line:var(--vscode-panel-border);--muted:var(--vscode-descriptionForeground);--surface:var(--vscode-sideBar-background);--good:#50c99c;--warn:#e8b568;--bad:#f17878}*{box-sizing:border-box}body{margin:0;padding:22px 18px 36px;color:var(--vscode-foreground);font:13px/1.55 var(--vscode-font-family);background:radial-gradient(ellipse at 88% 0%,color-mix(in srgb,var(--teal) 12%,transparent),transparent 44%),var(--vscode-sideBar-background)}.brand{display:flex;align-items:center;gap:11px}.mark{display:grid;place-items:center;width:37px;height:37px;border:1px solid color-mix(in srgb,var(--teal) 45%,var(--line));border-radius:12px;color:var(--teal);font-size:18px}.eyebrow{text-transform:uppercase;letter-spacing:.15em;color:var(--muted);font-size:9px}.brand h1{font-size:17px;line-height:1.2;margin:2px 0 0;letter-spacing:-.025em}.tagline{color:var(--muted);margin:14px 0 18px;max-width:320px}.hero,.card,.event{background:color-mix(in srgb,var(--surface) 88%,transparent);border:1px solid var(--line);border-radius:14px}.hero{padding:14px;margin-bottom:12px;background:linear-gradient(135deg,color-mix(in srgb,var(--teal) 11%,var(--surface)),var(--surface) 75%)}.status{display:flex;align-items:center;gap:8px;font-weight:650}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);box-shadow:0 0 0 4px color-mix(in srgb,var(--muted) 14%,transparent)}.dot.on{background:var(--good);box-shadow:0 0 0 4px color-mix(in srgb,var(--good) 16%,transparent)}.project{font-size:11px;color:var(--muted);margin:8px 0 13px;overflow-wrap:anywhere}.actions{display:grid;grid-template-columns:1fr 1fr;gap:7px}.btn{border:1px solid var(--line);background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border-radius:8px;padding:8px 9px;text-align:center;font:600 11px var(--vscode-font-family);cursor:pointer}.btn:hover{background:var(--vscode-button-secondaryHoverBackground)}.btn.primary{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border-color:transparent}.btn.primary:hover{background:var(--vscode-button-hoverBackground)}.btn:disabled{opacity:.45;cursor:not-allowed}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:13px 0}.metric{padding:10px;border:1px solid var(--line);border-radius:10px;text-align:center;background:color-mix(in srgb,var(--surface) 65%,transparent)}.metric strong{display:block;font-size:18px;line-height:1.25}.metric span{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.section{display:flex;align-items:center;justify-content:space-between;margin:21px 1px 9px}.section h2{font-size:12px;letter-spacing:.04em;margin:0}.link{color:var(--teal);background:none;border:0;padding:4px;font:600 10px var(--vscode-font-family);cursor:pointer}.event{padding:12px;margin:8px 0}.event-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.event h3{font-size:12px;margin:0;overflow-wrap:anywhere}.stage{font-size:9px;padding:3px 7px;border-radius:99px;background:color-mix(in srgb,var(--warn) 15%,transparent);color:var(--warn);white-space:nowrap}.stage.ready{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good)}.stage.error{background:color-mix(in srgb,var(--bad) 14%,transparent);color:var(--bad)}.event-meta{font-size:10px;color:var(--muted);margin:7px 0}.change{display:flex;gap:6px;font:10px var(--vscode-editor-font-family);padding:5px 0;border-top:1px solid color-mix(in srgb,var(--line) 60%,transparent)}.change b{color:var(--teal);font-weight:500}.detail{padding-top:8px;color:var(--muted);font-size:10px}.detail p{margin:5px 0}.file{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:5px 0;border-top:1px solid color-mix(in srgb,var(--line) 60%,transparent)}.empty{padding:23px 12px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:12px}.empty strong{display:block;color:var(--vscode-foreground);margin-bottom:4px}.event-actions{display:flex;gap:7px;margin-top:10px}.event-actions .btn{flex:1}.mini{font-size:10px;color:var(--muted);margin-top:16px;border-top:1px solid var(--line);padding-top:12px}.mini button{font-size:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--vscode-descriptionForeground)}
</style></head><body>
<header class="brand"><div class="mark">⌁</div><div><div class="eyebrow">Schema Guardian</div><h1>SH-PSCBS</h1></div></header>
<p class="tagline">Catch breaking database changes. Review a tested fix without leaving your editor.</p>
<section class="hero"><div class="status"><span id="dot" class="dot"></span><span id="status">Monitor is off</span></div><div id="project" class="project"></div><div class="actions"><button class="btn primary" data-action="toggle" id="toggle">Start monitoring</button><button class="btn" data-action="once">Scan now</button></div></section>
<div class="metrics"><div class="metric"><strong id="total">—</strong><span>Open events</span></div><div class="metric"><strong id="reviews">—</strong><span>For review</span></div><div class="metric"><strong id="failures">—</strong><span>Need attention</span></div></div>
<div class="section"><h2>Activity</h2><button class="link" data-action="refresh">↻ Refresh</button></div><div id="events"></div>
<div class="mini">Secure setup <button class="link" data-action="database">Database</button><button class="link" data-action="groq">Groq key</button><button class="link" data-action="settings">Preferences</button><button class="link" data-action="setup">Python runtime</button><button class="link" data-action="terminal">CLI terminal</button></div>
<script nonce="${nonce}">${script}</script></body></html>`;
}
