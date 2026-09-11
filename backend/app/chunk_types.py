# -*- coding: utf-8 -*-
"""chunk 内容类型体系（design §11）：词表 + 两个正交维度。

设计要点（铁律 L1 = LLM 无终审权 / 提名-裁决分离）：
  - type 词表**用户可自定义**（表 `chunk_types`）；`builtin` 与 `unknown`
    不可删除（`builtin` 可改显示名/说明/两维度/权重/停用）；
  - **LLM 只提名 type**：两个维度（confidence / mandatory）一律由词表
    默认值**确定性裁决**，LLM 的提名不直接落库；
  - type 不在 active 词表 -> 回落 `unknown`，禁止臆造（铁律 L2）；
  - LLM 不可用时 `rule_type()` 关键词兜底（降级链，不中断主链）。

两个维度（正交，见 design §11.1）：
  - `confidence`: high / medium / low  —— 这条知识**有多准**
  - `mandatory` : 0 参考 / 1 建议 / 2 强制 —— 这条知识**能不能违反**

为什么必须两个维度：低置信 + 强制 = 内部口径（必须遵守但未必可靠）；
高置信 + 参考 = 公开案例（准确但仅供参考）。合成一个字段就表达不了。
"""
import re

UNKNOWN = "unknown"

CONFIDENCE_LEVELS = ("high", "medium", "low")
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
MANDATORY_LEVELS = (0, 1, 2)
MANDATORY_LABELS = {0: "参考", 1: "建议", 2: "强制"}

#: 语义来源标记（写入 chunks.type_source，便于区分责任链）
SOURCE_LLM = "llm"
SOURCE_RULE = "rule"
SOURCE_USER = "user"
SOURCE_LEGACY = "legacy"

MAX_LABEL_LEN = 24
MAX_DESC_LEN = 200
MAX_CODE_LEN = 32

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,%d}$" % (MAX_CODE_LEN - 1))

# ---------------- 预设词表（builtin） ----------------
#
# 可改（label / description / 默认两维度 / priority / status），不可删。
# `unknown` 是兜底项，同样不可删。
BUILTIN_TYPES: list[dict] = [
    {"code": "hard_rule", "label": "强制规则", "default_confidence": "high",
     "default_mandatory": 2, "priority": 100,
     "description": "平台或部门的硬性约束，回答必须遵守，违反即错"},
    {"code": "regulation", "label": "法规条文", "default_confidence": "high",
     "default_mandatory": 2, "priority": 95,
     "description": "法律法规、标准条款，须逐字遵守"},
    {"code": "fact", "label": "事实陈述", "default_confidence": "high",
     "default_mandatory": 1, "priority": 60,
     "description": "可验证的客观事实"},
    {"code": "metric", "label": "数据指标", "default_confidence": "medium",
     "default_mandatory": 1, "priority": 55,
     "description": "数字与口径定义，须标注来源与时点"},
    {"code": "term", "label": "术语概念", "default_confidence": "high",
     "default_mandatory": 0, "priority": 50,
     "description": "名词定义与解释"},
    {"code": "case", "label": "案例示例", "default_confidence": "medium",
     "default_mandatory": 0, "priority": 40,
     "description": "具体实例，仅供参考"},
    {"code": "opinion", "label": "观点建议", "default_confidence": "low",
     "default_mandatory": 0, "priority": 30,
     "description": "主观建议，可被推翻"},
    {"code": UNKNOWN, "label": "未定类型", "default_confidence": "low",
     "default_mandatory": 0, "priority": 0,
     "description": "兜底：判定失败或不在词表"},
]

BUILTIN_CODES = {t["code"] for t in BUILTIN_TYPES}


# ---------------- 预设写入 ----------------

def ensure_seed(conn) -> int:
    """幂等写入预设词表：只 INSERT 缺失的 code，**不覆盖**用户已改的值。

    返回本次新插入的条数（0 = 词表已完整）。"""
    existing = {r["code"] for r in
                conn.execute("SELECT code FROM chunk_types").fetchall()}
    added = 0
    for t in BUILTIN_TYPES:
        if t["code"] in existing:
            continue
        conn.execute(
            "INSERT INTO chunk_types(code, label, description,"
            " default_confidence, default_mandatory, priority, builtin,"
            " status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (t["code"], t["label"], t["description"], t["default_confidence"],
             t["default_mandatory"], t["priority"], True, "active", _now()))
        added += 1
    if added:
        conn.commit()
    return added


def _now() -> float:
    import time
    return time.time()


# ---------------- 读取 ----------------

