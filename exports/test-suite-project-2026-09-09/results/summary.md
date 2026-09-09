# 项目能力题套件 · qwen2.5:7b-32k 结果

> 数字人：代码工程师（identity 3，14 条本体注入，reactive）· provider=ollama qwen2.5:7b-32k · temperature=0 · 沙箱可执行验证 ≤3 轮
> 测试标准见 README-测试标准.md；原始往返见 results/<task>.json

| task | category | verdict | 轮次 | R1代码长 | 说明 |
|---|---|---|---|---|---|
| `proj_normalize_name` | data_cleaning | **fail** | 3 | 835 | fail→fail→fail |
| `proj_parse_dsn` | db | **fail** | 3 | 808 | fail→fail→fail |
| `proj_paginate` | web | **pass** | 1 | 347 | pass |
| `proj_dedup_tags` | data_cleaning | **pass** | 3 | 226 | fail→fail→pass |
| `proj_jsonb_get` | db | **pass** | 1 | 310 | pass |
| `proj_chunk_overlap` | rag | **fail** | 3 | 391 | fail→fail→fail |
| `proj_cosine` | vector | **pass** | 1 | 504 | pass |
| `proj_validate_tags` | validation | **pass** | 1 | 495 | pass |
| `proj_safe_truncate` | text | **pass** | 1 | 165 | pass |
| `proj_extract_code` | text | **pass** | 2 | 824 | fail→pass |
| `proj_merge_summaries` | rag | **pass** | 2 | 1006 | fail→pass |
| `proj_topk` | vector | **pass** | 1 | 460 | pass |

**总通过率：9/12（75%）· 首轮通过：6/12（50%）· 平均轮次：1.83**

## 未通过题分析

### proj_normalize_name（fail）
- R1 输出尾部: `~^^
  File "<string>", line 29, in run_test
    assert result == expected, f"Expected {expected}, got {result} for input '{input_name}'"
           ^^^^^^^^^^^^^^^^^^
AssertionError: Expected charlieengineer, got charlie engineer for input 'Charlie (Engineer)'`
- R2 输出尾部: `recent call last):
  File "<string>", line 36, in <module>
    check(normalize_name)
    ~~~~~^^^^^^^^^^^^^^^^
  File "<string>", line 31, in check
    assert candidate('ABC（中文）DEF') == 'abcdef'
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError`
- R3 输出尾部: `all last):
  File "<string>", line 18, in <module>
    check(normalize_name)
    ~~~~~^^^^^^^^^^^^^^^^
  File "<string>", line 12, in check
    assert candidate('  Hello (World)  ') == 'hello'
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError`
### proj_parse_dsn（fail）
- R1 输出尾部: `parse_dsn)
    ~~~~~^^^^^^^^^^^
  File "<string>", line 40, in check
    assert candidate('postgresql://u:p@h/db') == \
           ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<string>", line 15, in parse_dsn
    raise ValueError("DSN格式不正确")
ValueError: DSN格式不正确`
- R2 输出尾部: `stgresql://u@h:5555/db') == \
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        {'user': 'u', 'password': '', 'host': 'h', 'port': 5555, 'dbname': 'db'}
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError`
- R3 输出尾部: `      ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<string>", line 12, in parse_dsn
    port = int(host_port[1]) if len(host_port) > 1 else 5432
           ~~~^^^^^^^^^^^^^^
ValueError: invalid literal for int() with base 10: '5432/rag'`
### proj_chunk_overlap（fail）
- R1 输出尾部: `>", line 22, in <module>
    check(chunk_overlap)
    ~~~~~^^^^^^^^^^^^^^^
  File "<string>", line 16, in check
    assert candidate('abcdefgh', 4, 2) == ['abcd', 'cdef', 'efgh']
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError`
- R2 输出尾部: `>", line 24, in <module>
    check(chunk_overlap)
    ~~~~~^^^^^^^^^^^^^^^
  File "<string>", line 18, in check
    assert candidate('abcdefgh', 4, 2) == ['abcd', 'cdef', 'efgh']
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError`
- R3 输出尾部: `Traceback (most recent call last):
  File "<string>", line 21, in <module>
    check(chunk_overlap)
    ^^^^^
NameError: name 'check' is not defined`

## 逐题代码（rounds 完整往返见 JSON）
## 失败题根因诊断（人工复核，2026-09-09）

### proj_normalize_name — 7B 对「中文/全角」边界覆盖不足
隐藏测试要求：去括号**含中文括号（）及其中内容**、去**所有空白含全角空格**。
qwen 三轮只删英文半角 `()` 与半角空格 → `ABC（中文）DEF` 类用例全挂。
R1 还在模块级自带 run_test() 并调用（import 即执行，输出纪律松动）。
→ 本体规则虽是中文表述，但 7B 对「括号」「空白」的泛化停留在 ASCII；测试反馈只回
   assert 尾部，未提示「中文括号」方向，写测改循环无法收敛。

### proj_parse_dsn — 需求歧义 + 覆盖不足
隐藏测试期望 `postgresql://u:p@h/db`（**URL 无 :port 段**）也返回 5432。
qwen 两轮的正则/split 实现都要求 `:port` 段必现，无段即 ValueError。
→ prompt docstring 只写「端口缺省时默认 5432」，未明说「端口段可整段省略」；
   属题目需求描述歧义（不是纯模型问题），应把 docstring 改为「端口段可选，缺省 5432」。

### proj_chunk_overlap — 边界最细的一题
要求每段 ≤max_len、相邻重叠 overlap 字符、**末段即使很短也保留**。
隐藏测试覆盖 末段保留 + 重叠量精确 + max_len 严格上界；qwen 三版在「末段拼接/
重叠不超界/空文本」组合上反复踩雷，反馈信息不足以收敛。
→ 属 7B 对「多约束联合满足」的弱项：分段类题目推荐给 GLM/V4 或拆成更小断言。
