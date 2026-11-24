"""Response cache behavior."""

from server.cache import ResponseCache


def test_hit_then_cleared_by_new_version():
    cache = ResponseCache()
    cache.put("k", 1, "answer")
    assert cache.get("k", 1) == "answer"
    assert cache.get("k", 2) is None
    assert cache.get("k", 1) is None


def test_entry_expires():
    now = [0.0]
    cache = ResponseCache(clock=lambda: now[0])
    cache.put("k", 1, "answer", ttl=86400)
    now[0] = 86399
    assert cache.get("k", 1) == "answer"
    now[0] = 86400
    assert cache.get("k", 1) is None


def test_key_ignores_order_and_case():
    assert ResponseCache.key("t", {"a": "X", "b": 1}) == ResponseCache.key("t", {"b": 1, "a": "x"})
