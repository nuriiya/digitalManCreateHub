# qwen2.5:7b-32k × 本体注入 × 写码 pipeline · 实验留存

> 时间：2026-09-09 10:21 · 后端 :8000 dev 容器 · 生成文件：exports/qwen25-本体写码pipeline-留存.md

## 实验设置

| 项 | 值 |
|---|---|
| 数字人 | 代码工程师（identity 3，reactive=True）|
| 使命 | 把技术方案转化为可运行代码实现。 |
| 生成通道 | provider=ollama · qwen2.5:7b-32k（本机 Ollama，temperature=0）|
| 判定 | 沙箱容器跑 assert（HumanEval 隐藏测试），LLM 不判分 |
| 解题方式 | 本体**全量注入** system prompt（代码题不走字面检索）+ 写→测→改 ≤3 轮 |

## 注入的编程规范本体（14 条，全量）

- 标签规范形式：标签的规范形式是去除首尾空白并转为小写后的字符串，用于统一比较与输出。
- 去重返回规范形式：去重时应将每个标签转换为规范形式，并将规范形式加入结果列表，而不是保留原始标签。
- 截断上限不计省略号：safe_truncate 的 max_chars 指截断时保留原文本字符的个数，省略号是额外追加的，不占用 max_chars 的额度。
- 超长时截取前缀再拼接省略号：当 len(text) > max_chars 时，应返回 text[:max_chars] + ellipsis，不要用 max_chars - len(ellipsis) 作为前缀截取长度。
- 字符数：Python 中按字符数截断时使用 len 和切片按 Unicode 字符计算，中文等每个字符计为 1，不按字节截断。
- 非空摘要段先去掉首尾空白再输出：处理每个输入段时应先 strip() 去除首尾空白，strip 后为空则过滤掉，非空则用 strip 后的文本参与后续去重和连接，不能保留原始外层空白。
- 去重键与保留文本分离：判断内容重复时，把去除所有空白和标点后的内容作为已见键；真正加入结果的是清理掉首尾空白的该段原文，而不是原始未清理段落，也不是去除空白标点后的键。
- 沙箱安全边界：所有代码执行都在一次性隔离沙箱中：禁网、只读根文件系统、无特权、内存 256MB/CPU 1 核/PID 上限 64。数字人无法触及宿主文件系统或外网。
- 执行动作白名单：只能调用本人已声明且审批通过的动作；未声明的动作会被 guard 拒绝。执行前 guard 确定性裁决（入参必填 + 长度封顶），越权调用一律拒绝。
- 写测改循环：写代码 → run_test 跑测试 → 观察绿/红 → 红则带失败堆栈修正 → 再跑，直到绿或达轮数上限。终止靠确定性判定（测试通过），不靠自我宣称完成。
- 输出纯代码：只输出纯 Python 代码，不带解释和 markdown 标记，引号括号成对闭合
- 边界条件处理：空输入/单元素/负数/去重/截断等边界必须显式处理，不能只写主路径
- 询问层级：代码工程师可询问技术设计工程师：当技术方案不明确时，向技术设计工程师提问澄清，不要自行猜测设计意图。
- 审核门：代码过审才交接：产出代码后需代码审查员审核（审核门），审核通过后才交接给测试工程师，审核驳回则修改后重新提交。

## 题目与结果一览

| 题 | verdict | 轮次 | 说明 |
|---|---|---|---|
| `humaneval_2` | **pass** | 1 | 一轮通过 |
| `humaneval_0` | **pass** | 2 | 第2轮修正后通过 |
| `humaneval_1` | **pass** | 1 | 一轮通过 |

## humaneval_2 · entry=`truncate_number`

**题目 prompt：**
```python
def truncate_number(number: float) -> float:
    """ Given a positive floating point number, it can be decomposed into
    and integer part (largest integer smaller than given number) and decimals
    (leftover part always smaller than 1).

    Return the decimal part of the number.
    >>> truncate_number(3.5)
    0.5
    """
```

### 第 1 轮 → **pass**

**生成的代码：**
```python
def truncate_number(number: float) -> float:
    """ Given a positive floating point number, it can be decomposed into
    and integer part (largest integer smaller than given number) and decimals
    (leftover part always smaller than 1).

    Return the decimal part of the number.
    >>> truncate_number(3.5)
    0.5
    """
    integer_part = int(number)
    decimal_part = number - integer_part
    return decimal_part
```


## humaneval_0 · entry=`has_close_elements`

**题目 prompt：**
```python
from typing import List


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    """
```

### 第 1 轮 → **fail**

**生成的代码：**
```python
def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    """
    if not numbers or len(numbers) < 2:
        return False

    numbers.sort()

    for i in range(len(numbers) - 1):
        if numbers[i + 1] - numbers[i] < threshold:
            return True

    return False
```

**测试输出：**
```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    def has_close_elements(numbers: List[float], threshold: float) -> bool:
                                    ^^^^
NameError: name 'List' is not defined. Did you mean: 'list'?
```

### 第 2 轮 → **pass**

**生成的代码：**
```python
from typing import List


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    """
    numbers.sort()
    for i in range(len(numbers) - 1):
        if numbers[i + 1] - numbers[i] < threshold:
            return True
    return False
```


## humaneval_1 · entry=`separate_paren_groups`

**题目 prompt：**
```python
from typing import List


def separate_paren_groups(paren_string: str) -> List[str]:
    """ Input to this function is a string containing multiple groups of nested parentheses. Your goal is to
    separate those group into separate strings and return the list of those.
    Separate groups are balanced (each open brace is properly closed) and not nested within each other
    Ignore any spaces in the input string.
    >>> separate_paren_groups('( ) (( )) (( )( ))')
    ['()', '(())', '(()())']
    """
```

### 第 1 轮 → **pass**

**生成的代码：**
```python
from typing import List


def separate_paren_groups(paren_string: str) -> List[str]:
    stack = []
    result = []
    current_group = ''
    
    for char in paren_string.replace(' ', ''):
        if char == '(':
            stack.append(char)
            current_group += char
        elif char == ')':
            stack.pop()
            current_group += char
            if not stack:
                result.append(current_group)
                current_group = ''
    
    return result

# 测试用例
def run_test():
    test_cases = [
        ('( ) (( )) (( )( ))', ['()', '(())', '(()())']),
        ('((()))', ['((()))']),
        ('()', ['()']),
        ('', []),
        ('(())(())', ['(())', '(())'])
    ]
    
    for input_str, expected in test_cases:
        result = separate_paren_groups(input_str)
        assert result == expected, f"Failed for input: {input_str}. Expected {expected}, got {result}"
    
    print("All test cases passed!")

run_test()
```

**测试输出：**
```
All test cases passed!
```

## 观察结论

1. **本体注入链路有效**：14 条编程规范（写测改循环/边界条件/沙箱边界/输出纯代码）进入 system，qwen2.5 按「只输出代码」作答，验证全走容器 assert。
2. **写测改循环止损**：has_close_elements 首轮红，第 2 轮带失败堆栈重生成即绿——终止由测试决定，不是 LLM 自夸。
3. **本地 7B 可用但质量档位低**：3/3 通过均为 HumanEval 简单-中等题；对照 GLM/V4 同链路通常更强。
4. **定位建议**：7B 适合作低成本回归/对照基线，不宜直接作为生产写码通道。