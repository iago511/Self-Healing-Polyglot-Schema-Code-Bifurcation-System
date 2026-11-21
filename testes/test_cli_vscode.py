import json
from types import SimpleNamespace

import pytest

import main
from sh_pscbs.eventos.repositorio import RepositorioEventos
from sh_pscbs.orquestracao.estado import EtapaCorrecao, EventoSchema


def test_cli_lists_closed_events_for_editor_history(monkeypatch, tmp_path, capsys):
    database = tmp_path / "events.db"
    repo = RepositorioEventos(database)
    event = EventoSchema(
        id_evento="event-from-vscode",
        projeto="demo",
        banco="customers",
        versao_snapshot_anterior="before",
        versao_snapshot_atual="after",
        mudancas=[{"tipo": "coluna_removida", "tabela": "customers", "coluna": "nickname"}],
    )
    record = repo.registrar_evento(event)
    repo.salvar_estado(
        record.estado.model_copy(update={"etapa_atual": EtapaCorrecao.APLICADO}),
        versao_esperada=record.versao,
    )
    config = SimpleNamespace(eventos_db=database)
    monkeypatch.setattr(main, "escopo", lambda _config: {"projeto": "demo", "banco": "customers", "nome_schema": "public"})

    assert main._executar(config, SimpleNamespace(comando="pendentes", falhas=False, todos=True, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "event-from-vscode"
    assert payload[0]["stage"] == "aplicado"
    assert payload[0]["detail"]["evento"]["banco"] == "customers"


def test_cli_rejects_conflicting_event_filters(tmp_path):
    config = SimpleNamespace(eventos_db=tmp_path / "events.db")
    with pytest.raises(ValueError, match="Use --falhas ou --todos"):
        main._executar(config, SimpleNamespace(comando="pendentes", falhas=True, todos=True, json=False))
