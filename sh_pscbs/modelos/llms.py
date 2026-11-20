import os

from groq import APIConnectionError, InternalServerError, RateLimitError
from langchain.agents.middleware import wrap_model_call
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

MODELO_ESPECIALISTA = "openai/gpt-oss-120b"
MODELO_RAPIDO = "openai/gpt-oss-20b"
MODELO_CODIGO = "qwen2.5-coder:3b"

_ERROS_TRANSITORIOS = (APIConnectionError, InternalServerError, RateLimitError)


def criar_modelos_groq():
    """Cria os perfis especialista e rápido com a chave do ambiente."""
    chave = os.getenv("GROQ_API_KEY", "").strip()
    if not chave:
        raise ValueError("Configure GROQ_API_KEY no ambiente ou no arquivo .env.")

    comuns = {"api_key": chave, "temperature": 0, "timeout": 30, "max_retries": 1}
    especialista = ChatGroq(
        model=MODELO_ESPECIALISTA, reasoning_effort="medium", max_tokens=4096, **comuns
    )
    rapido = ChatGroq(
        model=MODELO_RAPIDO, reasoning_effort="low", max_tokens=2048, **comuns
    )
    return especialista, rapido


def fallback_do_agente(reserva):
    """Usa o modelo reserva em falhas transitórias, mantendo as tools do agente."""
    @wrap_model_call
    def tentar_reserva(request, handler):
        try:
            return handler(request)
        except _ERROS_TRANSITORIOS:
            return handler(request.override(model=reserva))

    return tentar_reserva


def criar_modelo_codigo():
    """Cria o modelo Ollama usado pelas ferramentas que editam código."""
    return ChatOllama(
        model=MODELO_CODIGO,
        temperature=0,
        base_url=os.getenv("OLLAMA_BASE_URL") or None,
    )
