"""
Market Data Engine for 0DTE SPX Credit Spread Scanner
Fetches live market data from Yahoo Finance, Alpha Vantage, Finnhub, and FRED.
All API keys read from environment variables for cloud deployment.
"""

import os
import yfinance as yf
import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List
import numpy as np

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FRED_KEY = os.environ.get("FRED_API_KEY", "")


class MarketDataEngine:
    def __init__(self):
        self.spx_ticker = yf.Ticker("^SPX")
        self.spy_ticker = yf.Ticker("SPY")
        self.vix_ticker = yf.Ticker("^VIX")
        self._cache = {}
        self._cache_time = {}
        self._cache_ttl = 60

    def _is_cache_valid(self, key: str) -> bool:
        if key in self._cache_time:
            return (datetime.now() - self._cache_time[key]).total_seconds() < self._cache_ttl
        return False

    def _set_cache(self, key: str, value):
        self._cache[key] = value
        self._cache_time[key] = datetime.now()

    def get_spx_price(self) -> Dict:
        if self._is_cache_valid("spx_price"):
            return self._cache["spx_price"]
        try:
            data = self.spx_ticker.history(period="2d")
            if data.empty or len(data) < 1:
                raise ValueError("No SPX data")
            current = float(data['Close'].iloc[-1])
            prev_close = float(data['Close'].iloc[-2]) if len(data) >= 2 else current
            change = current - prev_close
            change_pct = (change / prev_close) * 100
            result = {"price": round(current, 2), "prev_close": round(prev_close, 2),
                      "change": round(change, 2), "change_pct": round(change_pct, 2), "source": "^SPX"}
            self._set_cache("spx_price", result)
            return result
        except Exception as e:
            logger.warning(f"^SPX fetch failed: {e}, trying SPY proxy")
            try:
                spy_data = self.spy_ticker.history(period="2d")
                if spy_data.empty:
                    raise ValueError("No SPY data")
                spy_price = float(spy_data['Close'].iloc[-1])
                spx_est = spy_price * 10
                spy_prev = float(spy_data['Close'].iloc[-2]) if len(spy_data) >= 2 else spy_price
                change = (spy_price - spy_prev) * 10
                change_pct = ((spy_price - spy_prev) / spy_prev) * 100
                result = {"price": round(spx_est, 2), "prev_close": round(spy_prev * 10, 2),
                          "change": round(change, 2), "change_pct": round(change_pct, 2), "source": "SPY*10 (proxy)"}
                self._set_cache("spx_price", result)
                return result
            except Exception as e2:
                logger.error(f"All SPX price sources failed: {e2}")
                return {"price": 0, "prev_close": 0, "change": 0, "change_pct": 0, "source": "unavailable"}

    def get_vix(self) -> Dict:
        if self._is_cache_valid("vix"):
            return self._cache["vix"]
        try:
            data = self.vix_ticker.history(period="5d")
            if data.empty:
                raise ValueError("No VIX data")
            current = float(data['Close'].iloc[-1])
            prev = float(data['Close'].iloc[-2]) if len(data) >= 2 else current
            change = current - prev
            if current < 12:
                assessment, selling_grade = "Very Low — thin premiums, tight spreads, marginal setups", "C"
            elif current < 15:
                assessment, selling_grade = "Low — decent premiums, be selective with strikes", "B-"
            elif current < 20:
                assessment, selling_grade = "Sweet Spot — ideal for selling premium, fat credits", "A"
            elif current < 25:
                assessment, selling_grade = "Elevated — rich premiums but wider moves, tighten stops", "A-"
            elif current < 30:
                assessment, selling_grade = "High — big premiums but dangerous, reduce size", "B"
            else:
                assessment, selling_grade = "Extreme — crisis-level vol, sit on hands or trade tiny", "D"
            result = {"level": round(current, 2), "prev": round(prev, 2), "change": round(change, 2),
                      "assessment": assessment, "selling_grade": selling_grade, "ideal_range": 15 <= current <= 25}
            self._set_cache("vix", result)
            return result
        except Exception as e:
            logger.error(f"VIX fetch failed: {e}")
            return {"level": 0, "prev": 0, "change": 0, "assessment": "Data unavailable",
                    "selling_grade": "N/A", "ideal_range": False}

    def get_futures(self) -> Dict:
        if self._is_cache_valid("futures"):
            return self._cache["futures"]
        try:
            es = yf.Ticker("ES=F")
            data = es.history(period="2d")
            if data.empty:
                raise ValueError("No futures data")
            current = float(data['Close'].iloc[-1])
            prev = float(data['Close'].iloc[-2]) if len(data) >= 2 else current
            change = current - prev
            change_pct = (change / prev) * 100
            if change > 0:
                direction = f"🟢 +{change:.0f} pts (+{change_pct:.2f}%)"
            elif change < 0:
                direction = f"🔴 {change:.0f} pts ({change_pct:.2f}%)"
            else:
                direction = "⚪ Flat"
            result = {"price": round(current, 2), "prev": round(prev, 2), "change": round(change, 2),
                      "change_pct": round(change_pct, 2), "direction": direction}
            self._set_cache("futures", result)
            return result
        except Exception as e:
            logger.error(f"Futures fetch failed: {e}")
            return {"price": 0, "prev": 0, "change": 0, "change_pct": 0, "direction": "Data unavailable"}

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
            result["yield_curve_status"] = "Inverted (recession signal)" if result["yield_curve"] < 0 else ("Flat (caution)" if result["yield_curve"] < 0.5 else "Normal")
        else:
            result["yield_curve"] = None
            result["yield_curve_status"] = "N/A"
        self._set_cache("macro", result)
        return result

    def get_options_data(self) -> Dict:
        if self._is_cache_valid("options"):
            return self._cache["options"]
        spx_price_data = self.get_spx_price()
        spx_price = spx_price_data["price"]
        if spx_price == 0:
            return {"error": "Cannot get SPX price"}
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        tickers_to_try = [("^SPX", 1.0), ("SPX", 1.0), ("SPY", 10.0)]
        for ticker_symbol, multiplier in tickers_to_try:
            try:
                ticker = yf.Ticker(ticker_symbol)
                expirations = ticker.options
                if not expirations:
                    continue
                target_exp = None
                for exp in expirations:
                    if exp == today:
                        target_exp = exp; break
                    if exp == tomorrow:
                        target_exp = exp; break
                if not target_exp:
                    for exp in sorted(expirations):
                        if datetime.strptime(exp, "%Y-%m-%d") >= datetime.now():
                            target_exp = exp; break
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
        return self._generate_synthetic_options(spx_price)

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
                    best_short, best_delta = row, delta; break
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
                    long_strike, long_premium = target_long, self._get_mid_price(candidates.iloc[0]); break
            if long_strike is None:
                below = puts[puts['strike'] < short_strike].sort_values('strike', ascending=False)
                if not below.empty:
                    long_strike, long_premium = below.iloc[0]['strike'], self._get_mid_price(below.iloc[0])
                else:
                    return None
            credit = round((short_premium - long_premium) * multiplier, 2)
            width_spx = round((short_strike - long_strike) * multiplier, 2)
            max_loss = round(width_spx - credit, 2)
            return {"short_strike": round(short_strike, 1), "long_strike": round(long_strike, 1),
                    "short_strike_spx": round(short_strike * multiplier, 1), "long_strike_spx": round(long_strike * multiplier, 1),
                    "credit": credit, "credit_dollar": round(credit * 100, 2), "max_loss": max_loss,
                    "max_loss_dollar": round(max_loss * 100, 2), "width": width_spx,
                    "delta": round(best_delta, 3), "multiplier": multiplier}
        except Exception as e:
            logger.error(f"Put spread search error: {e}"); return None

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
                    best_short, best_delta = row, delta; break
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
                    long_strike, long_premium = target_long, self._get_mid_price(candidates.iloc[0]); break
            if long_strike is None:
                above = calls[calls['strike'] > short_strike].sort_values('strike', ascending=True)
                if not above.empty:
                    long_strike, long_premium = above.iloc[0]['strike'], self._get_mid_price(above.iloc[0])
                else:
                    return None
            credit = round((short_premium - long_premium) * multiplier, 2)
            width_spx = round((long_strike - short_strike) * multiplier, 2)
            max_loss = round(width_spx - credit, 2)
            return {"short_strike": round(short_strike, 1), "long_strike": round(long_strike, 1),
                    "short_strike_spx": round(short_strike * multiplier, 1), "long_strike_spx": round(long_strike * multiplier, 1),
                    "credit": credit, "credit_dollar": round(credit * 100, 2), "max_loss": max_loss,
                    "max_loss_dollar": round(max_loss * 100, 2), "width": width_spx,
                    "delta": round(best_delta, 3), "multiplier": multiplier}
        except Exception as e:
            logger.error(f"Call spread search error: {e}"); return None

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
            "put_spread": {"short_strike": put_short, "long_strike": put_long, "short_strike_spx": put_short,
                           "long_strike_spx": put_long, "credit": put_credit, "credit_dollar": round(put_credit * 100, 2),
                           "max_loss": round(5 - put_credit, 2), "max_loss_dollar": round((5 - put_credit) * 100, 2),
                           "width": 5, "delta": 0.12, "multiplier": 1},
            "call_spread": {"short_strike": call_short, "long_strike": call_long, "short_strike_spx": call_short,
                            "long_strike_spx": call_long, "credit": call_credit, "credit_dollar": round(call_credit * 100, 2),
                            "max_loss": round(5 - call_credit, 2), "max_loss_dollar": round((5 - call_credit) * 100, 2),
                            "width": 5, "delta": 0.10, "multiplier": 1},
            "iron_condor": {"total_credit": round(put_credit + call_credit, 2), "max_profit": round(put_credit + call_credit, 2),
                            "max_loss": round(5 - (put_credit + call_credit), 2),
                            "lower_breakeven": round(put_short - (put_credit + call_credit), 2),
                            "upper_breakeven": round(call_short + (put_credit + call_credit), 2)},
            "note": "⚠️ Synthetic estimates — live options chain unavailable."
        }

    def get_full_snapshot(self) -> Dict:
        return {
            "spx": self.get_spx_price(), "vix": self.get_vix(), "futures": self.get_futures(),
            "calendar": self.get_economic_calendar(), "macro": self.get_macro_context(),
            "options": self.get_options_data(), "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S ET"),
        }
