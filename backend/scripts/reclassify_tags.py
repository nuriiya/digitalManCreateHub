# -*- coding: utf-8 -*-
"""一次性收敛现有候选的领域标签到固定 6 类闭集。

历史数据由 LLM 自由打标（AI智能体/计算化学/材料科学…30+ 自定义标签），
本轮把标签体系收紧为闭集 6 类。此脚本用 GLM 5.2 把每个候选重新归类到
TAG_PRESET，覆盖 candidates.tags。

用法（backend 目录下）：
    ../.venv/Scripts/python.exe -X utf8 scripts/reclassify_tags.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import db, llm  # noqa: E402
from app.ontology import TAG_PRESET  # noqa: E402

BATCH = 40
PRESET_STR = " / ".join(sorted(TAG_PRESET))


def _prompt(batch: list[dict]) -> str:
    lines = []
    for c in batch:
        lines.append(f"- [{c['id']}] {c['name']}：{c['definition'] or '（无定义）'}")
    return (
        "你是本体标签归类器，只归类、不裁决。以下是候选本体实体，"
        f"请把每个实体归类到固定分类（闭集）：{PRESET_STR}。\n"
        "每个实体标注 1~3 个最贴切的分类标签。规则=制度/流程规范/合规要求；"
        "法律=法条/法规/司法解释；专业知识=行业方法论/领域原理；"
        "术语概念=基础定义；数据指标=量化口径；案例示例=判例/范例。\n\n"
        + "\n".join(lines) + "\n\n"
        "严格按 JSON 输出：{\"items\": [{\"id\": 编号, \"tags\": [\"专业知识\", \"术语概念\"]}]}\n"
        "id 必须是列表里出现的编号，tags 只能取上述 6 个分类，不得自拟。"
    )


def reclassify() -> None:
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, definition FROM candidates WHERE kind='entity'"
        " ORDER BY id").fetchall()]
    print(f"待收敛候选: {len(rows)}")
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            reply = llm.chat2([{"role": "user", "content": _prompt(batch)}])
            data = llm.extract_json(reply)
        except Exception as e:  # noqa: BLE001
            print(f"批次 {i // BATCH + 1} GLM 失败: {e}")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            print(f"批次 {i // BATCH + 1} 解析失败: {reply[:120]}")
            continue
        for it in data["items"]:
            if not isinstance(it, dict):
                continue
            try:
                cid = int(it.get("id"))
            except (TypeError, ValueError):
                continue
            tags = [t for t in (it.get("tags") or []) if t in TAG_PRESET]
            conn.execute("UPDATE candidates SET tags=? WHERE id=?",
                         (tags, cid))
            done += 1
        conn.commit()
        print(f"批次 {i // BATCH + 1}/{((len(rows) - 1) // BATCH) + 1} 完成")
    conn.commit()
    db.reset_conn()
    print(f"收敛完成：更新 {done}/{len(rows)} 个候选")


if __name__ == "__main__":
    reclassify()
