"""OpenAI Images API 到 ChatGPT 网页链路的转换层。"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
from typing import Any

from PIL import Image

from helper.backend import WebImageBackend, extract_conversation_id, extract_references
from helper.errors import HelperError, UpstreamError

_cursor_lock = threading.Lock()
_state_lock = threading.Lock()
_credential_cursor = 0
_credential_locks: dict[str, threading.Lock] = {}
_cooldowns: dict[str, float] = {}


@dataclass
class ImageRequest:
    """保存标准化后的 Images API 请求。"""

    prompt: str
    n: int = 1
    size: str = "1024x1024"
    quality: str = "auto"
    response_format: str = "b64_json"
    stream: bool = False
    images: list[str] = field(default_factory=list)
    masks: list[str] = field(default_factory=list)


def handle_images(payload: dict[str, Any]) -> dict[str, Any]:
    """处理一次来自 DLL 的生成或编辑请求。"""
    request = parse_image_request(base64.b64decode(payload.get("body_base64") or ""),
                                  str(payload.get("content_type") or ""), bool(payload.get("stream")),
                                  str(payload.get("request_path") or ""))
    credentials = list(payload.get("credentials") or [])
    if not credentials:
        raise HelperError("CPA 中没有可用凭证", 503, "no_credentials")
    results: list[dict[str, Any] | None] = [None] * request.n
    workers = min(request.n, len(credentials), 4)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(generate_one, request, credentials, payload, index): index
                   for index in range(request.n)}
        for future in as_completed(futures):
            index = futures[future]
            results[index] = future.result()
    data = [item for item in results if item is not None]
    created = int(time.time())
    if request.stream:
        chunk = {"object": "image.generation.result", "created": created, "model": "gpt-image-2",
                 "index": 1, "total": len(data), "data": data}
        body = "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\ndata: [DONE]\n\n"
        content_type = "text/event-stream"
    else:
        body = json.dumps({"created": created, "data": data}, ensure_ascii=False, separators=(",", ":"))
        content_type = "application/json"
    return {"status_code": 200, "headers": {"Content-Type": [content_type]},
            "body_base64": base64.b64encode(body.encode()).decode()}


def parse_image_request(body: bytes, content_type: str, stream_override: bool, request_path: str = "") -> ImageRequest:
    """解析 JSON 或 multipart Images API 请求。"""
    if content_type.lower().startswith("multipart/form-data"):
        values, files = parse_multipart(body, content_type)
        prompt = first(values.get("prompt"))
        images = files.get("image[]", []) or files.get("image", [])
        masks = files.get("mask", [])
        request = ImageRequest(prompt=prompt, n=parse_int(first(values.get("n")), 1),
                               size=first(values.get("size")) or "1024x1024",
                               quality=first(values.get("quality")) or "auto",
                               response_format=first(values.get("response_format")) or "b64_json",
                               stream=parse_bool(first(values.get("stream"))) or stream_override,
                               images=images, masks=masks)
    else:
        try:
            value = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise HelperError(f"请求 JSON 无效: {exc}", 400, "invalid_request") from exc
        images: list[str] = []
        for item in value.get("images") or []:
            if isinstance(item, dict) and item.get("image_url"):
                images.append(str(item["image_url"]))
            elif isinstance(item, str):
                images.append(item)
        if value.get("image_url"):
            images.append(str(value["image_url"]))
        masks: list[str] = []
        mask = value.get("mask")
        if isinstance(mask, dict) and mask.get("image_url"):
            masks.append(str(mask["image_url"]))
        request = ImageRequest(prompt=str(value.get("prompt") or ""), n=parse_int(value.get("n"), 1),
                               size=str(value.get("size") or "1024x1024"),
                               quality=str(value.get("quality") or "auto"),
                               response_format=str(value.get("response_format") or "b64_json"),
                               stream=bool(value.get("stream")) or stream_override,
                               images=images, masks=masks)
    if not request.prompt.strip():
        raise HelperError("prompt 不能为空", 400, "invalid_request")
    if request.n < 1 or request.n > 4:
        raise HelperError("n 必须在 1 到 4 之间", 400, "invalid_request")
    if request.images == [] and request_path == "/v1/images/edits":
        raise HelperError("图片编辑请求缺少 image", 400, "invalid_request")
    if request.response_format not in {"b64_json", "url"}:
        request.response_format = "b64_json"
    return request


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """使用标准库 email 解析 multipart 表单与图片。"""
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
    values: dict[str, list[str]] = {}
    files: dict[str, list[str]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            mime = part.get_content_type() or "application/octet-stream"
            files.setdefault(name, []).append(f"data:{mime};base64," + base64.b64encode(data).decode())
        else:
            values.setdefault(name, []).append(data.decode(part.get_content_charset() or "utf-8", "replace"))
    return values, files


def generate_one(request: ImageRequest, credentials: list[dict[str, Any]], settings: dict[str, Any], index: int) -> dict[str, Any]:
    """为单张图片非阻塞轮询 CPA 凭证，忙碌账号不会阻塞换号。"""
    ordered = rotate_credentials(credentials, index)
    errors: list[str] = []
    attempted: set[str] = set()
    wait_seconds = min(max(float(settings.get("timeout_seconds") or 30), 1.0), 30.0)
    wait_deadline = time.monotonic() + wait_seconds
    while len(attempted) < len({credential_key(item) for item in ordered}):
        busy = False
        progressed = False
        for credential in ordered:
            key = credential_key(credential)
            if key in attempted:
                continue
            if credential_cooling(key):
                attempted.add(key)
                continue
            lock = credential_lock(key)
            if not lock.acquire(blocking=False):
                busy = True
                continue
            if credential_cooling(key):
                attempted.add(key)
                lock.release()
                continue
            attempted.add(key)
            progressed = True
            try:
                return generate_with_credential(request, credential, settings)
            except UpstreamError as exc:
                errors.append(str(exc))
                if exc.status_code in (401, 403):
                    set_credential_cooldown(key, 300)
                elif exc.status_code == 429:
                    set_credential_cooldown(key, 120)
                elif exc.retryable:
                    set_credential_cooldown(key, 30)
            except Exception as exc:
                errors.append(str(exc))
                set_credential_cooldown(key, 15)
            finally:
                lock.release()
        if busy and not progressed and time.monotonic() < wait_deadline:
            time.sleep(0.02)
            continue
        if not busy or time.monotonic() >= wait_deadline:
            break
    raise HelperError(errors[-1] if errors else "全部 CPA 凭证均忙碌或不可用", 503, "all_credentials_failed")


def credential_key(credential: dict[str, Any]) -> str:
    """使用 Token 摘要生成不泄露凭证的进程内调度键。"""
    token = str(credential.get("access_token") or "")
    return hashlib.sha256(token.encode()).hexdigest()


def credential_lock(key: str) -> threading.Lock:
    """线程安全地获取单凭证互斥锁。"""
    with _state_lock:
        return _credential_locks.setdefault(key, threading.Lock())


def credential_cooling(key: str) -> bool:
    """检查凭证是否仍处于内存冷却期。"""
    with _state_lock:
        return _cooldowns.get(key, 0) > time.time()


def set_credential_cooldown(key: str, seconds: float) -> None:
    """线程安全地更新凭证冷却截止时间。"""
    with _state_lock:
        _cooldowns[key] = time.time() + seconds


def rotate_credentials(credentials: list[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
    """按全局游标轮换凭证起始位置。"""
    global _credential_cursor
    with _cursor_lock:
        start = (_credential_cursor + offset) % len(credentials)
        _credential_cursor = (_credential_cursor + 1) % len(credentials)
    return credentials[start:] + credentials[:start]


def generate_with_credential(request: ImageRequest, credential: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """使用单个 CPA 凭证执行完整网页生图流程。"""
    proxy_url = str(credential.get("proxy_url") or settings.get("proxy_url") or "")
    backend = WebImageBackend(str(credential.get("access_token") or ""), str(settings.get("base_url") or ""),
                              proxy_url, str(settings.get("cf_cookies") or ""))
    conversation_id = ""
    try:
        normalized_images = [normalize_image_source(backend, value) for value in request.images]
        normalized_masks = [normalize_image_source(backend, value) for value in request.masks]
        if normalized_masks:
            normalized_images = composite_masks(normalized_images, normalized_masks)
        references = [backend.upload_image(value, f"image_{number}.png")
                      for number, value in enumerate(normalized_images, start=1)]
        backend.bootstrap()
        requirements = backend.get_requirements()
        conduit = backend.prepare_conversation(request.prompt, requirements)
        events = list(backend.start_generation(request.prompt, requirements, conduit, references))
        files: list[str] = []
        sediments: list[str] = []
        direct_images: list[str] = []
        for event in events:
            conversation_id = conversation_id or extract_conversation_id(event)
            found_files, found_sediments, found_images = extract_references(event)
            append_unique(files, found_files)
            append_unique(sediments, found_sediments)
            append_unique(direct_images, found_images)
        if direct_images:
            raw = base64.b64decode(direct_images[0])
        else:
            if not conversation_id:
                raise HelperError("上游 SSE 未返回 conversation_id")
            if not files and not sediments:
                files, sediments = backend.poll_image_references(conversation_id, files, sediments,
                                                                  min(float(settings.get("timeout_seconds") or 120), 180.0))
            images = backend.download_images(conversation_id, files, sediments)
            if not images:
                raise HelperError("上游完成但未取得图片结果")
            raw = images[0]
        encoded = base64.b64encode(raw).decode()
        item = {"revised_prompt": request.prompt}
        if request.response_format == "url":
            item["url"] = "data:image/png;base64," + encoded
        else:
            item["b64_json"] = encoded
        return item
    finally:
        if conversation_id and settings.get("cleanup_conversation", True):
            try:
                backend.delete_conversation(conversation_id)
            except Exception:
                pass
        backend.close()


def normalize_image_source(backend: WebImageBackend, value: str) -> str:
    """把远程 URL、data URL 或纯 Base64 统一为 data URL。"""
    if value.startswith("data:image/"):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        response = backend.session.get(value, timeout=120)
        backend._check(response, "input_image_download")
        mime = str(response.headers.get("content-type") or "image/png").split(";", 1)[0]
        return f"data:{mime};base64," + base64.b64encode(response.content).decode()
    return "data:image/png;base64," + value


def composite_masks(images: list[str], masks: list[str]) -> list[str]:
    """按 chatgpt2api 语义把 mask alpha 通道写入输入图片。"""
    output: list[str] = []
    for index, image_value in enumerate(images):
        image = Image.open(BytesIO(decode_data_url(image_value))).convert("RGBA")
        mask_value = masks[index] if index < len(masks) else masks[-1]
        mask = Image.open(BytesIO(decode_data_url(mask_value)))
        alpha = mask.getchannel("A") if mask.mode == "RGBA" else mask.convert("L")
        image.putalpha(alpha.resize(image.size, Image.Resampling.LANCZOS))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        output.append("data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode())
    return output


def decode_data_url(value: str) -> bytes:
    """解码图片 data URL。"""
    return base64.b64decode(value.split(",", 1)[1] if "," in value else value)


def parse_int(value: Any, fallback: int) -> int:
    """安全解析整数表单字段。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def parse_bool(value: Any) -> bool:
    """安全解析布尔表单字段。"""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def first(values: list[str] | None) -> str:
    """获取表单字段的第一个值。"""
    return values[0] if values else ""


def append_unique(target: list[str], values: list[str]) -> None:
    """追加未出现过的非空字符串。"""
    for value in values:
        if value and value not in target:
            target.append(value)
