export interface EventSummary {
  id: string;
  stage: string;
  proposalId: string;
}

export function parsePendingEvents(output: string): EventSummary[] {
  return output.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).flatMap((line) => {
    const [id, stage, proposalId = ""] = line.split(/\s+/, 3);
    return id && stage ? [{ id, stage, proposalId }] : [];
  });
}

export function isApprovalReady(state: any): boolean {
  return state?.etapa_atual === "aguardando_aprovacao"
    && Boolean(state?.proposta?.id_proposta)
    && state?.validacao?.status === "aprovada"
    && state?.revisao?.parecer === "favoravel";
}

export function displayStage(stage: string): string {
  const names: Record<string, string> = {
    detectado: "Detected", analisando: "Analyzing impact", impacto_analisado: "Impact analyzed",
    planejado: "Plan ready", proposta_gerada: "Proposal generated", validando: "Running tests",
    em_revisao: "Under review", aguardando_aprovacao: "Ready for your review",
    precisa_esclarecimento: "Needs clarification", ajustes_necessarios: "Changes requested",
    sem_alteracoes: "No code changes needed", aprovado: "Approved", aplicado: "Committed",
    rejeitado: "Rejected", falhou: "Needs attention",
  };
  return names[stage] ?? stage.replaceAll("_", " ");
}
