import threading
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent

from sh_pscbs.agentes.prompts import ORQUESTRADOR_ARQUIVOS_PROMPT
from sh_pscbs.modelos.llms import (
    criar_modelo_codigo,
    criar_modelos_groq,
    fallback_do_agente,
)
from sh_pscbs.monitoramento.watcher import rodar_watcher
from tools.tools import TOOLS, set_llm


def criar_orquestrador():
    especialista, rapido = criar_modelos_groq()
    set_llm(criar_modelo_codigo())
    return create_agent(
        model=especialista,
        tools=TOOLS,
        system_prompt=ORQUESTRADOR_ARQUIVOS_PROMPT,
        middleware=[fallback_do_agente(rapido)],
    )


def invocar_agente(orquestrador, mensagem: str, thread_id: str = "session_id"):
    try:
        resposta = orquestrador.invoke(
            {"messages": [{"role": "human", "content": mensagem}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        if resposta and "messages" in resposta:
            ultima = resposta["messages"][-1]
            conteudo = ultima.content if hasattr(ultima, "content") else str(ultima)
            if isinstance(conteudo, list):
                conteudo = " ".join(
                    bloco.get("text", "") for bloco in conteudo
                    if isinstance(bloco, dict)
                )
            print(f"\nAssistente: {conteudo}\n")
            return conteudo
    except Exception as e:
        print(f"Erro ao consumir a API: {e}")
    return ""


def main():
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    try:
        orquestrador = criar_orquestrador()
    except ValueError as erro:
        raise SystemExit(str(erro)) from None

    def conversar(mensagem, thread_id="session_id"):
        return invocar_agente(orquestrador, mensagem, thread_id)

    threading.Thread(target=rodar_watcher, args=(conversar,), daemon=True).start()
    try:
        while True:
            mensagem = input("> ").strip()
            if mensagem.lower() in ("sair", "end", "fim", "tchau", "bye"):
                break
            if mensagem:
                conversar(mensagem)
    except (EOFError, KeyboardInterrupt):
        pass
    print("Encerrando a conversa...")


if __name__ == "__main__":
    main()
