"""先检查输入是否合法，再让案件文本进入模型。"""
from collections.abc import Mapping
from . import config
from .businesses.registry import get_business
from .models import Document, ExtractionError


def parse_request(payload) -> Document:
    """只读取三个必需字段，保留原文内容以便后续证据定位。"""
    def bad(message, path):
        raise ExtractionError('INVALID_REQUEST', message, status=400, fields=[path])
    if not isinstance(payload, Mapping):
        bad('请求体必须是JSON对象', '/')
    # 上游可携带额外字段；仅提取本服务需要的字段，不处理回调地址等信息。
    required = {'requestId', 'businessType', 'fileContext'}
    if not required.issubset(payload):
        bad('缺少requestId、businessType或fileContext必需字段', '/')
    rid = payload['requestId']
    if not isinstance(rid, str) or not rid.strip() or len(rid) > 128:
        bad('requestId必须为1至128字符的非空字符串', '/requestId')
    get_business(payload['businessType'])
    items = payload['fileContext']
    if not isinstance(items, list) or not 1 <= len(items) <= config.MAX_ITEMS:
        bad('fileContext必须为1至20项的文本数组', '/fileContext')
    # 分别检查每段和总长度；不裁剪文本，否则字符偏移会失效。
    for i, item in enumerate(items):
        if not isinstance(item, str) or not item.strip() or len(item) > config.MAX_ITEM_CHARS:
            bad('每项必须为非空文本且不超过50000字符', f'/fileContext/{i}')
    if sum(map(len, items)) > config.MAX_TOTAL_CHARS:
        bad('文本总长度不能超过100000字符', '/fileContext')
    return Document(rid, tuple(items), payload['businessType'])
