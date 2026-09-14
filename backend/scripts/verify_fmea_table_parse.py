# -*- coding: utf-8 -*-
"""DFMEA 交付物 → 落库 单测（确定性，无 LLM）。

覆盖 design §15.8 ① 的引擎零件：
  · `parse_table_rows` —— 交付物正文 → 行（两形态，纯正则）
  · `_cell` / `_paren_sources` / `_strip_paren` —— 取值与来源抽取
  · `ingest_table_rows` —— 真库落库（走同一个 write_row，口径不放松）

为什么必须钉住（2026-09-13 run#28 实测）：生成的 pipeline 把写入器节点排在
**复核门之后**，复核门 FAIL 即中止下游 → `dfmea_rows` 一行都没有 → 考官卷
塌到 30/150。引擎改为**在复核门之前**读交付物正文确定性落库。格式一旦解析
错，表就是"不存在"，所以解析器必须有回归测试。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_fmea_table_parse.py
"""
import sys

sys.path.insert(0, "/app/backend")

from app import db, fmea  # noqa: E402

fails: list[str] = []


def chk(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + repr(extra)[:180]) if extra else ""))
    if not cond:
        fails.append(name)


# ---------------- 形态 1：行内 `字段=值`（run#28 的真实格式） ----------------

R1 = ("**R1** part=天线与馈电；功能=2.4GHz 射频信号收发；失效模式=天线阻抗失配；"
      "失效后果=发射功率下降，通信距离不足；失效原因=天线匹配网络的电容电感公差漂移；"
      "S=6（history#1；table#severity6）；O=4（history#1；table#occurrence4）；"
      "D=3（history#1；table#detection3）；AP=M（history#1）；"
      "预防控制=选用 1% 精度匹配元件；探测控制=网络分析仪测 S11 回波损耗；"
      "建议措施=增加匹配网络来料抽检比例")

SAMPLE = """
## 一、DFMEA 草案表

### 1. 天线与馈电

""" + R1 + """

**R2** part=天线与馈电；功能=2.4GHz 射频信号收发；失效模式=天线馈点虚焊；失效后果=间歇性断连，连接不稳定；失效原因=回流焊温度曲线不当导致润湿不良；S=7（history#2；table#severity7）；O=5（history#2；table#occurrence5）；D=2（history#2；table#detection2）；AP=M（history#2）；预防控制=优化焊接温区与钢网开孔；探测控制=在线 AOI 焊点检测；建议措施=增加焊点推力抽检

**R3** part=天线与馈电；功能=天线馈电与接地；失效模式=天线净空区被器件侵占；失效后果=辐射效率下降，方向图畸变；失效原因=PCB 布局后期改版未复核净空区；S=5（history#3；table#severity5）；O=3（history#3；table#occurrence3）；D=4（history#3；table#detection4）；AP=M（history#3）；预防控制=净空区在布局规则中设为禁布区；探测控制=Layout 评审 checklist 复核；建议措施=把净空区检查加入 DRC 规则
"""

rows = fmea.parse_table_rows(SAMPLE)
chk("形态1 解析出 3 行", len(rows) == 3, len(rows))
if len(rows) >= 3:
    chk("R1 failure_mode 正确", rows[0]["failure_mode"] == "天线阻抗失配",
        rows[0].get("failure_mode"))
    chk("R1 part 正确", rows[0]["part"] == "天线与馈电", rows[0].get("part"))
    chk("R1 failure_effect 正确",
        rows[0].get("failure_effect") == "发射功率下降，通信距离不足",
        rows[0].get("failure_effect"))
    chk("R1 failure_cause 正确",
        rows[0].get("failure_cause") == "天线匹配网络的电容电感公差漂移",
        rows[0].get("failure_cause"))
    chk("R1 S/O/D/AP 齐备",
        all(rows[0].get(k) for k in ("severity", "occurrence", "detection", "ap")),
        rows[0])
    chk("R2 failure_mode 正确", rows[1]["failure_mode"] == "天线馈点虚焊",
        rows[1].get("failure_mode"))
    chk("R3 failure_mode 正确",
        rows[2]["failure_mode"] == "天线净空区被器件侵占",
        rows[2].get("failure_mode"))

