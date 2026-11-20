from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from sh_pscbs.eventos.repositorio import ConflitoVersaoEvento, RepositorioEventos
from sh_pscbs.orquestracao.estado import DecisaoHumana, EtapaCorrecao, EventoSchema, PropostaCorrecao


@pytest.fixture
def evento():
    return EventoSchema(
        id_evento="evento-1",
        projeto="api",
        banco="clientes",
        versao_snapshot_anterior="v1",
        versao_snapshot_atual="v2",
        mudancas=[{"tipo": "coluna_removida", "tabela": "clientes", "coluna": "apelido"}],
    )


def test_persiste_estado_e_registro_repetido_nao_reseta_evento(tmp_path, monkeypatch, evento):
    caminho = tmp_path / "dados" / "eventos.db"
    monkeypatch.setenv("EVENTOS_DB_PATH", str(caminho))
    repo = RepositorioEventos()
    registro = repo.registrar_evento(evento)
    novo_estado = registro.estado.model_copy(update={"etapa_atual": EtapaCorrecao.ANALISANDO})
    atualizado = repo.salvar_estado(novo_estado, versao_esperada=registro.versao)

    reaberto = RepositorioEventos(caminho)
    assert caminho.is_file()
    assert atualizado.versao == 2
    assert reaberto.obter_evento(evento.id_evento) == atualizado
    assert reaberto.registrar_evento(evento) == atualizado
    assert len(reaberto.listar_historico(evento.id_evento)) == 2

    with pytest.raises(ValueError, match="outro evento"):
        reaberto.registrar_evento(evento.model_copy(update={"projeto": "outro"}))


def test_gravacao_concorrente_nao_perde_atualizacao(tmp_path, evento):
    repo = RepositorioEventos(tmp_path / "eventos.db")
    registro = repo.registrar_evento(evento)
    barreira = Barrier(2, timeout=5)

    def gravar(etapa):
        estado = registro.estado.model_copy(update={"etapa_atual": etapa})
        barreira.wait()
        try:
            return repo.salvar_estado(estado, versao_esperada=registro.versao)
        except ConflitoVersaoEvento:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(gravar, [EtapaCorrecao.ANALISANDO, EtapaCorrecao.FALHOU]))

    gravados = [registro for registro in resultados if registro is not None]
    assert len(gravados) == 1
    assert repo.obter_evento(evento.id_evento) == gravados[0]
    assert len(repo.listar_historico(evento.id_evento)) == 2


def test_historico_preserva_decisao_de_proposta_substituida(tmp_path, evento):
    repo = RepositorioEventos(tmp_path / "eventos.db")
    inicial = repo.registrar_evento(evento)
    proposta = PropostaCorrecao(
        id_proposta="patch-1",
        codigo_base_id="base",
        versao_snapshot_atual="v2",
        arquivos=["api.py"],
        referencia_diff="patch-1.diff",
        resumo="Remover referência a apelido",
    )
    decisao = DecisaoHumana(
        id_proposta="patch-1",
        autor_id="dev-1",
        decidida_em=datetime.now(timezone.utc),
        decisao="solicitar_ajustes",
    )
    revisado = repo.salvar_estado(
        inicial.estado.model_copy(update={"proposta": proposta, "decisao_humana": decisao}),
        versao_esperada=inicial.versao,
        motivo="Pedido de ajustes",
    )
    final = repo.salvar_estado(
        revisado.estado.model_copy(update={
            "proposta": proposta.model_copy(update={"id_proposta": "patch-2"}),
            "decisao_humana": None,
        }),
        versao_esperada=revisado.versao,
    )

    assert final.estado.decisao_humana is None
    _, transicao, _ = repo.listar_historico(evento.id_evento)
    assert transicao.decisao_humana == decisao
    assert transicao.motivo == "Pedido de ajustes"


def test_filtra_pendentes_por_projeto_e_exclui_eventos_encerrados(tmp_path, evento):
    repo = RepositorioEventos(tmp_path / "eventos.db")
    inicial = repo.registrar_evento(evento)
    repo.salvar_estado(
        inicial.estado.model_copy(update={"etapa_atual": EtapaCorrecao.REJEITADO}),
        versao_esperada=inicial.versao,
    )
    repo.registrar_evento(evento.model_copy(update={"id_evento": "evento-2"}))
    repo.registrar_evento(evento.model_copy(update={"id_evento": "evento-3", "projeto": "site"}))

    pendentes = repo.listar_pendentes(projeto="api")
    assert [registro.estado.evento.id_evento for registro in pendentes] == ["evento-2"]
    assert len(repo.listar_pendentes(limite=1)) == 1
    assert repo.obter_evento("inexistente") is None
