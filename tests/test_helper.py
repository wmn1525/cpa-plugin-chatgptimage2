"""网页生图助手单元与模拟上游测试。"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import helper.protocol as protocol
from helper.backend import WebImageBackend, extract_conversation_id, extract_references
from helper.control import RequestControl
from helper.errors import HelperError, UpstreamError
from helper.main import read_frame, write_frame
from helper.protocol import ImageRequest, generate_one, handle_images, parse_image_request
from helper.pow import build_legacy_requirements_token, parse_pow_resources

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
OUTPUT_FILE_ID = "file_00000000aaaaaaaaaaaaaaaaaaaaaaaa"


class MockChatGPTHandler(BaseHTTPRequestHandler):
    """模拟网页生图需要的 ChatGPT HTTP 端点。"""

    def log_message(self, format: str, *args: object) -> None:
        """关闭测试服务器访问日志。"""

    def _json(self, value: object, status: int = 200) -> None:
        """发送 JSON 响应。"""
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record_auth(self) -> None:
        """只记录授权头摘要，供并发换号集成测试统计。"""
        authorization = self.headers.get("Authorization") or ""
        if not authorization:
            return
        digest = hashlib.sha256(authorization.encode()).hexdigest()
        with self.server.auth_lock:
            self.server.seen_authorizations.add(digest)
            if not self.server.first_authorization_digest:
                self.server.first_authorization_digest = digest

    def _credential_expired(self) -> bool:
        """判断当前请求是否应模拟首个凭证在使用中失效。"""
        if not self.server.expire_first_credential:
            return False
        authorization = self.headers.get("Authorization") or ""
        digest = hashlib.sha256(authorization.encode()).hexdigest()
        with self.server.auth_lock:
            expired = bool(digest and digest == self.server.first_authorization_digest)
            if expired:
                self.server.expired_response_count += 1
            return expired

    def do_GET(self) -> None:
        """处理首页、下载地址和图片响应。"""
        self._record_auth()
        if self.path == "/":
            raw = b'<html data-build="mock"><script src="/c/mock/_script.js"></script></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == f"/backend-api/files/{OUTPUT_FILE_ID}/download":
            self._json({"download_url": self.server.base_url + "/image.png"})
        elif self.path == "/image.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG_1X1)))
            self.end_headers()
            self.wfile.write(PNG_1X1)
        elif self.path == "/__test__/auth-count":
            with self.server.auth_lock:
                count = len(self.server.seen_authorizations)
            self._json({"count": count})
        elif self.path == "/__test__/expired-count":
            with self.server.auth_lock:
                count = self.server.expired_response_count
            self._json({"count": count})
        elif self.path.startswith("/backend-api/conversation/"):
            with self.server.poll_lock:
                status = self.server.poll_statuses.pop(0) if self.server.poll_statuses else 200
            if status != 200:
                self._json({"temporary": True}, status)
                return
            self._json({"conversation_id": "conv_mock", "mapping": {"x": {"message": {
                "author": {"role": "tool"}, "metadata": {"async_task_type": "image_gen"},
                "content": {"parts": [f"file-service://{OUTPUT_FILE_ID}"]}}}}})
        else:
            self._json({}, 404)

    def do_POST(self) -> None:
        """处理 Sentinel、上传和 conversation 请求。"""
        self._record_auth()
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.endswith("/chat-requirements/prepare"):
            self._json({"prepare_token": "prepare", "proofofwork": {"required": False},
                        "turnstile": {"required": False}})
        elif self.path.endswith("/chat-requirements/finalize"):
            self._json({"token": "requirements"})
        elif self.path == "/backend-api/f/conversation/prepare":
            self._json({"conduit_token": "conduit"})
        elif self.path == "/backend-api/f/conversation":
            if self._credential_expired():
                if self.server.expire_credential_mode == "sse":
                    payload = {"type": "error", "error": {"code": "token_invalidated"}}
                    raw = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self._json({"expired": True}, 401)
                return
            delay = float(getattr(self.server, "generation_delay", 0))
            if delay > 0:
                time.sleep(delay)
            payload = {"conversation_id": "conv_mock", "message": {
                "author": {"role": "tool"}, "metadata": {"async_task_type": "image_gen"},
                "content": {"parts": [f"file-service://{OUTPUT_FILE_ID}"]}}}
            raw = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == "/backend-api/files":
            self._json({"file_id": "file_input", "upload_url": self.server.base_url + "/upload"})
        elif self.path == "/backend-api/files/file_input/uploaded":
            self._json({"ok": True})
        elif self.path == "/__test__/expire-first":
            self.server.expire_first_credential = True
            self.server.expire_credential_mode = "http"
            self._json({"ok": True})
        elif self.path == "/__test__/expire-first-sse":
            self.server.expire_first_credential = True
            self.server.expire_credential_mode = "sse"
            self._json({"ok": True})
        else:
            self._json({}, 404)

    def do_PUT(self) -> None:
        """接收模拟对象存储图片上传。"""
        self._record_auth()
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(201)
        self.end_headers()

    def do_PATCH(self) -> None:
        """接收会话隐藏请求。"""
        self._record_auth()
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._json({"ok": True})


class MockServer:
    """管理后台线程中的模拟 ChatGPT 服务。"""

    def __enter__(self) -> "MockServer":
        """启动随机端口 HTTP 服务。"""
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockChatGPTHandler)
        self.server.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server.generation_delay = 0
        self.server.poll_lock = threading.Lock()
        self.server.poll_statuses = []
        self.server.auth_lock = threading.Lock()
        self.server.seen_authorizations = set()
        self.server.first_authorization_digest = ""
        self.server.expire_first_credential = False
        self.server.expire_credential_mode = "http"
        self.server.expired_response_count = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        """关闭模拟 HTTP 服务。"""
        self.server.shutdown()
        self.server.server_close()


class HelperTests(unittest.TestCase):
    """验证助手的核心兼容行为。"""

    def setUp(self) -> None:
        """清理跨请求凭证调度状态，保证并发测试相互隔离。"""
        with protocol._cursor_lock:
            protocol._credential_cursor = 0
        with protocol._state_lock:
            protocol._credential_locks.clear()
            protocol._cooldowns.clear()

    def test_pow_resources_and_token(self) -> None:
        """首页资源和 requirements token 应可稳定生成。"""
        sources, build = parse_pow_resources('<html data-build="b"><script src="/c/x/_a.js"></script></html>')
        self.assertEqual(build, "c/x/_")
        self.assertTrue(sources)
        self.assertTrue(build_legacy_requirements_token("ua", sources, build).startswith("gAAAAAC"))

    def test_parse_json_request(self) -> None:
        """JSON 图片生成参数应正确标准化。"""
        request = parse_image_request(b'{"prompt":"cat","n":2,"stream":true}', "application/json", False)
        self.assertEqual(request.prompt, "cat")
        self.assertEqual(request.n, 2)
        self.assertTrue(request.stream)

    def test_extract_references(self) -> None:
        """嵌套事件应提取会话和文件引用。"""
        value = {"conversation_id": "conv", "x": f"file-service://{OUTPUT_FILE_ID}"}
        files, sediments, images = extract_references(value)
        self.assertIn(OUTPUT_FILE_ID, files)
        self.assertEqual(sediments, [])
        self.assertEqual(images, [])
        self.assertEqual(extract_conversation_id(value), "conv")

    def test_frame_roundtrip(self) -> None:
        """长度前缀 RPC 帧应无损读写。"""
        import io
        stream = io.BytesIO()
        write_frame(stream, {"id": 1, "ok": True})
        stream.seek(0)
        self.assertEqual(read_frame(stream)["id"], 1)
        self.assertEqual(struct.unpack(">I", stream.getvalue()[:4])[0], len(stream.getvalue()) - 4)

    def test_mock_generation_and_edit(self) -> None:
        """模拟上游应跑通文生图和图片编辑完整流程。"""
        with MockServer() as mock:
            base = {"credentials": [{"access_token": "fake-token", "auth_index": "1"}],
                    "base_url": mock.server.base_url, "timeout_seconds": 30,
                    "cleanup_conversation": True, "content_type": "application/json", "stream": False}
            generation = dict(base)
            generation["request_path"] = "/v1/images/generations"
            generation["body_base64"] = base64.b64encode(b'{"prompt":"cat"}').decode()
            result = handle_images(generation)
            body = json.loads(base64.b64decode(result["body_base64"]))
            self.assertTrue(body["data"][0]["b64_json"])

            edit = dict(base)
            edit["request_path"] = "/v1/images/edits"
            raw = json.dumps({"prompt": "edit", "images": [{"image_url":
                "data:image/png;base64," + base64.b64encode(PNG_1X1).decode()}]}).encode()
            edit["body_base64"] = base64.b64encode(raw).decode()
            result = handle_images(edit)
            self.assertEqual(result["status_code"], 200)

    def test_busy_credential_switches_immediately(self) -> None:
        """首选凭证忙碌时应立即尝试其他账号而不是阻塞。"""
        credentials = [{"access_token": "token-a"}, {"access_token": "token-b"}]
        first_lock = protocol.credential_lock(protocol.credential_key(credentials[0]))
        first_lock.acquire()
        try:
            with patch("helper.protocol.generate_with_credential",
                       side_effect=lambda _request, credential, _settings, _control:
                       {"token": credential["access_token"]}):
                result = generate_one(ImageRequest("cat"), credentials, {"timeout_seconds": 1}, 0)
        finally:
            first_lock.release()
        self.assertEqual(result["token"], "token-b")

    def test_busy_credential_waits_until_released(self) -> None:
        """全部账号忙碌时应在总截止时间内排队并在释放后继续。"""
        credentials = [{"access_token": "token-a"}]
        lock = protocol.credential_lock(protocol.credential_key(credentials[0]))
        lock.acquire()
        timer = threading.Timer(0.1, lock.release)
        timer.start()
        try:
            with patch("helper.protocol.generate_with_credential", return_value={"ok": True}):
                result = generate_one(ImageRequest("cat"), credentials, {"timeout_seconds": 1}, 0)
        finally:
            timer.join()
            if lock.locked():
                lock.release()
        self.assertTrue(result["ok"])

    def test_generation_deadline_releases_credential(self) -> None:
        """上游长时间不返回时应按总超时结束并释放凭证锁。"""
        with MockServer() as mock:
            mock.server.generation_delay = 0.5
            payload = {"credentials": [{"access_token": "timeout-token"}],
                       "base_url": mock.server.base_url, "timeout_seconds": 0.1,
                       "cleanup_conversation": True, "content_type": "application/json", "stream": False,
                       "request_path": "/v1/images/generations",
                       "body_base64": base64.b64encode(b'{"prompt":"cat"}').decode()}
            with self.assertRaises(HelperError) as caught:
                handle_images(payload)
            key = protocol.credential_key(payload["credentials"][0])
            self.assertTrue(protocol.credential_lock(key).acquire(blocking=False))
            protocol.credential_lock(key).release()
        self.assertEqual(caught.exception.status_code, 504)

    def test_poll_retries_524(self) -> None:
        """Cloudflare 524 应按临时错误重试而不是立即判定失败。"""
        with MockServer() as mock:
            mock.server.poll_statuses = [524, 200]
            control = RequestControl(2)
            backend = WebImageBackend("fake-token", mock.server.base_url, control)
            try:
                with patch.object(control, "sleep"):
                    files, _ = backend.poll_image_references("conv_mock", ["seed"], [], 1)
            finally:
                backend.close()
        self.assertIn(OUTPUT_FILE_ID, files)

    def test_cancel_closes_registered_resource(self) -> None:
        """取消请求时应关闭正在阻塞的网络资源并使后续阶段停止。"""
        class Resource:
            """记录测试资源是否被关闭。"""

            def __init__(self) -> None:
                """创建未关闭的测试资源。"""
                self.closed = False

            def close(self) -> None:
                """记录关闭动作。"""
                self.closed = True

        control = RequestControl(10)
        resource = Resource()
        control.register(resource)
        control.cancel()
        self.assertTrue(resource.closed)
        with self.assertRaises(HelperError):
            control.timeout()

    def test_cooldown_is_visible_to_following_requests(self) -> None:
        """账号限流后并发和后续请求应跳过其冷却状态。"""
        credentials = [{"access_token": "token-a"}, {"access_token": "token-b"}]
        calls = {"token-a": 0, "token-b": 0}

        def fake_generate(_request: ImageRequest, credential: dict, _settings: dict,
                          _control: RequestControl) -> dict:
            """模拟首个账号限流、第二个账号成功。"""
            token = credential["access_token"]
            calls[token] += 1
            if token == "token-a":
                raise UpstreamError("rate limited", 429)
            return {"token": token}

        with patch("helper.protocol.generate_with_credential", side_effect=fake_generate):
            first = generate_one(ImageRequest("cat"), credentials, {"timeout_seconds": 1}, 0)
            with protocol._cursor_lock:
                protocol._credential_cursor = 0
            second = generate_one(ImageRequest("cat"), credentials, {"timeout_seconds": 1}, 0)
        self.assertEqual(first["token"], "token-b")
        self.assertEqual(second["token"], "token-b")
        self.assertEqual(calls["token-a"], 1)

    def test_expired_credential_switches_to_refreshed_token(self) -> None:
        """使用中失效的旧 Token 应冷却，刷新后的新 Token 应立即可用。"""
        credentials = [{"access_token": "expired-token"}, {"access_token": "backup-token"}]

        def fake_generate(_request: ImageRequest, credential: dict, _settings: dict,
                          _control: RequestControl) -> dict:
            """模拟旧 Token 在 conversation 阶段返回 401。"""
            if credential["access_token"] == "expired-token":
                raise UpstreamError("expired", 401)
            return {"token": credential["access_token"]}

        with patch("helper.protocol.generate_with_credential", side_effect=fake_generate):
            result = generate_one(ImageRequest("cat"), credentials, {"timeout_seconds": 1}, 0)
        old_key = protocol.credential_key(credentials[0])
        refreshed = {"access_token": "refreshed-token"}
        self.assertEqual(result["token"], "backup-token")
        self.assertTrue(protocol.credential_cooling(old_key))
        self.assertFalse(protocol.credential_cooling(protocol.credential_key(refreshed)))

        with patch("helper.protocol.generate_with_credential",
                   return_value={"token": refreshed["access_token"]}):
            result = generate_one(ImageRequest("cat"), [refreshed], {"timeout_seconds": 1}, 0)
        self.assertEqual(result["token"], "refreshed-token")

    def test_all_expired_credentials_preserve_401(self) -> None:
        """全部快照凭证失效时应通知 DLL 重新读取 CPA 凭证。"""
        def fake_generate(_request: ImageRequest, _credential: dict, _settings: dict,
                          _control: RequestControl) -> dict:
            """模拟唯一凭证在执行阶段失效。"""
            raise UpstreamError("expired", 401)

        with patch("helper.protocol.generate_with_credential", side_effect=fake_generate):
            with self.assertRaises(HelperError) as caught:
                generate_one(ImageRequest("cat"), [{"access_token": "expired"}],
                             {"timeout_seconds": 1}, 0)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.code, "credential_expired")

    def test_all_forbidden_credentials_are_treated_as_expired(self) -> None:
        """全部凭证返回 403 时也应通知 DLL 重新读取 CPA 快照。"""
        def fake_generate(_request: ImageRequest, _credential: dict, _settings: dict,
                          _control: RequestControl) -> dict:
            """模拟上游拒绝已失效的 OAuth 凭证。"""
            raise UpstreamError("forbidden", 403)

        with patch("helper.protocol.generate_with_credential", side_effect=fake_generate):
            with self.assertRaises(HelperError) as caught:
                generate_one(ImageRequest("cat"), [{"access_token": "forbidden"}],
                             {"timeout_seconds": 1}, 0)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.code, "credential_expired")

    def test_concurrent_requests_use_distinct_credentials(self) -> None:
        """高并发请求应并行使用不同账号且同账号不重入。"""
        credentials = [{"access_token": f"token-{index}"} for index in range(4)]
        guard = threading.Lock()
        active: set[str] = set()
        overlap: list[str] = []
        peak = 0

        def fake_generate(_request: ImageRequest, credential: dict, _settings: dict,
                          _control: RequestControl) -> dict:
            """记录同时使用的凭证并模拟短时生成。"""
            nonlocal peak
            token = credential["access_token"]
            with guard:
                if token in active:
                    overlap.append(token)
                active.add(token)
                peak = max(peak, len(active))
            time.sleep(0.05)
            with guard:
                active.remove(token)
            return {"token": token}

        with patch("helper.protocol.generate_with_credential", side_effect=fake_generate):
            with ThreadPoolExecutor(max_workers=32) as executor:
                futures = [executor.submit(generate_one, ImageRequest("cat"), credentials,
                                           {"timeout_seconds": 5}, index) for index in range(100)]
                results = [future.result() for future in futures]
        self.assertEqual(len(results), 100)
        self.assertEqual(overlap, [])
        self.assertGreaterEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()
