"""验证 PyInstaller 助手产物的标准输入输出协议。"""

from __future__ import annotations

import base64
import json
import os
import queue
import struct
import subprocess
import threading
import time
import unittest

from tests.test_helper import MockServer


def write_request(process: subprocess.Popen, value: dict) -> None:
    """向助手进程写入一帧测试请求。"""
    raw = json.dumps(value).encode()
    process.stdin.write(struct.pack(">I", len(raw)) + raw)
    process.stdin.flush()


def read_response(process: subprocess.Popen) -> dict:
    """从助手进程读取一帧测试响应。"""
    length = struct.unpack(">I", process.stdout.read(4))[0]
    return json.loads(process.stdout.read(length))


class HelperExecutableTests(unittest.TestCase):
    """运行最终助手 EXE 完成模拟生图。"""

    def test_executable_roundtrip(self) -> None:
        """最终 EXE 应能通过长度前缀协议返回图片。"""
        executable = os.path.abspath(os.path.join("dist", "cpaimage-helper.exe"))
        if not os.path.exists(executable):
            self.skipTest("助手 EXE 尚未构建")
        with MockServer() as mock:
            process = subprocess.Popen([executable, "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                payload = {"id": 7, "method": "images", "payload": {
                    "request_path": "/v1/images/generations",
                    "body_base64": base64.b64encode(b'{"prompt":"cat"}').decode(),
                    "content_type": "application/json", "stream": False,
                    "credentials": [{"access_token": "fake-token", "auth_index": "1"}],
                    "base_url": mock.server.base_url, "timeout_seconds": 30,
                    "cleanup_conversation": True}}
                write_request(process, payload)
                response = read_response(process)
                self.assertTrue(response["ok"], response)
                body = json.loads(base64.b64decode(response["result"]["body_base64"]))
                self.assertTrue(body["data"][0]["b64_json"])
            finally:
                process.kill()
                process.wait(timeout=10)
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()

    def test_executable_cancel_interrupts_generation(self) -> None:
        """取消帧应中断 EXE 内正在等待的上游请求。"""
        executable = os.path.abspath(os.path.join("dist", "cpaimage-helper.exe"))
        if not os.path.exists(executable):
            self.skipTest("助手 EXE 尚未构建")
        with MockServer() as mock:
            mock.server.generation_delay = 5
            process = subprocess.Popen([executable, "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            responses: queue.Queue[dict] = queue.Queue()

            def read_two() -> None:
                """后台读取取消确认和原请求终止响应。"""
                responses.put(read_response(process))
                responses.put(read_response(process))

            reader = threading.Thread(target=read_two, daemon=True)
            try:
                payload = {"id": 8, "method": "images", "payload": {
                    "request_path": "/v1/images/generations",
                    "body_base64": base64.b64encode(b'{"prompt":"cat"}').decode(),
                    "content_type": "application/json", "stream": False,
                    "credentials": [{"access_token": "fake-token", "auth_index": "1"}],
                    "base_url": mock.server.base_url, "timeout_seconds": 30,
                    "cleanup_conversation": True}}
                write_request(process, payload)
                time.sleep(0.2)
                reader.start()
                write_request(process, {"id": 9, "method": "cancel", "payload": {"request_id": 8}})
                first = responses.get(timeout=3)
                second = responses.get(timeout=3)
                received = {first["id"]: first, second["id"]: second}
                self.assertEqual(set(received), {8, 9})
                self.assertTrue(received[9]["ok"])
                self.assertTrue(received[9]["result"]["cancelled"])
                self.assertFalse(received[8]["ok"])
                self.assertEqual(received[8]["error"]["http_status"], 504)
            finally:
                process.kill()
                process.wait(timeout=10)
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
