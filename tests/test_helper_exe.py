"""验证 PyInstaller 助手产物的标准输入输出协议。"""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import unittest

from tests.test_helper import MockServer


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
                raw = json.dumps(payload).encode()
                process.stdin.write(struct.pack(">I", len(raw)) + raw)
                process.stdin.flush()
                length = struct.unpack(">I", process.stdout.read(4))[0]
                response = json.loads(process.stdout.read(length))
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


if __name__ == "__main__":
    unittest.main()
