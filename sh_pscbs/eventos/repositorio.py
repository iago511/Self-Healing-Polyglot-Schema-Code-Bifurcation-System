"""Persistência local dos eventos e do estado da correção."""

import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sh_pscbs.monitoramento.snapshot import comparar_schema_detalhado, identificar_schema, serializar_schema
from sh_pscbs.orquestracao.estado import DecisaoHumana, EstadoCorrecao, EtapaCorrecao, EventoSchema


def caminho_banco_eventos() -> Path:
    """Retorna o caminho configurado ou a pasta de dados do usuário."""
    configurado = os.getenv("EVENTOS_DB_PATH")
    if configurado:
        return Path(configurado).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "SH-PSCBS" / "eventos.db"


@dataclass(frozen=True)
class RegistroEvento:
    estado: EstadoCorrecao
    versao: int
    criado_em: datetime
    atualizado_em: datetime


@dataclass(frozen=True)
class TransicaoEvento:
    versao: int
    etapa: EtapaCorrecao
    tentativas: int
    registrada_em: datetime
    motivo: str | None
    decisao_humana: DecisaoHumana | None


class ConflitoVersaoEvento(RuntimeError):
    """O estado mudou desde a última leitura do evento."""


_ESQUEMA_SQL = """
BEGIN;
CREATE TABLE IF NOT EXISTS eventos (
    id_evento TEXT PRIMARY KEY,
    projeto TEXT NOT NULL,
    etapa TEXT NOT NULL,
    estado_json TEXT NOT NULL,
    versao INTEGER NOT NULL,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS eventos_projeto_criado_em
    ON eventos (projeto, criado_em, id_evento);
CREATE TABLE IF NOT EXISTS schemas_observados (
    projeto TEXT NOT NULL,
    banco TEXT NOT NULL,
    nome_schema TEXT NOT NULL,
    versao TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    PRIMARY KEY (projeto, banco, nome_schema)
);
CREATE TABLE IF NOT EXISTS transicoes (
    id_evento TEXT NOT NULL REFERENCES eventos (id_evento),
    versao INTEGER NOT NULL,
    etapa TEXT NOT NULL,
    tentativas INTEGER NOT NULL,
    registrada_em TEXT NOT NULL,
    motivo TEXT,
    decisao_json TEXT,
    PRIMARY KEY (id_evento, versao)
);
COMMIT;
"""


