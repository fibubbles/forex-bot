from datetime import datetime, timezone

from src.veto import NewsVeto, parse_response

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def _payload(final_text: str, stop: str = "end_turn") -> dict:
    return {"stop_reason": stop, "content": [
        {"type": "text", "text": "Let me check the economic calendar."},
        {"type": "server_tool_use", "id": "t1", "name": "web_search", "input": {"query": "q"}},
        {"type": "web_search_tool_result", "tool_use_id": "t1", "content": []},
        {"type": "text", "text": final_text},
    ]}


def test_allow():
    r = parse_response(_payload('{"action": "allow", "reason": "No major events", "events": []}'))
    assert r.allowed


def test_block_with_events():
    r = parse_response(_payload('{"action": "block", "reason": "NFP soon", "events": ["NFP 12:30 UTC"]}'))
    assert not r.allowed
    assert r.events == ["NFP 12:30 UTC"]


def test_json_inside_code_fence():
    r = parse_response(_payload('```json\n{"action": "allow", "reason": "quiet", "events": []}\n```'))
    assert r.allowed


def test_malformed_json_blocks():
    r = parse_response(_payload('{"action": "allow", "reason": '))
    assert not r.allowed and "fail-safe" in r.reason


def test_unknown_action_blocks():
    r = parse_response(_payload('{"action": "maybe", "reason": "unsure", "events": []}'))
    assert not r.allowed


def test_incomplete_turn_blocks():
    r = parse_response(_payload('{"action": "allow", "reason": "ok", "events": []}', stop="pause_turn"))
    assert not r.allowed


def test_api_error_blocks():
    def failing_post(body):
        raise TimeoutError("too slow")

    r = NewsVeto("dummy", post=failing_post).check("long", 1.1, 1.09, 1.12, NOW)
    assert not r.allowed and "fail-safe" in r.reason


def test_request_contains_trade_and_web_search():
    body = NewsVeto("dummy").build_request("short", 1.1, 1.11, 1.09, NOW)
    assert "SHORT EURUSD" in body["messages"][0]["content"]
    assert body["tools"][0]["name"] == "web_search"