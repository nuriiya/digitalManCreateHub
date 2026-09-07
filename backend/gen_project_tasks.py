# -*- coding: utf-8 -*-
"""生成「项目相关」能力题（贴合 FastAPI/pgvector/RAG 技术栈的纯函数编码题）。

设计理念（方案 B）：
  HumanEval 是通用算法题，DeepSeek/GLM 秒杀（基线 100%），测不出训练效果。
  项目相关题考的是「项目真实会出现的纯函数」，且 docstring 只给意图、不给
  精确约定 —— 裸模型会漏掉「去括号」「闭集校验」「重叠切块」等细节，只有
  装配了本体的数字人才能全对，这样「提升率」才有区分度。

每道题：prompt(给数字人的签名+意图) + test(check(candidate) 隐藏断言) +
        canonical_solution(参考解，仅自检用，绝不喂给数字人)。

用法：backend 目录下
    python gen_project_tasks.py            # 生成 + 自检 + 导入
"""
import json
import re
from pathlib import Path

from app import db, capability

DATA = Path(__file__).resolve().parent / "data"
DST = DATA / "capability_tasks_project.json"

# ---------------------------------------------------------------- 题目定义

TASKS = [
    {
        "id": "proj_normalize_name",
        "category": "data_cleaning",
        "persona_role": "code_engineer",
        "prompt": "def normalize_name(name: str) -> str:\n"
                  "    \"\"\"规范化实体名称。\n"
                  "    用于本体实体去重前的名称标准化，规则是：\n"
                  "    - 转小写\n"
                  "    - 去掉括号及括号内的内容\n"
                  "    - 去掉所有空白字符\n"
                  "    \"\"\"\n",
        "entry_point": "normalize_name",
        "test": "def check(candidate):\n"
                "    assert candidate('Foo Bar') == 'foobar'\n"
                "    assert candidate('  Hello (World)  ') == 'hello'\n"
                "    assert candidate('ABC（中文）DEF') == 'abcdef'\n"
                "    assert candidate('a\\tb\\nc') == 'abc'\n"
                "    assert candidate('NO_PAREN') == 'no_paren'\n",
        "canonical_solution": "def normalize_name(name: str) -> str:\n"
                             "    s = re.sub(r'[\\(（].*?[\\)）]', '', name)\n"
                             "    s = re.sub(r'\\s+', '', s)\n"
                             "    return s.lower()\n",
        "source": "project:ontology-name-norm",
    },
    {
        "id": "proj_parse_dsn",
        "category": "db",
        "persona_role": "code_engineer",
        "prompt": "def parse_dsn(dsn: str) -> dict:\n"
                  "    \"\"\"解析 PostgreSQL 连接串，返回 dict。\n"
                  "    格式：postgresql://user:password@host:port/dbname\n"
                  "    端口缺省时默认 5432，密码可为空。\n"
                  "    \"\"\"\n",
        "entry_point": "parse_dsn",
        "test": "def check(candidate):\n"
                "    assert candidate('postgresql://postgres:secret@localhost:5432/rag') == \\\n"
                "        {'user': 'postgres', 'password': 'secret', 'host': 'localhost', 'port': 5432, 'dbname': 'rag'}\n"
                "    assert candidate('postgresql://u:p@h/db') == \\\n"
                "        {'user': 'u', 'password': 'p', 'host': 'h', 'port': 5432, 'dbname': 'db'}\n"
                "    assert candidate('postgresql://u@h:5555/db') == \\\n"
                "        {'user': 'u', 'password': '', 'host': 'h', 'port': 5555, 'dbname': 'db'}\n",
        "canonical_solution": "def parse_dsn(dsn: str) -> dict:\n"
                             "    body = dsn.split('://', 1)[1]\n"
                             "    auth, rest = body.rsplit('@', 1)\n"
                             "    user, _, password = auth.partition(':')\n"
                             "    hostport, _, dbname = rest.partition('/')\n"
                             "    if ':' in hostport:\n"
                             "        host, port = hostport.rsplit(':', 1)\n"
                             "    else:\n"
                             "        host, port = hostport, '5432'\n"
                             "    return {'user': user, 'password': password, 'host': host,\n"
                             "            'port': int(port), 'dbname': dbname}\n",
        "source": "project:db-dsn",
    },
    {
        "id": "proj_paginate",
        "category": "web",
        "persona_role": "code_engineer",
        "prompt": "def paginate(total: int, page: int, page_size: int) -> dict:\n"
                  "    \"\"\"计算分页信息。\n"
                  "    page 从 1 开始；返回 {offset, has_next}。\n"
                  "    \"\"\"\n",
        "entry_point": "paginate",
        "test": "def check(candidate):\n"
                "    assert candidate(100, 1, 20) == {'offset': 0, 'has_next': True}\n"
                "    assert candidate(100, 5, 20) == {'offset': 80, 'has_next': False}\n"
                "    assert candidate(61, 3, 20) == {'offset': 40, 'has_next': True}\n"
                "    assert candidate(40, 3, 20) == {'offset': 40, 'has_next': False}\n",
        "canonical_solution": "def paginate(total: int, page: int, page_size: int) -> dict:\n"
                             "    offset = (page - 1) * page_size\n"
                             "    return {'offset': offset, 'has_next': offset + page_size < total}\n",
        "source": "project:web-paginate",
    },
    {
        "id": "proj_dedup_tags",
        "category": "data_cleaning",
        "persona_role": "code_engineer",
        "prompt": "def dedup_tags(tags: list) -> list:\n"
                  "    \"\"\"标签去重。\n"
                  "    保持首次出现的顺序；忽略大小写差异和首尾空白。\n"
                  "    \"\"\"\n",
        "entry_point": "dedup_tags",
        "test": "def check(candidate):\n"
                "    assert candidate(['规则', '法律', '规则', ' 规则 ']) == ['规则', '法律']\n"
                "    assert candidate(['A', 'a', 'B', 'b']) == ['A', 'B']\n"
                "    assert candidate([' x ', 'X', ' y']) == ['x', 'y']\n"
                "    assert candidate([]) == []\n",
        "canonical_solution": "def dedup_tags(tags: list) -> list:\n"
                             "    seen = set()\n"
                             "    out = []\n"
                             "    for t in tags:\n"
                             "        k = t.strip().lower()\n"
                             "        if k not in seen:\n"
                             "            seen.add(k)\n"
                             "            out.append(t.strip())\n"
                             "    return out\n",
        "source": "project:tag-dedup",
    },
    {
        "id": "proj_jsonb_get",
        "category": "db",
        "persona_role": "code_engineer",
        "prompt": "def jsonb_get(data, path: str, default=None):\n"
                  "    \"\"\"安全读取嵌套 JSON 字段。\n"
                  "    路径用点分隔（如 'a.b.c'）；任一层缺失或不是 dict 都返回 default。\n"
                  "    \"\"\"\n",
        "entry_point": "jsonb_get",
        "test": "def check(candidate):\n"
                "    assert candidate({'a': {'b': {'c': 1}}}, 'a.b.c') == 1\n"
                "    assert candidate({'a': {'b': 2}}, 'a.b.c') is None\n"
                "    assert candidate({'a': 1}, 'a.b') is None\n"
                "    assert candidate({'a': {'b': 3}}, 'a.b', -1) == 3\n"
                "    assert candidate({'a': {'b': None}}, 'a.b.c', 'x') == 'x'\n",
        "canonical_solution": "def jsonb_get(data, path: str, default=None):\n"
                             "    cur = data\n"
                             "    for key in path.split('.'):\n"
                             "        if not isinstance(cur, dict) or key not in cur:\n"
                             "            return default\n"
                             "        cur = cur[key]\n"
                             "    return cur\n",
        "source": "project:jsonb-safe-get",
    },
    {
        "id": "proj_chunk_overlap",
        "category": "rag",
        "persona_role": "code_engineer",
        "prompt": "def chunk_overlap(text: str, max_len: int, overlap: int) -> list:\n"
                  "    \"\"\"把文本切成有重叠的分段。\n"
                  "    每段不超过 max_len 字符；相邻段之间重叠 overlap 个字符；\n"
                  "    最后一段即使很短也要保留。\n"
                  "    \"\"\"\n",
        "entry_point": "chunk_overlap",
        "test": "def check(candidate):\n"
                "    assert candidate('abcdefgh', 4, 2) == ['abcd', 'cdef', 'efgh']\n"
                "    assert candidate('abcdefgh', 4, 0) == ['abcd', 'efgh']\n"
                "    assert candidate('abc', 10, 2) == ['abc']\n"
                "    assert candidate('', 5, 2) == []\n",
        "canonical_solution": "def chunk_overlap(text: str, max_len: int, overlap: int) -> list:\n"
                             "    if not text:\n"
                             "        return []\n"
                             "    out = []\n"
                             "    step = max_len - overlap\n"
                             "    i = 0\n"
                             "    while i < len(text):\n"
                             "        out.append(text[i:i + max_len])\n"
                             "        if i + max_len >= len(text):\n"
                             "            break\n"
                             "        i += step\n"
                             "    return out\n",
        "source": "project:rag-chunk",
    },
    {
        "id": "proj_cosine",
        "category": "vector",
        "persona_role": "code_engineer",
        "prompt": "def cosine(a: list, b: list) -> float:\n"
                  "    \"\"\"两个等长向量的余弦相似度（纯 Python，不用 numpy）。\n"
                  "    任一向量模长为 0 时返回 0.0。\n"
                  "    \"\"\"\n",
        "entry_point": "cosine",
        "test": "def check(candidate):\n"
                "    assert abs(candidate([1, 0], [1, 0]) - 1.0) < 1e-9\n"
                "    assert abs(candidate([1, 0], [0, 1])) < 1e-9\n"
                "    assert candidate([0, 0], [1, 1]) == 0.0\n"
                "    assert abs(candidate([1, 1], [1, 1]) - 1.0) < 1e-9\n",
        "canonical_solution": "def cosine(a: list, b: list) -> float:\n"
                             "    import math\n"
                             "    dot = sum(x * y for x, y in zip(a, b))\n"
                             "    na = math.sqrt(sum(x * x for x in a))\n"
                             "    nb = math.sqrt(sum(y * y for y in b))\n"
                             "    if na == 0 or nb == 0:\n"
                             "        return 0.0\n"
                             "    return dot / (na * nb)\n",
        "source": "project:pgvector-cosine",
    },
    {
        "id": "proj_validate_tags",
        "category": "validation",
        "persona_role": "code_engineer",
        "prompt": "def validate_tags(tags: list, allowed: list) -> list:\n"
                  "    \"\"\"闭集校验：只保留在 allowed 里的标签，预设外的丢弃。\n"
                  "    返回去重后的合法标签列表，保持顺序。\n"
                  "    \"\"\"\n",
        "entry_point": "validate_tags",
        "test": "def check(candidate):\n"
                "    ALLOWED = ['规则', '法律', '专业知识', '术语概念', '数据指标', '案例示例']\n"
                "    assert candidate(['规则', '乱七八糟', '法律', '规则'], ALLOWED) == ['规则', '法律']\n"
                "    assert candidate(['未知'], ALLOWED) == []\n"
                "    assert candidate(['数据指标', '案例示例'], ALLOWED) == ['数据指标', '案例示例']\n",
        "canonical_solution": "def validate_tags(tags: list, allowed: list) -> list:\n"
                             "    aset = set(allowed)\n"
                             "    seen = set()\n"
                             "    out = []\n"
                             "    for t in tags:\n"
                             "        if t in aset and t not in seen:\n"
                             "            seen.add(t)\n"
                             "            out.append(t)\n"
                             "    return out\n",
        "source": "project:closed-set-tags",
    },
    {
        "id": "proj_safe_truncate",
        "category": "text",
        "persona_role": "code_engineer",
        "prompt": "def safe_truncate(text: str, max_chars: int, ellipsis: str = '...') -> str:\n"
                  "    \"\"\"按字符数截断（不按字节，中文也要完整）。\n"
                  "    超长时截断并追加 ellipsis；不超长原样返回。\n"
                  "    \"\"\"\n",
        "entry_point": "safe_truncate",
        "test": "def check(candidate):\n"
                "    assert candidate('hello', 10) == 'hello'\n"
                "    assert candidate('hello world', 5) == 'hello...'\n"
                "    assert candidate('你好世界', 2) == '你好...'\n"
                "    assert candidate('abcdef', 3, '…') == 'abc…'\n",
        "canonical_solution": "def safe_truncate(text: str, max_chars: int, ellipsis: str = '...') -> str:\n"
                             "    if len(text) <= max_chars:\n"
                             "        return text\n"
                             "    return text[:max_chars] + ellipsis\n",
        "source": "project:truncate",
    },
    {
        "id": "proj_extract_code",
        "category": "text",
        "persona_role": "code_engineer",
        "prompt": "def extract_code(reply: str) -> str:\n"
                  "    \"\"\"从模型回答里提取纯 Python 代码。\n"
                  "    优先提取 ```python ... ``` 代码块；没有代码块则从首个 def/from/import\n"
                  "    开头的行开始截取到结尾；都没有则返回原文。\n"
                  "    \"\"\"\n",
        "entry_point": "extract_code",
        "test": "def check(candidate):\n"
                "    assert candidate('解释一下\\n```python\\ndef f():\\n    return 1\\n```\\n以上') == \\\n"
                "        'def f():\\n    return 1'\n"
                "    assert candidate('说明\\ndef f():\\n    return 1\\n') == 'def f():\\n    return 1'\n"
                "    assert candidate('from x import y') == 'from x import y'\n",
        "canonical_solution": "def extract_code(reply: str) -> str:\n"
                             "    m = re.search(r'```(?:python|py)?\\s*\\n(.*?)```', reply, re.DOTALL)\n"
                             "    if m:\n"
                             "        return m.group(1).strip()\n"
                             "    lines = reply.splitlines()\n"
                             "    for i, line in enumerate(lines):\n"
                             "        if line.startswith(('def ', 'from ', 'import ', 'class ')):\n"
                             "            return '\\n'.join(lines[i:]).strip()\n"
                             "    return reply.strip()\n",
        "source": "project:extract-code-block",
    },
    {
        "id": "proj_merge_summaries",
        "category": "rag",
        "persona_role": "code_engineer",
        "prompt": "def merge_summaries(parts: list) -> str:\n"
                  "    \"\"\"把多段摘要聚合成整篇摘要。\n"
                  "    过滤空白段；去除内容重复的段（忽略空白和标点差异）；\n"
                  "    用换行连接剩余段。\n"
                  "    \"\"\"\n",
        "entry_point": "merge_summaries",
        "test": "def check(candidate):\n"
                "    assert candidate(['a', 'b']) == 'a\\nb'\n"
                "    assert candidate(['a', 'a', '']) == 'a'\n"
                "    assert candidate(['  a  ', 'a']) == 'a'\n"
                "    assert candidate(['hello', 'world', 'hello']) == 'hello\\nworld'\n",
        "canonical_solution": "def merge_summaries(parts: list) -> str:\n"
                             "    seen = set()\n"
                             "    out = []\n"
                             "    for p in parts:\n"
                             "        s = p.strip()\n"
                             "        if not s:\n"
                             "            continue\n"
                             "        k = re.sub(r'[^\\w]', '', s)\n"
                             "        if k in seen:\n"
                             "            continue\n"
                             "        seen.add(k)\n"
                             "        out.append(s)\n"
                             "    return '\\n'.join(out)\n",
        "source": "project:rag-merge-summary",
    },
    {
        "id": "proj_topk",
        "category": "vector",
        "persona_role": "code_engineer",
        "prompt": "def topk(items: list, k: int) -> list:\n"
                  "    \"\"\"按分数取 top-k。\n"
                  "    items 是 (label, score) 元组列表；返回 score 降序的前 k 个 label；\n"
                  "    分数相同时保持原顺序；k 超过总数返回全部。\n"
                  "    \"\"\"\n",
        "entry_point": "topk",
        "test": "def check(candidate):\n"
                "    assert candidate([('a', 0.9), ('b', 0.5), ('c', 0.8)], 2) == ['a', 'c']\n"
                "    assert candidate([('a', 0.5), ('b', 0.5)], 1) == ['a']\n"
                "    assert candidate([('a', 1.0)], 5) == ['a']\n",
        "canonical_solution": "def topk(items: list, k: int) -> list:\n"
                             "    ranked = sorted(items, key=lambda x: -x[1])\n"
                             "    return [label for label, _ in ranked[:k]]\n",
        "source": "project:retrieval-topk",
    },
]


def self_check() -> bool:
    """用参考解跑一遍 test，确认题目本身正确（参考解必须全绿）。"""
    ns = {"re": re}
    all_ok = True
    for t in TASKS:
        code = t["canonical_solution"] + "\n\n" + t["test"] + \
               f"\ncheck({t['entry_point']})\n"
        try:
            exec(code, ns)
            print(f"  [OK] {t['id']}")
        except Exception as e:  # noqa: BLE001
            all_ok = False
            print(f"  [FAIL] {t['id']}: {e}")
    return all_ok


def main():
    print(f"=== 自检 {len(TASKS)} 道参考解 ===")
    if not self_check():
        print("自检未通过，不写入。")
        return 1
    DST.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 写入 {DST} ===")
    conn = db.get_conn()
    added = capability.import_tasks(conn, TASKS)
    print(f"导入 {added} 道项目能力题（总 {len(capability.list_tasks(conn))} 道）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
