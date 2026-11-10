from datetime import date
from decimal import Decimal

from dynabilling.budget import BudgetInput, evaluate
from dynabilling.dynatrace import _parse_date


def _inp(**kw):
    base = dict(total_credits=Decimal("100000"), used_credits=Decimal("70000"),
                contract_start=date(2026, 1, 1), contract_end=date(2026, 12, 31),
                last_complete_day=date(2026, 11, 30), last_day_cost=Decimal("1200"),
                averages={3: Decimal("1000"), 7: Decimal("950"), 30: Decimal("900")}, main_window=3, fx_rate=Decimal("5"))
    base.update(kw)
    return BudgetInput(**base)


def test_allowed_daily_and_pct():
    r = evaluate(_inp())
    assert r["days_remaining"] == 31
    assert r["remaining"] == 30000
    assert r["allowed_daily"] == round(30000 / 31, 2)
    assert r["allowed_daily_brl"] == round(30000 / 31 * 5, 2)
    # 1000 / 967.74 = 103.33%
    assert r["main"]["pct_of_allowed"] == 103.33
    assert r["main"]["status"] == "warning"
    assert r["last_day"]["status"] == "critical"
    assert r["averages"]["30"]["status"] == "ok"
    assert r["averages"]["30"]["diff_brl"] < 0


def test_projection_and_exhaustion():
    r = evaluate(_inp())
    assert r["projected_used"] == 70000 + 1000 * 31
    assert r["exhaustion_date"] == "2026-12-30"


def test_overspent_contract():
    r = evaluate(_inp(used_credits=Decimal("101000")))
    assert r["remaining"] == -1000
    assert r["main"]["status"] == "critical"


def test_parse_date():
    assert _parse_date("2026-11-09T00:00:00Z") == date(2026, 11, 9)
    assert _parse_date("2026-11-09") == date(2026, 11, 9)


def test_main_window_drives_projection():
    r = evaluate(_inp(averages={3: Decimal("2000"), 7: Decimal("1000"), 30: Decimal("900")}, main_window=3))
    assert r["main"]["cost"] == 2000
    assert r["projected_used"] == 70000 + 2000 * 31
    assert r["averages"]["7"]["cost"] == 1000
