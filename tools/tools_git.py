#tools que rodam no terminal esolhidas pelo agente
import os
from langchain.tools import tool

@tool("git_status", return_direct=True)
def git_status() -> str:
    """
    Retorna o status do repositório Git.
    """
    try:
        status = os.popen("git status").read()
        return status
    except Exception as e:
        return f"Erro ao obter o status do Git: {e}"

tool("git_add", return_direct=True)
def git_add(file_path: str) -> str:
    """
    Adiciona um arquivo ao índice do Git.
    """
    try:
        result = os.popen(f"git add {file_path}").read()
        return f"Arquivo '{file_path}' adicionado ao índice do Git."
    except Exception as e:
        return f"Erro ao adicionar o arquivo '{file_path}' ao índice do Git: {e}"

tool("git_commit", return_direct=True)
def git_commit(message: str) -> str:
    """
    Cria um commit no Git com a mensagem fornecida.
    """
    try:
        result = os.popen(f"git commit -m \"{message}\"").read()
        return f"Commit criado com a mensagem: '{message}'."
    except Exception as e:
        return f"Erro ao criar o commit: {e}"

def git_push() -> str:
    """
    Realiza o push das alterações para o repositório remoto.
    """
    try:
        result = os.popen("git push").read()
        return "Alterações enviadas para o repositório remoto."
    except Exception as e:
        return f"Erro ao realizar o push: {e}"

def git_pull() -> str:
    """
    Realiza o pull das alterações do repositório remoto.
    """
    try:
        result = os.popen("git pull").read()
        return "Alterações recebidas do repositório remoto."
    except Exception as e:
        return f"Erro ao realizar o pull: {e}"

def git_checkout(branch_name: str) -> str:
    """
    Realiza o checkout para a branch especificada.
    """
    try:
        result = os.popen(f"git checkout {branch_name}").read()
        return f"Checkout realizado para a branch '{branch_name}'."
    except Exception as e:
        return f"Erro ao realizar o checkout para a branch '{branch_name}': {e}"

def git_cria_troca_branch(branch_name: str) -> str:
    """
    Cria uma nova branch e realiza o checkout para ela.
    """
    try:
        result = os.popen(f"git checkout -b {branch_name}").read()
        return f"Nova branch '{branch_name}' criada e checkout realizado."
    except Exception as e:
        return f"Erro ao criar a nova branch '{branch_name}': {e}"
