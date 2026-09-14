# -*- coding: utf-8 -*-
"""数字人模板（design §16）：把「一次性 seed 脚本」沉淀为**可复用的六元组蓝图**。

模板 = 身份骨架 + 锚点 + 本体条目 + 动作清单 + **填空槽位**。
用户选模板、填槽位即可生成一个完整数字人（此前只能得到"身份 + 可选动作提名"，
本体与锚点要手工补 —— 这正是 DFMEA 那批数字人当时只能靠硬编码脚本落地的原因）。

设计边界（与平台一贯原则一致）：
  - 模板是**数据**（可自定义、可停用），不是代码分支；
  - 渲染（槽位替换 + 必填校验）是**确定性的**，**不经过 LLM**；
  - 动作只存 `builtin_name`，`input_schema` 从 `actions.BUILTIN_ACTIONS` 取
    （单一事实源 —— 早前 seed 脚本逐个重定义 schema，实际并不生效，已消除该重复）。

槽位语法：蓝图字符串里的 `{{key}}` 在渲染时被槽位值替换（递归处理 dict/list/str）。
"""
import json
import re
import time

from . import actions, db, trainer

SLOT_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# ---------------- 内置模板 ----------------
# 可改（label/description/槽位/蓝图），不可删。code 为英文小写下划线。

