# 要素式起诉状字段抽取服务

当前使用本地ZIP上传、服务器现有Conda base环境。独立验证流程见 [STAGING_DEPLOYMENT.md](STAGING_DEPLOYMENT.md)，日志排障见 [LOGGING.md](LOGGING.md)。发行包排除本地案件、密钥和测试资料。

从上游已解析文本抽取离婚纠纷和民间借贷字段，成功直接返回下游 `docType + blocks` JSON。支持 `divorce-dispute` 和 `private-lending`，不新增事实、诉讼请求、法条或证据；不解析上传文件，不生成 Word，不调用下游接口，不做异步回调。

## 启动

本地支持Python 3.11+。服务器沿用 `/root/miniconda3/bin/python` 和已有依赖，按独立验证指南检查。使用已有环境变量 `QWEN_BASE_URL`、`QWEN_API_KEY`、`QWEN_MODEL`。密钥不要写进代码、README、测试文件或日志。本项目不自动加载 `.env`。

可选配置：`QWEN_TIMEOUT_S`（默认120秒）、`QWEN_MAX_TOKENS`（默认8192）、`QWEN_ENABLE_THINKING`（true/false，不设则由提供商决定）。配置的 token 上限用于每次模型调用。

```powershell
python main.py
```

统一抽取接口为 `POST /element-extraction/api/v1/divorce/extract`，通过请求中的businessType选择板块。文档位于 `/docs`。

| 板块 | businessType | 响应docType |
|---|---|---|
| 离婚 | `divorce-dispute` | `divorce-dispute` |
| 借贷 | `private-lending` | `private-lending` |

对外地址为 `https://fbwlawonetest.365lawhelp.com/element-extraction/api/v1/divorce/extract`。

当前主服务为8010，本次独立验证实例拟使用服务器本机8011。`/healthz` 只检查进程存活，`/readyz` 检查配置，不验证模型网络连通性。部署到其他端口可使用 `python -m uvicorn app.api:app --host 127.0.0.1 --port 8001`。

## 输入与输出

```json
{
  "requestId": "req-001",
  "businessType": "divorce-dispute",
  "fileContext": ["同一案件的已经解析完成的文本"]
}
```

只读取以上三个必需字段；`notify_url`、元数据等额外字段直接忽略，不进入模型或返回结果，也不触发回调。三个必需字段及其类型、业务类型和长度限制仍会校验。1至20段，每段最多50000字符，总计最多100000字符。上游须保留字段标签及可靠勾选状态；同一请求不得混合不同案件。缺少勾选信息时不能从模板选项文字推断选择。

成功返回 HTTP 200，根对象严格为 `docType` 和 `blocks`，没有 requestId、status、review 等额外业务字段。示例输入和参考输出见 `tests/fixtures/D001/`。

未知叶子使用 null；没有实际记录的重复列表为 null；不创建全空主体、代理人、财产或子女占位。明确无代理人时输出一条 category=代理人、data.has=无、其余 data 字段 null 的记录。调解五项始终保持五个槽位，未知槽位 null。该空值写法尚待下游渲染联调验证。

日期只转换完整日期的显示格式为 YYYY年M月D日，不补不完整日期。金额保留数量、单位和限定，不做计算，不把“5万元”转换成“5.00”。离婚板块按原契约处理自然人和代理人。借贷支持自然人、组织、第三人及签署日期，组织extra只保留联系人，其他选项说明使用value/text，落款依据明确原文。

文本不完整、缺少任一方姓名或没有明确诉请时，仍返回完整结构，未知字段为null。局部内容冲突只清空对应字段；内部证据路径、引文或证据缺失只记录警告，不因此拒绝整份JSON。非法枚举不按相近值替换。成功表示完成结构化处理，不代表所有字段均已填写或已经人工核实。

模型超时等技术故障仍返回JSON错误体，例如：

```json
{"requestId":"req-001","error":{"code":"MODEL_TIMEOUT","message":"模型请求超时","fields":[],"retryable":true}}
```

HTTP 400：输入非法；502：模型调用失败或重试后仍未返回可处理的JSON对象；504：模型超时；500：配置/内部错误；503：容量满或服务关闭。案情缺失、局部冲突和内部证据问题不再直接返回422。技术故障不伪装成一次正常的全空抽取。

## 实现和约束

`config/divorce.schema.json` 冻结目标结构，也是运行时任务投影和抽取描述的权威来源；`config/divorce.fields.json` 为由Schema导出的77条字段规则（含数组通配路径，不是每案填值数量）。修改Schema后可运行 `python scripts/export_fields.py` 同步字典；发布后已有ID应冻结，不应通过调整Schema顺序重新编号。离婚五任务分为当事人、一般诉请、子女和补偿诉请、事实、剩余模块；每条目标路径只有一个任务负责。先建立共享实体上下文，后运行五任务。借贷使用 `config/private-lending.schema.json` 和132条字段规则，按当事人、诉请、借贷事实、担保事实及剩余模块划分五任务。自然人和组织分支规则计入各自字段记录。业务入口在 `app/businesses/registry.py` 注册，专属规则位于各业务模块。

默认最多5个模型调用并发；所有实体识别、抽取和重试共享配额。请求容量20，满时立即503；模型输出和临时服务错误最多重试一次。格式失败的重试带具体Schema纠错提示；若任务已返回可处理的局部对象，则由程序补齐结构、过滤多余键并将不合法值置空。线程池和请求槽为单进程范围，多实例部署需另行接入共享限额。

提示词要求非空字段尽量提供原文引文；证据路径和引文不可靠时仅记警告，已知字段仍可依据原文保留。明确无来源的姓名、数字或金额单位、矛盾的归属等局部置空。`/data/`、`/blocks/data/`等已知任务前缀统一到最终`/blocks/`，不改动当事人对象内部合法的data层。空白差异可定位回原文，不做近似词句匹配。`extract_with_audit()` 返回内部evidence、metrics、entities、warnings；普通API仍只返回业务JSON，服务日志记录警告数量，未增加审计查询或自动落盘功能。模型的语义准确率和漏抽率后续持续优化。

## 测试与真实模型验证

```powershell
python -B -m unittest discover -v
python -B scripts/live_check.py --case D001 --repeat 2
python -B scripts/live_check.py --case D002
```

单元测试不联网。真实模型测试仅在显式执行脚本时发送对应案件文本，可能产生提供商调用费用。D001来自指定Word，D002为人工构造的口述案例。D001参考JSON不是全文逐字比对基准，允许有来源且语义一致的叙述和财产集合表示。

`reports/live-*-summary.json` 保存不含案件正文的验收摘要；`reports/live-*.private.json` 保存含案件个人信息的结果和证据，仅供本地诊断，已加入gitignore，不应上传或公开。示例文件本身也含用户提供的个人信息。业务日志记录配置摘要、请求结束摘要及失败尝试，使用requestId和businessType关联；详细字段见LOGGING.md。

真实测试通过只说明已测试案例通过，不代表所有离婚案件准确率或下游Word渲染已经验收。下游需验证null、代理人无记录、调解空槽、列表扩容和授权对象的实际落位。

民间借贷真实验收可执行 `python -B scripts/live_check.py --case L001`（Word）和 `--case L002`（虚构组织及担保案例），此操作会调用已有模型账号。金额验收容许保留原文人民币和元标识，不做单位换算。
