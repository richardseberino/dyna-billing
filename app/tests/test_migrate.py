from dynabilling.migrate import SCHEMA, statements


def test_schema_statements():
    st = statements(SCHEMA.read_text(encoding="utf-8"))
    tables = [x for x in st if x.upper().startswith("CREATE TABLE")]
    assert len(tables) == 6
    assert all("IF NOT EXISTS" in t for t in tables)
    assert not any(x.startswith("--") for x in st)
