"""Acesso ao MySQL."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings


def connect(s: Settings, retries: int = 30) -> pymysql.connections.Connection:
    last: Exception | None = None
    for _ in range(retries):
        try:
            return pymysql.connect(
                host=s.db_host, port=s.db_port, user=s.db_user, password=s.db_password,
                database=s.db_name, cursorclass=DictCursor, autocommit=False, charset="utf8mb4",
            )
        except pymysql.err.OperationalError as e:  # banco ainda subindo
            last = e
            time.sleep(2)
    raise RuntimeError(f"Não foi possível conectar ao MySQL: {last}")


@contextmanager
def session(s: Settings) -> Iterator[pymysql.connections.Connection]:
    conn = connect(s)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(conn, sql: str, params: tuple | dict | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())
