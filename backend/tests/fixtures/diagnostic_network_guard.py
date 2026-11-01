"""Process-local diagnostic guard; never changes application/provider settings."""
from contextlib import contextmanager
from unittest.mock import patch
import socket
import httpx


class DiagnosticNetworkBlocked(RuntimeError):
    pass


@contextmanager
def block_diagnostic_network():
    """SQL via libpq remains available; Python network/HTTP fails before sending.

    Deliberately raises outside httpx.HTTPError so semantic lookup cannot turn a
    blocked paid embedding into an apparently successful lexical-only probe.
    """
    attempts=[]
    def deny(*args,**kwargs):
        attempts.append('blocked')
        raise DiagnosticNetworkBlocked('Diagnostic outbound network blocked before send')
    async def deny_async(*args,**kwargs):return deny()
    with patch.object(httpx.Client,'send',deny),patch.object(httpx.AsyncClient,'send',deny_async),patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny):
        yield attempts
