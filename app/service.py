"""提供抽取服务入口，内部审计信息与下游业务结果分开返回。"""
import time
from .adapter import QwenAdapter, QwenConfig
from .input import parse_request
from .pipeline import ExtractionPipeline
from .config import VERSION
from .event_log import emit, request_context, request_finished
from .businesses.registry import BUSINESSES


class ExtractionService:
    """服务层复用同一条抽取流水线，不掺入HTTP响应格式。"""
    def __init__(self, adapter, **options):
        self.pipeline = ExtractionPipeline(adapter, **options)

    @classmethod
    def from_env(cls):
        config = QwenConfig.from_env()
        service = cls(QwenAdapter(config))
        # 记录实际加载值，便于确认环境变量在本进程中已经生效。
        emit('config_loaded', version=VERSION, businessType=None, businessTypes=list(BUSINESSES), model=config.model,
             maxTokens=config.max_tokens, timeoutSeconds=config.timeout_s,
             enableThinking=config.enable_thinking)
        return service

    def extract_with_audit(self, payload, *, started=None):
        """供本地验收使用，额外返回证据和指标；日志不记录案件正文。"""
        started = time.monotonic() if started is None else started
        context = request_context(payload)
        stage = 'input'
        try:
            document = parse_request(payload)
            stage = 'pipeline'
            output, audit = self.pipeline.run(document, business_type=payload['businessType'])
        except Exception as error:
            request_finished(context, started=started, error=error, stage=stage)
            raise
        request_finished(context, started=started, warnings=audit['warnings'])
        return (output, audit)

    def extract(self, payload, *, started=None):
        """普通调用只取下游业务JSON，不把内部审计字段混入结果。"""
        return self.extract_with_audit(payload, started=started)[0]

    def close(self):
        self.pipeline.close()
