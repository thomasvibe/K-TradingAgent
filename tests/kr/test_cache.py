import time

import pandas as pd
import pytest

from tradingagents.dataflows.kr import cache


@pytest.mark.unit
def test_cached_json_hits_and_ttl(kr_env):
    calls = []

    def fetch():
        calls.append(1)
        return {"a": 1}

    assert cache.cached_json("t", ["k"], fetch, ttl=None) == {"a": 1}
    assert cache.cached_json("t", ["k"], fetch, ttl=None) == {"a": 1}
    assert len(calls) == 1
    c = cache.KrCache.instance()
    c.set("t:expired", "t", "{}", ttl=-1)
    assert c.get("t:expired") is None


@pytest.mark.unit
def test_cached_frame_roundtrips_datetime_index(kr_env):
    idx = pd.to_datetime(["2025-09-10", "2025-09-11"])
    df = pd.DataFrame({"종가": [1, 2], "이름": ["가", "나"]}, index=idx)
    df.index.name = "날짜"
    out = cache.cached_frame("f", ["x"], lambda: df, ttl=None)
    again = cache.cached_frame("f", ["x"], lambda: pd.DataFrame(), ttl=None)
    for got in (out, again):
        assert isinstance(got.index, pd.DatetimeIndex)
        assert got.index.name == "날짜"
        assert list(got["이름"]) == ["가", "나"]


@pytest.mark.unit
def test_ttl_for_window_immutable_when_historical(kr_env):
    assert cache.ttl_for_window("2025-09-11") is None
    assert cache.ttl_for_window("2025-10-01") == 900.0  # "today" frozen at 2025-10-01


@pytest.mark.unit
def test_with_retry_respects_no_retry(kr_env):
    class Stop(Exception):
        pass

    n = []

    def fn():
        n.append(1)
        raise Stop()

    with pytest.raises(Stop):
        cache.with_retry(fn, source="x", no_retry_on=(Stop,))
    assert len(n) == 1


@pytest.mark.unit
def test_purge_older_than(kr_env):
    c = cache.KrCache.instance()
    c.set("old", "s", "{}", ttl=None)
    c.connect().execute("UPDATE api_cache SET created_at = ?", (time.time() - 10 * 86400,))
    c.connect().commit()
    assert c.purge_older_than(5) == 1