BUILTIN_TEMPLATES: list[dict] = [
    # ---------- 1. DFMEA 工程师（design §15） ----------
    {
        "code": "dfmea_engineer",
        "label": "DFMEA 工程师",
        "description": "承载 DFMEA 取值优先级链与逐格来源标注方法论的数字人",
        "category": "general",
        "slots": [
            {"key": "name", "label": "数字人名", "required": True,
             "default": "DFMEA 工程师", "placeholder": "如：手机 DFMEA 工程师"},
            {"key": "domain", "label": "应用领域", "required": True, "default": "产品",
             "placeholder": "如：手机蓝牙模块", "hint": "会写进使命与定位描述"},
        ],
        "blueprint": {
            "mission": "把{{domain}}的部件与需求转化为可追溯的 DFMEA 表，"
                       "逐格按证据取值并标注来源；无证据时明确标为 AI 生成待确认",
            "description": "平台级通用数字人（DFMEA 方法论）：不臆造数值 —— 每一格都按"
                           "「历史 FMEA 库 → AP/S-O-D 准则表 → 询问部件专家 → AI 推断」"
                           "的优先级取值，并把来源逐格标注，供复核与人工确认",
            "prompt": "你是 {{domain}} 的 DFMEA 工程师。宁可标「AI 生成·待人工确认」，"
                      "也不许编造数值。每一格都必须先查证据再写值，并写明来源。"
                      "回答先给结论再给依据。",
            "keywords": ["失效模式", "失效后果", "严重度", "频度", "探测度",
                         "行动优先级", "AP", "S-O-D", "取值优先级", "来源标注"],
            "anchors": [["取值优先级链", "规则"], ["来源标注规则", "规则"],
                        ["不得越级代填", "规则"], ["AI 生成待确认", "规则"]],
            "ontology": [
                ["概念", "失效模式",
                 "部件未达到设计意图功能时的表现形式，回答「什么坏了」；描述应具体到物理现象"],
                ["概念", "失效后果",
                 "失效模式对上一层功能或最终用户的影响，回答「坏了会怎样」；严重度只由后果决定"],
                ["概念", "严重度 S",
                 "失效后果的严重程度 1~10；**只由后果决定，与原因无关**；必须对照 S 准则表取值"],
                ["概念", "失效原因",
                 "导致失效模式的根本原因，回答「为什么会坏」；应指向可采取预防控制的机理"],
                ["概念", "频度 O",
                 "失效原因发生的可能性 1~10；取决于预防控制的有效性与历史发生数据，须对照 O 准则表取值"],
                ["概念", "现有控制",
                 "现阶段的预防控制（阻止原因发生，作用于 O）与探测控制（发现失效，作用于 D）"],
                ["概念", "探测度 D",
                 "现有探测控制在失效流出前发现它的能力 1~10；探测能力越强分值越低，须对照 D 准则表取值"],
                ["概念", "行动优先级 AP",
                 "由 (S, O, D) 三元组查 AP 矩阵得到的 High / Medium / Low；决定改进措施的紧迫度"],
                ["规则", "部件清单先行",
                 "接到分析需求后**第一步**是搜索目标产品的部件清单（用「搜索部件清单」动作），"
                 "逐个部件分析；不得跳过部件清单直接编造失效模式"],
                ["规则", "同类案例类比",
                 "目标产品在历史库若没有直接记录，按三步做类比：① 从「搜索部件清单」结果里"
                 "取每个子系统的 `analogy_family`；② 用它调"
                 "`查询历史 FMEA(family=...)` 取回**同族全部历史案例**；"
                 "③ 据其失效模式与 S/O/D 做类比推导，来源标 `history#<编号>`。"
                 "**若部件项自带 `analogy_cases`（形如 `[\"history#11\",\"history#12\"]`），"
                 "那就是与该子系统一一对应的历史案例号 —— 直接拿这几条做类比，不必再从整族里挑**。"
                 "族代码只告诉你去哪一族，`analogy_cases` 才是\"这个子系统对应这几条\"的确定答案。"
                 "**不要拿中文部件名当 part 去查** —— 历史库的 part 是原产品名、"
                 "part_no 是英文编号，中文名必然 0 命中（实测踩过）"],
                ["规则", "部件覆盖完整性",
                 "**逐个子系统覆盖**：搜索到的部件清单里**每个子系统都必须至少产出 1 条"
                 "失效模式**；上游专家未覆盖、或明确声明知识缺失的子系统，由你自行分析"
                 "（标 ai_inferred / ai_new）或改问其他专家补齐 —— 不得留空。"
                 "**写行时 `part` 字段必须写该子系统的名称**（用部件清单里的 subsystem 原文，"
                 "如「射频接收前端」「电源保护」），不要写整个产品名、也不要用你自己起的"
                 "简称 —— 逐系统覆盖是否做到，就是按 `part`/失效模式文本逐条核对的。"
                 "实测 2026-09-13：部件清单为空时退化成「照抄题面提到的几个大类」，"
                 "13 个子系统只覆盖 7 个，A 类覆盖题直接丢 25 分。"
                 "**同族有 N 个子系统就有 N 条 case（如 SW 族 3 个子系统 ↔ history#11/12/13"
                 " 三条），`analogy_cases` 已给出对应关系 —— 照它逐条落行，"
                 "不要因为\"这几条看着像\"就只填其中一个、把同族的其他子系统空着**。"
                 "实测 2026-09-14：SW 族 3 个子系统只填了「固件升级」，"
                 "「基带与固件」「连接与漫游管理」整行缺失，两个覆盖题各丢 5 分。"],
                ["规则", "部件清单为空必须先纠错重查",
                 "「搜索部件清单」返回 `count=0` 时**不许就此放弃**：先看返回里的 `products` "
                 "可用产品清单与 `hint`，用其中的产品名**重查一次**（你的产品名与知识库登记名"
                 "很可能措辞不同，如输入「射频无线模块（蓝牙/WiFi）」而库里登记为「WiFi 模块」）。"
                 "只有重查仍为空、且 `products` 列表为空，才允许退回「仅用历史库部件族」的降级路径 —— "
                 "且必须明确声明发生了什么。实测 2026-09-13：一次措辞不匹配导致整张 13 条"
                 "部件知识库被跳过。"],
                ["规则", "取值优先级链",
                 "每一格按四级顺序取值：① 历史 FMEA 库 → ② AP/S-O-D 准则表 → ③ 询问部件专家数字人 → ④ AI 推断"],
                ["规则", "无历史证据的子系统必须问专家",
                 "优先级链的**第 3 级不是摆设**：当某个子系统在历史库里**没有对应部件族**、"
                 "或历史命中 `hits=0` 时，必须用 `ask_expert` 向对口专家求证，并把该子系统相关格的"
                 "来源标 `expert:<专家名>`。**这不算越级** —— 上一级无证据才轮到下一级，"
                 "正是优先级链的设计意图。"
                 "判据：部件清单给的子系统数（如 13 个）通常**多于**历史库的部件族数（如 7 个），"
                 "多出来的那几个子系统天然没有历史证据 —— 逐个数一遍，凡是 `analogy_family` "
                 "在历史库里 `hits=0` 或为空串的，逐个去问对口专家。"
                 "实测 2026-09-13：聚合节点因历史族命中就全程不问专家，"
                 "`expert:` 引用为 0 处，复核门判定「专家能力完全未被使用」。"],
                ["规则", "专家复核不可省（历史命中也要问）",
                 "**光有历史类比不足以签发表**。历史库是**别的产品**（如蓝牙模块）的案例，"
                 "跨产品迁移必须由对口专家确认「迁移到本产品是否成立、量级要不要调」。"
                 "因此：编排里凡给你 `ask` 边的专家（如射频硬件 / 电源与时钟 / 结构与工艺 / "
                 "嵌入式固件），**每个专家至少问一次**，问的是它负责的那批子系统里"
                 "**风险最高**（S 最大或历史迁移最不确定）的那一条，问题必须具体到"
                 "「部件 + 失效模式 + 迁移后的 S/O/D 是否成立」。"
                 "把回答要点落到**相关格的来源**上（`expert:<专家名>`），而不是只在正文里提一句。"
                 "实测 2026-09-14 job#32：13 个子系统全部命中历史 → 聚合节点全程未调 `ask_expert` → "
                 "`sources` 里 `expert:` 引用 **0 处**（正文写了「本次未调用专家」），"
                 "专家数字人虽挂在 `ask` 边上却完全空转；复核门与考官卷独立判为缺件。"],
                ["规则", "不得越级代填",
                 "上一级有证据时禁止用下一级覆盖 —— 第 1 级命中就不许改用第 2/3/4 级的值"],
                ["规则", "来源随格存储",
                 "同一行不同格可有不同来源，必须逐格标注，不允许整行只标一个来源"],
                ["规则", "来源标注闭集",
                 "来源只能是 history / table / expert:<专家名> / ai_inferred / ai_new；"
                 "history 可带引用号写作 history#<id>，table 可写作 table#<维度>"],
                ["规则", "全新功能判定",
                 "当部件或功能在历史库无对应、准则表无从查起、且专家也无法提供信息时，判为全新功能"],
                ["规则", "AI 生成待确认",
                 "全新功能的取值必须标 ai_new，并在交付时单列「待人工确认清单」；"
                 "有类推依据的推断标 ai_inferred，二者不可混用"],
                ["规则", "AP 以表为准",
                 "AP 必须由 (S,O,D) 查 AP 矩阵得到，不得自行给值；与表不一致时以表为准"],
                ["规则", "表必完整可复核",
                 "**汇总阶段就要产出可直接逐格核对的完整表**：每行含 失效模式 / 后果 / "
                 "原因 / S / O / D / AP / 建议措施，并**逐格标注来源**。"
                 "**表里出现的每一列都要有来源**，包括你为映射而新增的列"
                 "（如「映射 WiFi 模块子系统」）—— 不能只在交接说明里口头声明"
                 "「part/function 为 ai_inferred」，该列每格必须自己带标注"
                 "（`parts#子系统名` 或 `ai_inferred`）。"
                 "**S/O/D 必须是 1~10 的单个整数，严禁写区间**（`S7-8` ✗）—— "
                 "AP 查表依赖确定的 (S,O,D) 三元组，区间无法核对、复核门会直接判 FAIL。"
                 "**交付前必调「取待人工确认清单」**并把返回的清单原文附在交接物末尾 —— "
                 "ai_inferred / ai_new 的格不列清单，复核门必然判 FAIL。"
                 "交给复核门的必须是**表**，不是\"失效模式清单\""],
                ["规则", "S-O-D 先查准则",
                 "给 S/O/D 打分前必须先查对应维度的评分准则（不传参数一次取回三张表），"
                 "确保分值有可引用的判定依据。**打分后来源格必须写对应准则引用号**："
                 "`table#severity7` / `table#occurrence5` / `table#detection4` —— "
                 "**尤其 O 与 D**：它们是 S/O/D 里最容易被全部标成 `ai_inferred` 的两格。"
                 "实测 2026-09-13：7 行的 O/D **全部**为 ai_inferred、准则表一次都没被引用，"
                 "复核门据此判 FAIL（「O、D 无准则/历史依据」）。准则表就在库里、查它只需"
                 "一次调用 —— 明明有证据却标推断，等于自降证据等级。"],
                ["规则", "对照准则复核打分",
                 "打分时**必须拿后果描述去比准则原文**，不能凭数值直觉。典型错误："
                 "后果写「模块无法使用」却给 S=7（主要功能降级）—— 按准则表应为 "
                 "**S=8（丧失主要功能，产品无法使用）**。实测 2026-09-13 被复核门逐条抓出。"
                 "比法：读 table#severity 全文 → 用后果的**可观测表述**去匹配最贴合的一档"],
                ["规则", "证据可追溯",
                 "引用历史库时写入其引用号（如 history#13）；引用查表写 table#ap；"
                 "引用专家写 expert:<专家名> —— 复核门据此逐格核对。"
                 "**禁止用「同上」「余 ai_inferred」「等均为」这类汇总写法**："
                 "逐格标注的字面含义是每一格都写明来源，汇总写法会让复核门无法逐格核对"
                 "（实测 2026-09-13 因此判 FAIL）"],
                ["规则", "无证据不臆造",
                 "任何一格在四级都无依据时不得编造；应标 ai_new 或留空并说明缺哪类证据"],
                ["规则", "询问专家规则",
                 "**两个场景都必须问**：① 历史库与准则表都无结果时（补证据）；"
                 "② 有历史类比时，对**你负责的每个专家各问一次**做迁移确认（见「专家复核不可省」）。"
                 "一次问一个专家，问题须具体到「部件 + 失效模式 + 迁移后的 S/O/D 是否成立」。"
                 "**不要把「历史命中」当成不必问专家的理由** —— 历史是别的产品的案例，"
                 "跨产品迁移的成立性正需要对口专家确认。"],
                ["规则", "part 写中文子系统名",
                 "`part` 列必须填**中文子系统名**（如「射频天线」「连接与漫游管理」"
                 "「固件升级」），**严禁填族代码**（`ANT`/`RF`/`PMU`/`CLK`/`SW`/"
                 "`EMC`/`CON`）。族代码只是 `history_query(family=…)` 的**检索键**，"
                 "不是 part 的值。实测 2026-09-13：把 `SW` 写进 part，导致"
                 "「连接与漫游管理」这个子系统在结果表里**一行都没有**，考官卷直接"
                 "判「该系统有分析行：命中 0 行」。**每个子系统至少 1 行**："
                 "`part_search` 返回几个子系统，表里就要有几个不同 part 值。"],
                ["规则", "专家引用可追溯",
                 "**问过专家就必须在表里留下 `expert:<专家名>` 来源**。"
                 "专家的回答自带 `source=expert:<名>`，采信其结论的格（S/O/D 或"
                 "失效模式）来源必须写该标识。实测 2026-09-13：四位专家全被问过，"
                 "但 17 行来源一律写 `history#N` —— 专家的把关在交付物里"
                 "**完全不可追溯**，考官卷判「expert 引用可追溯：0 处」。"
                 "**交付前必调「核验专家引用可追溯」**：返回的 `missing` 非空就必须"
                 "回头补引用（把该专家提供的值改标 `expert:<名>`），直到 `ok=true`。"],
                ["规则", "产出必须是正文不是工具草稿",
                 "**交付物正文里绝不允许出现 `<tool_call>` / `<tool_calls>` 标记或工具调用"
                 "草稿**。查资料可以随便查，但每轮查完都要把结论沉淀进正文；轮数将尽时"
                 "**立刻停手、把已查到的信息整理成完整表格输出**。"
                 "实测 2026-09-13：汇总节点（要吃下 4 份上游清单，上下文最长）反复调"
                 "「查 AP / S-O-D 准则表」直到轮数耗尽，最终整段产出只有 65 字："
                 "`<tool_call>{\"name\": \"查 AP / S-O-D 准则表\", \"args\": {}}</tool_call>` —— "
                 "**表根本没被产出**，下游复核门只能判 FAIL，整条链路白跑，"
                 "而报错读起来像「复核员挑刺」。**汇总节点的第一职责是产出表**，"
                 "工具只是为表服务；宁可少查一次，也要先把表写出来"],
                ["规则", "写行自检",
                 "写行前自检：① 每格是否都有来源；② AP 是否与表一致；"
                 "③ ai_new 项是否已列入待确认清单；"
                 "④ **每条都必须有建议措施 action**（确实无需措施时写"
                 "「暂无需措施，按现有控制执行」，不留空）—— "
                 "**这是复核门的硬判据**：`action` 整列为空直接判 FAIL 并中止下游。"
                 "实测 2026-09-14：15 行全部 `action=NULL`，"
                 "「结构完整性」题丢分、复核门据此否决整表；"
                 "⑤ part 是否写的是中文子系统名、每个子系统是否都有行"],
                ["规则", "必须落库",
                 "完成分析后**必须**调用「写入 DFMEA 记录」把每条失效模式落库"
                 "（用 rows 数组一次写入多行）；只输出文本而未落库视为未完成"],
                ["规则", "表格行写法定死（引擎会确定性落库）",
                 "汇总节点产出的表**必须用行内 `字段=值` 的写法**，一行一条失效模式，"
                 "行首用 `**R1**`/`**R2**`… 编号，字段之间用 `；` 分隔，"
                 "字段名固定为：`part`、`功能`、`失效模式`、`失效后果`、`失效原因`、"
                 "`S`、`O`、`D`、`AP`、`预防控制`、`探测控制`、`建议措施`。"
                 "**每个取值后面用括号写来源**，例如 "
                 "`S=6（history#1；table#severity6）`、"
                 "`失效模式=天线阻抗失配（history#1）`。\n"
                 "**为什么必须按这个格式**（实测 2026-09-13）：pipeline 里写入器节点"
                 "常被排在复核门**之后**，复核门 FAIL 时写入器根本不执行 → 结果表"
                 "一行都没有，复核门只能对着文本口头核验并报「清单 count=0」。"
                 "引擎现在会**在复核门之前**读这份正文、按上述格式**确定性落库**"
                 "（逐格来源从括号里抽，不臆造）。所以：**正文里的表就是落库的唯一来源**，"
                 "格式写错等于表不存在。不要依赖「写入 DFMEA 记录」动作兜底 —— 它可能"
                 "因为排在复核门之后就执行不到。"],
                ["规则", "优先批量调用",
                 "动作支持批量：查准则可不传参数一次取回 S/O/D 三张表；"
                 "查 AP 可传 items 数组一次查多条；写行可传 rows 数组一次写多行。"
                 "批量调用能显著减少往返，应优先使用"],
            ],
            "actions": [
                {"builtin_name": "fmea_part_search", "name": "搜索部件清单",
                 "description": "自主搜索待分析产品的子系统清单；每个部件带 "
                                "analogy_family（可直接喂给「查询历史 FMEA」做同类案例类比）"},
                {"builtin_name": "fmea_history_query", "name": "查询历史 FMEA",
                 "description": "检索历史 FMEA 库（第 1 级证据）；"
                                "family=部件族（如 ANT/RF/PMU，取自部件清单的 "
                                "analogy_family）做同类案例类比，也可按 part/keyword 查"},
                {"builtin_name": "fmea_ap_table", "name": "查 AP / S-O-D 准则表",
                 "description": "不传参数一次取回 S/O/D 全部准则；传 severity/occurrence/detection 查 AP；传 items 批量查"},
                {"builtin_name": "ask_expert", "name": "询问专家数字人",
                 "description": "向部件专家提问取回专业信息（取值优先级第 3 级）"},
                {"builtin_name": "fmea_write_row", "name": "写入 DFMEA 记录",
                 "description": "写入 DFMEA 行并逐格标注来源；支持 rows 数组批量写入"},
                {"builtin_name": "fmea_pending_confirm", "name": "取待人工确认清单",
                 "description": "**交付前必调**：确定性抽回本 run 所有 ai_inferred / "
                                "ai_new 的格，作为「待人工确认清单」附在交接物里"},
                {"builtin_name": "fmea_expert_citations", "name": "核验专家引用可追溯",
                 "description": "**交付前必调**：确定性核验「问过的专家是否在表里被引用」，"
                                "missing 非空必须补引用直到 ok=true"},
            ],
            "owns": ["取值优先级链", "来源标注规则", "不得越级代填", "AI 生成待确认", "AP 以表为准"],
        },
    },
    # ---------- 2. DFMEA 复核员（review 门） ----------
    {
        "code": "dfmea_reviewer",
        "label": "DFMEA 复核员",
        "description": "复核 DFMEA 的依据可追溯性、AP 一致性与待确认清单（review 门角色）",
        "category": "general",
        "slots": [
            {"key": "name", "label": "数字人名", "required": True, "default": "DFMEA 复核员"},
            {"key": "domain", "label": "应用领域", "required": True, "default": "产品"},
        ],
        "blueprint": {
            "mission": "核对{{domain}} DFMEA 每格的 S/O/D 是否有可追溯依据、AP 是否与表一致、"
                       "ai_new 项是否已列入待人工确认清单",
            "description": "平台级通用数字人（复核门角色）：只做核对与质疑，不替 DFMEA 工程师"
                           "改数；发现依据不足或来源缺失时明确指出并要求补齐",
            "prompt": "你是 DFMEA 复核员，只核对不代填。逐格检查依据是否可追溯、"
                      "AP 是否与 AP 表一致、ai_new 是否已列清单。发现问题直接指出，不要粉饰。",
            "keywords": ["复核", "依据", "可追溯", "AP 一致性", "待人工确认"],
            "anchors": [["复核准则", "规则"], ["AP 以表为准", "规则"]],
            "ontology": [
                ["概念", "DFMEA 复核",
                 "对 DFMEA 表逐格核对证据充分性与来源可追溯性的独立评审"],
                ["规则", "复核准则",
                 "逐项核对：① 每格是否标注来源；② S/O/D 是否有准则表或历史库依据；"
                 "③ AP 是否与 AP 表一致（用「核验整表 AP 一致性」一次核完）；"
                 "④ ai_new / ai_inferred 项是否已列入待人工确认清单（用「取待人工确认清单」"
                 "取回**实际清单**再判，不要凭交接物的口头声明）；"
                 "⑤ 是否存在越级代填（上级有证据却用了下级来源）；"
                 "⑥ **专家引用是否可追溯**（用「核验专家引用可追溯」：问过的专家名单与"
                 "表里引用的 `expert:<名>` 求差集，missing 非空即判 FAIL）；"
                 "⑦ **part 是否写中文子系统名**（写族代码 `ANT`/`RF`/`SW` 等即判 FAIL），"
                 "且 `part_search` 给出的每个子系统是否都至少有一行；"
                 "⑧ **结构完整性**：每行的 `建议措施`（action）是否非空 —— "
                 "整列为空即判 FAIL（DFMEA 的落点是改进措施，无措施的表不完整）；"
                 "同时核对 `失效模式`/`失效后果`/`失效原因` 三列是否逐行非空；"
                 "⑨ **专家层是否被真正使用**：编排里挂在 `ask` 边上的对口专家，"
                 "若 `expert:` 引用为 **0 处**（整表全靠历史类比、专家数字人完全空转）即判 FAIL —— "
                 "历史库是**别的产品**的案例，跨产品迁移的成立性必须有专家确认。"
                 "判据：`核验专家引用可追溯` 返回的 `consulted` 与表里 `expert:<名>` 的差集为空、"
                 "但 `consulted` 本身为空 → 判 FAIL（不是「没问题」，而是「**根本没问**」）"],
                ["规则", "复核不代填",
                 "复核员只提出质疑与补齐要求，不直接修改 DFMEA 数值 —— 数值修改权归 DFMEA 工程师与用户"],
                ["规则", "凭证即来源标注",
                 "本平台的「凭证编号」即 DFMEA 每格的**来源标注**：history#<id>（历史库条目编号）、"
                 "table#<维度>（准则/AP 表）、expert:<专家名>（专家回答）、ai_inferred / ai_new（AI 推断）。"
                 "复核时按此口径判断来源是否可追溯，不要要求额外的编号体系；带上述标注即视为可追溯"],
                ["规则", "复核聚焦",
                 "聚焦**可机器核查**的项：来源标注是否齐备可追溯、AP 是否与 AP 表一致、"
                 "ai_new 是否已列入待确认清单、是否存在越级代填。"
                 "对需求阶段已声明为「待确认」的事项（如评分体系选型、待实测数据），"
                 "记录为观察项，不因此直接否决整表"],
                ["规则", "自己取证据再判",
                 "**核 AP 必须一次核完整表**：调「核验整表 AP 一致性」"
                 "（传 rows=<表中每一行的 {failure_mode, severity, occurrence, detection, ap}>），"
                 "它逐行返回表值与你被核值是否一致（match=true/false/null），"
                 "**一次调用就能核完十几行**，是核 AP 的唯一正确用法。"
                 "若某行连 S/O/D 都没给出整数，返回 match=null —— 据实记为「无法核验」并要求补齐，"
                 "不要替它猜一个 AP。"
                 "**不能因为「没有单独返回 AP 表」就判 AP 无法核验** —— 实测 2026-09-13："
                 "复核员逐行调「按三元组查一个值」，17 行只核完 1 行就耗尽工具轮数，"
                 "于是报「其余 16 条未逐行核验→证据不足→FAIL」，"
                 "而实际上 17 行的 AP 与表**完全一致**。"
                 "**判 FAIL 前先确认自己用尽了取证手段**：整表核验是**一次调用**的事。"
                 "（同类问题：查 S/O/D 准则也是先取原文再判，不要凭印象说「无依据」）"],
                ["规则", "同源核验的边界",
                 "本平台的复核门在当前配置下由**另一个模型**（DeepSeek V4-Pro）承担，"
                 "与生成器（V4.1-Flash）不同源。但**同属 DeepSeek 家族**，"
                 "因此在「领域惯例判断」（如某失效模式是否该出现在此部件）上"
                 "可能存在共同盲区。发现此类**需要外部事实**的疑点时，"
                 "标为观察项并说明「需异厂商模型或人工确认」，不要凭家族共识直接判 FAIL"],
            ],
            "actions": [
                {"builtin_name": "fmea_history_query", "name": "查询历史 FMEA",
                 "description": "核对某条目是否真在历史库中（验证来源可追溯）"},
                {"builtin_name": "fmea_ap_verify", "name": "核验整表 AP 一致性",
                 "description": "**核 AP 的主用动作**：传 rows=<表中每行的 "
                                "{failure_mode, severity, occurrence, detection, ap}>，"
                                "一次返回逐行 match（true/false/null），十几行一次核完"},
                {"builtin_name": "fmea_ap_table", "name": "查 AP / S-O-D 准则表",
                 "description": "取回 AP 矩阵原文（matrix_severity=<S值>）或 S/O/D 准则表原文，"
                                "用于复核取值口径；**核 AP 一致性请优先用「核验整表 AP 一致性」**"},
                {"builtin_name": "ontology_retrieve", "name": "检索本体",
                 "description": "检索复核准则"},
                {"builtin_name": "fmea_pending_confirm", "name": "取待人工确认清单",
                 "description": "核「ai_new / ai_inferred 是否已列清单」时调它取回**实际清单**，"
                                "不要凭交接物的口头声明判断"},
                {"builtin_name": "fmea_expert_citations", "name": "核验专家引用可追溯",
                 "description": "核「专家引用是否可追溯」时调它：返回 consulted（问过谁）/"
                                "cited（表里引用了谁）/missing（问过但没引用），"
                                "missing 非空即判 FAIL，不要凭印象判断"},
            ],
            "owns": ["复核准则", "DFMEA 复核"],        },
    },
    # ---------- 3. 部件专家（通用槽位化，一次可生成任意子系统专家） ----------
    {
        "code": "part_expert",
        "label": "部件专家（通用）",
        "description": "某子系统/部件的领域专家，向 DFMEA 工程师提供部件知识与典型失效",
        "category": "domain_expert",
        "slots": [
            {"key": "name", "label": "数字人名", "required": True,
             "default": "", "placeholder": "如：射频硬件专家"},
            {"key": "subsystem", "label": "负责的子系统", "required": True,
             "placeholder": "如：射频链路（天线、匹配网络、PA/LNA、滤波器）"},
            {"key": "scope", "label": "知识范围", "required": False,
             "default": "该子系统的结构、材料、工况边界与常见失效",
             "placeholder": "如：材料 / 工况 / 边界条件"},
            {"key": "keywords", "label": "关键词（逗号分隔）", "required": False,
             "default": "", "placeholder": "如：天线, 阻抗匹配, 回波损耗"},
            {"key": "domain_ontology", "label": "领域知识（每行：类型|名称|定义）",
             "required": False, "type": "ontology", "default": "",
             "hint": "类型 ∈ 概念 / 规则 / 流程 / 角色 / 指标；留空则只带通用骨架",
             "placeholder": "概念|天线阻抗匹配|使天线在 2.4GHz 呈现 50Ω 纯阻性，失配导致功率反射"},
        ],
        "blueprint": {
            "mission": "提供{{subsystem}}的部件知识与典型失效",
            "description": "领域专家数字人：被 DFMEA 工程师以 ask 回退询问时，"
                           "给出部件级失效模式、机理与量化区间",
            "keywords": ["部件", "失效模式", "机理", "量化区间"],
            "anchors": [["答复要求", "规则"]],
            "ontology": [
                ["概念", "部件知识范围", "{{scope}}"],
                ["规则", "答复要求",
                 "回答应给出：可能的失效模式、常见原因、典型量化区间与验证方法；"
                 "无把握时明确说「该数值需实测确认」，不猜测具体数字"],
                ["规则", "不做 DFMEA 结论",
                 "只提供部件级事实与机理，不代替 DFMEA 工程师给 S/O/D 最终评分或 AP"],
                # 证据优先级：**历史 FMEA 库是第一证据源**。
                #
                # 2026-09-14 job#31 实测：4 个部件专家全体回「我的知识里没有这方面
                # 内容」，根因是它们只有「检索本体 + 检索 RAG」两个动作，而 RAG 里
                # 全是无关论文摘要、本体只覆盖十几个概念 —— 真正装着 17 条历史
                # FMEA 案例（含 S/O/D/AP 与失效链）的 `fmea_cases` 它们根本够不着。
                # 于是最该产出失效模式的节点集体交白卷，整表只能由上游那张
                # `ai_inferred` 映射表撑起来。
                #
                # 修法（属「补零件」，不改生成逻辑）：① 给部件专家绑上
                # `fmea_history_query`；② 在锚点本体里把证据优先级写成硬规则。
                ["规则", "证据优先级",
                 "产出失效模式时，证据按以下优先级取用，并在每一条后标注来源："
                 "① `查询历史 FMEA`（family= 同族）取回的同族历史案例 —— 这是**首选**"
                 "证据，其中的失效模式、后果、原因、S/O/D 可直接类比；"
                 "② 本体段里的概念与规则；③ RAG 原文片段。"
                 "历史库有同族案例时**不得**以「我不知道」作答；"
                 "只有三路都为空才允许声明知识空白。"],
                ["规则", "类比推导要求",
                 "拿到同族历史案例后，必须**逐条**给出「历史案例 → 本子系统」的类比"
                 "迁移：历史失效模式对应本子系统的哪个部件/功能、迁移后失效模式如何"
                 "表述、沿用或调整后的 S/O/D 取值。标注来源写作 `history#<id>`。"
                 "不得只罗列历史案例而不做迁移。"],
            ],
            "actions": [
                {"builtin_name": "ontology_retrieve", "name": "检索本体",
                 "description": "在本体段检索该子系统的概念与判定依据"},
                {"builtin_name": "fmea_history_query", "name": "查询历史 FMEA",
                 "description": "按部件族（family）取回同族历史案例，含失效模式/后果/"
                                "原因/S/O/D/AP —— 本子系统失效模式的**首选证据源**"},
                {"builtin_name": "rag_retrieve", "name": "检索 RAG 资料",
                 "description": "在已入库文档中检索该子系统相关原文片段"},
            ],
            "owns": ["部件知识范围", "答复要求", "证据优先级", "类比推导要求"],
        },
    },
    # ---------- 4. 知识摄取官（design §12） ----------
    {
        "code": "knowledge_ingestor",
        "label": "知识摄取官",
        "description": "承载 RAG 摄取契约（分块/类型/证据/强制注入）的数字人",
        "category": "general",
        "slots": [
            {"key": "name", "label": "数字人名", "required": True, "default": "知识摄取官"},
        ],
        "blueprint": {
            "mission": "把部门文档切成分段、判明性质、守住证据，产出可检索、可溯源、可审批的知识",
            "description": "平台级通用数字人：负责 RAG 摄取的规则定义、内容类型词表维护与复盘提名；"
                           "不参与逐条判定 —— 摄取执行由确定性 ingest job 完成",
            "prompt": "回答时优先引用摄取契约本体；涉及分块/去重/类型判定的问题只陈述规则，"
                      "不代替 ingest 链路做逐条裁决；拿不准就说不知道。",
            "keywords": ["分段", "分段摘要", "内容类型", "置信度", "强制等级",
                         "证据 span", "去重键", "入库契约"],
            "anchors": [["入库契约", "规则"], ["类型判定规则", "规则"],
                        ["强制注入规则", "规则"], ["Chunk 分段单元", "概念"]],
            "ontology": [
                ["概念", "Chunk 分段单元",
                 "文档切分后的最小检索单位；句边界优先、超长句硬切并保留重叠（默认 800 字 / 重叠 100）"],
                ["概念", "分段摘要", "对 chunk 生成的语义浓缩；是 embedding 的向量化对象，不是原文"],
                ["概念", "内容类型 type", "chunk_types 词表项；派生「置信度 + 强制等级」两个正交维度"],
                ["概念", "置信度", "知识可信程度：high / medium / low；由 type 词表默认值决定，用户可覆盖"],
                ["概念", "强制等级", "知识约束力：0 参考 / 1 建议 / 2 强制；=2 时对话必选注入，走独立配额"],
                ["概念", "证据 span", "chunk 内逐字可回溯的字符区间（start/end）；本体候选必须携带"],
                ["概念", "去重键", "文档 name：hash 相同则跳过；同名不同 hash 视为冲突，须用户二次确认"],
                ["概念", "入库契约",
                 "覆盖语义：DELETE chunks（mentions 级联）+ UPDATE documents（保 id）+ INSERT 新 chunks"],
                ["规则", "分块规则", "句边界优先切分；单句超长硬切且相邻块保留重叠，禁止整篇成为一块"],
                ["规则", "类型判定规则", "LLM 只提名 type；两个维度一律由词表默认值确定性裁决，提名不直接落库"],
                ["规则", "未知类型不臆造", "type 不在 active 词表即置 unknown，禁止临时造类型"],
                ["规则", "证据逐字规则", "抽取的实体名称与定义必须在来源 chunk 内逐字命中，否则拒绝入池"],
                ["规则", "embedding 对象规则", "只对 summary 向量化（bge-m3 锁定），原文与 tags 不参与向量"],
                ["规则", "强制注入规则", "mandatory=2 的 chunk 必选注入上下文，走独立配额不参与普通 top-K"],
                ["规则", "覆盖确认规则", "同名不同 hash 不静默覆盖，必须先 dry-run 呈现冲突再二次确认"],
            ],
            "actions": [
                {"builtin_name": "ontology_retrieve", "name": "检索本体", "description": "检索摄取契约本体"},
                {"builtin_name": "rag_retrieve", "name": "检索 RAG 资料", "description": "检索已入库语料"},
            ],
            "owns": ["入库契约", "类型判定规则", "强制注入规则", "证据逐字规则", "内容类型 type"],
        },
    },
]

