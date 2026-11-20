"""Mapeia referências no projeto a partir das mudanças no schema."""

from collections import defaultdict
from pathlib import Path

from sh_pscbs.agentes.schema import AnaliseSchema
from sh_pscbs.orquestracao.estado import EvidenciaImpacto, EventoSchema, RelatorioImpacto
from tools.tools_alteracoes import analisar_impacto


def analisar_impacto_projeto(
    evento: EventoSchema,
    analise_schema: AnaliseSchema,
    pasta: str | Path,
) -> RelatorioImpacto:
    """Busca evidências no código e distingue referências diretas de indícios."""
    tabelas_por_coluna: dict[str, set[str]] = defaultdict(set)
    tabelas = {mudanca.tabela for mudanca in evento.mudancas}
    for mudanca in evento.mudancas:
        if mudanca.coluna is not None and mudanca.tipo in {"coluna_removida", "coluna_alterada"}:
            tabelas_por_coluna[mudanca.coluna].add(mudanca.tabela)

    colunas = set(tabelas_por_coluna)
    relatorio = RelatorioImpacto(
        avisos=list(analise_schema.duvidas),
        limitacoes=[
            "A busca literal diferencia maiúsculas de minúsculas e pode omitir referências dinâmicas.",
            "Ocorrências na mesma linha ou no mesmo escopo são indícios de vínculo com uma tabela.",
        ],
    )
    busca = analisar_impacto.invoke({"pasta": str(pasta), "termos": sorted(tabelas | colunas)})
    if not busca["sucesso"]:
        relatorio.avisos.append(f"Não foi possível analisar o projeto: {busca['erro']}")
        return relatorio

    relatorio.avisos.extend(busca["avisos"])
    relatorio.observacao = busca.get("observacao")
    evidencias = [
        EvidenciaImpacto.model_validate(item)
        for item in busca["evidencias"]
    ]
    if not evidencias:
        relatorio.avisos.append("A busca não encontrou referências aos termos da mudança.")
        return relatorio

    tabelas_por_escopo: dict[tuple[str, str], set[str]] = defaultdict(set)
    for evidencia in evidencias:
        if evidencia.escopo is not None:
            tabelas_por_escopo[evidencia.arquivo, evidencia.escopo].update(
                tabelas.intersection(evidencia.termos)
            )

    vistos = set()
    for evidencia in sorted(evidencias, key=lambda item: (item.arquivo, item.linha)):
        vistos.update(evidencia.termos)
        tabelas_na_linha = tabelas.intersection(evidencia.termos)
        tabelas_no_escopo = (
            tabelas_por_escopo[evidencia.arquivo, evidencia.escopo]
            if evidencia.escopo is not None else set()
        )
        motivos = []
        for coluna in sorted(colunas.intersection(evidencia.termos)):
            # Um único termo pode ser tanto o nome da coluna como o da tabela.
            tabelas_da_coluna = tabelas_por_coluna[coluna] - {coluna}
            na_linha = tabelas_da_coluna & tabelas_na_linha
            no_escopo = tabelas_da_coluna & tabelas_no_escopo
            if na_linha:
                motivos.append(f"A mesma linha cita a coluna {coluna} e a tabela {', '.join(sorted(na_linha))}.")
            elif no_escopo:
                motivos.append(
                    f"A coluna {coluna} aparece em um escopo que também cita {', '.join(sorted(no_escopo))}."
                )
            else:
                motivos.append(f"A ocorrência de {coluna} pode ser homônima. Confirme a tabela que ela usa.")
        if tabelas_na_linha and not motivos:
            motivos.append(
                f"A linha cita {', '.join(sorted(tabelas_na_linha))}. "
                "Verifique se a leitura ou a gravação depende da alteração no banco."
            )
        evidencia.motivo = " ".join(motivos)
        relatorio.evidencias.append(evidencia)

    sem_ocorrencias = (tabelas | colunas) - vistos
    if sem_ocorrencias:
        relatorio.avisos.append(f"Sem ocorrências para: {', '.join(sorted(sem_ocorrencias))}.")
    return relatorio
