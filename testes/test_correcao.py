import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sh_pscbs.agentes.correcao import gerar_proposta_correcao, identificar_codigo_base
from sh_pscbs.orquestracao.estado import EventoSchema, PlanoCorrecao


@pytest.fixture
def evento():
    return EventoSchema(
        id_evento="remocao-apelido", projeto="api", banco="cadastro",
        versao_snapshot_anterior="v1", versao_snapshot_atual="v2",
        mudancas=[{"tipo": "coluna_removida", "tabela": "clientes", "coluna": "apelido"}],
    )


def _plano(*acoes):
    return PlanoCorrecao(
        acoes=[{"arquivo": arquivo, "operacao": operacao, "descricao": descricao}
               for arquivo, operacao, descricao in acoes],
        testes_previstos=["Verificar a consulta de clientes."],
    )


class ModeloSimulado:
    def __init__(self, *respostas):
        self.respostas = iter(respostas)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        resposta = next(self.respostas)
        if isinstance(resposta, Exception):
            raise resposta
        return SimpleNamespace(content=resposta)


def test_proposta_com_varios_arquivos_preserva_original(tmp_path, evento, monkeypatch):
    projeto = tmp_path / "origem"
    projeto.mkdir()
    (projeto / "cadastro anterior.py").write_bytes(b"apelido = 1")
    (projeto / "api.py").write_bytes(b"apelido = 1\r\nresposta = apelido\r\n")
    (projeto / "legado vazio.py").touch()
    (projeto / ".env").write_text("SEGREDO=simulado", encoding="utf-8")
    monkeypatch.setenv("EVENTOS_DB_PATH", str(tmp_path / "dados" / "eventos.db"))
    base = identificar_codigo_base(projeto)
    plano = _plano(
        ("cadastro anterior.py", "alterar", "Preservar a consulta de cadastro."),
        ("api.py", "alterar", "Atualizar a API."),
        ("novo.py", "criar", "Adicionar um adaptador."),
        ("legado vazio.py", "excluir", "Remover módulo legado sem consumidores."),
    )
    modelo = ModeloSimulado("nome = 1", "nome = 1\nresposta = nome", "ativo = True")

    proposta = gerar_proposta_correcao(evento, plano, projeto, codigo_base_id=base, modelo=modelo)

    diff = Path(proposta.referencia_diff)
    pasta_proposta = diff.parent
    copia = pasta_proposta / "projeto"
    manifesto = json.loads((pasta_proposta / "manifesto.json").read_text(encoding="utf-8"))
    assert pasta_proposta.parent == (tmp_path / "dados" / "propostas").resolve()
    assert proposta.codigo_base_id == base == identificar_codigo_base(projeto)
    assert proposta.versao_snapshot_atual == "v2"
    assert proposta.arquivos == ["cadastro anterior.py", "api.py", "novo.py", "legado vazio.py"]
    assert manifesto["proposta"] == proposta.model_dump(mode="json")
    assert manifesto["hash_diff"] == hashlib.sha256(diff.read_bytes()).hexdigest()
    assert manifesto["hash_copia"] == identificar_codigo_base(copia)
    assert (copia / "novo.py").is_file()
    assert not (copia / "legado vazio.py").exists()
    assert not (copia / ".env").exists()
    assert (projeto / "legado vazio.py").is_file() and not (projeto / "novo.py").exists()
    assert (copia / "api.py").read_bytes() == b"nome = 1\r\nresposta = nome\r\n"
    assert "Preservar a consulta de cadastro." in modelo.prompts[0]
    assert "a/cadastro anterior.py" in modelo.prompts[1]
    assert len(modelo.prompts) == 3

    git = shutil.which("git")
    if git:
        revisao = shutil.copytree(projeto, tmp_path / "revisao")
        resultado = subprocess.run(
            [git, "-c", "core.autocrlf=false", "apply", "--binary", str(diff)],
            cwd=revisao, capture_output=True,
            check=False, timeout=10,
        )
        assert resultado.returncode == 0, resultado.stderr.decode(errors="replace")
        assert identificar_codigo_base(revisao) == manifesto["hash_copia"]


def test_falha_entre_acoes_nao_publica_proposta_parcial(tmp_path, evento):
    origem = tmp_path / "origem"
    origem.mkdir()
    (origem / "api.py").write_text("valor = 1\n", encoding="utf-8")
    destino = tmp_path / "propostas"
    plano = _plano(("api.py", "alterar", "Trocar valor."), ("novo.py", "criar", "Criar módulo."))
    modelo = ModeloSimulado("valor = 2", RuntimeError("Ollama indisponível"))

    with pytest.raises(RuntimeError, match="novo.py"):
        gerar_proposta_correcao(evento, plano, origem, pasta_propostas=destino, modelo=modelo)

    assert list(destino.iterdir()) == []
    assert (origem / "api.py").read_text(encoding="utf-8") == "valor = 1\n"
    assert not (origem / "novo.py").exists()


@pytest.mark.parametrize("arquivo", ["../fora.py", ".env"])
def test_rejeita_acoes_fora_do_escopo(tmp_path, evento, arquivo):
    destino = tmp_path / "propostas"
    plano = _plano((arquivo, "alterar", "Caminho indevido."))
    with pytest.raises(ValueError):
        gerar_proposta_correcao(evento, plano, tmp_path, pasta_propostas=destino, modelo=ModeloSimulado())
    assert not destino.exists()


def test_detecta_base_desatualizada_antes_de_chamar_modelo(tmp_path, evento):
    origem = tmp_path / "origem"
    origem.mkdir()
    arquivo = origem / "api.py"
    arquivo.write_text("versao = 1\n", encoding="utf-8")
    base = identificar_codigo_base(origem)
    arquivo.write_text("versao = 2\n", encoding="utf-8")
    plano = _plano(("api.py", "alterar", "Atualizar versão."))

    with pytest.raises(ValueError, match="mudou desde a análise"):
        gerar_proposta_correcao(
            evento, plano, origem, codigo_base_id=base,
            pasta_propostas=tmp_path / "propostas", modelo=ModeloSimulado(),
        )


def test_mudanca_em_arquivo_fora_do_plano_interrompe_publicacao(tmp_path, evento):
    origem = tmp_path / "origem"
    origem.mkdir()
    (origem / "api.py").write_text("valor = 1\n", encoding="utf-8")
    outro = origem / "outro.py"
    outro.write_text("externo = 1\n", encoding="utf-8")
    plano = _plano(("api.py", "alterar", "Trocar valor."))
    destino = tmp_path / "propostas"

    class ModeloComEdicaoConcorrente:
        def invoke(self, prompt):
            outro.write_text("externo = 2\n", encoding="utf-8")
            return SimpleNamespace(content="valor = 2")

    with pytest.raises(ValueError, match="original mudou durante"):
        gerar_proposta_correcao(
            evento, plano, origem, pasta_propostas=destino, modelo=ModeloComEdicaoConcorrente(),
        )

    assert list(destino.iterdir()) == []
    assert (origem / "api.py").read_text(encoding="utf-8") == "valor = 1\n"
