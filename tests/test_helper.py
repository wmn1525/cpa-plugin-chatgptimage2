"""网页生图助手单元与模拟上游测试。"""

from __future__ import annotations

import base64
import json
import struct
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helper.backend import extract_conversation_id, extract_references
from helper.main import read_frame, write_frame
from helper.protocol import handle_images, parse_image_request
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

    def do_GET(self) -> None:
        """处理首页、下载地址和图片响应。"""
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
        elif self.path.startswith("/backend-api/conversation/"):
            self._json({"conversation_id": "conv_mock", "mapping": {"x": {"message": {
                "author": {"role": "tool"}, "metadata": {"async_task_type": "image_gen"},
                "content": {"parts": [f"file-service://{OUTPUT_FILE_ID}"]}}}}})
        else:
            self._json({}, 404)

    def do_POST(self) -> None:
        """处理 Sentinel、上传和 conversation 请求。"""
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
        else:
            self._json({}, 404)

    def do_PUT(self) -> None:
        """接收模拟对象存储图片上传。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(201)
        self.end_headers()

    def do_PATCH(self) -> None:
        """接收会话隐藏请求。"""
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
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        """关闭模拟 HTTP 服务。"""
        self.server.shutdown()
        self.server.server_close()


class HelperTests(unittest.TestCase):
    """验证助手的核心兼容行为。"""

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


if __name__ == "__main__":
    unittest.main()

