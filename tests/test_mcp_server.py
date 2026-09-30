import io
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from src import mcp_server as srv

SETTINGS = {"terminal_path": None, "symbol": "XAUUSD.vxc", "magic": 20260923, "mode": "live"}


class FakeApi:
    def __init__(self):
        self.calls, self.fail_once = [], set()

    def __getattr__(self, name):
        def fn(*a, **k):
            self.calls.append(name)
            if name in self.fail_once:
                self.fail_once.discard(name)
                return None
            return {
                "account_info": NS(login=1, server="S", currency="USC", balance=800.0, equity=790.0,
                                   margin=5.0, margin_free=785.0, leverage=1000, trade_mode=2,
                                   trade_allowed=True),
                "positions_get": (NS(ticket=7, symbol="XAUUSD.vxc", type=0, volume=0.1, price_open=4300.0,
                                     sl=4290.0, tp=4320.0, price_current=4301.0, profit=10.0,
                                     time=1790000000, magic=20260923),
                                  NS(ticket=8, symbol="XAUUSD.vxc", type=1, volume=0.01, price_open=4300.0,
                                     sl=0.0, tp=0.0, price_current=4301.0, profit=-1.0,
                                     time=1790000000, magic=0)),
                "terminal_info": NS(path="C:\\MT5", trade_allowed=True),
            }.get(name, True)
        return fn

    def last_error(self):
        return (-1, "fake")

    def shutdown(self):
        self.calls.append("shutdown")


def _server():
    mt5 = srv.MT5(settings_loader=lambda: SETTINGS)
    api = FakeApi()
    mt5._mt5 = api
    mt5.api = lambda: api  # never import the real package in tests
    return srv.Server(srv.Tools(mt5)), api


