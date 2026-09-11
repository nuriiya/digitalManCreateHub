# -*- coding: utf-8 -*-
"""统一消息协议 DMP（design §10）：与大语言模型的一切交互统一用 JSON 信封传递。

本模块是协议层的**唯一实现**，提供五个能力：

1. `Envelope` —— 消息信封数据模型（kind 十类闭集）；
2. `render(env)` —— Envelope → OpenAI 消息（`content` 是 JSON 字符串）；
3. `parse(content)` —— LLM 输出 / DB 行 → Envelope，非 JSON 自动降级 legacy 包装；
4. `parse_json(text)` —— **全项目唯一的 JSON 抠取实现**（收敛原 4 套私有解析器）；
5. `StreamJsonReader` —— 流式增量字段解码（不需要等完整 JSON 就能逐 token 渲染）。

设计约束（design §10.1，不可破）：
  C1 OpenAI 兼容外壳 —— content 必须是字符串，JSON 在此序列化（`ensure_ascii=False`）；
  C2 LLM 无终审权 —— 协议只负责结构化传输，字段落库前仍走确定性校验；
  C3 流式体验不可退 —— 见 `StreamJsonReader`；
  C4 旧会话可读 —— 纯文本内容自动包成 legacy 信封；
  C5 可关断 —— `DMP_MODE=off` 时调用方回退自由文本（见 `dmp_enabled()`）。
"""
import json
import os
import re
from dataclasses import dataclass, field

SCHEMA_VERSION = 1

#: kind 闭集（design §10.2）。越界即视为非法信封，降级 legacy。
KINDS = (
    "instruction",     # system → llm：身份 / 铁律 / 护栏
    "context",         # system → llm：锚点 / 本体 / 关系 / 动作 / RAG（每条带 ref）
    "question",        # user → llm：用户提问
    "answer",          # llm → user：回答（markdown + 引用 + 拒答标记）
    "tool_call",       # llm → system：动作提名
    "tool_result",     # system → llm：动作执行结果
    "review_request",  # identity → identity：复核请求（对齐上轮定的 {"call":"review"}）
    "review_result",   # identity → identity：复核结论 / 补丁
    "verdict",         # llm → system：判卷 / 考核 / 分诊结果
    "handoff",         # identity → identity：上下文交接物
)

#: kind → OpenAI role 映射（C1：外壳仍是 role/content）
ROLE_BY_KIND = {
    "instruction": "system",
    "context": "system",
    "question": "user",
    "answer": "assistant",
    "tool_call": "assistant",
    "tool_result": "user",
    "review_request": "user",
    "review_result": "assistant",
    "verdict": "assistant",
    "handoff": "user",
}

#: 默认的「回答文本」字段路径（流式解码与 legacy 兜底都依赖它）
ANSWER_TEXT_PATH = ("payload", "text")


def dmp_enabled() -> bool:
    """C5：`DMP_MODE=off|0|false` 时全线回退自由文本（协议 bug 不得阻断主链）。"""
    v = (os.environ.get("DMP_MODE") or "on").strip().lower()
    return v not in ("off", "0", "false", "no")


# ---------------- 1. 信封 ----------------

@dataclass
class Envelope:
    """一条结构化消息。`from_`/`to` 用 `{"type","id","name"}` 描述端点。"""
    kind: str
    payload: dict = field(default_factory=dict)
    from_: dict | None = None
    to: dict | None = None
    task: dict | None = None            # 机读任务契约 {"id","goal","criterion"}
    refs: list = field(default_factory=list)   # 证据引用 [{"kind":"chunk","id":12}]
    meta: dict = field(default_factory=dict)
    v: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        d: dict = {"v": self.v, "kind": self.kind, "payload": self.payload or {}}
        if self.from_:
            d["from"] = self.from_
        if self.to:
            d["to"] = self.to
        if self.task:
            d["task"] = self.task
        if self.refs:
            d["refs"] = self.refs
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Envelope":
        return cls(
            kind=str(d.get("kind") or "answer"),
            payload=d.get("payload") if isinstance(d.get("payload"), dict) else {},
            from_=d.get("from"),
            to=d.get("to"),
            task=d.get("task"),
            refs=d.get("refs") if isinstance(d.get("refs"), list) else [],
            meta=d.get("meta") if isinstance(d.get("meta"), dict) else {},
            v=int(d.get("v") or SCHEMA_VERSION),
        )

    def text(self) -> str:
        """取回答文本（answer.text / 兜底 payload.text）。"""
        p = self.payload or {}
        return str(p.get("text") or "")


