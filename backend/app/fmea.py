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
import re
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

#: 本 run 内**实际询问过的专家名**集合（确定性登记，非 LLM 记忆）。
#: 为什么要有（2026-09-13 考官卷题 17 实测「expert 引用可追溯 0 处」）：
#: 四位部件专家全都通过 ask 边被问过，但 DFMEA 工程师写行时把每格来源一律
#: 标成 `history#N` —— 因为**每一行确实都有历史证据**，于是「价值优先链」的
#: 第 3 级（问专家）虽被走到、却没有任何一处被引用，专家的把关能力在交付物里
#: **不可追溯**。回答本身自带 `source=expert:<名>`，但模型不会主动抄进 sources。
#: 这里在询问发生时登记专家名（谁问的、问了谁），供交付前强制引用与复核核验。
_consulted_experts: contextvars.ContextVar = contextvars.ContextVar(
    "fmea_consulted_experts", default=None)


def register_consulted_expert(name: str) -> None:
    """登记一位被询问过的专家（确定性，由 `_exec_ask_expert` 调用）。"""
    if not name:
        return
    cur = _consulted_experts.get()
    if cur is None:
        cur = []
        _consulted_experts.set(cur)
    if name not in cur:
        cur.append(name)


def consulted_experts() -> list:
    """本 run 内询问过的专家名（按首次询问顺序）。"""
    return list(_consulted_experts.get() or [])


def reset_consulted_experts() -> None:
    _consulted_experts.set([])


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

#: 一个格的来源标注里**不允许出现**的分隔符。一个格 = 一个来源 token。
#:
#: 为什么必须拦（2026-09-14 job#32 考官卷题 16 实测）：模型会写
#: `severity=6（history#1；table#severity6）` —— 意思是"这一格同时有两条依据"，
#: 但 schema 是**逐格单来源**。原 `source_kind_ok` 用 `s.split("#",1)[0]` 只校验
#: **第一个 `#` 之前**的前缀，于是 `"history#1；table#severity6"` 的基名是
#: `"history"` → **蒙混过关**入库；而复核/考卷按「值必须是单个 `history#<数字>`」
#: 判 → 一场考试 38 处被记为**格式异常**。
#: 正确做法不是放宽考卷，而是**在入口拦掉拼接串**（合法表达见 `_merge_src`：
#: `table#severity6` 占该格、裸 `history#1` 归 `failure_mode`）。
_SRC_SEPARATORS = "；;，,、|/"


