# -*- coding: utf-8 -*-
"""给能力型数字人装配「反应式循环」：执行类动作 + 安全边界本体 + 打开 reactive 开关。

能力型数字人（写→测→改核心闭环）：
  代码工程师(3)：run_test / run_code / read_file / write_file
  测试工程师(5)：run_test
  调试工程师(6)：run_test / read_file

动作装配走确定性代码（不依赖 LLM 提名），安全边界作为本体段装配进去，
让数字人在反应式循环里「知道」自己的动作边界 + 沙箱安全铁律。

用法：backend 目录下
    python train_reactive.py
"""
import json

from app import db, actions, identity, trainer

# 三个能力型数字人的动作白名单（builtin_name -> 动作中文名）
REACTIVE_PLAN = {
    3: {  # 代码工程师
        "run_test": "运行测试",
        "run_code": "运行代码",
        "read_file": "读取文件",
        "write_file": "写入文件",
    },
    5: {  # 测试工程师
        "run_test": "运行测试",
    },
    6: {  # 调试工程师
        "run_test": "运行测试",
        "read_file": "读取文件",
    },
}

# 安全边界本体（每个能力型数字人装配同一套沙箱安全铁律）
SAFETY_BOUNDARY = [
    {
        "kind": "规则",
        "name": "沙箱安全边界",
        "definition": "所有代码执行都在一次性隔离沙箱中：禁网、只读根文件系统、无特权、"
                      "内存 256MB/CPU 1 核/PID 上限 64。数字人无法触及宿主文件系统或外网。",
    },
    {
        "kind": "规则",
        "name": "执行动作白名单",
        "definition": "只能调用本人已声明且审批通过的动作；未声明的动作会被 guard 拒绝。"
                      "执行前 guard 确定性裁决（入参必填 + 长度封顶），越权调用一律拒绝。",
    },
    {
        "kind": "流程",
        "name": "写测改循环",
        "definition": "写代码 → run_test 跑测试 → 观察绿/红 → 红则带失败堆栈修正 → 再跑，"
                      "直到绿或达轮数上限。终止靠确定性判定（测试通过），不靠自我宣称完成。",
    },
]


def add_action(conn, identity_id, builtin_name) -> bool:
    """装配一个 builtin 动作（幂等，直接 approved——用户显式配置的意图）。"""
    meta = actions.BUILTIN_ACTIONS[builtin_name]
    exists = conn.execute(
        "SELECT id, status FROM persona_actions WHERE identity_id=? AND name=?",
        (identity_id, meta["name"])).fetchone()
    if exists:
        if exists["status"] != "approved":
            conn.execute("UPDATE persona_actions SET status='approved' WHERE id=?",
                         (exists["id"],))
            conn.commit()
        return False  # 已存在，未新增
    conn.execute(
        "INSERT INTO persona_actions(identity_id, name, description,"
        " input_schema, kind, mcp_server_id, mcp_tool_name, builtin_name,"
        " status, created_at) VALUES(?,?,?,?, 'builtin', NULL, NULL, ?,"
        " 'approved', ?)",
        (identity_id, meta["name"], meta["description"],
         json.dumps(meta["input_schema"], ensure_ascii=False),
         builtin_name, db.now()))
    conn.commit()
    return True


def main():
    conn = db.get_conn()
    total_actions = 0
    total_ontology = 0

    for identity_id, action_map in REACTIVE_PLAN.items():
        row = conn.execute("SELECT name FROM identities WHERE id=?",
                           (identity_id,)).fetchone()
        if not row:
            print(f"  [跳过] 数字人 #{identity_id} 不存在")
            continue
        print(f"\n=== 数字人 #{identity_id} {row['name']} ===")

        # 1. 打开 reactive 开关
        identity.update_identity(conn, identity_id, {"reactive": True})
        print(f"  reactive 开关：已打开")

        # 2. 装配执行类动作（确定性代码，直接 approved）
        for builtin_name, name in action_map.items():
            added = add_action(conn, identity_id, builtin_name)
            total_actions += 1 if added else 0
            print(f"  动作：{name}（{builtin_name}）{'新增' if added else '已存在'}")

        # 3. 装配安全边界本体
        for s in SAFETY_BOUNDARY:
            added = trainer.add_ontology(conn, identity_id, s["kind"],
                                         s["name"], s["definition"],
                                         note="训练师：反应式循环安全边界")
            total_ontology += 1 if added else 0

    print(f"\n=== 完成：新增动作 {total_actions}，新增本体 {total_ontology} ===")
    # 验证
    print("\n=== 验证装配结果 ===")
    for identity_id in REACTIVE_PLAN:
        acts = actions.approved_actions(conn, identity_id)
        ont = trainer.list_ontology(conn, identity_id)
        name = conn.execute("SELECT name, reactive FROM identities WHERE id=?",
                            (identity_id,)).fetchone()
        print(f"  #{identity_id} {name['name']} reactive={name['reactive']}"
              f" | 动作 {len(acts)} 个 | 本体 {len(ont)} 段")


if __name__ == "__main__":
    main()
