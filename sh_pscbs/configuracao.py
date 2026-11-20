"""Configuração compartilhada pelo terminal, monitor e grafo."""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from psycopg2.extensions import parse_dsn

from sh_pscbs.eventos.repositorio import caminho_banco_eventos


def _env(nome: str, padrao: str = "") -> str:
    return os.getenv(nome, "").strip() or padrao


def _inteiro(nome: str, padrao: int, maximo: int) -> int:
    try:
        numero = int(_env(nome, str(padrao)))
    except ValueError:
        raise ValueError(f"{nome} deve ser um número inteiro.") from None
    if not 1 <= numero <= maximo:
        raise ValueError(f"{nome} deve estar entre 1 e {maximo}.")
    return numero


def resolver_pasta_projeto() -> Path:
    caminho = _env("PROJECT_PATH") or _env("PASTA")
    if not caminho:
        raise ValueError("Configure PROJECT_PATH com a raiz do projeto monitorado.")
    pasta = Path(caminho).expanduser().resolve()
    if not pasta.is_dir():
        raise ValueError("PROJECT_PATH deve apontar para uma pasta existente.")
    return pasta


@dataclass(frozen=True)
class Configuracao:
    pasta_projeto: Path
    projeto_id: str
    db_url: str = field(repr=False)
    banco_id: str
    nome_schema: str
    eventos_db: Path
    pasta_propostas: Path
    intervalo: int = 5
    imagem_teste: str = ""
    timeout_testes: int = 120
    max_tentativas: int = 2

    @classmethod
    def do_ambiente(cls) -> "Configuracao":
        pasta = resolver_pasta_projeto()
        db_url = _env("DB_URL")
        if not db_url:
            raise ValueError("Configure DB_URL com a conexão PostgreSQL.")
        try:
            dsn = parse_dsn(db_url)
        except Exception:
            raise ValueError("DB_URL não é uma conexão PostgreSQL válida.") from None
        host = dsn.get("host") or dsn.get("hostaddr") or _env("PGHOST") or _env("PGHOSTADDR", "localhost")
        porta = dsn.get("port") or _env("PGPORT", "5432")
        banco = dsn.get("dbname") or _env("PGDATABASE") or dsn.get("user") or _env("PGUSER")
        identidade_banco = f"{host}:{porta}/{banco}" if banco else f"{host}:{porta}"
        eventos = caminho_banco_eventos().resolve()
        propostas = Path(_env("PROPOSTAS_DIR", str(eventos.parent / "propostas"))).expanduser().resolve()
        if eventos.is_relative_to(pasta) or propostas.is_relative_to(pasta):
            raise ValueError("O SQLite e as propostas precisam ficar fora do projeto monitorado.")

        caminho_id = hashlib.sha256(os.fsencode(pasta)).hexdigest()[:12]
        return cls(
            pasta_projeto=pasta, projeto_id=_env("PROJECT_ID", f"{pasta.name}-{caminho_id}"),
            db_url=db_url, banco_id=_env("BANCO_ID", identidade_banco), nome_schema=_env("PG_SCHEMA", "public"),
            eventos_db=eventos, pasta_propostas=propostas, intervalo=_inteiro("WATCH_INTERVAL_SECONDS", 5, 3600),
            imagem_teste=_env("IMAGEM_TESTE"), timeout_testes=_inteiro("TEST_TIMEOUT_SECONDS", 120, 1800),
            max_tentativas=_inteiro("MAX_TENTATIVAS", 2, 10),
        )
