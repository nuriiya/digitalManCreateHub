# -*- coding: utf-8 -*-
"""LLM access regression: streaming call with a two-tier timeout.

- idle tier (SDK read timeout) = gap between incoming bytes: trips when a
  streaming upstream goes silent (blackhole, mid-stream stall). A
  slow-but-healthy model that keeps dripping tokens is never killed here.
- hard tier (daemon-thread ceiling) = total wall-clock cap against
  pathological loops (endless thinking / upstream busy but never done).
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from app import llm, settings_store


def _sse_delta(content: str) -> str:
    return (f'{{"id":"1","object":"chat.completion.chunk","created":0,'
            f'"model":"m","choices":[{{"index":0,"delta":'
            f'{{"content":"{content}"}},"finish_reason":null}}]}}')


def _make_sse_server(steps):
    """steps: list of (delay_seconds, payload_or_None). None = stay silent.
    Replies to POST /chat/completions as an OpenAI-style SSE stream."""
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for delay, payload in steps:
                    if delay:
                        time.sleep(delay)
                    if payload is not None:
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # client gave up (timeout) - fine

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _cfg(port, timeout, hard_timeout):
    return {"llm": {"base_url": f"http://127.0.0.1:{port}/v1",
                    "api_key": "k", "model": "m",
                    "timeout": timeout, "hard_timeout": hard_timeout}}


def test_idle_timeout_kills_silent_upstream(env):
    """Blackhole upstream (accepts, sends nothing) must raise LLMError after
    the *idle* ceiling (no bytes for N seconds) - this is the streaming
    semantics: silence = dead, slow tokens = alive."""
    llm.clear_fake_chat()
    srv, port = _make_sse_server([(8, None)])  # hold, never reply
    with patch.object(settings_store, "load_settings",
                      return_value=_cfg(port, timeout=1, hard_timeout=10)):
        t0 = time.time()
        with pytest.raises(llm.LLMError):
            llm.chat([{"role": "user", "content": "hi"}])
        elapsed = time.time() - t0
    assert elapsed < 5, f"idle timeout took too long: {elapsed:.1f}s"
    srv.shutdown()


def test_slow_stream_finishes_within_both_tiers(env):
    """Tokens drip every 0.2s (well under the idle ceiling) for 2s total:
    the call must succeed and return the concatenated stream - proving a
    slow-but-healthy model is NEVER killed by the idle tier."""
    llm.clear_fake_chat()
    steps = [(0.2, _sse_delta(c)) for c in "你好世界"]
    srv, port = _make_sse_server(steps)
    with patch.object(settings_store, "load_settings",
                      return_value=_cfg(port, timeout=1, hard_timeout=10)):
        reply = llm.chat([{"role": "user", "content": "hi"}])
    assert reply == "你好世界"
    srv.shutdown()


def test_slow_stream_hits_hard_ceiling(env):
    """Tokens keep arriving every 0.5s (idle never trips) but the total run
    exceeds the hard ceiling: the daemon-thread cap must fire - this is the
    anti-deadloop guarantee."""
    llm.clear_fake_chat()
    steps = [(0.5, _sse_delta(c)) for c in "一二三四五六七八九十"]
    srv, port = _make_sse_server(steps)  # ~5s total stream
    with patch.object(settings_store, "load_settings",
                      return_value=_cfg(port, timeout=1, hard_timeout=2)):
        t0 = time.time()
        with pytest.raises(llm.LLMError):
            llm.chat([{"role": "user", "content": "hi"}])
        elapsed = time.time() - t0
    assert 2 <= elapsed < 5, f"hard ceiling fired at {elapsed:.1f}s"
    srv.shutdown()


def test_idle_timeout_on_mid_stream_silence(env):
    """Two tokens arrive, then the upstream goes silent mid-stream: the idle
    ceiling must trip even though the stream already started."""
    llm.clear_fake_chat()
    steps = [(0.1, _sse_delta("你")), (0.1, _sse_delta("好")),
             (4.0, None), (0.1, _sse_delta("界"))]
    srv, port = _make_sse_server(steps)
    with patch.object(settings_store, "load_settings",
                      return_value=_cfg(port, timeout=1, hard_timeout=10)):
        t0 = time.time()
        with pytest.raises(llm.LLMError):
            llm.chat([{"role": "user", "content": "hi"}])
        elapsed = time.time() - t0
    assert elapsed < 4, f"mid-stream idle timeout took too long: {elapsed:.1f}s"
    srv.shutdown()


def test_fake_chat_path_stays_direct(env):
    """The injected fake chat (UT/offline) must stay synchronous - the
    daemon-thread ceiling only wraps real OpenAI calls."""
    llm.set_fake_chat(lambda msgs: "fake-reply")
    assert llm.chat([{"role": "user", "content": "hi"}]) == "fake-reply"
    llm.clear_fake_chat()


def test_parse_invalid_json_returns_none():
    assert llm.extract_json("not json at all") is None
    assert llm.extract_json('{"a": 1} trailing garbage') == {"a": 1}
    assert llm.extract_json('[1, 2, 3]') == [1, 2, 3]
    assert llm.extract_json('') is None


def test_judge_channel_disables_thinking(env):
    """Judge channel (verdict + failure attribution) must disable GLM's
    reasoning chain; chat keeps it. Regression for the 10+ minute thinking
    hang that also dropped the provider connection mid-stream (reasoning
    tokens stream as reasoning_content and are discarded here anyway)."""
    llm.clear_fake_chat()
    seen: list = []

    def fake_call(s, messages, temperature, idle_seconds, hard_seconds,
                  extra_body=None):
        seen.append(extra_body)
        return "ok", None

    with patch.object(llm, "_call_llm_with_usage", side_effect=fake_call):
        llm.chat2([{"role": "user", "content": "judge"}])
        llm.chat_persona([{"role": "user", "content": "hello"}])

    assert seen == [{"thinking": {"type": "disabled"}}, None]
