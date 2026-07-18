"""助手统一错误类型。"""


class HelperError(RuntimeError):
    """保存可返回给 CPA 的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 502, code: str = "upstream_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class UpstreamError(HelperError):
    """保存 ChatGPT 上游状态与可重试属性。"""

    def __init__(self, message: str, status_code: int, retryable: bool = False) -> None:
        super().__init__(message, status_code, "upstream_error")
        self.retryable = retryable

