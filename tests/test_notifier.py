from src.notifier import TelegramNotifier, parse_command
from src.state import ControlFlags


def _update(uid: int, chat_id: int, text: str) -> dict:
    return {"update_id": uid, "message": {"chat": {"id": chat_id}, "text": text}}


def test_parse_command():
    assert parse_command("/status") == "/status"
    assert parse_command("/Pause@hafiz_fx_alert_bot now") == "/pause"
    assert parse_command("hai") is None
    assert parse_command(None) is None


def test_only_authorised_chat_commands_are_accepted():
    n = TelegramNotifier("dummy-token", 111)
    cmds = n.extract_commands([
        _update(1, 111, "/status"),
        _update(2, 999, "/closeall"),   # stranger: ignored
        _update(3, 111, "hello"),       # not a command
        _update(4, 111, "/pause"),
    ])
    assert cmds == ["/status", "/pause"]
    assert n._offset == 5  # every update acknowledged, including ignored ones


def test_control_flags_persist_across_restart(tmp_path):
    path = tmp_path / "control.json"
    ControlFlags(path).set_paused(True)
    assert ControlFlags(path).paused
    ControlFlags(path).set_paused(False)
    assert not ControlFlags(path).paused


def test_failed_messages_are_queued_and_resent():
    n = TelegramNotifier("dummy-token", 111)
    sent, down = [], {"yes": True}

    def fake_call(method, **params):
        if down["yes"]:
            raise TimeoutError("telegram unreachable")
        if method == "sendMessage":
            sent.append(params["text"])
        return []

    n._call = fake_call
    n.send("📈 DEMO BUY")                 # fails -> queued, backoff starts
    assert sent == [] and n._outbox == ["📈 DEMO BUY"]
    assert n.poll_commands() == []       # still in backoff: no network call

    down["yes"] = False
    n._backoff_until = 0.0               # pretend 60 s have passed
    n.poll_commands()                    # successful poll flushes the queue
    assert sent == ["📈 DEMO BUY"] and n._outbox == []