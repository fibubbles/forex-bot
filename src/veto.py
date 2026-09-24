"""News-risk veto using the Claude API (Haiku + server-side web search). Stdlib HTTP only.

The veto can only ALLOW or BLOCK a trade that the strategy and risk manager already
approved. It never chooses direction, size, SL or TP.

Fail-safe: API error, timeout, incomplete turn or invalid JSON -> BLOCK.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime
from typing import Callable, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You are a risk filter for an automated EURUSD swing-trading bot.
You do NOT choose trades. You only decide whether a proposed trade must be BLOCKED because of news risk.

Use web search to check:
1. High-impact USD or EUR events within the next 8 hours (NFP, CPI, FOMC or ECB rate decisions, Fed/ECB chair speeches).
2. Breaking news in the last 6 hours that could cause abnormal EURUSD volatility.

Rules:
- Block if a high-impact USD or EUR event falls within the next 8 hours.
- Only these count as high-impact: NFP, US CPI, FOMC and ECB rate decisions, Fed and ECB chair speeches,
  US GDP, Eurozone CPI flash. Other central banks (SNB, Riksbank, Norges Bank, Banxico, etc.) and
  medium-impact data (e.g. weekly jobless claims) are NOT reasons to block.
- If an event's time is unknown, search for its scheduled time. Block only if you cannot rule out
  that a high-impact USD or EUR event falls inside the window.
- Block if there is a major surprise or shock event affecting USD or EUR.
- If search results are unclear or unavailable, BLOCK.

Your final message must be ONLY this JSON object, with no other text:
{"action": "allow" or "block", "reason": "<max 20 words>", "events": ["<event, time UTC>"]}"""


class VetoResult(BaseModel):
    action: Literal["allow", "block"]
    reason: str = Field(max_length=300)
    events: list[str] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def _block(reason: str) -> VetoResult:
    return VetoResult(action="block", reason=reason, events=[])


def parse_response(payload: dict) -> VetoResult:
    """Extract and validate the final JSON answer. Anything unexpected -> block."""
    if payload.get("stop_reason") != "end_turn":
        return _block(f"fail-safe: stop_reason={payload.get('stop_reason')}")

    content = payload.get("content", [])
    last_tool = max((i for i, b in enumerate(content) if b.get("type") != "text"), default=-1)
    text = "".join(b.get("text", "") for b in content[last_tool + 1:] if b.get("type") == "text")

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return _block("fail-safe: no JSON object in final answer")
    try:
        return VetoResult.model_validate(json.loads(text[start:end + 1]))
    except (json.JSONDecodeError, ValidationError) as e:
        return _block(f"fail-safe: invalid answer ({type(e).__name__})")


class NewsVeto:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 post: Callable[[dict], dict] | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self._post = post or self._http_post

    @classmethod
    def from_env(cls) -> "NewsVeto | None":
        load_dotenv()
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            log.warning("ANTHROPIC_API_KEY not set: news veto DISABLED")
            return None
        return cls(key, os.getenv("VETO_MODEL", DEFAULT_MODEL))

    def _http_post(self, body: dict) -> dict:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.load(resp)

    def build_request(self, side: str, entry: float, sl: float, tp: float, now_utc: datetime) -> dict:
        trade = (f"Proposed trade: {side.upper()} EURUSD, entry ~{entry}, SL {sl}, TP {tp}, "
                 f"time {now_utc:%Y-%m-%d %H:%M} UTC.")
        return {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": trade}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        }

    def check(self, side: str, entry: float, sl: float, tp: float, now_utc: datetime) -> VetoResult:
        try:
            payload = self._post(self.build_request(side, entry, sl, tp, now_utc))
        except Exception as e:  # network, HTTP error, timeout: never trade blind
            code = getattr(e, "code", "")
            log.warning("Veto API call failed: %s %s", type(e).__name__, code)
            return _block(f"fail-safe: API error ({type(e).__name__} {code})".strip())
        result = parse_response(payload)
        log.info("Veto: %s | %s", result.action, result.reason)
        return result