# -*- coding: utf-8 -*-
"""DFMEA 领域逻辑（design §15）——查历史 / 查表 / 写行 / 来源标注裁决。

**定位**：本模块是「零件」层，只提供**确定性**的数据访问与校验；**不含任何
pipeline 生成逻辑**（生成仍由 LLM 完成，见 `pipeline.generate_from_request`）。

三条硬约束（对齐 design §15.2，落地为代码而非提示词自觉）：

  1. **来源闭集**（`validate_sources`）：每格来源必须是 `history` / `table` /
     `expert:<名字>` / `ai_inferred` / `ai_new` 之一。越界**拒绝写入** ——
     这是「LLM 提名、代码裁决」在本链路的落点。
  2. **AP 以表为准**（`write_row`）：给了 S/O/D 时，AP 一律由 `fmea_ap_matrix`
     查出并**覆盖** LLM 给的取值（记录 `ap_corrected`）。评分与行动优先级必须
     可追溯到表，不能由模型随口给。
  3. **`ai_new` 可被机器筛出**（`pending_ai_new`）：全新功能产生的值必须能单独
     捞成「待人工确认清单」，不允许静默混入正常值。

「不得越级代填」由 DFMEA 工程师的**本体规则**约束（LLM 自觉层 + 人工复核）；
代码侧只保证来源可查、可筛、可追溯 —— 这与「代码定路径、LLM 只做节点」一致。
"""
import contextvars
import json
import time

# ---------------- 来源闭集 ----------------

SRC_HISTORY = "history"            # 历史 FMEA 库命中
SRC_TABLE = "table"                # AP 表 / S-O-D 准则查表
SRC_EXPERT_PREFIX = "expert:"      # 询问专家数字人，形如 expert:射频专家
SRC_AI_INFERRED = "ai_inferred"    # AI 推断（有类推依据）
SRC_AI_NEW = "ai_new"              # AI 生成·待人工确认（全新功能，无类推依据）

SOURCE_KINDS = (SRC_HISTORY, SRC_TABLE, SRC_AI_INFERRED, SRC_AI_NEW)

#: 逐格来源的字段名（即 DFMEA 表的「格」）
SOURCE_FIELDS = ("failure_mode", "failure_effect", "severity", "failure_cause",
                 "occurrence", "prevention_control", "detection_control",
                 "detection", "ap", "action")

# ---------------- 运行期上下文 ----------------
# pipeline 执行线程内 set，fmea_write_row 借此自动关联 run_id；
# ask 深度用于阻断「专家再问专家」的无限递归。

_run_id: contextvars.ContextVar = contextvars.ContextVar("fmea_run_id", default=None)
_ask_depth: contextvars.ContextVar = contextvars.ContextVar("fmea_ask_depth", default=0)
#: 本节点**允许询问**的专家名列表（由 pipeline 引擎按 ask 边注入）。
#: None = 未受约束（非 pipeline 场景，如手动对话）；[] = 明确不许询问任何人。
_allowed_experts: contextvars.ContextVar = contextvars.ContextVar(
    "fmea_allowed_experts", default=None)

MAX_ASK_DEPTH = 2


def set_run_id(rid) -> None:
    _run_id.set(rid)


def current_run_id():
    return _run_id.get()


def set_allowed_experts(names) -> None:
    """设置本节点可询问的专家名单（ask 边的执行语义）。None = 不限制。"""
    _allowed_experts.set(list(names) if names else None)


def allowed_experts():
    return _allowed_experts.get()


def ask_depth() -> int:
    return _ask_depth.get()


def push_ask() -> None:
    _ask_depth.set(_ask_depth.get() + 1)


def pop_ask() -> None:
    _ask_depth.set(max(0, _ask_depth.get() - 1))


# ---------------- 来源标注裁决 ----------------

