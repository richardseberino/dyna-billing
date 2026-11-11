from dynabilling.auth import LoginThrottle, MAX_FAILS, hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("senha-muito-forte")
    assert h.startswith("scrypt$")
    assert verify_password("senha-muito-forte", h)
    assert not verify_password("errada", h)
    assert hash_password("x" * 10) != hash_password("x" * 10)  # salt aleatório


def test_verify_rejects_garbage():
    assert not verify_password("a", "lixo")


def test_throttle_locks_after_max_fails():
    t = LoginThrottle()
    for _ in range(MAX_FAILS):
        assert not t.locked("user:a")
        t.fail("user:a")
    assert t.locked("user:a")
    t.reset("user:a")
    assert not t.locked("user:a")