# ---------------- 形态 2：markdown 表格 ----------------

MD = """
| part | failure_mode | severity | occurrence | detection | ap |
|---|---|---|---|---|---|
| 射频功率放大器 | PA 输出功率不足 | 6 | 4 | 3 | M |
| 射频接收前端 | LNA 增益不足 | 7 | 3 | 2 | M |
"""
mrows = fmea.parse_table_rows(MD)
chk("形态2 解析出 2 行", len(mrows) == 2, len(mrows))
chk("形态2 failure_mode 正确",
    bool(mrows) and mrows[0]["failure_mode"] == "PA 输出功率不足", mrows)

# ---------------- 负例：不得误收 / 不得臆造 ----------------

EXPERT = """
| 部件 | 失效模式 | 常见机理 | 典型量化方向 | 数据来源 |
|---|---|---|---|---|
| 天线 | 阻抗失配（偏离 50Ω） | 匹配网络偏差 | VSWR 恶化 | 本体 |
| 滤波器 | 插损增大 | 需实测 | 插损 dB | 本体 |
"""
chk("专家清单不误收（无 S/O/D 列）", len(fmea.parse_table_rows(EXPERT)) == 0,
    fmea.parse_table_rows(EXPERT))

NARR = ("## 结论\n\n已完成射频无线模块 DFMEA 草案，共 17 行失效模式。\n"
        "所有 S/O/D 均为 1~10 单个整数。\n")
chk("纯叙述文本 0 行", len(fmea.parse_table_rows(NARR)) == 0,
    fmea.parse_table_rows(NARR))

chk("缺 failure_mode 不收",
    len(fmea.parse_table_rows("**R9** part=时钟源；S=4；O=3；D=5；AP=L\n")) == 0)
chk("空输入 0 行", fmea.parse_table_rows("") == [])
chk("None 输入 0 行", fmea.parse_table_rows(None) == [])

# ---------------- _cell / _paren_sources / _strip_paren ----------------

chk("_cell 取值不含后续字段",
    fmea._cell(R1, "failure_mode", "失效模式") == "天线阻抗失配",
    fmea._cell(R1, "failure_mode", "失效模式"))
chk("_cell part 不含功能", fmea._cell(R1, "part") == "天线与馈电",
    fmea._cell(R1, "part"))
chk("_cell AP 值", fmea._cell(R1, "ap", "AP") == "M（history#1）",
    fmea._cell(R1, "ap", "AP"))
chk("_cell 建议措施到尾",
    fmea._cell(R1, "action", "建议措施") == "增加匹配网络来料抽检比例",
    fmea._cell(R1, "action", "建议措施"))
chk("_strip_paren 剥括号", fmea._strip_paren("6（history#1；table#severity6）") == "6",
    fmea._strip_paren("6（history#1；table#severity6）"))
chk("_paren_sources 具体表引用占该格",
    fmea._paren_sources("6（history#1；table#severity6）",
                        owner_field="severity").get("severity") == "table#severity6",
    fmea._paren_sources("6（history#1；table#severity6）", owner_field="severity"))
chk("_paren_sources 裸来源归 failure_mode（身份锚）",
    fmea._paren_sources("6（history#1；table#severity6）",
                        owner_field="severity").get("failure_mode") == "history#1",
    fmea._paren_sources("6（history#1；table#severity6）", owner_field="severity"))
chk("_paren_sources 一字段一 token（无拼接串）",
    all(_SEP not in str(v)
        for v in fmea._paren_sources("6（history#1；table#severity6）",
                                     owner_field="severity").values()
        for _SEP in ("；", ";", ",")),
    fmea._paren_sources("6（history#1；table#severity6）", owner_field="severity"))
chk("_paren_sources 纯裸来源 → failure_mode",
    fmea._paren_sources("M（history#1）").get("failure_mode") == "history#1",
    fmea._paren_sources("M（history#1）"))
chk("_paren_sources 表引用仍归自己的格",
    fmea._paren_sources("M（table#ap）", owner_field="ap").get("ap") == "table#ap",
    fmea._paren_sources("M（table#ap）", owner_field="ap"))