def source_kind_ok(s) -> bool:
    """来源标注是否合法。

    允许**带引用号**的形式（`history#13` / `table#ap` / `table#severity5`）——
    因为 `history_query` 返回的正是这种可追溯引用号，逐格标注时把它原样填回
    是最自然、也最可复核的做法（自审 2026-09-11：原实现只认裸 `history`，
    会让「按引用号标注」被判非法，与查询侧自相矛盾）。
    `expert:` 前缀必须带具体专家名（不带名字无法追溯）。

    **一个格只允许一个来源 token**（2026-09-14）：含分隔符（`；;，,、|/`）即非法
    —— 拼接串既不是闭集成员、也无法被下游逐格复核。多个依据要分开写在**各自的
    字段格**上（`table#severity6` 记 `severity`、裸 `history#1` 记 `failure_mode`）。
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    if any(ch in s for ch in _SRC_SEPARATORS):
        return False
    if s.startswith(SRC_EXPERT_PREFIX):
        return len(s) > len(SRC_EXPERT_PREFIX)
    base = s.split("#", 1)[0]
    # 带引用号的形式：`#` 之后必须是**纯引用号**（数字）或**字段名+数字/字段名**
    # （`table#ap` / `table#severity6`）。空后缀（`history#`）一律非法。
    if "#" in s:
        suffix = s.split("#", 1)[1].strip()
        if not suffix:
            return False
    return base in SOURCE_KINDS


def _merge_src(dst: dict, field: str, val: str) -> None:
    """把一格解析出的来源并入 `dst`，**冲突时按"表优先、裸来源归位"裁决**。

    场景：`severity=6（history#1；table#severity6）` —— 同格两条依据。
    规则（确定性、无 LLM）：
      1. `table#<字段>…` 是**该格自己的**表引用 → 占该格；
      2. 裸 `history#N` 没有字段名 → 归到 `failure_mode`（行的**身份锚**），
         因为"这条行是从 history#N 类比来的"正是它表达的意思；
      3. 若 `failure_mode` 已被占（模型自己标过），则**保留先到的**、不覆盖。
    绝不把两个 token 拼成一个值。
    """
    if not val:
        return
    if val.startswith(SRC_TABLE) and "#" in val:
        dst[field] = val
        return
    if field == "failure_mode":
        dst.setdefault("failure_mode", val)
        return
    dst.setdefault("failure_mode", val)


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


# ---------------- 子系统名归一化（part 列的口径） ----------------

#: 部件编号族代码 → 该族的**中文子系统名**。这是「part 列写什么」的单一事实源。
#:
#: 为什么要这段（2026-09-13 实测）：DFMEA 工程师写行时把 `part_search` 返回的
#: `analogy_family`（族代码，如 `SW`）当作 part 填了进去，导致一行本应属于
#: 「连接与漫游管理」的失效模式被记在 `SW` 名下 —— 下游按子系统检索/复核时
#: **整条子系统查不到行**（考官卷题 10「连接与漫游管理」命中 0 行）。
#: 族代码是**检索用的机器键**，中文子系统名才是**人读的 part 值**，两者不能混用。
#: 这里做确定性归一：模型给族代码就翻译成中文名，给人读名就原样保留。
FAMILY_TO_SUBSYS = {
    "ANT": ["射频天线", "天线馈电与接地"],
    "RF": ["射频功率放大器", "射频接收前端", "收发切换与滤波"],
    "PMU": ["射频供电", "电源保护"],
    "CLK": ["参考时钟"],
    "SW": ["基带与固件", "连接与漫游管理", "固件升级"],
    "EMC": ["射频屏蔽与 EMC"],
    "CON": ["模块互连"],
}

#: 全部合法 part 值（中文子系统名）。`part` 只允许在这些值里取。
KNOWN_SUBSYS = [s for v in FAMILY_TO_SUBSYS.values() for s in v]

#: 失效模式关键词 → 子系统。用于「模型只给了族代码、但有多个候选子系统」时，
#: 按失效模式描述**确定性**落到最贴切的那个子系统（不是让 LLM 再猜一次）。
_SUBSYS_KEYWORDS = (
    ("连接与漫游管理", ("漫游", "认证", "扫描", "SSID", "连接管理", "重连",
                        "配对", "GATT", "服务发现", "重连", "断连")),
    ("固件升级", ("升级", "OTA", "回滚", "刷写", "版本")),
    ("基带与固件", ("基带", "状态机", "固件", "寄存器", "驱动")),
    ("天线馈电与接地", ("馈电", "接地")),
    ("射频天线", ("天线",)),
    ("射频功率放大器", ("功率放大", "发射功率", "PA", "饱和")),
    ("射频接收前端", ("接收前端", "低噪", "LNA", "灵敏度", "噪声系数")),
    ("收发切换与滤波", ("切换", "滤波", "双工", "谐波", "杂散")),
    ("射频供电", ("供电", "LDO", "纹波", "电源纹波")),
    ("电源保护", ("保护", "过流", "过温", "浪涌", "ESD")),
    ("参考时钟", ("时钟", "晶振", "频偏", "相噪")),
    ("射频屏蔽与 EMC", ("屏蔽", "EMC", "抗扰", "辐射", "干扰")),
    ("模块互连", ("互连", "连接器", "焊", "馈线", "走线")),
)


def canonical_part(part: str, failure_mode: str = "") -> str:
    """把 `part` 归一到**中文子系统名**。确定性，无 LLM。

    三种情况：
      1. 已经是合法中文子系统名 → 原样返回；
      2. 是族代码（`ANT`/`RF`/`PMU`/`CLK`/`SW`/`EMC`/`CON`）→ 若该族只对应
         一个子系统，直接翻；若对应多个，用 `failure_mode` 的关键词确定性挑选；
      3. 其它自由文本 → 用关键词打分挑最贴切的子系统；全不命中则原样保留
         （不臆造归类，交给复核门人工判断）。
    """
    p = (part or "").strip()
    fm = (failure_mode or "").strip()

    def _pick_by_keyword(cands: list) -> str:
        """在候选子系统里按 failure_mode 关键词挑一个；挑不出就取第一个。"""
        for name in cands:
            for kw_name, kws in _SUBSYS_KEYWORDS:
                if kw_name != name:
                    continue
                if any(k.lower() in fm.lower() for k in kws):
                    return name
        return cands[0] if cands else p

    # 1) 已是合法中文名
    if p in KNOWN_SUBSYS:
        return p
    # 2) 族代码
    up = p.upper()
    if up in FAMILY_TO_SUBSYS:
        fam = FAMILY_TO_SUBSYS[up]
        return fam[0] if len(fam) == 1 else _pick_by_keyword(fam)
    # 3) 自由文本：先看是否含某个子系统名，再按关键词打分
    for name in KNOWN_SUBSYS:
        if name in p:
            return name
    if fm:
        for name, kws in _SUBSYS_KEYWORDS:
            if any(k.lower() in fm.lower() for k in kws):
                return name
    return p


# ---------------- 搜索部件（分析的起点） ----------------

def part_search(conn, product: str = "", query: str = "",
                limit: int = 30) -> dict:
    """自主搜索待分析产品的**子系统清单**（design §15.7，分析起点）。

    两路信息，供 DFMEA 工程师决定「分析哪些部件、每个部件去哪找证据」：

      - `parts`：**部件知识库**里该产品的子系统（功能 / 工况 / 关键词 / 类比线索）。
        `product` 留空时返回库里有哪些产品可选。
      - `history_parts`：**历史 FMEA 库**出现过的部件编号族与条目数 —— 即
        「现有案例」的清单，据此判断哪些子系统能找到同类历史证据、哪些得靠
        专家或推断。

    **刻意不返回失效模式**：失效模式必须由 DFMEA 工程师自行推导（领域推理 +
    同类案例类比）。本动作只回答「有哪些部件」「历史上哪一族可类比」。

    **`product` 是模糊匹配**（2026-09-13 修）：库内产品名（`WiFi 模块`）与任务
    里给的产品名（`射频无线模块（蓝牙/WiFi）`）不必字面相等。若一句都没匹配上，
    回传 `products` 可用清单与 `hint`，调用方据此改词重查 —— 而不是静默返回空表。
    """
    product = (product or "").strip()
    query = (query or "").strip()
    if not product:
        prods = [r["product"] for r in conn.execute(
            "SELECT DISTINCT product FROM fmea_parts ORDER BY product").fetchall()]
        return {"ok": True, "result": {
            "products": prods, "parts": [], "history_parts": [], "count": 0,
            "hint": "请指定 product（如「WiFi 模块」）后再搜索"}}
    # 产品名**模糊匹配**：任务输入里的产品名（如「射频无线模块（蓝牙/WiFi）」）
    # 与知识库 product 字段（如「WiFi 模块」）几乎必然字面不等 —— 精确匹配会让
    # 整张部件知识库查不出来（2026-09-13 实测：13 条 WiFi 子系统全部漏检，
    # 导致 DFMEA 只覆盖 7 个历史族、丢掉 6 个子系统）。四段递进：
    #   ① 精确 → ② 子串互含 → ③ **token 重叠打分**（拆分括号/斜杠/空格后比词，
    #      命中比例 ≥ 0.5 即算，解决「（蓝牙/WiFi）」夹在中间导致子串断开的
    #      情形）→ ④ 全失败则回传可用产品清单，让调用方自己改词重查。
    prods = [r["product"] for r in conn.execute(
        "SELECT DISTINCT product FROM fmea_parts ORDER BY product").fetchall()]

    def _tokens(s):
        """切词：按非字母数字汉字分隔，保留长度 ≥2 的片段并小写。"""
        import re as _re
        parts = _re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", s)
        return {p.lower() for p in parts if len(p) >= 2}

    matched = ""
    if product in prods:
        matched = product
    else:
        low = product.lower()
        cands = [p for p in prods if p.lower() in low or low in p.lower()]
        if not cands:
            itk = _tokens(product) | (_tokens(query) if query else set())
            scored = []
            for p in prods:
                ptk = _tokens(p)
                if not ptk or not itk:
                    continue
                ratio = len(ptk & itk) / len(ptk)
                if ratio >= 0.5:
                    scored.append((ratio, len(p), p))
            if scored:
                # 命中率优先，其次取更具体的（词更长）产品名
                scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
                cands = [scored[0][2]]
        if cands:
            # 取最长匹配（「WiFi 模块」优先于泛化词），词长降序避免短词抢先
            matched = sorted(cands, key=len, reverse=True)[0]
    if not matched:
        return {"ok": True, "result": {
            "product": product, "parts": [], "history_parts": [], "count": 0,
            "products": prods,
            "hint": f"知识库中没有匹配「{product}」的产品。可用产品："
                    + "、".join(prods) + "。请用其中之一作为 product 重查。"}}
    product = matched
    clauses, params = ["product = ?"], [product]
    if query:
        clauses.append("(subsystem LIKE ? OR function LIKE ? OR condition LIKE ?"
                       " OR ? = ANY(keywords))")
        params += [f"%{query}%"] * 3 + [query]
    try:
        lim = max(1, min(int(limit or 30), 60))
    except Exception:  # noqa: BLE001
        lim = 30
    rows = conn.execute(
        "SELECT id, subsystem, function, condition, keywords, note"
        " FROM fmea_parts WHERE " + " AND ".join(clauses) +
        " ORDER BY id LIMIT ?", params + [lim]).fetchall()
    # 额外抽出两个"拿去就用"的类比线索：
    #
    #   - `analogy_family`（如 `ANT`）：**可直接喂给 history_query 的族代码**，
    #     让"类比推导"从"靠模型从提示文字里抠缩写"变成"拿去就用"。
    #   - `analogy_cases`（如 `["history#1","history#2"]`）：**把 note 里的
    #     `BT-XXX-NN` 编号解析成具体 case id**。
    #
    # 为什么第 2 项是必须的（2026-09-14 job#31 实测）：`family=SW` 在历史库里
    # 有 **3 条**案例（SW-01 状态机死锁 / SW-02 服务发现超时 / SW-03 升级变砖），
    # 而 WiFi 侧也有 **3 个** SW 子系统（基带与固件 / 连接与漫游管理 / 固件升级）。
    # 只给族代码时，模型要自己把 3 条案例映射到 3 个子系统上 —— 实测它只填了
    # 1 个（固件升级 ← SW-03），另两个子系统**整行缺失**（题9 / 题10 得 0 分）。
    # 而 seed 的 note 里其实**早就写死了**一一对应（BT-SW-01→基带与固件、
    # BT-SW-02→连接与漫游管理、BT-SW-03→固件升级）。把编号解析出来直接给出，
    # 映射就从"模型推断"变成"照单执行"，这正是「补零件」而非介入生成。
    import re as _re
    #: part_no → [case id, …]。**一个编号可对应多条案例**
    #: （实测 BT-ANT-01 同时是 history#1 天线阻抗失配 与 history#2 天线馈点虚焊）——
    #: 必须收全，不能只留最后一条，否则等于悄悄丢证据。
    cases_by_no: dict = {}
    for r in conn.execute(
            "SELECT id, part_no FROM fmea_cases WHERE part_no <> ''"
            " ORDER BY id").fetchall():
        cases_by_no.setdefault(str(r["part_no"]).strip().upper(),
                               []).append(int(r["id"]))
    parts = []
    for r in rows:
        note = r["note"] or ""
        fams = _re.findall(r"BT-([A-Z]+)", note)
        cases = []
        for no in _re.findall(r"BT-[A-Z]+-\d+", note):
            for cid in cases_by_no.get(no.upper(), []):
                ref = "history#%d" % cid
                if ref not in cases:
                    cases.append(ref)
        parts.append({"subsystem": r["subsystem"], "function": r["function"],
                      "condition": r["condition"],
                      "keywords": list(r["keywords"] or []),
                      "analogy_family": fams[0] if fams else "",
                      "analogy_cases": cases,
                      "analogy_hint": note})
    # 历史库里出现过的部件编号族（现有案例的可类比落点）
    hist = conn.execute(
        "SELECT split_part(part_no, '-', 2) AS fam, COUNT(*) AS n"
        " FROM fmea_cases WHERE part_no <> '' GROUP BY 1 ORDER BY 1").fetchall()
    hist_parts = [{"family": h["fam"], "cases": h["n"]} for h in hist]
    out = {"ok": True, "result": {
        "product": product, "parts": parts, "count": len(parts),
        "history_parts": hist_parts,
        "history_total": sum(h["cases"] for h in hist_parts),
        "next_step": "对每个子系统调用 fmea_history_query(family=该部件的 "
                     "analogy_family) 取回同类历史案例，再据此推导 S/O/D。"
                     "若部件带 analogy_cases（如 ['history#1','history#2']），"
                     "那就是与它**一一对应**的历史案例号，直接用这几条做类比即可，"
                     "无需再从整族里挑；**每个子系统都必须至少落一行**，"
                     "不得因为「不确定该对应哪条」而跳过子系统。"}}
    # 产品名匹配上了、但子系统一条都没取到 —— 两种情况**必须区分**：
    #
    #   ① 表本身是空的（种子脚本没跑 / 被清空）→ 这是**工程故障**，要让模型
    #      知道「不要再换产品名重试」，并把修复指令如实上报（响亮失败）。
    #   ② 表有内容、只是 `query` 过滤后为空 → 这是**正常的查无此词**，应当
    #      回一个「可用子系统清单」让模型自己改词，而不是谎报知识库为空。
    #
    # 2026-09-14 修（job#31 实战）：v1 的判据写成 `if not parts:`，把 ② 也误判成
    # ①，于是模型拿到「知识库未初始化，请勿改用其他产品名重试」后**主动放弃**
    # 了全部 13 个子系统（那是 run#29 的直接病根换了张皮）。正确判据是
    # 「该 product 在表里总共有几行」——
    kb_rows = conn.execute(
        "SELECT COUNT(*) c FROM fmea_parts WHERE product=?",
        (product,)).fetchone()["c"]
    if not parts and kb_rows == 0:
        return {"ok": False,
                "error": (
                    f"部件知识库『{product}』没有任何子系统记录（fmea_parts 表为空）。"
                    "这不是产品名错误，而是知识库未初始化 —— 请勿改用其他产品名重试。"
                    "修复：在容器内运行 `python scripts/seed_fmea_parts.py` 载入部件清单。"
                    "若无法修复，请直接把该情况写进交付物并停止调用本动作。"),
                "result": {**out["result"], "knowledge_base_empty": True}}
    if not parts:
        # ② 表有内容、只是 query 没命中：给出可用子系统名，供「拿去就用」。
        names = [r["subsystem"] for r in conn.execute(
            "SELECT subsystem FROM fmea_parts WHERE product=? ORDER BY id",
            (product,)).fetchall()]
        out["result"]["query_miss"] = True
        out["result"]["available_subsystems"] = names
        out["result"]["hint"] = (
            f"产品『{product}』共有 {kb_rows} 个子系统，但 query=「{query}」"
            "一条都没命中。请去掉 query 重查（拿全量清单），或改用下面的子系统名。")
    return out


# ---------------- 查历史 FMEA ----------------

def history_query(conn, part: str = "", keyword: str = "", family: str = "",
                  limit: int = 8) -> dict:
    """检索历史 FMEA 条目（取值优先级链第 1 级）。

    三种查法可单用或组合：

      - `part`：按部件名/编号模糊匹配；
      - `keyword`：按失效模式 / 原因 / 后果的文本匹配；
      - **`family`：按「部件族」检索**（`part_no` 的第 2 段，如 `ANT` / `RF` /
        `PMU` / `CLK` / `SW` / `CON` / `EMC`）—— 这是**同类案例类比的主路径**。

    为什么必须有 `family`（2026-09-12 实测）：目标产品（WiFi 模块）在库里没有
    直接记录时，DFMEA 工程师知道"WiFi 天线 ← ANT 族"（`part_search` 给了提示），
    但按 `part="天线"` 查**必然 0 命中** —— 历史库的 `part` 是「手机蓝牙模块」、
    `part_no` 是英文编号 `BT-ANT-01`，中英文两边都对不上。实测第 4 轮因此
    **历史引用归零**、整表退化成 AI 推断（ai_new 占比 38%），复核门只能记观察项。
    按族查询才是这条链路的正确入口。

    每条返回带 `ref`（形如 `history#12`）—— 供 DFMEA 逐格标注来源时引用，
    使「这一格从哪来」可被复核门逐条追溯。
    """
    part = (part or "").strip()
    keyword = (keyword or "").strip()
    family = (family or "").strip().upper()
    if not part and not keyword and not family:
        return {"ok": False,
                "error": "part / keyword / family 至少提供一个"
                         "（类比推导建议用 family，如 family='ANT'）"}
    clauses, params = [], []
    if part:
        clauses.append("(part LIKE ? OR part_no LIKE ?)")
        params += [f"%{part}%", f"%{part}%"]
    if keyword:
        clauses.append("(failure_mode LIKE ? OR failure_cause LIKE ?"
                       " OR failure_effect LIKE ?)")
        params += [f"%{keyword}%"] * 3
    if family:
        clauses.append("upper(split_part(part_no, '-', 2)) = ?")
        params.append(family)
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


def ap_matrix_slice(conn, severity=None, occurrence=None, limit: int = 400) -> dict:
    """返回 AP 矩阵的**原文切片**，供复核门独立复现查表（design §15.7）。

    为什么需要（2026-09-13 实测）：复核门要核 "AP 是否与表一致"，但它能调的
    `fmea_ap_table` 只有「按 (S,O,D) 查一个值」的形态 —— **没有取回表原文的
    能力**。于是复核员在物理上无法自行复现 AP 推导，只能如实报
    「AP 表取回失败，无法核验」并给 FAIL。这是**接口没给够**，不是判别能力问题：
    判别者需要的是**证据本身**，而不是「再问一次同一个函数」。

    给 `severity` 则只返回该 S 下 O×D 的一整片（10×10=100 格，足以复现一个
    S 档的全部取值）；都不给则返回前 `limit` 格。返回带行数的 `rows` 便于
    复核门按 (S,O,D) 三元组精确比对。
    """
    clauses, params = [], []
    if severity is not None and severity != "":
        try:
            clauses.append("severity=?"); params.append(int(severity))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "severity 必须是整数"}
    if occurrence is not None and occurrence != "":
        try:
            clauses.append("occurrence=?"); params.append(int(occurrence))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "occurrence 必须是整数"}
    try:
        lim = max(1, min(int(limit or 400), 1000))
    except Exception:  # noqa: BLE001
        lim = 400
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT severity, occurrence, detection, ap FROM fmea_ap_matrix "
        + where + " ORDER BY severity, occurrence, detection LIMIT ?",
        params + [lim]).fetchall()
    return {"ok": True, "result": {
        "ref": f"{SRC_TABLE}#ap_matrix", "count": len(rows),
        "rows": [dict(r) for r in rows],
        "note": "AP 值以本表为准；复核时按 (severity, occurrence, detection) "
                "三元组比对，勿凭 S/O/D 自行推算"}}


def _as_int(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except Exception:
        return None


def ap_verify_rows(conn, rows: list) -> dict:
    """**一次核验全部行的 AP 是否与表一致** —— 复核门的专用批量动作。

    为什么必须单开一个动作（2026-09-13 实测，第二轮）：
    复核员要核 17 行的 AP，`fmea_ap_table` 的「按三元组查一个值」每次只回 1 行，
    而 `MAX_ACTION_ROUNDS=8` —— **物理上核不完**。它已经知道自己该用
    `matrix_severity` 取整片，但即便取回整片，仍要自己把 100 格与 17 行逐一对齐，
    在一次 tool-use 循环里做不完，于是如实报「其余 16 条未逐行核验」并给 FAIL。
    这是**接口没给够**：判别者要的是「这 17 行各自对不对」的**结论**，
    而不是「一张 100 格的表，你自己比对」。

    入参 `rows=[{failure_mode?, severity, occurrence, detection, ap}, …]`，
    逐行返回 `{idx, s, o, d, claimed, table, match}`；`match` 取
    True/False/None（None = 表内查无该三元组）。调用者据此一次性判 AP 一致性。

    注意：本动作**只比对、不改数** —— 复核员「不代填」的边界不因此放松。
    """
    out, n_match, n_mismatch, n_missing = [], 0, 0, 0
    for i, raw in enumerate(rows or []):
        if not isinstance(raw, dict):
            out.append({"idx": i, "error": "元素必须是对象"})
            continue
        fm = (raw.get("failure_mode") or "").strip()
        claimed = raw.get("ap")
        r = ap_lookup(conn, raw.get("severity"), raw.get("occurrence"),
                      raw.get("detection"))
        item = {"idx": i, "failure_mode": fm,
                "s": _as_int(raw.get("severity")),
                "o": _as_int(raw.get("occurrence")),
                "d": _as_int(raw.get("detection")),
                "claimed": (str(claimed).strip().upper() if claimed else None)}
        if not r.get("ok"):
            item["table"] = None
            item["match"] = None
            item["note"] = r.get("error")
            n_missing += 1
        else:
            table_ap = r["result"]["ap"]
            item["table"] = table_ap
            item["ref"] = r["result"].get("ref")
            if item["claimed"] is None:
                item["match"] = None
                item["note"] = "该行未填 AP，无法比对"
                n_missing += 1
            elif item["claimed"] == str(table_ap).strip().upper():
                item["match"] = True
                n_match += 1
            else:
                item["match"] = False
                n_mismatch += 1
        out.append(item)
    return {"ok": True, "result": {
        "items": out, "rows": len(out), "matched": n_match,
        "mismatched": n_mismatch, "unverifiable": n_missing,
        "all_match": (n_mismatch == 0 and n_missing == 0 and len(out) > 0),
        "ref": f"{SRC_TABLE}#ap_matrix",
        "note": "AP 以表为准；match=false 的行必须要求 DFMEA 工程师改正，"
                "unverifiable 的行须补齐 S/O/D 整数后再核"}}


# ---------------- 写 DFMEA 行 ----------------


def _src_base(s) -> str:
    s = str(s or "").strip()
    if s.startswith(SRC_EXPERT_PREFIX):
        return "expert"
    return s.split("#", 1)[0]


def write_row(conn, row: dict, run_id=None) -> dict:
    """写一行 DFMEA 记录。来源非法即拒绝；**AP 与 AP 来源都以表为准**。

    **run 内幂等（(part, failure_mode) 唯一）**：同一 run 下同一 (part,
    failure_mode) 已存在则**更新**该行，不新增重复行。为什么必须做：pipeline 里
    「汇总 / 查表 / 写行」常由同一个数字人（DFMEA 工程师）分几个节点承担，每个
    节点都有独立的 tool-use 循环 —— 实测 WiFi 那轮同一批 29 条被写了**两遍**
    （58 行，row_id 34–62 与 63–91），复核门直接判 FAIL（row_id 区间不一致、
    下游无法确定以哪套为准）。这是**数据完整性**问题，放在领域层修，不靠提示词自觉。

    **AP 来源校正**：AP 由 AP 表查出时，来源格必须标 `table#ap` —— 否则会出现
    「值来自表、却标 ai_inferred」的自相矛盾（实测被复核门抓为 P1）。
    """
    row = row or {}
    fm = (row.get("failure_mode") or "").strip()
    if not fm:
        return {"ok": False, "error": "failure_mode 不能为空"}
    # 先校验再取副本 —— `validate_sources` 负责判定「不是对象」并返回可读错误；
    # 若在它之前就 `dict(...)`，模型把 sources 传成 list 时会直接抛 ValueError
    # 冒泡出去（自审 2026-09-12：这个顺序错误让整条 pipeline 在 7 分钟内崩掉）。
    raw_sources = row.get("sources")
    ok, errs = validate_sources(raw_sources)
    if not ok:
        return {"ok": False, "error": "来源标注非法：" + "；".join(errs)}
    sources = dict(raw_sources)

    s, o, d = _as_int(row.get("severity")), _as_int(row.get("occurrence")), \
        _as_int(row.get("detection"))
    ap = (row.get("ap") or "").strip().upper() or None
    ap_corrected = False
    ap_src_fixed = False
    # AP 以表为准（S/O/D 齐备时）。查不到组合不阻断写入 —— 记为修正未生效。
    if s and o and d:
        looked = ap_lookup(conn, s, o, d)
        if looked.get("ok"):
            table_ap = looked["result"]["ap"]
            if ap != table_ap:
                ap_corrected = ap is not None
                ap = table_ap
            # 来源格同步以表为准：未标 / 标成 AI 推断的，一律改判 table#ap
            if ap is not None and _src_base(sources.get("ap")) in (
                    "", SRC_AI_INFERRED, SRC_AI_NEW):
                sources["ap"] = f"{SRC_TABLE}#ap"
                ap_src_fixed = True
        elif ap is None:
            return {"ok": False, "error": looked.get("error", "AP 查表失败")}

    rid = run_id if run_id is not None else current_run_id()
    raw_part = (row.get("part") or "").strip()
    # part 归一化：族代码（`SW`）→ 中文子系统名（`连接与漫游管理`）。
    # 族代码是检索键、中文名才是人读的 part 值；混用会让下游按子系统检索时漏检
    # （2026-09-13 考官卷题 10 实测命中 0 行）。归一确定性、不接受 LLM 再猜。
    part = canonical_part(raw_part, fm)
    part_normalized = bool(raw_part) and part != raw_part
    dup = None
    if rid is not None:
        dup = conn.execute(
            "SELECT id FROM dfmea_rows WHERE run_id=? AND part=?"
            " AND failure_mode=?", (rid, part, fm)).fetchone()
    payload = (part, row.get("function"), fm, row.get("failure_effect"), s,
               row.get("failure_cause"), o, row.get("prevention_control"),
               row.get("detection_control"), d, ap, row.get("action"),
               json.dumps(sources, ensure_ascii=False))
    if dup:
        conn.execute(
            "UPDATE dfmea_rows SET part=?, function=?, failure_mode=?,"
            " failure_effect=?, severity=?, failure_cause=?, occurrence=?,"
            " prevention_control=?, detection_control=?, detection=?, ap=?,"
            " action=?, sources=? WHERE id=?", payload + (dup["id"],))
        row_id, updated = dup["id"], True
    else:
        cur = conn.execute(
            "INSERT INTO dfmea_rows(run_id, part, function, failure_mode,"
            " failure_effect, severity, failure_cause, occurrence,"
            " prevention_control, detection_control, detection, ap, action,"
            " sources, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid,) + payload + (time.time(),))
        row_id, updated = cur.lastrowid, False
    conn.commit()
    ai_new_fields = [f for f, v in sources.items()
                     if str(v).startswith(SRC_AI_NEW)]
    return {"ok": True, "result": {
        "row_id": row_id, "run_id": rid, "ap": ap,
        "updated": updated, "ap_corrected": ap_corrected,
        "ap_src_fixed": ap_src_fixed,
        "part": part, "part_normalized": part_normalized,
        "ai_new_fields": ai_new_fields}}


def write_rows(conn, rows: list, run_id=None) -> dict:
    """**批量**写 DFMEA 行：一次调用写多行。

    为什么需要：一张 DFMEA 表动辄十几行，逐行调用会把单次 tool-use 循环的
    轮数耗尽（实测 2026-09-11：模型连"查准则"都没查完就结束了）。
    逐行仍走同一个 `write_row`（来源闭集校验 + AP 以表为准**不放松**），
    只是把往返次数从 N 降到 1。失败的行走 `errors` 逐条返回，不静默吞掉。
    """
    ok_ids, errs = [], []
    n_upd = 0
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            errs.append({"index": i, "error": "元素必须是对象"})
            continue
        r = write_row(conn, row, run_id)
        if r.get("ok"):
            ok_ids.append(r["result"])
            if r["result"].get("updated"):
                n_upd += 1
        else:
            errs.append({"index": i,
                         "failure_mode": row.get("failure_mode"),
                         "error": r.get("error")})
    return {"ok": True, "result": {
        "written": len(ok_ids), "updated": n_upd, "failed": len(errs),
        "items": ok_ids, "errors": errs}}


# ---------------- 交付物 → 落库（确定性，复核门之前） ----------------
#
# 为什么需要这一段（2026-09-13 架构级缺陷，run#28 实测）：
# 生成的 pipeline 里「写入器」节点（step9-write-pending-approval）排在**复核门
# 之后**（step7-consolidate → step8-review → step9-write）。于是复核门 FAIL 时
# 引擎直接中止下游 —— `dfmea_rows` **一行都没有**，复核门自己也只能对着上游
# 交接物里的 markdown 文本口头核验，核不到就报「取待人工确认清单返回 count=0」。
# 考官卷因此塌到 30/150 = 20.0%（27 道题的表都不存在，无从判分）。
#
# 这是**编排顺序**问题，不是生成质量问题：表其实已经在汇总节点的交付物里写全了
# （run#28 的 step7 原文就有 R1–R17 逐格 S/O/D/AP），只是没落库。修法不是去改
# 生成（那会违反「不介入生成」的铁律），而是**给引擎补一个确定性零件**：
# 任何节点交出的交付物里若含完整 DFMEA 行，引擎在**交物落库的同时**把它们
# 一并写进 `dfmea_rows` —— 于是复核门打开时表就在库里，FAIL 也不再清零分数。

#: DFMEA 行里出现的关键字段（用于判定「这段文本是不是逐格行」）。
_ROW_FIELD_MARKS = ("part", "failure_mode", "severity", "occurrence",
                    "detection", "ap", "action", "function",
                    "failure_effect", "failure_cause",
                    "prevention_control", "detection_control")

#: 单字母列名（表头常写 `S`/`O`/`D`/`AP`）→ 字段名。
#: 为什么需要（2026-09-14 job#32）：`_ROW_FIELD_MARKS` 里是 `severity`，
#: 而表头格是 `S` —— `"severity" in "s"` 为 False，于是 S/O/D 三列**全部映射不到**
#: → 数据行被「S/O/D 必须齐备」的闸门整表丢弃（实测中文表头表解析出 **0 行**）。
_ONE_LETTER_MARKS = {"s": "severity", "o": "occurrence", "d": "detection",
                     "ap": "ap"}

#: 中文表头 → 字段名。**长别名在前**（`建议措施` 必须先于 `措施` 命中，
#: 否则 `措施` 会先占位、而 `建议措施` 那一列反倒落空）。
#:
#: 为什么需要（2026-09-14 job#32）：markdown 表格路径的表头检测原本只认英文
#: 字段名 + 硬编码一句 `"失效模式" in c`，模型写中文表头（`| 子系统 | 功能 |
#: 建议措施 | 失效模式 |`）时**除失效模式外全部识别不出** → `action` 列丢失。
_CN_HEADERS = (
    ("建议措施", "action"), ("改进措施", "action"), ("建议行动", "action"),
    ("措施", "action"), ("对策", "action"), ("建议", "action"),
    ("改善措施", "action"),
    ("失效模式", "failure_mode"), ("潜在失效模式", "failure_mode"),
    ("失效后果", "failure_effect"), ("失效影响", "failure_effect"),
    ("失效原因", "failure_cause"), ("潜在原因", "failure_cause"),
    ("预防控制", "prevention_control"), ("预防措施", "prevention_control"),
    ("探测控制", "detection_control"), ("检测控制", "detection_control"),
    ("严重度", "severity"), ("频度", "occurrence"), ("发生度", "occurrence"),
    ("探测度", "detection"), ("检出度", "detection"),
    ("子系统", "part"), ("部件", "part"), ("零件", "part"),
    ("功能", "function"),
)

#: DFMEA 行内**全部**可能的字段名（含中文别名）—— 值的终止边界靠它判定。
_FIELD_ALIASES = (
    "part", "部件", "子系统",
    "function", "功能",
    "failure_mode", "失效模式",
    "failure_effect", "失效后果",
    "failure_cause", "失效原因",
    "severity", "occurrence", "detection", "ap",
    "prevention_control", "预防控制",
    "detection_control", "探测控制",
    "action", "建议措施", "建议行动", "措施", "建议", "改善措施",
    "S", "O", "D", "AP",
)

#: 字段名正则片段（长名在前，避免 `S` 抢先匹配掉 `severity`）。
_FIELD_ALT = "|".join(re.escape(f) for f in
                      sorted(set(_FIELD_ALIASES), key=len, reverse=True))


def _cell(text: str, *fields) -> str:
    """从一行里抽出某字段的值（确定性，不调 LLM）。`fields` 可给多个别名。

    值的终止边界 = **下一个 `字段=` / `字段:` 出现处**，或行尾。
    不能靠「分隔符 + 字段名」（初版写法）：真实交付物用的是 `；` 紧跟字段名
    （`…失效模式=X；失效后果=Y`），`；` 后无空格，用 `\\s` 要求前置空白会整段漏读
    （2026-09-13 单测实测：R1 的 failure_mode 把整行都吞了）。
    多别名按**给定顺序**尝试（调用方把最具体的名放前面）。

    **兜底形态**：模型偶尔写 `S5 O4 D4 AP=M`（S/O/D 用空格分隔、不写 `=`）。
    实测 2026-09-14 job#33：17 行里有 1 行是这种写法 → 该行的 `action` 与 S/O/D
    全部抠不出（题 21 因此丢 5 分）。故对**短字段**（S/O/D/AP 及中文单字别名）
    再试一次「裸值」正则：字段名后**直接跟数字**（`S5`）或跟 `=`/`:`。
    """
    for field in fields:
        if not field:
            continue
        m = re.search(
            rf"(?:^|[；;、,，\s]){re.escape(field)}\s*[=:：]\s*(.*?)\s*"
            rf"(?=(?:[；;、,，\s])(?:{_FIELD_ALT})\s*[=:：]|$)",
            text, re.M)
        if m:
            v = m.group(1).strip().strip("`*「」【】")
            if v:
                return v
    # 兜底：`S5` / `O4` / `D4` / `AP=M`（字段名后直接跟值，数字或单个大写字母）
    for field in fields:
        if not field or len(field) > 4:
            continue        # 只对短字段兜底，长字段裸匹配误伤率高
        m = re.search(
            rf"(?:^|[；;、,，\s]){re.escape(field)}\s*([0-9]{{1,2}}|="
            rf"[A-Za-z]{{1,4}})\s*(?=[；;、,，\s]|$)",
            text)
        if m:
            v = m.group(1).strip().strip("=").strip()
            if v:
                return v
    return ""


def parse_table_rows(text: str) -> list[dict]:
    """从交付物正文里**确定性**抠出 DFMEA 行（不调 LLM）。

    支持两种形态（实测 run#28 的表是形态 1）：
      形态 1（逐行段落，**主形态**）：
        **R1** part=天线与馈电；功能=…；失效模式=天线阻抗失配；…；
        S=6（history#1；table#severity6）；O=4（…）；D=3（…）；AP=M（…）
      形态 2（markdown 表格）：
        | part | failure_mode | severity | occurrence | detection | ap | …

    判定一律要求 **failure_mode 与 (S,O,D) 同时在场** —— 只要提到「失效模式」
    四个字就当成行会把专家清单误收（专家清单的 kind 也是失效模式清单）。
    抠不到行就返回 []，由调用方决定是否记为「无表」，**绝不臆造行**。
    """
    out: list[dict] = []
    txt = text or ""
    if not txt:
        return out

    # ---- 形态 2：markdown 表格 ----
    # 关键：**列映射要从表头行记下来，再套用到数据行**。初版逐行独立判断列，
    # 数据行的格是数据值（`PA 输出功率不足`），永远匹配不到字段名 → 整表漏读
    # （2026-09-13 单测实测：数据行 idx={} 直接跳过）。
    lines = txt.splitlines()
    hdr_idx: dict | None = None
    for ln in lines:
        s = ln.strip()
        if not (s.startswith("|") and s.count("|") >= 4):
            hdr_idx = None          # 离开表格 → 表头失效
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells if c):
            continue                # 分隔行（|---|---|）
        low = [c.lower() for c in cells]
        # 该行是不是表头？至少两格能对上字段名（或含「失效模式」）
        probe: dict = {}
        for j, c in enumerate(low):
            for f in _ROW_FIELD_MARKS:
                if c == f or (f in c and len(c) <= len(f) + 4):
                    probe.setdefault(f, j)
            # 单字母列名（`S`/`O`/`D`/`AP`）
            if c in _ONE_LETTER_MARKS:
                probe.setdefault(_ONE_LETTER_MARKS[c], j)
            # 中文列名映射（模型常写中文表头；`失效模式` 是判"这行是表头"的锚）
            for alias, f in _CN_HEADERS:
                if alias in c:
                    probe.setdefault(f, j)
        if "failure_mode" in probe and len(probe) >= 3:
            hdr_idx = probe        # 记住这一表的列映射
            continue                # 表头行本身不入结果
        if not hdr_idx:
            continue                # 没表头的裸数据行：无法安全映射，跳过
        r = {f: cells[j] for f, j in hdr_idx.items() if j < len(cells)}
        if not str(r.get("failure_mode") or "").strip():
            continue
        # 只收 S/O/D 齐备的完整行（与形态 1 同口径）
        if not all(str(r.get(k) or "").strip()
                   for k in ("severity", "occurrence", "detection")):
            continue
        out.append(r)

    # ---- 形态 1：逐行 `字段=值` 段落 ----
    # 以行首的编号（`**R1**` / `R1.` / `R1、` / `1)`）为切分点。
    blocks = re.split(r"(?m)^\s*(?=\*{0,2}[Rr]?\d+\*{0,2}\s*[\.、:：)）\s])", txt)
    for b in blocks:
        fm = _cell(b, "failure_mode", "失效模式")
        if not fm:
            continue
        got = {"failure_mode": fm}
        for f, aliases in (("part", ("part", "部件", "子系统")),
                           ("function", ("function", "功能")),
                           ("severity", ("severity", "S")),
                           ("occurrence", ("occurrence", "O")),
                           ("detection", ("detection", "D")),
                           ("ap", ("ap", "AP")),
                           ("failure_effect", ("failure_effect", "失效后果")),
                           ("failure_cause", ("failure_cause", "失效原因")),
                           ("prevention_control",
                            ("prevention_control", "预防控制", "预防措施")),
                           ("detection_control",
                            ("detection_control", "探测控制", "检测控制")),
                           # `action`（建议措施）**必须收**：DFMEA 的落点就是改进
                           # 措施，缺它整表不完整（2026-09-14 job#32 题 21 实测：
                           # 初版别名表里根本没有 action → 形态 1 的表永远抠不出
                           # 措施列 → 落库 `action` 整列 NULL）。
                           ("action",
                            ("action", "建议措施", "措施", "改进措施", "建议行动",
                             "建议", "改善措施", "处理措施")),
                           ):
            v = _cell(b, *aliases)
            if v:
                got[f] = v
        # 只收**同时具备 S/O/D 的完整行**；不完整的留给写行工具逐条处理。
        if not all(got.get(k) for k in ("severity", "occurrence", "detection")):
            continue
        out.append(got)
    return out


def _strip_paren(v) -> str:
    """剥掉取值后面的括号说明：`6（history#1；table#severity6）` → `6`。"""
    return re.split(r"[（(]", str(v or "").strip(), 1)[0].strip()


def _paren_sources(v, owner_field: str = "") -> dict:
    """从 `6（history#1；table#severity6）` 里抽出逐格来源 → {'severity': 'table#severity6'}。

    模型把来源写在括号里（实测 run#28 全表都是这个写法）。这是**可机器解析**的
    信息，白扔掉就又要靠 LLM 重述一遍；确定性抽出来，正好补上 sources 字段。

    两种来源形态**可以并存**（实测 `6（history#1；table#severity6）`）：
      - `table#severity6`：**带字段名**的表引用 → 归属该字段（此处 severity）；
      - `history#1`：**裸来源**，没有字段名 → 归属到**行的身份锚**
        `failure_mode`（"这条行是从 history#1 类比来的"）。
    ⚠️ **同格两个来源不是拼接理由**（2026-09-14 job#32 实测 38 处格式异常）：
    初版遇裸来源 `out.setdefault(owner_field, naked[0])` —— 而 `owner_field` 已被
    `table#severity6` 占用 → setdefault **静默无效**，裸来源丢失；更糟的是模型
    自己写 `sources={"severity": "history#1；table#severity6"}` 时拼接串能通过
    `source_kind_ok`（只校验第一个 `#` 前的前缀）→ 入库成非法值。
    现在统一走 `_merge_src`：**一字段一 token，冲突按「表优先、裸来源归 failure_mode」**。
    """
    m = re.search(r"[（(]([^）)]*)[）)]", str(v or ""))
    if not m:
        return {}
    out: dict = {}
    naked: list = []
    for part in re.split(r"[；;,，]", m.group(1)):
        p = part.strip()
        if not p:
            continue
        # 带字段名的表引用：`table#severity6` → suffix `severity6` 以字段名打头。
        # ⚠️ **不能用 `startswith("table")`**（2026-09-14 job#34 实测 题14 缺 1/13）：
        # 模型会写散文前缀 —— `S=8（history#13 原 9；按 table#severity8 调整：…）`
        # 的第二个 token 是 `按 table#severity8 调整：…`，`startswith("table")` 为
        # False → `_src_base` 得 `"按 table"` 不在闭集 → 该 token **既非表引用也非
        # 裸来源，被静默丢弃** → severity 格没有来源 → 题14（关键格来源齐备）失分。
        # 改为**在 token 内定位 `table#`**，再取 `#` 后的字段名。
        tm = re.search(rf"{SRC_TABLE}\s*#\s*([A-Za-z_][A-Za-z_0-9]*)", p, re.I)
        if tm:
            suffix = tm.group(1).lower()
            hits = [f for f in sorted(SOURCE_FIELDS, key=len, reverse=True)
                    if suffix.startswith(f)]
            if hits:
                # 规范化：`table#severity8`（丢掉散文尾巴），保留 # 后的原始串
                _merge_src(out, hits[0], f"{SRC_TABLE}#{suffix}")
                continue
        base = _src_base(p)
        if base in SOURCE_KINDS or base == "expert":
            naked.append(p)
    # 裸来源统一归到 failure_mode（行的身份锚），**绝不与表引用挤同一格**
    for p in naked:
        _merge_src(out, "failure_mode", p)
    _ = owner_field          # 保留形参以兼容既有调用点（语义已改为身份锚）
    return out


def ingest_table_rows(conn, text: str, run_id=None) -> dict:
    """把交付物里**已经写好的 DFMEA 表**确定性落库（复核门之前调用）。

    与 `write_row` 的关系：逐行走**同一个** `write_row`（来源闭集校验 + AP 以
    表为准 + (part, failure_mode) 幂等**都不放松**）。本函数只负责「把文本里的行
    抠出来、把括号里的来源补上」，不裁决业务。

    返回 `{parsed, written, updated, skipped, errors}`：
      - `parsed`：解析出的行数（0 = 这段交付物里没有可落库的完整行）；
      - `written`/`updated`：新增/更新行数；
      - `skipped`：解析到但 `write_row` 拒收的行（来源非法 / AP 查表失败）；
      - `errors`：每条拒收原因（不静默吞掉）。

    **同一 run 内「最后交付物胜」**（2026-09-14 job#32 实测）：修复轮的交付物是
    对初轮草稿的**修订版**，但 `(part, failure_mode)` 幂等挡不住"同一子系统的失效
    模式被改写了"的情形 —— 实测 2 行 `failure_mode` 被改写（`WiFi 连接状态机死锁`
    加上「（原 history#11 为…跨协议类比）」后缀）→ 判为**新行 INSERT**，于是新旧
    两代并存：初轮 8 行 `action=NULL` 的老行残留，题 21「每行 action 非空」整题失分
    （复核门也独立抓出「疑似重复写入 row_id 273/280、274/281」）。
    修法：落库前**按本段交付物覆盖的子系统清掉旧代**，让新表**取代**旧表；
    未被本段覆盖的子系统**保持不动**（不误删别的节点的产出）。
    """
    rows = parse_table_rows(text)
    ok_ids, errs = [], []
    n_upd, n_superseded = 0, 0
    # ---- 先算出本段交付物涉及哪些子系统，清掉这些子系统的旧代 ----
    rid_pre = run_id if run_id is not None else current_run_id()
    covered: set = set()
    for r in rows:
        p = canonical_part((r.get("part") or "").strip(),
                           r.get("failure_mode") or "")
        if p and p != "待归子系统":
            covered.add(p)
    if rid_pre is not None and covered:
        qs = ",".join("?" for _ in covered)
        cur = conn.execute(
            f"DELETE FROM dfmea_rows WHERE run_id=? AND part IN ({qs})",
            (rid_pre,) + tuple(sorted(covered)))
        n_superseded = cur.rowcount or 0
        conn.commit()
    for i, r in enumerate(rows):
        raw = dict(r)
        src: dict = {}
        for f in SOURCE_FIELDS:
            if f not in raw:
                continue
            v = raw[f]
            # 该格的来源写在括号里：`6（history#1；table#severity6）`。
            # 表引用占本格；裸 `history#1` 归 failure_mode（行的身份锚）。
            src.update(_paren_sources(v, owner_field=f))
            raw[f] = _strip_paren(v)
        # S/O/D/AP 要转成整数/大写；`6` → 6
        for f in ("severity", "occurrence", "detection"):
            if f in raw:
                v = _as_int(raw[f])
                if v:
                    raw[f] = v
        if "ap" in raw:
            raw["ap"] = str(raw["ap"]).strip().upper()[:1] or None
        # part / failure_mode 若也带 `(...)` 来源，一并归位
        for f in ("part", "failure_mode", "failure_effect",
                  "failure_cause", "action"):
            if f in raw:
                got = _paren_sources(raw[f], owner_field=f)
                if got:
                    src.update(got)
                raw[f] = _strip_paren(raw[f])
        # ---- 关键格来源兜底（2026-09-14 job#34 实测 题14 severity 缺 1/13）----
        # S/O/D/AP 是**决定行动优先级的核心值**，绝不允许"有值但无来源"入库 ——
        # 那正是「LLM 提名、代码裁决」的反面（值在表里、依据不可追溯）。
        # 但**也不臆造**：只有当该格**自身括号里确实写了某个来源 token**，
        # 只是没被解析器识别到正确字段时才归位（典型：`按 table#severity8 调整：…`
        # 的散文前缀吞掉了 `table#`，修 `_paren_sources` 后已能识别；
        # 此处再兜一层：若该格有值却无来源，且括号里含可识别的来源 token，
        # 就把它记到本格，而不是丢弃）。
        for f in ("severity", "occurrence", "detection", "ap"):
            if f in raw and raw.get(f) not in (None, "") and f not in src:
                got = _paren_sources(r.get(f) or "", owner_field=f)
                if got:
                    # 优先取与本格字段同名的 token
                    if f in got:
                        src[f] = got[f]
                    else:
                        _merge_src(src, f, next(iter(got.values())))
        # `sources` 是**合同必填**（`validate_sources` 对 None 直接拒收）。
        # 交付物里没写逐格来源的行 → **不臆造**（那会违反来源闭集），
        # 明确跳过分档，由模型的写行工具逐条处理。
        if not src:
            errs.append({"index": i, "failure_mode": raw.get("failure_mode"),
                         "error": "该行没有逐格来源标注（sources 为空），"
                                  "不臆造来源，跳过；请用写行工具补来源后落库"})
            continue
        raw["sources"] = src
        if not raw.get("part"):
            raw["part"] = "待归子系统"
        res = write_row(conn, raw, run_id)
        if res.get("ok"):
            ok_ids.append(res["result"])
            if res["result"].get("updated"):
                n_upd += 1
        else:
            errs.append({"index": i, "failure_mode": raw.get("failure_mode"),
                         "error": res.get("error")})
    return {"ok": True, "result": {
        "parsed": len(rows), "written": len(ok_ids) - n_upd,
        "updated": n_upd, "skipped": len(errs),
        "superseded": n_superseded,
        "items": ok_ids, "errors": errs}}


def pending_confirmation(conn, run_id=None) -> dict:
    """**确定性**生成「待人工确认清单」—— 从已落库行里直接抽，不靠 LLM 记得写。

    为什么必须是代码（design §15.7 实测 2026-09-13）：
    复核门的第三条否决理由是「ai_inferred / ai_new 必须列入待人工确认清单并显式交付，
    当前缺失该清单」。DFMEA 工程师在交接物里**口头声明**了「part/function 为
    ai_inferred（待确认）」，但清单本身没产出 —— 这是**交付物契约**层面的缺件，
    靠提示词「记得附清单」不可靠（LLM 每轮都可能漏）。改为：谁写了行，谁就能
    从 `sources` 里**机械抽出** AI 来源的格，清单永远存在、永远与表一致。

    返回 `{count, items:[{row_id, part, failure_mode, field, source}]}`；
    `source` 是 `ai_inferred` 或 `ai_new`。没有 AI 来源时 `count=0`。
    """
    if run_id is None:
        rows = conn.execute(
            "SELECT id, part, failure_mode, sources FROM dfmea_rows"
            " ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, part, failure_mode, sources FROM dfmea_rows"
            " WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    items = []
    for r in rows:
        try:
            src = r["sources"]
            if isinstance(src, str):
                src = json.loads(src or "{}")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(src, dict):
            continue
        for field, val in src.items():
            base = _src_base(val)
            if base in (SRC_AI_INFERRED, SRC_AI_NEW):
                items.append({"row_id": r["id"], "part": r["part"],
                              "failure_mode": r["failure_mode"],
                              "field": field, "source": base})
    return {"ok": True, "result": {
        "count": len(items), "items": items,
        "consulted_experts": consulted_experts(),
        "note": "AI 推断/新增的格必须由人工逐条确认后才可冻结；"
                "consulted_experts 是本次实际问过的专家名，交付物里每一格"
                "若采信了某专家的结论，来源须标 expert:<该名>"}}


def expert_citations(conn, run_id=None) -> dict:
    """**确定性**核验「问过的专家是否在表里被引用」（design §15.7 / T-M 题 17）。

    为什么要单列一个动作（2026-09-13 实测）：四位部件专家全部通过 ask 边被
    询问，但 DFMEA 工程师把 17 行的每一格来源一律写 `history#N` —— 因为每行
    **确实都有历史证据**，于是专家结论在交付物里**完全不可追溯**。考官卷因此判
    「expert 引用可追溯：0 处」不合格。本函数把「问过谁」与「表里引用了谁」做
    差集，让缺口**可机器检出**，成为工程师自检与复核门核验的共同依据。

    返回 `{consulted, cited, missing, ok}`：
      - `consulted`：本次 run 实际询问过的专家名（`register_consulted_expert` 登记）；
      - `cited`：已落库行的 `sources` 里出现过的 `expert:<名>` 去重清单；
      - `missing`：问过但没有任何一处引用 —— 这些专家白问了，必须补引用；
      - `ok`：`missing` 为空（问过就必须引用）。
    """
    consulted = consulted_experts()
    if run_id is None:
        rows = conn.execute("SELECT sources FROM dfmea_rows ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT sources FROM dfmea_rows WHERE run_id=? ORDER BY id",
            (run_id,)).fetchall()
    cited: list = []
    for r in rows:
        try:
            src = r["sources"]
            if isinstance(src, str):
                src = json.loads(src or "{}")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(src, dict):
            continue
        for val in src.values():
            if not isinstance(val, str):
                continue
            base = _src_base(val)
            if base == "expert":
                nm = val.split(":", 1)[1].split("#", 1)[0].strip()
                if nm and nm not in cited:
                    cited.append(nm)
    missing = [n for n in consulted if n not in cited]
    return {"ok": True, "result": {
        "consulted": consulted, "cited": cited, "missing": missing,
        "ok": not missing,
        "note": "问过的专家必须在表里留下 expert:<名> 来源，否则其把关不可追溯。"
                "missing 非空时：把该专家提供的 S/O/D 或失效模式的来源格改标 "
                "expert:<名>（不要一律写 history）"}}


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
