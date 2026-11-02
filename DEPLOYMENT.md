# Linux测试服务器部署说明

本说明对应当前同步JSON抽取版本，采用Git拉取、Python虚拟环境和systemd运行。仅准备部署材料，不代表已在Linux或远程服务器完成验证。

## 上传范围

需要提交：`app/`、`config/`、`deploy/`、`main.py`、`requirements.txt`、`requirements-server.txt`、`README.md`、本文件、`.gitignore`、`.gitattributes`、`.env.example`。

本次默认不提交：`doc/`、`reports/`、`tests/`、`scripts/`、IDE配置、缓存、虚拟环境、真实环境配置和密钥。这些本地文件不删除。tests中不仅有真实案例，也有包含姓名、电话的断言，因此暂将本地验收目录整体排除；scripts依赖这些本地资料，也不属于服务运行依赖。后续应先准备可公开的脱敏测试集，再单独纳入版本控制。服务器通过本说明中的虚构请求联调，不能直接用本地完整测试命令验收一个不含测试资料的Git克隆。

不要把真实密钥填进`.env.example`。应用不会自动加载`.env`，服务器使用`/etc/divorce-dispute.env`，由systemd注入环境变量。

## 发布前需要提供

- Git仓库地址、发布分支或提交号，以及服务器的只读拉取权限。
- Linux运行账号、安装目录（下文默认`/opt/divorce-dispute`）及Python 3.11环境、venv、pip、Git、curl。
- 当前模型网关地址、模型名称、有效API Key；密钥在服务器单独配置。
- 服务器到模型网关的DNS、HTTPS、代理及证书配置。代码使用系统证书校验，不关闭TLS验证。
- 服务端口（默认8000）及允许访问的联调方IP。当前API没有内置鉴权，8000只对受控测试网或白名单开放；公网入口交由已有网关提供认证和HTTPS。
- 如有反向代理或调用方网关，确认同步长请求的超时设置。可先按600秒进行低并发联调，再根据实际耗时调整；这不是服务的总耗时保证。

## 本地首次提交示例

当前项目尚未初始化Git。以下命令供首次建库时执行；仓库地址自行替换。已有仓库不重复执行初始化和分支创建。

```powershell
Set-Location C:\Users\admin\Desktop\Divorce_dispute
git init
git branch -M main
git add .gitignore .gitattributes .env.example requirements.txt requirements-server.txt README.md DEPLOYMENT.md main.py app config deploy
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "Prepare divorce extraction test deployment"
git remote add origin YOUR_GIT_REPOSITORY_URL
git push -u origin main
```

提交前检查暂存区文件和内容。`.gitignore`只对未跟踪文件生效，已经跟踪的敏感文件不会因为增加忽略规则而自动退出版本控制。如果曾提交真实密钥，应撤销该密钥并另行处理仓库历史，不能只依靠删除当前文件。

## 服务器安装

以有目录写权限的部署账号执行；先确认Python 3.11和venv可用。示例中的仓库地址、目录和账号必须替换为实际值。

```bash
git clone YOUR_GIT_REPOSITORY_URL /opt/divorce-dispute
cd /opt/divorce-dispute
git rev-parse HEAD
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-server.txt
.venv/bin/python -m pip check
```

`requirements-server.txt`固定当前本地已验证的运行时版本及其依赖，不包含httpx等本地测试专用依赖，也不包含Windows环境路径。Linux对应包的安装还需在实际服务器确认；不要复制本机的`.venv`到Linux。

由有管理员权限的人员准备环境文件：

```bash
sudo install -m 600 .env.example /etc/divorce-dispute.env
sudoedit /etc/divorce-dispute.env
```

填写`QWEN_BASE_URL`、`QWEN_API_KEY`，确认`QWEN_MODEL`与当前网关一致。默认模型超时120秒、每次输出上限8192 tokens；实际是否支持取决于使用的网关。环境文件使用`KEY=value`格式，不写`export`，也不把真实密钥复制回Git仓库。

## 配置并启动systemd

先编辑`deploy/divorce-dispute.service`，把`User=appuser`改为实际存在的普通运行账号；确认项目路径和虚拟环境路径正确。默认绑定所有网卡，须配合测试网络访问控制；通过本机反向代理访问时可改为`--host 127.0.0.1`。

```bash
sudo install -m 644 deploy/divorce-dispute.service /etc/systemd/system/divorce-dispute.service
sudo systemctl daemon-reload
sudo systemctl enable --now divorce-dispute
sudo systemctl status divorce-dispute --no-pager
sudo journalctl -u divorce-dispute -n 100 --no-pager
```

使用`--workers 1`，不启用`--reload`。一个进程内部已含五个抽取工作线程；多个worker会各自建立配额。主程序`python main.py`默认只监听127.0.0.1，服务器运行以systemd中的显式命令为准。

## 联调检查

先检查进程和配置：

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

healthz只表示进程存活；readyz只验证配置，不验证模型网络和密钥是否能实际调用。然后使用纯虚构数据进行真实模型测试，该操作会产生模型调用：

```bash
curl --fail --max-time 600 \
  -H 'Content-Type: application/json' \
  --data-binary @deploy/smoke-request.json \
  http://127.0.0.1:8000/v1/extract \
  -o /tmp/divorce-smoke-response.json

.venv/bin/python -c "import json; from jsonschema import Draft202012Validator; from app.tasks import load_schema; data=json.load(open('/tmp/divorce-smoke-response.json', encoding='utf-8')); Draft202012Validator(load_schema()).validate(data); print('JSON结构校验通过')"
```

成功根对象应只有`docType`和`blocks`；缺失或不明确字段允许null。网络、模型或配置故障返回JSON错误体，不能把它当作成功业务对象。先确认本机调用成功，再从联调方访问服务器内网IP的8000端口。

客户端和代理的读超时需要单独设置；Uvicorn的keep-alive超时不是单次抽取的总超时。服务尚未实现整个请求的总时间预算，模型重试和排队会增加耗时。

## 更新与回退

```bash
cd /opt/divorce-dispute
git rev-parse HEAD
git pull --ff-only
.venv/bin/python -m pip install -r requirements-server.txt
sudo systemctl restart divorce-dispute
curl --fail http://127.0.0.1:8000/readyz
```

更新前记录旧提交号；出现问题时，可由部署人员检出已确认的旧版本、安装其对应依赖并重启。每次修改代码或环境变量后都要重启服务。原始案件、测试输出和真实密钥不要加入后续提交。

参考：Git忽略规则 https://git-scm.com/docs/gitignore ；systemd环境配置 https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html 。Uvicorn启动选项已核对本项目已安装版本的`python -m uvicorn --help`。
