"""One real veto call to verify the API key, web search and JSON parsing (costs a few cents)."""
import json
import logging
import sys
from datetime import datetime, timezone

from src.veto import NewsVeto, parse_response

logging.basicConfig(level=logging.INFO)


def main() -> int:
    veto = NewsVeto.from_env()
    if veto is None:
        print("ANTHROPIC_API_KEY missing in .env")
        return 1

    body = veto.build_request("long", 1.14100, 1.13800, 1.14500, datetime.now(timezone.utc))
    try:
        payload = veto._post(body)
    except Exception as e:  # show the API's error message (never contains the key)
        detail = e.read().decode(errors="replace") if hasattr(e, "read") else ""
        print(f"API call failed: {type(e).__name__} {getattr(e, 'code', '')}\n{detail}")
        return 1

    result = parse_response(payload)
    searches = sum(1 for b in payload.get("content", []) if b.get("type") == "server_tool_use")
    print("\n=== VETO LIVE TEST ===")
    print(f"model: {payload.get('model')} | stop_reason: {payload.get('stop_reason')} | web searches: {searches}")
    print(f"usage: {json.dumps(payload.get('usage', {}))}")
    print(f"result: {result.action.upper()} | {result.reason}")
    print(f"events: {result.events}")
    return 0


if __name__ == "__main__":
    sys.exit(main())