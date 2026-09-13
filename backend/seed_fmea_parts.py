# -*- coding: utf-8 -*-
"""部件知识库 seed（design §15.7）——待分析产品的**子系统清单**。

为什么需要它：DFMEA 的第一步是「这个产品由哪些部件构成」。历史 FMEA 库是
**蓝牙模块**的（17 条），WiFi 模块在库里没有直接记录 —— 所以链路必须先
「自主搜索部件」拿到子系统清单，再对每个子系统去历史库找**同类案例**做类比
推导。这也正是本表存在的意义。

**本表只给「有哪些部件、各干什么、什么工况」，刻意不给失效模式**：
失效模式必须由 DFMEA 工程师自行推导（领域推理 + 同类案例类比），
否则这一环就退化成「读表抄答案」，考核也就失去意义。

`note` 里写的是**可类比的历史部件编号族**（如"对应历史库 BT-ANT-*"）——
这是"根据现有案例推导"的显式线索，只指引去查哪一族历史案例，
不透露失效模式与任何 S/O/D 取值。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python seed_fmea_parts.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402

PRODUCT = "WiFi 模块"

#: 子系统清单：subsystem / code / function / condition / keywords / 可类比历史族
PARTS: list[dict] = [
    {"subsystem": "射频天线", "code": "WF-ANT-01",
     "function": "2.4GHz 与 5GHz 双频段电磁波辐射与接收",
     "condition": "整机内置天线，靠近金属中框与电池，用户手握遮挡",
     "keywords": ["天线", "辐射效率", "双频", "遮挡", "SAR"],
     "note": "对应历史库 BT-ANT-01（同为内置天线辐射链）"},
    {"subsystem": "天线馈电与接地", "code": "WF-ANT-02",
     "function": "天线馈电走线与参考地连接，保证馈点阻抗与回流路径稳定",
     "condition": "整机堆叠装配，地平面与螺钉压接",
     "keywords": ["馈电", "接地", "地平面", "回流", "接触阻抗"],
     "note": "对应历史库 BT-ANT-02（同为馈电与接地链）"},
    {"subsystem": "射频功率放大器", "code": "WF-RF-01",
     "function": "把射频信号放大到天线口所需功率",
     "condition": "高功率发射态长期工作，温升显著，负载失配风险",
     "keywords": ["功率放大器", "PA", "饱和功率", "温升", "驻波"],
     "note": "对应历史库 BT-RF-01（同为发射功率放大链）"},
    {"subsystem": "射频接收前端", "code": "WF-RF-02",
     "function": "低噪声放大与接收频带选择，保证接收灵敏度",
     "condition": "弱信号接收态，邻频强干扰共存的整机环境",
     "keywords": ["低噪放", "LNA", "灵敏度", "邻频", "阻塞"],
     "note": "对应历史库 BT-RF-02 / BT-RF-03（接收频带选择与低噪放链）"},
    {"subsystem": "收发切换与滤波", "code": "WF-RF-03",
     "function": "时分收发切换与射频滤波，抑制带外与谐波",
     "condition": "高占空比收发切换，与蜂窝频段共存",
     "keywords": ["收发切换", "滤波器", "谐波", "带外", "隔离度"],
     "note": "对应历史库 BT-RF-02（同为频带选择链）"},
    {"subsystem": "射频供电", "code": "WF-PMU-01",
     "function": "为射频前端与基带提供稳定低纹波电源",
     "condition": "电池供电，发射瞬间大电流脉冲，纹波敏感",
     "keywords": ["供电", "LDO", "纹波", "压降", "瞬态"],
     "note": "对应历史库 BT-PMU-01（同为射频与基带供电链）"},
    {"subsystem": "电源保护", "code": "WF-PMU-02",
     "function": "过流、过温与浪涌保护，防止异常工况损坏",
     "condition": "异常短路与浪涌冲击工况",
     "keywords": ["过流", "过温", "保护", "浪涌", "熔断"],
     "note": "对应历史库 BT-PMU-02（同为过流与热保护链）"},
    {"subsystem": "参考时钟", "code": "WF-CLK-01",
     "function": "为射频载波与基带数字部分提供基准时钟",
     "condition": "整机温度变化范围大，晶振老化与频偏",
     "keywords": ["晶振", "参考时钟", "频偏", "相位噪声", "老化"],
     "note": "对应历史库 BT-CLK-01（同为射频载波基准链）"},
    {"subsystem": "基带与固件", "code": "WF-SW-01",
     "function": "基带处理与固件调度，维持连接状态机运行",
     "condition": "长时间运行，内存与任务调度受限",
     "keywords": ["固件", "基带", "状态机", "内存", "复位"],
     "note": "对应历史库 BT-SW-01（同为连接状态管理链）"},
    {"subsystem": "连接与漫游管理", "code": "WF-SW-02",
     "function": "SSID 扫描、认证与漫游切换管理",
     "condition": "多 AP 环境，信号强度快速变化",
     "keywords": ["漫游", "认证", "扫描", "SSID", "切换"],
     "note": "对应历史库 BT-SW-02（同为服务发现/连接管理链）"},
    {"subsystem": "固件升级", "code": "WF-SW-03",
     "function": "固件在线升级与版本回滚",
     "condition": "升级过程断电或链路中断",
     "keywords": ["固件升级", "OTA", "回滚", "断电", "校验"],
     "note": "对应历史库 BT-SW-03（同为固件在线升级链）"},
    {"subsystem": "射频屏蔽与 EMC", "code": "WF-EMC-01",
     "function": "屏蔽罩抑制对外辐射并抵抗外部电磁干扰",
     "condition": "整机紧凑布局，多射频共存互扰",
     "keywords": ["屏蔽", "EMC", "辐射", "抗扰", "互扰"],
     "note": "对应历史库 BT-EMC-01 / BT-EMC-02（同为屏蔽与抗扰链）"},
    {"subsystem": "模块互连", "code": "WF-CON-01",
     "function": "模块与主板的电气互连与馈线连接",
     "condition": "跌落与温度循环下的焊点应力",
     "keywords": ["连接器", "焊点", "馈线", "接触", "振动"],
     "note": "对应历史库 BT-CON-01 / BT-CON-02（同为电气互连与馈线链）"},
]


def ensure(conn) -> tuple[int, int]:
    """幂等写入：按 (product, subsystem) 去重。返回 (新增, 总数)。"""
    added = 0
    for p in PARTS:
        exists = conn.execute(
            "SELECT id FROM fmea_parts WHERE product=? AND subsystem=?",
            (PRODUCT, p["subsystem"])).fetchone()
        if exists:
            conn.execute(
                "UPDATE fmea_parts SET function=?, condition=?, keywords=?, note=?"
                " WHERE id=?",
                (p["function"], p["condition"], p["keywords"], p["note"],
                 exists["id"]))
            continue
        conn.execute(
            "INSERT INTO fmea_parts(product, subsystem, function, condition,"
            " keywords, note, created_at) VALUES(?,?,?,?,?,?,?)",
            (PRODUCT, p["subsystem"], p["function"], p["condition"],
             p["keywords"], p["note"], time.time()))
        added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM fmea_parts WHERE product=?",
                         (PRODUCT,)).fetchone()["c"]
    return added, total


def main() -> None:
    conn = db.get_conn()
    added, total = ensure(conn)
    print(f"[部件知识库] {PRODUCT}：新增 {added} 条 / 共 {total} 条")
    rows = conn.execute(
        "SELECT subsystem, note FROM fmea_parts WHERE product=? ORDER BY id",
        (PRODUCT,)).fetchall()
    for r in rows:
        print(f"    {r['subsystem']:<10} ← {r['note']}")


if __name__ == "__main__":
    main()
