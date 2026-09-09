# -*- coding: utf-8 -*-
"""Loopback-endpoint network helpers (proxy bypass).

On this machine Windows has a system proxy enabled (127.0.0.1:6789). That is
a trap for calls to LOCAL services (Ollama on :11434):

  - the OpenAI SDK's httpx client honours HTTP(S)_PROXY env vars (trust_env
    defaults to True) -> the loopback request is sent to the proxy;
  - urllib honours the Windows *registry* proxy settings -> same trap even
    when the env vars are empty.

Either way the proxy answers 502 and the call dies. Benchmark job #30 was
killed on its very first local-7B answer by exactly this (see events:
channel=ollama -> LLMError InternalServerError 502).

Rule: every request to a loopback endpoint must bypass proxies explicitly.
"""
from __future__ import annotations

import urllib.request as _ur
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1", "",
               "host.docker.internal", "gateway.docker.internal",
               "docker.for.win.localhost", "docker.for.mac.localhost")


def is_local_url(url: str | None) -> bool:
    """True for loopback / docker-internal endpoints that must bypass the
    external CN proxy.

    Includes host.docker.internal: the dev compose routes Ollama through
    the host at that name — sending it via the external LLM_PROXY (mihomo
    6789) times out / 502s, so it is treated as "local" for proxy bypass.
    """
    if not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS or host.startswith("127.") \
        or host.endswith(".internal")


def local_request(url: str, data: bytes | None = None,
                  headers: dict | None = None):
    """urllib Request for a loopback endpoint (JSON by default)."""
    return _ur.Request(url, data=data,
                       headers=headers or {"Content-Type": "application/json"})


def local_urlopen(req, timeout: float | None = None):
    """urlopen() with proxies disabled — for loopback endpoints only."""
    opener = _ur.build_opener(_ur.ProxyHandler({}))
    return opener.open(req, timeout=timeout) if timeout is not None \
        else opener.open(req)
