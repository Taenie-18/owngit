"""输出精简的JSON事件，各业务板块共用字段与标准输出通道。"""
from collections import Counter
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import threading
import time
import traceback


logger = logging.getLogger('app.events')
_lock = threading.Lock()
_fields = {
    'version', 'requestId', 'businessType', 'businessTypes', 'model', 'maxTokens', 'timeoutSeconds',
    'enableThinking', 'status', 'elapsedMs', 'warningCounts', 'stage', 'errorCode',
    'task', 'attempt', 'finishReason', 'promptTokens', 'completionTokens', 'totalTokens',
    'willRetry', 'providerStatus', 'exceptionType', 'errorLocation',
}


def emit(event, *, level=logging.INFO, **fields):
    """只接收白名单元数据，独立配置应用INFO日志并避免重复传播。"""
    with _lock:
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter('%(message)s'))
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    record = {
        'timestamp': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
        'level': logging.getLevelName(level), 'event': event,
        **{key: value for key, value in fields.items() if key in _fields},
    }
    logger.log(level, json.dumps(record, ensure_ascii=False, separators=(',', ':')))


def request_context(payload):
    """只提取有限长度的关联标识，非法请求体也可以记录结果摘要。"""
    payload = payload if isinstance(payload, dict) else {}
    result = {}
    for key, limit in (('requestId', 128), ('businessType', 64)):
        value = payload.get(key)
        result[key] = value if isinstance(value, str) and 0 < len(value) <= limit else None
    return result


def exception_location(error):
    """仅记录异常类型和代码位置，不输出可能包含案件内容的异常原文。"""
    frames = traceback.extract_tb(error.__traceback__)
    location = None
    if frames:
        frame = frames[-1]
        location = f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
    return {'exceptionType': type(error).__name__, 'errorLocation': location}


def request_finished(context, *, started=None, error=None, stage=None, warnings=()):
    """每次调用只写一条结束摘要，警告按类型计数。"""
    status = getattr(error, 'status', 500) if error is not None else 200
    fields = {
        **context, 'status': status,
        'elapsedMs': int((time.monotonic() - started) * 1000) if started is not None else None,
    }
    if error is None:
        fields['warningCounts'] = dict(Counter(item['code'] for item in warnings))
        level = logging.INFO
    else:
        fields.update(stage=getattr(error, 'stage', None) or stage,
                      errorCode=getattr(error, 'code', 'INTERNAL'))
        diagnostics = getattr(error, 'diagnostics', {})
        fields.update({key: diagnostics[key] for key in ('task', 'exceptionType', 'errorLocation')
                       if key in diagnostics})
        if not hasattr(error, 'code'):
            fields.update(exception_location(error))
        level = logging.ERROR if status >= 500 else logging.WARNING
    emit('request_finished', level=level, **fields)