def _row_to_dict(r) -> dict:
    d = dict(r)
    d["default_mandatory"] = int(d.get("default_mandatory") or 0)
    d["priority"] = int(d.get("priority") or 0)
    d["builtin"] = bool(d.get("builtin"))
    d["mandatory_label"] = MANDATORY_LABELS.get(d["default_mandatory"], "参考")
    d["confidence_label"] = CONFIDENCE_LABELS.get(
        d.get("default_confidence") or "low", "低")
    return d


def list_types(conn, active_only: bool = False) -> list[dict]:
    """词表列表（按 priority 降序，再按 code）。"""
    where = "WHERE status='active'" if active_only else ""
    rows = conn.execute(
        f"SELECT id, code, label, description, default_confidence,"
        f" default_mandatory, priority, builtin, status, created_at"
        f" FROM chunk_types {where} ORDER BY priority DESC, code").fetchall()
    return [_row_to_dict(r) for r in rows]


def type_map(conn) -> dict[str, dict]:
    """{code: 词表项}（仅 active）—— 判定的裁决依据。"""
    return {t["code"]: t for t in list_types(conn, active_only=True)}


def get_by_id(conn, type_id: int):
    row = conn.execute(
        "SELECT id, code, label, description, default_confidence,"
        " default_mandatory, priority, builtin, status, created_at"
        " FROM chunk_types WHERE id=?", (type_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_by_code(conn, code: str):
    row = conn.execute(
        "SELECT id, code, label, description, default_confidence,"
        " default_mandatory, priority, builtin, status, created_at"
        " FROM chunk_types WHERE code=?", (code,)).fetchone()
    return _row_to_dict(row) if row else None


def usage_count(conn, code: str) -> int:
    """该 type 被多少 chunk 引用（删除前的引用完整性检查）。"""
    return conn.execute(
        "SELECT COUNT(*) c FROM chunks WHERE type=?", (code,)).fetchone()["c"]


# ---------------- 写入（用户自定义词表） ----------------

class TypeError_(Exception):
    """词表校验失败（供 API 层转 400）。"""


def _validate(code: str, label: str, confidence: str, mandatory: int) -> None:
    if not _CODE_RE.match(code or ""):
        raise TypeError_("code 必须是英文小写字母开头、只含小写字母/数字/下划线")
    if not (label or "").strip():
        raise TypeError_("label 不能为空")
    if len(label) > MAX_LABEL_LEN:
        raise TypeError_(f"label 超过 {MAX_LABEL_LEN} 字")
    if confidence not in CONFIDENCE_LEVELS:
        raise TypeError_("confidence 必须是 high/medium/low")
    if int(mandatory) not in MANDATORY_LEVELS:
        raise TypeError_("mandatory 必须是 0(参考)/1(建议)/2(强制)")


def create_type(conn, code: str, label: str, description: str = "",
                default_confidence: str = "medium",
                default_mandatory: int = 0, priority: int = 10) -> dict:
    """新增自定义 type（用户可自定义词表）。"""
    code = (code or "").strip().lower()
    label = (label or "").strip()
    description = (description or "").strip()[:MAX_DESC_LEN]
    _validate(code, label, default_confidence, default_mandatory)
    if code == UNKNOWN:
        raise TypeError_("unknown 是保留的兜底类型，不可新增")
    if get_by_code(conn, code):
        raise TypeError_(f"type code 已存在：{code}")
    conn.execute(
        "INSERT INTO chunk_types(code, label, description, default_confidence,"
        " default_mandatory, priority, builtin, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (code, label, description, default_confidence, int(default_mandatory),
         int(priority), False, "active", _now()))
    conn.commit()
    return get_by_code(conn, code)


def update_type(conn, type_id: int, patch: dict) -> dict:
    """改 label / description / 两维度 / priority / status（builtin 也能改，但不可删）。"""
    cur_row = get_by_id(conn, type_id)
    if not cur_row:
        raise TypeError_("type 不存在")
    label = (patch.get("label") or cur_row["label"]).strip()
    confidence = patch.get("default_confidence") or cur_row["default_confidence"]
    mandatory = patch.get("default_mandatory")
    mandatory = cur_row["default_mandatory"] if mandatory is None else int(mandatory)
    _validate(cur_row["code"], label, confidence, mandatory)
    description = patch.get("description")
    description = (cur_row.get("description") or "") if description is None \
        else str(description).strip()[:MAX_DESC_LEN]
    priority = patch.get("priority")
    priority = cur_row["priority"] if priority is None else int(priority)
    status = patch.get("status") or cur_row["status"]
    if status not in ("active", "disabled"):
        raise TypeError_("status 必须是 active/disabled")
    if cur_row["code"] == UNKNOWN and status == "disabled":
        raise TypeError_("unknown 是兜底类型，不可停用")
    conn.execute(
        "UPDATE chunk_types SET label=?, description=?, default_confidence=?,"
        " default_mandatory=?, priority=?, status=? WHERE id=?",
        (label, description, confidence, mandatory, priority, status, type_id))
    conn.commit()
    return get_by_id(conn, type_id)


def delete_type(conn, type_id: int) -> None:
    """删除自定义 type。builtin / unknown 不可删；被 chunk 引用不可删。"""
    cur_row = get_by_id(conn, type_id)
    if not cur_row:
        raise TypeError_("type 不存在")
    if cur_row["builtin"]:
        raise TypeError_("预置类型不可删除（可改说明或停用）")
    if cur_row["code"] == UNKNOWN:
        raise TypeError_("unknown 是兜底类型，不可删除")
    n = usage_count(conn, cur_row["code"])
    if n:
        raise TypeError_(f"该类型已被 {n} 个 chunk 引用，不可删除"
                         "（可先改这些 chunk 的类型，或将其停用）")
    conn.execute("DELETE FROM chunk_types WHERE id=?", (type_id,))
    conn.commit()


# ---------------- 裁决（提名-裁决分离） ----------------

#: unknown 缺失时的硬编码兜底（理论上 ensure_seed 已写入）
_FALLBACK_UNKNOWN = {"code": UNKNOWN, "default_confidence": "low",
                     "default_mandatory": 0}


def resolve(code: str | None, tmap: dict[str, dict]) -> dict:
    """**确定性裁决**：把 LLM 提名的 type 映射成落库三元组。

    - code 不在 active 词表（含 None / 空 / 拼错）-> 回落 `unknown`；
    - `type_confidence` / `type_mandatory` **一律取词表默认值**
      （LLM 若额外提名 confidence 也不采用 —— 保证同一 type 全库语义一致）。

    返回 {"type", "type_confidence", "type_mandatory"}。"""
    key = (code or "").strip()
    t = tmap.get(key)
    if not t:
        key = UNKNOWN
        t = tmap.get(UNKNOWN) or _FALLBACK_UNKNOWN
    return {
        "type": key,
        "type_confidence": t.get("default_confidence") or "low",
        "type_mandatory": int(t.get("default_mandatory") or 0),
    }


# ---------------- 规则兜底（LLM 不可用时的降级链） ----------------
#
# 顺序敏感：强制语气 > 法规 > 指标 > 术语 > 案例 > 观点。
_RULE_PATTERNS: list[tuple[str, str]] = [
    (r"必须|禁止|不得|严禁|务必|一律|须遵守|不允许", "hard_rule"),
    (r"第[一二三四五六七八九十百]+条|条例|法规|规范|标准|GB/T|ISO ?\d|管理办法", "regulation"),
    (r"\d+(\.\d+)?\s*%|同比|环比|增长|下降|指标|KPI|占比|金额|数量达到", "metric"),
    (r"是指|定义为|含义是|称为|叫做|术语|概念|定义[:：]", "term"),
    (r"例如|比如|举例|案例|示例|如[:：]", "case"),
    (r"建议|推荐|倾向于|我认为|可能|或许|最好|不妨", "opinion"),
]


def rule_type(text: str) -> str:
    """关键词规则判定（0 LLM）。命中返回对应 code；未命中回落 `fact`。

    未命中给 `fact` 而非 `unknown`：这是降级链（LLM 不可用）下的最佳可用
    判定，且 `type_source='rule'` 可追溯；若给 unknown 会让全库类型覆盖率
    在降级期间失真，掩盖"是链路降级"而非"内容不可判定"。
    """
    t = text or ""
    for pattern, code in _RULE_PATTERNS:
        if re.search(pattern, t):
            return code
    return "fact"


# ---------------- prompt 注入（把 active 词表给 LLM） ----------------

def catalog_for_prompt(tmap: dict[str, dict]) -> str:
    """生成给 LLM 的类型候选说明（只给可选项，不给维度 —— 维度由代码裁决）。"""
    items = sorted(tmap.values(), key=lambda t: -int(t.get("priority") or 0))
    lines = [f"- {t['code']}（{t['label']}）：{t.get('description') or ''}"
             for t in items if t["code"] != UNKNOWN]
    lines.append(f"- {UNKNOWN}：以上都不合适时使用")
    return "\n".join(lines)