chk("_paren_sources 无来源 → 空字典",
    fmea._paren_sources("M") == {}, fmea._paren_sources("M"))

# ---- 来源 token 校验：拼接串必须被拦（2026-09-14 job#32 题 16，38 处异常） ----

chk("source_kind_ok 纯 history#1 合法", fmea.source_kind_ok("history#1"))
chk("source_kind_ok table#severity6 合法",
    fmea.source_kind_ok("table#severity6"))
chk("source_kind_ok table#ap 合法", fmea.source_kind_ok("table#ap"))
chk("source_kind_ok expert:xxx 合法", fmea.source_kind_ok("expert:射频硬件专家"))
chk("source_kind_ok ai_inferred 合法", fmea.source_kind_ok("ai_inferred"))
chk("source_kind_ok 全角分号拼接串非法",
    not fmea.source_kind_ok("history#1；table#severity6"))
chk("source_kind_ok 半角分号拼接串非法",
    not fmea.source_kind_ok("history#1;table#severity6"))
chk("source_kind_ok 逗号拼接串非法",
    not fmea.source_kind_ok("history#1,table#severity6"))
chk("source_kind_ok 空后缀 history# 非法",
    not fmea.source_kind_ok("history#"))
chk("validate_sources 拒收拼接串",
    not fmea.validate_sources({"severity": "history#1；table#severity6"})[0])
chk("validate_sources 接受逐格单来源",
    fmea.validate_sources({"severity": "table#severity6",
                           "failure_mode": "history#1"})[0])

# ---- `action` 别名（2026-09-14 job#32 题 21：`action` 整列 NULL） ----

chk("action 在 _FIELD_ALIASES 里", "action" in fmea._FIELD_ALIASES,
    fmea._FIELD_ALIASES[-6:])
chk("「建议措施」在 _FIELD_ALIASES 里", "建议措施" in fmea._FIELD_ALIASES,
    fmea._FIELD_ALIASES[-6:])
chk("action 在 _ROW_FIELD_MARKS 里", "action" in fmea._ROW_FIELD_MARKS,
    fmea._ROW_FIELD_MARKS)
_CN = dict(fmea._CN_HEADERS)
chk("中文表头「建议措施」→ action", _CN.get("建议措施") == "action", _CN.get("建议措施"))
chk("中文表头「措施」→ action", _CN.get("措施") == "action", _CN.get("措施"))
chk("_CN_HEADERS 长别名在前（建议措施 先于 措施）",
    [a for a, f in fmea._CN_HEADERS if f == "action"][0] == "建议措施",
    [a for a, f in fmea._CN_HEADERS if f == "action"])
chk("中文表头「失效模式」→ failure_mode",
    _CN.get("失效模式") == "failure_mode", _CN.get("失效模式"))

# ---- 形态 2 中文表头（2026-09-14：原实现只认英文 + 硬编码"失效模式"） ----

MD_CN = """
| 子系统 | 功能 | 建议措施 | 失效模式 | S | O | D | AP |
|---|---|---|---|---|---|---|---|
| 射频功率放大器 | 信号放大 | 增加偏置电压在线监测点 | PA 输出功率不足 | 6 | 3 | 3 | M |
| 射频接收前端 | 信号接收 | 增加 LNA 供电轨 LC 滤波 | LNA 增益不足 | 6 | 4 | 4 | M |
"""
_cnrows = fmea.parse_table_rows(MD_CN)
chk("中文表头形态2 解析出 2 行", len(_cnrows) == 2, len(_cnrows))
if _cnrows:
    chk("中文表头 action 被识别",
        _cnrows[0].get("action") == "增加偏置电压在线监测点", _cnrows[0])
    chk("中文表头 failure_mode 被识别",
        _cnrows[0].get("failure_mode") == "PA 输出功率不足", _cnrows[0])
    chk("中文表头 part 被识别",
        _cnrows[0].get("part") == "射频功率放大器", _cnrows[0])
    chk("中文表头 S/O/D 单字母列被识别",
        all(str(_cnrows[0].get(k) or "").strip()
            for k in ("severity", "occurrence", "detection")), _cnrows[0])

