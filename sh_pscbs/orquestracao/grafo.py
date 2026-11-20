"""Coordena as etapas do SH-PSCBS e grava o resultado de cada nó no SQLite."""

import os
from collections.abc import Callable
from functools import wraps
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from sh_pscbs.agentes.correcao import _validar_acoes, gerar_proposta_correcao, identificar_codigo_base
from sh_pscbs.agentes.impacto import analisar_impacto_projeto
from sh_pscbs.agentes.planejamento import planejar_correcao
from sh_pscbs.agentes.revisor import revisar_proposta
from sh_pscbs.agentes.schema import analisar_schema
from sh_pscbs.configuracao import Configuracao
from sh_pscbs.eventos.repositorio import ConflitoVersaoEvento, RegistroEvento, RepositorioEventos
from sh_pscbs.orquestracao.estado import EtapaCorrecao, EventoSchema
from sh_pscbs.validacao.executor import validar_proposta_correcao


_ROTAS = {
    EtapaCorrecao.DETECTADO: "analisar",
    EtapaCorrecao.ANALISANDO: "analisar",
    EtapaCorrecao.IMPACTO_ANALISADO: "planejar",
    EtapaCorrecao.PLANEJADO: "corrigir",
    EtapaCorrecao.PROPOSTA_GERADA: "validar",
    EtapaCorrecao.VALIDANDO: "validar",
    EtapaCorrecao.EM_REVISAO: "revisar",
}
ETAPAS_PROCESSAVEIS = tuple(_ROTAS)


class Execucao(TypedDict):
    registro: RegistroEvento


def conferir_escopo(evento: EventoSchema, config: Configuracao) -> None:
    if (evento.projeto, evento.banco, evento.nome_schema) != (config.projeto_id, config.banco_id, config.nome_schema):
        raise ValueError("O evento pertence a outro projeto, banco ou schema.")


def salvar_etapa(repo: RepositorioEventos, registro: RegistroEvento, etapa: EtapaCorrecao, **dados) -> RegistroEvento:
    estado = registro.estado.model_copy(update={"etapa_atual": etapa, **dados})
    return repo.salvar_estado(estado, versao_esperada=registro.versao, motivo=dados.get("ultimo_erro"))


