"""先检查输入是否合法，再让案件文本进入模型。"""
from collections.abc import Mapping
from . import config
from .models import Document, ExtractionError


def parse_request(payload) -> Document:
    """严格接收三个约定字段，保留原文内容以便后续证据定位。"""
    def bad(message, path):
        raise ExtractionError('INVALID_REQUEST', message, status=400, fields=[path])
    if not isinstance(payload, Mapping):
        bad('请求体必须是JSON对象', '/')
    # 不接受回调地址或其他板块字段，防止调用方式与本版契约混淆。
    required = {'requestId', 'businessType', 'fileContext'}
    if set(payload) != required:
        bad('只接受requestId、businessType、fileContext三个必需字段', '/')
    rid = payload['requestId']
    if not isinstance(rid, str) or not rid.strip() or len(rid) > 128:
        bad('requestId必须为1至128字符的非空字符串', '/requestId')
    if payload['businessType'] != config.BUSINESS_TYPE:
        bad('businessType仅支持divorce-dispute', '/businessType')
    items = payload['fileContext']
    if not isinstance(items, list) or not 1 <= len(items) <= config.MAX_ITEMS:
        bad('fileContext必须为1至20项的文本数组', '/fileContext')
    # 分别检查每段和总长度；不裁剪文本，否则字符偏移会失效。
    for i, item in enumerate(items):
        if not isinstance(item, str) or not item.strip() or len(item) > config.MAX_ITEM_CHARS:
            bad('每项必须为非空文本且不超过50000字符', f'/fileContext/{i}')
    if sum(map(len, items)) > config.MAX_TOTAL_CHARS:
        bad('文本总长度不能超过100000字符', '/fileContext')
    return Document(rid, tuple(items))
