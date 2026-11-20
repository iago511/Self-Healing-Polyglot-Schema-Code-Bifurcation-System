import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import tools as registro, tools_alteracoes as fluxo


class ModeloFalso:
    def __init__(self, resposta):
        self.resposta = resposta

    def invoke(self, prompt):
        assert "Diff do schema" in prompt
        return SimpleNamespace(content=self.resposta)


@pytest.fixture
def projeto(tmp_path, monkeypatch):
    original = tmp_path / "original"
    copia = tmp_path / "copia"
    original.mkdir()
    copia.mkdir()
    conteudo = "def salvar(email):\n    return email\n"
    (original / "cliente.py").write_text(conteudo, encoding="utf-8")
    (copia / "cliente.py").write_text(conteudo, encoding="utf-8")
    monkeypatch.setattr(fluxo, "_modelo_codigo", ModeloFalso("def salvar(email_contato):\n    return email_contato\n"))
    return original, copia, conteudo


def propor(original, copia, arquivo="cliente.py", **kwargs):
    return fluxo.propor_alteracao.invoke({
        "pasta_original": str(original), "pasta_copia": str(copia),
        "arquivo_relativo": arquivo, "diff_schema": "[-] clientes.email\n[+] clientes.email_contato",
        "tabela": "clientes", **kwargs,
    })


def validar(manifesto, **kwargs):
    return fluxo.validar_proposta.invoke({"caminho_manifesto": manifesto, **kwargs})


def aplicar(manifesto, **kwargs):
    return fluxo.aplicar_proposta.invoke({"caminho_manifesto": manifesto, **kwargs})


def reverter(manifesto, **kwargs):
    return fluxo.reverter_proposta.invoke({"caminho_manifesto": manifesto, **kwargs})


def test_impacto_mostra_linhas_contexto_e_exclui_ambientes(tmp_path):
    (tmp_path / "model.py").write_text(
        "class Cliente:\n    def salvar(self, email):\n        consulta = 'SELECT email FROM clientes'\n"
        "        return self.email\n", encoding="utf-8"
    )
    (tmp_path / "outro.py").write_text("email_contato = 1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignorado.py").write_text("email = 1\n", encoding="utf-8")

    resultado = fluxo.analisar_impacto.invoke({"pasta": str(tmp_path), "termos": ["email", "clientes"]})

    assert resultado["sucesso"]
    assert {item["arquivo"] for item in resultado["evidencias"]} == {"model.py"}
    assert [(item["linha"], item["metodo"]) for item in resultado["evidencias"]] == [
        (2, "identificador_python"), (3, "string_python"), (4, "identificador_python")
    ]
    assert all(item["escopo"] == "salvar" for item in resultado["evidencias"])