chk("_ONE_LETTER_MARKS 覆盖 S/O/D/AP",
    fmea._ONE_LETTER_MARKS.get("s") == "severity"
    and fmea._ONE_LETTER_MARKS.get("o") == "occurrence"
    and fmea._ONE_LETTER_MARKS.get("d") == "detection"
    and fmea._ONE_LETTER_MARKS.get("ap") == "ap", fmea._ONE_LETTER_MARKS)

# ---- 形态 1 的「裸值」兜底：`S5 O4 D4 AP=M`（不写 `=`） ----
# 2026-09-14 job#33 实测：17 行里有 1 行是这种写法 → 该行 action 与 S/O/D
# 全部抠不出（题 21 丢 5 分）。
BARE = ("**R1** part=模块互连；失效模式=馈线走线阻抗不连续（history#15）；"
        "失效后果=回波损耗增大；失效原因=走线宽度在过孔处突变；"
        "S5 O4 D4 AP=M；建议：阻抗连续性纳入 Layout 复核")
_b = fmea.parse_table_rows(BARE)
chk("裸值形态 S/O/D 能解析出 1 行", len(_b) == 1, _b)
if _b:
    chk("裸值 S → severity=5", str(_b[0].get("severity")) == "5", _b[0])
    chk("裸值 O → occurrence=4", str(_b[0].get("occurrence")) == "4", _b[0])
    chk("裸值 D → detection=4", str(_b[0].get("detection")) == "4", _b[0])
    chk("裸值 AP=M → ap=M", str(_b[0].get("ap")) == "M", _b[0])
    chk("「建议：」被识别为 action",
        _b[0].get("action") == "阻抗连续性纳入 Layout 复核", _b[0])
chk("_cell 兜底 S5 → 5", fmea._cell("S5 O4 D4", "severity", "S") == "5",
    fmea._cell("S5 O4 D4", "severity", "S"))
chk("_cell 正常 S=6 优先于兜底",
    fmea._cell("S=6；O=4", "severity", "S") == "6",
    fmea._cell("S=6；O=4", "severity", "S"))
chk("「建议」在 _FIELD_ALIASES 里", "建议" in fmea._FIELD_ALIASES,
    fmea._FIELD_ALIASES[-8:])

chk("_FIELD_ALT 长名在前",
    fmea._FIELD_ALT.index("severity") < fmea._FIELD_ALT.index("S"),
    fmea._FIELD_ALT[:60])

# ---- `_cell` 的 `re.M`：末字段在「块后还有其它小节」时仍能抠出 ----
# 2026-09-14 job#34 实测 题21（每行 action 非空）失分：汇总节点的 R13 行是
# 最后一个字段行，其后紧跟 `\n\n### 缺失与待人工确认项…`。`_cell` 原用 `$`
# **且未开 re.M** → `$` 只匹配整串末尾 → 末字段 `建议措施=…` 永远匹配不到
# → action=NULL。修法：`re.M`（`$` 匹配每行行尾）。
_MR = ("**R13**：part=模块互连（parts#模块互连）；功能=模块与主板的电气互连与馈线连接（parts#模块互连）；"
       "失效模式=BGA 焊球开裂（history#14）；失效后果=间歇性功能失效（history#14）；失效原因=热循环应力集中（history#14）；"
       "S=8（history#14；table#severity8）；O=3（history#14）；D=4（history#14）；AP=M（history#14）；"
       "预防控制=PCB 布局减小热失配应力（history#14）；探测控制=X-Ray 焊点检测（history#14）；"
       "建议措施=增加温度循环可靠性试验（history#14）\n\n### 缺失与待人工确认项（因工具轮次用尽）\n")
_MR_ACT = fmea._cell(_MR, "action", "建议措施", "措施", "改进措施", "建议行动", "建议", "改善措施", "处理措施")
chk("末字段 action 在块后有小节时仍抠出",
    _MR_ACT == "增加温度循环可靠性试验（history#14）", _MR_ACT)
chk("同块 S 前字段不受影响",
    fmea._cell(_MR, "severity", "S") == "8（history#14；table#severity8）",
    fmea._cell(_MR, "severity", "S"))