def criar_grafo(config: Configuracao, repo: RepositorioEventos):
    """Compila o grafo com retomada pela etapa armazenada do evento."""
    pasta = config.pasta_projeto

    def etapa(funcao: Callable):
        @wraps(funcao)
        def executar(execucao: Execucao) -> Execucao:
            registro = execucao["registro"]
            try:
                return {"registro": funcao(registro)}
            except ConflitoVersaoEvento:
                raise
            except Exception as erro:
                detalhe = str(erro)
                for chave in (config.db_url, os.getenv("GROQ_API_KEY")):
                    if chave:
                        detalhe = detalhe.replace(chave, "[oculto]")
                return {"registro": salvar_etapa(
                    repo, registro, EtapaCorrecao.FALHOU,
                    ultimo_erro=f"{funcao.__name__}: {type(erro).__name__}: {detalhe}"[:1500],
                )}
        return executar

    @etapa
    def analisar(registro):
        evento = registro.estado.evento
        base = identificar_codigo_base(pasta)
        analise = analisar_schema(evento)
        impacto = analisar_impacto_projeto(evento, analise, pasta)
        if identificar_codigo_base(pasta) != base:
            raise ValueError("O projeto mudou durante a análise de impacto.")
        return salvar_etapa(
            repo, registro, EtapaCorrecao.IMPACTO_ANALISADO, analise_schema=analise,
            hipoteses_renomeacao=analise.hipoteses_renomeacao, relatorio_impacto=impacto,
            codigo_base_id=base, ultimo_erro=None,
        )

    @etapa
    def planejar(registro):
        estado = registro.estado
        if identificar_codigo_base(pasta) != estado.codigo_base_id:
            raise ValueError("O código mudou depois da análise de impacto.")
        decisao = planejar_correcao(
            estado.evento, estado.analise_schema, estado.relatorio_impacto, pasta,
            esclarecimentos=estado.esclarecimentos,
        )
        if identificar_codigo_base(pasta) != estado.codigo_base_id:
            raise ValueError("O código mudou durante o planejamento.")
        plano = decisao.plano
        if plano is not None:
            plano = plano.model_copy(update={"acoes": _validar_acoes(plano, pasta)})
        return salvar_etapa(
            repo, registro, EtapaCorrecao(decisao.status), plano=plano,
            justificativa_planejamento=decisao.justificativa, perguntas_planejamento=decisao.perguntas,
        )

    @etapa
    def corrigir(registro):
        estado = registro.estado
        ajustes = []
        if estado.revisao is not None:
            ajustes.extend(estado.revisao.ajustes_solicitados or [estado.revisao.justificativa])
        if estado.validacao is not None:
            ajustes.extend(
                f"{verificacao.nome}: {(verificacao.detalhe or verificacao.status)[-1200:]}"
                for verificacao in estado.validacao.verificacoes if verificacao.status == "reprovada"
            )
        if estado.decisao_humana is not None and estado.decisao_humana.comentario:
            ajustes.append(estado.decisao_humana.comentario)
        proposta = gerar_proposta_correcao(
            estado.evento, estado.plano, pasta, codigo_base_id=estado.codigo_base_id,
            pasta_propostas=config.pasta_propostas, ajustes=ajustes,
        )
        return salvar_etapa(
            repo, registro, EtapaCorrecao.PROPOSTA_GERADA, proposta=proposta, validacao=None,
            revisao=None, decisao_humana=None, tentativas=estado.tentativas + 1,
        )

    @etapa
    def validar(registro):
        validacao = validar_proposta_correcao(
            registro.estado.proposta, pasta, imagem_teste=config.imagem_teste,
            timeout_segundos=config.timeout_testes,
        )
        if validacao.hash_diff is None:
            motivo = next((v.detalhe for v in validacao.verificacoes if v.status == "reprovada"), None)
            return salvar_etapa(repo, registro, EtapaCorrecao.FALHOU, validacao=validacao, ultimo_erro=motivo)
        return salvar_etapa(repo, registro, EtapaCorrecao.EM_REVISAO, validacao=validacao)

    @etapa
    def revisar(registro):
        estado = registro.estado
        parecer = revisar_proposta(estado.evento, estado.plano, estado.proposta, estado.validacao, pasta)
        destino = EtapaCorrecao.AJUSTES_NECESSARIOS
        if parecer.parecer == "favoravel" and estado.validacao.status == "aprovada":
            destino = EtapaCorrecao.AGUARDANDO_APROVACAO
        elif (
            parecer.parecer == "ajustes_necessarios"
            and estado.validacao.status != "incompleta"
            and estado.tentativas < config.max_tentativas
        ):
            destino = EtapaCorrecao.PLANEJADO
        return salvar_etapa(repo, registro, destino, revisao=parecer)

    def proximo(execucao: Execucao):
        conferir_escopo(execucao["registro"].estado.evento, config)
        return _ROTAS.get(execucao["registro"].estado.etapa_atual, END)

    grafo = StateGraph(Execucao)
    for no in (analisar, planejar, corrigir, validar, revisar):
        grafo.add_node(no.__name__, no)
    grafo.add_conditional_edges(START, proximo, [*dict.fromkeys(_ROTAS.values()), END])
    grafo.add_conditional_edges("analisar", proximo, ["planejar", END])
    grafo.add_conditional_edges("planejar", proximo, ["corrigir", END])
    grafo.add_conditional_edges("corrigir", proximo, ["validar", END])
    grafo.add_conditional_edges("validar", proximo, ["revisar", END])
    grafo.add_conditional_edges("revisar", proximo, ["corrigir", END])
    return grafo.compile()
