"""
Trade Ticket Formatter — Tastytrade-style 0DTE SPX trade tickets.
"""

from datetime import datetime
from typing import Dict, Optional
import json
import os
import logging

logger = logging.getLogger(__name__)

LOG_DIR = "/home/ubuntu/spx_bot/logs"


def format_trade_ticket(snapshot: Dict) -> str:
    """Format a complete trade ticket from market snapshot."""

    spx = snapshot.get("spx", {})
    vix = snapshot.get("vix", {})
    futures = snapshot.get("futures", {})
    calendar = snapshot.get("calendar", {})
    options = snapshot.get("options", {})
    timestamp = snapshot.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S ET"))

    today = datetime.now().strftime("%B %d, %Y")
    today_short = datetime.now().strftime("%m/%d/%Y")

    # ─── GO / NO-GO Decision ────────────────────────────────────────────
    go_decision, go_reasons = _make_go_decision(vix, calendar, options, spx)

    # ─── Build ticket ────────────────────────────────────────────────────
    lines = []
    lines.append(f"📊 0DTE SPX TRADE TICKET — {today}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    # GO/NO-GO
    if go_decision:
        lines.append(f"✅ GO — {go_reasons}")
    else:
        lines.append(f"⛔ NO-GO — {go_reasons}")

    lines.append("")

    # Market Conditions
    spx_price = spx.get("price", 0)
    spx_change = spx.get("change", 0)
    spx_pct = spx.get("change_pct", 0)
    vix_level = vix.get("level", 0)
    vix_grade = vix.get("selling_grade", "N/A")
    futures_dir = futures.get("direction", "N/A")

    sign = "+" if spx_change >= 0 else ""
    lines.append(f"📈 SPX: {spx_price:,.2f} ({sign}{spx_change:,.2f} / {sign}{spx_pct:.2f}%)")
    lines.append(f"🌡️ VIX: {vix_level:.2f} — Premium Grade: {vix_grade}")
    lines.append(f"🔮 Futures: {futures_dir}")
    lines.append(f"📋 {vix.get('assessment', 'N/A')}")

    # Calendar events
    if calendar.get("has_major"):
        lines.append("")
        lines.append("⚠️ MAJOR EVENTS TODAY:")
        for ev in calendar.get("major_events", [])[:3]:
            lines.append(f"  • {ev['name']} ({ev.get('time', 'TBD')})")
    else:
        lines.append("📅 No major economic events — clear runway")

    lines.append("")

    # Expected Move
    if options and "error" not in options:
        em = options.get("expected_move", 0)
        em_low = options.get("expected_range_low", 0)
        em_high = options.get("expected_range_high", 0)
        straddle = options.get("straddle_price", 0)

        lines.append("━━━ EXPECTED MOVE ━━━")
        lines.append(f"ATM Straddle: ${straddle:.2f}")
        lines.append(f"Expected Move: SPX {spx_price:,.0f} ± {em:.0f} pts")
        lines.append(f"Today's Range: {em_low:,.0f} — {em_high:,.0f}")

        if options.get("ticker_used") == "SYNTHETIC":
            lines.append("⚠️ Synthetic estimate (live chain unavailable)")
        elif options.get("ticker_used") == "SPY":
            lines.append("📌 Based on SPY options (scaled to SPX)")

        lines.append(f"📆 Expiration: {options.get('expiration', today_short)}")
        lines.append("")

        # ─── PUT CREDIT SPREAD ───────────────────────────────────────────
        put = options.get("put_spread")
        if put:
            ps_short = put.get("short_strike_spx", put.get("short_strike", 0))
            ps_long = put.get("long_strike_spx", put.get("long_strike", 0))
            lines.append("━━━ PUT CREDIT SPREAD ━━━")
            lines.append(f"  📍 Sell {ps_short:.0f}P / Buy {ps_long:.0f}P")
            lines.append(f"  💰 Credit: ${put['credit']:.2f} (${put['credit_dollar']:.0f}/contract)")
            lines.append(f"  🔻 Max Loss: ${put['max_loss']:.2f} (${put['max_loss_dollar']:.0f}/contract)")
            lines.append(f"  📐 Width: {put['width']:.0f} pts | Delta: {put['delta']:.2f}")

            # Quality check
            rr_ratio = put['credit'] / put['max_loss'] if put['max_loss'] > 0 else 0
            if put['credit'] >= 0.50:
                lines.append(f"  ✅ Credit meets minimum ($0.50+) | R:R {rr_ratio:.2f}")
            else:
                lines.append(f"  ⚠️ Thin credit (${put['credit']:.2f}) — consider wider spread")
            lines.append("")

        # ─── CALL CREDIT SPREAD ──────────────────────────────────────────
        call = options.get("call_spread")
        if call:
            cs_short = call.get("short_strike_spx", call.get("short_strike", 0))
            cs_long = call.get("long_strike_spx", call.get("long_strike", 0))
            lines.append("━━━ CALL CREDIT SPREAD ━━━")
            lines.append(f"  📍 Sell {cs_short:.0f}C / Buy {cs_long:.0f}C")
            lines.append(f"  💰 Credit: ${call['credit']:.2f} (${call['credit_dollar']:.0f}/contract)")
            lines.append(f"  🔻 Max Loss: ${call['max_loss']:.2f} (${call['max_loss_dollar']:.0f}/contract)")
            lines.append(f"  📐 Width: {call['width']:.0f} pts | Delta: {call['delta']:.2f}")

            rr_ratio = call['credit'] / call['max_loss'] if call['max_loss'] > 0 else 0
            if call['credit'] >= 0.50:
                lines.append(f"  ✅ Credit meets minimum ($0.50+) | R:R {rr_ratio:.2f}")
            else:
                lines.append(f"  ⚠️ Thin credit (${call['credit']:.2f}) — consider wider spread")
            lines.append("")

        # ─── IRON CONDOR ─────────────────────────────────────────────────
        ic = options.get("iron_condor")
        if ic and vix_level >= 16 and not calendar.get("has_major"):
            lines.append("━━━ 🦅 IRON CONDOR (RECOMMENDED) ━━━")
            lines.append(f"  Combined: Sell {ps_short:.0f}P/{ps_long:.0f}P + Sell {cs_short:.0f}C/{cs_long:.0f}C")
            lines.append(f"  💰 Total Credit: ${ic['total_credit']:.2f}")
            lines.append(f"  🎯 Max Profit: ${ic['max_profit']:.2f} (${ic['max_profit'] * 100:.0f}/contract)")
            lines.append(f"  🔻 Max Loss: ${ic['max_loss']:.2f} (${ic['max_loss'] * 100:.0f}/contract)")
            lines.append(f"  📊 Breakevens: {ic['lower_breakeven']:,.0f} / {ic['upper_breakeven']:,.0f}")
            lines.append("")
        elif ic:
            lines.append("━━━ 🦅 IRON CONDOR ━━━")
            if vix_level < 16:
                lines.append("  ⚠️ VIX < 16 — single-side spread preferred over IC")
            if calendar.get("has_major"):
                lines.append("  ⚠️ Major catalyst today — avoid IC, use directional spread")
            lines.append(f"  (Combined credit would be: ${ic['total_credit']:.2f})")
            lines.append("")

    else:
        lines.append("⚠️ Options chain data unavailable — cannot generate spreads")
        lines.append("")

    # ─── Trade Management ────────────────────────────────────────────────
    lines.append("━━━ TRADE MANAGEMENT ━━━")
    lines.append("⏰ ENTRY: 9:45–10:30 AM ET (let the open settle)")
    lines.append("🛑 STOP: Close if spread hits 2× credit received")
    lines.append("🎯 TARGET: Close at 50% profit or let expire worthless")
    lines.append(f"📅 EXPIRY: Today — {today_short}")
    lines.append("")

    # ─── Fidelity Entry Guide ────────────────────────────────────────────
    lines.append("━━━ 🔵 HOW TO ENTER IN FIDELITY ━━━")
    lines.append("1. Log into Fidelity → go to **Trade** → **Options**")
    lines.append("2. Search for **SPX** (S&P 500 Index)")
    lines.append("3. Select expiration: **Today's Date**")
    lines.append("4. Use **Multi-Leg** order: **Sell** the short strike & **Buy** the long strike")
    lines.append("5. Set Order Type to **Limit** and enter the **Credit Amount**")
    lines.append("6. Review Buying Power requirement and **Submit**")
    lines.append("")

    # ─── Risk Reminders ──────────────────────────────────────────────────
    lines.append("━━━ RISK REMINDERS ━━━")
    lines.append("• Position size: 1-2% of account per trade")
    lines.append("• Never hold through a breach of your short strike")
    lines.append("• 0DTE = max gamma risk — respect your stops")
    lines.append("• This is NOT financial advice — trade at your own risk")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🕐 Generated: {timestamp}")

    if options.get("note"):
        lines.append(f"\n{options['note']}")

    ticket = "\n".join(lines)

    # Save to log
    _save_ticket_log(ticket, snapshot)

    return ticket


def _make_go_decision(vix: Dict, calendar: Dict, options: Dict, spx: Dict) -> tuple:
    """Determine GO/NO-GO for the trading day."""
    reasons = []
    score = 0

    # VIX check
    vix_level = vix.get("level", 0)
    if vix_level == 0:
        reasons.append("VIX data unavailable")
    elif 15 <= vix_level <= 25:
        score += 2
        reasons.append(f"VIX {vix_level:.1f} in sweet spot")
    elif 12 <= vix_level < 15:
        score += 1
        reasons.append(f"VIX {vix_level:.1f} acceptable")
    elif vix_level > 30:
        score -= 2
        reasons.append(f"VIX {vix_level:.1f} extreme caution")
    elif vix_level > 25:
        score += 0
        reasons.append(f"VIX {vix_level:.1f} elevated")
    else:
        reasons.append(f"VIX {vix_level:.1f} very low premiums")

    # Calendar check
    if calendar.get("has_major"):
        score -= 2
        reasons.append("major event day")
    else:
        score += 1
        reasons.append("clear calendar")

    # Options quality check
    if options and "error" not in options:
        put = options.get("put_spread")
        call = options.get("call_spread")
        if put and put.get("credit", 0) >= 0.50:
            score += 1
        if call and call.get("credit", 0) >= 0.50:
            score += 1
    else:
        score -= 1
        reasons.append("no options data")

    go = score >= 2
    reason_str = " | ".join(reasons)
    return go, reason_str


def _save_ticket_log(ticket: str, snapshot: Dict):
    """Save trade ticket to log file for performance tracking."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(LOG_DIR, f"tickets_{today}.log")

        with open(log_file, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"{'='*60}\n")
            f.write(ticket)
            f.write(f"\n{'='*60}\n\n")

        # Also save structured data as JSON
        json_file = os.path.join(LOG_DIR, f"tickets_{today}.json")
        records = []
        if os.path.exists(json_file):
            try:
                with open(json_file, "r") as f:
                    records = json.load(f)
            except:
                records = []

        records.append({
            "timestamp": datetime.now().isoformat(),
            "spx_price": snapshot.get("spx", {}).get("price"),
            "vix": snapshot.get("vix", {}).get("level"),
            "put_spread": snapshot.get("options", {}).get("put_spread"),
            "call_spread": snapshot.get("options", {}).get("call_spread"),
            "iron_condor": snapshot.get("options", {}).get("iron_condor"),
        })

        with open(json_file, "w") as f:
            json.dump(records, f, indent=2, default=str)

    except Exception as e:
        logger.error(f"Failed to save ticket log: {e}")


def format_vix_report(vix: Dict) -> str:
    """Format a VIX-focused report."""
    lines = [
        "🌡️ VIX PREMIUM SELLING CONDITIONS",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"VIX Level: {vix['level']:.2f}",
        f"Change: {'+' if vix['change'] >= 0 else ''}{vix['change']:.2f}",
        f"Premium Grade: {vix['selling_grade']}",
        f"Assessment: {vix['assessment']}",
        "",
        "VIX GUIDE FOR 0DTE:",
        "  < 12: Very thin premiums, skip or go wide",
        "  12-15: Decent, be selective",
        "  15-20: Sweet spot — fat credits, manageable moves",
        "  20-25: Rich premiums, wider expected moves",
        "  25-30: High vol — reduce size, widen strikes",
        "  30+: Crisis vol — sit on hands or trade tiny",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_spx_report(spx: Dict, options: Dict) -> str:
    """Format SPX price and expected move report."""
    sign = "+" if spx['change'] >= 0 else ""
    lines = [
        "📈 SPX PRICE & EXPECTED MOVE",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"SPX: {spx['price']:,.2f} ({sign}{spx['change']:,.2f} / {sign}{spx['change_pct']:.2f}%)",
        f"Source: {spx.get('source', 'N/A')}",
    ]

    if options and "error" not in options:
        em = options.get("expected_move", 0)
        lines.extend([
            "",
            f"Expected Move: ±{em:.0f} pts",
            f"Today's Range: {options.get('expected_range_low', 0):,.0f} — {options.get('expected_range_high', 0):,.0f}",
            f"ATM Straddle: ${options.get('straddle_price', 0):.2f}",
            f"Expiration: {options.get('expiration', 'N/A')}",
        ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_calendar_report(calendar: Dict) -> str:
    """Format economic calendar report."""
    lines = [
        "📅 ECONOMIC CALENDAR",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Date: {calendar.get('date', 'N/A')}",
        f"Status: {calendar.get('caution', 'N/A')}",
        "",
    ]

    if calendar.get("major_events"):
        lines.append("🔴 HIGH-IMPACT EVENTS:")
        for ev in calendar["major_events"]:
            est = f" (Est: {ev['estimate']})" if ev.get('estimate') else ""
            lines.append(f"  • {ev['name']} — {ev.get('time', 'TBD')}{est}")
        lines.append("")

    if calendar.get("all_events"):
        lines.append("📋 ALL US EVENTS:")
        for ev in calendar["all_events"][:8]:
            lines.append(f"  • {ev['name']} — {ev.get('time', 'TBD')}")
    else:
        lines.append("No US economic events scheduled today.")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)
