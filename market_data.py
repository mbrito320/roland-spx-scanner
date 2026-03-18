"""
Market Data Engine for 0DTE SPX Credit Spread Scanner
Confirmed working cloud-friendly data sources:
  SPX price  → Finnhub (SPY*10) PRIMARY, Alpha Vantage (SPY*10) SECONDARY
  VIX        → FRED VIXCLS (1-day lag) PRIMARY, Alpha Vantage VXX proxy SECONDARY
  Futures    → Finnhub (SPY) PRIMARY, Alpha Vantage (SPY) SECONDARY
  Options    → yfinance (fallback to synthetic if blocked)
  Calendar   → Finnhub economic calendar
  Macro      → FRED API
"""

import os
import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import numpy as np

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "DKY52LV6FTXF8N70")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "d6rk0ihr01qrri54egb0d6rk0ihr01qrri54egbg")
FRED_KEY = os.environ.get("FRED_API_KEY", "4184c09ced41242a3ed1a7becc7ba55d")

# Try importing yfinance (may be blocked on cloud IPs)
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not available")


class MarketDataEngine:
    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: Dict = {}
        self._cache_ttl = 60  # seconds
        self._yf_tickers: Dict = {}

    def _yf_ticker(self, symbol: str):
        if not YFINANCE_AVAILABLE:
            return None
        if symbol not in self._yf_tickers:
            self._yf_tickers[symbol] = yf.Ticker(symbol)
        return self._yf_tickers[symbol]

    def _is_cache_valid(self, key: str) -> bool:
        if key in self._cache_time:
            return (datetime.now() - self._cache_time[key]).total_seconds() < self._cache_ttl
        return False

    def _set_cache(self, key: str, value):
        self._cache[key] = value
        self._cache_time[key] = datetime.now()

    # ── SPX Price ─────────────────────────────────────────────────────────────
    def get_spx_price(self) -> Dict:
        if self._is_cache_valid("spx_price"):
            return self._cache["spx_price"]

        # 1. Finnhub SPY quote (real-time, works from cloud)
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": "SPY", "token": FINNHUB_KEY}, timeout=10)
            d = r.json()
            if d.get("c") and d["c"] > 0:
                price = round(d["c"] * 10, 2)
                prev = round(d.get("pc", d["c"]) * 10, 2)
                change = round(price - prev, 2)
                change_pct = round((change / prev) * 100, 2) if prev else 0
                result = {"price": price, "prev_close": prev, "change": change,
                          "change_pct": change_pct, "source": "Finnhub/SPY×10"}
                self._set_cache("spx_price", result)
                logger.info(f"SPX via Finnhub: {price}")
                return result
        except Exception as e:
            logger.warning(f"Finnhub SPX failed: {e}")

        # 2. Alpha Vantage SPY quote
        try:
            r = requests.get("https://www.alphavantage.co/query",
                             params={"function": "GLOBAL_QUOTE", "symbol": "SPY",
                                     "apikey": ALPHA_VANTAGE_KEY}, timeout=10)
            q = r.json().get("Global Quote", {})
            if q.get("05. price"):
                price = round(float(q["05. price"]) * 10, 2)
                prev = round(float(q.get("08. previous close", q["05. price"])) * 10, 2)
                change = round(price - prev, 2)
                change_pct = round((change / prev) * 100, 2) if prev else 0
                result = {"price": price, "prev_close": prev, "change": change,
                          "change_pct": change_pct, "source": "AlphaVantage/SPY×10"}
                self._set_cache("spx_price", result)
                logger.info(f"SPX via Alpha Vantage: {price}")
                return result
        except Exception as e:
            logger.warning(f"Alpha Vantage SPX failed: {e}")

        # 3. yfinance fallback
        if YFINANCE_AVAILABLE:
            for sym, mult in [("^SPX", 1.0), ("SPY", 10.0)]:
                try:
                    data = self._yf_ticker(sym).history(period="2d")
                    if not data.empty:
                        price = round(float(data["Close"].iloc[-1]) * mult, 2)
                        prev = round(float(data["Close"].iloc[-2]) * mult if len(data) >= 2 else price, 2)
                        change = round(price - prev, 2)
                        change_pct = round((change / prev) * 100, 2) if prev else 0
                        result = {"price": price, "prev_close": prev, "change": change,
                                  "change_pct": change_pct, "source": f"yfinance/{sym}"}
                        self._set_cache("spx_price", result)
                        logger.info(f"SPX via yfinance/{sym}: {price}")
                        return result
                except Exception as e:
                    logger.warning(f"yfinance {sym} failed: {e}")

        logger.error("All SPX price sources failed")
        return {"price": 0, "prev_close": 0, "change": 0, "change_pct": 0, "source": "unavailable"}

    # ── VIX ───────────────────────────────────────────────────────────────────
    def get_vix(self) -> Dict:
        if self._is_cache_valid("vix"):
            return self._cache["vix"]

        vix_level = 0.0
        prev_vix = 0.0
        source = "unknown"

        # 1. FRED VIXCLS (official CBOE VIX, 1-day lag — most reliable)
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                             params={"series_id": "VIXCLS", "api_key": FRED_KEY,
                                     "file_type": "json", "sort_order": "desc", "limit": 5},
                             timeout=10)
            obs = r.json().get("observations", [])
            valid = [o for o in obs if o.get("value", ".") != "."]
            if len(valid) >= 1:
                vix_level = float(valid[0]["value"])
                prev_vix = float(valid[1]["value"]) if len(valid) >= 2 else vix_level
                source = f"FRED/VIXCLS ({valid[0]['date']})"
                logger.info(f"VIX via FRED: {vix_level}")
        except Exception as e:
            logger.warning(f"FRED VIX failed: {e}")

        # 2. Alpha Vantage VXX (VIX futures ETF proxy, real-time)
        # VXX typically trades at ~1.5x VIX level; use as directional proxy
        if vix_level == 0:
            try:
                r = requests.get("https://www.alphavantage.co/query",
                                 params={"function": "GLOBAL_QUOTE", "symbol": "VXX",
                                         "apikey": ALPHA_VANTAGE_KEY}, timeout=10)
                q = r.json().get("Global Quote", {})
                if q.get("05. price"):
                    vxx = float(q["05. price"])
                    vxx_prev = float(q.get("08. previous close", vxx))
                    # VXX/VIX ratio is roughly 1.55 historically
                    vix_level = round(vxx / 1.55, 2)
                    prev_vix = round(vxx_prev / 1.55, 2)
                    source = "AlphaVantage/VXX÷1.55"
                    logger.info(f"VIX via VXX proxy: {vix_level}")
            except Exception as e:
                logger.warning(f"Alpha Vantage VXX failed: {e}")

        # 3. Finnhub VXX proxy
        if vix_level == 0:
            try:
                r = requests.get("https://finnhub.io/api/v1/quote",
                                 params={"symbol": "VXX", "token": FINNHUB_KEY}, timeout=10)
                d = r.json()
                if d.get("c") and d["c"] > 0:
                    vix_level = round(d["c"] / 1.55, 2)
                    prev_vix = round(d.get("pc", d["c"]) / 1.55, 2)
                    source = "Finnhub/VXX÷1.55"
                    logger.info(f"VIX via Finnhub VXX: {vix_level}")
            except Exception as e:
                logger.warning(f"Finnhub VXX failed: {e}")

        # 4. yfinance fallback
        if vix_level == 0 and YFINANCE_AVAILABLE:
            try:
                data = self._yf_ticker("^VIX").history(period="5d")
                if not data.empty:
                    vix_level = round(float(data["Close"].iloc[-1]), 2)
                    prev_vix = round(float(data["Close"].iloc[-2]) if len(data) >= 2 else vix_level, 2)
                    source = "yfinance/^VIX"
                    logger.info(f"VIX via yfinance: {vix_level}")
            except Exception as e:
                logger.warning(f"yfinance VIX failed: {e}")

        if vix_level == 0:
            logger.error("All VIX sources failed")
            return {"level": 0, "prev": 0, "change": 0, "assessment": "Data unavailable",
                    "selling_grade": "N/A", "ideal_range": False, "source": "unavailable"}

        change = round(vix_level - prev_vix, 2)
        if vix_level < 12:
            assessment, selling_grade = "Very Low — thin premiums, tight spreads, marginal setups", "C"
        elif vix_level < 15:
            assessment, selling_grade = "Low — decent premiums, be selective with strikes", "B-"
        elif vix_level < 20:
            assessment, selling_grade = "Sweet Spot — ideal for selling premium, fat credits", "A"
        elif vix_level < 25:
            assessment, selling_grade = "Elevated — rich premiums but wider moves, tighten stops", "A-"
        elif vix_level < 30:
            assessment, selling_grade = "High — big premiums but dangerous, reduce size", "B"
        else:
            assessment, selling_grade = "Extreme — crisis-level vol, sit on hands or trade tiny", "D"

        result = {"level": vix_level, "prev": prev_vix, "change": change,
                  "assessment": assessment, "selling_grade": selling_grade,
                  "ideal_range": 15 <= vix_level <= 25, "source": source}
        self._set_cache("vix", result)
        return result

    # ── Futures ───────────────────────────────────────────────────────────────
    def get_futures(self) -> Dict:
        if self._is_cache_valid("futures"):
            return self._cache["futures"]

        # 1. Finnhub SPY (real-time, use as ES proxy)
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": "SPY", "token": FINNHUB_KEY}, timeout=10)
            d = r.json()
            if d.get("c") and d["c"] > 0:
                current = round(d["c"] * 10, 2)
                prev = round(d.get("pc", d["c"]) * 10, 2)
                change = round(current - prev, 2)
                change_pct = round((change / prev) * 100, 2) if prev else 0
                direction = (f"🟢 +{change:.0f} pts (+{change_pct:.2f}%)" if change > 0
                             else f"🔴 {change:.0f} pts ({change_pct:.2f}%)" if change < 0
                             else "⚪ Flat")
                result = {"price": current, "prev": prev, "change": change,
                          "change_pct": change_pct, "direction": direction, "source": "Finnhub/SPY"}
                self._set_cache("futures", result)
                return result
        except Exception as e:
            logger.warning(f"Finnhub futures failed: {e}")

        # 2. Alpha Vantage SPY
        try:
            r = requests.get("https://www.alphavantage.co/query",
                             params={"function": "GLOBAL_QUOTE", "symbol": "SPY",
                                     "apikey": ALPHA_VANTAGE_KEY}, timeout=10)
            q = r.json().get("Global Quote", {})
            if q.get("05. price"):
                current = round(float(q["05. price"]) * 10, 2)
                prev = round(float(q.get("08. previous close", q["05. price"])) * 10, 2)
                change = round(current - prev, 2)
                change_pct = round((change / prev) * 100, 2) if prev else 0
                direction = (f"🟢 +{change:.0f} pts (+{change_pct:.2f}%)" if change > 0
                             else f"🔴 {change:.0f} pts ({change_pct:.2f}%)" if change < 0
                             else "⚪ Flat")
                result = {"price": current, "prev": prev, "change": change,
                          "change_pct": change_pct, "direction": direction, "source": "AlphaVantage/SPY"}
                self._set_cache("futures", result)
                return result
        except Exception as e:
            logger.warning(f"Alpha Vantage futures failed: {e}")

        # 3. yfinance ES=F fallback
        if YFINANCE_AVAILABLE:
            try:
                data = self._yf_ticker("ES=F").history(period="2d")
                if not data.empty:
                    current = round(float(data["Close"].iloc[-1]), 2)
                    prev = round(float(data["Close"].iloc[-2]) if len(data) >= 2 else current, 2)
                    change = round(current - prev, 2)
                    change_pct = round((change / prev) * 100, 2) if prev else 0
                    direction = (f"🟢 +{change:.0f} pts (+{change_pct:.2f}%)" if change > 0
                                 else f"🔴 {change:.0f} pts ({change_pct:.2f}%)" if change < 0
                                 else "⚪ Flat")
                    result = {"price": current, "prev": prev, "change": change,
                              "change_pct": change_pct, "direction": direction, "source": "yfinance/ES=F"}
                    self._set_cache("futures", result)
                    return result
            except Exception as e:
                logger.warning(f"yfinance futures failed: {e}")

        return {"price": 0, "prev": 0, "change": 0, "change_pct": 0,
                "direction": "Data unavailable", "source": "unavailable"}

    # ── Economic Calendar ─────────────────────────────────────────────────────
    def get_economic_calendar(self) -> Dict:
        if self._is_cache_valid("calendar"):
            return self._cache["calendar"]
        today = datetime.now().strftime("%Y-%m-%d")
        major_events, all_events = [], []
        HIGH_IMPACT = ["CPI", "Consumer Price", "FOMC", "Federal Reserve", "Interest Rate",
                       "Non-Farm", "Nonfarm", "NFP", "Employment", "GDP", "Gross Domestic",
                       "PCE", "Personal Consumption", "PPI", "Producer Price", "Retail Sales",
                       "ISM", "PMI", "Jobless Claims", "Unemployment", "Fed Chair", "Powell"]
        try:
            r = requests.get("https://finnhub.io/api/v1/calendar/economic",
                             params={"from": today, "to": today, "token": FINNHUB_KEY}, timeout=10)
            for ev in r.json().get("economicCalendar", []):
                if ev.get("country", "") != "US":
                    continue
                name = ev.get("event", "Unknown")
                info = {"name": name, "time": ev.get("time", "N/A"),
                        "impact": ev.get("impact", "low"), "actual": ev.get("actual", ""),
                        "estimate": ev.get("estimate", ""), "previous": ev.get("prev", "")}
                all_events.append(info)
                if any(kw.lower() in name.lower() for kw in HIGH_IMPACT) or ev.get("impact") == "high":
                    major_events.append(info)
        except Exception as e:
            logger.warning(f"Finnhub calendar failed: {e}")

        has_major = len(major_events) > 0
        caution = ("⚠️ MAJOR EVENT DAY — consider sitting out or trading after release"
                   if has_major else "✅ No major catalysts — clear to trade")
        result = {"date": today, "major_events": major_events, "all_events": all_events[:10],
                  "has_major": has_major, "caution": caution}
        self._set_cache("calendar", result)
        return result

    # ── Macro Context ─────────────────────────────────────────────────────────
    def get_macro_context(self) -> Dict:
        if self._is_cache_valid("macro"):
            return self._cache["macro"]
        result = {}
        for key, series_id in [("fed_funds", "FEDFUNDS"), ("treasury_10y", "DGS10"), ("treasury_2y", "DGS2")]:
            try:
                r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                                 params={"series_id": series_id, "api_key": FRED_KEY,
                                         "file_type": "json", "sort_order": "desc", "limit": 5},
                                 timeout=10)
                for o in r.json().get("observations", []):
                    val = o.get("value", ".")
                    if val != ".":
                        result[key] = float(val)
                        break
            except Exception as e:
                logger.warning(f"FRED {series_id} failed: {e}")
                result[key] = None

        if result.get("treasury_10y") and result.get("treasury_2y"):
            yc = round(result["treasury_10y"] - result["treasury_2y"], 2)
            result["yield_curve"] = yc
            result["yield_curve_status"] = ("Inverted (recession signal)" if yc < 0
                                            else "Flat (caution)" if yc < 0.5 else "Normal")
        else:
            result["yield_curve"] = None
            result["yield_curve_status"] = "N/A"
        self._set_cache("macro", result)
        return result

    # ── Options Data ──────────────────────────────────────────────────────────
    def get_options_data(self) -> Dict:
        if self._is_cache_valid("options"):
            return self._cache["options"]

        spx_data = self.get_spx_price()
        spx_price = spx_data["price"]
        if spx_price == 0:
            return {"error": "Cannot get SPX price", "note": "⚠️ SPX price unavailable"}

        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        # Try yfinance for options chain (best free source for options)
        if YFINANCE_AVAILABLE:
            for sym, mult in [("^SPX", 1.0), ("SPX", 1.0), ("SPY", 10.0)]:
                try:
                    ticker = self._yf_ticker(sym)
                    expirations = ticker.options
                    if not expirations:
                        continue
                    target_exp = None
                    for exp in expirations:
                        if exp in (today, tomorrow):
                            target_exp = exp
                            break
                    if not target_exp:
                        for exp in sorted(expirations):
                            if datetime.strptime(exp, "%Y-%m-%d") >= datetime.now():
                                target_exp = exp
                                break
                    if not target_exp:
                        continue
                    chain = ticker.option_chain(target_exp)
                    calls, puts = chain.calls, chain.puts
                    if calls.empty or puts.empty:
                        continue
                    ref_price = spx_price / mult
                    result = self._analyze_options_chain(calls, puts, ref_price, spx_price, mult, target_exp, sym)
                    if result:
                        self._set_cache("options", result)
                        logger.info(f"Options via yfinance/{sym}: exp={target_exp}")
                        return result
                except Exception as e:
                    logger.warning(f"Options fetch failed for {sym}: {e}")

        # Fallback: synthetic estimates from VIX
        logger.warning("All options sources failed — using synthetic estimates")
        result = self._generate_synthetic_options(spx_price)
        self._set_cache("options", result)
        return result

    def _analyze_options_chain(self, calls, puts, ref_price, spx_price, multiplier,
                                expiration, ticker_symbol) -> Optional[Dict]:
        try:
            atm_strike = self._find_nearest_strike(calls, ref_price)
            if atm_strike is None:
                return None
            atm_call = calls[calls["strike"] == atm_strike]
            atm_put = puts[puts["strike"] == atm_strike]
            if atm_call.empty or atm_put.empty:
                return None
            call_mid = self._get_mid_price(atm_call.iloc[0])
            put_mid = self._get_mid_price(atm_put.iloc[0])
            straddle = (call_mid + put_mid) * multiplier
            expected_move = round(straddle * 0.85, 2)
            put_spread = self._find_put_credit_spread(puts, ref_price, multiplier)
            call_spread = self._find_call_credit_spread(calls, ref_price, multiplier)
            iron_condor = None
            if put_spread and call_spread:
                tc = round(put_spread["credit"] + call_spread["credit"], 2)
                sw = max(put_spread["width"], call_spread["width"])
                iron_condor = {
                    "total_credit": tc, "max_profit": tc, "max_loss": round(sw - tc, 2),
                    "lower_breakeven": round(put_spread.get("short_strike_spx", put_spread["short_strike"]) - tc, 2),
                    "upper_breakeven": round(call_spread.get("short_strike_spx", call_spread["short_strike"]) + tc, 2),
                }
            return {
                "spx_price": spx_price, "expiration": expiration, "ticker_used": ticker_symbol,
                "multiplier": multiplier, "atm_strike": round(atm_strike * multiplier, 1),
                "straddle_price": round(straddle, 2), "expected_move": expected_move,
                "expected_range_low": round(spx_price - expected_move, 2),
                "expected_range_high": round(spx_price + expected_move, 2),
                "put_spread": put_spread, "call_spread": call_spread, "iron_condor": iron_condor,
            }
        except Exception as e:
            logger.error(f"Options chain analysis error: {e}")
            return None

    def _find_nearest_strike(self, chain, price) -> Optional[float]:
        if chain.empty:
            return None
        strikes = chain["strike"].values
        return float(strikes[np.argmin(np.abs(strikes - price))])

    def _get_mid_price(self, row) -> float:
        bid = row.get("bid", 0) or 0
        ask = row.get("ask", 0) or 0
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return row.get("lastPrice", 0) or 0

    def _estimate_delta_from_otm(self, strike, ref_price, is_put=True) -> float:
        if ref_price == 0:
            return 0.5
        otm_pct = ((ref_price - strike) / ref_price) if is_put else ((strike - ref_price) / ref_price)
        thresholds = [(0, 0.50), (0.005, 0.45), (0.01, 0.35), (0.015, 0.25),
                      (0.02, 0.18), (0.025, 0.14), (0.03, 0.10), (0.04, 0.07),
                      (0.05, 0.05), (0.07, 0.03)]
        for threshold, delta in reversed(thresholds):
            if otm_pct >= threshold:
                return delta
        return 0.01

    def _find_put_credit_spread(self, puts, ref_price, multiplier) -> Optional[Dict]:
        try:
            best_short, best_delta = None, None
            for _, row in puts.sort_values("strike", ascending=False).iterrows():
                strike = row["strike"]
                if strike >= ref_price:
                    continue
                delta = abs(float(row.get("delta", 0) or 0)) if "delta" in row.index else 0
                if delta == 0:
                    delta = self._estimate_delta_from_otm(strike, ref_price, is_put=True)
                if 0.08 <= delta <= 0.18:
                    best_short, best_delta = row, delta
                    break
            if best_short is None:
                nearest = self._find_nearest_strike(puts, ref_price * 0.975)
                if nearest:
                    best_short = puts[puts["strike"] == nearest].iloc[0]
                    best_delta = self._estimate_delta_from_otm(nearest, ref_price, is_put=True)
                else:
                    return None
            short_strike = best_short["strike"]
            short_prem = self._get_mid_price(best_short)
            long_strike, long_prem = None, 0
            for w in [5 / multiplier, 10 / multiplier]:
                cands = puts[puts["strike"] == short_strike - w]
                if not cands.empty:
                    long_strike, long_prem = short_strike - w, self._get_mid_price(cands.iloc[0])
                    break
            if long_strike is None:
                below = puts[puts["strike"] < short_strike].sort_values("strike", ascending=False)
                if not below.empty:
                    long_strike, long_prem = below.iloc[0]["strike"], self._get_mid_price(below.iloc[0])
                else:
                    return None
            credit = round((short_prem - long_prem) * multiplier, 2)
            width = round((short_strike - long_strike) * multiplier, 2)
            return {
                "short_strike": round(short_strike, 1), "long_strike": round(long_strike, 1),
                "short_strike_spx": round(short_strike * multiplier, 1),
                "long_strike_spx": round(long_strike * multiplier, 1),
                "credit": credit, "credit_dollar": round(credit * 100, 2),
                "max_loss": round(width - credit, 2), "max_loss_dollar": round((width - credit) * 100, 2),
                "width": width, "delta": round(best_delta, 3), "multiplier": multiplier,
            }
        except Exception as e:
            logger.error(f"Put spread error: {e}")
            return None

    def _find_call_credit_spread(self, calls, ref_price, multiplier) -> Optional[Dict]:
        try:
            best_short, best_delta = None, None
            for _, row in calls.sort_values("strike", ascending=True).iterrows():
                strike = row["strike"]
                if strike <= ref_price:
                    continue
                delta = abs(float(row.get("delta", 0) or 0)) if "delta" in row.index else 0
                if delta == 0:
                    delta = self._estimate_delta_from_otm(strike, ref_price, is_put=False)
                if 0.08 <= delta <= 0.18:
                    best_short, best_delta = row, delta
                    break
            if best_short is None:
                nearest = self._find_nearest_strike(calls, ref_price * 1.025)
                if nearest:
                    best_short = calls[calls["strike"] == nearest].iloc[0]
                    best_delta = self._estimate_delta_from_otm(nearest, ref_price, is_put=False)
                else:
                    return None
            short_strike = best_short["strike"]
            short_prem = self._get_mid_price(best_short)
            long_strike, long_prem = None, 0
            for w in [5 / multiplier, 10 / multiplier]:
                cands = calls[calls["strike"] == short_strike + w]
                if not cands.empty:
                    long_strike, long_prem = short_strike + w, self._get_mid_price(cands.iloc[0])
                    break
            if long_strike is None:
                above = calls[calls["strike"] > short_strike].sort_values("strike", ascending=True)
                if not above.empty:
                    long_strike, long_prem = above.iloc[0]["strike"], self._get_mid_price(above.iloc[0])
                else:
                    return None
            credit = round((short_prem - long_prem) * multiplier, 2)
            width = round((long_strike - short_strike) * multiplier, 2)
            return {
                "short_strike": round(short_strike, 1), "long_strike": round(long_strike, 1),
                "short_strike_spx": round(short_strike * multiplier, 1),
                "long_strike_spx": round(long_strike * multiplier, 1),
                "credit": credit, "credit_dollar": round(credit * 100, 2),
                "max_loss": round(width - credit, 2), "max_loss_dollar": round((width - credit) * 100, 2),
                "width": width, "delta": round(best_delta, 3), "multiplier": multiplier,
            }
        except Exception as e:
            logger.error(f"Call spread error: {e}")
            return None

    def _generate_synthetic_options(self, spx_price: float) -> Dict:
        """Generate synthetic spread estimates when live options chain is unavailable."""
        vix_data = self.get_vix()
        vix = vix_data["level"] if vix_data["level"] > 0 else 20
        daily_vol = vix / (252 ** 0.5) / 100
        expected_move = round(spx_price * daily_vol, 2)
        put_short = round(spx_price * 0.975 / 5) * 5
        put_long = put_short - 5
        put_credit = round(expected_move * 0.12, 2)
        call_short = round(spx_price * 1.025 / 5) * 5
        call_long = call_short + 5
        call_credit = round(expected_move * 0.10, 2)
        today = datetime.now().strftime("%Y-%m-%d")
        return {
            "spx_price": spx_price, "expiration": today, "ticker_used": "SYNTHETIC",
            "multiplier": 1, "atm_strike": round(spx_price / 5) * 5,
            "straddle_price": round(expected_move / 0.85, 2), "expected_move": expected_move,
            "expected_range_low": round(spx_price - expected_move, 2),
            "expected_range_high": round(spx_price + expected_move, 2),
            "put_spread": {
                "short_strike": put_short, "long_strike": put_long,
                "short_strike_spx": put_short, "long_strike_spx": put_long,
                "credit": put_credit, "credit_dollar": round(put_credit * 100, 2),
                "max_loss": round(5 - put_credit, 2), "max_loss_dollar": round((5 - put_credit) * 100, 2),
                "width": 5, "delta": 0.12, "multiplier": 1,
            },
            "call_spread": {
                "short_strike": call_short, "long_strike": call_long,
                "short_strike_spx": call_short, "long_strike_spx": call_long,
                "credit": call_credit, "credit_dollar": round(call_credit * 100, 2),
                "max_loss": round(5 - call_credit, 2), "max_loss_dollar": round((5 - call_credit) * 100, 2),
                "width": 5, "delta": 0.10, "multiplier": 1,
            },
            "iron_condor": {
                "total_credit": round(put_credit + call_credit, 2),
                "max_profit": round(put_credit + call_credit, 2),
                "max_loss": round(5 - (put_credit + call_credit), 2),
                "lower_breakeven": round(put_short - (put_credit + call_credit), 2),
                "upper_breakeven": round(call_short + (put_credit + call_credit), 2),
            },
            "note": "⚠️ Synthetic estimates — live options chain unavailable (market closed or data blocked).",
        }

    def get_full_snapshot(self) -> Dict:
        return {
            "spx": self.get_spx_price(),
            "vix": self.get_vix(),
            "futures": self.get_futures(),
            "calendar": self.get_economic_calendar(),
            "macro": self.get_macro_context(),
            "options": self.get_options_data(),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S ET"),
        }
