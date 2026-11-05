"""串联主体识别、并行抽取、结果合并和最终校验。"""
import copy
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from jsonschema import Draft202012Validator
from .entity_index import build_entities
from .models import ExtractionError, ModelRequest
from .tasks import load_schema, task_specs, prompt
from .businesses.registry import get_business
from .event_log import emit, exception_location


class ExtractionPipeline:
    """一个服务实例复用线程池和配额，处理多个并发案件。"""
    def __init__(self, adapter, *, workers=5, capacity=20, validator=None):
        if not 1 <= workers <= 5 or capacity < 1:
            raise ValueError('workers must be 1..5, capacity positive')
        self.adapter = adapter
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='extraction')
        # quota限制模型调用数；slots限制进入流程的案件数，两者含义不同。
        self.quota = threading.BoundedSemaphore(workers)
        self.slots = threading.BoundedSemaphore(capacity)
        self.validator = validator
        self.validators = {}
        self.validator_lock = threading.Lock()
        self.closed = False

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)

    def call(self, request, schema, metrics, context):
        """所有模型调用都走此入口；可重试错误最多再尝试一次。"""
        for attempt in range(2):
            response = None
            started = None
            try:
                with self.quota:
                    started = time.monotonic()
                    response = self.adapter.generate_json(request)
                metric={'phase':request.phase,'attempt':attempt+1,'model':response.model,
                    'latencyMs':response.latency_ms,'usage':response.usage,'finishReason':response.finish_reason}
                metrics.append(metric)
                parsed = copy.deepcopy(response.parsed)
                if response.finish_reason != 'stop':
                    raise ExtractionError('MODEL_INCOMPLETE', '模型未完成输出', status=502, retryable=True)
                is_task = 'data' in schema['properties']
                # 证据属于内部诊断，不参与任务是否可交付的结构判定。
                data_schema = schema['properties']['data'] if is_task else schema
                errors = list(Draft202012Validator(data_schema).iter_errors(parsed.get('data') if is_task else parsed))
                if errors:
                    if is_task and attempt == 1 and isinstance(parsed.get('data'), dict):
                        metric['schemaRepaired'] = True
                    else:
                        paths = ['/' + '/'.join(str(p) for p in e.absolute_path) for e in errors[:10]]
                        request = ModelRequest(request.phase, request.system, request.prompt + '\n修正上次输出：' + json.dumps({
                            'previousOutput': parsed, 'errors': paths,
                            'instruction': '按任务契约重新输出完整JSON；原文缺失或不明确的字段用null，不猜测。'
                        }, ensure_ascii=False), request.max_tokens)
                        raise ExtractionError('MODEL_SCHEMA', '模型未返回可处理的任务对象', status=502, fields=paths, retryable=True)
                if is_task:
                    # 只合并任务负责的模块/编号，避免模型越界输出造成覆盖。
                    allowed = data_schema['properties']
                    parsed['data'] = {key: {k: v for k, v in value.items() if k in allowed[key]['properties']}
                                      if isinstance(value, dict) else value
                                      for key, value in parsed['data'].items() if key in allowed}
                    for key in ('evidence', 'conflicts'):
                        if not isinstance(parsed.get(key), list):
                            parsed[key] = []
                return parsed
            except Exception as exc:
                failure = exc if isinstance(exc, ExtractionError) else ExtractionError(
                    'INTERNAL', '内部错误', status=500, diagnostics=exception_location(exc))
                config = getattr(self.adapter, 'config', None)
                diagnostics = {
                    'task': request.phase, 'attempt': attempt + 1,
                    'model': getattr(config, 'model', None),
                    'maxTokens': request.max_tokens if request.max_tokens is not None else getattr(config, 'max_tokens', None),
                    'finishReason': response.finish_reason if response is not None else None,
                    'promptTokens': None, 'completionTokens': None, 'totalTokens': None,
                    **failure.diagnostics,
                }
                if response is not None:
                    for source, target in (('prompt_tokens', 'promptTokens'),
                                           ('completion_tokens', 'completionTokens'), ('total_tokens', 'totalTokens')):
                        value = response.usage.get(source)
                        diagnostics[target] = value if type(value) is int and value >= 0 else None
                will_retry = failure.retryable and attempt == 0
                emit('model_attempt_failed', level=logging.WARNING if will_retry else logging.ERROR,
                     **context, **diagnostics, errorCode=failure.code, willRetry=will_retry,
                     elapsedMs=int((time.monotonic() - started) * 1000) if started is not None else None)
                if not will_retry:
                    # 复制错误而不修改共享异常，避免并发请求之间串入任务上下文。
                    raise ExtractionError(failure.code, failure.message, status=failure.status,
                        fields=failure.fields, retryable=failure.retryable, stage='model',
                        diagnostics=diagnostics) from None
                time.sleep(0.1)

    @staticmethod
    def merge(base, incoming, path='/blocks'):
        """合并互不重叠的任务结果；同一路径被重复写入时拒绝静默覆盖。"""
        for k, v in incoming.items():
            if k not in base:
                base[k] = copy.deepcopy(v)
            elif isinstance(base[k], dict) and isinstance(v, dict):
                ExtractionPipeline.merge(base[k], v, path + '/' + k)
            else:
                raise ExtractionError('TASK_OVERLAP', '不同任务重复写入同一字段', fields=[path + '/' + k])

    def run(self, document, *, business_type=None):
        """先共享已知主体，再并行抽取；案情缺失不阻断其他字段。"""
        if self.closed:
            raise ExtractionError('SERVICE_CLOSED', '服务已关闭', status=503, stage='capacity')
        if not self.slots.acquire(blocking=False):
            raise ExtractionError('BUSY', '并发请求已达到容量，请稍后重试', status=503, retryable=True, stage='capacity')
        futures = []
        metrics = []
        warnings = []
        business_type = business_type or document.business_type
        context = {'requestId': document.request_id, 'businessType': business_type}
        stage = 'model'
        try:
            # 主体识别同样经过call入口，不能绕过共享模型配额。
            business = get_business(business_type)
            tasks = task_specs(load_schema(business_type), business_type)
            entities = build_entities(lambda r, s: self.call(r, s, metrics, context), document, warnings)
            for task in tasks:
                request = ModelRequest(task.name, business.SYSTEM, prompt(task, document, entities))
                # 显式传递关联标识，线程池中的并发案件共享日志格式。
                futures.append(self.executor.submit(self.call, request, task.schema, metrics, context))
            merged = {}
            evidence = []
            conflicts = []
            # 按完成顺序收集，但字段归属由任务划分决定，与完成先后无关。
            for future in as_completed(futures):
                stage = 'model'
                part = future.result()
                stage = 'merge'
                self.merge(merged, part['data'])
                evidence.extend(part['evidence'])
                conflicts.extend(part['conflicts'])
            # 格式处理不补事实；内部证据和指标与下游业务结果分离。
            output = {'docType': business_type, 'blocks': merged}
            stage = 'validation'
            # 校验器按板块缓存，线程池和配额仍由整个服务共用。
            with self.validator_lock:
                if self.validator is None and business_type not in self.validators:
                    from .validation import ResultValidator
                    self.validators[business_type] = ResultValidator(business_type)
                validator = self.validator if self.validator is not None else self.validators[business_type]
            for metric in metrics:
                if metric.get('schemaRepaired'):
                    warnings.append({'code': 'TASK_STRUCTURE_REPAIRED', 'path': metric['phase']})
            audit = validator.validate(output, document, entities, evidence, warnings=warnings, conflicts=conflicts)
            return (output, {'evidence': audit, 'metrics': metrics, 'entities': entities, 'warnings': warnings})
        except ExtractionError as exc:
            raise ExtractionError(exc.code, exc.message, status=exc.status, fields=exc.fields,
                retryable=exc.retryable, stage=exc.stage or stage, diagnostics=exc.diagnostics) from None
        except Exception as exc:
            raise ExtractionError('INTERNAL', '内部错误', status=500, stage=stage,
                                  diagnostics=exception_location(exc)) from None
        finally:
            # 尚未开始的任务可取消；已运行任务要等其结束，再释放案件槽，防止突破容量。
            for future in futures:
                future.cancel()
            for future in futures:
                if not future.cancelled():
                    try:
                        future.result()
                    except Exception:
                        pass  # 清理阶段不覆盖前面已抛出的原始错误。
            self.slots.release()
