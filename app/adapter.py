"""负责单次模型调用和错误归类，不包含案件抽取规则。"""
import copy
import json
import math
import os
import socket
import time
from dataclasses import dataclass
from urllib import request, error
from .models import ExtractionError, ModelRequest, ModelResponse


@dataclass(frozen=True)
class QwenConfig:
    """从环境变量读取连接配置；不把密钥写入仓库。"""
    base_url: str
    model: str
    api_key: str
    timeout_s: float = 120
    max_tokens: int = 8192
    enable_thinking: bool | None = None

    @classmethod
    def from_env(cls):
        values = {key: os.getenv(key, '').strip() for key in ('QWEN_BASE_URL', 'QWEN_MODEL', 'QWEN_API_KEY')}
        if not all(values.values()) or not values['QWEN_BASE_URL'].startswith(('http://', 'https://')):
            raise ExtractionError('MODEL_CONFIG', '请配置有效的QWEN_BASE_URL、QWEN_MODEL和QWEN_API_KEY', status=500)
        try:
            timeout = float(os.getenv('QWEN_TIMEOUT_S', '120'))
            tokens = int(os.getenv('QWEN_MAX_TOKENS', '8192'))
            if not math.isfinite(timeout) or timeout <= 0 or tokens <= 0:
                raise ValueError()
        except ValueError:
            raise ExtractionError('MODEL_CONFIG', '模型超时与token上限必须为有效正数', status=500) from None
        thinking = os.getenv('QWEN_ENABLE_THINKING', '').strip().lower()
        if thinking not in ('', 'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
            raise ExtractionError('MODEL_CONFIG', 'QWEN_ENABLE_THINKING格式错误', status=500)
        return cls(values['QWEN_BASE_URL'], values['QWEN_MODEL'], values['QWEN_API_KEY'], timeout,
                   tokens, None if not thinking else thinking in ('true','1','yes','on'))


class QwenAdapter:
    def __init__(self, config: QwenConfig):
        self.config = config

    def payload(self, req: ModelRequest):
        """组装供应商请求；任务显式设置的token上限优先。"""
        result = {'model':self.config.model, 'temperature':0,
                  'max_tokens':req.max_tokens if req.max_tokens is not None else self.config.max_tokens,
                  'response_format':{'type':'json_object'},
                  'messages':[{'role':'system','content':req.system},{'role':'user','content':req.prompt}]}
        if self.config.enable_thinking is not None:
            result['enable_thinking'] = self.config.enable_thinking
        return result

    def generate_json(self, req):
        url = self.config.base_url.rstrip('/')
        if not url.endswith('/chat/completions'):
            url += '/chat/completions'
        payload = self.payload(req)
        http_req = request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json', 'Authorization':f'Bearer {self.config.api_key}'})
        diagnostics = {'model': self.config.model, 'maxTokens': payload['max_tokens'],
                       'finishReason': None, 'promptTokens': None, 'completionTokens': None,
                       'totalTokens': None, 'providerStatus': None}
        start = time.monotonic()
        try:
            with request.urlopen(http_req, timeout=self.config.timeout_s) as response:
                diagnostics['providerStatus'] = getattr(response, 'status', 200)
                body = response.read()
            raw = json.loads(body.decode('utf-8'))
            choice = raw['choices'][0]
            finish = choice.get('finish_reason')
            usage = raw.get('usage') or {}
            if not isinstance(usage, dict):
                usage = {}
            # 先保留允许的诊断字段，再判断是否截断，失败时同样能追查用量。
            for source, target in (('prompt_tokens', 'promptTokens'),
                                   ('completion_tokens', 'completionTokens'), ('total_tokens', 'totalTokens')):
                value = usage.get(source)
                diagnostics[target] = value if type(value) is int and value >= 0 else None
            diagnostics['finishReason'] = finish if isinstance(finish, str) and len(finish) <= 64 else None
            if finish != 'stop':
                raise ExtractionError('MODEL_INCOMPLETE', '模型输出未正常完成', status=502, retryable=True)
            parsed = json.loads(choice['message']['content'])
            if not isinstance(parsed, dict):
                raise ValueError()
            return ModelResponse(parsed, str(raw.get('model') or self.config.model),
                {k:v for k,v in usage.items() if isinstance(v,int) and not isinstance(v,bool)},
                int((time.monotonic()-start)*1000), finish)
        except error.HTTPError as exc:
            # 只保留可操作的错误类别，不向调用方回显供应商正文或密钥。
            code = exc.code
            diagnostics['providerStatus'] = code
            failure = ExtractionError('MODEL_RATE_LIMIT' if code == 429 else 'MODEL_AUTH' if code in (401,403) else 'MODEL_HTTP',
                '模型接口调用失败', status=504 if code == 408 else 502,
                retryable=code in (408,429) or code >= 500)
        except (socket.timeout, TimeoutError):
            failure = ExtractionError('MODEL_TIMEOUT', '模型请求超时', status=504, retryable=True)
        except error.URLError as exc:
            timeout = isinstance(exc.reason, (socket.timeout, TimeoutError))
            failure = ExtractionError('MODEL_TIMEOUT' if timeout else 'MODEL_NETWORK', '模型连接失败',
                                      status=504 if timeout else 502, retryable=True)
        except OSError:
            failure = ExtractionError('MODEL_NETWORK', '模型连接失败', status=502, retryable=True)
        except ExtractionError as exc:
            failure = exc
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, UnicodeDecodeError):
            failure = ExtractionError('MODEL_INVALID_JSON', '模型返回的JSON结构无效', status=502, retryable=True)
        failure.diagnostics = diagnostics
        raise failure from None


class MockAdapter:
    """单元测试替身，按任务返回预设结果，不发起网络请求。"""
    def __init__(self, responses):
        self.responses = responses

    def generate_json(self, request):
        value = self.responses[request.phase]
        if isinstance(value, Exception):
            raise value
        return ModelResponse(copy.deepcopy(value))