# 多行形态（每行一个字段）也必须能跨行抠
_ML = ("part=P\n失效模式=FM（history#2）\nS=7（history#2）\nO=5（history#2）\n"
       "D=2（history#2）\nAP=M（history#2）\n建议措施=ACT（history#2）\n\n### 备注\n尾巴")
chk("多行块 action 跨行抠出",
    fmea._cell(_ML, "action", "建议措施", "措施") == "ACT（history#2）",
    fmea._cell(_ML, "action", "建议措施", "措施"))
chk("多行块 failure_mode 跨行抠出",
    fmea._cell(_ML, "failure_mode", "失效模式") == "FM（history#2）",
    fmea._cell(_ML, "failure_mode", "失效模式"))

# ---- `_paren_sources`：token 里带散文前缀的 `table#…` 必须归位 ----
# 2026-09-14 job#34 实测 题14（关键格来源齐备）severity 缺 1/13：
# R11 写 `S=8（history#13 原 9；按 table#severity8 调整：丧失主要功能…）`，
# 第二个 token 是 `按 table#severity8 调整：…` —— 原实现用
# `p.lower().startswith("table")` 为 False → `_src_base` 得 `"按 table"` 不在
# 闭集 → 该 token **既非表引用也非裸来源，被静默丢弃** → severity 格无来源。
# 修法：在 token 内正则定位 `table#<字段名>`，不要求 token 以 table 开头。
_PS = fmea._paren_sources(
    "8（history#13 原 9；按 table#severity8 调整：丧失主要功能，产品无法使用，不涉及安全/法规）",
    "severity")
chk("散文前缀 table# 仍归位到 severity", _PS.get("severity") == "table#severity8", _PS)
chk("归位后的 token 通过来源闭集校验",
    fmea.source_kind_ok(_PS.get("severity") or "") is True, _PS)
chk("散文前缀形态下裸 history 仍归 failure_mode",
    _PS.get("failure_mode") == "history#13 原 9", _PS)
_PS2 = fmea._paren_sources("M（table#apM；history#3）", "ap")
chk("ap 的 table#apM 仍识别", _PS2.get("ap", "").lower() == "table#apm", _PS2)
chk("标准 table#severity6 不受影响",
    fmea._paren_sources("6（history#1；table#severity6）", "severity").get("severity")
    == "table#severity6")

# ---------------- ingest_table_rows：真库落库 ----------------

ING = """
**R1** part=SW；功能=A；失效模式=漫游认证失败导致重连；失效后果=B；失效原因=C；S=6（history#1；table#severity6）；O=4（history#1；table#occurrence4）；D=3（history#1；table#detection3）；AP=M（history#1）；预防控制=X；探测控制=Y；建议措施=Z

**R2** part=射频天线；功能=天线馈电与接地；失效模式=天线阻抗失配；失效后果=B；失效原因=C；S=5（history#2；table#severity5）；O=3（history#2；table#occurrence3）；D=4（history#2；table#detection4）；AP=M（history#2）；预防控制=X；探测控制=Y；建议措施=Z

| part | failure_mode | severity | occurrence | detection | ap |
|---|---|---|---|---|---|
| 参考时钟 | 主晶振频偏 | 6 | 4 | 3 | M |
"""

TEST_RUN = 999901
conn = db.get_conn()
conn.execute("DELETE FROM dfmea_rows WHERE run_id=?", (TEST_RUN,))
conn.commit()

res = fmea.ingest_table_rows(conn, ING, run_id=TEST_RUN)["result"]
stored = fmea.list_rows(conn, run_id=TEST_RUN)
by_fm = {r["failure_mode"]: r for r in stored}

chk("解析 3 行", res["parsed"] == 3, res["parsed"])
chk("落库 2 行（第 3 行无来源被明确跳过）", len(stored) == 2, len(stored))
chk("跳过有明确原因（不臆造来源）",
    res["skipped"] == 1 and "不臆造来源" in res["errors"][0]["error"], res["errors"])
chk("part 归一 SW→连接与漫游管理",
    by_fm.get("漫游认证失败导致重连", {}).get("part") == "连接与漫游管理",
    [(r["failure_mode"], r["part"]) for r in stored])
chk("part 保持中文（射频天线）",
    by_fm.get("天线阻抗失配", {}).get("part") == "射频天线",
    by_fm.get("天线阻抗失配", {}).get("part"))
