import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import psycopg2
import pytest
from langchain_groq import ChatGroq
from psycopg2 import sql

import main
from sh_pscbs.agentes import correcao, planejamento, revisor
from sh_pscbs.eventos.repositorio import RepositorioEventos
from sh_pscbs.monitoramento import watcher
from sh_pscbs.orquestracao.estado import EtapaCorrecao
from sh_pscbs.validacao import executor


@pytest.fixture
def banco(monkeypatch):
    dsn = os.getenv("SH_TEST_POSTGRES_DSN")
    if os.getenv("SH_DEMO_MODE") == "1" and not dsn:
        pytest.fail("The demo requires its disposable PostgreSQL service.")
    if not dsn:
        schema = {"clientes": {"apelido": {"tipo": "text"}}}
        for modulo in (main, watcher):
            monkeypatch.setattr(modulo, "ler_schema_detalhado", lambda *_: schema)
        monkeypatch.setenv("DB_URL", "postgresql://teste@127.0.0.1/teste")
        monkeypatch.setenv("PG_SCHEMA", "public")
        yield lambda: schema["clientes"].update(nome=schema["clientes"].pop("apelido"))
        return

    nome = f"pscbs_{uuid4().hex}"
    identificador = sql.Identifier(nome)
    monkeypatch.setenv("DB_URL", dsn)
    monkeypatch.setenv("PG_SCHEMA", nome)
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conexao:
        conexao.autocommit = True
        with conexao.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(identificador))
            try:
                cursor.execute(sql.SQL("CREATE TABLE {}.clientes (apelido text)").format(identificador))
                yield lambda: cursor.execute(
                    sql.SQL("ALTER TABLE {}.clientes RENAME COLUMN apelido TO nome").format(identificador)
                )
            finally:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(identificador))


def _chamada(nome, argumentos, numero):
    return {"model": "openai/gpt-oss-120b", "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"chamada-{numero}", "type": "function",
            "function": {"name": nome, "arguments": json.dumps(argumentos)},
        }]},
    }]}


def _resposta_json(argumentos):
    return {"model": "openai/gpt-oss-120b", "choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(argumentos)},
    }]}


