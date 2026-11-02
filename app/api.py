"""提供同步HTTP接口；成功只返回下游JSON，错误单独返回。"""
from contextlib import asynccontextmanager
import threading
from typing import Any
from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .adapter import QwenConfig
from .config import VERSION
from .input import parse_request
from .models import ExtractionError
from .service import ExtractionService


def create_app(service=None):
    """测试可注入本地服务；正式请求首次到达时才读取模型配置。"""
    # 初始化加锁，避免多个首批请求各自创建一套线程池。
    holder = {'service': service}
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        if holder['service'] is not None:
            holder['service'].close()

    app = FastAPI(title='离婚纠纷要素式起诉状字段抽取', version=VERSION, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        error = ExtractionError('INVALID_REQUEST', '请求体必须是合法JSON', status=400, fields=['/'])
        return JSONResponse(status_code=400, content=error.body())

    @app.get('/')
    def root():
        return {'service': 'divorce-dispute', 'docs': '/docs', 'health': '/healthz', 'readiness': '/readyz'}

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    # 就绪检查只验证配置，不发起付费模型请求，也不承诺网络已连通。
    @app.get('/readyz')
    def ready():
        try:
            if holder['service'] is None:
                QwenConfig.from_env()
            return {'status': 'ready', 'modelConnectivityChecked': False}
        except ExtractionError as exc:
            return JSONResponse(status_code=503, content=exc.body())

    @app.post('/v1/extract')
    def extract(payload: Any=Body(...)):
        rid = payload.get('requestId') if isinstance(payload, dict) and isinstance(payload.get('requestId'), str) else None
        try:
            parse_request(payload)  # 先拒绝非法请求，再初始化模型服务。
            with lock:
                if holder['service'] is None:
                    holder['service'] = ExtractionService.from_env()
                current = holder['service']
            return current.extract(payload)
        except ExtractionError as exc:
            return JSONResponse(status_code=exc.status, content=exc.body(rid))
        except Exception:
            # 兜底错误不回显异常原文，避免泄露案件正文、内部路径或供应商密钥。
            return JSONResponse(status_code=500, content=ExtractionError('INTERNAL', '内部错误', status=500).body(rid))

    return app


app = create_app()