chk("markdown 无来源行未落库", "主晶振频偏" not in by_fm, list(by_fm))
chk("S/O/D 为整数",
    by_fm.get("天线阻抗失配", {}).get("severity") == 5
    and by_fm.get("天线阻抗失配", {}).get("detection") == 4,
    (by_fm.get("天线阻抗失配", {}).get("severity"),
     by_fm.get("天线阻抗失配", {}).get("detection")))
chk("AP 以表为准（M）", by_fm.get("天线阻抗失配", {}).get("ap") == "M",
    by_fm.get("天线阻抗失配", {}).get("ap"))
chk("sources 抽到 table#severity6",
    by_fm.get("漫游认证失败导致重连", {}).get("sources", {}).get("severity")
    == "table#severity6",
    by_fm.get("漫游认证失败导致重连", {}).get("sources"))

r2 = fmea.ingest_table_rows(conn, ING, run_id=TEST_RUN)["result"]
# 二次写入的**不变式是"行数不变、内容一致"**，而不是"走 UPDATE 分支"。
# 自「最后交付物胜」引入后，落库前会按本段覆盖的子系统清旧代（DELETE + INSERT），
# 所以 `updated` 可能为 0 —— 但**结果与幂等完全等价**（2 行、无重复、内容一致）。
# 断言结果，不断言实现手法（否则测试会锁死一种可替换的内部机制）。
chk("幂等：二次写入后仍 2 行（无重复）",
    len(fmea.list_rows(conn, run_id=TEST_RUN)) == 2,
    len(fmea.list_rows(conn, run_id=TEST_RUN)))
chk("幂等：二次写入 parsed 一致", r2["parsed"] == 3, r2["parsed"])
chk("幂等：二次写入不产生跳过差异", r2["skipped"] == 1, r2["skipped"])
chk("幂等：行内容与首次一致",
    sorted((r["part"], r["failure_mode"]) for r in stored)
    == sorted((r["part"], r["failure_mode"])
              for r in fmea.list_rows(conn, run_id=TEST_RUN)),
    [r["failure_mode"] for r in fmea.list_rows(conn, run_id=TEST_RUN)])

pc = fmea.pending_confirmation(conn, run_id=TEST_RUN)
chk("待确认清单 0 项（来源均 history/table）", pc["result"]["count"] == 0,
    pc["result"]["count"])

# ---- 「最后交付物胜」：修复轮重写同一子系统时旧代必须被取代 ----
# 2026-09-14 job#32 题 21 实测：初轮 13 行 `action=NULL` 的老行与修复轮的新行
# **并存**（2 行因 failure_mode 被改写而不匹配幂等键 → INSERT）→ 考卷读到混合
# 两代 → `action` 非空判定失败。
_SUP_A = ("**R1** part=时钟源；失效模式=主晶振频偏（history#9）；"
          "S=8（history#9；table#severity8）；O=4（history#9；table#occurrence4）；"
          "D=3（history#9；table#detection3）；AP=M（table#ap）")
_SUP_B = ("**R1** part=时钟源；措施=负阻裕度纳入设计评审项；"
          "失效模式=主晶振频偏（history#9）；"
          "S=8（history#9；table#severity8）；O=4（history#9；table#occurrence4）；"
          "D=3（history#9；table#detection3）；AP=M（table#ap）")
ra = fmea.ingest_table_rows(conn, _SUP_A, run_id=TEST_RUN)["result"]
_rows_a = [r for r in fmea.list_rows(conn, run_id=TEST_RUN)
           if r["failure_mode"] == "主晶振频偏"]
chk("初轮：1 行且 action 为空",
    len(_rows_a) == 1 and not _rows_a[0].get("action"),
    [(r["part"], r.get("action")) for r in _rows_a])
rb = fmea.ingest_table_rows(conn, _SUP_B, run_id=TEST_RUN)["result"]
_rows_b = [r for r in fmea.list_rows(conn, run_id=TEST_RUN)
           if r["failure_mode"] == "主晶振频偏"]
chk("修复轮：旧代被取代（superseded ≥ 1）", rb.get("superseded", 0) >= 1,
    rb.get("superseded"))
