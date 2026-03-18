"""
Conversational AI Engine — Uses gemini-2.5-flash via OpenAI-compatible API.
Reads OPENAI_API_KEY and OPENAI_BASE_URL from environment.
"""

import os
import logging
from openai import OpenAI
from typing import Dict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior options trader at Tastytrade who specializes in 0DTE (zero days to expiration) SPX credit spreads. You are an expert in:

- Selling premium on the S&P 500 index using credit spreads
- Options Greeks (Delta, Gamma, Theta, Vega, Rho)
- Implied volatility, VIX, and volatility surface analysis
- Iron condors, strangles, straddles, and all spread strategies
- Risk management for 0DTE trades (gamma risk, pin risk, assignment)
- Tastytrade methodology: sell premium, manage winners at 50%, keep position size small
- Market microstructure, SPX vs SPY options, cash-settled vs equity options
- Economic calendar impact on options pricing

Your communication style:
- Professional but approachable, like a seasoned trader mentoring a colleague
- Use specific numbers and examples when explaining concepts
- Reference real market mechanics and practical trading scenarios
- Be honest about risks — 0DTE trading is high-risk and not suitable for everyone
- Always remind users this is educational, not financial advice

When market data is provided in the context, incorporate it naturally into your responses.
Keep responses concise but thorough — aim for 2-4 paragraphs unless a longer explanation is warranted."""


class AIEngine:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.manus.im/api/llm-proxy/v1"),
        )
        self.model = "gemini-2.5-flash"
        self.conversation_history = {}

    async def chat(self, user_id: int, message: str, market_context: str = "") -> str:
        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            if market_context:
                messages.append({"role": "system", "content": f"Current market data:\n{market_context}"})
            history = self.conversation_history.get(user_id, [])
            messages.extend(history[-12:])
            messages.append({"role": "user", "content": message})
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=1500, temperature=0.7)
            reply = response.choices[0].message.content
            if user_id not in self.conversation_history:
                self.conversation_history[user_id] = []
            self.conversation_history[user_id].append({"role": "user", "content": message})
            self.conversation_history[user_id].append({"role": "assistant", "content": reply})
            if len(self.conversation_history[user_id]) > 20:
                self.conversation_history[user_id] = self.conversation_history[user_id][-12:]
            return reply
        except Exception as e:
            logger.error(f"AI chat error: {e}")
            return f"I'm having trouble connecting right now. Error: {str(e)[:100]}. Try /scan for the latest trade ticket."

    def get_market_context_string(self, snapshot: Dict) -> str:
        parts = []
        spx = snapshot.get("spx", {})
        if spx.get("price"):
            parts.append(f"SPX: {spx['price']:,.2f} ({'+' if spx['change'] >= 0 else ''}{spx['change']:,.2f})")
        vix = snapshot.get("vix", {})
        if vix.get("level"):
            parts.append(f"VIX: {vix['level']:.2f} ({vix.get('assessment', '')})")
        futures = snapshot.get("futures", {})
        if futures.get("direction"):
            parts.append(f"ES Futures: {futures['direction']}")
        options = snapshot.get("options", {})
        if options and "error" not in options:
            parts.append(f"Expected Move: ±{options.get('expected_move', 0):.0f} pts")
            put = options.get("put_spread")
            if put:
                parts.append(f"Put Spread: Sell {put.get('short_strike_spx', put['short_strike'])}P/Buy {put.get('long_strike_spx', put['long_strike'])}P for ${put['credit']:.2f}")
            call = options.get("call_spread")
            if call:
                parts.append(f"Call Spread: Sell {call.get('short_strike_spx', call['short_strike'])}C/Buy {call.get('long_strike_spx', call['long_strike'])}C for ${call['credit']:.2f}")
        return " | ".join(parts)
