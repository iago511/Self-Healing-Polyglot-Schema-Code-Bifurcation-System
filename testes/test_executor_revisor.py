import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_groq import ChatGroq

from sh_pscbs.agentes.correcao import gerar_proposta_correcao, identificar_codigo_base
from sh_pscbs.agentes.revisor import revisar_proposta
from sh_pscbs.orquestracao.estado import EventoSchema, PlanoCorrecao
from sh_pscbs.validacao import executor
from sh_pscbs.validacao.executor import validar_proposta_correcao
from tools import tools_alteracoes


@pytest.fixture
def preparar_proposta(tmp_path):
    def criar(*, before="apelido = 1", after="nome = 1"):
        original = tmp_path / "original"
        original.mkdir()
        (original / "clientes.py").write_text(before + "\n", encoding="utf-8")
        (original / "api.py").write_text("resposta = 'apelido'\n", encoding="utf-8")
        (original / "legado.py").touch()
        (original / ".env").write_text("SEGREDO=simulado", encoding="utf-8")
        evento = EventoSchema(
            id_evento="e1", projeto="api", banco="clientes",
            versao_snapshot_anterior="v1", versao_snapshot_atual="v2",
            mudancas=[{"tipo": "coluna_removida", "tabela": "clientes", "coluna": "apelido"}],
        )
        plano = PlanoCorrecao(acoes=[
            {"arquivo": "clientes.py", "operacao": "alterar", "descricao": "Atualizar a consulta."},
            {"arquivo": "api.py", "operacao": "alterar", "descricao": "Atualizar a resposta."},
            {"arquivo": "legado.py", "operacao": "excluir", "descricao": "Remover arquivo sem consumidores."},
        ], testes_previstos=["Verificar a consulta e a resposta juntas."])
        respostas = iter([after, "resposta = 'nome'"])
        modelo = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=next(respostas)))
        proposta = gerar_proposta_correcao(
            evento, plano, original, pasta_propostas=tmp_path / "propostas", modelo=modelo,
        )
        return evento, plano, proposta, original
    return criar


def _simular_docker(monkeypatch, retorno):
    monkeypatch.setattr(tools_alteracoes.shutil, "which", lambda _: "docker")
    execucoes = []

    def executar(comando, **opcoes):
        if comando[1] == "image":
            return SimpleNamespace(returncode=0)
        execucoes.append(comando)
        montagem = comando[comando.index("--mount") + 1]
        pasta = Path(montagem.split(",src=", 1)[1].split(",dst=", 1)[0])
        assert (pasta / "clientes.py").read_text(encoding="utf-8") == "nome = 1\n"
        assert (pasta / "api.py").read_text(encoding="utf-8") == "resposta = 'nome'\n"
        assert not (pasta / "legado.py").exists()
        assert not (pasta / ".env").exists()
        assert montagem.endswith("readonly")
        assert comando[comando.index("--network") + 1] == "none"
        opcoes["stdout"].write(b"saida de teste")
        return SimpleNamespace(returncode=retorno)

    monkeypatch.setattr(tools_alteracoes.subprocess, "run", executar)
    return execucoes


@pytest.mark.parametrize(("retorno", "status"), [(0, "aprovada"), (1, "reprovada"), (5, "incompleta")])
def test_executor_valida_os_arquivos_juntos(preparar_proposta, monkeypatch, retorno, status):
    _, _, proposta, original = preparar_proposta()
    execucoes = _simular_docker(monkeypatch, retorno)

    resultado = validar_proposta_correcao(proposta, original, imagem_teste="pytest:local")

    assert resultado.id_proposta == proposta.id_proposta
    assert resultado.status == status
    assert "saida de teste" in resultado.verificacoes[-1].detalhe
    assert len(execucoes) == 1
    assert (original / "legado.py").exists()
    assert (original / "clientes.py").read_text(encoding="utf-8") == "apelido = 1\n"


def test_executor_identifica_erro_python_sem_executar_codigo(preparar_proposta, monkeypatch):
    _, _, proposta, original = preparar_proposta(after="return 1")
    execucoes = _simular_docker(monkeypatch, 0)
    resultado = validar_proposta_correcao(proposta, original, imagem_teste="pytest:local")
    assert resultado.status == "reprovada"
    assert any(verificacao.status == "reprovada" and "clientes.py" in verificacao.nome
               for verificacao in resultado.verificacoes)
    assert not execucoes


def test_executor_rejeita_copia_com_arquivo_extra_mesmo_com_hash_atualizado(preparar_proposta):
    _, _, proposta, original = preparar_proposta()
    pasta = Path(proposta.referencia_diff).parent
    copia = pasta / "projeto"
    (copia / "extra.py").write_text("fora_do_plano = True\n", encoding="utf-8")
    manifesto = pasta / "manifesto.json"
    dados = json.loads(manifesto.read_text(encoding="utf-8"))
    dados["hash_copia"] = identificar_codigo_base(copia)
    manifesto.write_text(json.dumps(dados), encoding="utf-8")

    resultado = validar_proposta_correcao(proposta, original)

    assert resultado.status == "reprovada"
    assert "fora dos arquivos planejados" in resultado.verificacoes[0].detalhe


