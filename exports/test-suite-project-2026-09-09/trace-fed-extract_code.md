# 失败用例完整对话 · proj_extract_code (id=174)

> pipeline + 本体 + reviewer 喂饱 · 数字人：writer=代码工程师(3) ↔ reviewer=调试工程师(6) · qwen2.5:7b-32k · temperature=0
> 结果：**fail**（3 轮均 fail） · run_id=235 · entry=extract_code

## 任务 docstring（注入到 reviewer system 的【任务要求】）
```python
def extract_code(reply: str) -> str:
    """从模型回答里提取纯 Python 代码。
    优先提取 ```python ... ``` 代码块；没有代码块则从首个 def/from/import
    开头的行开始截取到结尾；都没有则返回原文。
    """
```

## 隐藏测试断言（注入到 reviewer system 的【隐藏测试断言】）
```python
def check(candidate):
    assert candidate('解释一下\n```python\ndef f():\n    return 1\n```\n以上') == \
        'def f():\n    return 1'
    assert candidate('说明\ndef f():\n    return 1\n') == 'def f():\n    return 1'
    assert candidate('from x import y') == 'from x import y'
```

---

## R1 · 沙箱裁定：fail

**writer 写出的代码（完整）**：
```python
def extract_code(reply: str) -> str:
    """
    从模型回答里提取纯 Python 代码。
    优先提取 ```python ... ``` 代码块；没有代码块则从首个 def/from/import
    开头的行开始截取到结尾；都没有则返回原文。
    """
    # 检查是否包含 ```python ... ``` 代码块
    start = reply.find("```python")
    if start != -1:
        end = reply.find("```", start + 7)
        if end != -1:
            return reply[start + 7:end].strip()

    # 检查是否包含 def/from/import 开头的行
    lines = reply.splitlines()
    code_lines = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("def ") or stripped_line.startswith("from ") or stripped_line.startswith("import "):
            code_lines.append(line)
            break

    # 如果找到 def/from/import 开头的行，截取到结尾
    if code_lines:
        return "".join(code_lines).strip()

    # 如果没有找到任何代码块或 def/from/import 开头的行，返回原文
    return reply.strip()
```

**沙箱失败 output（完整）**：
```
Traceback (most recent call last):
  File "<string>", line 37, in <module>
    check(extract_code)
    ~~~~~^^^^^^^^^^^^^^
  File "<string>", line 31, in check
    assert candidate('解释一下\n```python\ndef f():\n    return 1\n```\n以上') == \
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        'def f():\n    return 1'
        ^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError
```

**reviewer 诊断（完整）**：
```
根据失败测试报告，断言 `check(extract_code)` 失败，具体表现为：

```python
assert candidate('解释一下\n```python\ndef f():\n    return 1\n```\n以上') == \
    'def f():\n    return 1'
```

实际返回值与期望值不符。失败原因在于代码块 ```python ... ``` 之后的代码没有被正确截取。具体来说，`extract_code` 函数在提取 ```python 代码块时，只截取了 ```python 之前的代码，而没有包含 ```python 之后的代码。

根据 docstring，函数应优先提取 ```python ... ``` 代码块，因此需要修正代码块的提取逻辑。

**修改建议：**
在提取 ```python 代码块时，应包含 ```python 之后的代码。具体修改如下：

```python
def extract_code(reply: str) -> str:
    start = reply.find("```python")
    if start != -1:
        end = reply.find("```", start + 7)
        if end != -1:
            return reply[start + 7:end + 3].strip()  # 包含 ```python 之后的代码

    lines = reply.splitlines()
    code_lines = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("def ") or stripped_line.startswith("from ") or stripped_line.startswith("import "):
            code_lines.append(line)
            break

    if code_lines:
        return "".join(code_lines).strip()

    return reply.strip()
```

通过上述修改，`extract_code` 函数在提取 ```python 代码块时，将包含 ```python 之后的代码，确保测试通过。
```

---

## R2 · 沙箱裁定：fail

**writer 写出的代码（完整）**：
```python
def extract_code(reply: str) -> str:
    start = reply.find("
```

**沙箱失败 output（完整）**：
```
File "<string>", line 2
    start = reply.find("
                       ^
SyntaxError: unterminated string literal (detected at line 2)
```

**reviewer 诊断（完整）**：
```
根据失败测试报告中的堆栈信息，`SyntaxError: unterminated string literal` 表明在 `reply.find("`")` 这一行中，字符串未正确终止。具体来看，`reply` 中的字符串字面量在 `\n` 之后没有正确闭合。

从 docstring 可知，`extract_code` 函数需要从模型回答中提取纯 Python 代码。测试用例 `check(candidate('解释一下\n```python\ndef f():\n    return 1\n```\n以上'))` 中的字符串包含换行符 `\n`，这导致了字符串未正确闭合。

**修改建议：**
在 `reply.find("`")` 之前，确保 `reply` 中的字符串字面量正确闭合。具体来说，需要在 `reply` 中替换或移除换行符 `\n`，确保字符串字面量正确闭合。

**验证方式：**
修复后重新运行 `check(candidate)` 断言，确保所有测试用例通过。特别是需要验证包含换行符的字符串能否正确处理。
```

---

## R3 · 沙箱裁定：fail

**writer 写出的代码（完整）**：
```python
def extract_code(reply: str) -> str:
    """从模型回答里提取纯 Python 代码。
    优先提取
```

**沙箱失败 output（完整）**：
```
File "<string>", line 2
    """从模型回答里提取纯 Python 代码。
    ^
SyntaxError: unterminated triple-quoted string literal (detected at line 13)
```

**reviewer 诊断**：（空 —— review 失败或 R3 不再触发）

---

## 顶层字段
- `run_id`: `235`
- `task_id`: `174`
- `writer_id`: `3`
- `reviewer_id`: `6`
- `entry_point`: `'extract_code'`
- `verdict`: `'fail'`
- `output`: `'File "<string>", line 2\n    """从模型回答里提取纯 Python 代码。\n    ^\nSyntaxError: unterminated triple-quoted string literal (detected at line 13)'`
- `reactive`: `True`
- `use_ontology`: `True`
- `mode`: `'pipeline'`
- `reply`: `'def extract_code(reply: str) -> str:\n    """从模型回答里提取纯 Python 代码。\n    优先提取'`