def test_monitor_ate_publicacao_git(tmp_path, monkeypatch, banco, capsys):
    demo = os.getenv("SH_DEMO_MODE") == "1"

    def show_demo(*lines):
        if demo:
            with capsys.disabled():
                print(*lines, sep="\n", flush=True)

    if not shutil.which("git"):
        pytest.skip("Git não instalado.")
    projeto = tmp_path / "origem"
    projeto.mkdir()
    remoto = tmp_path / "remoto.git"

    def git(*args, cwd=projeto):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=True, text=True, timeout=15,
        ).stdout.strip()

    original = 'CONSULTA = "SELECT apelido FROM clientes"\n'
    corrigido = 'CONSULTA = "SELECT nome FROM clientes"\n'
    (projeto / "clientes.py").write_text(original, encoding="utf-8")
    (projeto / "test_clientes.py").write_text(
        'from clientes import CONSULTA\n\ndef test_consulta():\n    assert "SELECT nome FROM clientes" == CONSULTA\n',
        encoding="utf-8",
    )
    (projeto / ".gitignore").write_text(".env\n", encoding="utf-8")
    (projeto / ".env").write_text("SEGREDO=simulado", encoding="utf-8")
    git("init", "-b", "main")
    git("config", "user.name", "Integração SH-PSCBS")
    git("config", "user.email", "teste@example.invalid")
    git("config", "core.autocrlf", "false")
    git("add", "--all")
    git("commit", "-m", "Projeto sintético")
    commit_original = git("rev-parse", "HEAD")
    git("init", "--bare", str(remoto))
    git("remote", "add", "origin", str(remoto))

    eventos = tmp_path / "eventos.db"
    for chave, valor in {
        "PROJECT_PATH": projeto, "PROJECT_ID": "api", "BANCO_ID": "teste",
        "EVENTOS_DB_PATH": eventos, "PROPOSTAS_DIR": tmp_path / "propostas",
        "IMAGEM_TESTE": "", "MAX_TENTATIVAS": 2, "TEST_TIMEOUT_SECONDS": 45,
    }.items():
        monkeypatch.setenv(chave, str(valor))

    modelo = ChatGroq(model="openai/gpt-oss-120b", api_key="chave-simulada")
    monkeypatch.setattr(planejamento, "criar_modelos_groq", lambda: (modelo, modelo))
    monkeypatch.setattr(revisor, "criar_modelos_groq", lambda: (modelo, modelo))
    monkeypatch.setattr(correcao, "criar_modelo_codigo", lambda: SimpleNamespace(
        invoke=lambda prompt: SimpleNamespace(content=corrigido),
    ))
    chamadas = []

    def responder(**requisicao):
        contexto = json.loads(requisicao["messages"][-1]["content"])
        chamadas.append(contexto)
        if "proposta" in contexto:
            return _chamada("ParecerRevisao", {
                "id_proposta": contexto["proposta"]["id_proposta"], "parecer": "favoravel",
                "justificativa": "A consulta usa a coluna atual.", "ajustes_solicitados": [],
            }, len(chamadas))
        assert contexto["relatorio_impacto"]["evidencias"]
        return _resposta_json({
            "status": "planejado", "justificativa": "A consulta usa a coluna antiga.", "perguntas": [],
            "plano": {"acoes": [{"arquivo": "./clientes.py", "operacao": "alterar", "descricao": "Atualizar a consulta."}]},
        })

    assert main.main(["copiar", str(tmp_path / "copia")]) == 0
    copia = tmp_path / "copia"
    assert (copia / "clientes.py").is_file() and not (copia / ".git").exists() and not (copia / ".env").exists()
    with patch.object(modelo.client, "create", side_effect=responder):
        assert main.main(["monitor", "--uma-vez"]) == 0
        repo = RepositorioEventos(eventos)
        assert repo.listar_pendentes() == []
        banco()
        show_demo("SH-PSCBS / WORKFLOW DEMO", "Model responses are simulated.", "",
                  "01  PostgreSQL schema change",
                  "ALTER TABLE clientes RENAME COLUMN apelido TO nome;")
        assert main.main(["monitor", "--uma-vez"]) == 0
        registro, = repo.listar_pendentes()
        estado = registro.estado
        assert estado.etapa_atual == EtapaCorrecao.AJUSTES_NECESSARIOS, estado.ultimo_erro
        assert estado.validacao.status == "incompleta"
        id_evento, id_proposta = estado.evento.id_evento, estado.proposta.id_proposta
        aprovar = ["aprovar", id_evento, "--proposta", id_proposta, "--autor", "operador"]
        assert main.main(aprovar) == 1
        show_demo("", "02  Proposed fix", Path(estado.proposta.referencia_diff).read_text(encoding="utf-8").strip(),
                  "Approval blocked until tests pass.")

        imagem = os.getenv("SH_TEST_DOCKER_IMAGE")
        if not imagem:
            def pytest_demo_or_stub(imagem, timeout, preparar):
                pasta = tmp_path / "projeto-em-teste"
                preparar(pasta)
                assert (pasta / "clientes.py").read_text(encoding="utf-8") == corrigido
                if demo:
                    resultado = subprocess.run(
                        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                        cwd=pasta, capture_output=True, text=True, check=False, timeout=timeout,
                    )
                    show_demo("", "03  Validate and review", resultado.stdout.strip())
                    return {
                        "status": "aprovados" if resultado.returncode == 0 else "falharam",
                        "saida": resultado.stdout + resultado.stderr,
                    }
                return {"status": "aprovados"}
            monkeypatch.setattr(executor, "_executar_pytest_isolado", pytest_demo_or_stub)
        monkeypatch.setenv("IMAGEM_TESTE", imagem or "fixture:local")
        assert main.main(["reprocessar", id_evento]) == 0
        estado = repo.obter_evento(id_evento).estado
        assert estado.etapa_atual == EtapaCorrecao.AGUARDANDO_APROVACAO, estado.validacao
        assert estado.proposta.id_proposta == id_proposta
        assert len(chamadas) == 3
        assert main.main(["monitor", "--uma-vez"]) == 0
        assert len(repo.listar_pendentes()) == 1
        assert main.main(["aprovar", id_evento, "--proposta", "desatualizada", "--autor", "operador"]) == 1
        assert main.main(aprovar) == 0

    estado = repo.obter_evento(id_evento).estado
    assert estado.etapa_atual == EtapaCorrecao.APLICADO
    assert estado.decisao_humana.autor_id == "operador"
    assert git("rev-parse", "HEAD") == commit_original
    assert git("branch", "--show-current") == "main"
    assert git("status", "--porcelain") == ""
    assert (projeto / "clientes.py").read_text(encoding="utf-8") == original
    assert git("show", f"{estado.commit_git}:clientes.py") == corrigido.strip()
    assert git("show", "-s", "--format=%s", estado.commit_git) == f"fix: apply SH-PSCBS proposal {id_proposta}"
    assert main.main(aprovar) == 0
    assert repo.obter_evento(id_evento).estado.commit_git == estado.commit_git
    assert main.main(["publicar", id_evento]) == 0
    assert git("rev-parse", f"refs/heads/{estado.branch_git}", cwd=remoto) == estado.commit_git
    show_demo("Review approved. Commit created after human approval.", "",
              "04  Published to a local Git remote",
              f"{estado.commit_git[:7]}  {estado.branch_git}",
              "Original checkout is clean.")
    assert repo.listar_pendentes() == []
    assert EtapaCorrecao.APROVADO in [item.etapa for item in repo.listar_historico(id_evento)]
    capsys.readouterr()
    assert main.main(["evento", id_evento]) == 0
    assert json.loads(capsys.readouterr().out)["estado"]["commit_git"] == estado.commit_git