chk("修复轮：不产生重复行（仍 1 行）", len(_rows_b) == 1, len(_rows_b))
chk("修复轮：action 已补上", bool(_rows_b and _rows_b[0].get("action")),
    _rows_b[0].get("action") if _rows_b else None)
chk("未覆盖的子系统不被误删",
    len([r for r in fmea.list_rows(conn, run_id=TEST_RUN)
         if r["failure_mode"] in ("天线阻抗失配", "漫游认证失败导致重连")]) == 2,
    [r["failure_mode"] for r in fmea.list_rows(conn, run_id=TEST_RUN)])

# 清理测试数据
conn.execute("DELETE FROM dfmea_rows WHERE run_id=?", (TEST_RUN,))
conn.commit()

print()
print("--- part_search 夹具自检（run#29 静默故障回归）---")

# run#29 实测：fmea_parts 为空时 part_search 返回 `parts: [], count: 0`，
# 与「产品名没匹配上」**无法区分** —— 工程师据此反复重试同一个动作，
# 8 轮耗尽后交付物是 215 字的裸 <tool_calls> 块，整表为空（30/150）。
# 现在必须把「知识库空了」变成响亮的 ok=False + 修复指令。
_live = conn.execute("SELECT COUNT(*) c FROM fmea_parts").fetchone()["c"]
if _live:
    res = fmea.part_search(conn, "WiFi 模块")
    chk("part_search 有数据时 ok=True / parts 非空",
        res["ok"] and res["result"]["count"] > 0,
        (res["ok"], res["result"]["count"]))
    fams = {p["analogy_family"] for p in res["result"]["parts"]}
    chk("每个部件带 analogy_family（可直接喂 history_query）",
        all(p["analogy_family"] for p in res["result"]["parts"]), sorted(fams))
    # analogy_cases：把 note 里的 `BT-XXX-NN` 解析成**具体 case id**。
    # 为什么必须（2026-09-14 job#31 实测）：`family=SW` 在历史库有 3 条案例、
    # WiFi 侧也有 3 个 SW 子系统，只给族代码模型就要自己映射 —— 实测只填了
    # 1 个（固件升级），另两个子系统整行缺失（题9/题10 各丢 5 分）。
    _parts = res["result"]["parts"]
    chk("每个部件带 analogy_cases（list）",
        all(isinstance(p.get("analogy_cases"), list) for p in _parts))
    chk("每个部件的 analogy_cases 都非空（逐一落到具体案例号）",
        all(p.get("analogy_cases") for p in _parts),
        [p["subsystem"] for p in _parts if not p.get("analogy_cases")])
    chk("analogy_cases 格式为 history#<数字>",
        all(r.startswith("history#") and r.split("#", 1)[1].isdigit()
            for p in _parts for r in p["analogy_cases"]),
        sorted({r for p in _parts for r in p["analogy_cases"]})[:6])
    # 同一族的多个子系统必须**各对各的**案例号（不能都指向同一条）
    _sw = [p for p in _parts if p["analogy_family"] == "SW"]
    _sw_ids = [tuple(p["analogy_cases"]) for p in _sw]
    chk("SW 族 3 个子系统各自对应不同的历史案例",
        len(_sw) == 3 and len(set(_sw_ids)) == 3,
        [_sw_ids, [p["subsystem"] for p in _sw]])
    # 一对多：一个 part_no 可对应多条案例，必须收全（BT-ANT-01 = #1 + #2）
    _ant = [p for p in _parts if p["subsystem"] == "射频天线"]
    chk("同一 part_no 的多条案例收全（BT-ANT-01 -> history#1+#2）",
        bool(_ant) and _ant[0]["analogy_cases"] == ["history#1", "history#2"],
        _ant[0]["analogy_cases"] if _ant else None)
    # 天线馈电与接地必须给 ANT 族案例（不是 EMC）—— 题23 的根因
    _feed = [p for p in _parts if p["subsystem"] == "天线馈电与接地"]
    chk("天线馈电与接地 -> ANT 族案例（不得串到 EMC）",
        bool(_feed) and _feed[0]["analogy_family"] == "ANT"
        and _feed[0]["analogy_cases"] == ["history#3"],
        (_feed[0]["analogy_family"], _feed[0]["analogy_cases"]) if _feed else None)
    # 产品名模糊匹配：任务里的产品名与库内名不必字面相等
    res2 = fmea.part_search(conn, "射频无线模块（蓝牙/WiFi）")
    chk("产品名模糊匹配（括号/斜杠夹词也能命中）",
        res2["ok"] and res2["result"]["count"] > 0,
        (res2["ok"], res2["result"]["count"]))
