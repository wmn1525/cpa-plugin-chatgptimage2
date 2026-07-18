"""独立运行模拟 ChatGPT 服务，供真实 CPA 集成测试使用。"""

from __future__ import annotations

import argparse
import threading
from http.server import ThreadingHTTPServer

from tests.test_helper import MockChatGPTHandler


def main() -> int:
    """解析端口并持续运行模拟服务。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--generation-delay", type=float, default=0)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), MockChatGPTHandler)
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    server.generation_delay = max(0, args.generation_delay)
    server.auth_lock = threading.Lock()
    server.seen_authorizations = set()
    server.poll_lock = threading.Lock()
    server.poll_statuses = []
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
