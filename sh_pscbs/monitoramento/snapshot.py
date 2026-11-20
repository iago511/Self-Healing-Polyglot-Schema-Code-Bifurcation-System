import json
import hashlib
import os
import psycopg2
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sh_pscbs.orquestracao.estado import MudancaSchema

SNAPSHOTS_DIR = Path(
    os.getenv("SNAPSHOTS_DIR") or Path(__file__).resolve().parents[2] / "schema_snapshots"
)


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


def ler_schema_detalhado(url: str, nome_schema: str) -> dict:
    """Lê metadados de tabelas do schema com uma sessão somente leitura."""
    with closing(psycopg2.connect(url, connect_timeout=5, options="-c statement_timeout=10000")) as conexao:
        conexao.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conexao.cursor() as cursor:
            cursor.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (nome_schema,))
            if cursor.fetchone() is None:
                raise ValueError("O schema configurado não existe ou não está visível para a conta do monitor.")
            cursor.execute(
                """
                SELECT t.table_name, c.column_name, c.data_type, c.is_nullable,
                       c.column_default, c.udt_schema, c.udt_name,
                       c.character_maximum_length, c.numeric_precision, c.numeric_scale,
                       c.is_identity, c.is_generated
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c
                  ON (t.table_schema, t.table_name) = (c.table_schema, c.table_name)
                WHERE t.table_schema = %s AND t.table_type = 'BASE TABLE'
                ORDER BY t.table_name, c.ordinal_position
                """,
                (nome_schema,),
            )
            tabelas = {}
            for tabela, coluna, tipo, nulo, padrao, tipo_schema, tipo_nome, tamanho, precisao, escala, identidade, gerada in cursor:
                colunas = tabelas.setdefault(tabela, {})
                if coluna is None:
                    continue
                colunas[coluna] = {
                    "tipo": tipo, "aceita_nulo": nulo == "YES", "valor_padrao": padrao,
                    "tipo_schema": tipo_schema, "tipo_nome": tipo_nome, "tamanho": tamanho,
                    "precisao": precisao, "escala": escala, "identidade": identidade, "gerada": gerada,
                }
            return tabelas


def serializar_schema(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identificar_schema(schema: dict) -> str:
    return hashlib.sha256(serializar_schema(schema).encode("utf-8")).hexdigest()


def comparar_schema_detalhado(antigo: dict, novo: dict) -> list[MudancaSchema]:
    mudancas = []
    for tabela in sorted(antigo.keys() | novo.keys()):
        if tabela not in antigo:
            mudancas.append(MudancaSchema(tipo="tabela_adicionada", tabela=tabela, valores_posteriores=novo[tabela]))
        elif tabela not in novo:
            mudancas.append(MudancaSchema(tipo="tabela_removida", tabela=tabela, valores_anteriores=antigo[tabela]))
        else:
            for coluna in sorted(antigo[tabela].keys() | novo[tabela].keys()):
                antes, depois = antigo[tabela].get(coluna), novo[tabela].get(coluna)
                if antes == depois:
                    continue
                tipo = "coluna_adicionada" if antes is None else "coluna_removida" if depois is None else "coluna_alterada"
                mudancas.append(MudancaSchema(tipo=tipo, tabela=tabela, coluna=coluna,
                                              valores_anteriores=antes or {}, valores_posteriores=depois or {}))
    return mudancas
