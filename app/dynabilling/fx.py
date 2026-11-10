"""Conversão da moeda do contrato para BRL.

FX_MODE=ptax  -> busca a PTAX de venda (fechamento) no Banco Central (API Olinda) e grava em fx_rates.
FX_MODE=fixed -> usa FX_FIXED_RATE.
Se a moeda do contrato já for BRL, a taxa é 1.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests

from .config import Settings
from .db import query

log = logging.getLogger(__name__)

PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoMoedaPeriodo(moeda=@moeda,dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
)


def fetch_ptax(currency: str, ref: date | None = None) -> tuple[date, Decimal]:
    ref = ref or date.today()
    params = {
        "@moeda": f"'{currency}'",
        "@dataInicial": f"'{(ref - timedelta(days=10)).strftime('%m-%d-%Y')}'",
        "@dataFinalCotacao": f"'{ref.strftime('%m-%d-%Y')}'",
        "$format": "json",
    }
    r = requests.get(PTAX_URL, params=params, timeout=30)
    r.raise_for_status()
    values = r.json().get("value", [])
    closes = [v for v in values if v.get("tipoBoletim") == "Fechamento"] or values
    if not closes:
        raise RuntimeError(f"PTAX sem cotação para {currency}")
    last = closes[-1]
    d = datetime.fromisoformat(last["dataHoraCotacao"][:19]).date()
    return d, Decimal(str(last["cotacaoVenda"]))


def refresh_rate(conn, s: Settings, currency: str | None) -> None:
    """Chamado pelo coletor: grava a cotação do dia."""
    if not currency or currency.upper() == "BRL" or s.fx_mode != "ptax":
        return
    try:
        d, rate = fetch_ptax(currency.upper())
    except Exception as e:
        log.warning("Falha ao obter PTAX: %s", e)
        return
    with conn.cursor() as cur:
        cur.execute(
            "REPLACE INTO fx_rates (rate_date, currency, rate_brl, source) VALUES (%s,%s,%s,'PTAX')",
            (d, currency.upper(), rate),
        )


def current_rate(conn, s: Settings, currency: str | None) -> dict:
    """Taxa atual (moeda do contrato -> BRL) usada pela interface."""
    cur = (currency or "BRL").upper()
    if cur == "BRL":
        return {"currency": cur, "rate": Decimal(1), "source": "BRL", "date": None}
    if s.fx_mode == "fixed" and s.fx_fixed_rate:
        return {"currency": cur, "rate": s.fx_fixed_rate, "source": "FX_FIXED_RATE", "date": None}
    rows = query(conn, "SELECT rate_date, rate_brl, source FROM fx_rates WHERE currency=%s "
                       "ORDER BY rate_date DESC LIMIT 1", (cur,))
    if rows:
        return {"currency": cur, "rate": rows[0]["rate_brl"], "source": rows[0]["source"],
                "date": rows[0]["rate_date"]}
    if s.fx_fixed_rate:
        return {"currency": cur, "rate": s.fx_fixed_rate, "source": "FX_FIXED_RATE", "date": None}
    log.warning("Sem cotação para %s; usando 1.0. Configure FX_FIXED_RATE ou rode o coletor.", cur)
    return {"currency": cur, "rate": Decimal(1), "source": "indisponível", "date": None}
