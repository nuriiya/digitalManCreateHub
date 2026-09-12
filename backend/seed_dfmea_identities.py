# -*- coding: utf-8 -*-
"""DFMEA 数字人 seed —— 现由**模板机制**驱动（design §16）。

原先这批数字人的身份 / 本体 / 锚点 / 动作是**写死在本脚本里**的；现已沉淀为
`app/persona_templates.py` 的内置模板（`dfmea_engineer` / `part_expert` /
`dfmea_reviewer`）。本脚本只负责「按模板 + 槽位值实例化」，因此与 UI 上
「新建数字人 → 选模板 → 填空」走**同一条路径**（单一事实源，不再有第二份定义）。

其中 4 个部件专家的**领域知识**作为 `domain_ontology` 槽位值传入
（格式：每行 `类型|名称|定义`），与用户在 UI 上填写的是同一个入口。

幂等可重跑，全部为确定性写入（不经过 LLM）。

用法（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python seed_dfmea_identities.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, persona_templates as pt  # noqa: E402

# ---------------- 槽位值（模板 code, values） ----------------

_RF_DOMAIN = """概念|天线阻抗匹配|使天线在 2.4GHz 呈现 50Ω 纯阻性；失配导致功率反射、有效辐射功率下降
概念|回波损耗 S11|衡量匹配优劣，S11 越低匹配越好；量产常见要求 ≤ -10dB
概念|射频前端|由 PA（发射）、LNA（接收）、滤波器与开关组成，决定链路预算
概念|链路预算|发射功率 + 天线增益 - 路径损耗 - 接收灵敏度；不足表现为通信距离缩短
规则|射频失效答复要求|回答应给出：可能的失效模式、常见原因、典型量化区间（如插损 dB、频偏 ppm）与来源；无把握时明确说「该数值需实测确认」，不猜测具体数字"""

_POWER_CLOCK_DOMAIN = """概念|LDO 输出纹波|供电轨的交流扰动；纹波耦合进射频会抬升噪声底、降低接收灵敏度
概念|去耦电容布局|去耦电容须就近放置以降低供电回路阻抗，远离则高频去耦失效
概念|晶振负载电容|与晶振标称负载电容匹配决定振荡频率；不匹配表现为频偏偏离
概念|起振裕度|驱动电路的负阻需大于晶振等效串联电阻的数倍，否则可能停振
规则|供电与时钟失效答复要求|回答应给出失效模式、机理（如阻抗/耦合路径）、典型量级与验证方法；涉及具体器件型号时不臆造，说明需查器件手册"""

_STRUCTURE_DOMAIN = """概念|BGA 焊球应力|热膨胀系数失配在温度循环下于焊球形成应力集中，导致开裂与间歇失效
概念|回流焊润湿|焊料未充分润湿焊盘即形成虚焊；与温度曲线、钢网开孔、表面处理有关
概念|屏蔽罩接地|屏蔽罩需通过密集接地过孔形成低阻回路；接地不良则 EMI 辐射超标
概念|阻抗连续性|射频走线在过孔/拐角处的阻抗突变会引起反射，增大回波损耗
规则|工艺失效答复要求|回答应给出失效模式、工艺诱因（温度曲线/应力/布局）与检测手段；不臆造具体生产参数，说明须结合产线实测"""

_FIRMWARE_DOMAIN = """概念|连接状态机|管理待机/广播/连接/断连的状态迁移；异常断连未复位会导致重连失败
概念|GATT 服务发现|客户端枚举服务与特征值的过程；缓冲区不足会造成分包丢失与超时
概念|MTU 协商|决定单包有效载荷大小；评估不足会导致长报文分片失败
概念|固件升级回滚|升级中断时需有可回退的分区（A/B）与完整性校验，否则模块变砖
规则|固件失效答复要求|回答应给出失效模式、触发条件（弱信号/断电/边界负载）与验证方法；涉及具体协议版本行为时说明依据来源，不臆造版本差异"""

INSTANCES: list[tuple[str, dict]] = [
    ("dfmea_engineer", {"name": "DFMEA 工程师", "domain": "产品"}),
    ("part_expert", {"name": "射频硬件专家",
                     "subsystem": "射频链路（天线、匹配网络、PA/LNA、滤波器）",
                     "scope": "射频链路的材料、工况边界与常见失效",
                     "keywords": "天线, 阻抗匹配, 回波损耗, 射频前端, 链路预算, SAW",
                     "domain_ontology": _RF_DOMAIN}),
    ("part_expert", {"name": "电源与时钟专家",
                     "subsystem": "供电（LDO/去耦/浪涌）与时钟（晶振/负载电容/频偏）",
                     "scope": "供电与时钟的器件特性、布局约束与常见失效",
                     "keywords": "LDO, 纹波, 去耦, 晶振, 负载电容, 频偏, 起振",
                     "domain_ontology": _POWER_CLOCK_DOMAIN}),
    ("part_expert", {"name": "结构与工艺专家",
                     "subsystem": "焊接互连、连接器、屏蔽与 EMC",
                     "scope": "工艺窗口、应力与 EMC 边界条件",
                     "keywords": "BGA, 虚焊, 回流焊, 屏蔽罩, EMI, 热循环, 阻抗连续",
                     "domain_ontology": _STRUCTURE_DOMAIN}),
    ("part_expert", {"name": "嵌入式固件专家",
                     "subsystem": "协议栈、连接状态机与固件升级",
                     "scope": "协议行为、状态迁移与升级可靠性",
                     "keywords": "协议栈, 状态机, 配对, GATT, MTU, 固件升级, 回滚",
                     "domain_ontology": _FIRMWARE_DOMAIN}),
    ("dfmea_reviewer", {"name": "DFMEA 复核员", "domain": "产品"}),
]


def main() -> int:
    conn = db.get_conn()
    n_seed = pt.ensure_seed(conn)
    print(f"内置模板：新增 {n_seed} 个"
          f"（库中共 {len(pt.list_templates(conn))} 个）\n")

    print("=== 由模板实例化 DFMEA 数字人 ===")
    rows = []
    for code, values in INSTANCES:
        tpl = pt.get_by_code(conn, code)
        if not tpl:
            print(f"  !! 模板缺失：{code}")
            continue
        r = pt.instantiate(conn, tpl, values)
        if not r.get("ok"):
            print(f"  !! {values.get('name')} 失败：{r.get('errors')}")
            continue
        c = r["counts"]
        rows.append((r["identity_id"], r["name"], r["category"],
                     c["ontology"], c["actions_bound"]))
        if r.get("action_errors"):
            print(f"     !! 动作绑定失败：{r['action_errors']}")

    for iid, name, cat, n_ont, n_act in rows:
        print(f"  #{iid:<3} {name:<16} {cat:<14} 本体 {n_ont:>2} 条 · 动作 {n_act} 个")
    print(f"\n合计 {len(rows)} 个数字人")
    ok = len(rows) == len(INSTANCES) and all(n > 0 for _, _, _, _, n in rows)
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