# ---------------- 2. 渲染（唯一拍平点） ----------------

def render(env: Envelope) -> dict:
    """Envelope → OpenAI 消息。C1：content 必须是字符串，JSON 在此序列化。"""
    return {
        "role": ROLE_BY_KIND.get(env.kind, "user"),
        "content": json.dumps(env.to_dict(), ensure_ascii=False),
    }


def build_messages(envs: list[Envelope]) -> list[dict]:
    return [render(e) for e in envs]


# ---------------- 3. 解析（唯一还原点） ----------------

def parse(content: str) -> Envelope:
    """内容 → Envelope。非 JSON / 缺 kind / kind 越界 → legacy 信封（C4）。"""
    text = content or ""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            cand = json.loads(text)
        except Exception:
            cand = None
        if isinstance(cand, dict) and cand.get("kind"):
            env = Envelope.from_dict(cand)
            if env.kind in KINDS:
                return env
            return Envelope(kind="answer", payload={"text": text},
                            meta={"legacy": True, "unknown_kind": str(cand.get("kind"))})
    return Envelope(kind="answer", payload={"text": text}, meta={"legacy": True})


# ---------------- 4. JSON 抠取（全项目唯一实现） ----------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_GREEDY_RE = re.compile(r"\{.*\}|\[.*\]", re.S)


def parse_json(text: str):
    """容错 JSON 解析。顺序：直接解析 → 剥 ``` 围栏 → 贪婪匹配首个 {..}/[..]。

    这是 design §10.4 要求收敛的**唯一**解析实现，替代原来的四套私有版本
    （`llm.extract_json` / `pipeline._extract_json` / `main._design_changes_via_llm`
    / `main._design_pipeline_via_llm`）。全部失败返回 None（调用方决定降级策略）。
    """
    if not text:
        return None
    s = text.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = _FENCE_RE.search(s)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    m = _GREEDY_RE.search(s)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ---------------- 5. 流式增量字段解码 ----------------

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


