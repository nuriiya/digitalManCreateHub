# -*- coding: utf-8 -*-
"""DFMEA 数据域 seed（design §15.3 的 B1 / B2）。

植入三样东西：
  1. **S/O/D 评分准则**（AIAG-VDA 手册结构，10 分制）—— 查表动作的「准则」侧；
  2. **AP 行动优先级矩阵**（10×10×10 = 1000 格）—— 查表动作的「矩阵」侧；
  3. **手机蓝牙模块历史 FMEA**（6 个子系统 × 多条）—— 取值优先级链第 1 级证据。

都是**数据零件**：不参与 pipeline 生成，只被 fmea_history_query / fmea_ap_table
两个动作**查表读取**。幂等，可重复执行。

AP 说明（重要，勿当成官方逐格表）
--------------------------------
AIAG-VDA 官方 AP 表是按 (S,O,D) 三维查的完整表。此处用**可解释的简化规则**生成
1000 格，规则保留官方核心原则（S 优先、O 次之、D 再次；S≥9 涉及安全/法规时
显著抬高），并把规则口径写进本文档。表是**纯数据**，可由用户整体替换为官方表，
动作侧只做查表、不含任何判定逻辑。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python seed_fmea_data.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402


# ---------------- 1. S / O / D 评分准则（AIAG-VDA 结构） ----------------

SOD_CRITERIA: dict[str, list[tuple[int, str]]] = {
    "severity": [
        (10, "无预警的失效，影响安全或违反法规"),
        (9, "有预警的失效，影响安全或违反法规"),
        (8, "丧失主要功能（产品无法使用）"),
        (7, "主要功能降级（可用但性能明显下降）"),
        (6, "丧失次要功能"),
        (5, "次要功能降级"),
        (4, "外观/听觉/触感不符合，多数用户察觉"),
        (3, "外观/听觉/触感不符合，约半数用户察觉"),
        (2, "外观/听觉/触感不符合，少数用户察觉"),
        (1, "无可辨识的影响"),
    ],
    "occurrence": [
        (10, "极高：≥100 次/千件"),
        (9, "很高：50 次/千件"),
        (8, "高：20 次/千件"),
        (7, "较高：10 次/千件"),
        (6, "中等：2 次/千件"),
        (5, "中低：0.5 次/千件"),
        (4, "低：0.1 次/千件"),
        (3, "很低：0.01 次/千件"),
        (2, "极低：0.001 次/千件"),
        (1, "通过预防控制消除，失效几乎不可能发生"),
    ],
    "detection": [
        (10, "无探测机会或无法探测"),
        (9, "仅能随机/间接探测，很难检出"),
        (8, "事后人工目检"),
        (7, "事后人工量具检查"),
        (6, "事中人工检查"),
        (5, "事中量具检查 / 下线功能测试"),
        (4, "事后自动化探测"),
        (3, "事中自动化探测"),
        (2, "在线自动化探测 + 防错"),
        (1, "防错设计，失效不可能流出"),
    ],
}


# ---------------- 2. AP 行动优先级（简化规则，生成 1000 格） ----------------

def ap_of(s: int, o: int, d: int) -> str:
    """由 (S, O, D) 判定 AP（H 高 / M 中 / L 低）。

    简化规则（保留 AIAG-VDA 核心原则：S 优先、O 次之、D 再次）：
      - S ≥ 9（安全/法规）：O 或 D 偏高即 H；
      - S 7~8（主功能）：O 高或「O 中且 D 高」为 H；
      - S 4~6（次功能）：O 很高或「O 高且 D 高」为 H；
      - S ≤ 3：仅在 O、D 双高时为 M，其余 L。
    """
    if s >= 9:
        if o >= 4 or d >= 7:
            return "H"
        if o >= 2 or d >= 3:
            return "M"
        return "L"
    if s >= 7:
        if o >= 6 or (o >= 4 and d >= 5):
            return "H"
        if o >= 2 or d >= 5:
            return "M"
        return "L"
    if s >= 4:
        if o >= 8 or (o >= 6 and d >= 5):
            return "H"
        if o >= 3 or d >= 5:
            return "M"
        return "L"
    return "M" if (o >= 8 and d >= 5) else "L"


# ---------------- 3. 手机蓝牙模块历史 FMEA（6 子系统） ----------------
#
# 字段：part / part_no / function / failure_mode / failure_effect / severity /
#       failure_cause / occurrence / prevention_control / detection_control /
#       detection / action / source_doc
# AP 不手填 —— 由 ap_of(s,o,d) 计算，保证与 AP 矩阵一致。

_BT = "蓝牙模块"
_FMEA_ROWS: list[dict] = [
    # —— 天线子系统 ——
    {"part": _BT, "part_no": "BT-ANT-01", "function": "2.4GHz 射频信号收发",
     "failure_mode": "天线阻抗失配", "failure_effect": "发射功率下降，通信距离不足",
     "severity": 6, "failure_cause": "天线匹配网络的电容电感公差漂移",
     "occurrence": 4, "prevention_control": "选用 1% 精度匹配元件",
     "detection_control": "网络分析仪测 S11 回波损耗", "detection": 3,
     "action": "增加匹配网络来料抽检比例", "source_doc": "BT-FMEA-2023-A1"},
    {"part": _BT, "part_no": "BT-ANT-01", "function": "2.4GHz 射频信号收发",
     "failure_mode": "天线馈点虚焊", "failure_effect": "间歇性断连，连接不稳定",
     "severity": 7, "failure_cause": "回流焊温度曲线不当导致润湿不良",
     "occurrence": 5, "prevention_control": "优化焊接温区与钢网开孔",
     "detection_control": "在线 AOI 焊点检测", "detection": 2,
     "action": "增加焊点推力抽检", "source_doc": "BT-FMEA-2023-A2"},
    {"part": _BT, "part_no": "BT-ANT-02", "function": "天线馈电与接地",
     "failure_mode": "天线净空区被器件侵占", "failure_effect": "辐射效率下降，方向图畸变",
     "severity": 5, "failure_cause": "PCB 布局后期改版未复核净空区",
     "occurrence": 3, "prevention_control": "净空区在布局规则中设为禁布区",
     "detection_control": "Layout 评审 checklist 复核", "detection": 4,
     "action": "把净空区检查加入 DRC 规则", "source_doc": "BT-FMEA-2022-B3"},
    # —— 射频前端 ——
    {"part": _BT, "part_no": "BT-RF-01", "function": "射频功率放大",
     "failure_mode": "PA 输出功率不足", "failure_effect": "链路预算不足，通信距离缩短",
     "severity": 6, "failure_cause": "PA 偏置电压因分压电阻偏差而异常",
     "occurrence": 3, "prevention_control": "偏置电路采用冗余设计",
     "detection_control": "出厂发射功率测试", "detection": 3,
     "action": "增加偏置电压在线监测点", "source_doc": "BT-FMEA-2023-C1"},
    {"part": _BT, "part_no": "BT-RF-02", "function": "接收频带选择",
     "failure_mode": "SAW 滤波器插损超标", "failure_effect": "接收灵敏度下降",
     "severity": 6, "failure_cause": "滤波器来料批次一致性差",
     "occurrence": 3, "prevention_control": "供应商批次管控与合格供方名录",
     "detection_control": "来料插损抽检", "detection": 4,
     "action": "要求供应商提供批次测试报告", "source_doc": "BT-FMEA-2021-D2"},
    {"part": _BT, "part_no": "BT-RF-03", "function": "射频低噪声放大",
     "failure_mode": "LNA 增益不足", "failure_effect": "接收灵敏度下降，误包率上升",
     "severity": 6, "failure_cause": "LNA 供电轨噪声耦合至栅极",
     "occurrence": 4, "prevention_control": "电源去耦电容就近布局",
     "detection_control": "接收灵敏度与误包率测试", "detection": 4,
     "action": "增加 LNA 供电轨的 LC 滤波", "source_doc": "BT-FMEA-2023-C3"},
    # —— 电源子系统 ——
    {"part": _BT, "part_no": "BT-PMU-01", "function": "为射频与基带供电",
     "failure_mode": "LDO 输出纹波过大", "failure_effect": "射频噪声增加，灵敏度下降",
     "severity": 6, "failure_cause": "输出电容 ESR 偏高或容值不足",
     "occurrence": 5, "prevention_control": "选用低 ESR 陶瓷电容并做相位裕度仿真",
     "detection_control": "电源纹波与噪声测试", "detection": 4,
     "action": "将输出电容改为 X7R 低 ESR 规格", "source_doc": "BT-FMEA-2023-E1"},
    {"part": _BT, "part_no": "BT-PMU-02", "function": "过流与热保护",
     "failure_mode": "LDO 过流保护误触发", "failure_effect": "模块意外断电重启",
     "severity": 7, "failure_cause": "上电浪涌电流超过保护阈值",
     "occurrence": 3, "prevention_control": "增加软启动与限流电路",
     "detection_control": "上电浪涌电流测试", "detection": 5,
     "action": "把软启动参数写入设计规范", "source_doc": "BT-FMEA-2022-E4"},
    # —— 时钟子系统 ——
    {"part": _BT, "part_no": "BT-CLK-01", "function": "提供射频载波基准时钟",
     "failure_mode": "主晶振频偏超差", "failure_effect": "载波频率偏移，配对失败",
     "severity": 8, "failure_cause": "晶振负载电容与标称不匹配",
     "occurrence": 4, "prevention_control": "负载电容按晶振规格精确匹配并留调试点",
     "detection_control": "频偏测试（±ppm）", "detection": 3,
     "action": "首件增加频偏实测并归档", "source_doc": "BT-FMEA-2023-F1"},
    {"part": _BT, "part_no": "BT-CLK-02", "function": "提供基带与低功耗时钟",
     "failure_mode": "晶振停振", "failure_effect": "模块完全无法工作",
     "severity": 9, "failure_cause": "晶振激励功率不足或驱动电路参数错误",
     "occurrence": 2, "prevention_control": "驱动电路负阻裕度仿真验证",
     "detection_control": "上电功能与起振测试", "detection": 3,
     "action": "把负阻裕度纳入设计评审项", "source_doc": "BT-FMEA-2023-F2"},
    # —— 协议栈 / 固件 ——
    {"part": _BT, "part_no": "BT-SW-01", "function": "蓝牙连接状态管理",
     "failure_mode": "配对状态机死锁", "failure_effect": "无法重新连接已配对设备",
     "severity": 7, "failure_cause": "异常断连后状态机未复位",
     "occurrence": 5, "prevention_control": "状态机加超时复位与看门狗",
     "detection_control": "异常断连场景回归测试", "detection": 4,
     "action": "补充弱信号反复断连的压力测试用例", "source_doc": "BT-FMEA-2023-G1"},
    {"part": _BT, "part_no": "BT-SW-02", "function": "GATT 服务发现",
     "failure_mode": "GATT 服务发现超时", "failure_effect": "应用层功能不可用",
     "severity": 6, "failure_cause": "协议栈缓冲区不足导致分包丢失",
     "occurrence": 4, "prevention_control": "按最大 MTU 评估缓冲区大小",
     "detection_control": "端到端集成测试", "detection": 4,
     "action": "提升协议栈缓冲区并加丢包重传", "source_doc": "BT-FMEA-2023-G2"},
    {"part": _BT, "part_no": "BT-SW-03", "function": "固件在线升级",
     "failure_mode": "固件升级失败变砖", "failure_effect": "模块永久不可用",
     "severity": 9, "failure_cause": "升级中断且无回滚机制",
     "occurrence": 3, "prevention_control": "双分区 A/B 升级 + 版本回滚",
     "detection_control": "断点/断电升级测试", "detection": 3,
     "action": "升级流程增加完整性校验与回滚验证", "source_doc": "BT-FMEA-2023-G3"},
    # —— 连接器 / 焊接 ——
    {"part": _BT, "part_no": "BT-CON-01", "function": "模块与主板电气互连",
     "failure_mode": "BGA 焊球开裂", "failure_effect": "间歇性功能失效",
     "severity": 8, "failure_cause": "热循环应力集中",
     "occurrence": 3, "prevention_control": "PCB 布局减小热失配应力",
     "detection_control": "X-Ray 焊点检测", "detection": 4,
     "action": "增加温度循环可靠性试验", "source_doc": "BT-FMEA-2022-H1"},
    {"part": _BT, "part_no": "BT-CON-02", "function": "天线馈线互连",
     "failure_mode": "馈线走线阻抗不连续", "failure_effect": "回波损耗增大，功率反射",
     "severity": 5, "failure_cause": "走线宽度在过孔处突变",
     "occurrence": 4, "prevention_control": "50Ω 阻抗受控走线与过孔补偿",
     "detection_control": "TDR 阻抗测试", "detection": 4,
     "action": "把阻抗连续性纳入 Layout 复核", "source_doc": "BT-FMEA-2021-H3"},
    # —— EMC / 屏蔽 ——
    {"part": _BT, "part_no": "BT-EMC-01", "function": "抑制向外电磁辐射",
     "failure_mode": "屏蔽罩接地不良", "failure_effect": "EMI 辐射超标，影响其他模块",
     "severity": 5, "failure_cause": "屏蔽罩焊点虚接或接地过孔不足",
     "occurrence": 4, "prevention_control": "增加接地过孔密度与焊点数量",
     "detection_control": "EMC 辐射发射测试", "detection": 3,
     "action": "屏蔽罩接地电阻纳入出厂抽检", "source_doc": "BT-FMEA-2022-J1"},
    {"part": _BT, "part_no": "BT-EMC-02", "function": "抵抗外部电磁干扰",
     "failure_mode": "射频前端受外部干扰失敏", "failure_effect": "通信中断或误码率升高",
     "severity": 7, "failure_cause": "邻近大电流走线耦合",
     "occurrence": 3, "prevention_control": "射频区域与大电流走线物理隔离",
     "detection_control": "EMC 抗扰度测试", "detection": 4,
     "action": "布局规范中固化隔离间距要求", "source_doc": "BT-FMEA-2022-J2"},
]


# ---------------- 写入 ----------------

def main() -> int:
    conn = db.get_conn()
    now = time.time()

    # 1) S/O/D 准则
    n_sod = 0
    for dim, items in SOD_CRITERIA.items():
        for score, criterion in items:
            cur = conn.execute(
                "INSERT INTO fmea_sod_criteria(dimension, score, criterion,"
                " created_at) VALUES(?,?,?,?)"
                " ON CONFLICT (dimension, score) DO NOTHING",
                (dim, score, criterion, now))
            n_sod += cur.rowcount
    conn.commit()

    # 2) AP 矩阵（1000 格）
    n_ap = 0
    for s in range(1, 11):
        for o in range(1, 11):
            for d in range(1, 11):
                cur = conn.execute(
                    "INSERT INTO fmea_ap_matrix(severity, occurrence, detection,"
                    " ap, created_at) VALUES(?,?,?,?,?)"
                    " ON CONFLICT (severity, occurrence, detection) DO NOTHING",
                    (s, o, d, ap_of(s, o, d), now))
                n_ap += cur.rowcount
    conn.commit()

    # 3) 历史 FMEA 案例（按 part+failure_mode 去重）
    n_case = 0
    for r in _FMEA_ROWS:
        exists = conn.execute(
            "SELECT 1 FROM fmea_cases WHERE part=? AND failure_mode=?",
            (r["part"], r["failure_mode"])).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO fmea_cases(part, part_no, function, failure_mode,"
            " failure_effect, severity, failure_cause, occurrence,"
            " prevention_control, detection_control, detection, ap, action,"
            " source_doc, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["part"], r["part_no"], r["function"], r["failure_mode"],
             r["failure_effect"], r["severity"], r["failure_cause"],
             r["occurrence"], r["prevention_control"], r["detection_control"],
             r["detection"], ap_of(r["severity"], r["occurrence"], r["detection"]),
             r["action"], r["source_doc"], now))
        n_case += 1
    conn.commit()

    # 汇总
    tot_sod = conn.execute("SELECT COUNT(*) c FROM fmea_sod_criteria").fetchone()["c"]
    tot_ap = conn.execute("SELECT COUNT(*) c FROM fmea_ap_matrix").fetchone()["c"]
    tot_case = conn.execute("SELECT COUNT(*) c FROM fmea_cases").fetchone()["c"]
    parts = [r["part_no"] for r in conn.execute(
        "SELECT DISTINCT part_no FROM fmea_cases ORDER BY part_no").fetchall()]

    print(f"[1] S/O/D 准则：新增 {n_sod}，库中共 {tot_sod} 条（期望 30）")
    print(f"[2] AP 矩阵：新增 {n_ap}，库中共 {tot_ap} 格（期望 1000）")
    print(f"[3] 历史 FMEA：新增 {n_case}，库中共 {tot_case} 条")
    print(f"    子系统编号：{'、'.join(parts)}")
    ok = tot_sod == 30 and tot_ap == 1000 and tot_case >= 10
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
