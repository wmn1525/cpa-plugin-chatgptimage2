"""验证 HTTP 200 上游载荷中的凭证失效识别。"""

from __future__ import annotations

import json
import unittest

from helper.backend import contains_invalid_token_error, iter_sse_json
from helper.errors import UpstreamError


class FakeResponse:
    """提供 iter_sse_json 所需的最小响应对象。"""

    def __init__(self, values: list[dict]) -> None:
        """把 JSON 对象编码成 SSE data 帧。"""
        self._lines: list[bytes] = []
        for value in values:
            self._lines.extend([("data: " + json.dumps(value)).encode(), b""])

    def iter_lines(self) -> list[bytes]:
        """返回预构造的 SSE 行。"""
        return self._lines


class CredentialEventTests(unittest.TestCase):
    """覆盖结构化 Token 错误与用户文本误判边界。"""

    def test_sse_token_invalidated_raises_401(self) -> None:
        """HTTP 200 SSE error 事件应立即转换为 401。"""
        response = FakeResponse([{"type": "error", "error": {"code": "token_invalidated"}}])
        with self.assertRaises(UpstreamError) as caught:
            list(iter_sse_json(response))
        self.assertEqual(caught.exception.status_code, 401)

    def test_poll_token_revoked_is_detected(self) -> None:
        """轮询 JSON 中嵌套的 token_revoked 应被识别。"""
        payload = {"task": {"status": "failed", "error": {"message": "token_revoked"}}}
        self.assertTrue(contains_invalid_token_error(payload))

    def test_prompt_text_does_not_trigger_token_error(self) -> None:
        """普通消息中包含错误关键词时不应误判用户凭证。"""
        payload = {"message": {"content": {"parts": ["draw the words token_invalidated"]}}}
        self.assertFalse(contains_invalid_token_error(payload))


if __name__ == "__main__":
    unittest.main()
