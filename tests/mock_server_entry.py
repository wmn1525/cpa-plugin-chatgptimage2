"""独立运行模拟 ChatGPT 服务，供真实 CPA 集成测试使用。"""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer

from tests.test_helper import MockChatGPTHandler


def main() -> int:
    """解析端口并持续运行模拟服务。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), MockChatGPTHandler)
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

