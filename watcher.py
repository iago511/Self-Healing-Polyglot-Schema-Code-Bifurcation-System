import time
from snapshot import verificar_mudancas
import dotenv

DB_URL    = dotenv.get_key(dotenv.find_dotenv(), "DB_URL")
PASTA     = r"C:\Users\iagodiniz-ieg\Downloads\sh_test"
INTERVALO = 5

def extrair_mudancas(diff: str) -> dict:
    removidas, adicionadas, tabelas_novas = [], [], []
    tabelas = set()
    for linha in diff.splitlines():
        linha = linha.strip()
        if linha.startswith("[-]") and "." in linha:
            tabela = linha.split(".")[0].replace("[-]", "").strip()
            coluna = linha.split(".")[1].split(" ")[0]
            removidas.append(coluna)
            tabelas.add(tabela)
        elif linha.startswith("[+]") and "." in linha:
            tabela = linha.split(".")[0].replace("[+]", "").strip()
            coluna = linha.split(".")[1].split(" ")[0]
            adicionadas.append(coluna)
            tabelas.add(tabela)
        elif linha.startswith("[+] Tabela adicionada:"):
            nome = linha.replace("[+] Tabela adicionada:", "").strip()
            tabelas_novas.append(nome)
    return {
        "removidas": removidas,
        "adicionadas": adicionadas,
        "tabelas": list(tabelas),
        "tabelas_novas": tabelas_novas,
    }
def rodar_watcher(invocar_agente):
    print("[Watcher] Iniciado.\n")
    while True:
        resultado = verificar_mudancas(DB_URL)

        if "Nenhuma mudança" not in resultado and "Primeiro snapshot" not in resultado:
            print(f"\n[Watcher] Mudança detectada:\n{resultado}\n")
            mudancas = extrair_mudancas(resultado)
            tabelas_str = ", ".join(mudancas["tabelas"])

            if mudancas["removidas"]:
                mensagem_remocao = f"""
O banco removeu colunas. Diff:
{resultado}

Tabelas afetadas: {tabelas_str}
Colunas removidas: {mudancas['removidas']}

Execute:
1. Busque em {PASTA} arquivos que referenciam essas colunas.
2. Para cada arquivo encontrado, chame reescrever_arquivo_afetado passando tabela='{tabelas_str}'.
3. Informe o que foi corrigido.
"""
                invocar_agente(mensagem_remocao, thread_id="watcher_remocao")

            if mudancas["adicionadas"]:
                mensagem_adicao = f"""
O banco adicionou novas colunas. Diff:
{resultado}

Tabelas afetadas: {tabelas_str}
Colunas adicionadas: {mudancas['adicionadas']}

Execute:
1. Liste todos os arquivos .py em {PASTA} usando get_tree_folder.
2. Para cada arquivo .py encontrado, chame reescrever_arquivo_afetado passando tabela='{tabelas_str}'.
3. Informe quais arquivos foram atualizados.
"""
                invocar_agente(mensagem_adicao, thread_id="watcher_adicao")

            if mudancas["tabelas_novas"]:
                for tabela_nova in mudancas["tabelas_novas"]:
                    mensagem_nova_tabela = f"""
O banco criou uma nova tabela chamada `{tabela_nova}`. Diff:
{resultado}

Esta tabela ainda não existe no projeto em {PASTA}.

Execute:
1. Leia os arquivos .py existentes em {PASTA} usando get_tree_folder para entender o padrão do projeto.
2. Leia o conteúdo de um arquivo existente (ex: api.py ou models.py) para entender o estilo do código.
3. Crie um arquivo novo chamado `{tabela_nova}.py` em {PASTA} seguindo o mesmo padrão dos arquivos existentes, contendo:
   - Uma classe ou modelo representando a tabela `{tabela_nova}`.
   - Funções de criar, buscar e listar registros dessa tabela.
   - Validações básicas se o arquivo de referência tiver.
4. Informe o caminho do arquivo criado.
"""
                    invocar_agente(mensagem_nova_tabela, thread_id=f"watcher_nova_{tabela_nova}")

                    mensagem_integrar = f"""
O arquivo `{tabela_nova}.py` acabou de ser criado em {PASTA}.

Agora verifique se algum arquivo existente no projeto precisa ser atualizado para integrar com ele.

Execute:
1. Liste todos os arquivos .py em {PASTA} usando get_tree_folder.
2. Leia o conteúdo de cada arquivo existente (exceto `{tabela_nova}.py`).
3. Para cada arquivo, avalie se ele deveria importar ou referenciar `{tabela_nova}.py` com base no que já faz.
   Exemplos: um arquivo de rotas deveria incluir as rotas do novo módulo. Um arquivo de modelos deveria importar a nova classe.
4. Se precisar de atualização, chame reescrever_arquivo_afetado com tabela='{tabela_nova}'.
5. Informe quais arquivos foram atualizados e o que foi adicionado em cada um.
"""
                    invocar_agente(mensagem_integrar, thread_id=f"watcher_integrar_{tabela_nova}")

        time.sleep(INTERVALO)