def _rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_initialize_echoes_protocol_and_lists_tools():
    s, _ = _server()
    init = _rpc(s, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"]
    names = {t["name"] for t in _rpc(s, "tools/list")["result"]["tools"]}
    assert {"account", "positions", "strategy_view", "bot_trades"} <= names


def test_notifications_are_never_answered():
    s, _ = _server()
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_a_protocol_error():
    s, _ = _server()
    assert _rpc(s, "resources/list")["error"]["code"] == -32601


def test_positions_marks_bot_and_manual():
    s, _ = _server()
    out = _rpc(s, "tools/call", {"name": "positions", "arguments": {}})["result"]
    rows = json.loads(out["content"][0]["text"])
    assert [r["source"] for r in rows] == ["bot", "manual/other"]


def test_reattaches_once_after_ipc_failure():
    s, api = _server()
    api.fail_once.add("account_info")
    out = _rpc(s, "tools/call", {"name": "account", "arguments": {}})["result"]
    assert out["isError"] is False and api.calls.count("account_info") == 2


def test_bad_arguments_are_tool_errors_not_crashes():
    s, _ = _server()
    out = _rpc(s, "tools/call", {"name": "bars", "arguments": {"timeframe": "M1"}})["result"]
    assert out["isError"] is True and "timeframe" in out["content"][0]["text"]
    assert _rpc(s, "tools/call", {"name": "order_send", "arguments": {}})["result"]["isError"] is True


def test_serve_reads_and_writes_json_lines():
    s, _ = _server()
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n'
                        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    stdout = io.StringIO()
    s.serve(stdin, stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert replies[1]["error"]["code"] == -32700 and len(replies) == 2


def test_order_tools_hidden_unless_enabled(monkeypatch):
    s, _ = _server()
    monkeypatch.delenv("MT5_MCP_TRADING", raising=False)
    names = {t["name"] for t in _rpc(s, "tools/list")["result"]["tools"]}
    assert "place_order" not in names
    assert _rpc(s, "tools/call", {"name": "place_order", "arguments": {}})["result"]["isError"] is True
    monkeypatch.setenv("MT5_MCP_TRADING", "YES")
    tools = {t["name"]: t for t in _rpc(s, "tools/list")["result"]["tools"]}
    assert {"preview_order", "place_order", "close_position", "modify_position"} <= set(tools)
    assert tools["place_order"]["annotations"]["destructiveHint"] is True
    assert set(tools["place_order"]["inputSchema"]["required"]) == {"side", "sl", "tp", "reason"}


def test_server_source_has_no_trading_calls():
    """Orders only go through src/claude_trading.py: this file must not call order functions."""
    code = Path(srv.__file__).read_text(encoding="utf-8").split('"""', 2)[2]  # skip the docstring
    assert not re.search(r"order_send|order_check|order_calc|positions_close|TRADE_ACTION", code)


def test_database_is_opened_read_only(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "bot.db"
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
    monkeypatch.setattr(srv, "DB_PATH", db)
    with pytest.raises(sqlite3.OperationalError):
        srv.db_query("INSERT INTO t VALUES (1)")


def test_instructions_follow_the_mode(monkeypatch):
    monkeypatch.setenv("MT5_MCP_TRADING", "YES")
    s, _ = _server()
    assert "REAL-MONEY" in _rpc(s, "initialize")["result"]["instructions"]
    s.tools.mt5._settings_loader = lambda: {**SETTINGS, "mode": "demo"}
    assert "DEMO" in _rpc(s, "initialize")["result"]["instructions"]


def _fake_mt5_module(monkeypatch, trade_mode, server):
    import sys
    import types
    calls = {}
    mod = types.ModuleType("MetaTrader5")
    mod.ACCOUNT_TRADE_MODE_DEMO = 0
    mod.initialize = lambda **kw: calls.setdefault("init", kw) is not None
    mod.account_info = lambda: NS(login=5056424854, server=server, trade_mode=trade_mode)
    mod.shutdown = lambda: calls.setdefault("shutdown", True)
    mod.last_error = lambda: (0, "ok")
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    return calls


def test_demo_server_logs_into_the_demo_account(monkeypatch, tmp_path):
    env = tmp_path / ".env.demo"
    env.write_text("MT5_LOGIN=5056424854\nMT5_PASSWORD=pw\nMT5_SERVER=MetaQuotes-Demo\n")
    calls = _fake_mt5_module(monkeypatch, trade_mode=0, server="MetaQuotes-Demo")
    mt5 = srv.MT5(lambda: {**SETTINGS, "mode": "demo", "login_env": str(env), "terminal_path": "C:\\T"})
    mt5.api()
    assert calls["init"]["login"] == 5056424854 and calls["init"]["server"] == "MetaQuotes-Demo"


def test_demo_server_refuses_a_real_account(monkeypatch):
    calls = _fake_mt5_module(monkeypatch, trade_mode=2, server="ValetaxIntl-Live8")
    mt5 = srv.MT5(lambda: {**SETTINGS, "mode": "demo", "login_env": None})
    with pytest.raises(srv.ToolError, match="not a demo"):
        mt5.api()
    assert calls["shutdown"] and mt5._mt5 is None


def test_live_server_never_logs_in(monkeypatch):
    calls = _fake_mt5_module(monkeypatch, trade_mode=2, server="ValetaxIntl-Live8")
    srv.MT5(lambda: {**SETTINGS, "login_env": ".env.cent"}).api()
    assert "password" not in calls["init"]


def test_order_tool_labels_follow_the_mode(monkeypatch):
    monkeypatch.setenv("MT5_MCP_TRADING", "YES")
    s, _ = _server()
    desc = {t["name"]: t["description"] for t in _rpc(s, "tools/list")["result"]["tools"]}
    assert desc["place_order"].startswith("REAL MONEY")
    s.tools.mt5._settings_loader = lambda: {**SETTINGS, "mode": "demo"}
    desc = {t["name"]: t["description"] for t in _rpc(s, "tools/list")["result"]["tools"]}
    assert desc["place_order"].startswith("DEMO") and "REAL MONEY" not in desc["close_position"]
