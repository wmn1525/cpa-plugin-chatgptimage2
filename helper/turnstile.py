"""ChatGPT Sentinel Turnstile token 解释器。"""

from __future__ import annotations

import base64
import json
import random
import time
from typing import Any, Optional


class OrderedMap:
    """模拟 JavaScript 对象的有序键集合。"""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.values: dict[str, Any] = {}

    def add(self, key: str, value: Any) -> None:
        """写入键值并保留首次出现顺序。"""
        if key not in self.values:
            self.keys.append(key)
        self.values[key] = value


def _to_str(value: Any) -> str:
    """转换为 Sentinel 虚拟机使用的 JavaScript 字符串。"""
    if value is None:
        return "undefined"
    special = {
        "window.Math": "[object Math]", "window.Reflect": "[object Reflect]",
        "window.performance": "[object Performance]", "window.localStorage": "[object Storage]",
        "window.Object": "function Object() { [native code] }",
        "window.Reflect.set": "function set() { [native code] }",
        "window.performance.now": "function () { [native code] }",
        "window.Object.create": "function create() { [native code] }",
        "window.Object.keys": "function keys() { [native code] }",
        "window.Math.random": "function random() { [native code] }",
    }
    if isinstance(value, str):
        return special.get(value, value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    return str(value)


def _xor_string(text: str, key: str) -> str:
    """使用循环密钥执行字符串异或。"""
    if not key:
        return text
    return "".join(chr(ord(ch) ^ ord(key[index % len(key)])) for index, ch in enumerate(text))


def solve_turnstile_token(dx: str, p_token: str) -> Optional[str]:
    """执行 Sentinel 返回的紧凑指令集并计算 turnstile token。"""
    try:
        tokens = json.loads(_xor_string(base64.b64decode(dx).decode(), p_token))
    except Exception:
        return None
    values: dict[Any, Any] = {}
    started = time.time_ns()
    result = ""

    def op1(e: float, t: float) -> None:
        """对两个寄存器执行异或。"""
        values[e] = _xor_string(_to_str(values[e]), _to_str(values[t]))

    def op2(e: float, t: Any) -> None:
        """写入常量。"""
        values[e] = t

    def op3(e: str) -> None:
        """输出最终 Base64 token。"""
        nonlocal result
        result = base64.b64encode(e.encode()).decode()

    def op5(e: float, t: float) -> None:
        """连接数组或字符串。"""
        current, incoming = values[e], values[t]
        if isinstance(current, (list, tuple)):
            values[e] = list(current) + [incoming]
        elif isinstance(current, (str, float, int)) or isinstance(incoming, (str, float, int)):
            values[e] = _to_str(current) + _to_str(incoming)
        else:
            values[e] = "NaN"

    def op6(e: float, t: float, n: float) -> None:
        """连接对象属性路径。"""
        path = f"{values[t]}.{values[n]}"
        values[e] = "https://chatgpt.com/" if path == "window.document.location" else path

    def op7(e: float, *args: float) -> None:
        """调用无返回值函数。"""
        target = values[e]
        params = [values[arg] for arg in args]
        if target == "window.Reflect.set":
            params[0].add(str(params[1]), params[2])
        elif callable(target):
            target(*params)

    def op8(e: float, t: float) -> None:
        """复制寄存器。"""
        values[e] = values[t]

    def op14(e: float, t: float) -> None:
        """解析 JSON。"""
        values[e] = json.loads(values[t])

    def op15(e: float, t: float) -> None:
        """序列化 JSON。"""
        values[e] = json.dumps(values[t])

    def op17(e: float, t: float, *args: float) -> None:
        """调用有返回值的浏览器模拟函数。"""
        target = values[t]
        params = [values[arg] for arg in args]
        if target == "window.performance.now":
            values[e] = (time.time_ns() - started + random.random()) / 1e6
        elif target == "window.Object.create":
            values[e] = OrderedMap()
        elif target == "window.Object.keys":
            values[e] = ["STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4", "oai-did", "client-correlated-secret"]
        elif target == "window.Math.random":
            values[e] = random.random()
        elif callable(target):
            values[e] = target(*params)

    def op18(e: float) -> None:
        """解码 Base64。"""
        values[e] = base64.b64decode(_to_str(values[e])).decode()

    def op19(e: float) -> None:
        """编码 Base64。"""
        values[e] = base64.b64encode(_to_str(values[e]).encode()).decode()

    def op20(e: float, t: float, n: float, *args: float) -> None:
        """条件调用函数。"""
        if values[e] == values[t] and callable(values[n]):
            values[n](*[values[arg] for arg in args])

    def op21(*_: Any) -> None:
        """忽略空操作。"""

    def op23(e: float, t: float, *args: float) -> None:
        """非空时调用函数。"""
        if values[e] is not None and callable(values[t]):
            values[t](*args)

    def op24(e: float, t: float, n: float) -> None:
        """连接两个属性名。"""
        values[e] = f"{values[t]}.{values[n]}"

    values.update({1: op1, 2: op2, 3: op3, 5: op5, 6: op6, 7: op7, 8: op8, 9: tokens,
                   10: "window", 14: op14, 15: op15, 16: p_token, 17: op17, 18: op18,
                   19: op19, 20: op20, 21: op21, 23: op23, 24: op24})
    for token in tokens:
        try:
            operation = values.get(token[0])
            if callable(operation):
                operation(*token[1:])
        except Exception:
            continue
    return result or None

