"""定义请求、模型结果和统一错误，供各层共享。"""
from dataclasses import dataclass, field
from typing import Any


class ExtractionError(Exception):
    """各层使用同一错误类型，HTTP层只负责转换状态码和错误体。"""
    def __init__(self, code: str, message: str, *, status: int = 422,
                 fields: list[str] | None = None, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.status = (code, message, status)
        self.fields, self.retryable = (fields or [], retryable)

    def body(self, request_id=None):
        return {'requestId': request_id, 'error': {'code': self.code,
                'message': self.message, 'fields': self.fields, 'retryable': self.retryable}}


@dataclass(frozen=True)
class Document:
    """通过输入校验的案件；冻结后避免并发任务修改共享原文。"""
    request_id: str
    file_context: tuple[str, ...]


@dataclass(frozen=True)
class ModelRequest:
    """单次模型请求；max_tokens为空时使用适配器配置。"""
    phase: str
    system: str
    prompt: str
    max_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    """模型返回值与调用指标；不在此层判断案件内容是否正确。"""
    parsed: dict[str, Any]
    model: str = 'mock'
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str = 'stop'
