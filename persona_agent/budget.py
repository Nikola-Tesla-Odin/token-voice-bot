"""Monthly spend guard. Every Claude response's usage is priced and added to a per-month ledger;
once the month's total reaches PERSONA_AGENT_BUDGET_USD (default $5), new calls are refused.
Web research is paced across the month so plain posts can keep going.

Prices are list prices per million tokens and can drift; treat totals as close estimates. For a hard
guarantee, also set a monthly spend limit in the Anthropic Console (Settings → Limits).
"""

from __future__ import annotations

import json
import os
from datetime import date

from .persona import HOME

BUDGET_USD = float(os.environ.get("PERSONA_AGENT_BUDGET_USD", "5"))
# Keep a little headroom so the last call of the month can't push the total over the cap.
HEADROOM_USD = 0.02
# Set aside for a month of plain posts (~360 at ~$0.0005 each on Haiku) before any web research.
PLAIN_POST_RESERVE_USD = float(os.environ.get("PERSONA_AGENT_PLAIN_RESERVE_USD", "0.30"))

# (input, output) USD per million tokens.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
UNKNOWN_MODEL_PRICE = (10.00, 50.00)  # price unknown models pessimistically
WEB_SEARCH_USD = 10.00 / 1000


def _ledger_path():
    return HOME / "spend" / f"{date.today():%Y-%m}.json"


def spent() -> float:
    path = _ledger_path()
    return json.loads(path.read_text())["usd"] if path.exists() else 0.0


def cost(response) -> float:
    u = response.usage
    price_in, price_out = PRICES.get(response.model, UNKNOWN_MODEL_PRICE)
    tokens_in = (
        u.input_tokens
        + 1.25 * (u.cache_creation_input_tokens or 0)
        + 0.1 * (u.cache_read_input_tokens or 0)
    )
    searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    return (tokens_in * price_in + u.output_tokens * price_out) / 1e6 + searches * WEB_SEARCH_USD


def record(response) -> float:
    usd = cost(response)
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {"usd": 0.0, "calls": 0}
    data["usd"] += usd
    data["calls"] += 1
    path.write_text(json.dumps(data))
    return usd


def can_afford_research(cost_usd: float = 0.02) -> bool:
    """Pace the expensive part (web research) evenly across the month, keeping a reserve so the cheap
    plain posts never run out. Falling behind just means fewer news posts, never missed posts."""
    today = date.today()
    next_month = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    days_in_month = (next_month - date(today.year, today.month, 1)).days
    research_budget = max(BUDGET_USD - PLAIN_POST_RESERVE_USD, 0)
    allowed_by_today = research_budget * today.day / days_in_month
    return spent() + cost_usd <= allowed_by_today


def ensure_room() -> None:
    total = spent()
    if total + HEADROOM_USD >= BUDGET_USD:
        raise RuntimeError(
            f"Monthly budget reached: ${total:.2f} of ${BUDGET_USD:.2f} spent in {date.today():%B}. "
            "Pausing until next month (raise PERSONA_AGENT_BUDGET_USD to continue)."
        )
