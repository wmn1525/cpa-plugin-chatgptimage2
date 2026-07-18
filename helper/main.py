"""助手进程入口与长度前缀 RPC 服务。"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, BinaryIO

from helper.errors import HelperError
from helper.protocol import handle_images

_write_lock = threading.Lock()


def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    """从标准输入读取一个大端长度前缀 JSON 帧。"""
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError("incomplete frame header")
    length = struct.unpack(">I", header)[0]
    if length <= 0 or length > 512 * 1024 * 1024:
        raise ValueError("invalid frame length")
    payload = stream.read(length)
    if len(payload) != length:
        raise EOFError("incomplete frame body")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("frame must be object")
    return value


def write_frame(stream: BinaryIO, value: dict[str, Any]) -> None:
    """向标准输出写入一个大端长度前缀 JSON 帧。"""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    with _write_lock:
        stream.write(struct.pack(">I", len(payload)))
        stream.write(payload)
        stream.flush()


def process_request(request: dict[str, Any], output: BinaryIO) -> None:
    """处理单个 RPC 请求并写回统一响应。"""
    request_id = int(request.get("id") or 0)
    try:
        if request.get("method") != "images":
            raise HelperError("unknown helper method", 400, "unknown_method")
        result = handle_images(request.get("payload") or {})
        response = {"id": request_id, "ok": True, "result": result}
    except HelperError as exc:
        response = {"id": request_id, "ok": False,
                    "error": {"code": exc.code, "message": str(exc), "http_status": exc.status_code}}
    except Exception as exc:
        response = {"id": request_id, "ok": False,
                    "error": {"code": "helper_error", "message": str(exc), "http_status": 502}}
    write_frame(output, response)


def serve_stdio() -> int:
    """在标准输入输出上运行并发 RPC 服务。"""
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    with ThreadPoolExecutor(max_workers=8) as executor:
        while True:
            request = read_frame(input_stream)
            if request is None:
                return 0
            executor.submit(process_request, request, output_stream)


def main() -> int:
    """解析命令行并启动助手服务。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    args = parser.parse_args()
    return serve_stdio() if args.stdio else 2


if __name__ == "__main__":
    raise SystemExit(main())