else:
    res = fmea.part_search(conn, "WiFi 模块")
    chk("空知识库时 ok=False（不是静默空表）", res["ok"] is False, res.get("ok"))
    chk("空知识库时带 knowledge_base_empty 标记",
        (res.get("result") or {}).get("knowledge_base_empty") is True)
    chk("空知识库时错误信息含修复指令 seed_fmea_parts",
        "seed_fmea_parts" in (res.get("error") or ""))

# 未知产品名：应给出可用产品清单 + hint（引导改词，而不是空表）
res3 = fmea.part_search(conn, "完全不存在的产品 ZZZ")
chk("未知产品名回传 products 清单与 hint",
        res3["ok"] and res3["result"]["count"] == 0
        and "hint" in res3["result"],
        (res3["ok"], res3["result"].get("count"), bool(res3["result"].get("hint"))))

# history_query 按族检索（类比推导主路径）
hq = fmea.history_query(conn, family="ANT")
chk("history_query(family=ANT) 命中 ≥1 且带可引用 ref",
        hq["ok"] and hq["result"]["hits"] >= 1
        and all(i.get("ref", "").startswith("history#") for i in hq["result"]["items"]),
        hq["result"]["hits"])
hq0 = fmea.history_query(conn)
chk("history_query 三参数全空 -> 明确报错（引导用 family）",
        hq0["ok"] is False and "family" in (hq0.get("error") or ""))

print()
print("--- part_search 判据区分（2026-09-14 job#31 回归）---")

# job#31 实战：v1 的判据写成 `if not parts:`，把「query 没命中」误判成
# 「知识库未初始化」。模型收到「请勿改用其他产品名重试」的响亮错误后**主动
# 放弃了全部子系统**（run#29 的病根换了张皮，最终整条链降级到 7 个历史族、
# 丢掉 13 个子系统）。判据必须是「该 product 在表里总共几行」。
if _live:
    # ① 表有货 + query 不命中 -> 正常 query_miss，不得报 ok=False
    miss = fmea.part_search(conn, "WiFi 模块", query="完全不存在的词ZZZ")
    chk("表有货但 query 不命中 -> ok=True（不得谎报知识库为空）",
        miss["ok"] is True, miss.get("ok"))
    chk("表有货但 query 不命中 -> 带 query_miss 标记",
        (miss.get("result") or {}).get("query_miss") is True)
    chk("表有货但 query 不命中 -> 回可用子系统清单（拿去就用）",
        len((miss.get("result") or {}).get("available_subsystems") or []) > 0,
        len((miss.get("result") or {}).get("available_subsystems") or []))
    chk("表有货但 query 不命中 -> 不得带 knowledge_base_empty",
        (miss.get("result") or {}).get("knowledge_base_empty") is not True)
    # ② 不传 query -> 全量 13 条
    full = fmea.part_search(conn, "WiFi 模块")
    chk("不传 query -> 返回全量子系统（13 条）",
        full["ok"] and full["result"]["count"] == 13, full["result"]["count"])
    # ③ 产品名真不存在（表里该 product 0 行）-> 才允许响亮报错
    #    （走的是 `if not matched` 分支，回 products 清单，不是 kb_empty）
    bogus = fmea.part_search(conn, "完全不存在产品ZZZ")
    chk("产品名查无 -> 回 products 清单引导改词（不报 kb_empty）",
        (bogus.get("result") or {}).get("knowledge_base_empty") is not True
        and len((bogus.get("result") or {}).get("products") or []) > 0,
        len((bogus.get("result") or {}).get("products") or []))

print()
print("RESULT:", "OK" if not fails else "FAILED(%d): %s" % (len(fails), fails))
sys.exit(0 if not fails else 1)
