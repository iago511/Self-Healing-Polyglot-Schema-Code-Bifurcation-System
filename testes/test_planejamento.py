import json
from unittest.mock import patch

import pytest
from groq import BadRequestError
from httpx import Request, Response
from langchain_groq import ChatGroq

from sh_pscbs.agentes import planejamento
from pydantic import ValidationError

from sh_pscbs.agentes.planejamento import ResultadoPlanejamento, _validar_arquivos_do_plano, planejar_correcao
from sh_pscbs.agentes.schema import analisar_schema
from sh_pscbs.orquestracao.estado import AcaoCorrecao, EventoSchema, PlanoCorrecao, RelatorioImpacto


@pytest.fixture
def evento():
    return EventoSchema(
        id_evento="e1",
        projeto="clientes",
        banco="db",
        versao_snapshot_anterior="v1",
        versao_snapshot_atual="v2",
        mudancas=[{"tipo": "coluna_removida", "tabela": "clientes", "coluna": "apelido"}],
    )


def _chamada(nome, argumentos, numero=1):
    return {"model": "openai/gpt-oss-120b", "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"chamada-{numero}",
            "type": "function",
            "function": {"name": nome, "arguments": json.dumps(argumentos)},
        }]},
    }]}


def _resposta_json(conteudo):
    return {"model": "openai/gpt-oss-120b", "choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(conteudo)},
    }]}


def _resposta_texto(conteudo):
    return {"model": "openai/gpt-oss-120b", "choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": conteudo},
    }]}


def _resultado_planejado(arquivo):
    return {
        "status": "planejado",
        "justificativa": "A consulta usa a coluna removida.",
        "perguntas": [],
        "plano": {
            "acoes": [{
                "arquivo": arquivo,
                "operacao": "alterar",
                "descricao": "Ajustar a projeção SQL para excluir o apelido.",
            }],
            "testes_previstos": ["Verificar a consulta de clientes."],
        },
    }


def test_planejamento_usa_reserva_quando_groq_falha_ao_gerar_chamada(tmp_path, evento, monkeypatch):
    (tmp_path / "clientes.sql").write_text("SELECT apelido FROM clientes;\n", encoding="utf-8")
    principal = ChatGroq(model="openai/gpt-oss-120b", api_key="chave-de-teste")
    reserva = ChatGroq(model="openai/gpt-oss-20b", api_key="chave-de-teste")
    monkeypatch.setattr(planejamento, "criar_modelos_groq", lambda: (principal, reserva))
    erro = BadRequestError(
        "Resposta sem uma chamada válida",
        response=Response(400, request=Request("POST", "https://api.groq.invalid")),
        body={"error": {"code": "output_parse_failed"}},
    )

    with (
        patch.object(principal.client, "create", side_effect=[
            erro, _resposta_json(_resultado_planejado("clientes.sql")),
        ]) as tentativa,
        patch.object(reserva.client, "create", return_value=_resposta_texto(
            "clientes.sql consulta a coluna apelido removida. Atualizar a consulta SQL."
        )) as segunda,
    ):
        resultado = planejar_correcao(evento, analisar_schema(evento), RelatorioImpacto(), tmp_path)

    assert resultado.status == "planejado"
    assert tentativa.call_count == 2
    segunda.assert_called_once()


