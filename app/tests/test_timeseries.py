from datetime import date

from dynabilling.web import auto_bucket, bucket_bounds, bucket_starts


def test_bucket_starts_covers_range():
    assert bucket_starts("day", date(2026, 1, 30), date(2026, 2, 2)) == [
        date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 2)]
    # 2026-11-19 é quinta: semana começa na segunda 16/11
    assert bucket_starts("week", date(2026, 11, 19), date(2026, 11, 30)) == [
        date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 30)]
    assert bucket_starts("month", date(2026, 11, 15), date(2027, 1, 2)) == [
        date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]


def test_bucket_bounds():
    assert bucket_bounds("day", date(2026, 3, 5)) == (date(2026, 3, 5), date(2026, 3, 5))
    assert bucket_bounds("week", date(2026, 11, 16)) == (date(2026, 11, 16), date(2026, 11, 22))
    assert bucket_bounds("month", date(2026, 2, 1)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert bucket_bounds("month", date(2026, 12, 1)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_auto_bucket():
    assert auto_bucket(date(2026, 1, 1), date(2026, 1, 30)) == "day"
    assert auto_bucket(date(2026, 1, 1), date(2026, 4, 30)) == "week"
    assert auto_bucket(date(2026, 3, 31), date(2026, 11, 19)) == "month"