def test_executor_rejeita_diff_divergente_da_copia(preparar_proposta):
    _, _, proposta, original = preparar_proposta()
    Path(proposta.referencia_diff).write_text("diff substituído", encoding="utf-8")
    resultado = validar_proposta_correcao(proposta, original)
    assert resultado.status == "reprovada"
    assert "diff não corresponde" in resultado.verificacoes[0].detalhe


def _chamada(nome, argumentos, numero):
    return {"model": "openai/gpt-oss-120b", "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"chamada-{numero}", "type": "function",
            "function": {"name": nome, "arguments": json.dumps(argumentos)},
        }]},
    }]}


@pytest.mark.parametrize(("com_imagem", "parecer_esperado"), [(True, "favoravel"), (False, "ajustes_necessarios")])
def test_revisor_le_versoes_e_respeita_pendencias(preparar_proposta, monkeypatch, com_imagem, parecer_esperado):
    evento, plano, proposta, original = preparar_proposta()
    _simular_docker(monkeypatch, 0)
    validacao = validar_proposta_correcao(proposta, original, imagem_teste="pytest:local" if com_imagem else "")
    modelo = ChatGroq(model="openai/gpt-oss-120b", api_key="chave-de-teste")
    etapas = iter([
        ("consultar_arquivo_revisao", {"arquivo": ".env", "versao": "original"}, None),
        ("consultar_arquivo_revisao", {"arquivo": "clientes.py", "versao": "original"}, "erro"),
        ("ParecerRevisao", {
            "id_proposta": proposta.id_proposta,
            "parecer": "favoravel",
            "justificativa": "O código corresponde ao plano.",
            "ajustes_solicitados": [],
        }, "trecho"),
    ])
    chamadas = 0

    def responder(**requisicao):
        nonlocal chamadas
        assert {item["function"]["name"] for item in requisicao["tools"]} == {
            "ler_diff_revisao", "consultar_arquivo_revisao", "ParecerRevisao",
        }
        assert "SEGREDO=simulado" not in json.dumps(requisicao["messages"])
        nome, argumentos, campo = next(etapas)
        if campo:
            retorno = json.loads(requisicao["messages"][-1]["content"])
            assert campo in retorno
            if campo == "trecho":
                assert "apelido = 1" in retorno["trecho"]
        chamadas += 1
        return _chamada(nome, argumentos, chamadas)

    with patch.object(modelo.client, "create", side_effect=responder):
        parecer = revisar_proposta(evento, plano, proposta, validacao, original, modelo=modelo)

    assert chamadas == 3
    assert parecer.parecer == parecer_esperado
    if not com_imagem:
        assert any("Pytest da proposta completa" in ajuste for ajuste in parecer.ajustes_solicitados)


def test_revisor_recusa_validacao_de_outra_proposta(preparar_proposta):
    evento, plano, proposta, original = preparar_proposta()
    validacao = validar_proposta_correcao(proposta, original)
    with pytest.raises(ValueError, match="outra versão"):
        revisar_proposta(evento, plano, proposta, validacao.model_copy(update={"id_proposta": "antiga"}), original)

    with pytest.raises(ValueError, match="conteúdo atual do diff"):
        revisar_proposta(evento, plano, proposta, validacao.model_copy(update={"hash_diff": "0" * 64}), original)


@pytest.mark.parametrize("consultar_diff", [False, True])
def test_parecer_favoravel_exige_diff_disponibilizado_integralmente(preparar_proposta, monkeypatch, consultar_diff):
    anterior = "\n".join(f"valor_{indice} = 1" for indice in range(45))
    posterior = anterior.replace(" = 1", " = 2")
    evento, plano, proposta, original = preparar_proposta(before=anterior, after=posterior)
    monkeypatch.setattr(executor, "_executar_pytest_isolado", lambda *args: {"status": "aprovados"})
    validacao = validar_proposta_correcao(proposta, original, imagem_teste="pytest:local")
    modelo = ChatGroq(model="openai/gpt-oss-120b", api_key="chave-de-teste")
    respostas = []
    if consultar_diff:
        respostas.append(_chamada("ler_diff_revisao", {"linha_inicial": 61, "quantidade": 80}, 1))
    respostas.append(_chamada("ParecerRevisao", {
        "id_proposta": proposta.id_proposta,
        "parecer": "favoravel",
        "justificativa": "O código corresponde ao plano.",
        "ajustes_solicitados": [],
    }, 2))

    with patch.object(modelo.client, "create", side_effect=respostas):
        parecer = revisar_proposta(evento, plano, proposta, validacao, original, modelo=modelo)

    assert parecer.parecer == ("favoravel" if consultar_diff else "ajustes_necessarios")
