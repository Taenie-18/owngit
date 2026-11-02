"""提供抽取服务入口，内部审计信息与下游业务结果分开返回。"""
import logging
import time
from .adapter import QwenAdapter, QwenConfig
from .input import parse_request
from .pipeline import ExtractionPipeline

logger = logging.getLogger(__name__)


class ExtractionService:
    """服务层复用同一条抽取流水线，不掺入HTTP响应格式。"""
    def __init__(self, adapter, **options):
        self.pipeline = ExtractionPipeline(adapter, **options)

    @classmethod
    def from_env(cls):
        return cls(QwenAdapter(QwenConfig.from_env()))

    def extract_with_audit(self, payload):
        """供本地验收使用，额外返回证据和指标；日志不记录案件正文。"""
        document = parse_request(payload)
        start = time.monotonic()
        output, audit = self.pipeline.run(document)
        logger.info('extract_complete requestId=%r elapsedMs=%d calls=%d warnings=%d', document.request_id,
            int((time.monotonic() - start) * 1000), len(audit['metrics']), len(audit['warnings']))
        return (output, audit)

    def extract(self, payload):
        """普通调用只取下游业务JSON，不把内部审计字段混入结果。"""
        return self.extract_with_audit(payload)[0]

    def close(self):
        self.pipeline.close()
