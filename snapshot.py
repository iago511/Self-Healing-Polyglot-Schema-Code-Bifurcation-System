import json
import os
import psycopg2
from datetime import datetime, timezone


SNAPSHOTS_DIR = "schema_snapshots"


def ler_schema(url):
    """Lê o schema atual do banco."""
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    schema = {}
    for tabela, coluna, tipo in rows:
        if tabela not in schema:
            schema[tabela] = {}
        schema[tabela][coluna] = tipo

    return schema


def salvar_snapshot(schema):
    """Salva o schema em JSON com timestamp."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    
    ultimo_snapshot = carregar_ultimo_snapshot()
    if ultimo_snapshot is not None and schema == ultimo_snapshot:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    caminho = f"{SNAPSHOTS_DIR}/schema_{timestamp}.json"

    with open(caminho, "w") as f:
        json.dump(schema, f, indent=2)

    with open(f"{SNAPSHOTS_DIR}/schema_latest.json", "w") as f:
        json.dump(schema, f, indent=2)

    return caminho


def carregar_ultimo_snapshot():
    """Carrega o snapshot mais recente. Retorna None se não existir."""
    caminho = f"{SNAPSHOTS_DIR}/schema_latest.json"
    if not os.path.exists(caminho):
        return None
    with open(caminho) as f:
        return json.load(f)


def comparar(antigo, novo):
    """Compara dois schemas e mostra o que mudou."""
    linhas = []

    for t in set(novo) - set(antigo):
        linhas.append(f"[+] Tabela adicionada: {t}")

    for t in set(antigo) - set(novo):
        linhas.append(f"[-] Tabela removida: {t}")

    for t in set(antigo) & set(novo):
        for c in set(novo[t]) - set(antigo[t]):
            linhas.append(f"[+] {t}.{c} adicionada ({novo[t][c]})")
        for c in set(antigo[t]) - set(novo[t]):
            linhas.append(f"[-] {t}.{c} removida ({antigo[t][c]})")

    return "\n".join(linhas) if linhas else "Nenhuma mudança detectada."


def verificar_mudancas(url):
    """Fluxo completo: lê o banco, compara com o último snapshot, salva."""
    antigo  = carregar_ultimo_snapshot()
    novo    = ler_schema(url)
    caminho = salvar_snapshot(novo)

    if antigo is None:
        return f"Primeiro snapshot salvo em {caminho}."

    return comparar(antigo, novo)