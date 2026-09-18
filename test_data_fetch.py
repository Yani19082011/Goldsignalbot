"""
Offline tests for the multi-provider fetch/fallback logic in data_fetch.py.
No real network calls - yfinance/requests are monkeypatched, since this
sandbox's network policy blocks the real providers anyway (see
test_signals.py's docstring). Run with: python3 test_data_fetch.py
"""
import pandas as pd

import config
import data_fetch

GOLD = config.INSTRUMENTS[0]
assert GOLD["key"] == "GOLD"
TECH100 = next((i for i in config.INSTRUMENTS if i["key"] == "TECH100"), None)


def _sample_df(n=60, start=2000.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = pd.Series([start + i * 0.5 for i in range(n)], index=idx)
    return pd.DataFrame({
        "Open": close, "High": close * 1.001, "Low": close * 0.999,
        "Close": close, "Volume": 100,
    })


def test_falls_back_to_second_yahoo_ticker_when_first_is_empty():
    calls = []

    def fake_fetch_yfinance(ticker, period, interval):
        calls.append(ticker)
        if ticker == GOLD["yahoo_ticker"]:
            return pd.DataFrame()  # simulate empty/blocked response
        return _sample_df()

    orig = data_fetch._fetch_yfinance
    data_fetch._fetch_yfinance = fake_fetch_yfinance
    try:
        df = data_fetch.get_candles(GOLD, interval="15m")
        assert calls == [GOLD["yahoo_ticker"], GOLD["yahoo_fallback"]]
        assert not df.empty
        print(f"[yahoo-fallback] tried {calls}, got {len(df)} rows - OK")
    finally:
        data_fetch._fetch_yfinance = orig


def test_falls_back_to_twelvedata_when_yahoo_fails_entirely():
    def fake_fetch_yfinance(ticker, period, interval):
        raise RuntimeError("simulated Yahoo outage")

    def fake_fetch_twelvedata(symbol, interval, outputsize=500):
        return _sample_df()

    orig_yf = data_fetch._fetch_yfinance
    orig_td = data_fetch._fetch_twelvedata
    orig_key = config.TWELVEDATA_API_KEY
    data_fetch._fetch_yfinance = fake_fetch_yfinance
    data_fetch._fetch_twelvedata = fake_fetch_twelvedata
    config.TWELVEDATA_API_KEY = "fake-key-for-test"
    try:
        df = data_fetch.get_candles(GOLD, interval="15m")
        assert not df.empty
        print("[twelvedata-fallback] Yahoo down, Twelve Data served data - OK")
    finally:
        data_fetch._fetch_yfinance = orig_yf
        data_fetch._fetch_twelvedata = orig_td
        config.TWELVEDATA_API_KEY = orig_key


def test_twelvedata_is_skipped_without_an_api_key():
    def fake_fetch_yfinance(ticker, period, interval):
        raise RuntimeError("simulated Yahoo outage")

    def fake_fetch_stooq(symbol):
        return _sample_df(n=30)

    orig_yf = data_fetch._fetch_yfinance
    orig_stooq = data_fetch._fetch_stooq
    orig_key = config.TWELVEDATA_API_KEY
    data_fetch._fetch_yfinance = fake_fetch_yfinance
    data_fetch._fetch_stooq = fake_fetch_stooq
    config.TWELVEDATA_API_KEY = ""  # not configured
    try:
        df = data_fetch.get_candles(GOLD, interval="15m")
        assert not df.empty, "should still fall through to stooq"
        print("[no-twelvedata-key] correctly skipped straight to stooq - OK")
    finally:
        data_fetch._fetch_yfinance = orig_yf
        data_fetch._fetch_stooq = orig_stooq
        config.TWELVEDATA_API_KEY = orig_key


def test_raises_only_when_every_provider_fails():
    def fake_fail(*args, **kwargs):
        raise RuntimeError("down")

    def fake_empty(*args, **kwargs):
        return pd.DataFrame()

    orig_yf, orig_td, orig_stooq, orig_key = (
        data_fetch._fetch_yfinance, data_fetch._fetch_twelvedata,
        data_fetch._fetch_stooq, config.TWELVEDATA_API_KEY,
    )
    data_fetch._fetch_yfinance = fake_fail
    data_fetch._fetch_twelvedata = fake_empty
    data_fetch._fetch_stooq = fake_fail
    config.TWELVEDATA_API_KEY = "fake-key"
    try:
        try:
            data_fetch.get_candles(GOLD, interval="15m")
            raise AssertionError("expected RuntimeError when every provider fails")
        except RuntimeError as e:
            assert "any source" in str(e)
            print("[all-providers-down] correctly raised RuntimeError - OK")
    finally:
        data_fetch._fetch_yfinance = orig_yf
        data_fetch._fetch_twelvedata = orig_td
        data_fetch._fetch_stooq = orig_stooq
        config.TWELVEDATA_API_KEY = orig_key


def test_twelvedata_interval_mapping_and_parsing():
    sample_response = {
        "status": "ok",
        "values": [
            {"datetime": "2026-01-01 00:00:00", "open": "2000.0", "high": "2001.0", "low": "1999.0", "close": "2000.5", "volume": "0"},
            {"datetime": "2026-01-01 00:15:00", "open": "2000.5", "high": "2002.0", "low": "2000.0", "close": "2001.5", "volume": "0"},
        ],
    }

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return sample_response

    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResp()

    import requests
    orig_get = requests.get
    orig_key = config.TWELVEDATA_API_KEY
    requests.get = fake_get
    config.TWELVEDATA_API_KEY = "fake-key"
    try:
        df = data_fetch._fetch_twelvedata(GOLD["twelvedata_symbol"], "15m")
        assert captured["params"]["interval"] == "15min"
        assert captured["params"]["symbol"] == GOLD["twelvedata_symbol"]
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(df) == 2
        assert df["Close"].iloc[-1] == 2001.5
        print("[twelvedata-parsing] interval mapped to '15min' and OHLC parsed correctly - OK")
    finally:
        requests.get = orig_get
        config.TWELVEDATA_API_KEY = orig_key


def test_tech100_instrument_is_configured_with_its_own_symbols():
    assert TECH100 is not None, "TECH100 should be enabled by default (ENABLE_TECH100 defaults to true)"
    assert TECH100["yahoo_ticker"] != GOLD["yahoo_ticker"]
    assert TECH100["twelvedata_symbol"] != GOLD["twelvedata_symbol"]

    calls = []

    def fake_fetch_yfinance(ticker, period, interval):
        calls.append(ticker)
        return _sample_df(start=20000.0)

    orig = data_fetch._fetch_yfinance
    data_fetch._fetch_yfinance = fake_fetch_yfinance
    try:
        df = data_fetch.get_candles(TECH100, interval="15m")
        assert calls == [TECH100["yahoo_ticker"]], f"expected Tech100's own ticker, got {calls}"
        assert not df.empty
        print(f"[tech100-instrument] fetched with {calls[0]} (not gold's ticker) - OK")
    finally:
        data_fetch._fetch_yfinance = orig


def test_tech100_qqq_reading_gets_scaled_to_index_level():
    # Regression test for the REAL incident, round 1 (18.09): Twelve
    # Data/QQQ returns real, well-formed candles (~$718, an ETF share
    # price) while Yahoo Finance is down (common from Render's IP) - the
    # fetch must now scale that up to the actual Nasdaq-100 index/CFD level
    # (~$29,800) via TECH100_ETF_SCALE, not reject it and not emit it raw.
    assert TECH100 is not None
    assert TECH100["twelvedata_symbol"] == "QQQ"
    assert TECH100.get("twelvedata_scale", 1.0) > 1.0

    def fake_fetch_yfinance(ticker, period, interval):
        raise RuntimeError("simulated Yahoo outage (matches the real incident)")

    def fake_fetch_twelvedata(symbol, interval, outputsize=500):
        return _sample_df(n=1, start=718.0)  # raw QQQ ETF share price

    orig_yf, orig_td, orig_key = (
        data_fetch._fetch_yfinance, data_fetch._fetch_twelvedata, config.TWELVEDATA_API_KEY,
    )
    data_fetch._fetch_yfinance = fake_fetch_yfinance
    data_fetch._fetch_twelvedata = fake_fetch_twelvedata
    config.TWELVEDATA_API_KEY = "fake-key"
    try:
        df = data_fetch.get_candles(TECH100, interval="15m")
        assert not df.empty
        last_close = float(df["Close"].iloc[-1])
        expected = 718.0 * TECH100["twelvedata_scale"]
        assert abs(last_close - expected) < 1.0, f"expected ~{expected:.0f} (scaled), got {last_close:.0f}"
        assert last_close > TECH100["sanity_min"]
        print(f"[etf-scale] raw QQQ reading (718) scaled to index level ({last_close:.0f}) - OK")
    finally:
        data_fetch._fetch_yfinance = orig_yf
        data_fetch._fetch_twelvedata = orig_td
        config.TWELVEDATA_API_KEY = orig_key


def test_sanity_check_still_guards_an_unscaled_wrong_reading():
    # The sanity check is a second, independent safety net - even if a
    # future config change reintroduces an unscaled/mis-scaled provider
    # (e.g. someone sets a symbol's scale factor back to 1.0 by mistake),
    # a reading nowhere near the instrument's plausible range must still be
    # rejected and the fetch must fall through to the next provider,
    # instead of ever emailing a signal on the wrong price scale.
    assert TECH100 is not None

    def fake_fetch_yfinance(ticker, period, interval):
        raise RuntimeError("simulated Yahoo outage")

    def fake_fetch_twelvedata(symbol, interval, outputsize=500):
        return _sample_df(n=1, start=718.0)  # unscaled QQQ-level reading

    def fake_fetch_stooq(symbol):
        return _sample_df(n=1, start=29800.0)  # correct index/CFD scale, already

    instrument = dict(TECH100)
    instrument["twelvedata_scale"] = 1.0  # simulate the misconfiguration
    instrument["stooq_scale"] = 1.0  # this fallback's fake data is already correctly scaled

    orig_yf, orig_td, orig_stooq, orig_key = (
        data_fetch._fetch_yfinance, data_fetch._fetch_twelvedata,
        data_fetch._fetch_stooq, config.TWELVEDATA_API_KEY,
    )
    data_fetch._fetch_yfinance = fake_fetch_yfinance
    data_fetch._fetch_twelvedata = fake_fetch_twelvedata
    data_fetch._fetch_stooq = fake_fetch_stooq
    config.TWELVEDATA_API_KEY = "fake-key"
    try:
        df = data_fetch.get_candles(instrument, interval="15m")
        assert not df.empty
        last_close = float(df["Close"].iloc[-1])
        assert last_close > 8000, (
            f"unscaled QQQ-like reading ({last_close}) leaked through instead of being rejected"
        )
        print(f"[sanity-check] unscaled reading (718) correctly rejected, fell through to stooq ({last_close:.0f}) - OK")
    finally:
        data_fetch._fetch_yfinance = orig_yf
        data_fetch._fetch_twelvedata = orig_td
        data_fetch._fetch_stooq = orig_stooq
        config.TWELVEDATA_API_KEY = orig_key


if __name__ == "__main__":
    tests = [
        test_falls_back_to_second_yahoo_ticker_when_first_is_empty,
        test_falls_back_to_twelvedata_when_yahoo_fails_entirely,
        test_twelvedata_is_skipped_without_an_api_key,
        test_raises_only_when_every_provider_fails,
        test_twelvedata_interval_mapping_and_parsing,
        test_tech100_instrument_is_configured_with_its_own_symbols,
        test_tech100_qqq_reading_gets_scaled_to_index_level,
        test_sanity_check_still_guards_an_unscaled_wrong_reading,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"FAIL: {t.__name__}: {e}")
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("All data_fetch tests passed.")