def test_agente_investiga_com_tools_de_leitura_e_entrega_plano(tmp_path, evento):
    projeto = tmp_path / "projeto"
    projeto.mkdir()
    sql = "SELECT apelido FROM clientes;\n"
    arquivo = projeto / "clientes.sql"
    arquivo.write_text(sql, encoding="utf-8")
    (projeto / ".env").write_text("SEGREDO=segredo-simulado\n", encoding="utf-8")
    (tmp_path / "fora.txt").write_text("texto-fora-do-projeto\n", encoding="utf-8")
    impacto = RelatorioImpacto(evidencias=[{
        "arquivo": "clientes.sql", "linha": 1, "trecho": sql.strip(),
        "termos": ["apelido", "clientes"], "metodo": "texto",
    }, {
        "arquivo": ".env", "linha": 1, "trecho": "segredo-simulado",
        "termos": ["clientes"], "metodo": "texto",
    }])
    modelo = ChatGroq(model="openai/gpt-oss-120b", api_key="chave-de-teste")
    passos = [
        ("listar_pasta_projeto", {}, None),
        ("ler_trecho_projeto", {"arquivo": ".env"}, {"itens"}),
        ("ler_trecho_projeto", {"arquivo": "../fora.txt"}, {"erro"}),
        ("ler_trecho_projeto", {"arquivo": "clientes.sql"}, {"erro"}),
        ("ResultadoPlanejamento", _resultado_planejado("clientes.sql"), {"trecho"}),
    ]
    chamadas = 0

    def responder(**kwargs):
        nonlocal chamadas
        mensagens = kwargs["messages"]
        if "tools" not in kwargs:
            assert kwargs["response_format"]["type"] == "json_schema"
            assert kwargs["response_format"]["json_schema"]["strict"] is True
            assert "segredo-simulado" not in json.dumps(mensagens)
            assert "texto-fora-do-projeto" not in json.dumps(mensagens)
            chamadas += 1
            return _resposta_json(_resultado_planejado("clientes.sql"))

        assert {funcao["function"]["name"] for funcao in kwargs["tools"]} == {
            "listar_pasta_projeto", "ler_trecho_projeto",
        }
        assert "response_format" not in kwargs
        assert "segredo-simulado" not in json.dumps(mensagens)
        assert "texto-fora-do-projeto" not in json.dumps(mensagens)
        if chamadas == len(passos) - 1:
            chamadas += 1
            return _resposta_texto("clientes.sql usa apelido na consulta afetada; atualizar a projeção.")
        nome, argumentos, esperado = passos[chamadas]
        if esperado:
            retorno = json.loads(mensagens[-1]["content"])
            assert esperado <= retorno.keys()
            if "itens" in esperado:
                assert [item["caminho"] for item in retorno["itens"]] == ["clientes.sql"]
            if "trecho" in esperado:
                assert sql.strip() in retorno["trecho"]
        chamadas += 1
        return _chamada(nome, argumentos, chamadas)

    with patch.object(modelo.client, "create", side_effect=responder):
        resultado = planejar_correcao(evento, analisar_schema(evento), impacto, projeto, modelo=modelo)

    assert resultado.status == "planejado"
    assert resultado.plano.acoes[0].arquivo == "clientes.sql"
    assert arquivo.read_text(encoding="utf-8") == sql
    assert chamadas == len(passos) + 1


@pytest.mark.parametrize("arquivo", ["../fora.py", ".env", "inexistente.py"])
def test_recusa_plano_que_indica_arquivo_invalido(tmp_path, evento, arquivo):
    modelo = ChatGroq(model="openai/gpt-oss-120b", api_key="chave-de-teste")
    resposta = _resposta_json(_resultado_planejado(arquivo))
    with patch.object(modelo.client, "create", return_value=resposta), pytest.raises(ValueError):
        planejar_correcao(evento, analisar_schema(evento), RelatorioImpacto(), tmp_path, modelo=modelo)


def test_consolida_acoes_compativeis_no_mesmo_arquivo(tmp_path):
    (tmp_path / "app.py").write_text("def endpoint(): pass\n", encoding="utf-8")
    plano = PlanoCorrecao(acoes=[
        AcaoCorrecao(arquivo="app.py", operacao="alterar", descricao="Atualizar a consulta."),
        AcaoCorrecao(arquivo="./app.py", operacao="alterar", descricao="Atualizar o modelo de entrada."),
    ])

    _validar_arquivos_do_plano(plano, tmp_path)

    assert len(plano.acoes) == 1
    assert plano.acoes[0].arquivo == "app.py"
    assert "Atualizar a consulta." in plano.acoes[0].descricao
    assert "Atualizar o modelo de entrada." in plano.acoes[0].descricao


def test_rejeita_operacoes_incompativeis_no_mesmo_arquivo(tmp_path):
    (tmp_path / "app.py").write_text("def endpoint(): pass\n", encoding="utf-8")
    plano = PlanoCorrecao(acoes=[
        AcaoCorrecao(arquivo="app.py", operacao="alterar", descricao="Mudar o endpoint."),
        AcaoCorrecao(arquivo="./app.py", operacao="excluir", descricao="Remover o módulo."),
    ])

    with pytest.raises(ValueError, match="operações incompatíveis"):
        _validar_arquivos_do_plano(plano, tmp_path)


def test_esclarecimento_precisa_de_pergunta_e_nao_de_plano():
    with pytest.raises(ValidationError):
        ResultadoPlanejamento(
            status="precisa_esclarecimento", justificativa="Faltam regras de negócio.",
            perguntas=[], plano=None,
        )