BUILTIN_CODES = {t["code"] for t in BUILTIN_TEMPLATES}


# ---------------- 预设写入 ----------------

def _now() -> float:
    return time.time()


def ensure_seed(conn) -> int:
    """幂等写入内置模板：只 INSERT 缺失的 code，**不覆盖**用户改动。"""
    existing = {r["code"] for r in
                conn.execute("SELECT code FROM persona_templates").fetchall()}
    added = 0
    for t in BUILTIN_TEMPLATES:
        if t["code"] in existing:
            continue
        conn.execute(
            "INSERT INTO persona_templates(code, label, description, category,"
            " slots, blueprint, builtin, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (t["code"], t["label"], t.get("description", ""), t["category"],
             json.dumps(t["slots"], ensure_ascii=False),
             json.dumps(t["blueprint"], ensure_ascii=False),
             True, "active", _now()))
        added += 1
    if added:
        conn.commit()
    return added


def reset_builtin(conn, codes=None) -> int:
    """把内置模板**恢复为代码里的蓝图**（显式操作，返回恢复条数）。

    为什么需要：`ensure_seed` 只补缺失、不覆盖 —— 这是为了尊重用户对内置换模板的
    修改。但**平台自身升级内置蓝图**时（例如给 DFMEA 工程师新增「搜索部件清单」
    动作）需要一个显式入口，否则代码改了、库里还是旧蓝图，seed 出去的仍是旧数字人。

    语义是**恢复出厂**：用户对内置模板的改动会被覆盖。因此只应由 seed / 运维脚本
    显式调用，绝不放进启动路径。
    """
    tgt = set(codes) if codes else set(BUILTIN_CODES)
    n = 0
    for t in BUILTIN_TEMPLATES:
        if t["code"] not in tgt:
            continue
        row = get_by_code(conn, t["code"])
        if not row:
            conn.execute(
                "INSERT INTO persona_templates(code, label, description,"
                " category, slots, blueprint, builtin, status, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (t["code"], t["label"], t.get("description", ""), t["category"],
                 json.dumps(t["slots"], ensure_ascii=False),
                 json.dumps(t["blueprint"], ensure_ascii=False),
                 True, "active", _now()))
        else:
            conn.execute(
                "UPDATE persona_templates SET label=?, description=?, category=?,"
                " slots=?, blueprint=?, builtin=TRUE, status='active' WHERE id=?",
                (t["label"], t.get("description", ""), t["category"],
                 json.dumps(t["slots"], ensure_ascii=False),
                 json.dumps(t["blueprint"], ensure_ascii=False), row["id"]))
        n += 1
    conn.commit()
    return n


