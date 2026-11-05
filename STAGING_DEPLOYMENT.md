# 多板块抽取服务独立验证部署指南

版本为 1.1.1。部署方式沿用本地 ZIP 上传、服务器现有 Conda base 环境。本次先使用独立目录、独立 systemd 服务和服务器本机端口验证。

## 1. 部署位置

| 项目 | 当前主测试服务 | 本次独立验证实例 |
|---|---|---|
| 工作目录 | `/vdb1/divorce_dispute` | `/vdb1/divorce_dispute_staging` |
| 服务名 | `divorce-dispute` | `divorce-dispute-staging` |
| 监听 | `0.0.0.0:8010` | `127.0.0.1:8011`，启动前确认端口空闲 |
| Python | `/root/miniconda3/bin/python` | `/root/miniconda3/bin/python` |
| 模型配置 | `/etc/divorce-dispute.env` | `/etc/divorce-dispute.env` |

临时实例只做服务器本机验证，两个实例共用模型账号，因此先按低并发逐案验证。

## 2. 本地交付文件

部署 ZIP 的根目录直接包含 `app/`、`config/`、`deploy/` 和 `RELEASE.json`。

- `app/businesses/` 包含离婚、借贷和业务注册模块。
- `config/` 包含两个板块的 Schema 和字段字典。
- `deploy/divorce-dispute-staging.service` 是临时服务配置。
- `deploy/verify_staging.py` 使用虚构案件检查统一接口的两个业务板块，并保存完整响应。
- `RELEASE.json` 记录版本、文件清单和 SHA-256，便于校验上传后的代码。

通过 FinalShell 将本次交付的 ZIP 上传到独立验证目录，以下命令按文件名 `element-extraction-staging-1.1.1.zip` 编写。

## 3. 检查环境并解压

先确认 8011 端口及临时目录可用。

```bash
sudo ss -lntp | grep ':8011'
ls -ld /vdb1/divorce_dispute_staging
```

端口被占用时选择其他空闲端口，并同步修改临时服务与验证命令。如果目录已有其他验证资料，先核对用途。

```bash
mkdir -p /vdb1/divorce_dispute_staging
cd /vdb1/divorce_dispute_staging
unzip -t element-extraction-staging-1.1.1.zip
unzip -oq element-extraction-staging-1.1.1.zip -d /vdb1/divorce_dispute_staging
```

复用手册记录的服务器 Python 与依赖，先检查实际状态。

```bash
/root/miniconda3/bin/python --version
/root/miniconda3/bin/python -m pip check
/root/miniconda3/bin/python -c "import fastapi, uvicorn, pydantic, jsonschema; print('CORE IMPORTS OK')"
```

本次功能使用既有依赖和标准库。当前服务器环境的依赖版本与历史 `requirements-server.txt` 固定版本存在差异，按上述实际环境检查结果部署。

校验发行包内文件和两个板块的 Schema。

```bash
/root/miniconda3/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from app.api import app
from app.tasks import load_schema

release = json.loads(Path('RELEASE.json').read_text(encoding='utf-8'))
for name, expected in release['files'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
for business in ('divorce-dispute', 'private-lending'):
    Draft202012Validator.check_schema(load_schema(business))
print('VERSION', release['version'])
print('FILES AND SCHEMAS OK')
for route in app.routes:
    if 'POST' in (getattr(route, 'methods', None) or set()):
        print(route.path)
PY
```

预期能看到以下唯一抽取路径。

```text
/element-extraction/api/v1/divorce/extract
```

## 4. 启动临时服务

```bash
sudo install -m 644 deploy/divorce-dispute-staging.service /etc/systemd/system/divorce-dispute-staging.service
sudo systemd-analyze verify /etc/systemd/system/divorce-dispute-staging.service
sudo systemctl daemon-reload
sudo systemctl start divorce-dispute-staging
sudo systemctl status divorce-dispute-staging --no-pager -l
```

如果是更新已有临时实例，完成文件检查后执行 `sudo systemctl restart divorce-dispute-staging`。

```bash
curl --fail http://127.0.0.1:8011/healthz
curl --fail http://127.0.0.1:8011/readyz
```

`readyz` 检查配置，模型连通性由下一步真实抽取验证。首次合法抽取初始化模型后，`config_loaded` 应列出两个业务类型和实际 `maxTokens`。服务器此前已调整为 16384，请结合实际日志核对。

## 5. 验证离婚与民间借贷

```bash
cd /vdb1/divorce_dispute_staging
/root/miniconda3/bin/python deploy/verify_staging.py
```

脚本向同一个地址依次提交两种businessType，验证返回 Schema、原告姓名及借贷本金，并输出完整响应文件路径。调用真实模型会消耗账号额度。

示例摘要如下。

```json
{"requestId":"staging-private-lending-demo","businessType":"private-lending","status":200,"schemaValid":true,"resultFile":"/tmp/element-extraction-staging/staging-private-lending-demo.json"}
```

查看实际输出路径对应的文件即可看到抽取后的完整 JSON。

```bash
/root/miniconda3/bin/python -m json.tool /tmp/element-extraction-staging/实际响应文件名.json
```

检查日志时使用临时服务名。

```bash
sudo journalctl -u divorce-dispute-staging -n 100 --no-pager -o cat
sudo journalctl -u divorce-dispute-staging --since "1 hour ago" --no-pager -o cat \
  | grep -F -- '"requestId":"实际请求ID"'
```

businessType仅支持divorce-dispute和private-lending，未知值返回HTTP 400。统一接口根据该字段选择Schema和业务规则，模型配额由两个板块共用。

## 6. 验证结论与后续发布

记录发行包版本、文件校验结果、两个板块的响应、日志关联结果及测试时间。完整 Word 案例的本地验收资料保存在开发端，独立部署包包含虚构的联调数据。

独立验证通过后，再备份并更新 `/vdb1/divorce_dispute`，重启 `divorce-dispute`。更新后在同一个域名路径验证两个板块。

```text
POST https://fbwlawonetest.365lawhelp.com/element-extraction/api/v1/divorce/extract
```

按已确认的 Nginx 前缀转发配置验证实际访问结果。Apifox的统一接口成功响应使用包含两个分支的oneOf Schema，通过docType区分。可以在项目目录导出当前接口的完整响应Schema，再复制到Apifox的JSON Schema配置中。

```bash
/root/miniconda3/bin/python - <<'PY'
import json
from pathlib import Path
from app.api import app
route = '/element-extraction/api/v1/divorce/extract'
schema = app.openapi()['paths'][route]['post']['responses']['200']['content']['application/json']['schema']
path = Path('/tmp/extraction-response.schema.json')
path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding='utf-8')
print(path)
PY
```

每个板块的原始Schema及字段字典继续用于核对各自业务结构和字段含义。主服务发布后的域名校验完成后，可停止临时实例。

```bash
sudo systemctl stop divorce-dispute-staging
```
