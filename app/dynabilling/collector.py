"""Coletor: busca o billing diário na Dynatrace e grava no MySQL.

Uso:
  python -m dynabilling.collector                 # loop contínuo (COLLECT_INTERVAL_MINUTES)
  python -m dynabilling.collector --once          # uma execução
  python -m dynabilling.collector --once --from 2026-01-01 --to 2026-03-31   # reprocessa período
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, timedelta

from . import config, fx
from .db import query, session
from .dynatrace import make_client

log = logging.getLogger("collector")


def _upsert_subscription(conn, sub: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO subscriptions (uuid,name,status,start_date,end_date,budget_total,budget_used,currency,raw_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE name=VALUES(name), status=VALUES(status), start_date=VALUES(start_date),
                 end_date=VALUES(end_date), budget_total=VALUES(budget_total), budget_used=VALUES(budget_used),
                 currency=VALUES(currency), raw_json=VALUES(raw_json)""",
            (sub["uuid"], sub["name"], sub["status"], sub["start_date"], sub["end_date"],
             sub["budget_total"], sub["budget_used"], sub["currency"], json.dumps(sub["raw"], default=str)),
        )


def _upsert_envs(conn, envs: dict[str, str]) -> None:
    if not envs:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO environments (environment_id,name) VALUES (%s,%s) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name)",
            list(envs.items()),
        )


def _upsert_rows(conn, sub_uuid: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO daily_cost (subscription_uuid,usage_date,environment_id,capability_key,capability_name,
                                       cost,currency,usage_value,usage_unit)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE capability_name=VALUES(capability_name), cost=VALUES(cost),
                 currency=VALUES(currency), usage_value=VALUES(usage_value), usage_unit=VALUES(usage_unit)""",
            [(sub_uuid, r["usage_date"], r["environment_id"], r["capability_key"], r["capability_name"],
              r["cost"], r["currency"], r["usage_value"], r["usage_unit"]) for r in rows],
        )
        # garante que ambientes sem nome também apareçam
        cur.executemany("INSERT IGNORE INTO environments (environment_id) VALUES (%s)",
                        list({(r["environment_id"],) for r in rows}))
    return len(rows)


def run_once(s: config.Settings, date_from: date | None = None, date_to: date | None = None) -> int:
    client = make_client(s)
    today = date.today()

    with session(s) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO collector_runs (status) VALUES ('running')")
            run_id = cur.lastrowid
        conn.commit()

        total = 0
        try:
            sub = client.get_subscription()
            _upsert_subscription(conn, sub)
            _upsert_envs(conn, client.list_environments())
            fx.refresh_rate(conn, s, sub["currency"])
            conn.commit()

            if date_from is None:
                last = query(conn, "SELECT MAX(usage_date) AS d FROM daily_cost WHERE subscription_uuid=%s",
                             (sub["uuid"],))[0]["d"]
                if last is None:
                    # primeira carga: desde o início do contrato (necessário para o saldo correto)
                    start = s.contract_start or sub["start_date"]
                    date_from = start if start else today - timedelta(days=s.backfill_days)
                else:
                    date_from = min(last + timedelta(days=1), today - timedelta(days=s.refresh_days))
                if sub["start_date"]:
                    date_from = max(date_from, sub["start_date"])
            # o dia corrente ainda não tem custo fechado na Dynatrace: coleta sempre até D-1
            yesterday = today - timedelta(days=1)
            date_to = min(date_to or yesterday, yesterday)
            if date_from > date_to:
                log.info("Nada a coletar (início %s > D-1 %s)", date_from, date_to)

            log.info("Coletando %s .. %s (assinatura %s)", date_from, date_to, sub["uuid"])
            d = date_from
            while d <= date_to:
                rows = client.daily_records(d)
                n = _upsert_rows(conn, sub["uuid"], rows)
                conn.commit()
                total += n
                log.info("  %s: %d linhas", d, n)
                d += timedelta(days=1)

            with conn.cursor() as cur:
                cur.execute("UPDATE collector_runs SET status='ok', finished_at=NOW(), rows_upserted=%s, "
                            "message=%s WHERE id=%s", (total, f"{date_from}..{date_to}", run_id))
        except Exception as e:
            conn.rollback()
            log.exception("Falha na coleta")
            with conn.cursor() as cur:
                cur.execute("UPDATE collector_runs SET status='error', finished_at=NOW(), rows_upserted=%s, "
                            "message=%s WHERE id=%s", (total, str(e)[:4000], run_id))
            conn.commit()
            raise
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Coletor de billing Dynatrace")
    p.add_argument("--once", action="store_true", help="executa uma vez e sai")
    p.add_argument("--from", dest="date_from", type=date.fromisoformat)
    p.add_argument("--to", dest="date_to", type=date.fromisoformat)
    a = p.parse_args()
    s = config.load()

    if a.once or a.date_from:
        run_once(s, a.date_from, a.date_to)
        return
    while True:
        try:
            run_once(s)
        except Exception:
            pass  # já registrado; tenta de novo no próximo ciclo
        log.info("Próxima coleta em %d min", s.collect_interval_minutes)
        time.sleep(s.collect_interval_minutes * 60)


if __name__ == "__main__":
    main()
