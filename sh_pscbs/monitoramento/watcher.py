"""Observa o schema e encaminha eventos pendentes ao grafo."""

import os
import time

from psycopg2 import Error as ErroPostgreSQL

from sh_pscbs.eventos.repositorio import ConflitoVersaoEvento
from sh_pscbs.monitoramento.snapshot import identificar_schema, ler_schema_detalhado
from sh_pscbs.orquestracao.estado import EtapaCorrecao
from sh_pscbs.orquestracao.grafo import ETAPAS_PROCESSAVEIS, salvar_etapa


def pasta_projeto():
    """Lê o caminho configurado para o projeto monitorado."""
    return os.getenv("PROJECT_PATH") or os.getenv("PASTA")


def extrair_mudancas(diff: str) -> dict:
    """Interpreta o formato de texto dos snapshots legados."""
    removidas, adicionadas, tabelas_novas = [], [], []
    tabelas = set()
    for linha in diff.splitlines():
        linha = linha.strip()
        if linha.startswith("[+] Tabela adicionada:"):
            tabelas_novas.append(linha.removeprefix("[+] Tabela adicionada:").strip())
        elif linha.startswith(("[-]", "[+]")) and "." in linha:
            tabela, coluna = linha[3:].strip().split(".", 1)
            tabelas.add(tabela)
            destino = removidas if linha.startswith("[-]") else adicionadas
            destino.append(coluna.split(" ", 1)[0])
    return {
        "removidas": removidas,
        "adicionadas": adicionadas,
        "tabelas": sorted(tabelas),
        "tabelas_novas": tabelas_novas,
    }


def escopo(config):
    return {"projeto": config.projeto_id, "banco": config.banco_id, "nome_schema": config.nome_schema}


def processar_evento(config, grafo, registro):
    return grafo.invoke(
        {"registro": registro}, config={"recursion_limit": 4 * config.max_tentativas + 20},
    )["registro"]


def monitorar_uma_vez(config, repo, grafo):
    schema = ler_schema_detalhado(config.db_url, config.nome_schema)
    if evento := repo.observar_schema(**escopo(config), schema=schema):
        print(f"Evento detectado {evento.estado.evento.id_evento}")
    versao_schema = identificar_schema(schema)
    resultados = []
    for registro in repo.listar_pendentes(**escopo(config), etapas=ETAPAS_PROCESSAVEIS):
        try:
            if registro.estado.evento.versao_snapshot_atual != versao_schema:
                resultado = salvar_etapa(
                    repo, registro, EtapaCorrecao.FALHOU,
                    ultimo_erro="O schema mudou novamente antes de concluir este evento.",
                )
            else:
                resultado = processar_evento(config, grafo, registro)
        except ConflitoVersaoEvento:
            # Outro processo já avançou o mesmo evento.
            continue
        resultados.append(resultado)
        print(f"{resultado.estado.evento.id_evento} {resultado.estado.etapa_atual.value}")
    return resultados


def rodar_watcher(config, repo, grafo, *, uma_vez=False):
    print(f"Monitorando {config.projeto_id} no schema {config.nome_schema}.")
    if uma_vez:
        return monitorar_uma_vez(config, repo, grafo)
    while True:
        try:
            monitorar_uma_vez(config, repo, grafo)
        except ErroPostgreSQL as erro:
            print(f"Falha ao consultar PostgreSQL ({type(erro).__name__}). Nova tentativa em {config.intervalo}s.")
        time.sleep(config.intervalo)
