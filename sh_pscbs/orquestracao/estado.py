from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class MudancaSchema(BaseModel):
    """Uma alteração observada entre dois snapshots do banco."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal[
        "tabela_adicionada",
        "tabela_removida",
        "tabela_alterada",
        "coluna_adicionada",
        "coluna_removida",
        "coluna_alterada",
    ]
    tabela: str = Field(min_length=1)
    coluna: str | None = Field(default=None, min_length=1)
    valores_anteriores: dict[str, JsonValue] = Field(default_factory=dict)
    valores_posteriores: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validar_alvo(self) -> "MudancaSchema":
        if self.tipo.startswith("coluna_") and self.coluna is None:
            raise ValueError("Uma alteração de coluna precisa identificar a coluna.")
        if self.tipo.startswith("tabela_") and self.coluna is not None:
            raise ValueError("Uma alteração de tabela deve identificar somente a tabela.")
        return self


class HipoteseRenomeacao(BaseModel):
    """Associação sugerida entre uma coluna removida e uma coluna adicionada."""

    model_config = ConfigDict(extra="forbid")

    tabela: str = Field(min_length=1)
    coluna_anterior: str = Field(min_length=1)
    coluna_nova: str = Field(min_length=1)

    @model_validator(mode="after")
    def validar_nomes(self) -> "HipoteseRenomeacao":
        if self.coluna_anterior == self.coluna_nova:
            raise ValueError("Uma hipótese de renomeação precisa de nomes diferentes.")
        return self


class AnaliseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumo: str
    riscos: list[str] = Field(default_factory=list)
    hipoteses_renomeacao: list[HipoteseRenomeacao] = Field(default_factory=list)
    duvidas: list[str] = Field(default_factory=list)


class EventoSchema(BaseModel):
    """A mudança detectada, com a referência às duas versões comparadas."""

    model_config = ConfigDict(extra="forbid")

    id_evento: str = Field(min_length=1)
    projeto: str = Field(min_length=1, description="Identificador do projeto.")
    banco: str = Field(min_length=1, description="Identificador do banco.")
    nome_schema: str = Field(default="public", min_length=1)
    versao_snapshot_anterior: str = Field(min_length=1)
    versao_snapshot_atual: str = Field(min_length=1)
    mudancas: list[MudancaSchema] = Field(min_length=1)


class EvidenciaImpacto(BaseModel):
    """Referência encontrada no código durante a análise de impacto."""

    model_config = ConfigDict(extra="forbid")

    arquivo: str = Field(min_length=1, description="Caminho relativo ao projeto.")
    linha: int = Field(ge=1)
    linha_final: int | None = Field(default=None, ge=1)
    trecho: str = Field(min_length=1)
    termos: list[str] = Field(min_length=1)
    metodo: str = Field(min_length=1, description="Como a referência foi encontrada.")
    escopo: str | None = None
    motivo: str | None = None

    @model_validator(mode="after")
    def validar_intervalo(self) -> "EvidenciaImpacto":
        if self.linha_final is not None and self.linha_final < self.linha:
            raise ValueError("A linha final deve ser igual ou posterior à linha inicial.")
        return self


class RelatorioImpacto(BaseModel):
    """Evidências encontradas e pontos que ficaram sem confirmação."""

    model_config = ConfigDict(extra="forbid")

    evidencias: list[EvidenciaImpacto] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    limitacoes: list[str] = Field(default_factory=list)
    observacao: str | None = None


class AcaoCorrecao(BaseModel):
    """Uma alteração planejada em um arquivo."""

    model_config = ConfigDict(extra="forbid")

    arquivo: str = Field(min_length=1, description="Caminho relativo ao projeto.")
    operacao: Literal["criar", "alterar", "excluir"]
    descricao: str = Field(min_length=1)


class PlanoCorrecao(BaseModel):
    """Alterações em ordem de execução e verificações esperadas."""

    model_config = ConfigDict(extra="forbid")

    acoes: list[AcaoCorrecao] = Field(min_length=1)
    testes_previstos: list[str] = Field(default_factory=list)


class PropostaCorrecao(BaseModel):
    """Referência a uma versão específica do patch preparado para revisão."""

    model_config = ConfigDict(extra="forbid")

    id_proposta: str = Field(min_length=1, description="Identificador da versão do patch.")
    codigo_base_id: str = Field(min_length=1)
    versao_snapshot_atual: str = Field(min_length=1)
    arquivos: list[str] = Field(min_length=1)
    referencia_diff: str = Field(min_length=1)
    resumo: str = Field(min_length=1)


class VerificacaoValidacao(BaseModel):
    """Resultado individual de uma verificação ou teste."""

    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1)
    status: Literal["aprovada", "reprovada", "nao_executada", "nao_aplicavel"]
    detalhe: str | None = None


class ResultadoValidacao(BaseModel):
    """Resultados das verificações de uma versão da proposta."""

    model_config = ConfigDict(extra="forbid")

    id_proposta: str = Field(min_length=1)
    hash_diff: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    verificacoes: list[VerificacaoValidacao] = Field(default_factory=list)

    @property
    def status(self) -> Literal["aprovada", "reprovada", "incompleta"]:
        resultados = {verificacao.status for verificacao in self.verificacoes}
        if "reprovada" in resultados:
            return "reprovada"
        if "aprovada" not in resultados or "nao_executada" in resultados:
            return "incompleta"
        return "aprovada"


class ParecerRevisao(BaseModel):
    """Avaliação técnica da proposta pelo agente revisor."""

    model_config = ConfigDict(extra="forbid")

    id_proposta: str = Field(min_length=1)
    parecer: Literal["favoravel", "ajustes_necessarios", "desfavoravel"]
    justificativa: str = Field(min_length=1)
    ajustes_solicitados: list[str] = Field(default_factory=list)


class DecisaoHumana(BaseModel):
    """Decisão da pessoa responsável sobre a versão revisada da proposta."""

    model_config = ConfigDict(extra="forbid")

    id_proposta: str = Field(min_length=1)
    autor_id: str = Field(min_length=1)
    decidida_em: AwareDatetime
    decisao: Literal["aprovar", "rejeitar", "solicitar_ajustes"]
    comentario: str | None = None


class EtapaCorrecao(StrEnum):
    DETECTADO = "detectado"
    ANALISANDO = "analisando"
    IMPACTO_ANALISADO = "impacto_analisado"
    PLANEJADO = "planejado"
    PROPOSTA_GERADA = "proposta_gerada"
    VALIDANDO = "validando"
    EM_REVISAO = "em_revisao"
    AGUARDANDO_APROVACAO = "aguardando_aprovacao"
    PRECISA_ESCLARECIMENTO = "precisa_esclarecimento"
    AJUSTES_NECESSARIOS = "ajustes_necessarios"
    SEM_ALTERACOES = "sem_alteracoes"
    APROVADO = "aprovado"
    APLICADO = "aplicado"
    REJEITADO = "rejeitado"
    FALHOU = "falhou"


class EstadoCorrecao(BaseModel):
    """Dados compartilhados pelo grafo durante a correção de um evento."""

    model_config = ConfigDict(extra="forbid")

    evento: EventoSchema
    etapa_atual: EtapaCorrecao = EtapaCorrecao.DETECTADO
    hipoteses_renomeacao: list[HipoteseRenomeacao] = Field(default_factory=list)
    analise_schema: AnaliseSchema | None = None
    codigo_base_id: str | None = None
    relatorio_impacto: RelatorioImpacto | None = None
    plano: PlanoCorrecao | None = None
    proposta: PropostaCorrecao | None = None
    validacao: ResultadoValidacao | None = None
    revisao: ParecerRevisao | None = None
    decisao_humana: DecisaoHumana | None = None
    justificativa_planejamento: str | None = None
    perguntas_planejamento: list[str] = Field(default_factory=list)
    esclarecimentos: list[str] = Field(default_factory=list)
    branch_git: str | None = None
    commit_git: str | None = None
    tentativas: int = Field(default=0, ge=0)
    ultimo_erro: str | None = None

    @model_validator(mode="after")
    def validar_versao_da_proposta(self) -> "EstadoCorrecao":
        for resultado in (self.validacao, self.revisao, self.decisao_humana):
            if resultado is not None and (
                self.proposta is None or resultado.id_proposta != self.proposta.id_proposta
            ):
                raise ValueError("Validação, revisão e decisão devem se referir à proposta atual.")
        return self
