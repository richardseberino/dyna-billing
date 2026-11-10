"""Cliente da Dynatrace Account Management API (Dynatrace Platform Subscription - DPS).

Endpoints usados (base https://api.dynatrace.com):
  GET /sub/v2/accounts/{account}/subscriptions                       -> lista assinaturas
  GET /sub/v2/accounts/{account}/subscriptions/{sub}                 -> detalhes (período, budget)
  GET /sub/v3/accounts/{account}/subscriptions/{sub}/environments/cost  -> custo por ambiente/capability
  GET /sub/v3/accounts/{account}/subscriptions/{sub}/environments/usage -> uso por ambiente/capability
  GET /env/v2/accounts/{account}/environments                        -> nomes dos ambientes

Autenticação: Bearer token. Pode ser um token já emitido (DT_BEARER_TOKEN, ex. platform token)
ou um OAuth client (DT_CLIENT_ID/DT_CLIENT_SECRET) com escopos
`account-uac-read` e `account-env-read`; neste caso o token é obtido e renovado automaticamente.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import requests

from .config import Settings

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- helpers

def _iso(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, (int, float)):  # epoch ms
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date()
    s = str(v)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(s[:10])


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


# --------------------------------------------------------------------------- real client

class DynatraceClient:
    def __init__(self, s: Settings):
        if not s.dt_account_uuid:
            raise ValueError("DT_ACCOUNT_UUID não configurado")
        if not (s.dt_bearer_token or (s.dt_client_id and s.dt_client_secret)):
            raise ValueError("Configure DT_BEARER_TOKEN ou DT_CLIENT_ID/DT_CLIENT_SECRET")
        self.s = s
        self.http = requests.Session()
        self._token: str | None = s.dt_bearer_token
        self._token_exp: float = float("inf") if s.dt_bearer_token else 0
        self.subscription_uuid = s.dt_subscription_uuid

    # -- auth
    def _auth_header(self) -> dict[str, str]:
        if not self._token or time.time() > self._token_exp - 60:
            r = requests.post(
                self.s.dt_sso_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.s.dt_client_id,
                    "client_secret": self.s.dt_client_secret,
                    "scope": "account-uac-read account-env-read",
                    "resource": f"urn:dtaccount:{self.s.dt_account_uuid}",
                },
                timeout=30,
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 300))
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.s.dt_api_base}{path}"
        for attempt in range(6):
            r = self.http.get(url, params=params, headers=self._auth_header(), timeout=60)
            if r.status_code in (429, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("HTTP %s em %s, aguardando %ss", r.status_code, path, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"GET {path} -> HTTP {r.status_code}: {r.text[:500]}")
            return r.json()
        raise RuntimeError(f"GET {path}: excesso de tentativas")

    def _get_paged(self, path: str, params: dict | None = None) -> Iterable[dict]:
        base = dict(params or {})
        page_params = base
        while True:
            body = self._get(path, page_params)
            yield body
            nxt = body.get("nextPageKey")
            if not nxt:
                return
            # a API de subscriptions exige startTime/endTime também nas páginas seguintes
            page_params = {**base, "page-key": nxt}

    # -- API
    def _acc(self) -> str:
        return f"/sub/v2/accounts/{self.s.dt_account_uuid}"

    def resolve_subscription_uuid(self) -> str:
        if self.subscription_uuid:
            return self.subscription_uuid
        body = self._get(f"{self._acc()}/subscriptions")
        subs = body.get("data") or body.get("subscriptions") or []
        if not subs:
            raise RuntimeError(
                "Nenhuma assinatura DPS encontrada na conta (a API retornou lista vazia). "
                "Possíveis causas: contrato DPS ainda não provisionado/ativo, licenciamento clássico "
                "(Host Units/DDUs, que não usa esta API) ou DT_ACCOUNT_UUID de outra conta.")
        active = [x for x in subs if str(x.get("status", "")).upper() == "ACTIVE"] or subs
        self.subscription_uuid = active[0]["uuid"]
        log.info("Assinatura selecionada: %s (%s)", self.subscription_uuid, active[0].get("name"))
        return self.subscription_uuid

    def get_subscription(self) -> dict:
        uuid = self.resolve_subscription_uuid()
        raw = self._get(f"{self._acc()}/subscriptions/{uuid}")
        budget = raw.get("budget") or (raw.get("currentPeriod") or {}).get("budget") or {}
        return {
            "uuid": uuid,
            "name": raw.get("name"),
            "status": raw.get("status"),
            "start_date": _parse_date(_first(raw, "startTime", "startDate")),
            "end_date": _parse_date(_first(raw, "endTime", "endDate")),
            "budget_total": _dec(_first(budget, "total", "totalBudget", "value")),
            "budget_used": _dec(_first(budget, "used", "usedBudget", "consumed")),
            "currency": _first(budget, "currencyCode", "currency") or raw.get("currencyCode"),
            "raw": raw,
        }

    def list_environments(self) -> dict[str, str]:
        try:
            body = self._get(f"/env/v2/accounts/{self.s.dt_account_uuid}/environments")
        except Exception as e:  # escopo account-env-read é opcional
            log.warning("Não foi possível listar nomes dos ambientes: %s", e)
            return {}
        items = body.get("data") or body.get("tenantResources") or body.get("environments") or []
        return {str(_first(i, "id", "environmentId", "tenantId")): str(_first(i, "name", default=""))
                for i in items if _first(i, "id", "environmentId", "tenantId")}

    def daily_records(self, day: date) -> list[dict]:
        """Custo + uso de um dia, por ambiente e capability (recurso)."""
        uuid = self.resolve_subscription_uuid()
        base = f"/sub/v3/accounts/{self.s.dt_account_uuid}/subscriptions/{uuid}/environments"
        # a API trata endTime como inclusivo: usar 23:59:59 do próprio dia evita trazer o dia seguinte
        params = {"startTime": _iso(day), "endTime": _iso(day).replace("00:00:00", "23:59:59"),
                  "page-size": 50}

        merged: dict[tuple, dict] = {}

        def _rows(body: dict, kind: str):
            for env in body.get("data") or []:
                env_id = str(_first(env, "environmentId", "environmentUuid", "id", default="unknown"))
                for k, v in env.items():
                    if not isinstance(v, list):
                        continue
                    for rec in v:
                        if isinstance(rec, dict) and _first(rec, "capabilityKey", "capability"):
                            yield env_id, rec

        for body in self._get_paged(f"{base}/cost", params):
            for env_id, rec in _rows(body, "cost"):
                d = _parse_date(_first(rec, "startTime", "bookingDate", "date")) or day
                key = (d, env_id, _first(rec, "capabilityKey", "capability"))
                row = merged.setdefault(key, _blank(d, env_id, rec))
                row["cost"] += _dec(_first(rec, "value", "cost", default=0)) or Decimal(0)
                row["currency"] = _first(rec, "currencyCode", "currency") or row["currency"]

        for body in self._get_paged(f"{base}/usage", params):
            for env_id, rec in _rows(body, "usage"):
                d = _parse_date(_first(rec, "startTime", "date")) or day
                key = (d, env_id, _first(rec, "capabilityKey", "capability"))
                row = merged.setdefault(key, _blank(d, env_id, rec))
                row["usage_value"] = (row["usage_value"] or Decimal(0)) + (
                    _dec(_first(rec, "value", "usage", default=0)) or Decimal(0))
                row["usage_unit"] = _first(rec, "unitMeasure", "unit") or row["usage_unit"]

        return list(merged.values())


def _blank(d: date, env_id: str, rec: dict) -> dict:
    return {
        "usage_date": d,
        "environment_id": env_id,
        "capability_key": _first(rec, "capabilityKey", "capability"),
        "capability_name": _first(rec, "capabilityName", "capabilityKey", "capability"),
        "cost": Decimal(0),
        "currency": None,
        "usage_value": None,
        "usage_unit": None,
    }


# --------------------------------------------------------------------------- mock client

class MockClient:
    """Gera dados sintéticos para testar a stack sem credenciais (DT_MOCK=true)."""

    ENVS = {"abc12345": "Produção", "def67890": "Homologação", "ghi24680": "Desenvolvimento"}
    CAPS = [  # key, nome, unidade, custo base diário (USD) por ambiente-peso
        ("FULLSTACK_MONITORING", "Full-Stack Monitoring", "GiB-hour", 260),
        ("INFRASTRUCTURE_MONITORING", "Infrastructure Monitoring", "host-hour", 70),
        ("LOG_MANAGEMENT_INGEST", "Log Management & Analytics - Ingest & Process", "GiB", 120),
        ("LOG_MANAGEMENT_RETAIN", "Log Management & Analytics - Retain", "GiB-day", 35),
        ("LOG_MANAGEMENT_QUERY", "Log Management & Analytics - Query", "GiB scanned", 25),
        ("REAL_USER_MONITORING", "Real User Monitoring", "session", 40),
        ("SYNTHETIC_MONITORING", "Browser Monitor or Clickpath", "synthetic action", 15),
        ("APPLICATION_SECURITY", "Runtime Vulnerability Analytics", "GiB-hour", 30),
    ]
    WEIGHT = {"abc12345": 1.0, "def67890": 0.35, "ghi24680": 0.15}

    def __init__(self, s: Settings):
        self.s = s
        today = date.today()
        self.start = s.contract_start or date(today.year - 1, today.month, 1) + timedelta(days=60)
        self.end = s.contract_end or date(self.start.year + 1, self.start.month, self.start.day) - timedelta(days=1)
        self.total = s.contract_total_credits or Decimal("400000")
        self.subscription_uuid = "mock-subscription"

    def resolve_subscription_uuid(self) -> str:
        return self.subscription_uuid

    def get_subscription(self) -> dict:
        return {"uuid": self.subscription_uuid, "name": "DPS Mock", "status": "ACTIVE",
                "start_date": self.start, "end_date": self.end, "budget_total": self.total,
                "budget_used": None, "currency": "USD", "raw": {"mock": True}}

    def list_environments(self) -> dict[str, str]:
        return dict(self.ENVS)

    def daily_records(self, day: date) -> list[dict]:
        rnd = random.Random(day.toordinal())
        trend = 1 + (day - self.start).days / 900  # crescimento leve ao longo do contrato
        weekend = 0.8 if day.weekday() >= 5 else 1.0
        out = []
        for env, w in self.WEIGHT.items():
            for key, name, unit, base in self.CAPS:
                cost = Decimal(str(round(base * w * trend * weekend * rnd.uniform(0.85, 1.2), 4)))
                out.append({"usage_date": day, "environment_id": env, "capability_key": key,
                            "capability_name": name, "cost": cost, "currency": "USD",
                            "usage_value": cost * Decimal("12.5"), "usage_unit": unit})
        return out


def make_client(s: Settings):
    return MockClient(s) if s.dt_mock else DynatraceClient(s)