def test_fluxo_edicao_validacao_aplicacao_e_reversao(projeto):
    original, copia, conteudo = projeto
    resultado = propor(original, copia)
    assert resultado["sucesso"]
    assert "-def salvar(email):" in resultado["diff"]
    assert "+def salvar(email_contato):" in resultado["diff"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo

    manifesto = resultado["manifesto"]
    verificacao = validar(manifesto)
    assert verificacao["status"] == "incompleta"
    assert not verificacao["sucesso"]
    assert verificacao["sintaxe"]["status"] == "aprovada"
    assert verificacao["testes"]["status"] == "nao_executados"
    assert not aplicar(manifesto)["sucesso"]
    assert not aplicar(manifesto, confirmar=True)["sucesso"]

    aplicado = aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)
    assert aplicado["sucesso"]
    assert Path(aplicado["backup"]).read_text(encoding="utf-8") == conteudo
    assert "email_contato" in (original / "cliente.py").read_text(encoding="utf-8")

    restaurado = reverter(manifesto, confirmar=True)
    assert restaurado["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_criacao_e_reversao_de_arquivo_novo(projeto):
    original, copia, _ = projeto
    resultado = propor(original, copia, "modelos/pedido.py")
    assert resultado["sucesso"]
    manifesto = resultado["manifesto"]
    assert not (original / "modelos/pedido.py").exists()
    assert validar(manifesto)["status"] == "incompleta"
    assert aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    assert (original / "modelos/pedido.py").exists()
    assert reverter(manifesto, confirmar=True)["sucesso"]
    assert not (original / "modelos/pedido.py").exists()


def test_exclusao_e_reversao_de_arquivo(projeto, monkeypatch):
    original, copia, conteudo = projeto
    monkeypatch.setattr(fluxo, "_modelo_codigo", None)
    resultado = propor(original, copia, excluir=True)
    assert resultado["sucesso"]
    assert "+++ /dev/null" in resultado["diff"]
    assert not (copia / "cliente.py").exists()
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo
    manifesto = resultado["manifesto"]
    verificacao = validar(manifesto)
    assert verificacao["sintaxe"]["status"] == "nao_aplicavel"
    assert verificacao["status"] == "incompleta"
    assert aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    assert not (original / "cliente.py").exists()
    assert reverter(manifesto, confirmar=True)["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_original_alterado_nao_e_sobrescrito(projeto):
    original, copia, _ = projeto
    manifesto = propor(original, copia)["manifesto"]
    validar(manifesto)
    (original / "cliente.py").write_text("mudanca_externa = True\n", encoding="utf-8")

    resultado = aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)

    assert not resultado["sucesso"]
    assert "original mudou" in resultado["erro"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == "mudanca_externa = True\n"


def test_mudanca_em_outro_arquivo_invalida_a_validacao(projeto):
    original, copia, conteudo = projeto
    (original / "outro.py").write_text("valor = 1\n", encoding="utf-8")
    (copia / "outro.py").write_text("valor = 1\n", encoding="utf-8")
    manifesto = propor(original, copia)["manifesto"]
    validar(manifesto)
    (original / "outro.py").write_text("valor = 2\n", encoding="utf-8")

    resultado = aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)

    assert not resultado["sucesso"]
    assert "projeto original mudou" in resultado["erro"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_copia_alterada_nao_e_validada_nem_aplicada(projeto):
    original, copia, conteudo = projeto
    manifesto = propor(original, copia)["manifesto"]
    assert validar(manifesto)["status"] == "incompleta"
    (copia / "cliente.py").write_text("outra_mudanca = True\n", encoding="utf-8")

    assert not validar(manifesto)["sucesso"]
    assert not aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_reversao_nao_apaga_alteracoes_posteriores(projeto):
    original, copia, _ = projeto
    manifesto = propor(original, copia)["manifesto"]
    validar(manifesto)
    assert aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    (original / "cliente.py").write_text("mudanca_posterior = True\n", encoding="utf-8")

    resultado = reverter(manifesto, confirmar=True)

    assert not resultado["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == "mudanca_posterior = True\n"


def test_recupera_aplicacao_interrompida_antes_da_escrita(projeto):
    original, copia, _ = projeto
    manifesto = propor(original, copia)["manifesto"]
    validar(manifesto)
    caminho = Path(manifesto)
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["estado"] = "aplicando"
    caminho.write_text(json.dumps(dados), encoding="utf-8")

    assert aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    assert "email_contato" in (original / "cliente.py").read_text(encoding="utf-8")


def test_recupera_aplicacao_e_reversao_interrompidas(projeto):
    original, copia, conteudo = projeto
    manifesto = propor(original, copia)["manifesto"]
    validar(manifesto)
    assert aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    caminho = Path(manifesto)
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["estado"] = "aplicando"
    caminho.write_text(json.dumps(dados), encoding="utf-8")

    assert aplicar(manifesto, confirmar=True)["sucesso"]
    assert reverter(manifesto, confirmar=True)["sucesso"]
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["estado"] = "revertendo"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    assert reverter(manifesto, confirmar=True)["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_sintaxe_invalida_bloqueia_aplicacao(projeto, monkeypatch):
    original, copia, conteudo = projeto
    monkeypatch.setattr(fluxo, "_modelo_codigo", ModeloFalso("def funcao(:\n    pass\n"))
    manifesto = propor(original, copia)["manifesto"]

    verificacao = validar(manifesto)

    assert not verificacao["sucesso"]
    assert verificacao["sintaxe"]["status"] == "falhou"
    assert not aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_rejeita_caminho_fora_do_projeto_e_copia_invalida(projeto):
    original, copia, _ = projeto
    assert not propor(original, copia, "../fora.py")["sucesso"]
    assert not propor(original, original)["sucesso"]
    assert not propor(original, copia, str(original / "cliente.py"))["sucesso"]


def test_nao_sobrescreve_modificacao_ja_existente_na_copia(projeto):
    original, copia, _ = projeto
    (copia / "cliente.py").write_text("trabalho_existente = True\n", encoding="utf-8")
    resultado = propor(original, copia)
    assert not resultado["sucesso"]
    assert (copia / "cliente.py").read_text(encoding="utf-8") == "trabalho_existente = True\n"


def test_metadados_nao_podem_apontar_para_fora_da_copia(projeto, tmp_path):
    original, copia, _ = projeto
    destino = tmp_path / "fora"
    destino.mkdir()
    try:
        (copia / ".sh_pscbs").symlink_to(destino, target_is_directory=True)
    except OSError:
        pytest.skip("Criação de link simbólico indisponível neste ambiente")
    resultado = propor(original, copia)
    assert not resultado["sucesso"]
    assert list(destino.iterdir()) == []


def test_execucao_de_testes_usa_copia_filtrada_e_container_sem_rede(projeto, monkeypatch):
    original, copia, _ = projeto
    (original / ".env").write_text("SEGREDO=ficticio\n", encoding="utf-8")
    (original / "privado.key").write_text("chave-ficticia", encoding="utf-8")
    (copia / ".env").write_text("SEGREDO=ficticio\n", encoding="utf-8")
    (copia / "privado.key").write_text("chave-ficticia", encoding="utf-8")
    (original / "outro.py").write_text("versao_original = True\n", encoding="utf-8")
    (copia / "outro.py").write_text("proposta_nao_aplicada = True\n", encoding="utf-8")
    manifesto = propor(original, copia)["manifesto"]
    comandos = []
    monkeypatch.setenv("DOCKER_TEST_STORAGE", "bind")
    monkeypatch.setattr(fluxo.shutil, "which", lambda _: "docker")

    def executar_falso(comando, **opcoes):
        comandos.append(comando)
        if comando[1] == "run":
            montagem = comando[comando.index("--mount") + 1]
            pasta = Path(montagem.split(",src=", 1)[1].split(",dst=", 1)[0])
            assert (pasta / "cliente.py").exists()
            assert "email_contato" in (pasta / "cliente.py").read_text(encoding="utf-8")
            assert (pasta / "outro.py").read_text(encoding="utf-8") == "versao_original = True\n"
            assert not (pasta / ".env").exists()
            assert not (pasta / "privado.key").exists()
            assert not (pasta / ".sh_pscbs").exists()
            opcoes["stdout"].write(b"2 passed\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(fluxo.subprocess, "run", executar_falso)
    resultado = validar(manifesto, imagem_teste="projeto:testes")

    assert resultado.get("sucesso"), resultado
    assert resultado["testes"]["status"] == "aprovados"
    assert resultado["testes"]["saida"] == "2 passed\n"
    assert "--read-only" in comandos[1]
    assert comandos[1][comandos[1].index("--network") + 1] == "none"
    assert aplicar(manifesto, confirmar=True)["sucesso"]


def test_imagem_indisponivel_nao_e_reportada_como_teste_aprovado(projeto, monkeypatch):
    original, copia, _ = projeto
    manifesto = propor(original, copia)["manifesto"]
    monkeypatch.setattr(fluxo.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(fluxo.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))

    resultado = validar(manifesto, imagem_teste="inexistente:local")

    assert resultado["testes"]["status"] == "nao_executados"
    assert not aplicar(manifesto, confirmar=True)["sucesso"]


def test_testes_com_falha_bloqueiam_aplicacao_mesmo_com_excecao(projeto, monkeypatch):
    original, copia, conteudo = projeto
    manifesto = propor(original, copia)["manifesto"]
    monkeypatch.setattr(fluxo, "_testar_em_container", lambda *args: {"status": "falharam", "codigo_saida": 1})
    resultado = validar(manifesto, imagem_teste="projeto:testes")
    assert resultado["status"] == "reprovada"
    assert not aplicar(manifesto, confirmar=True, permitir_validacao_incompleta=True)["sucesso"]
    assert (original / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_falha_do_modelo_nao_modifica_a_copia(projeto, monkeypatch):
    original, copia, conteudo = projeto

    class ModeloComFalha:
        def invoke(self, prompt):
            raise RuntimeError("modelo indisponível")

    monkeypatch.setattr(fluxo, "_modelo_codigo", ModeloComFalha())
    resultado = propor(original, copia)
    assert not resultado["sucesso"]
    assert (copia / "cliente.py").read_text(encoding="utf-8") == conteudo


def test_copia_alterada_durante_geracao_nao_e_sobrescrita(projeto, monkeypatch):
    original, copia, _ = projeto

    class ModeloEnquantoArquivoMuda:
        def invoke(self, prompt):
            (copia / "cliente.py").write_text("edicao_concorrente = True\n", encoding="utf-8")
            return SimpleNamespace(content="codigo_proposto = True\n")

    monkeypatch.setattr(fluxo, "_modelo_codigo", ModeloEnquantoArquivoMuda())
    resultado = propor(original, copia)
    assert not resultado["sucesso"]
    assert (copia / "cliente.py").read_text(encoding="utf-8") == "edicao_concorrente = True\n"


def test_ferramentas_ficam_registradas_e_modelo_e_compartilhado(monkeypatch):
    monkeypatch.setattr(registro, "_llm", None)
    monkeypatch.setattr(fluxo, "_modelo_codigo", None)
    modelo = ModeloFalso("pass\n")
    registro.set_llm(modelo)

    assert all(tool in registro.TOOLS for tool in fluxo.TOOLS_ALTERACOES)
    assert fluxo._modelo_codigo is modelo
