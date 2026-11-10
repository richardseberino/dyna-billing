"""Avaliação do consumo diário vs. média permitida até o fim do contrato.

Regra:
    saldo            = créditos totais - créditos consumidos
    dias_restantes   = dias entre o último dia completo coletado (D) e o fim do contrato (inclusive)
    média_permitida  = saldo / dias_restantes
    % da média       = custo diário / média_permitida * 100

Comparamos o custo do último dia completo e as médias móveis (janela principal configurável,
padrão 3 dias, mais 7 e 30 dias como contexto) com a média permitida. A projeção e a data de
esgotamento usam a janela principal, que reflete melhor o ritmo atual quando novas monitorações
estão sendo incluídas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

Q2 = Decimal("0.01")


def _r(v: Decimal | None) -> float | None:
    return None if v is None else float(Decimal(v).quantize(Q2, ROUND_HALF_UP))


@dataclass
class BudgetInput:
    total_credits: Decimal
    used_credits: Decimal
    contract_start: date | None
    contract_end: date
    last_complete_day: date
    last_day_cost: Decimal
    averages: dict[int, Decimal]  # janela (dias) -> custo médio diário
    main_window: int              # janela usada no destaque e na projeção
    fx_rate: Decimal  # moeda do contrato -> BRL


def status_for(pct: Decimal | None) -> str:
    if pct is None:
        return "unknown"
    if pct <= 100:
        return "ok"
    if pct <= 110:
        return "warning"
    return "critical"


def evaluate(b: BudgetInput) -> dict:
    rate = b.fx_rate
    remaining = b.total_credits - b.used_credits
    days_remaining = (b.contract_end - b.last_complete_day).days
    allowed = remaining / days_remaining if days_remaining > 0 else None

    def cmp(cost: Decimal) -> dict:
        pct = (cost / allowed * 100) if allowed and allowed > 0 else None
        diff = (cost - allowed) if allowed is not None else None
        return {
            "cost": _r(cost), "cost_brl": _r(cost * rate),
            "pct_of_allowed": _r(pct),
            "diff": _r(diff), "diff_brl": _r(diff * rate) if diff is not None else None,
            "status": status_for(pct) if allowed is None or allowed > 0 else "critical",
        }

    pct_time = None
    if b.contract_start:
        span = (b.contract_end - b.contract_start).days + 1
        elapsed = (b.last_complete_day - b.contract_start).days + 1
        pct_time = Decimal(elapsed) / Decimal(span) * 100 if span > 0 else None

    main_avg = b.averages[b.main_window]
    projected_used = b.used_credits + main_avg * max(days_remaining, 0)
    exhaustion = None
    if main_avg > 0 and remaining > 0:
        exhaustion = b.last_complete_day + timedelta(days=int(remaining / main_avg))

    return {
        "fx_rate": float(rate),
        "contract_start": b.contract_start.isoformat() if b.contract_start else None,
        "contract_end": b.contract_end.isoformat(),
        "last_complete_day": b.last_complete_day.isoformat(),
        "days_remaining": days_remaining,
        "total": _r(b.total_credits), "total_brl": _r(b.total_credits * rate),
        "used": _r(b.used_credits), "used_brl": _r(b.used_credits * rate),
        "remaining": _r(remaining), "remaining_brl": _r(remaining * rate),
        "pct_used": _r(b.used_credits / b.total_credits * 100) if b.total_credits else None,
        "pct_time_elapsed": _r(pct_time),
        "allowed_daily": _r(allowed), "allowed_daily_brl": _r(allowed * rate) if allowed is not None else None,
        "last_day": cmp(b.last_day_cost),
        "main_window": b.main_window,
        "main": cmp(main_avg),
        "averages": {str(w): cmp(v) for w, v in sorted(b.averages.items())},
        "projected_used": _r(projected_used), "projected_used_brl": _r(projected_used * rate),
        "projected_pct_of_total": _r(projected_used / b.total_credits * 100) if b.total_credits else None,
        "projected_balance_brl": _r((b.total_credits - projected_used) * rate),
        "exhaustion_date": exhaustion.isoformat() if exhaustion else None,
    }
