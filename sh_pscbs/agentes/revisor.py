"""Levanta riscos de regressão e emite um parecer técnico sobre a proposta."""

import json
from pathlib import Path
from typing import Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langchain_core.language_models import BaseChatModel

from sh_pscbs.agentes.correcao import _validar_acoes
from sh_pscbs.agentes.prompts import REVISAO_PROMPT
from sh_pscbs.modelos.llms import criar_modelos_groq, fallback_do_agente
from sh_pscbs.orquestracao.estado import (
    EventoSchema,
    ParecerRevisao,
    PlanoCorrecao,
    PropostaCorrecao,
    ResultadoValidacao,
)
from sh_pscbs.validacao.executor import ArtefatosProposta, carregar_artefatos_proposta


def _pagina(linhas: list[str], linha_inicial: int, quantidade: int) -> dict:
    if linha_inicial < 1 or not 1 <= quantidade <= 80:
        return {"erro": "Use uma linha inicial positiva e quantidade de 1 a 80."}
    if linha_inicial > len(linhas):
        return {"erro": "A linha inicial está além do fim do conteúdo."}

    trecho = []
    tamanho = 0
    for numero, linha in enumerate(linhas[linha_inicial - 1:linha_inicial + quantidade - 1], start=linha_inicial):
        linha_numerada = f"{numero}: {linha}"
        if tamanho + len(linha_numerada) + 1 > 12_000:
            if not trecho:
                return {"trecho": linha_numerada[:12_000], "linhas_completas": 0, "proxima_linha": linha_inicial,
                        "aviso": "A linha excede o limite de leitura."}
            break
        trecho.append(linha_numerada)
        tamanho += len(linha_numerada) + 1
    proxima = linha_inicial + len(trecho)
    return {"trecho": "\n".join(trecho), "linhas_completas": len(trecho),
            "proxima_linha": proxima if proxima <= len(linhas) else None}


def _ferramentas_revisao(artefatos: ArtefatosProposta, linhas_diff: list[str], linhas_enviadas: set[int]):
    arquivos = {arquivo.acao.arquivo: arquivo for arquivo in artefatos.arquivos}

    @tool
    def ler_diff_revisao(linha_inicial: int = 1, quantidade: int = 60) -> dict:
        """Lê um trecho numerado do diff. Use proxima_linha para continuar."""
        pagina = _pagina(linhas_diff, linha_inicial, quantidade)
        linhas_enviadas.update(range(linha_inicial, linha_inicial + pagina.get("linhas_completas", 0)))
        return pagina

    @tool
    def consultar_arquivo_revisao(
        arquivo: str,
        versao: Literal["original", "proposta"],
        linha_inicial: int = 1,
        quantidade: int = 40,
    ) -> dict:
        """Lê trechos da versão original ou proposta de um arquivo presente no plano."""
        item = arquivos.get(arquivo)
        if item is None:
            return {"erro": "O arquivo não está na lista de arquivos alterados."}
        conteudo = item.antes if versao == "original" else item.depois
        if conteudo is None:
            return {"erro": "O arquivo não existe nesta versão."}
        return {"arquivo": arquivo, "versao": versao,
                **_pagina(conteudo.decode("utf-8").splitlines(), linha_inicial, quantidade)}

    return [ler_diff_revisao, consultar_arquivo_revisao]


def revisar_proposta(
    evento: EventoSchema,
    plano: PlanoCorrecao,
    proposta: PropostaCorrecao,
    validacao: ResultadoValidacao,
    pasta_projeto: str | Path,
    *,
    modelo: BaseChatModel | None = None,
) -> ParecerRevisao:
    """Investiga a proposta e devolve um parecer vinculado à versão revisada."""
    if validacao.id_proposta != proposta.id_proposta:
        raise ValueError("A validação pertence a outra versão da proposta.")
    if proposta.versao_snapshot_atual != evento.versao_snapshot_atual:
        raise ValueError("O evento e a proposta têm versões de schema diferentes.")
    original = Path(pasta_projeto).expanduser().resolve(strict=True)
    artefatos = carregar_artefatos_proposta(proposta, original)
    if artefatos.id_evento != evento.id_evento:
        raise ValueError("O manifesto pertence a outro evento.")
    if validacao.hash_diff != artefatos.hash_diff:
        raise ValueError("A validação não corresponde ao conteúdo atual do diff.")
    plano_normalizado = plano.model_copy(update={"acoes": _validar_acoes(plano, original)})
    if artefatos.plano != plano_normalizado:
        raise ValueError("O plano recebido não corresponde ao plano da proposta.")

    linhas_diff = artefatos.diff.splitlines()
    previa = _pagina(linhas_diff, 1, 60)
    linhas_enviadas = set(range(1, previa["linhas_completas"] + 1))

    middleware = []
    if modelo is None:
        modelo, reserva = criar_modelos_groq()
        middleware = [fallback_do_agente(reserva)]
    agente = create_agent(
        model=modelo,
        tools=_ferramentas_revisao(artefatos, linhas_diff, linhas_enviadas),
        system_prompt=REVISAO_PROMPT,
        response_format=ToolStrategy(ParecerRevisao),
        middleware=middleware,
    )
    contexto = {
        "evento": evento.model_dump(mode="json"),
        "plano": plano_normalizado.model_dump(mode="json"),
        "proposta": proposta.model_dump(mode="json", exclude={"referencia_diff"}),
        "validacao": validacao.model_dump(mode="json") | {"status": validacao.status},
        "previa_diff": previa,
    }
    resposta = agente.invoke(
        {"messages": [{"role": "user", "content": json.dumps(contexto, ensure_ascii=False)}]},
        config={"recursion_limit": 24},
    )
    parecer = resposta.get("structured_response")
    if not isinstance(parecer, ParecerRevisao) or not parecer.justificativa.strip():
        raise RuntimeError("O agente encerrou sem produzir um parecer válido.")
    if parecer.id_proposta != proposta.id_proposta:
        raise ValueError("O agente devolveu um parecer para outra proposta.")

    pendencias = []
    if validacao.status != "aprovada":
        pendencias.extend(
            f"Resolver a verificação {verificacao.nome!r} ({verificacao.status})."
            for verificacao in validacao.verificacoes
            if verificacao.status in {"reprovada", "nao_executada"}
        )
        if not pendencias:
            pendencias.append("Obter uma validação concluída da proposta.")
    if len(linhas_enviadas) != len(linhas_diff):
        pendencias.append("Revisar as partes do diff que ainda não foram consultadas.")

    if parecer.parecer == "favoravel" and parecer.ajustes_solicitados:
        pendencias.extend(parecer.ajustes_solicitados)
    if parecer.parecer == "favoravel" and pendencias:
        return ParecerRevisao(
            id_proposta=proposta.id_proposta,
            parecer="ajustes_necessarios",
            justificativa="A proposta ainda tem pendências. " + parecer.justificativa,
            ajustes_solicitados=list(dict.fromkeys(pendencias)),
        )
    return parecer
