import os
import shutil
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from google import genai
from google.genai import types
import re
from typing import Optional
from langchain.tools import tool
from pydantic import BaseModel, Field
from snapshot import verificar_mudancas
from watcher import PASTA

_llm = None

def set_llm(llm):
    global _llm
    _llm = llm


class VerificarMudancasSchema(BaseModel):
    db_url: str = Field(..., description="URL de conexão com o banco. Ex: postgresql://user:senha@host:5432/banco")

class BuscarArquivosAfetadosSchema(BaseModel):
    pasta: str = Field(..., description="Caminho da pasta do projeto para buscar.")
    colunas_removidas: list[str] = Field(..., description="Lista de colunas removidas do banco.")

class SearchFolderOnComputer(BaseModel):
    """Tool to search for a folder on the computer."""

    folder_name: str = Field(..., description="The name of the folder to search for.")


class GetTreeFolder(BaseModel):
    """Tool to get the tree structure of a folder."""

    folder_path: str = Field(..., description="The path of the folder to get the tree structure for.")


class CreateFolder(BaseModel):
    """Tool to create a folder on the computer."""

    folder_path: str = Field(..., description="The path of the folder to create.")


class DeleteFolder(BaseModel):
    """Tool to delete a folder on the computer."""

    folder_path: str = Field(..., description="The path of the folder to delete.")


class MoveFolder(BaseModel):
    """Tool to move a folder on the computer."""

    source_path: str = Field(..., description="The source path of the folder to move.")
    destination_path: str = Field(..., description="The destination path of the folder to move to.")


class CreateAnyFile(BaseModel):
    """Tool to create any file on the computer."""

    file_path: str = Field(..., description="The path of the file to create.")
    content: str = Field(..., description="The content to write to the file.")


class DeleteAnyFile(BaseModel):
    """Tool to delete any file on the computer."""

    file_path: str = Field(..., description="The path of the file to delete.")


class UpdateAnyFile(BaseModel):
    """Tool to update any file on the computer."""

    file_path: str = Field(..., description="The path of the file to update.")
    content: str = Field(..., description="The new content to write to the file.")


class MoveAnyFile(BaseModel):
    """Tool to move any file on the computer."""

    source_path: str = Field(..., description="The source path of the file to move.")
    destination_path: str = Field(..., description="The destination path of the file to move to.")

class ReescreverArquivoSchema(BaseModel):
    arquivo: str = Field(..., description="Caminho completo do arquivo a ser reescrito.")
    diff: str = Field(..., description="Diff do schema retornado pelo verificar_mudancas_schema.")
    tabela: str = Field(..., description="Nome da tabela do banco que foi alterada. Ex: clientes")

class ShowSchemaDatabase(BaseModel):
    """Tool to show the schema of a database."""

    db_host: str = Field(..., description="The host of the database.")
    db_port: int = Field(..., description="The port of the database.")
    db_name: str = Field(..., description="The name of the database.")
    db_user: str = Field(..., description="The username for the database.")
    db_password: str = Field(..., description="The password for the database.")

@tool("buscar_arquivos_afetados", args_schema=BuscarArquivosAfetadosSchema)
def buscar_arquivos_afetados(pasta: str, colunas_removidas: list[str]) -> str:
    """
    Varre os arquivos do projeto e retorna quais referenciam
    as colunas que foram removidas do banco de dados.
    """
    afetados = []

    for root, dirs, files in os.walk(pasta):
        dirs[:] = [d for d in dirs if d not in {".venv", "__pycache__", ".git", "node_modules"}]
        for nome in files:
            if not nome.endswith((".py", ".kt", ".java", ".dart", ".js", ".ts", ".xml")):
                continue
            caminho = os.path.join(root, nome)
            try:
                with open(caminho, encoding="utf-8", errors="ignore") as f:
                    conteudo = f.read()
            except Exception:
                continue
            colunas_encontradas = [c for c in colunas_removidas if c in conteudo]
            if colunas_encontradas:
                afetados.append(f"{caminho} → referencia: {', '.join(colunas_encontradas)}")
    if not afetados:
        return "Nenhum arquivo afetado encontrado."
    return "Arquivos que precisam ser atualizados:\n" + "\n".join(afetados)

@tool("verificar_mudancas_schema", args_schema=VerificarMudancasSchema)
def verificar_mudancas_schema(db_url: str) -> str:
    """
    Verifica se o schema do banco de dados mudou desde o último snapshot.
    """
    return verificar_mudancas(db_url)

@tool("search_folder_on_computer", args_schema=SearchFolderOnComputer)
def search_folder_on_computer(folder_name: str) -> Optional[str]:
    """
    Procura uma pasta pelo nome no computador.
    """

    common_paths = [
        os.path.expanduser("~"),
        "/home",
        "/Users",
        os.getcwd(),
        "/opt",
        "/var",
    ]

    for base_path in common_paths:
        if not os.path.exists(base_path):
            continue

        try:
            for root, dirs, files in os.walk(base_path):
                if folder_name in dirs:
                    return os.path.join(root, folder_name)
        except (PermissionError, OSError):
            continue

    try:
        for root, dirs, files in os.walk("/"):
            if folder_name in dirs:
                return os.path.join(root, folder_name)
    except (PermissionError, OSError):
        pass

    return None


