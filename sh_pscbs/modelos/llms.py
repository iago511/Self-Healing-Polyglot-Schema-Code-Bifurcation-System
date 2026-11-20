import os

from groq import APIConnectionError, APIError, BadRequestError, InternalServerError, RateLimitError
from langchain.agents.middleware import wrap_model_call
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

MODELO_ESPECIALISTA = "openai/gpt-oss-120b"
MODELO_RAPIDO = "openai/gpt-oss-20b"
MODELO_CODIGO = "qwen2.5-coder:3b"

_ERROS_COM_RESERVA = (APIConnectionError, InternalServerError, RateLimitError, BadRequestError)
_ERROS_GERACAO = {"output_parse_failed", "tool_use_failed"}


def codigo_erro_groq(erro: APIError) -> str | None:
    corpo = erro.body
    if isinstance(corpo, dict):
        dados = corpo.get("error", corpo)
        if isinstance(dados, dict) and isinstance(dados.get("code"), str):
            return dados["code"]
    return None


def criar_modelos_groq():
    """Cria os perfis especialista e rápido com a chave do ambiente."""
    chave = os.getenv("GROQ_API_KEY", "").strip()
    if not chave:
        raise ValueError("Configure GROQ_API_KEY no ambiente ou no arquivo .env.")

    comuns = {"api_key": chave, "temperature": 0, "timeout": 30, "max_retries": 1}
    especialista = _modelo_groq(os.getenv("GROQ_MODELO_ESPECIALISTA") or MODELO_ESPECIALISTA, "medium", 4096, comuns)
    rapido = _modelo_groq(os.getenv("GROQ_MODELO_RAPIDO") or MODELO_RAPIDO, "low", 2048, comuns)
    return especialista, rapido


def _modelo_groq(nome, esforco, limite, comuns):
    opcoes = {}
    if nome.startswith("openai/gpt-oss-"):
        opcoes = {"reasoning_effort": esforco, "model_kwargs": {"include_reasoning": False}}
    return ChatGroq(model=nome, max_tokens=limite, **opcoes, **comuns)


def fallback_do_agente(reserva):
    """Usa o modelo reserva quando o principal falha na conexão ou na geração."""
    @wrap_model_call
    def tentar_reserva(request, handler):
        try:
            return handler(request)
        except _ERROS_COM_RESERVA as erro:
            if isinstance(erro, BadRequestError) and codigo_erro_groq(erro) not in _ERROS_GERACAO:
                raise
        return handler(request.override(model=reserva))

    return tentar_reserva


def criar_modelo_codigo():
    """Cria o modelo Ollama usado pelas ferramentas que editam código."""
    try:
        timeout = int(os.getenv("OLLAMA_TIMEOUT_SECONDS") or "300")
    except ValueError:
        raise ValueError("OLLAMA_TIMEOUT_SECONDS deve ser um inteiro entre 1 e 1800.") from None
    if not 1 <= timeout <= 1800:
        raise ValueError("OLLAMA_TIMEOUT_SECONDS deve ser um inteiro entre 1 e 1800.")
    return ChatOllama(
        model=os.getenv("OLLAMA_MODELO") or MODELO_CODIGO,
        temperature=0,
        base_url=os.getenv("OLLAMA_BASE_URL") or None,
        client_kwargs={"timeout": timeout},
    )
