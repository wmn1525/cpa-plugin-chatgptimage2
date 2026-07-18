"""ChatGPT 网页生图上游客户端。"""

from __future__ import annotations

import base64
import json
import random
import re
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Iterator

from curl_cffi import requests
from PIL import Image

from helper.control import RequestControl
from helper.errors import HelperError, UpstreamError
from helper.pow import build_legacy_requirements_token, build_proof_token, parse_pow_resources
from helper.turnstile import solve_turnstile_token

FILE_SERVICE_RE = re.compile(r"file-service://([A-Za-z0-9_-]+)")
FILE_ID_RE = re.compile(r"\b(file_00000000[a-f0-9]{24})\b")
SEDIMENT_RE = re.compile(r"sediment://([A-Za-z0-9_-]+)")


@dataclass
class ChatRequirements:
    """保存网页对话请求需要的 Sentinel token。"""

    token: str
    proof_token: str = ""


class WebImageBackend:
    """封装单个 CPA access_token 对应的网页会话。"""

    def __init__(self, access_token: str, base_url: str, control: RequestControl,
                 proxy_url: str = "", cf_cookies: str = "") -> None:
        """创建带浏览器 TLS 指纹和 Cookie 池的 curl_cffi 会话。"""
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.control = control
        self.device_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
        )
        options: dict[str, Any] = {"impersonate": "chrome110", "verify": True}
        if proxy_url:
            options["proxy"] = proxy_url
        self.session = requests.Session(**options)
        self.control.register(self.session)
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self.user_agent,
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "OAI-Device-Id": self.device_id,
            "OAI-Session-Id": self.session_id,
            "OAI-Language": "zh-CN",
        })
        self._set_cookie_header(cf_cookies)
        self.script_sources: list[str] = []
        self.data_build = ""

    def _set_cookie_header(self, cookie_header: str) -> None:
        """把配置中的 Cookie 字符串写入会话 CookieJar。"""
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name:
                self.session.cookies.set(name, value, domain="chatgpt.com")

    def close(self) -> None:
        """释放 curl_cffi 会话资源。"""
        self.control.unregister(self.session)
        self.session.close()

    def _headers(self, path: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        """构造带 ChatGPT target path 的网页请求头。"""
        headers = dict(self.session.headers)
        headers["X-OpenAI-Target-Path"] = path
        headers["X-OpenAI-Target-Route"] = path
        if extra:
            headers.update(extra)
        return headers

    def _check(self, response: Any, path: str) -> None:
        """把 curl_cffi 响应状态转换为统一上游错误。"""
        if 200 <= response.status_code < 300:
            return
        preview = str(getattr(response, "text", ""))[:500]
        status = int(response.status_code)
        if status == 401:
            raise UpstreamError("CPA 凭证已失效", 401, False)
        if status == 403:
            raise UpstreamError(f"ChatGPT 拒绝网页请求: {preview}", 403, False)
        if status == 429:
            raise UpstreamError("ChatGPT 网页生图额度受限", 429, True)
        raise UpstreamError(f"{path} 请求失败: HTTP {status} {preview}", status, status >= 500)

    def bootstrap(self) -> None:
        """访问 ChatGPT 首页并解析 Sentinel PoW 环境。"""
        response = self.session.get(self.base_url + "/", headers={
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }, timeout=self.control.timeout(30))
        self._check(response, "bootstrap")
        self.script_sources, self.data_build = parse_pow_resources(response.text)

    def get_requirements(self) -> ChatRequirements:
        """执行 Sentinel prepare/finalize 并计算 PoW 与 Turnstile。"""
        p_token = build_legacy_requirements_token(self.user_agent, self.script_sources, self.data_build)
        base = "/backend-api/sentinel/chat-requirements"
        prepare_path = base + "/prepare"
        response = self.session.post(self.base_url + prepare_path,
                                     headers=self._headers(prepare_path, {"Content-Type": "application/json"}),
                                     json={"p": p_token}, timeout=self.control.timeout(30))
        self._check(response, prepare_path)
        data = response.json()
        if (data.get("arkose") or {}).get("required"):
            raise HelperError("上游要求 Arkose 验证，首版插件暂不处理", 403, "arkose_required")
        proof_token = ""
        proof = data.get("proofofwork") or {}
        if proof.get("required"):
            proof_token = build_proof_token(str(proof.get("seed") or ""), str(proof.get("difficulty") or ""),
                                            self.user_agent, self.script_sources, self.data_build)
        turnstile_token = ""
        turnstile = data.get("turnstile") or {}
        if turnstile.get("required") and turnstile.get("dx"):
            turnstile_token = solve_turnstile_token(str(turnstile["dx"]), p_token) or ""
        finalize_path = base + "/finalize"
        response = self.session.post(self.base_url + finalize_path,
                                     headers=self._headers(finalize_path, {"Content-Type": "application/json"}),
                                     json={"prepare_token": data.get("prepare_token", ""), "proof_token": proof_token,
                                           "turnstile_token": turnstile_token}, timeout=self.control.timeout(30))
        self._check(response, finalize_path)
        finalized = response.json()
        token = str(finalized.get("token") or "")
        if not token:
            raise HelperError("Sentinel finalize 未返回 requirements token")
        return ChatRequirements(token=token, proof_token=proof_token)

    def upload_image(self, data_url: str, file_name: str) -> dict[str, Any]:
        """上传一张输入图片并返回 conversation 使用的文件元数据。"""
        raw = decode_data_url(data_url)
        image = Image.open(BytesIO(raw))
        width, height = image.size
        mime_type = Image.MIME.get(image.format, "image/png")
        path = "/backend-api/files"
        response = self.session.post(self.base_url + path,
                                     headers=self._headers(path, {"Content-Type": "application/json"}),
                                     json={"file_name": file_name, "file_size": len(raw), "use_case": "multimodal",
                                           "width": width, "height": height}, timeout=self.control.timeout(60))
        self._check(response, path)
        metadata = response.json()
        upload = self.session.put(metadata["upload_url"], headers={"Content-Type": mime_type,
                                  "x-ms-blob-type": "BlockBlob", "x-ms-version": "2020-04-08",
                                  "Origin": self.base_url, "Referer": self.base_url + "/"}, data=raw,
                                  timeout=self.control.timeout(120))
        self._check(upload, "image_upload")
        confirm_path = f"/backend-api/files/{metadata['file_id']}/uploaded"
        response = self.session.post(self.base_url + confirm_path,
                                     headers=self._headers(confirm_path, {"Content-Type": "application/json"}),
                                     data="{}", timeout=self.control.timeout(60))
        self._check(response, confirm_path)
        return {"file_id": metadata["file_id"], "file_name": file_name, "file_size": len(raw),
                "mime_type": mime_type, "width": width, "height": height}

    def prepare_conversation(self, prompt: str, requirements: ChatRequirements) -> str:
        """请求网页图片会话使用的 conduit token。"""
        path = "/backend-api/f/conversation/prepare"
        payload = {"action": "next", "fork_from_shared_post": False, "parent_message_id": str(uuid.uuid4()),
                   "model": "gpt-5-3", "client_prepare_state": "success", "timezone_offset_min": -480,
                   "timezone": "Asia/Shanghai", "conversation_mode": {"kind": "primary_assistant"},
                   "system_hints": ["picture_v2"], "partial_query": {"id": str(uuid.uuid4()),
                   "author": {"role": "user"}, "content": {"content_type": "text", "parts": [prompt]}},
                   "supports_buffering": True, "supported_encodings": ["v1"],
                   "client_contextual_info": {"app_name": "chatgpt.com"}}
        response = self.session.post(self.base_url + path, headers=self._image_headers(path, requirements),
                                     json=payload, timeout=self.control.timeout(60))
        self._check(response, path)
        return str(response.json().get("conduit_token") or "")

    def start_generation(self, prompt: str, requirements: ChatRequirements, conduit_token: str,
                         references: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """发起 picture_v2 SSE 请求并逐条解析 JSON 事件。"""
        parts: list[Any] = [{"content_type": "image_asset_pointer",
                             "asset_pointer": f"file-service://{item['file_id']}", "width": item["width"],
                             "height": item["height"], "size_bytes": item["file_size"]} for item in references]
        parts.append(prompt)
        content = {"content_type": "multimodal_text", "parts": parts} if references else {
            "content_type": "text", "parts": [prompt]}
        metadata: dict[str, Any] = {"system_hints": ["picture_v2"],
                                    "serialization_metadata": {"custom_symbol_offsets": []}}
        if references:
            metadata["attachments"] = [{"id": item["file_id"], "mimeType": item["mime_type"],
                                        "name": item["file_name"], "size": item["file_size"],
                                        "width": item["width"], "height": item["height"]} for item in references]
        payload = {"action": "next", "messages": [{"id": str(uuid.uuid4()), "author": {"role": "user"},
                   "create_time": time.time(), "content": content, "metadata": metadata}],
                   "parent_message_id": str(uuid.uuid4()), "model": "gpt-5-3", "client_prepare_state": "sent",
                   "timezone_offset_min": -480, "timezone": "Asia/Shanghai",
                   "conversation_mode": {"kind": "primary_assistant"}, "enable_message_followups": True,
                   "system_hints": ["picture_v2"], "supports_buffering": True, "supported_encodings": ["v1"],
                   "client_contextual_info": {"app_name": "chatgpt.com"},
                   "paragen_cot_summary_display_override": "allow", "force_parallel_switch": "auto"}
        path = "/backend-api/f/conversation"
        response = self.session.post(self.base_url + path,
                                     headers=self._image_headers(path, requirements, conduit_token, "text/event-stream"),
                                     json=payload, timeout=self.control.timeout(300), stream=True)
        self._check(response, path)
        try:
            yield from iter_sse_json(response, self.control)
        finally:
            response.close()

    def _image_headers(self, path: str, requirements: ChatRequirements, conduit_token: str = "",
                       accept: str = "*/*") -> dict[str, str]:
        """构造网页生图专用 Sentinel 请求头。"""
        extra = {"Content-Type": "application/json", "Accept": accept,
                 "OpenAI-Sentinel-Chat-Requirements-Token": requirements.token}
        if requirements.proof_token:
            extra["OpenAI-Sentinel-Proof-Token"] = requirements.proof_token
        if conduit_token:
            extra["X-Conduit-Token"] = conduit_token
        if accept == "text/event-stream":
            extra["X-Oai-Turn-Trace-Id"] = str(uuid.uuid4())
        return self._headers(path, extra)

    def poll_image_references(self, conversation_id: str, file_ids: list[str], sediment_ids: list[str],
                              timeout: float) -> tuple[list[str], list[str]]:
        """轮询 conversation 文档直到出现可下载图片引用。"""
        deadline = time.monotonic() + timeout
        if not file_ids and not sediment_ids:
            self.control.sleep(min(5.0, max(0.0, timeout)))
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            path = f"/backend-api/conversation/{conversation_id}"
            response = self.session.get(self.base_url + path, headers=self._headers(path, {"Accept": "application/json"}),
                                        timeout=self.control.timeout(min(60, max(0.001, deadline - time.monotonic()))))
            if response.status_code in (429, 500, 502, 503, 504, 524):
                self.control.sleep(min(2 ** min(attempt, 4) + random.random(),
                                       max(0.0, deadline - time.monotonic())))
                continue
            self._check(response, path)
            found_files, found_sediments, _ = extract_references(response.json())
            append_unique(file_ids, found_files)
            append_unique(sediment_ids, found_sediments)
            if file_ids or sediment_ids:
                return file_ids, sediment_ids
            self.control.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        raise HelperError("ChatGPT 网页生图轮询超时", 504, "image_timeout")

    def download_images(self, conversation_id: str, file_ids: list[str], sediment_ids: list[str]) -> list[bytes]:
        """把 file-service 和 sediment 引用解析并下载为图片字节。"""
        urls: list[str] = []
        for file_id in file_ids:
            path = f"/backend-api/files/{file_id}/download"
            response = self.session.get(self.base_url + path, headers=self._headers(path, {"Accept": "application/json"}),
                                        timeout=self.control.timeout(60))
            self._check(response, path)
            url = str(response.json().get("download_url") or response.json().get("url") or "")
            append_unique(urls, [url])
        for sediment_id in sediment_ids:
            path = f"/backend-api/conversation/{conversation_id}/attachment/{sediment_id}/download"
            response = self.session.get(self.base_url + path, headers=self._headers(path, {"Accept": "application/json"}),
                                        timeout=self.control.timeout(60))
            self._check(response, path)
            url = str(response.json().get("download_url") or response.json().get("url") or "")
            append_unique(urls, [url])
        images: list[bytes] = []
        for url in urls:
            response = self.session.get(url, timeout=self.control.timeout(120))
            self._check(response, "image_download")
            if response.content not in images:
                images.append(response.content)
        return images

    def delete_conversation(self, conversation_id: str) -> None:
        """把生图会话设为不可见。"""
        path = f"/backend-api/conversation/{conversation_id}"
        response = self.session.patch(self.base_url + path,
                                      headers=self._headers(path, {"Content-Type": "application/json"}),
                                      json={"is_visible": False}, timeout=self.control.timeout(60))
        self._check(response, path)


def decode_data_url(value: str) -> bytes:
    """解码 data URL 或纯 Base64 图片。"""
    payload = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    return base64.b64decode(payload)


def iter_sse_json(response: Any, control: RequestControl | None = None) -> Iterator[dict[str, Any]]:
    """把 curl_cffi 流按 SSE data 帧解析为 JSON。"""
    lines: list[str] = []
    for raw in response.iter_lines():
        if control is not None:
            control.timeout()
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if not line:
            if lines:
                payload = "\n".join(lines).strip()
                lines.clear()
                if payload and payload != "[DONE]":
                    try:
                        value = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        yield value
        elif line.startswith("data:"):
            lines.append(line[5:].lstrip())


def append_unique(target: list[str], values: list[str]) -> None:
    """把非空且未出现的字符串追加到列表。"""
    for value in values:
        if value and value not in target:
            target.append(value)


def extract_references(value: Any) -> tuple[list[str], list[str], list[str]]:
    """递归提取会话 ID、图片文件 ID 和直接 Base64 结果。"""
    files: list[str] = []
    sediments: list[str] = []
    direct_images: list[str] = []

    def walk(item: Any) -> None:
        """递归访问 JSON 值并收集图片字段。"""
        if isinstance(item, str):
            append_unique(files, FILE_SERVICE_RE.findall(item))
            append_unique(files, FILE_ID_RE.findall(item))
            append_unique(sediments, SEDIMENT_RE.findall(item))
            return
        if isinstance(item, dict):
            if item.get("type") == "image_generation_call" and isinstance(item.get("result"), str):
                append_unique(direct_images, [item["result"].split(",", 1)[-1]])
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return files, sediments, direct_images


def extract_conversation_id(value: Any) -> str:
    """递归寻找 SSE 事件中的 conversation_id。"""
    if isinstance(value, dict):
        if isinstance(value.get("conversation_id"), str) and value["conversation_id"]:
            return value["conversation_id"]
        for child in value.values():
            found = extract_conversation_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = extract_conversation_id(child)
            if found:
                return found
    return ""
