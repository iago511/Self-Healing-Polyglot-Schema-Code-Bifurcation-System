from prompts import ORQUESTRADOR_ARQUIVOS_PROMPT
from tools import TOOLS, set_llm
import os
import threading
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

load_dotenv()

llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-3.5-flash-lite",
    temperature=0.7,
    top_p=0.95,
    api_key=os.getenv("GEMINI_API_KEY"),
)

llm_codigo = ChatOllama(
    model="qwen2.5-coder:3b",
    temperature=0,
)

set_llm(llm_codigo) 

orquestrador_app = create_agent(
    model=llm_gemini,            
    tools=TOOLS,
    system_prompt=ORQUESTRADOR_ARQUIVOS_PROMPT,
)


def invocar_agente(mensagem: str, thread_id: str = "session_id"):
    try:
        resposta = orquestrador_app.invoke(
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

def iniciar_watcher():
    from watcher import rodar_watcher
    rodar_watcher(invocar_agente)

thread_watcher = threading.Thread(target=iniciar_watcher, daemon=True)
thread_watcher.start()

while True:
    user_input = input("> ")

    if user_input.lower() in ("sair", "end", "fim", "tchau", "bye"):
        print("Encerrando a conversa...")
        break

    invocar_agente(user_input)