@tool("get_tree_folder", args_schema=GetTreeFolder)
def get_tree_folder(folder_path: str) -> Optional[str]:
    """
    Retorna a estrutura de pastas e arquivos de uma pasta.
    """

    if not os.path.exists(folder_path):
        return f"Erro: A pasta '{folder_path}' não existe."

    if not os.path.isdir(folder_path):
        return f"Erro: '{folder_path}' não é uma pasta."

    tree_structure = []

    for root, dirs, files in os.walk(folder_path):
        level = root.replace(folder_path, "").count(os.sep)
        indent = " " * 4 * level

        tree_structure.append(
            f"{indent}{os.path.basename(root)}/"
        )

        subindent = " " * 4 * (level + 1)

        for file in files:
            tree_structure.append(
                f"{subindent}{file}"
            )

    return "\n".join(tree_structure)


@tool("create_folder", args_schema=CreateFolder)
def create_folder(folder_path: str) -> str:
    """
    Cria uma pasta no caminho especificado.
    """

    try:
        if os.path.exists(folder_path):
            if os.path.isdir(folder_path):
                return f"A pasta '{folder_path}' já existe."
            else:
                return f"Erro: '{folder_path}' existe mas não é uma pasta."

        os.makedirs(folder_path, exist_ok=True)

        return f"Folder created at {folder_path}"

    except Exception as e:
        return f"Error creating folder: {str(e)}"


@tool("delete_folder", args_schema=DeleteFolder)
def delete_folder(folder_path: str) -> str:
    """
    Exclui uma pasta vazia.
    """

    try:
        if not os.path.exists(folder_path):
            return f"Erro: A pasta '{folder_path}' não existe."

        if not os.path.isdir(folder_path):
            return f"Erro: '{folder_path}' não é uma pasta."

        os.rmdir(folder_path)

        return f"Folder deleted at {folder_path}"

    except OSError as e:
        if e.errno == 39 or "Directory not empty" in str(e):
            return f"Erro: A pasta '{folder_path}' não está vazia. Use uma ferramenta recursiva para excluir seu conteúdo."

        return f"Error deleting folder: {str(e)}"

    except Exception as e:
        return f"Error deleting folder: {str(e)}"


@tool("move_folder", args_schema=MoveFolder)
def move_folder(
    source_path: str,
    destination_path: str
) -> str:
    """
    Move uma pasta de um local para outro.
    """

    try:
        if not os.path.exists(source_path):
            return f"Erro: A pasta de origem '{source_path}' não existe."

        if not os.path.isdir(source_path):
            return f"Erro: '{source_path}' não é uma pasta."

        if os.path.exists(destination_path):
            return f"Erro: O destino '{destination_path}' já existe."

        os.rename(
            source_path,
            destination_path
        )

        return (
            f"Folder moved from "
            f"{source_path} to {destination_path}"
        )

    except Exception as e:
        return f"Error moving folder: {str(e)}"


@tool("create_any_file", args_schema=CreateAnyFile)
def create_any_file(
    file_path: str,
    content: str
) -> str:
    """
    Cria um arquivo e escreve conteúdo nele.
    """

    try:
        dir_path = os.path.dirname(file_path)

        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as file:
            file.write(content)

        return f"File created at {file_path}"

    except Exception as e:
        return f"Error creating file: {str(e)}"


@tool("delete_any_file", args_schema=DeleteAnyFile)
def delete_any_file(file_path: str) -> str:
    """
    Exclui um arquivo.
    """

    try:
        if not os.path.exists(file_path):
            return f"Erro: O arquivo '{file_path}' não existe."

        if not os.path.isfile(file_path):
            return f"Erro: '{file_path}' não é um arquivo."

        os.remove(file_path)

        return f"File deleted at {file_path}"

    except Exception as e:
        return f"Error deleting file: {str(e)}"


@tool("update_any_file", args_schema=UpdateAnyFile)
def update_any_file(
    file_path: str,
    content: str
) -> str:
    """
    Substitui o conteúdo de um arquivo.
    """

    try:
        if not os.path.exists(file_path):
            return f"Erro: O arquivo '{file_path}' não existe."

        if not os.path.isfile(file_path):
            return f"Erro: '{file_path}' não é um arquivo."

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as file:
            file.write(content)

        return f"File updated at {file_path}"

    except Exception as e:
        return f"Error updating file: {str(e)}"