def source_kind_ok(s) -> bool:
    """来源标注是否合法。

    允许**带引用号**的形式（`history#13` / `table#ap` / `table#severity5`）——
    因为 `history_query` 返回的正是这种可追溯引用号，逐格标注时把它原样填回
    是最自然、也最可复核的做法（自审 2026-09-11：原实现只认裸 `history`，
    会让「按引用号标注」被判非法，与查询侧自相矛盾）。
    `expert:` 前缀必须带具体专家名（不带名字无法追溯）。
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    if s.startswith(SRC_EXPERT_PREFIX):
        return len(s) > len(SRC_EXPERT_PREFIX)
    base = s.split("#", 1)[0]
    return base in SOURCE_KINDS


def validate_sources(sources) -> tuple[bool, list[str]]:
    """逐格校验来源字典。返回 (ok, errors)。未填的格允许缺省。"""
    if not isinstance(sources, dict):
        return False, ["sources 必须是对象（逐格来源字典）"]
    errs: list[str] = []
    for f in SOURCE_FIELDS:
        v = sources.get(f)
        if v in (None, ""):
            continue
        if not source_kind_ok(v):
            errs.append(f"{f}: 非法来源「{v}」——须为 history / table / "
                        f"expert:<专家名> / ai_inferred / ai_new 之一")
    return (not errs), errs


# ---------------- 查历史 FMEA ----------------

def history_query(conn, part: str = "", keyword: str = "",
                  limit: int = 8) -> dict:
    """按部件/编号或关键词检索历史 FMEA 条目（取值优先级链第 1 级）。

    每条返回带 `ref`（形如 `history#12`）—— 供 DFMEA 逐格标注来源时引用，
    使「这一格从哪来」可被复核门逐条追溯。
    """
    part = (part or "").strip()
    keyword = (keyword or "").strip()
    if not part and not keyword:
        return {"ok": False, "error": "part 与 keyword 至少提供一个"}
    clauses, params = [], []
    if part:
        clauses.append("(part LIKE ? OR part_no LIKE ?)")
        params += [f"%{part}%", f"%{part}%"]
    if keyword:
        clauses.append("(failure_mode LIKE ? OR failure_cause LIKE ?"
                       " OR failure_effect LIKE ?)")
        params += [f"%{keyword}%"] * 3
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        lim = max(1, min(int(limit or 8), 20))
    except Exception:
        lim = 8
    rows = conn.execute(
        "SELECT id, part, part_no, function, failure_mode, failure_effect,"
        " severity, failure_cause, occurrence, prevention_control,"
        " detection_control, detection, ap, action, source_doc"
        f" FROM fmea_cases {where}"
        " ORDER BY severity DESC, occurrence DESC LIMIT ?",
        params + [lim]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["ref"] = f"{SRC_HISTORY}#{d['id']}"   # 逐格来源的可引用编号
        items.append(d)
    return {"ok": True, "result": {"hits": len(items), "items": items}}


# ---------------- 查表（AP / S-O-D 准则） ----------------

def ap_lookup(conn, severity, occurrence, detection) -> dict:
    """按 (S, O, D) 查 AP 行动优先级（H/M/L）。"""
    try:
        s, o, d = int(severity), int(occurrence), int(detection)
    except Exception:
        return {"ok": False, "error": "severity/occurrence/detection 必须是整数"}
    if not all(1 <= v <= 10 for v in (s, o, d)):
        return {"ok": False, "error": "S/O/D 必须在 1~10 之间"}
    r = conn.execute(
        "SELECT ap FROM fmea_ap_matrix WHERE severity=? AND occurrence=?"
        " AND detection=?", (s, o, d)).fetchone()
    if not r:
        return {"ok": False, "error": f"AP 表中查无 (S={s},O={o},D={d}) 组合"}
    return {"ok": True, "result": {"severity": s, "occurrence": o, "detection": d,
                                   "ap": r["ap"], "ref": f"{SRC_TABLE}#ap"}}


def criterion_lookup(conn, dimension: str, score=None) -> dict:
    """查 S/O/D 评分准则：给 score 查单条，不给则返回该维度 1~10 全表。"""
    dimension = (dimension or "").strip().lower()
    if dimension not in ("severity", "occurrence", "detection"):
        return {"ok": False, "error": "dimension 必须是 severity/occurrence/detection"}
    if score is None or score == "":
        rows = conn.execute(
            "SELECT score, criterion FROM fmea_sod_criteria WHERE dimension=?"
            " ORDER BY score", (dimension,)).fetchall()
        return {"ok": True, "result": {
            "dimension": dimension, "ref": f"{SRC_TABLE}#{dimension}",
            "criteria": [dict(r) for r in rows]}}
    try:
        sc = int(score)
    except Exception:
        return {"ok": False, "error": "score 必须是整数"}
    r = conn.execute(
        "SELECT score, criterion FROM fmea_sod_criteria WHERE dimension=?"
        " AND score=?", (dimension, sc)).fetchone()
    if not r:
        return {"ok": False, "error": f"准则表中查无 {dimension}={sc}"}
    return {"ok": True, "result": {"dimension": dimension, "score": r["score"],
                                   "criterion": r["criterion"],
                                   "ref": f"{SRC_TABLE}#{dimension}{sc}"}}


def criteria_all(conn) -> dict:
    """一次返回 S/O/D **三张**准则表。

    为什么需要：DFMEA 工程师先要拿到评分口径才能打分。若按维度分三次查，
    再加上逐条查 AP 与逐行写库，单次 tool-use 循环的轮数很快被耗尽
    （实测 2026-09-11：模型卡在「查 O 准则」就结束了，产出 0 行）。
    提供**批量粒度**的动作是「补零件」，不是干预生成。
    """
    out = {}
    for dim in ("severity", "occurrence", "detection"):
        rows = conn.execute(
            "SELECT score, criterion FROM fmea_sod_criteria WHERE dimension=?"
            " ORDER BY score", (dim,)).fetchall()
        out[dim] = [dict(r) for r in rows]
    return {"ok": True, "result": {"criteria": out, "ref": f"{SRC_TABLE}#all"}}


def ap_lookup_many(conn, items: list) -> dict:
    """批量查 AP：`items=[{severity,occurrence,detection}, …]`。

    同一行 DFMEA 的 AP 必须逐条查表，但**不必逐条往返** —— 一次调用查多条。
    """
    out, bad = [], []
    for it in (items or []):
        if not isinstance(it, dict):
            bad.append({"item": it, "error": "元素必须是对象"})
            continue
        r = ap_lookup(conn, it.get("severity"), it.get("occurrence"),
                      it.get("detection"))
        if r.get("ok"):
            out.append(r["result"])
        else:
            bad.append({"item": it, "error": r.get("error")})
    return {"ok": True, "result": {"items": out, "failed": bad,
                                   "hits": len(out)}}


# ---------------- 写 DFMEA 行 ----------------

def _as_int(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except Exception:
        return None


def write_row(conn, row: dict, run_id=None) -> dict:
    """写一行 DFMEA 记录。来源非法即拒绝；AP 一律以 AP 表为准。"""
    row = row or {}
    fm = (row.get("failure_mode") or "").strip()
    if not fm:
        return {"ok": False, "error": "failure_mode 不能为空"}
    sources = row.get("sources") or {}
    ok, errs = validate_sources(sources)
    if not ok:
        return {"ok": False, "error": "来源标注非法：" + "；".join(errs)}

    s, o, d = _as_int(row.get("severity")), _as_int(row.get("occurrence")), \
        _as_int(row.get("detection"))
    ap = (row.get("ap") or "").strip().upper() or None
    ap_corrected = False
    # AP 以表为准（S/O/D 齐备时）。查不到组合不阻断写入 —— 记为修正未生效。
    if s and o and d:
        looked = ap_lookup(conn, s, o, d)
        if looked.get("ok"):
            table_ap = looked["result"]["ap"]
            if ap != table_ap:
                ap_corrected = ap is not None
                ap = table_ap
        elif ap is None:
            return {"ok": False, "error": looked.get("error", "AP 查表失败")}

    rid = run_id if run_id is not None else current_run_id()
    cur = conn.execute(
        "INSERT INTO dfmea_rows(run_id, part, function, failure_mode,"
        " failure_effect, severity, failure_cause, occurrence,"
        " prevention_control, detection_control, detection, ap, action,"
        " sources, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, (row.get("part") or "").strip(), row.get("function"), fm,
         row.get("failure_effect"), s, row.get("failure_cause"), o,
         row.get("prevention_control"), row.get("detection_control"), d, ap,
         row.get("action"),
         json.dumps(sources, ensure_ascii=False), time.time()))
    conn.commit()
    ai_new_fields = [f for f, v in sources.items()
                     if str(v).startswith(SRC_AI_NEW)]
    return {"ok": True, "result": {
        "row_id": cur.lastrowid, "run_id": rid, "ap": ap,
        "ap_corrected": ap_corrected,
        "ai_new_fields": ai_new_fields}}


def write_rows(conn, rows: list, run_id=None) -> dict:
    """**批量**写 DFMEA 行：一次调用写多行。

    为什么需要：一张 DFMEA 表动辄十几行，逐行调用会把单次 tool-use 循环的
    轮数耗尽（实测 2026-09-11：模型连"查准则"都没查完就结束了）。
    逐行仍走同一个 `write_row`（来源闭集校验 + AP 以表为准**不放松**），
    只是把往返次数从 N 降到 1。失败的行走 `errors` 逐条返回，不静默吞掉。
    """
    ok_ids, errs = [], []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            errs.append({"index": i, "error": "元素必须是对象"})
            continue
        r = write_row(conn, row, run_id)
        if r.get("ok"):
            ok_ids.append(r["result"])
        else:
            errs.append({"index": i,
                         "failure_mode": row.get("failure_mode"),
                         "error": r.get("error")})
    return {"ok": True, "result": {
        "written": len(ok_ids), "failed": len(errs),
        "items": ok_ids, "errors": errs}}


def list_rows(conn, run_id=None, limit: int = 500) -> list[dict]:
    """DFMEA 结果行（读取侧）。

    此前 `dfmea_rows` 只写不读 —— 用户在界面上看不到产出。每行附带
    `ai_new_fields`（标了 `ai_new` 的格），便于前端直接高亮待人工确认项。
    """
    if run_id is None:
        rows = conn.execute(
            "SELECT * FROM dfmea_rows ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM dfmea_rows WHERE run_id=? ORDER BY id",
            (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        src = d.get("sources")
        d["sources"] = src if isinstance(src, dict) else {}
        d["ai_new_fields"] = [f for f, v in d["sources"].items()
                              if str(v).startswith(SRC_AI_NEW)]
        out.append(d)
    return out


def pending_ai_new(conn, run_id=None) -> list[dict]:
    """待人工确认清单：`ai_new`（AI 生成）所在的格逐条列出。"""
    rid = run_id if run_id is not None else current_run_id()
    if rid is None:
        rows = conn.execute(
            "SELECT id, part, failure_mode, sources FROM dfmea_rows"
            " ORDER BY id DESC LIMIT 200").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, part, failure_mode, sources FROM dfmea_rows"
            " WHERE run_id=? ORDER BY id", (rid,)).fetchall()
    out = []
    for r in rows:
        src = r["sources"] if isinstance(r["sources"], dict) else {}
        fields = [f for f, v in src.items() if str(v).startswith(SRC_AI_NEW)]
        if fields:
            out.append({"row_id": r["id"], "part": r["part"],
                        "failure_mode": r["failure_mode"], "fields": fields})
    return out
