"""Configuração via variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "sim", "on")


def _opt(name: str) -> str | None:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else None


def _opt_date(name: str) -> date | None:
    v = _opt(name)
    return date.fromisoformat(v) if v else None


def _opt_dec(name: str) -> Decimal | None:
    v = _opt(name)
    return Decimal(v) if v else None


@dataclass(frozen=True)
class Settings:
    # MySQL
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    # Dynatrace (Account Management API)
    dt_account_uuid: str | None
    dt_subscription_uuid: str | None
    dt_bearer_token: str | None   # token já emitido (platform token / bearer)
    dt_client_id: str | None      # OAuth client (alternativa ao bearer)
    dt_client_secret: str | None
    dt_api_base: str
    dt_sso_url: str
    dt_mock: bool

    # Coleta
    backfill_days: int
    refresh_days: int
    collect_interval_minutes: int
    avg_window_days: int       # janela da média móvel usada na avaliação

    # Contrato (sobrescrevem os valores vindos da API)
    contract_start: date | None
    contract_end: date | None
    contract_total_credits: Decimal | None

    # Câmbio
    fx_mode: str               # "ptax" | "fixed"
    fx_fixed_rate: Decimal | None


def load() -> Settings:
    return Settings(
        db_host=os.getenv("DB_HOST", "localhost"),
        db_port=int(os.getenv("DB_PORT", "3306")),
        db_user=os.getenv("DB_USER", "dyna"),
        db_password=os.getenv("DB_PASSWORD", "dyna"),
        db_name=os.getenv("DB_NAME", "dyna_billing"),
        dt_account_uuid=_opt("DT_ACCOUNT_UUID"),
        dt_subscription_uuid=_opt("DT_SUBSCRIPTION_UUID"),
        dt_bearer_token=_opt("DT_BEARER_TOKEN"),
        dt_client_id=_opt("DT_CLIENT_ID"),
        dt_client_secret=_opt("DT_CLIENT_SECRET"),
        dt_api_base=os.getenv("DT_API_BASE", "https://api.dynatrace.com").rstrip("/"),
        dt_sso_url=os.getenv("DT_SSO_URL", "https://sso.dynatrace.com/sso/oauth2/token"),
        dt_mock=_bool("DT_MOCK"),
        backfill_days=int(os.getenv("BACKFILL_DAYS", "90")),
        refresh_days=int(os.getenv("REFRESH_DAYS", "3")),
        collect_interval_minutes=int(os.getenv("COLLECT_INTERVAL_MINUTES", "360")),
        avg_window_days=max(1, int(os.getenv("AVG_WINDOW_DAYS", "3"))),
        contract_start=_opt_date("CONTRACT_START_DATE"),
        contract_end=_opt_date("CONTRACT_END_DATE"),
        contract_total_credits=_opt_dec("CONTRACT_TOTAL_CREDITS"),
        fx_mode=os.getenv("FX_MODE", "ptax").lower(),
        fx_fixed_rate=_opt_dec("FX_FIXED_RATE"),
    )
