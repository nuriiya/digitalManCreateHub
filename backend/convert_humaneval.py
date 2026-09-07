# -*- coding: utf-8 -*-
"""把 HumanEval JSONL 转成项目能力题库格式（capability_tasks.json）。

能力题 = 给数字人的任务(prompt) + 隐藏测试(test) + 判定入口(entry_point)。
test 是 `check(candidate)` 形式：把数字人生成的函数丢进去跑 assert，绿=通过。
canonical_solution 仅作参考，不喂给数字人（否则泄题）。

用法：backend 目录下
    python convert_humaneval.py [input.jsonl] [output.json]
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "HumanEval.jsonl"
DST = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA / "capability_tasks.json"


def convert() -> int:
    tasks = []
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            # HumanEval/123 -> humaneval_123
            sid = d["task_id"].split("/")[-1]
            tasks.append({
                "id": f"humaneval_{sid}",
                "category": "code_generation",
                "persona_role": "code_engineer",
                "prompt": d["prompt"],
                "entry_point": d["entry_point"],
                "test": d["test"],
                # 参考解，仅用于人工比对/回归，绝不喂给被测数字人
                "canonical_solution": d["canonical_solution"],
                "source": d["task_id"],
            })
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    return len(tasks)


if __name__ == "__main__":
    n = convert()
    print(f"转换完成：{n} 道能力题 -> {DST}")
