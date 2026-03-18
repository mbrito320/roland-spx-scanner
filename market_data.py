"""
Market Data Engine for 0DTE SPX Credit Spread Scanner
Primary: Alpha Vantage API (works from cloud IPs)
Fallback: Yahoo Finance (yfinance) for options chain
All API keys read from environment variables for cloud deployment.
"""

import os
import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List
import numpy as np

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "DKY52LV6FTXF8N70")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "d6rk0ihr01qrri54egb0d6rk0ihr01qrri54egbg")
FRED_KEY = os.environ.get("FRED_API_KEY", "4184c09ced41242a3ed1a7becc7ba55d")

# Try importing yfinance (may fail on cloud)
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not available")


class MarketDataEngine:
    def __init__(self):
        self._cache = {}
        self._cache_time = {}
        self._cache_ttl = 60
        # Lazy-init yfinance tickers
        self._yf_tickers = {}

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

    # ── SPX Price ────────────────────────────────────────────────────────────
    def get_spx_price(self) -> Dict:
        if self._is_cache_valid("spx_price"):
            return self._cache["spx_price"]

        # 1. Try Alpha Vantage (SPY as proxy, multiply by 10)
        result = self._get_spx_via_alpha_vantage()
        if result and result["price"] > 0:
            self._set_cache("spx_price", result)
            return result

        # 2. Try Finnhub
        result = self._get_spx_via_finnhub()
        if result and result["price"] > 0:
            self._set_cache("spx_price", result)
            return result

        # 3. Try yfinance
        result = self._get_spx_via_yfinance()
        if result and result["price"] > 0:
            self._set_cache("spx_price", result)
            return result

        return {"price": 0, "prev_close": 0, "change": 0, "change_pct": 0, "source": "unavailable"}

    def _get_spx_via_alpha_vantage(self) -> Optional[Dict]:
        """Use Alpha Vantage GLOBAL_QUOTE for SPY, multiply by 10 to estimate SPX."""
        try:
            url = "https://www.alphavantage.co/query"
            # Try SPY first (SPX proxy)
            for symbol, multiplier in [("SPY", 10.0), ("VOO", 10.0)]:
                params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": ALPHA_VANTAGE_KEY}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                quote = data.get("Global Quote", {})
                if not quote or "05. price" not in quote:
                    continue
                price = float(quote["05. price"]) * multiplier
                prev_close = float(quote["08. previous close"]) * multiplier
                change = price - prev_close
                change_pct = (change / prev_close) * 100 if prev_close else 0
                return {
                    "price": round(price, 2), "prev_close": round(prev_close, 2),
                    "change": round(change, 2), "change_pct": round(change_pct, 2),
                    "source": f"AlphaVantage/{symbol}*{multiplier:.0f}"
                }
        except Exception as e:
            logger.warning(f"Alpha Vantage SPX fetch failed: {e}")
        return None

    def _get_spx_via_finnhub(self) -> Optional[Dict]:
        """Use Finnhub quote for SPY as SPX proxy."""
        try:
            url = "https://finnhub.io/api/v1/quote"
            params = {"symbol": "SPY", "token": FINNHUB_KEY}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get("c") and data["c"] > 0:
                price = data["c"] * 10
                prev = data.get("pc", data["c"]) * 10
                change = price - prev
                change_pct = (change / prev) * 100 if prev else 0
                return {
                    "price": round(price, 2), "prev_close": round(prev, 2),
                    "change": round(change, 2), "change_pct": round(change_pct, 2),
                    "source": "Finnhub/SPY*10"
                }
        except Exception as e:
            logger.warning(f"Finnhub SPX fetch failed: {e}")
        return None

    def _get_spx_via_yfinance(self) -> Optional[Dict]:
        """Fallback to yfinance."""
        if not YFINANCE_AVAILABLE:
            return None
        for symbol, multiplier in [("^SPX", 1.0), ("SPY", 10.0)]:
            try:
                ticker = self._yf_ticker(symbol)
                data = ticker.history(period="2d")
                if data.empty or len(data) < 1:
                    continue
                current = float(data['Close'].iloc[-1]) * multiplier
                prev = float(data['Close'].iloc[-2]) * multiplier if len(data) >= 2 else current
                change = current - prev
                change_pct = (change / prev) * 100 if prev else 0
                return {
                    "price": round(current, 2), "prev_close": round(prev, 2),
                    "change": round(change, 2), "change_pct": round(change_pct, 2),
                    "source": f"yfinance/{symbol}"
                }
            except Exception as e:
                logger.warning(f"yfinance {symbol} failed: {e}")
        return None

    # ── VIX ──────────────────────────────────────────────────────────────────
    def get_vix(self) -> Dict:
        if self._is_cache_valid("vix"):
            return self._cache["vix"]

        vix_level = 0

        # 1. Alpha Vantage — VIX via CBOE
        try:
            url = "https://www.alphavantage.co/query"
            params = {"function": "GLOBAL_QUOTE", "symbol": "VIX", "apikey": ALPHA_VANTAGE_KEY}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            quote = data.get("Global Quote", {})
            if quote and "05. price" in quote:
                vix_level = float(quote["05. price"])
                prev = float(quote.get("08. previous close", vix_level))
                change = vix_level - prev
                logger.info(f"VIX from Alpha Vantage: {vix_level}")
        except Exception as e:
            logger.warning(f"Alpha Vantage VIX failed: {e}")

        # 2. Finnhub
        if vix_level == 0:
            try:
                url = "https://finnhub.io/api/v1/quote"
                params = {"symbol": "VIX", "token": FINNHUB_KEY}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                if data.get("c") and data["c"] > 0:
                    vix_level = data["c"]
                    prev = data.get("pc", vix_level)
                    change = vix_level - prev
                    logger.info(f"VIX from Finnhub: {vix_level}")
            except Exception as e:
                logger.warning(f"Finnhub VIX failed: {e}")

        # 3. yfinance fallback
        if vix_level == 0 and YFINANCE_AVAILABLE:
            try:
                ticker = self._yf_ticker("^VIX")
                data = ticker.history(period="5d")
                if not data.empty:
                    vix_level = float(data['Close'].iloc[-1])
                    prev = float(data['Close'].iloc[-2]) if len(data) >= 2 else vix_level
                    change = vix_level - prev
            except Exception as e:
                logger.warning(f"yfinance VIX failed: {e}")

        if vix_level == 0:
            return {"level": 0, "prev": 0, "change": 0, "assessment": "Data unavailable",
                    "selling_grade": "N/A", "ideal_range": False}

        prev = vix_level  # fallback if not set above
        change = 0
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

        result = {"level": round(vix_level, 2), "prev": round(prev, 2), "change": round(change, 2),
                  "assessment": assessment, "selling_grade": selling_grade,
                  "ideal_range": 15 <= vix_level <= 25}
        self._set_cache("vix", result)
        return result

    # ── Futures ──────────────────────────────────────────────────────────────
    def get_futures(self) -> Dict:
        if self._is_cache_valid("futures"):
            return self._cache["futures"]

        # 1. Finnhub for ES futures
        try:
            url = "https://finnhub.io/api/v1/quote"
            params = {"symbol": "CME:ES1!", "token": FINNHUB_KEY}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get("c") and data["c"] > 0:
                current = data["c"]
                prev = data.get("pc", current)
                change = current - prev
                change_pct = (change / prev) * 100 if prev else 0
                if change > 0:
                    direction = f"🟢 +{change:.0f} pts (+{change_pct:.2f}%)"
                elif change < 0:
                    direction = f"🔴 {change:.0f} pts ({change_pct:.2f}%)"
                else:
                    direction = "⚪ Flat"
                result = {"price": round(current, 2), "prev": round(prev, 2), "change": round(change, 2),
                          "change_pct": round(change_pct, 2), "direction": direction, "source": "Finnhub"}
                self._set_cache("futures", result)
                return result
        except Exception as e:
            logger.warning(f"Finnhub futures failed: {e}")

        # 2. Alpha Vantage for SPY as proxy
        try:
            url = "https://www.alphavantage.co/query"
            params = {"function": "GLOBAL_QUOTE", "symbol": "SPY", "apikey": ALPHA_VANTAGE_KEY}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            quote = data.get("Global Quote", {})
            if quote and "05. price" in quote:
                current = float(quote["05. price"]) * 10
                prev = float(quote.get("08. previous close", quote["05. price"])) * 10
                change = current - prev
                change_pct = (change / prev) * 100 if prev else 0
                direction = f"{'🟢 +' if change > 0 else '🔴 '}{change:.0f} pts ({change_pct:+.2f}%)"
                result = {"price": round(current, 2), "prev": round(prev, 2), "change": round(change, 2),
                          "change_pct": round(change_pct, 2), "direction": direction, "source": "AlphaVantage/SPY"}
                self._set_cache("futures", result)
                return result
        except Exception as e:
            logger.warning(f"Alpha Vantage futures proxy failed: {e}")

        # 3. yfinance fallback
        if YFINANCE_AVAILABLE:
            try:
                es = self._yf_ticker("ES=F")
                data = es.history(period="2d")
                if not data.empty:
                    current = float(data['Close'].iloc[-1])
                    prev = float(data['Close'].iloc[-2]) if len(data) >= 2 else current
                    change = current - prev
                    change_pct = (change / prev) * 100 if prev else 0
                    direction = f"{'🟢 +' if change > 0 else '🔴 '}{change:.0f} pts ({change_pct:+.2f}%)"
                    result = {"price": round(current, 2), "prev": round(prev, 2), "change": round(change, 2),
                              "change_pct": round(change_pct, 2), "direction": direction, "source": "yfinance"}
                    self._set_cache("futures", result)
                    return result
            except Exception as e:
                logger.warning(f"yfinance futures failed: {e}")

        return {"price": 0, "prev": 0, "change": 0, "change_pct": 0, "direction": "Data unavailable"}

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
            url = "https://finnhub.io/api/v1/calendar/economic"
            params = {"from": today, "to": today, "token": FINNHUB_KEY}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            for ev in data.get("economicCalendar", []):
                if ev.get("country", "") != "US":
                    continue
                event_name = ev.get("event", "Unknown")
                event_info = {"name": event_name, "time": ev.get("time", "N/A"),
                              "impact": ev.get("impact", "low"), "actual": ev.get("actual", ""),
                              "estimate": ev.get("estimate", ""), "previous": ev.get("prev", "")}
                all_events.append(event_info)
                is_major = any(kw.lower() in event_name.lower() for kw in HIGH_IMPACT)
                if is_major or ev.get("impact", "") == "high":
                    major_events.append(event_info)
        except Exception as e:
            logger.warning(f"Finnhub calendar fetch failed: {e}")
        has_major = len(major_events) > 0
        caution = "⚠️ MAJOR EVENT DAY — consider sitting out or trading after release" if has_major else "✅ No major catalysts — clear to trade"
        result = {"date": today, "major_events": major_events, "all_events": all_events[:10],
                  "has_major": has_major, "caution": caution}
        self._set_cache("calendar", result)
        return result

    # ── Macro Context ─────────────────────────────────────────────────────────
    def get_macro_context(self) -> Dict:
        if self._is_cache_valid("macro"):
            return self._cache["macro"]
        result = {}
        series_map = {"fed_funds": "FEDFUNDS", "treasury_10y": "DGS10", "treasury_2y": "DGS2"}
        for key, series_id in series_map.items():
            try:
                url = "https://api.stlouisfed.org/fred/series/observations"
                params = {"series_id": series_id, "api_key": FRED_KEY, "file_type": "json",
                          "sort_order": "desc", "limit": 5}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                for o in data.get("observations", []):
                    val = o.get("value", ".")
                    if val != ".":
                        result[key] = float(val)
                        break
            except Exception as e:
                logger.warning(f"FRED {series_id} fetch failed: {e}")
                result[key] = None
        if result.get("treasury_10y") and result.get("treasury_2y"):
            result["yield_curve"] = round(result["treasury_10y"] - result["treasury_2y"], 2)
            result["yield_curve_status"] = ("Inverted (recession signal)" if result["yield_curve"] < 0
                                            else ("Flat (caution)" if result["yield_curve"] < 0.5 else "Normal"))
        else:
            result["yield_curve"] = None
            result["yield_curve_status"] = "N/A"
        self._set_cache("macro", result)
        return result

    # ── Options Data ──────────────────────────────────────────────────────────
    def get_options_data(self) -> Dict:
        if self._is_cache_valid("options"):
            return self._cache["options"]
        spx_price_data = self.get_spx_price()
        spx_price = spx_price_data["price"]
        if spx_price == 0:
            return {"error": "Cannot get SPX price"}
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        if YFINANCE_AVAILABLE:
            tickers_to_try = [("^SPX", 1.0), ("SPX", 1.0), ("SPY", 10.0)]
            for ticker_symbol, multiplier in tickers_to_try:
                try:
                    ticker = self._yf_ticker(ticker_symbol)
                    expirations = ticker.options
                    if not expirations:
                        continue
                    target_exp = None
                    for exp in expirations:
                        if exp == today:
                            target_exp = exp
                            break
                        if exp == tomorrow:
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
                    ref_price = spx_price / multiplier
                    result = self._analyze_options_chain(calls, puts, ref_price, spx_price, multiplier, target_exp, ticker_symbol)
                    if result:
                        self._set_cache("options", result)
                        return result
                except Exception as e:
                    logger.warning(f"Options fetch failed for {ticker_symbol}: {e}")
                    continue

        logger.warning("All options sources failed, generating synthetic estimates")
        result = self._generate_synthetic_options(spx_price)
        self._set_cache("options", result)
        return result

    def _analyze_options_chain(self, calls, puts, ref_price, spx_price, multiplier, expiration, ticker_symbol) -> Optional[Dict]:
        try:
            atm_strike = self._find_nearest_strike(calls, ref_price)
            if atm_strike is None:
                return None
            atm_call = calls[calls['strike'] == atm_strike]
            atm_put = puts[puts['strike'] == atm_strike]
            if atm_call.empty or atm_put.empty:
                return None
            call_mid = self._get_mid_price(atm_call.iloc[0])
            put_mid = self._get_mid_price(atm_put.iloc[0])
            straddle_price = (call_mid + put_mid) * multiplier
            expected_move = round(straddle_price * 0.85, 2)
            put_spread = self._find_put_credit_spread(puts, ref_price, multiplier)
            call_spread = self._find_call_credit_spread(calls, ref_price, multiplier)
            iron_condor = None
            if put_spread and call_spread:
                total_credit = round(put_spread["credit"] + call_spread["credit"], 2)
                spread_width = max(put_spread["width"], call_spread["width"])
                max_loss = round(spread_width - total_credit, 2)
                iron_condor = {
                    "total_credit": total_credit, "max_profit": total_credit, "max_loss": max_loss,
                    "lower_breakeven": round(put_spread.get("short_strike_spx", put_spread["short_strike"]) - total_credit, 2),
                    "upper_breakeven": round(call_spread.get("short_strike_spx", call_spread["short_strike"]) + total_credit, 2),
                }
            return {
                "spx_price": spx_price, "expiration": expiration, "ticker_used": ticker_symbol,
                "multiplier": multiplier, "atm_strike": round(atm_strike * multiplier, 1),
                "straddle_price": round(straddle_price, 2), "expected_move": expected_move,
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
        strikes = chain['strike'].values
        idx = np.argmin(np.abs(strikes - price))
        return float(strikes[idx])

    def _get_mid_price(self, row) -> float:
        bid = row.get('bid', 0) or 0
        ask = row.get('ask', 0) or 0
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return row.get('lastPrice', 0) or 0

    def _estimate_delta_from_otm(self, strike, ref_price, is_put=True) -> float:
        if ref_price == 0:
            return 0.5
        otm_pct = ((ref_price - strike) / ref_price) if is_put else ((strike - ref_price) / ref_price)
        if otm_pct <= 0: return 0.50
        elif otm_pct < 0.005: return 0.45
        elif otm_pct < 0.01: return 0.35
        elif otm_pct < 0.015: return 0.25
        elif otm_pct < 0.02: return 0.18
        elif otm_pct < 0.025: return 0.14
        elif otm_pct < 0.03: return 0.10
        elif otm_pct < 0.04: return 0.07
        elif otm_pct < 0.05: return 0.05
        elif otm_pct < 0.07: return 0.03
        else: return 0.01

    def _find_put_credit_spread(self, puts, ref_price, multiplier) -> Optional[Dict]:
        try:
            sorted_puts = puts.sort_values('strike', ascending=False).copy()
            best_short, best_delta = None, None
            for _, row in sorted_puts.iterrows():
                strike = row['strike']
                if strike >= ref_price:
                    continue
                delta = abs(float(row.get('delta', 0) or 0)) if 'delta' in row.index else 0
                if delta == 0:
                    delta = self._estimate_delta_from_otm(strike, ref_price, is_put=True)
                if 0.08 <= delta <= 0.18:
                    best_short, best_delta = row, delta
                    break
            if best_short is None:
                target_strike = ref_price * 0.975
                nearest = self._find_nearest_strike(puts, target_strike)
                if nearest:
                    best_short = puts[puts['strike'] == nearest].iloc[0]
                    best_delta = self._estimate_delta_from_otm(nearest, ref_price, is_put=True)
                else:
                    return None
            short_strike = best_short['strike']
            short_premium = self._get_mid_price(best_short)
            long_strike, long_premium = None, 0
            for width in [5 / multiplier, 10 / multiplier]:
                target_long = short_strike - width
                candidates = puts[puts['strike'] == target_long]
                if not candidates.empty:
                    long_strike, long_premium = target_long, self._get_mid_price(candidates.iloc[0])
                    break
            if long_strike is None:
                below = puts[puts['strike'] < short_strike].sort_values('strike', ascending=False)
                if not below.empty:
                    long_strike, long_premium = below.iloc[0]['strike'], self._get_mid_price(below.iloc[0])
                else:
                    return None
            credit = round((short_premium - long_premium) * multiplier, 2)
            width_spx = round((short_strike - long_strike) * multiplier, 2)
            max_loss = round(width_spx - credit, 2)
            return {
                "short_strike": round(short_strike, 1), "long_strike": round(long_strike, 1),
                "short_strike_spx": round(short_strike * multiplier, 1),
                "long_strike_spx": round(long_strike * multiplier, 1),
                "credit": credit, "credit_dollar": round(credit * 100, 2),
                "max_loss": max_loss, "max_loss_dollar": round(max_loss * 100, 2),
                "width": width_spx, "delta": round(best_delta, 3), "multiplier": multiplier
            }
        except Exception as e:
            logger.error(f"Put spread search error: {e}")
            return None

    def _find_call_credit_spread(self, calls, ref_price, multiplier) -> Optional[Dict]:
        try:
            sorted_calls = calls.sort_values('strike', ascending=True).copy()
            best_short, best_delta = None, None
            for _, row in sorted_calls.iterrows():
                strike = row['strike']
                if strike <= ref_price:
                    continue
                delta = abs(float(row.get('delta', 0) or 0)) if 'delta' in row.index else 0
                if delta == 0:
                    delta = self._estimate_delta_from_otm(strike, ref_price, is_put=False)
                if 0.08 <= delta <= 0.18:
                    best_short, best_delta = row, delta
                    break
            if best_short is None:
                target_strike = ref_price * 1.025
                nearest = self._find_nearest_strike(calls, target_strike)
                if nearest:
                    best_short = calls[calls['strike'] == nearest].iloc[0]
                    best_delta = self._estimate_delta_from_otm(nearest, ref_price, is_put=False)
                else:
                    return None
            short_strike = best_short['strike']
            short_premium = self._get_mid_price(best_short)
            long_strike, long_premium = None, 0
            for width in [5 / multiplier, 10 / multiplier]:
                target_long = short_strike + width
                candidates = calls[calls['strike'] == target_long]
                if not candidates.empty:
                    long_strike, long_premium = target_long, self._get_mid_price(candidates.iloc[0])
                    break
            if long_strike is None:
                above = calls[calls['strike'] > short_strike].sort_values('strike', ascending=True)
                if not above.empty:
                    long_strike, long_premium = above.iloc[0]['strike'], self._get_mid_price(above.iloc[0])
                else:
                    return None
            credit = round((short_premium - long_premium) * multiplier, 2)
            width_spx = round((long_strike - short_strike) * multiplier, 2)
            max_loss = round(width_spx - credit, 2)
            return {
                "short_strike": round(short_strike, 1), "long_strike": round(long_strike, 1),
                "short_strike_spx": round(short_strike * multiplier, 1),
                "long_strike_spx": round(long_strike * multiplier, 1),
                "credit": credit, "credit_dollar": round(credit * 100, 2),
                "max_loss": max_loss, "max_loss_dollar": round(max_loss * 100, 2),
                "width": width_spx, "delta": round(best_delta, 3), "multiplier": multiplier
            }
        except Exception as e:
            logger.error(f"Call spread search error: {e}")
            return None

    def _generate_synthetic_options(self, spx_price: float) -> Dict:
        vix_data = self.get_vix()
        vix = vix_data["level"] if vix_data["level"] > 0 else 18
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
            "spx_price": spx_price, "expiration": today, "ticker_used": "SYNTHETIC", "multiplier": 1,
            "atm_strike": round(spx_price / 5) * 5, "straddle_price": round(expected_move / 0.85, 2),
            "expected_move": expected_move, "expected_range_low": round(spx_price - expected_move, 2),
            "expected_range_high": round(spx_price + expected_move, 2),
            "put_spread": {
                "short_strike": put_short, "long_strike": put_long,
                "short_strike_spx": put_short, "long_strike_spx": put_long,
                "credit": put_credit, "credit_dollar": round(put_credit * 100, 2),
                "max_loss": round(5 - put_credit, 2), "max_loss_dollar": round((5 - put_credit) * 100, 2),
                "width": 5, "delta": 0.12, "multiplier": 1
            },
            "call_spread": {
                "short_strike": call_short, "long_strike": call_long,
                "short_strike_spx": call_short, "long_strike_spx": call_long,
                "credit": call_credit, "credit_dollar": round(call_credit * 100, 2),
                "max_loss": round(5 - call_credit, 2), "max_loss_dollar": round((5 - call_credit) * 100, 2),
                "width": 5, "delta": 0.10, "multiplier": 1
            },
            "iron_condor": {
                "total_credit": round(put_credit + call_credit, 2),
                "max_profit": round(put_credit + call_credit, 2),
                "max_loss": round(5 - (put_credit + call_credit), 2),
                "lower_breakeven": round(put_short - (put_credit + call_credit), 2),
                "upper_breakeven": round(call_short + (put_credit + call_credit), 2)
            },
            "note": "⚠️ Synthetic estimates — live options chain unavailable (market closed or data blocked)."
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
