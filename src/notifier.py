"""Telegram alerts + remote commands (stdlib urllib only).

Safety:
- Commands are accepted ONLY from TELEGRAM_CHAT_ID; everything else is ignored and logged.
- Updates received while the bot was offline are skipped at startup, so an old
  /closeall can never fire unexpectedly after a restart.
- A failed notification never crashes the bot.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

from dotenv import load_dotenv

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Commands:\n"
    "/status - equity, positions, last decision\n"
    "/pause - stop new entries (open positions keep SL/TP)\n"
    "/resume - allow new entries (cannot override kill switch)\n"
    "/closeall - close all positions and pause\n"
    "/help - this message"
)


def parse_command(text: str | None) -> str | None:
    """'/Pause@my_bot now' -> '/pause'. Non-commands -> None."""
    if not text or not text.startswith("/"):
        return None
    return text.split()[0].split("@")[0].lower()


class NullNotifier:
    """Used when Telegram is not configured: messages go to the log only."""

    def send(self, text: str) -> None:
        log.info("[notify] %s", text)

    def send_throttled(self, key: str, text: str, min_interval: float = 600) -> None:
        self.send(text)

    def skip_pending(self) -> None:
        pass

    def poll_commands(self) -> list[str]:
        return []


class TelegramNotifier:
    def __init__(self, token: str, chat_id: int | str, timeout: float = 10) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self.chat_id = int(chat_id)
        self.timeout = timeout
        self._offset: int | None = None
        self._last_sent: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "TelegramNotifier | NullNotifier":
        load_dotenv()
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            log.warning("Telegram not configured; notifications go to the log only")
            return NullNotifier()
        return cls(token, chat)

    def _call(self, method: str, **params):
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(f"{self._base}/{method}", data=data, timeout=self.timeout) as resp:
            payload = json.load(resp)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {payload.get('description')}")
        return payload["result"]

    def send(self, text: str) -> None:
        try:
            self._call("sendMessage", chat_id=self.chat_id, text=text[:4000])
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

    def send_throttled(self, key: str, text: str, min_interval: float = 600) -> None:
        now = time.monotonic()
        if now - self._last_sent.get(key, float("-inf")) >= min_interval:
            self._last_sent[key] = now
            self.send(text)

    def skip_pending(self) -> None:
        """Acknowledge everything sent while the bot was offline, without executing it."""
        try:
            updates = self._call("getUpdates", timeout=0)
            if updates:
                self._offset = updates[-1]["update_id"] + 1
            log.info("Skipped %d pending Telegram update(s)", len(updates))
        except Exception as e:
            log.warning("Telegram skip_pending failed: %s", e)

    def extract_commands(self, updates: list[dict]) -> list[str]:
        """Advance the offset past every update; return commands from the authorised chat only."""
        commands = []
        for u in updates:
            self._offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            cmd = parse_command(msg.get("text"))
            if cmd is None:
                continue
            if chat_id != self.chat_id:
                log.warning("Ignored command %s from unauthorised chat %s", cmd, chat_id)
                continue
            commands.append(cmd)
        return commands

    def poll_commands(self) -> list[str]:
        params = {"timeout": 0}
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            return self.extract_commands(self._call("getUpdates", **params))
        except Exception as e:
            log.warning("Telegram poll failed: %s", e)
            return []