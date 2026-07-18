"""ChatGPT Sentinel PoW 计算工具。"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Sequence

DEFAULT_POW_SCRIPT = "https://chatgpt.com/backend-api/sentinel/sdk.js"
CORES = [8, 16, 24, 32]
DOCUMENT_KEYS = ["__reactContainer$fzelfjyxej8", "_reactListening5dehydibo78", "location"]
SCREEN_RESOLUTIONS = [[1920, 1080], [1440, 900], [2560, 1440], [3840, 2160]]


class ScriptSrcParser(HTMLParser):
    """从 ChatGPT 首页解析 PoW 脚本地址和构建标识。"""

    def __init__(self) -> None:
        super().__init__()
        self.script_sources: list[str] = []
        self.data_build = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """处理 script 标签并记录 src。"""
        if tag != "script":
            return
        source = dict(attrs).get("src")
        if not source:
            return
        self.script_sources.append(source)
        match = re.search(r"c/[^/]*/_", source)
        if match:
            self.data_build = match.group(0)


def parse_pow_resources(html_content: str) -> tuple[list[str], str]:
    """解析首页中用于生成浏览器证明的资源。"""
    parser = ScriptSrcParser()
    parser.feed(html_content)
    data_build = parser.data_build
    if not data_build:
        match = re.search(r'<html[^>]*data-build="([^"]*)"', html_content)
        if match:
            data_build = match.group(1)
    return parser.script_sources or [DEFAULT_POW_SCRIPT], data_build


def _legacy_parse_time() -> str:
    """生成网页算法要求的美东时间字符串。"""
    now = datetime.now(timezone(timedelta(hours=-5)))
    return now.strftime("%a %b %d %Y %H:%M:%S") + " GMT-0500 (Eastern Standard Time)"


def build_pow_config(user_agent: str, script_sources: Sequence[str] | None = None, data_build: str = "") -> list[Any]:
    """构造与 chatgpt2api 一致的浏览器环境数组。"""
    navigator_key = random.choice([
        "vendor∫Google Inc.", "webdriver∫false", "cookieEnabled∫true",
        "language∫zh-CN", "hardwareConcurrency∫32", "product∫Gecko",
    ])
    window_key = random.choice([
        "window", "document", "location", "history", "navigator", "performance", "crypto",
    ])
    source = random.choice(list(script_sources)) if script_sources else DEFAULT_POW_SCRIPT
    perf = time.perf_counter() * 1000
    return [
        sum(random.choice(SCREEN_RESOLUTIONS)), _legacy_parse_time(), 4294705152, 1,
        user_agent, source, data_build, "en-US", "en-US,es-US,en,es", random.random(),
        navigator_key, random.choice(DOCUMENT_KEYS), window_key, perf, str(uuid.uuid4()), "",
        random.choice(CORES), time.time() * 1000 - perf, 0, 0, 0, 0, 0, 0, 0,
    ]


def build_legacy_requirements_token(user_agent: str, script_sources: Sequence[str] | None = None, data_build: str = "") -> str:
    """生成 Sentinel prepare 请求使用的 p token。"""
    raw = json.dumps(build_pow_config(user_agent, script_sources, data_build), separators=(",", ":"), ensure_ascii=False)
    return "gAAAAAC" + base64.b64encode(raw.encode()).decode()


def build_proof_token(seed: str, difficulty: str, user_agent: str, script_sources: Sequence[str] | None = None, data_build: str = "") -> str:
    """按 SHA3-512 难度寻找 Sentinel proof token。"""
    config = build_pow_config(user_agent, script_sources, data_build)
    target = bytes.fromhex(difficulty)
    diff_len = len(difficulty) // 2
    seed_bytes = seed.encode()
    static_1 = (json.dumps(config[:3], separators=(",", ":"), ensure_ascii=False)[:-1] + ",").encode()
    static_2 = ("," + json.dumps(config[4:9], separators=(",", ":"), ensure_ascii=False)[1:-1] + ",").encode()
    static_3 = ("," + json.dumps(config[10:], separators=(",", ":"), ensure_ascii=False)[1:]).encode()
    for number in range(500000):
        final_json = static_1 + str(number).encode() + static_2 + str(number >> 1).encode() + static_3
        encoded = base64.b64encode(final_json)
        if hashlib.sha3_512(seed_bytes + encoded).digest()[:diff_len] <= target:
            return "gAAAAAB" + encoded.decode()
    raise RuntimeError(f"failed to solve proof token: difficulty={difficulty}")