# ---------------- 读取 ----------------

def _row_to_dict(r) -> dict:
    d = dict(r)
    for k in ("slots", "blueprint"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:  # noqa: BLE001
                v = [] if k == "slots" else {}
        d[k] = v if isinstance(v, (list, dict)) else ([] if k == "slots" else {})
    d["builtin"] = bool(d.get("builtin"))
    bp = d["blueprint"]
    d["stats"] = {
        "anchors": len(bp.get("anchors") or []),
        "ontology": len(bp.get("ontology") or []),
        "actions": len(bp.get("actions") or []),
    }
    return d


def list_templates(conn, active_only: bool = False) -> list[dict]:
    where = "WHERE status='active'" if active_only else ""
    rows = conn.execute(
        "SELECT id, code, label, description, category, slots, blueprint,"
        f" builtin, status, created_at FROM persona_templates {where}"
        " ORDER BY builtin DESC, id").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_by_id(conn, tid: int):
    r = conn.execute("SELECT * FROM persona_templates WHERE id=?",
                     (tid,)).fetchone()
    return _row_to_dict(r) if r else None


def get_by_code(conn, code: str):
    r = conn.execute("SELECT * FROM persona_templates WHERE code=?",
                     ((code or "").strip().lower(),)).fetchone()
    return _row_to_dict(r) if r else None


# ---------------- 渲染（确定性：槽位替换 + 必填校验） ----------------

def _subst(node, values: dict):
    """递归替换蓝图里的 {{slot}}。dict/list/str 都处理，其它原样返回。"""
    if isinstance(node, str):
        return SLOT_RE.sub(lambda m: str(values.get(m.group(1), "")), node)
    if isinstance(node, list):
        return [_subst(x, values) for x in node]
    if isinstance(node, dict):
        return {k: _subst(v, values) for k, v in node.items()}
    return node


def _split_keywords(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "")
    parts = re.split(r"[,，、;；]", s)
    return [p.strip() for p in parts if p.strip()]


#: 本体条目允许的 kind（与平台闭集一致；越界回落到「概念」）
_ONT_KINDS = ("概念", "规则", "流程", "角色", "组织架构", "指标")


def parse_ontology_text(raw) -> list[list[str]]:
    """把多行「类型|名称|定义」文本解析为本体条目。

    用于 `type="ontology"` 的槽位 —— 让**领域知识**也能作为填空内容注入，
    而不必为每个领域硬编码一个模板（通用模板 + 领域知识槽位 = 任意领域专家）。
    """
    out: list[list[str]] = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[|｜]", line, 2)]
        if len(parts) < 3:
            continue
        kind, nm, dfn = parts[0], parts[1], parts[2]
        if kind not in _ONT_KINDS:
            kind = "概念"
        if nm and dfn:
            out.append([kind, nm, dfn])
    return out


def resolve_slots(tpl: dict, values: dict) -> tuple[bool, list[str], dict]:
    """合并「槽位默认值 + 用户填写值」并校验必填。返回 (ok, errors, slots)。"""
    vals = {}
    for s in tpl.get("slots") or []:
        k = s.get("key")
        if not k:
            continue
        v = (values or {}).get(k)
        if v in (None, ""):
            v = s.get("default", "")
        vals[k] = str(v).strip() if isinstance(v, str) else v
    errs = []
    for s in tpl.get("slots") or []:
        if s.get("required") and not str(vals.get(s.get("key")) or "").strip():
            errs.append(f"{s.get('label') or s.get('key')} 为必填")
    if (values or {}).get("__name__"):
        vals["name"] = str(values["__name__"]).strip()
    return (not errs), errs, vals


def render(tpl: dict, values: dict) -> dict:
    """把模板渲染成**可直接落库**的数字人结构。

    返回 ``{"ok", "errors"?, ...}``；成功时含 name/mission/description/prompt/
    keywords/category/anchors/ontology/actions/owns。
    """
    ok, errs, slots = resolve_slots(tpl, values or {})
    if not ok:
        return {"ok": False, "errors": errs}
    bp = _subst(tpl.get("blueprint") or {}, slots)
    name = str(slots.get("name") or "").strip()
    if not name:
        return {"ok": False, "errors": ["数字人名不能为空"]}
    # 领域知识槽位（type=ontology）：解析后**追加**在模板自带本体之后
    extra_ont: list = []
    for s in (tpl.get("slots") or []):
        if s.get("type") == "ontology":
            extra_ont += parse_ontology_text(slots.get(s.get("key")))
    ontology_raw = list(bp.get("ontology") or []) + extra_ont
    return {
        "ok": True,
        "template": {"id": tpl.get("id"), "code": tpl.get("code"),
                     "label": tpl.get("label")},
        "slots": slots,
        "name": name,
        "mission": str(bp.get("mission") or "").strip(),
        "description": str(bp.get("description") or "").strip(),
        "prompt": str(bp.get("prompt") or "").strip(),
        "keywords": _split_keywords(bp.get("keywords")),
        "category": (tpl.get("category") or "domain_expert"),
        "anchors": [tuple(a) if isinstance(a, list) else a
                    for a in (bp.get("anchors") or [])],
        "ontology": [tuple(o) if isinstance(o, list) else o
                     for o in ontology_raw],
        "actions": [a for a in (bp.get("actions") or []) if isinstance(a, dict)],
        "owns": [str(x) for x in (bp.get("owns") or [])],
    }


# ---------------- 落库（instantiate） ----------------

def _upsert_identity(conn, r: dict, status: str) -> int:
    """模板路径的身份行写入 —— **已收口到 `identity.upsert_identity`**
    （design §20 / R-22）。

    此前这里是独立 INSERT/UPDATE（无 MAX_NAME_LEN 校验、与
    identity.create_identity 平行），加字段要改两处、校验分叉。现在只保留
    「模板蓝图 → 统一入口」的字段映射；幂等更新语义（重复实例化覆盖字段）
    由 `update_if_exists=True` 承载。
    """
    from . import identity as identity_mod
    return identity_mod.upsert_identity(
        conn, r["name"], r["mission"],
        description=r.get("description", ""),
        keywords=r.get("keywords") or [],
        prompt=r.get("prompt", ""),
        category=r.get("category") or "domain_expert",
        status=status,
        update_if_exists=True)


def _upsert_anchor(conn, iid: int, name: str, atype: str) -> None:
    row = conn.execute("SELECT id FROM anchors WHERE identity_id=? AND name=?",
                       (iid, name)).fetchone()
    if row:
        conn.execute("UPDATE anchors SET type=?, status='approved' WHERE id=?",
                     (atype, row["id"]))
    else:
        conn.execute(
            "INSERT INTO anchors(identity_id, name, type, definition, status,"
            " created_at) VALUES(?,?,?,?, 'approved', ?)",
            (iid, name, atype, "", _now()))
    conn.commit()


def _upsert_candidate(conn, kind, name, definition) -> int:
    from .ontology import _norm_name
    norm = _norm_name(name)
    row = conn.execute(
        "SELECT id FROM candidates WHERE name_norm=? ORDER BY id LIMIT 1",
        (norm,)).fetchone()
    if row:
        conn.execute("UPDATE candidates SET kind=?, definition=?, status='approved'"
                     " WHERE id=?", (kind, definition, row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO candidates(kind, name, name_norm, definition, status, tags,"
        " created_at) VALUES(?,?,?,?, 'approved', ?, ?)",
        (kind, name, norm, definition, [], _now()))
    conn.commit()
    return cur.lastrowid


def instantiate(conn, tpl: dict, values: dict,
                status: str = "approved") -> dict:
    """按模板 + 槽位值创建（或幂等更新）一个完整数字人。

    落库内容：身份 + 锚点 + 本体段（persona_ontology）+ 动作绑定（approved）
    + 「角色 --owns--> 条目」关系。全部为**确定性写入**，不经过 LLM。
    """
    r = render(tpl, values)
    if not r.get("ok"):
        return {"ok": False, "errors": r.get("errors") or ["渲染失败"]}
    try:
        iid = _upsert_identity(conn, r, status)
        for a in r["anchors"]:
            if isinstance(a, (list, tuple)) and len(a) >= 2:
                _upsert_anchor(conn, iid, str(a[0]), str(a[1]))
        for o in r["ontology"]:
            if isinstance(o, (list, tuple)) and len(o) >= 3:
                # update=True：模板是**单一事实源**，代码蓝图改了必须同步进库
                # （否则被编辑过的规则定义会静默停留在旧版 —— 实测 2026-09-13）。
                trainer.add_ontology(conn, iid, str(o[0]), str(o[1]), str(o[2]),
                                     note=f"template:{tpl.get('code')}",
                                     update=True)
        bound, failed = 0, []
        for a in r["actions"]:
            res = actions.bind_builtin_action(
                conn, iid, a.get("builtin_name"),
                name=a.get("name") or "", description=a.get("description") or "")
            if res.get("ok"):
                bound += 1
            else:
                failed.append({"builtin_name": a.get("builtin_name"),
                               "error": res.get("error")})
        # 沉淀到全局本体库 + owns 关系
        role_cid = _upsert_candidate(conn, "角色", r["name"], r["mission"])
        owns = 0
        for oname in r["owns"]:
            rec = conn.execute(
                "SELECT kind, definition FROM persona_ontology"
                " WHERE identity_id=? AND name=?", (iid, oname)).fetchone()
            if not rec:
                continue
            _upsert_candidate(conn, rec["kind"], oname, rec["definition"] or "")
            exists = conn.execute(
                "SELECT id FROM relations WHERE source_id=? AND target_name=?"
                " AND relation_type='owns'", (role_cid, oname)).fetchone()
            if not exists:
                conn.execute("INSERT INTO relations(source_id, target_name,"
                             " relation_type) VALUES(?,?, 'owns')",
                             (role_cid, oname))
                owns += 1
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "errors": [f"落库失败：{e}"]}
    return {"ok": True, "identity_id": iid, "name": r["name"],
            "category": r["category"],
            "counts": {"anchors": len(r["anchors"]),
                       "ontology": len(r["ontology"]),
                       "actions_bound": bound, "owns": owns},
            "action_errors": failed}


# ---------------- 用户自定义模板 CRUD ----------------

class TemplateError(Exception):
    """模板校验失败（供 API 层转 400）。"""


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


def create_template(conn, code: str, label: str, category: str = "domain_expert",
                    description: str = "", slots=None, blueprint=None) -> dict:
    code = (code or "").strip().lower()
    label = (label or "").strip()
    if not _CODE_RE.match(code):
        raise TemplateError("code 必须是英文小写字母开头、只含小写字母/数字/下划线（3~32 位）")
    if not label:
        raise TemplateError("label 不能为空")
    if get_by_code(conn, code):
        raise TemplateError(f"模板 code 已存在：{code}")
    if not isinstance(blueprint, dict) or not blueprint:
        raise TemplateError("blueprint 不能为空")
    conn.execute(
        "INSERT INTO persona_templates(code, label, description, category,"
        " slots, blueprint, builtin, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (code, label, description.strip(), category,
         json.dumps(slots or [], ensure_ascii=False),
         json.dumps(blueprint, ensure_ascii=False), False, "active", _now()))
    conn.commit()
    return get_by_code(conn, code)


def update_template(conn, tid: int, patch: dict) -> dict:
    cur = get_by_id(conn, tid)
    if not cur:
        raise TemplateError("模板不存在")
    label = (patch.get("label") or cur["label"]).strip()
    if not label:
        raise TemplateError("label 不能为空")
    description = cur.get("description") or ""
    if patch.get("description") is not None:
        description = str(patch["description"]).strip()
    category = patch.get("category") or cur["category"]
    slots = patch.get("slots") if isinstance(patch.get("slots"), list) else cur["slots"]
    blueprint = patch.get("blueprint") if isinstance(patch.get("blueprint"), dict) \
        else cur["blueprint"]
    status = patch.get("status") or cur["status"]
    if status not in ("active", "disabled"):
        raise TemplateError("status 必须是 active/disabled")
    conn.execute(
        "UPDATE persona_templates SET label=?, description=?, category=?,"
        " slots=?, blueprint=?, status=? WHERE id=?",
        (label, description, category,
         json.dumps(slots, ensure_ascii=False),
         json.dumps(blueprint, ensure_ascii=False), status, tid))
    conn.commit()
    return get_by_id(conn, tid)


def delete_template(conn, tid: int) -> None:
    cur = get_by_id(conn, tid)
    if not cur:
        raise TemplateError("模板不存在")
    if cur["builtin"]:
        raise TemplateError("内置模板不可删除（可改说明或停用）")
    conn.execute("DELETE FROM persona_templates WHERE id=?", (tid,))
    conn.commit()


def usage_count(conn, code: str) -> int:
    """该模板被创建过多少次（按本体 note 标记统计，用于删除前提示）。"""
    return conn.execute(
        "SELECT COUNT(DISTINCT identity_id) c FROM persona_ontology WHERE note=?",
        (f"template:{code}",)).fetchone()["c"]


# ---------------- 反向：把现有数字人沉淀为模板 ----------------

def _parametrize(node, pairs: list[tuple[str, str]]):
    """把蓝图里出现的**具体词**换成 `{{slot}}` 占位 —— `_subst` 的逆操作。

    pairs 按 find 长度**降序**替换，避免短词先命中把长词切碎
    （如 name="DFMEA 工程师"、domain="DFMEA"，若先换 domain 会残成
    `{{domain}} 工程师`，再换 name 就匹配不到了）。
    """
    if not pairs:
        return node
    ordered = sorted(pairs, key=lambda p: -len(p[0]))
    if isinstance(node, str):
        s = node
        for find, key in ordered:
            if find:
                s = s.replace(find, "{{%s}}" % key)
        return s
    if isinstance(node, list):
        return [_parametrize(x, ordered) for x in node]
    if isinstance(node, dict):
        return {k: _parametrize(v, ordered) for k, v in node.items()}
    return node


def identity_to_template(conn, identity_id: int, code: str, label: str = "",
                         description: str = "", category: str | None = None,
                         parametrize=None) -> dict:
    """把已存在的数字人**反向沉淀为模板**（六元组蓝图 + 槽位）。

    `parametrize` 是「词 → 槽位」映射列表，形如
    ``[{"find": "手机蓝牙模块", "key": "domain", "label": "应用领域"}]``；
    映射到的词在蓝图里被替换为 `{{domain}}`，并自动生成对应槽位声明。
    默认总会把**数字人名**参数化为 `name` 槽位。

    **往返一致性**：每个槽位的 `default` 都取原词，因此「空填写渲染」应还原出
    与原数字人等价的蓝图（`render()` 的结果与原记录逐字段相等）—— 这是本函数
    的验收口径，保证"沉淀下来的模板"没丢信息。

    非 builtin 的动作（MCP / 其它）无法用模板表达，会列在 `skipped_actions` 里
    如实上报，不静默丢弃。
    """
    row = conn.execute("SELECT * FROM identities WHERE id=?",
                       (identity_id,)).fetchone()
    if not row:
        return {"ok": False, "errors": [f"数字人 {identity_id} 不存在"]}
    ident = dict(row)

    anchors = [[r["name"], r["type"] or "规则"] for r in conn.execute(
        "SELECT name, type FROM anchors WHERE identity_id=? ORDER BY id",
        (identity_id,)).fetchall()]
    ontology = [[r["kind"], r["name"], r["definition"] or ""] for r in
                conn.execute("SELECT kind, name, definition FROM persona_ontology"
                             " WHERE identity_id=? ORDER BY id",
                             (identity_id,)).fetchall()]
    acts: list[dict] = []
    skipped: list[str] = []
    for r in conn.execute(
            "SELECT name, description, kind, builtin_name FROM persona_actions"
            " WHERE identity_id=? ORDER BY id", (identity_id,)).fetchall():
        if r["kind"] == "builtin" and r["builtin_name"]:
            acts.append({"builtin_name": r["builtin_name"], "name": r["name"],
                         "description": r["description"] or ""})
        else:
            skipped.append(r["name"])

    # owns：优先取图谱里该「角色」真实 owns 的条目；缺失时退化为锚点名
    owns = [x["target_name"] for x in conn.execute(
        "SELECT r.target_name FROM relations r JOIN candidates c"
        " ON r.source_id=c.id WHERE c.name=? AND c.kind='角色'"
        " AND r.relation_type='owns'", (ident["name"],)).fetchall()]
    if not owns:
        owns = [a[0] for a in anchors]

    kw = ident.get("keywords") or "[]"
    if isinstance(kw, str):
        try:
            kw = json.loads(kw)
        except Exception:  # noqa: BLE001
            kw = []
    if not isinstance(kw, list):
        kw = []

    # 槽位：name 恒有；其余来自 parametrize（default = 原词）
    slots: list[dict] = [{"key": "name", "label": "数字人名", "required": True,
                          "default": ident["name"],
                          "placeholder": f"如：{ident['name']}"}]
    pairs: list[tuple[str, str]] = []
    if ident["name"]:
        pairs.append((ident["name"], "name"))
    seen = {"name"}
    for p in (parametrize or []):
        if not isinstance(p, dict):
            continue
        find = str(p.get("find") or "").strip()
        key = re.sub(r"[^a-z0-9_]", "", str(p.get("key") or "").strip().lower())
        if not find or not key or key in seen:
            continue
        seen.add(key)
        pairs.append((find, key))
        slots.append({"key": key, "label": p.get("label") or key,
                      "required": False, "default": find})

    bp = _parametrize({
        "mission": (ident.get("mission") or "").strip(),
        "description": (ident.get("description") or "").strip(),
        "prompt": (ident.get("prompt") or "").strip(),
        "keywords": kw,
        "anchors": anchors,
        "ontology": ontology,
        "actions": acts,
        "owns": owns,
    }, pairs)

    try:
        tpl = create_template(
            conn, code, label or ident["name"],
            category or ident.get("category") or "domain_expert",
            description, slots, bp)
    except TemplateError as e:
        return {"ok": False, "errors": [str(e)]}
    return {"ok": True, "template": tpl, "skipped_actions": skipped,
            "source": {"identity_id": identity_id, "name": ident["name"]},
            "counts": {"anchors": len(anchors), "ontology": len(ontology),
                       "actions": len(acts), "slots": len(slots)}}
