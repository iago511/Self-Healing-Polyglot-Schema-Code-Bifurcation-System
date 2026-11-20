"""Interface do monitor e das decisões sobre propostas do SH-PSCBS."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from psycopg2 import Error as ErroPostgreSQL

from sh_pscbs.configuracao import Configuracao, resolver_pasta_projeto
from sh_pscbs.eventos.repositorio import ConflitoVersaoEvento, RepositorioEventos
from sh_pscbs.monitoramento.snapshot import identificar_schema, ler_schema_detalhado
from sh_pscbs.monitoramento.watcher import escopo, processar_evento, rodar_watcher
from sh_pscbs.orquestracao.estado import DecisaoHumana, EstadoCorrecao, EtapaCorrecao
from sh_pscbs.orquestracao.grafo import conferir_escopo, criar_grafo, salvar_etapa
from sh_pscbs.validacao.executor import carregar_artefatos_proposta
from tools.tools_alteracoes import copiar_projeto_isolado
from tools.tools_git import CommitProposta, GitProjeto


def _parser():
    parser = argparse.ArgumentParser(description="Monitora mudanças no schema PostgreSQL e propõe correções.")
    parser.add_argument("--watch-only", action="store_true", help="Executa o monitor (compatibilidade).")
    comandos = parser.add_subparsers(dest="comando")
    monitor = comandos.add_parser("monitor", help="Observa o banco e executa o grafo.")
    monitor.add_argument("--uma-vez", action="store_true", help="Executa apenas um ciclo.")
    pendentes = comandos.add_parser("pendentes", help="Lista eventos em aberto.")
    pendentes.add_argument("--falhas", action="store_true", help="Lista eventos que falharam.")
    comandos.add_parser("git-status", help="Mostra o estado do checkout original.")
    comandos.add_parser("copiar", help="Copia o projeto para uma pasta nova.").add_argument("destino", type=Path)

    for nome, ajuda in {
        "evento": "Exibe o estado e o histórico de um evento.",
        "esclarecer": "Responde às perguntas do planejamento e retoma o evento.",
        "reprocessar": "Retoma um evento com falha ou que precisa de ajustes.",
        "aprovar": "Aprova a proposta e cria seu commit em uma branch separada.",
        "rejeitar": "Registra a rejeição da proposta.",
        "ajustar": "Solicita ajustes e retoma o grafo.",
        "publicar": "Envia ao remoto o commit de uma proposta aprovada.",
    }.items():
        comando = comandos.add_parser(nome, help=ajuda)
        comando.add_argument("id_evento")
        if nome in {"aprovar", "rejeitar", "ajustar"}:
            comando.add_argument("--proposta", required=True, help="ID da versão revisada.")
            comando.add_argument("--autor", required=True, help="Identificador da pessoa responsável.")
            comando.add_argument("--motivo", required=nome == "ajustar")
        elif nome == "esclarecer":
            comando.add_argument("--resposta", required=True)
        elif nome == "publicar":
            comando.add_argument("--remoto", default="origin")
    return parser


def _conferir_schema(config, evento):
    atual = ler_schema_detalhado(config.db_url, config.nome_schema)
    if identificar_schema(atual) != evento.versao_snapshot_atual:
        raise ValueError("O schema mudou desde este evento. Consulte o evento mais recente.")


def _decidir(config, repo, registro, args):
    estado = registro.estado
    proposta = estado.proposta
    etapas = {EtapaCorrecao.AGUARDANDO_APROVACAO}
    if args.comando == "aprovar":
        etapas.update((EtapaCorrecao.APROVADO, EtapaCorrecao.APLICADO))
    else:
        etapas.add(EtapaCorrecao.AJUSTES_NECESSARIOS)
    if estado.etapa_atual not in etapas or proposta is None or proposta.id_proposta != args.proposta:
        raise ValueError("Essa versão da proposta não está disponível para a decisão solicitada.")
    if not args.autor.strip() or (args.comando == "ajustar" and not args.motivo.strip()):
        raise ValueError("Informe o autor e, ao solicitar ajustes, descreva o motivo.")
    if estado.etapa_atual == EtapaCorrecao.APLICADO:
        return registro

    decisao = DecisaoHumana(
        id_proposta=proposta.id_proposta, autor_id=args.autor.strip(),
        decidida_em=datetime.now(timezone.utc),
        decisao="solicitar_ajustes" if args.comando == "ajustar" else args.comando,
        comentario=args.motivo,
    )
    if args.comando == "rejeitar":
        return salvar_etapa(repo, registro, EtapaCorrecao.REJEITADO, decisao_humana=decisao)

    _conferir_schema(config, estado.evento)
    if args.comando == "ajustar":
        registro = salvar_etapa(repo, registro, EtapaCorrecao.PLANEJADO, decisao_humana=decisao, tentativas=0)
        return processar_evento(config, criar_grafo(config, repo), registro)

    if estado.validacao is None or estado.validacao.status != "aprovada" or (
        estado.revisao is None or estado.revisao.parecer != "favoravel"
    ):
        raise ValueError("A aprovação exige testes aprovados e parecer favorável.")
    diff = Path(proposta.referencia_diff).resolve()
    if not diff.is_relative_to(config.pasta_propostas):
        raise ValueError("O diff está fora de PROPOSTAS_DIR.")
    artefatos = carregar_artefatos_proposta(proposta, config.pasta_projeto)
    if (
        artefatos.id_evento != estado.evento.id_evento or artefatos.hash_diff != estado.validacao.hash_diff
        or artefatos.plano != estado.plano or proposta.versao_snapshot_atual != estado.evento.versao_snapshot_atual
    ):
        raise ValueError("Os artefatos não correspondem ao evento e à validação registrados.")

    git = GitProjeto(config.pasta_projeto)
    if estado.etapa_atual == EtapaCorrecao.AGUARDANDO_APROVACAO:
        registro = salvar_etapa(repo, registro, EtapaCorrecao.APROVADO, decisao_humana=decisao)
    commit = git.criar_commit_proposta(proposta.id_proposta, artefatos, codigo_base_id=proposta.codigo_base_id)
    return salvar_etapa(repo, registro, EtapaCorrecao.APLICADO, branch_git=commit.branch, commit_git=commit.commit)


def _retomar(config, repo, registro, args):
    estado = registro.estado
    _conferir_schema(config, estado.evento)
    if args.comando == "esclarecer":
        if estado.etapa_atual != EtapaCorrecao.PRECISA_ESCLARECIMENTO or not args.resposta.strip():
            raise ValueError("O evento precisa estar aguardando esclarecimentos e a resposta não pode estar vazia.")
        novo = EstadoCorrecao(evento=estado.evento, esclarecimentos=[*estado.esclarecimentos, args.resposta.strip()])
    elif estado.etapa_atual == EtapaCorrecao.FALHOU:
        novo = EstadoCorrecao(evento=estado.evento, esclarecimentos=estado.esclarecimentos)
    elif (
        estado.etapa_atual == EtapaCorrecao.AJUSTES_NECESSARIOS
        and estado.validacao is not None and estado.validacao.status == "incompleta"
    ):
        novo = estado.model_copy(update={
            "etapa_atual": EtapaCorrecao.PROPOSTA_GERADA, "validacao": None, "revisao": None, "ultimo_erro": None,
        })
    else:
        raise ValueError("O evento não está disponível para reprocessamento. Use ajustar para informar novas orientações.")
    registro = repo.salvar_estado(novo, versao_esperada=registro.versao)
    return processar_evento(config, criar_grafo(config, repo), registro)


def _executar(config, args):
    repo = RepositorioEventos(config.eventos_db)
    if args.comando in {None, "monitor"}:
        resultados = rodar_watcher(config, repo, criar_grafo(config, repo), uma_vez=getattr(args, "uma_vez", False))
        return int(any(r.estado.etapa_atual == EtapaCorrecao.FALHOU for r in resultados or ()))
    if args.comando == "pendentes":
        etapas = (EtapaCorrecao.FALHOU,) if args.falhas else None
        for registro in repo.listar_pendentes(**escopo(config), etapas=etapas):
            estado = registro.estado
            proposta = estado.proposta.id_proposta if estado.proposta else ""
            print(f"{estado.evento.id_evento} {estado.etapa_atual.value} {proposta}".rstrip())
        return 0

    registro = repo.obter_evento(args.id_evento)
    if registro is None:
        raise ValueError("Evento não encontrado.")
    conferir_escopo(registro.estado.evento, config)
    if args.comando == "evento":
        print(json.dumps({
            "estado": registro.estado.model_dump(mode="json"),
            "historico": [asdict(item) for item in repo.listar_historico(args.id_evento)],
        }, ensure_ascii=False, indent=2, default=_serializar_historico))
        return 0
    if args.comando == "publicar":
        estado = registro.estado
        if estado.etapa_atual != EtapaCorrecao.APLICADO or not (estado.branch_git and estado.commit_git):
            raise ValueError("O evento ainda não tem um commit aprovado para publicação.")
        _conferir_schema(config, estado.evento)
        GitProjeto(config.pasta_projeto).publicar(CommitProposta(estado.branch_git, estado.commit_git), args.remoto)
        print(f"Branch publicada {estado.branch_git} ({estado.commit_git}).")
        return 0

    handler = _retomar if args.comando in {"esclarecer", "reprocessar"} else _decidir
    estado = handler(config, repo, registro, args).estado
    print(f"{estado.evento.id_evento} {estado.etapa_atual.value}")
    if estado.commit_git:
        print(f"{estado.branch_git} {estado.commit_git}")
    return int(estado.etapa_atual == EtapaCorrecao.FALHOU)


def _serializar_historico(valor):
    if isinstance(valor, datetime):
        return valor.isoformat()
    return valor.model_dump(mode="json")


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.watch_only and args.comando not in {None, "monitor"}:
        parser.error("--watch-only só pode ser combinado com monitor.")
    load_dotenv(Path(__file__).resolve().with_name(".env"), encoding="utf-8-sig")
    try:
        if args.comando == "copiar":
            print(copiar_projeto_isolado(resolver_pasta_projeto(), args.destino))
            return 0
        if args.comando == "git-status":
            print(GitProjeto(resolver_pasta_projeto()).status() or "Checkout limpo.")
            return 0
        return _executar(Configuracao.do_ambiente(), args)
    except KeyboardInterrupt:
        return 130
    except ErroPostgreSQL as erro:
        print(f"Falha ao consultar PostgreSQL ({type(erro).__name__}).", file=sys.stderr)
    except (OSError, ValueError, RuntimeError, ConflitoVersaoEvento) as erro:
        print(erro, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
