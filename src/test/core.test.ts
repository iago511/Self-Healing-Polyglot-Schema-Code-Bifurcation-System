import assert from "node:assert/strict";
import test from "node:test";
import { displayStage, isApprovalReady, parsePendingEvents } from "../core";

test("parses CLI event rows, including rows without proposals", () => {
  assert.deepEqual(parsePendingEvents("evt-1 aguardando_aprovacao prop-1\nevt-2 falhou"), [
    { id: "evt-1", stage: "aguardando_aprovacao", proposalId: "prop-1" },
    { id: "evt-2", stage: "falhou", proposalId: "" },
  ]);
});

test("enables approval only after passing validation and favorable review", () => {
  const ready = { etapa_atual: "aguardando_aprovacao", proposta: { id_proposta: "p" },
    validacao: { status: "aprovada" }, revisao: { parecer: "favoravel" } };
  assert.equal(isApprovalReady(ready), true);
  assert.equal(isApprovalReady({ ...ready, validacao: { status: "incompleta" } }), false);
  assert.equal(isApprovalReady({ ...ready, revisao: { parecer: "desfavoravel" } }), false);
  assert.equal(isApprovalReady({ ...ready, etapa_atual: "falhou" }), false);
});

test("formats internal stage names for the dashboard", () => {
  assert.equal(displayStage("aguardando_aprovacao"), "Ready for your review");
});
