import json

from unittest.mock import MagicMock, patch

import snapshot

from watcher import extrair_mudancas

def test_diff_detecta_tabelas_e_colunas():

    antigo = {"clientes": {"id": "integer", "apelido": "text"}, "legado": {}}

    novo = {"clientes": {"id": "integer", "email": "text"}, "pedidos": {}}

    linhas = set(snapshot.comparar(antigo, novo).splitlines())

    assert linhas == {

        "[+] Tabela adicionada: pedidos", "[-] Tabela removida: legado",

        "[+] clientes.email adicionada (text)", "[-] clientes.apelido removida (text)",

    }

    assert snapshot.comparar(novo, novo) == "Nenhuma mudança detectada."

def test_extracao_para_orquestrador():

    diff = "[-] clientes.apelido removida (text)\n[+] clientes.email adicionada (text)\n[+] Tabela adicionada: pedidos"

    assert extrair_mudancas(diff) == {

        "removidas": ["apelido"], "adicionadas": ["email"],

        "tabelas": ["clientes"], "tabelas_novas": ["pedidos"],

    }

def test_snapshot_persiste_sem_duplicar(tmp_path, monkeypatch):

    monkeypatch.setattr(snapshot, "SNAPSHOTS_DIR", str(tmp_path))

    assert snapshot.carregar_ultimo_snapshot() is None

    schema = {"clientes": {"id": "integer"}}

    caminho = snapshot.salvar_snapshot(schema)

    assert json.loads(tmp_path.joinpath("schema_latest.json").read_text()) == schema

    assert caminho is not None

    assert snapshot.salvar_snapshot(schema) is None

def test_fluxo_primeira_leitura_e_mudanca(tmp_path, monkeypatch):

    monkeypatch.setattr(snapshot, "SNAPSHOTS_DIR", str(tmp_path))

    with patch.object(snapshot, "ler_schema", return_value={"clientes": {}}):

        assert "Primeiro snapshot" in snapshot.verificar_mudancas("url-ficticia")

        assert snapshot.verificar_mudancas("url-ficticia") == "Nenhuma mudança detectada."

    with patch.object(snapshot, "ler_schema", return_value={"clientes": {"email": "text"}}):

        assert snapshot.verificar_mudancas("url-ficticia") == "[+] clientes.email adicionada (text)"

def test_leitura_agrupa_colunas_e_fecha_conexao():
    conn = MagicMock()

    conn.cursor.return_value.fetchall.return_value = [

        ("clientes", "id", "integer"), ("clientes", "nome", "text"),

    ]

    with patch.object(snapshot.psycopg2, "connect", return_value=conn):

        assert snapshot.ler_schema("url-ficticia") == {"clientes": {"id": "integer", "nome": 

"text"}}

    conn.cursor.return_value.close.assert_called_once()

    conn.close.assert_called_once()