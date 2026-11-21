const vscode = acquireVsCodeApi();
const root = document.getElementById("events");
let latest = [];

function el(tag, cls, text) {
  const element = document.createElement(tag);
  if (cls) element.className = cls;
  if (text !== undefined) element.textContent = text;
  return element;
}

function send(type, details = {}) { vscode.postMessage({ type, ...details }); }

function button(label, action, disabled = false, cls = "btn") {
  const item = el("button", cls, label);
  item.disabled = disabled;
  item.addEventListener("click", () => send(action.type, action));
  return item;
}

function stageName(stage) {
  return ({
    detectado: "Detected", analisando: "Analyzing", impacto_analisado: "Impact analyzed",
    planejado: "Plan ready", proposta_gerada: "Proposal ready", validando: "Validating",
    em_revisao: "Reviewing", aguardando_aprovacao: "Ready for review",
    precisa_esclarecimento: "Needs input", ajustes_necessarios: "Changes requested",
    falhou: "Needs attention", aplicado: "Committed", rejeitado: "Rejected",
    sem_alteracoes: "No code changes",
  })[stage] || String(stage || "").replaceAll("_", " ");
}

function render(data) {
  latest = data.events || [];
  document.getElementById("dot").classList.toggle("on", Boolean(data.running));
  document.getElementById("status").textContent = data.running ? "Monitoring PostgreSQL" : "Monitor is off";
  document.getElementById("toggle").textContent = data.running ? "Stop monitoring" : "Start monitoring";
  document.getElementById("project").textContent = (data.configured?.project || "Open a project folder to get started")
    + " · PostgreSQL " + (data.configured?.database ? "connection saved" : "not configured")
    + " · Groq key " + (data.configured?.groq ? "saved" : "not configured");
  document.getElementById("total").textContent = String(latest.filter((item) => !["aplicado", "rejeitado", "sem_alteracoes"].includes(item.summary.stage)).length);
  document.getElementById("reviews").textContent = String(latest.filter((item) => item.summary.stage === "aguardando_aprovacao").length);
  document.getElementById("failures").textContent = String(latest.filter((item) => item.summary.stage === "falhou").length);
  root.replaceChildren();
  if (!latest.length) {
    const empty = el("div", "empty");
    empty.append(el("strong", "", "No schema events yet"), el("span", "", data.running
      ? "SH-PSCBS is watching for changes."
      : "Set up the connection, then scan once or start monitoring."));
    root.append(empty);
    return;
  }

  const sorted = [...latest].sort((a, b) => {
    const rank = (event) => event.summary.stage === "aguardando_aprovacao" ? 0 : event.summary.stage === "falhou" ? 1 : 2;
    return rank(a) - rank(b);
  });
  for (const item of sorted) renderEvent(item);
}

function renderEvent(item) {
  const summary = item.summary;
  const state = item.detail || {};
  const event = state.evento || {};
  const card = el("article", "event");
  const head = el("div", "event-head");
  const stage = el("span", "stage", stageName(summary.stage));
  if (summary.stage === "aguardando_aprovacao") stage.classList.add("ready");
  if (summary.stage === "falhou") stage.classList.add("error");
  head.append(el("h3", "", summary.id.slice(0, 12)), stage);
  card.append(head);
  card.append(el("div", "event-meta", `${event.banco || "PostgreSQL"} · ${event.nome_schema || "public"}`));

  for (const change of (event.mudancas || []).slice(0, 4)) {
    const row = el("div", "change");
    row.append(el("b", "", String(change.tipo || "change").replaceAll("_", " ")),
      el("span", "", `${change.tabela || ""}${change.coluna ? `.${change.coluna}` : ""}`));
    card.append(row);
  }
  if ((event.mudancas || []).length > 4) card.append(el("div", "event-meta", `+${event.mudancas.length - 4} more changes`));

  const details = el("details", "detail");
  const evidenceCount = state.relatorio_impacto?.evidencias?.length || 0;
  details.append(el("summary", "", `Impact · ${evidenceCount} code references`));
  const validation = state.validacao;
  if (validation) details.append(el("p", "", `Tests: ${stageName(validation.status)}`));
  if (state.revisao?.justificativa) details.append(el("p", "", state.revisao.justificativa));
  const explanation = state.justificativa_planejamento || state.ultimo_erro;
  if (explanation) details.append(el("p", "", explanation));

  for (const file of state.proposta?.arquivos || []) {
    const row = el("div", "file");
    row.append(el("span", "", file));
    const preview = el("button", "link", "Review diff");
    preview.addEventListener("click", () => send("diff", { id: summary.id, file }));
    row.append(preview);
    details.append(row);
  }
  card.append(details);

  const actions = el("div", "event-actions");
  actions.append(button("Open review", { type: "event", id: summary.id }));
  if (summary.stage === "aguardando_aprovacao") {
    const ready = validation?.status === "aprovada" && state.revisao?.parecer === "favoravel";
    actions.append(button("✓ Approve", { type: "approve", id: summary.id }, !ready, "btn primary"));
    actions.append(button("✕ Reject", { type: "reject", id: summary.id }));
    actions.append(button("Request changes", { type: "adjust", id: summary.id }));
  }
  if (summary.stage === "falhou") actions.append(button("Retry", { type: "retry", id: summary.id }, false, "btn primary"));
  if (summary.stage === "precisa_esclarecimento") actions.append(button("Answer", { type: "clarify", id: summary.id }, false, "btn primary"));
  if (summary.stage === "ajustes_necessarios" && state.proposta) actions.append(button("Request changes", { type: "adjust", id: summary.id }, false, "btn primary"));
  if (summary.stage === "aplicado") actions.append(button("Publish branch", { type: "publish", id: summary.id }, false, "btn primary"));
  card.append(actions);
  root.append(card);
}

document.addEventListener("click", (event) => {
  const item = event.target.closest("[data-action]");
  if (item) send(item.dataset.action);
});

window.addEventListener("message", (event) => {
  if (event.data.type === "data") render(event.data);
  if (event.data.type === "status") render({ ...event.data, events: latest });
  if (event.data.type === "selectEvent") {
    const card = [...root.querySelectorAll(".event")].find((candidate) => candidate.querySelector("h3")?.textContent === event.data.id.slice(0, 12));
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});

send("refresh");
