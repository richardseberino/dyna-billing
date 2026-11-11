"""Autenticação da interface web: usuários no MySQL, senha com scrypt, sessão em cookie assinado.

Gestão de usuários (CLI):
  python -m dynabilling.auth add <usuario>        # pede a senha
  python -m dynabilling.auth passwd <usuario>     # troca a senha
  python -m dynabilling.auth disable <usuario>    # bloqueia o acesso
  python -m dynabilling.auth enable <usuario>
  python -m dynabilling.auth delete <usuario>
  python -m dynabilling.auth list
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import os
import sys
import threading
import time

from .config import Settings, load
from .db import query, session

MIN_PASSWORD_LEN = 10
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_HOURS", "8")) * 3600
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60

_N, _R, _P = 2 ** 14, 8, 1

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(64)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    active        TINYINT(1)   NOT NULL DEFAULT 1,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP    NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


# --------------------------------------------------------------------------- senha

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                              n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


# hash fixo para gastar o mesmo tempo quando o usuário não existe (evita enumeração por tempo)
_DUMMY_HASH = hash_password(os.urandom(8).hex())


# --------------------------------------------------------------------------- usuários

def ensure_table(s: Settings) -> None:
    with session(s) as conn, conn.cursor() as cur:
        cur.execute(USERS_DDL)


def authenticate(s: Settings, username: str, password: str) -> bool:
    with session(s) as conn:
        rows = query(conn, "SELECT id, password_hash, active FROM users WHERE username=%s", (username,))
        if not rows:
            verify_password(password, _DUMMY_HASH)
            return False
        u = rows[0]
        ok = verify_password(password, u["password_hash"]) and bool(u["active"])
        if ok:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (u["id"],))
        return ok


def is_active(s: Settings, username: str) -> bool:
    with session(s) as conn:
        rows = query(conn, "SELECT active FROM users WHERE username=%s", (username,))
        return bool(rows and rows[0]["active"])


# --------------------------------------------------------------------------- bloqueio por tentativas

class LoginThrottle:
    """Bloqueia usuário/IP após MAX_FAILS falhas seguidas, por LOCK_SECONDS (em memória)."""

    def __init__(self):
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        lst = [t for t in self._fails.get(key, []) if now - t < LOCK_SECONDS]
        self._fails[key] = lst
        return lst

    def locked(self, *keys: str) -> bool:
        with self._lock:
            return any(len(self._recent(k)) >= MAX_FAILS for k in keys)

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._recent(k).append(time.time())

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)


# --------------------------------------------------------------------------- CLI

def _ask_password() -> str:
    if not sys.stdin.isatty():
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Senha: ")
        if pw != getpass.getpass("Confirme a senha: "):
            sys.exit("As senhas não conferem.")
    if len(pw) < MIN_PASSWORD_LEN:
        sys.exit(f"A senha deve ter pelo menos {MIN_PASSWORD_LEN} caracteres.")
    return pw


def main() -> None:
    p = argparse.ArgumentParser(description="Gestão de usuários da interface web")
    sub = p.add_subparsers(dest="cmd", required=True)
    for c in ("add", "passwd", "disable", "enable", "delete"):
        sub.add_parser(c).add_argument("username")
    sub.add_parser("list")
    a = p.parse_args()

    s = load()
    ensure_table(s)
    with session(s) as conn, conn.cursor() as cur:
        if a.cmd == "list":
            cur.execute("SELECT username, active, created_at, last_login FROM users ORDER BY username")
            for r in cur.fetchall():
                print(f"{r['username']:<24} {'ativo' if r['active'] else 'BLOQUEADO':<10} "
                      f"criado {r['created_at']}  último login {r['last_login'] or '-'}")
            return
        cur.execute("SELECT id FROM users WHERE username=%s", (a.username,))
        exists = cur.fetchone() is not None
        if a.cmd == "add":
            if exists:
                sys.exit("Usuário já existe; use 'passwd' para trocar a senha.")
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s,%s)",
                        (a.username, hash_password(_ask_password())))
        elif not exists:
            sys.exit("Usuário não encontrado.")
        elif a.cmd == "passwd":
            cur.execute("UPDATE users SET password_hash=%s WHERE username=%s",
                        (hash_password(_ask_password()), a.username))
        elif a.cmd in ("disable", "enable"):
            cur.execute("UPDATE users SET active=%s WHERE username=%s", (a.cmd == "enable", a.username))
        elif a.cmd == "delete":
            cur.execute("DELETE FROM users WHERE username=%s", (a.username,))
    print("OK")


if __name__ == "__main__":
    main()