class RepositorioEventos:
    """Guarda um estado atual por evento e um histórico resumido de transições."""

    def __init__(self, caminho: str | Path | None = None):
        self.caminho = (
            Path(caminho).expanduser() if caminho is not None else caminho_banco_eventos()
        ).resolve()
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conectar()) as conexao:
            conexao.executescript(_ESQUEMA_SQL)

    def _conectar(self) -> sqlite3.Connection:
        conexao = sqlite3.connect(self.caminho, timeout=5)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        return conexao

    @staticmethod
    def _registro(linha: sqlite3.Row) -> RegistroEvento:
        return RegistroEvento(
            estado=EstadoCorrecao.model_validate_json(linha["estado_json"]),
            versao=linha["versao"],
            criado_em=datetime.fromisoformat(linha["criado_em"]),
            atualizado_em=datetime.fromisoformat(linha["atualizado_em"]),
        )

    @staticmethod
    def _registrar_transicao(
        conexao: sqlite3.Connection,
        estado: EstadoCorrecao,
        versao: int,
        agora: datetime,
        motivo: str | None = None,
        decisao: DecisaoHumana | None = None,
    ) -> None:
        conexao.execute(
            """
            INSERT INTO transicoes (
                id_evento, versao, etapa, tentativas, registrada_em, motivo, decisao_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (estado.evento.id_evento, versao, estado.etapa_atual.value, estado.tentativas,
             agora.isoformat(), motivo, decisao.model_dump_json() if decisao else None),
        )

    def registrar_evento(self, evento: EventoSchema) -> RegistroEvento:
        """Cria o estado inicial ou devolve o registro já existente do mesmo evento."""
        estado = EstadoCorrecao.model_validate({"evento": evento.model_dump()})
        evento = estado.evento
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute("BEGIN IMMEDIATE")
            linha = conexao.execute(
                "SELECT * FROM eventos WHERE id_evento = ?", (evento.id_evento,),
            ).fetchone()
            if linha is not None:
                registro = self._registro(linha)
                if registro.estado.evento != evento:
                    raise ValueError("O ID já está associado a outro evento.")
                return registro

            return self._inserir_evento(conexao, estado)

    def _inserir_evento(self, conexao, estado: EstadoCorrecao) -> RegistroEvento:
        evento = estado.evento
        agora = datetime.now(timezone.utc)
        conexao.execute(
            """
            INSERT INTO eventos (id_evento, projeto, etapa, estado_json, versao, criado_em, atualizado_em)
                VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (evento.id_evento, evento.projeto, estado.etapa_atual.value,
             estado.model_dump_json(exclude_none=True, exclude_defaults=True), agora.isoformat(), agora.isoformat()),
        )
        self._registrar_transicao(conexao, estado, 1, agora)
        return RegistroEvento(estado, 1, agora, agora)

    def observar_schema(self, projeto: str, banco: str, nome_schema: str, schema: dict) -> RegistroEvento | None:
        """Persiste o snapshot mais recente e o evento na mesma transação SQLite."""
        versao = identificar_schema(schema)
        escopo = projeto, banco, nome_schema
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute("BEGIN IMMEDIATE")
            anterior = conexao.execute(
                "SELECT versao, schema_json FROM schemas_observados WHERE projeto=? AND banco=? AND nome_schema=?",
                escopo,
            ).fetchone()
            if anterior is not None and anterior["versao"] == versao:
                return None
            conexao.execute(
                """
                INSERT INTO schemas_observados (projeto, banco, nome_schema, versao, schema_json)
                    VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (projeto, banco, nome_schema) DO UPDATE SET
                    versao=excluded.versao, schema_json=excluded.schema_json
                """,
                (*escopo, versao, serializar_schema(schema)),
            )
            if anterior is None:
                return None
            mudancas = comparar_schema_detalhado(json.loads(anterior["schema_json"]), schema)
            if not mudancas:
                return None
            evento = EventoSchema(
                id_evento=uuid4().hex, projeto=projeto, banco=banco, nome_schema=nome_schema,
                versao_snapshot_anterior=anterior["versao"], versao_snapshot_atual=versao, mudancas=mudancas,
            )
            return self._inserir_evento(conexao, EstadoCorrecao(evento=evento))

    def obter_evento(self, id_evento: str) -> RegistroEvento | None:
        with closing(self._conectar()) as conexao:
            linha = conexao.execute(
                "SELECT * FROM eventos WHERE id_evento = ?", (id_evento,),
            ).fetchone()
        return self._registro(linha) if linha is not None else None

    def salvar_estado(
        self, estado: EstadoCorrecao, *, versao_esperada: int, motivo: str | None = None,
    ) -> RegistroEvento:
        """Atualiza estado e histórico juntos, protegendo contra gravações concorrentes."""
        dados = estado.model_dump_json(exclude_none=True, exclude_defaults=True)
        estado = EstadoCorrecao.model_validate_json(dados)
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute("BEGIN IMMEDIATE")
            linha = conexao.execute(
                "SELECT * FROM eventos WHERE id_evento = ?", (estado.evento.id_evento,),
            ).fetchone()
            if linha is None:
                raise KeyError(f"Evento não registrado: {estado.evento.id_evento}")
            atual = self._registro(linha)
            if atual.versao != versao_esperada:
                raise ConflitoVersaoEvento("O estado mudou. Leia o evento novamente antes de gravar.")
            if atual.estado.evento != estado.evento:
                raise ValueError("Os dados do evento original não podem ser alterados.")
            if estado == atual.estado and motivo is None:
                return atual

            agora = datetime.now(timezone.utc)
            versao = atual.versao + 1
            conexao.execute(
                """
                UPDATE eventos SET etapa = ?, estado_json = ?, versao = ?, atualizado_em = ?
                WHERE id_evento = ?
                """,
                (estado.etapa_atual.value, dados, versao, agora.isoformat(), estado.evento.id_evento),
            )
            decisao = None
            if estado.decisao_humana != atual.estado.decisao_humana:
                decisao = estado.decisao_humana
            self._registrar_transicao(conexao, estado, versao, agora, motivo, decisao)
            return RegistroEvento(estado, versao, atual.criado_em, agora)

    def listar_pendentes(
        self, *, projeto: str | None = None, banco: str | None = None, nome_schema: str | None = None,
        etapas: tuple[EtapaCorrecao, ...] | None = None, limite: int = 100,
    ) -> list[RegistroEvento]:
        """Lista eventos em aberto, incluindo os que aguardam decisão humana."""
        if limite < 1:
            raise ValueError("O limite precisa ser maior que zero.")
        if etapas == ():
            return []
        excluidas = (EtapaCorrecao.APLICADO, EtapaCorrecao.REJEITADO, EtapaCorrecao.FALHOU, EtapaCorrecao.SEM_ALTERACOES)
        filtro = etapas if etapas is not None else excluidas
        operador = "IN" if etapas is not None else "NOT IN"
        parametros = ",".join("?" for _ in filtro)
        with closing(self._conectar()) as conexao:
            linhas = conexao.execute(
                f"""
                SELECT * FROM eventos
                WHERE etapa {operador} ({parametros}) AND (? IS NULL OR projeto = ?)
                  AND (? IS NULL OR json_extract(estado_json, '$.evento.banco') = ?)
                  AND (? IS NULL OR COALESCE(json_extract(estado_json, '$.evento.nome_schema'), 'public') = ?)
                ORDER BY criado_em, id_evento LIMIT ?
                """,
                (*(etapa.value for etapa in filtro), projeto, projeto, banco, banco, nome_schema, nome_schema, limite),
            ).fetchall()
        return [self._registro(linha) for linha in linhas]

    def listar_historico(self, id_evento: str) -> list[TransicaoEvento]:
        """Retorna as transições e decisões registradas para o evento."""
        with closing(self._conectar()) as conexao:
            linhas = conexao.execute(
                "SELECT * FROM transicoes WHERE id_evento = ? ORDER BY versao", (id_evento,),
            ).fetchall()
        return [
            TransicaoEvento(
                versao=linha["versao"],
                etapa=EtapaCorrecao(linha["etapa"]),
                tentativas=linha["tentativas"],
                registrada_em=datetime.fromisoformat(linha["registrada_em"]),
                motivo=linha["motivo"],
                decisao_humana=(
                    DecisaoHumana.model_validate_json(linha["decisao_json"])
                    if linha["decisao_json"] is not None else None
                ),
            )
            for linha in linhas
        ]
