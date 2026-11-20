"""Interface web + API JSON sobre os dados de billing."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import logging
import os
import secrets

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, budget, config, fx, migrate
from .db import query, session

log = logging.getLogger("web")
BASE = Path(__file__).parent
app = FastAPI(title="Dynatrace Billing", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(BASE / "templates"))
S = config.load()
throttle = auth.LoginThrottle()

PUBLIC_PATHS = {"/login", "/health"}


@app.on_event("startup")
def _startup() -> None:
    migrate.migrate(S)


# --------------------------------------------------------------------------- autenticação

@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    user = request.session.get("user")
    if user and auth.is_active(S, user):
        request.state.user = user
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    request.session.clear()
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "não autenticado"}, status_code=401)
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={nxt}", status_code=303)


def _safe_next(nxt: str | None) -> str:
    # evita open redirect: só caminhos locais
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else "/"


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = None):
    if request.session.get("user"):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next)})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    username = username.strip()
    ip = request.client.host if request.client else "?"
    keys = (f"user:{username.lower()}", f"ip:{ip}")
    ctx = {"next": _safe_next(next), "username": username}
    if throttle.locked(*keys):
        log.warning("login bloqueado: usuario=%s ip=%s", username, ip)
        ctx["error"] = "Muitas tentativas. Aguarde 15 minutos e tente novamente."
        return templates.TemplateResponse(request, "login.html", ctx, status_code=429)
    if not auth.authenticate(S, username, password):
        throttle.fail(*keys)
        log.warning("login falhou: usuario=%s ip=%s", username, ip)
        ctx["error"] = "Usuário ou senha inválidos."
        return templates.TemplateResponse(request, "login.html", ctx, status_code=401)
    throttle.reset(*keys)
    request.session.clear()
    request.session["user"] = username
    log.info("login ok: usuario=%s ip=%s", username, ip)
    return RedirectResponse(_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


_secret = os.getenv("SESSION_SECRET")
if not _secret:
    _secret = secrets.token_urlsafe(48)
    log.warning("SESSION_SECRET não definido: usando chave temporária (sessões caem a cada reinício)")
# adicionado por último => executa antes do require_login, que depende de request.session
app.add_middleware(
    SessionMiddleware, secret_key=_secret, session_cookie="dynabilling_session",
    max_age=auth.SESSION_MAX_AGE, same_site="strict",
    https_only=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
)


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
    return templates.TemplateResponse(request, "index.html", {"user": request.state.user})


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


# expressão SQL que leva cada dia ao início do seu balde (semana começa na segunda)
BUCKET_SQL = {
    "day": "c.usage_date",
    "week": "DATE_SUB(c.usage_date, INTERVAL WEEKDAY(c.usage_date) DAY)",
    "month": "DATE_SUB(c.usage_date, INTERVAL DAYOFMONTH(c.usage_date) - 1 DAY)",
}


def bucket_bounds(bucket: str, b0: date) -> tuple[date, date]:
    """Primeiro e último dia do balde que começa em b0."""
    if bucket == "day":
        return b0, b0
    if bucket == "week":
        return b0, b0 + timedelta(days=6)
    nxt = date(b0.year + (b0.month == 12), b0.month % 12 + 1, 1)
    return b0, nxt - timedelta(days=1)


def bucket_starts(bucket: str, start: date, end: date) -> list[date]:
    """Início de todos os baldes que tocam [start, end], inclusive os sem custo."""
    b = start
    if bucket == "week":
        b = start - timedelta(days=start.weekday())
    elif bucket == "month":
        b = start.replace(day=1)
    out = []
    while b <= end:
        out.append(b)
        b = bucket_bounds(bucket, b)[1] + timedelta(days=1)
    return out


def auto_bucket(start: date, end: date) -> str:
    n = (end - start).days + 1
    return "day" if n <= 62 else "week" if n <= 200 else "month"


@app.get("/api/timeseries")
def timeseries(start: date | None = None, end: date | None = None,
               env: list[str] | None = Query(None), cap: list[str] | None = Query(None),
               by: str = Query("cap", pattern="^(cap|env)$"),
               bucket: str = Query("auto", pattern="^(auto|day|week|month)$")):
    """Custo em BRL por recurso (ou ambiente) agregado por dia/semana/mês, mais o período anterior equivalente."""
    with session(S) as conn:
        sub, rate = _ctx(conn)
        if not sub:
            return {"bucket": "day", "buckets": [], "series": []}
        rng = query(conn, "SELECT MIN(usage_date) mn, MAX(usage_date) mx FROM daily_cost WHERE subscription_uuid=%s",
                    (sub["uuid"],))[0]
        if rng["mn"] is None:
            return {"bucket": "day", "buckets": [], "series": []}
        start = start or rng["mn"]
        end = end or rng["mx"]
        if bucket == "auto":
            bucket = auto_bucket(start, end)
        r = _f(rate["rate"])

        if by == "cap":
            key, name, join = "c.capability_key", "MAX(c.capability_name)", ""
        else:
            key = "c.environment_id"
            name = "COALESCE(NULLIF(MAX(e.name),''), c.environment_id)"
            join = "LEFT JOIN environments e ON e.environment_id=c.environment_id"

        where, params = _filters(env, cap, start, end, sub["uuid"])
        rows = query(conn, f"""SELECT {BUCKET_SQL[bucket]} b, {key} id, {name} name, SUM(c.cost) cost
                               FROM daily_cost c {join} WHERE {where}
                               GROUP BY b, {key} ORDER BY b""", params)

        # período anterior com o mesmo número de dias, terminando na véspera de `start`
        n_days = (end - start).days + 1
        p_end = start - timedelta(days=1)
        p_start = p_end - timedelta(days=n_days - 1)
        where, params = _filters(env, cap, p_start, p_end, sub["uuid"])
        prev = query(conn, f"""SELECT {key} id, {name} name, SUM(c.cost) cost
                               FROM daily_cost c {join} WHERE {where} GROUP BY {key}""", params)

        # último dia analisado (limitado ao último dia com dados) × último dia do período anterior
        last_day = min(end, rng["mx"])
        day_cost: dict[str, dict] = {}
        for k, d in (("last_day", last_day), ("prev_last_day", p_end)):
            where, params = _filters(env, cap, d, d, sub["uuid"])
            for x in query(conn, f"""SELECT {key} id, {name} name, SUM(c.cost) cost
                                     FROM daily_cost c {join} WHERE {where} GROUP BY {key}""", params):
                day_cost.setdefault(x["id"], {"name": x["name"]})[k] = _f(x["cost"]) * r

        buckets = bucket_starts(bucket, start, end)
        idx = {b: i for i, b in enumerate(buckets)}
        series: dict[str, dict] = {}

        def get(sid, sname):
            return series.setdefault(sid, {"id": sid, "name": sname, "values": [0.0] * len(buckets),
                                           "total": 0.0, "previous": 0.0, "last_day": 0.0, "prev_last_day": 0.0})
        for x in rows:
            s = get(x["id"], x["name"])
            v = _f(x["cost"]) * r
            s["values"][idx[x["b"]]] = round(v, 2)
            s["total"] += v
        for x in prev:
            get(x["id"], x["name"])["previous"] = _f(x["cost"]) * r
        for sid, x in day_cost.items():
            s = get(sid, x["name"])
            s["last_day"], s["prev_last_day"] = x.get("last_day", 0.0), x.get("prev_last_day", 0.0)
        for s in series.values():
            for k in ("total", "previous", "last_day", "prev_last_day"):
                s[k] = round(s[k], 2)

        out_buckets = []
        for b in buckets:
            b0, b1 = bucket_bounds(bucket, b)
            lo, hi = max(b0, start), min(b1, end)
            out_buckets.append({"start": b0.isoformat(), "end": b1.isoformat(),
                                "days": (hi - lo).days + 1, "full_days": (b1 - b0).days + 1})
        return {
            "bucket": bucket, "by": by, "start": start.isoformat(), "end": end.isoformat(), "days": n_days,
            "buckets": out_buckets, "last_day": last_day.isoformat(),
            "series": sorted(series.values(), key=lambda s: -s["total"]),
            "previous": {"start": p_start.isoformat(), "end": p_end.isoformat(),
                         # só é comparável se a coleta já cobria o período anterior inteiro
                         "complete": rng["mn"] <= p_start},
        }


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
