"""Aplica o schema (idempotente: só CREATE TABLE IF NOT EXISTS).

Executado na inicialização do web e do coletor, para que bancos gerenciados (ex.: RDS),
que não rodam o docker-entrypoint-initdb.d, recebam as tabelas automaticamente.

  python -m dynabilling.migrate
"""
from __future__ import annotations

import logging
from pathlib import Path

from .config import Settings, load
from .db import session

log = logging.getLogger(__name__)
SCHEMA = Path(__file__).with_name("schema.sql")


def statements(sql: str) -> list[str]:
    out = []
    for chunk in sql.split(";"):
        body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


def migrate(s: Settings) -> int:
    stmts = statements(SCHEMA.read_text(encoding="utf-8"))
    with session(s) as conn, conn.cursor() as cur:
        for st in stmts:
            cur.execute(st)
    log.info("Schema verificado (%d comandos)", len(stmts))
    return len(stmts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"OK: {migrate(load())} comandos aplicados")
