"""Compare default (may try IPv6 first) vs IPv4-only HTTPS timing to Telegram."""
import time
import urllib.request

from src.net import OPENER

URL = "https://api.telegram.org"


def timed(open_fn) -> str:
    start = time.perf_counter()
    try:
        open_fn(URL, timeout=20).close()
        return f"{time.perf_counter() - start:.2f}s"
    except Exception as e:
        return f"FAIL {type(e).__name__} after {time.perf_counter() - start:.1f}s"


for i in range(1, 6):
    print(f"{i}: default {timed(urllib.request.urlopen):>28} | ipv4 {timed(OPENER.open):>28}")