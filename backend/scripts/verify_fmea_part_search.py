# -*- coding: utf-8 -*-
"""验证「搜索部件清单」动作（design §15.7）。

关键判据：**它只给部件与类比线索，不给失效模式** —— 否则考核失去意义。

跑法（容器内）：
  docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
    python scripts/verify_fmea_part_search.py
"""
import sys

sys.path.insert(0, "/app/backend")

from app import actions, db, fmea  # noqa: E402

fails: list[str] = []


def check(label, ok, extra=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f"   {extra}" if extra else ""))
    if not ok:
        fails.append(label)


conn = db.get_conn()

print("[1] 不传 product → 列出可搜索的产品")
r = fmea.part_search(conn, "")
check("ok", r.get("ok"))
res = r["result"]
check("返回 products 列表且非空", len(res.get("products") or []) >= 1,
      f"{res.get('products')}")
check("products 含 WiFi 模块", "WiFi 模块" in (res.get("products") or []))

print("[2] 搜索 WiFi 模块部件清单")
r = fmea.part_search(conn, "WiFi 模块")
res = r["result"]
parts = res["parts"]
check("部件数 >= 12", len(parts) >= 12, f"count={len(parts)}")
check("每个部件都有功能与工况",
      all(p["function"] and p["condition"] for p in parts))
check("每个部件都有可类比历史族提示",
      all(p["analogy_hint"] for p in parts))
check("**不含失效模式**（只有部件知识）",
      all("failure_mode" not in p and "severity" not in p for p in parts))
subs = [p["subsystem"] for p in parts]
check("覆盖天线/PA/供电/时钟/固件/EMC 等关键子系统",
      any("天线" in s for s in subs) and any("功率放大" in s for s in subs)
      and any("供电" in s for s in subs) and any("时钟" in s for s in subs)
      and any("固件" in s for s in subs) and any("屏蔽" in s for s in subs),
      "、".join(subs))

print("[3] 历史库（现有案例）部件族")
hp = res["history_parts"]
check("返回历史部件族", len(hp) >= 5, f"{[(h['family'], h['cases']) for h in hp]}")
check("含 ANT / RF / PMU / CLK 族",
      {"ANT", "RF", "PMU", "CLK"} <= {h["family"] for h in hp})
check("history_total == 各族之和",
      res["history_total"] == sum(h["cases"] for h in hp),
      f"{res['history_total']}")

print("[4] query 过滤")
r = fmea.part_search(conn, "WiFi 模块", query="天线")
check("按关键词命中天线相关子系统",
      0 < len(r["result"]["parts"]) < len(parts),
      f"{[p['subsystem'] for p in r['result']['parts']]}")

print("[5] 经动作分发层调用（execute_builtin）")
r = actions.execute_builtin(conn, 0, "fmea_part_search",
                            {"product": "WiFi 模块"})
check("动作执行 ok", r.get("ok"), str(r.get("error") or ""))
check("结果含 parts", len(r.get("result", {}).get("parts") or []) >= 12)

print("[6] 动作已注册进注册表")
check("BUILTIN_ACTIONS 含 fmea_part_search",
      "fmea_part_search" in actions.BUILTIN_ACTIONS)
meta = actions.BUILTIN_ACTIONS.get("fmea_part_search", {})
check("schema required=[product]", meta.get("input_schema", {}).get("required") == ["product"])

print("[7] DFMEA 工程师已绑定该动作且为 approved")
row = conn.execute(
    "SELECT pa.status, pa.builtin_name FROM persona_actions pa"
    " JOIN identities i ON pa.identity_id=i.id"
    " WHERE i.name LIKE ? AND pa.builtin_name='fmea_part_search'",
    ("%DFMEA 工程师%",)).fetchone()
check("已绑定", row is not None)
check("状态 approved", bool(row) and row["status"] == "approved",
      row["status"] if row else "")

print("\nRESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