class StreamJsonReader:
    """C3：把「一个 JSON 文档的 token 流」变成「某字段值的增量文本流」。

    定位路径（默认 `payload.text`）后，逐字符解码该字符串值；遇到未转义的
    结束引号即完成。转义序列跨 token 断开时等待下一片（不产出半个字符）。

    用法::

        r = StreamJsonReader()                 # 默认取 payload.text
        for token in llm_stream:
            delta = r.feed(token)
            if delta:
                yield delta                    # 逐 token 渲染 markdown
        env = r.finish()                       # 完整解析 + 校验（可能为 None）
    """

    def __init__(self, path: tuple[str, ...] = ANSWER_TEXT_PATH):
        self.path = tuple(path)
        self.buf = ""
        self.parts: list[str] = []
        self._start: int | None = None
        self._i = 0
        self.done = False

    # -- 内部 --
    def _locate(self) -> int | None:
        pos = 0
        for seg in self.path[:-1]:
            i = self.buf.find('"%s"' % seg, pos)
            if i < 0:
                return None
            pos = i + len(seg) + 2
        i = self.buf.find('"%s"' % self.path[-1], pos)
        if i < 0:
            return None
        j = self.buf.find(":", i + len(self.path[-1]) + 2)
        if j < 0:
            return None
        k = self.buf.find('"', j)
        if k < 0:
            return None
        return k + 1

    def _drain(self) -> str:
        raw = self.buf
        out: list[str] = []
        i = self._start + self._i  # type: ignore[operator]
        n = len(raw)
        while i < n:
            ch = raw[i]
            if ch == '"':
                self.done = True
                break
            if ch == "\\":
                if i + 1 >= n:
                    break                      # 转义未完整，等下一片
                nxt = raw[i + 1]
                if nxt == "u":
                    if i + 6 > n:
                        break
                    try:
                        out.append(chr(int(raw[i + 2:i + 6], 16)))
                    except Exception:
                        out.append(nxt)
                    i += 6
                    continue
                out.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            out.append(ch)
            i += 1
        self._i = i - self._start  # type: ignore[operator]
        return "".join(out)

    # -- 对外 --
    def feed(self, chunk: str) -> str:
        """喂入一个 token，返回该 token 带来的文本增量（可能为空串）。

        注意：目标字段解完后（`done=True`）**仍继续累积缓冲** —— JSON 文档尚未
        结束（后面还有 `,"refused":false}` 之类的尾字段），`finish()` 需要完整
        文本才能解析。2026-09-11 修复：早前 done 之后直接丢弃 token，导致
        `finish()` 永远拿不到合法 JSON（实测 9/10 通过的那一条失败项）。
        """
        if not chunk:
            return ""
        self.buf += chunk
        if self.done:
            return ""
        if self._start is None:
            self._start = self._locate()
            if self._start is None:
                return ""
        delta = self._drain()
        if delta:
            self.parts.append(delta)
        return delta

    def text(self) -> str:
        """目前已解码出的完整文本（流未结束时是部分文本）。"""
        return "".join(self.parts)

    def finish(self):
        """流结束：对完整缓冲做一次解析 + 校验。失败返回 None（调用方降级 raw 渲染）。"""
        env = parse(self.buf)
        if env.meta.get("legacy"):
            return None
        return env


# ---------------- 6. 便捷构造（入参打包） ----------------

def pack_instruction(identity: dict, rules: list | None = None,
                     guardians: list | None = None, to: dict | None = None) -> Envelope:
    """身份 + 铁律 + 护栏（design §10.2 的 instruction）。"""
    return Envelope(
        kind="instruction",
        payload={
            "identity": {
                "name": (identity or {}).get("name"),
                "mission": (identity or {}).get("mission"),
                "description": (identity or {}).get("description"),
            },
            "rules": rules or [],
            "guardians": guardians or [],
        },
        to=to or {"type": "llm"},
    )


def pack_context(anchors: list | None = None, ontology: list | None = None,
                 relations: list | None = None, actions: list | None = None,
                 rag: list | None = None, rules: list | None = None) -> Envelope:
    """结构化上下文（design §10.3）。条目应自带 `ref` 以便确定性校验不悬空。"""
    return Envelope(
        kind="context",
        payload={
            "anchors": anchors or [],
            "ontology": ontology or [],
            "relations": relations or [],
            "actions": actions or [],
            "rag": rag or [],          # 普通参考资料（mandatory <= 1）
            "rules": rules or [],      # 强制约束（mandatory == 2，必选注入）
        },
        to={"type": "llm"},
    )


def pack_question(text: str, attachments: list | None = None,
                  session_id: int | None = None, turn: int | None = None) -> Envelope:
    return Envelope(
        kind="question",
        payload={"text": text, "attachments": attachments or []},
        from_={"type": "user"},
        meta={k: v for k, v in (("session_id", session_id), ("turn", turn))
              if v is not None},
    )


def pack_answer(text: str, citations: list | None = None,
                refused: bool = False) -> Envelope:
    return Envelope(
        kind="answer",
        payload={"text": text, "citations": citations or [], "refused": refused},
        from_={"type": "identity"},
    )


def pack_tool_result(call_id: str, ok: bool, result=None, error: str = "") -> Envelope:
    return Envelope(
        kind="tool_result",
        payload={"id": call_id, "ok": bool(ok), "result": result, "error": error},
        from_={"type": "system"},
    )
