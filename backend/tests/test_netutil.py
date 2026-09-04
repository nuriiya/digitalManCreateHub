# -*- coding: utf-8 -*-
"""Loopback proxy-bypass tests.

Regression guard for benchmark job #30: with a proxy configured (env vars for
httpx / Windows registry for urllib), calls to Ollama on 127.0.0.1:11434 were
routed through the proxy and died with `InternalServerError 502` on the very
first local 7B answer. Every loopback request must bypass proxies.
"""
import urllib.request as ur

from app import netutil


def test_is_local_url():
    assert netutil.is_local_url("http://localhost:11434/v1")
    assert netutil.is_local_url("http://127.0.0.1:11434")
    assert netutil.is_local_url("http://[::1]:11434/api/tags")
    assert not netutil.is_local_url("https://open.bigmodel.cn/api/paas/v4")
    assert not netutil.is_local_url("")
    assert not netutil.is_local_url(None)


def test_local_urlopen_uses_empty_proxy_handler(monkeypatch):
    """The opener must carry a ProxyHandler with NO proxies, so loopback calls
    never reach the system proxy (which answers 502)."""
    captured = {}

    class FakeOpener:
        def open(self, req, timeout=None):
            captured["opened"] = True

            class _R:
                def read(self):
                    return b"{}"

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return _R()

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(ur, "build_opener", fake_build_opener)
    netutil.local_urlopen(netutil.local_request("http://127.0.0.1:11434/api/tags"),
                          timeout=1)
    assert captured["opened"] is True
    proxies = [h for h in captured["handlers"] if isinstance(h, ur.ProxyHandler)]
    assert proxies, "no ProxyHandler installed -> proxy could intercept localhost"
    assert all(h.proxies == {} for h in proxies)
