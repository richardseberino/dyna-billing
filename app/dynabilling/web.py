"""Interface web + API JSON sobre os dados de billing."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import budget, config, fx
from .db import query, session

BASE = Path(__file__).parent
app = FastAPI(title="Dynatrace Billing")
templates = Jinja2Templates(directory=str(BASE / "templates"))
S = config.load()


# --------------------------------------------------------------------------- helpers

def _subscription(conn) -> dict | None:
    if S.dt_subscription_uuid:
        rows = query(conn, "SELECT * FROM subscriptions WHERE uuid=%s", (S.dt_subscription_uuid,))
    else:
        rows = query(conn, "SELECT * FROM subscriptions ORDER BY updated_at DESC LIMIT 1")
    return rows[0] if rows else None


def _filters(env: list[str] | None, cap: list[str] | None, start: date | None, end: date | None,
             sub_uuid: str) -> tuple[str, list]:
    where, params = ["c.subscription_uuid=%s"], [sub_uuid]
    if start:
        where.append("c.usage_date >= %s"); params.append(start)
    if end:
        where.append("c.usage_date <= %s"); params.append(end)
    if env:
        where.append(f"c.environment_id IN ({','.join(['%s'] * len(env))})"); params += env
    if cap:
        where.append(f"c.capability_key IN ({','.join(['%s'] * len(cap))})"); params += cap
    return " AND ".join(where), params


def _ctx(conn):
    sub = _subscription(conn)
    if not sub:
        return None, None
    rate = fx.current_rate(conn, S, sub["currency"])
    return sub, rate


def _f(v) -> float:
    return float(v or 0)


# --------------------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# --------------------------------------------------------------------------- API

@app.get("/api/summary")
def summary():
    with session(S) as conn:
        sub, rate = _ctx(conn)
        if not sub:
            return {"configured": False, "missing": ["nenhuma coleta realizada ainda"]}

        start = S.contract_start or sub["start_date"]
        end = S.contract_end or sub["end_date"]
        total = S.contract_total_credits or sub["budget_total"]
        missing = [n for n, v in (("CONTRACT_END_DATE", end), ("CONTRACT_TOTAL_CREDITS", total)) if v is None]

        maxd = query(conn, "SELECT MAX(usage_date) d, MIN(usage_date) mn FROM daily_cost WHERE subscription_uuid=%s",
                     (sub["uuid"],))[0]
        if maxd["d"] is None:
            missing.append("sem dados de custo coletados")
        if missing:
            return {"configured": False, "missing": missing}

        last_day = min(maxd["d"], date.today() - timedelta(days=1))

        def total_between(a: date, b: date) -> Decimal:
            r = query(conn, "SELECT COALESCE(SUM(cost),0) s FROM daily_cost WHERE subscription_uuid=%s "
                            "AND usage_date BETWEEN %s AND %s", (sub["uuid"], a, b))
            return Decimal(r[0]["s"])

        def avg(days: int) -> Decimal:
            a = last_day - timedelta(days=days - 1)
            if start:
                a = max(a, start)
            a = max(a, maxd["mn"])
            n = (last_day - a).days + 1
            return total_between(a, last_day) / n if n > 0 else Decimal(0)

        if sub["budget_used"] is not None and S.contract_total_credits is None:
            used, used_source = Decimal(sub["budget_used"]), "Dynatrace API"
        else:
            used = total_between(start or maxd["mn"], last_day)
            used_source = "soma dos custos diários coletados"

        coverage_warning = bool(start and maxd["mn"] > start and used_source != "Dynatrace API")

        result = budget.evaluate(budget.BudgetInput(
            total_credits=Decimal(total), used_credits=used, contract_start=start, contract_end=end,
            last_complete_day=last_day, last_day_cost=total_between(last_day, last_day),
            averages={w: avg(w) for w in {S.avg_window_days, 7, 30}}, main_window=S.avg_window_days,
            fx_rate=Decimal(rate["rate"]),
        ))
        result.update({
            "configured": True,
            "subscription": {"uuid": sub["uuid"], "name": sub["name"], "status": sub["status"]},
            "currency": rate["currency"], "fx_source": rate["source"],
            "fx_date": rate["date"].isoformat() if rate["date"] else None,
            "used_source": used_source,
            "coverage_warning": coverage_warning,
            "data_from": maxd["mn"].isoformat(),
            "last_run": _last_run(conn),
        })
        return result


def _last_run(conn) -> dict | None:
    r = query(conn, "SELECT started_at, finished_at, status, rows_upserted, message FROM collector_runs "
                    "ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r[0].items()}


@app.get("/api/filters")
def filters():
    with session(S) as conn:
        sub = _subscription(conn)
        if not sub:
            return {"environments": [], "capabilities": [], "min_date": None, "max_date": None}
        envs = query(conn, """SELECT DISTINCT c.environment_id id, COALESCE(NULLIF(e.name,''), c.environment_id) name
                              FROM daily_cost c LEFT JOIN environments e ON e.environment_id=c.environment_id
                              WHERE c.subscription_uuid=%s ORDER BY name""", (sub["uuid"],))
        caps = query(conn, """SELECT capability_key id, MAX(capability_name) name FROM daily_cost
                              WHERE subscription_uuid=%s GROUP BY capability_key ORDER BY name""", (sub["uuid"],))
        rng = query(conn, "SELECT MIN(usage_date) mn, MAX(usage_date) mx FROM daily_cost WHERE subscription_uuid=%s",
                    (sub["uuid"],))[0]
        return {"environments": envs, "capabilities": caps,
                "min_date": rng["mn"].isoformat() if rng["mn"] else None,
                "max_date": rng["mx"].isoformat() if rng["mx"] else None}


@app.get("/api/daily")
def daily(start: date | None = None, end: date | None = None,
          env: list[str] | None = Query(None), cap: list[str] | None = Query(None)):
    """Custo diário em BRL por ambiente."""
    with session(S) as conn:
        sub, rate = _ctx(conn)
        if not sub:
            return {"days": [], "series": []}
        where, params = _filters(env, cap, start, end, sub["uuid"])
        rows = query(conn, f"""SELECT c.usage_date d, c.environment_id env,
                                      COALESCE(NULLIF(e.name,''), c.environment_id) name, SUM(c.cost) cost
                               FROM daily_cost c LEFT JOIN environments e ON e.environment_id=c.environment_id
                               WHERE {where} GROUP BY c.usage_date, c.environment_id, name
                               ORDER BY c.usage_date""", params)
        r = _f(rate["rate"])
        days = sorted({x["d"] for x in rows})
        idx = {d: i for i, d in enumerate(days)}
        series: dict[str, dict] = {}
        for x in rows:
            s = series.setdefault(x["env"], {"id": x["env"], "name": x["name"], "values": [0.0] * len(days)})
            s["values"][idx[x["d"]]] = round(_f(x["cost"]) * r, 2)
        return {"days": [d.isoformat() for d in days], "series": list(series.values())}


@app.get("/api/breakdown")
def breakdown(start: date | None = None, end: date | None = None,
              env: list[str] | None = Query(None), cap: list[str] | None = Query(None)):
    """Totais em BRL por recurso (capability) e por ambiente, no período."""
    with session(S) as conn:
        sub, rate = _ctx(conn)
        if not sub:
            return {"by_capability": [], "by_environment": [], "matrix": []}
        where, params = _filters(env, cap, start, end, sub["uuid"])
        r = _f(rate["rate"])
        by_cap = query(conn, f"""SELECT c.capability_key id, MAX(c.capability_name) name, SUM(c.cost) cost,
                                        SUM(c.usage_value) usage_value, MAX(c.usage_unit) unit
                                 FROM daily_cost c WHERE {where} GROUP BY c.capability_key
                                 ORDER BY cost DESC""", params)
        by_env = query(conn, f"""SELECT c.environment_id id, COALESCE(NULLIF(MAX(e.name),''), c.environment_id) name,
                                        SUM(c.cost) cost
                                 FROM daily_cost c LEFT JOIN environments e ON e.environment_id=c.environment_id
                                 WHERE {where} GROUP BY c.environment_id ORDER BY cost DESC""", params)
        matrix = query(conn, f"""SELECT c.environment_id env, COALESCE(NULLIF(MAX(e.name),''), c.environment_id) env_name,
                                        c.capability_key cap, MAX(c.capability_name) cap_name, SUM(c.cost) cost,
                                        SUM(c.usage_value) usage_value, MAX(c.usage_unit) unit
                                 FROM daily_cost c LEFT JOIN environments e ON e.environment_id=c.environment_id
                                 WHERE {where} GROUP BY c.environment_id, c.capability_key
                                 ORDER BY cost DESC""", params)
        conv = lambda rows: [{**x, "cost": round(_f(x["cost"]), 2), "cost_brl": round(_f(x["cost"]) * r, 2),
                              **({"usage_value": round(_f(x["usage_value"]), 3)} if "usage_value" in x else {})}
                             for x in rows]
        return {"by_capability": conv(by_cap), "by_environment": conv(by_env), "matrix": conv(matrix),
                "currency": rate["currency"], "fx_rate": r}


def _detail_rows(conn, sub, rate, start, end, env, cap, limit: int | None):
    where, params = _filters(env, cap, start, end, sub["uuid"])
    sql = f"""SELECT c.usage_date, c.environment_id, COALESCE(NULLIF(e.name,''), c.environment_id) environment_name,
                     c.capability_key, c.capability_name, c.usage_value, c.usage_unit, c.cost, c.currency
              FROM daily_cost c LEFT JOIN environments e ON e.environment_id=c.environment_id
              WHERE {where} ORDER BY c.usage_date DESC, c.cost DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    r = _f(rate["rate"])
    return [{**x, "usage_date": x["usage_date"].isoformat(), "usage_value": _f(x["usage_value"]),
             "cost": round(_f(x["cost"]), 4), "cost_brl": round(_f(x["cost"]) * r, 2)}
            for x in query(conn, sql, params)]


@app.get("/api/rows")
def rows(start: date | None = None, end: date | None = None,
         env: list[str] | None = Query(None), cap: list[str] | None = Query(None),
         limit: int = Query(500, le=10000)):
    with session(S) as conn:
        sub, rate = _ctx(conn)
        if not sub:
            return []
        return _detail_rows(conn, sub, rate, start, end, env, cap, limit)


@app.get("/api/export.csv")
def export(start: date | None = None, end: date | None = None,
           env: list[str] | None = Query(None), cap: list[str] | None = Query(None)):
    with session(S) as conn:
        sub, rate = _ctx(conn)
        data = _detail_rows(conn, sub, rate, start, end, env, cap, None) if sub else []
    buf = io.StringIO()
    cols = ["usage_date", "environment_id", "environment_name", "capability_key", "capability_name",
            "usage_value", "usage_unit", "cost", "currency", "cost_brl"]
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";")
    w.writeheader()
    w.writerows(data)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=dynatrace_billing.csv"})


@app.get("/health")
def health():
    return JSONResponse({"ok": True})
