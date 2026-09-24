"""HTTPS opener that connects over IPv4 only (stdlib).

Some networks advertise IPv6 but route it badly. Python tries addresses one by one,
so a stalled IPv6 attempt can use up the whole timeout before IPv4 is tried.
"""
from __future__ import annotations

import http.client
import socket
import urllib.request


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        last_error: OSError | None = None
        for *_, addr in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            try:
                sock = socket.create_connection(addr, self.timeout)
                break
            except OSError as e:
                last_error = e
        else:
            raise last_error or OSError(f"no IPv4 address for {self.host}")
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req, context=self._context)


OPENER = urllib.request.build_opener(_IPv4HTTPSHandler())