@tool("move_any_file", args_schema=MoveAnyFile)
def move_any_file(
    source_path: str,
    destination_path: str
) -> str:
    """
    Move um arquivo de um local para outro.
    """

    try:
        if not os.path.exists(source_path):
            return f"Erro: O arquivo de origem '{source_path}' não existe."

        if not os.path.isfile(source_path):
            return f"Erro: '{source_path}' não é um arquivo."

        if os.path.exists(destination_path):
            return f"Erro: O arquivo de destino '{destination_path}' já existe."

        dest_dir = os.path.dirname(destination_path)

        if dest_dir and not os.path.exists(dest_dir):
            os.makedirs(dest_dir, exist_ok=True)

        os.rename(
            source_path,
            destination_path
        )

        return (
            f"File moved from "
            f"{source_path} to {destination_path}"
        )

    except Exception as e:
        return f"Error moving file: {str(e)}"

@tool("show_schema_database", args_schema=ShowSchemaDatabase)
def show_schema_database(
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_password: str
) -> Optional[str]:
    """
    Mostra o esquema de um banco de dados PostgreSQL.
    """

    try:
        connection = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password
        )

        cursor = connection.cursor()

        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)

        tables = cursor.fetchall()

        schema_info = []

        for table in tables:
            table_name = table[0]
            schema_info.append(f"Table: {table_name}")

            cursor.execute(f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = '{table_name}'
            """)

            columns = cursor.fetchall()

            for column in columns:
                column_name, data_type = column
                schema_info.append(f"  Column: {column_name}, Type: {data_type}")

        cursor.close()
        connection.close()

        return "\n".join(schema_info)

    except Exception as e:
        return f"Error showing database schema: {str(e)}"
    
@tool("reescrever_arquivo_afetado", args_schema=ReescreverArquivoSchema)
def reescrever_arquivo_afetado(arquivo: str, diff: str, tabela: str) -> str:
    """Lê o arquivo, usa o LLM para reescrever com base no diff e salva."""
    import traceback
    try:
        if _llm is None:
            return "Erro: _llm não inicializado."

        with open(arquivo, encoding="utf-8") as f:
            codigo_atual = f.read()

        nome_arquivo = os.path.basename(arquivo)

        prompt = f"""Você é um engenheiro de software sênior.
        O banco de dados PostgreSQL foi alterado conforme o diff abaixo.
        Seu trabalho é atualizar o arquivo `{nome_arquivo}` para refletir essas mudanças.

        === TABELA AFETADA ===
        {tabela}

        === DIFF DO BANCO ===
        {diff}

        === REGRAS OBRIGATÓRIAS ===
        - Este arquivo pode ou não estar relacionado à tabela `{tabela}`. Avalie o código antes de alterar.
        - Se o arquivo NÃO lida com a tabela `{tabela}`, retorne o código exatamente como está, sem nenhuma alteração.
        - Linhas com [-] indicam colunas REMOVIDAS: apague toda referência a elas (parâmetros, chaves de dicionário, validações, comentários).
        - Linhas com [+] indicam colunas ADICIONADAS: inclua-as como novos parâmetros nas funções, novas chaves nos dicionários de payload, e adicione validação básica se já houver validação no arquivo.
        - Linhas com [+] Tabela adicionada: ignore, não é responsabilidade deste arquivo.
        - Preserve toda a lógica existente que não está relacionada às colunas alteradas.
        - Preserve os comentários e o estilo do código original.
        - NÃO adicione imports que não existiam.
        - NÃO invente lógica nova além do necessário para integrar as colunas.

        === CÓDIGO ATUAL ({nome_arquivo}) ===
        {codigo_atual}

        Retorne APENAS o código corrigido, sem explicações, sem markdown, sem ```python.
"""
        resposta = _llm.invoke(prompt)
        conteudo = resposta.content
        if isinstance(conteudo, list):
            conteudo = " ".join(b.get("text", "") for b in conteudo if isinstance(b, dict))
        codigo_novo = conteudo.strip()

        if codigo_novo.startswith("```"):
            linhas = [l for l in codigo_novo.splitlines() if not l.startswith("```")]
            codigo_novo = "\n".join(linhas).strip()
            
        backup_dir = os.path.join(PASTA, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        nome_backup = os.path.basename(arquivo) + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_path = os.path.join(backup_dir, nome_backup)
        shutil.copy2(arquivo, backup_path)
        print(f"[Backup] Salvo em: {backup_path}")

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(codigo_novo)

        return f"Arquivo reescrito com sucesso: {arquivo}"

    except Exception as e:
        print(f"\n[ERRO] reescrever_arquivo_afetado:\n{traceback.format_exc()}")
        return f"Erro ao reescrever {arquivo}: {str(e)}"
    
TOOLS = [
    search_folder_on_computer,
    get_tree_folder,
    create_folder,
    delete_folder,
    move_folder,
    create_any_file,
    delete_any_file,
    update_any_file,
    move_any_file,
    verificar_mudancas_schema,
    buscar_arquivos_afetados,
    reescrever_arquivo_afetado,
    show_schema_database
]