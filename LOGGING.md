# 日志模块功能与故障定位说明

更新日期为 2026-11-04。本文依据当前项目代码整理，供开发、测试和运维排查使用。日志中的时间、请求 ID 和用量示例均为演示数据。

## 1. 本次更新内容

| 能力 | 当前实现 | 排查价值 |
|---|---|---|
| 统一格式 | 应用日志由 `app.events` 输出，每行一个 JSON 对象，带 UTC 时间和级别 | 可按字段检索、复制和后续集中采集 |
| 请求关联 | 请求摘要和模型失败日志均携带 `requestId`、`businessType` | 串联一次案件抽取，区分不同业务板块 |
| 配置摘要 | 配置加载成功后记录版本、模型、输出额度、超时和思考模式 | 核对运行进程实际读到的配置 |
| 请求结束摘要 | 每次抽取调用结束记录状态、耗时，成功时汇总警告，失败时记录阶段和错误码 | 快速判断最终结果及故障所在环节 |
| 模型失败诊断 | 每次失败记录任务、尝试次数、结束状态、输出额度、可用的 token 用量及重试决定 | 定位截断、超时、鉴权和输出格式问题 |
| 内部异常定位 | 未预期异常记录类型及最内层代码位置 | 找到需要检查的函数和行号 |
| 字段白名单 | 常规日志仅输出约定元数据，异常使用类型及代码位置 | 控制日志内容和体积 |

一次正常请求通常产生一条 `request_finished`。模型初始化所在的请求还会产生 `config_loaded`。发生模型失败时，每次失败尝试增加一条 `model_attempt_failed`。Uvicorn 自身的启动与 HTTP 访问日志继续由 systemd 收集。

当前支持 `divorce-dispute` 和 `private-lending`，共用日志格式及模型配额。请求与尝试日志带具体业务标识，公共模型配置加载日志使用businessTypes列出支持板块。

## 2. 三类业务日志

### 2.1 `config_loaded` 配置加载成功

```jsonl
{"timestamp":"2026-11-04T02:00:00.000+00:00","level":"INFO","event":"config_loaded","version":"1.1.1","businessType":null,"businessTypes":["divorce-dispute","private-lending"],"model":"qwen3.8-max","maxTokens":16384,"timeoutSeconds":120,"enableThinking":false}
```

当前服务采用延迟初始化。正常 HTTP 部署下，首次通过入参校验的抽取请求会初始化模型服务并产生此日志。重启后的健康检查和就绪检查会先于该日志出现。

`maxTokens` 是该进程加载的默认输出额度，模型失败日志中的 `maxTokens` 是对应尝试实际发送的额度。任务显式覆盖额度时，后者优先。代码缺省值为 8192，实际运行值以日志为准。

`version` 来自代码中的 `VERSION`，目前为 `1.1.1`。定位具体提交时，还应在服务器项目目录查看 `git rev-parse HEAD`。

### 2.2 `request_finished` 请求最终结果

成功示例约耗时 28 秒，包含一条证据缺失警告。

```jsonl
{"timestamp":"2026-11-04T02:00:28.430+00:00","level":"INFO","event":"request_finished","requestId":"req-div-001","businessType":"divorce-dispute","status":200,"elapsedMs":28430,"warningCounts":{"EVIDENCE_MISSING":1}}
```

失败示例表示事实抽取任务在模型阶段失败，本服务最终返回 502。

```jsonl
{"timestamp":"2026-11-04T02:02:17.200+00:00","level":"ERROR","event":"request_finished","requestId":"req-div-002","businessType":"divorce-dispute","status":502,"elapsedMs":137200,"stage":"model","errorCode":"MODEL_INCOMPLETE","task":"facts"}
```

正常结束的请求以此事件判断最终结果。严重进程退出、强制终止等情况，可能需要结合 Uvicorn 和 systemd 日志追查尚未生成结束摘要的请求。

### 2.3 `model_attempt_failed` 单次模型尝试失败

```jsonl
{"timestamp":"2026-11-04T02:01:01.240+00:00","level":"WARNING","event":"model_attempt_failed","requestId":"req-div-002","businessType":"divorce-dispute","task":"facts","attempt":1,"model":"qwen3.8-max","maxTokens":16384,"finishReason":"length","promptTokens":5000,"completionTokens":16384,"totalTokens":21384,"providerStatus":200,"errorCode":"MODEL_INCOMPLETE","willRetry":true,"elapsedMs":61240}
{"timestamp":"2026-11-04T02:02:06.140+00:00","level":"ERROR","event":"model_attempt_failed","requestId":"req-div-002","businessType":"divorce-dispute","task":"facts","attempt":2,"model":"qwen3.8-max","maxTokens":16384,"finishReason":"length","promptTokens":5000,"completionTokens":16384,"totalTokens":21384,"providerStatus":200,"errorCode":"MODEL_INCOMPLETE","willRetry":false,"elapsedMs":64800}
```

这两条示例表示同一任务连续两次收到 `length`，且网关报告的输出用量均达到发送额度。排查时优先核对网关输出限制、该任务输出量及重试情况。

模型任务最多尝试两次。允许重试且尚有机会时记 `WARNING`，该任务停止重试时记 `ERROR`。主体上下文构建支持部分结构失败后的回退，因此最终状态仍需查看 `request_finished`。

## 3. 字段速查

字段按事件出现。网关未提供、类型无效或当前无法取得的诊断值可能为 `null`。

| 字段 | 含义与使用方式 |
|---|---|
| `timestamp` | UTC 时间，`+00:00` 表示零时区。北京时间在此基础上加 8 小时 |
| `level` | `INFO` 为配置加载或成功摘要，`WARNING` 为客户端请求问题或准备重试的模型失败，`ERROR` 为服务错误或结束重试的模型失败 |
| `event` | 日志事件名，先用它区分配置、最终结果和单次尝试 |
| `requestId` | 上游请求关联 ID。建议每次独立请求使用唯一 ID，排查时配合时间范围查询 |
| `businessType` | 请求所属板块，取divorce-dispute或private-lending；公共配置事件为null |
| `businessTypes` | 配置事件列出的支持板块集合 |
| `version` | 项目现有 `VERSION` 值，出现在配置加载日志中 |
| `status` | 本服务最终响应的 HTTP 状态，出现在请求摘要中 |
| `stage` | 失败阶段，见下一节 |
| `errorCode` | 程序归类的错误码，见错误码表 |
| `task` | 模型子任务名，用于定位具体模块 |
| `attempt` | 当前任务的尝试序号，取 1 或 2 |
| `willRetry` | 程序是否会为该任务安排下一次尝试 |
| `model` | 配置选用的模型名 |
| `maxTokens` | 配置默认额度或本次尝试实际发送的最大输出额度，按事件区分 |
| `timeoutSeconds` | 单次模型请求的超时配置 |
| `enableThinking` | 已加载的思考模式配置。`null` 表示该参数交给提供商默认处理 |
| `finishReason` | 网关返回的结束状态。正常通过适配器检查需要为 `stop` |
| `promptTokens` | 网关报告的输入 token 用量 |
| `completionTokens` | 网关报告的输出 token 用量 |
| `totalTokens` | 网关报告的总 token 用量 |
| `providerStatus` | 模型网关的 HTTP 状态。200 表示 HTTP 交互成功，业务输出仍需经过结束状态、JSON 和结构检查 |
| `elapsedMs` | 毫秒耗时。请求摘要从接口处理开始计时，含服务初始化、排队、重试与清理等待。模型失败事件从拿到模型配额后计时，包含本次调用及随后本地处理 |
| `warningCounts` | 校验警告按类型累计的次数，同一字段可产生多条警告 |
| `exceptionType` | 未预期异常的 Python 类型，例如 `ValueError` |
| `errorLocation` | 异常最内层位置，格式为 `文件名:行号:函数名` |

HTTP 响应错误体里的 `retryable` 表示错误类别是否允许重试。日志里的 `willRetry` 表示本轮流程的实际安排。第二次失败后，前者可能仍为 `true`，后者为 `false`。

非法 JSON、缺少有效关联标识等请求可能记录 `requestId=null`。请求体尚未完成解析时，耗时也可能为 `null`，可结合提交时间和访问日志查询。

## 4. 失败阶段和任务名称

| `stage` | 所在环节 | 优先检查 |
|---|---|---|
| `input` | JSON 或业务入参校验 | 请求格式、必需字段、业务类型、文本数组及长度限制 |
| `config` | 模型服务初始化 | 环境变量、模型配置、项目配置文件及初始化异常位置 |
| `capacity` | 请求准入或服务状态 | 并发案件数、进程是否正在关闭 |
| `model` | 主体识别或字段抽取调用及任务结构检查 | `task`、错误码、结束状态、重试及网关响应 |
| `merge` | 并行任务结果合并 | 是否存在字段归属重叠或内部合并异常 |
| `validation` | 最终结构修复与校验 | 异常类型、代码位置、校验逻辑 |
| `pipeline` | 服务层记录的流水线兜底阶段 | 同一请求其他日志及可用异常位置 |

| `task` | 离婚纠纷任务内容 |
|---|---|
| `entities` | 先识别实际主体，构建共享上下文 |
| `parties` | 原告、被告和代理人 |
| `general_claims` | 诉请正文、离婚、财产、债务、费用及其他诉请 |
| `family_claims` | 子女抚养、抚养费、探望和补偿赔偿等诉请 |
| `facts` | 事实与理由、法律依据和证据等对应字段 |
| `remaining` | 保全、调解和落款 |

借贷任务为 `entities`、`parties`、`claims`、`loan_facts`、`security_facts`、`remaining`，分别对应主体、当事人、诉请、借贷事实、担保事实及剩余模块。

并行任务的日志可能交错，查询时同时使用 `requestId`、`task` 和 `attempt`。一个任务最终失败后，其他已经开始的任务还可能继续完成或产生失败日志，请求摘要会等待清理结束后输出。

## 5. 错误码与定位方法

下表中的 HTTP 状态适用于错误最终传到抽取接口的情况。

| 错误码 | HTTP 状态 | 当前代码中的含义 | 定位方法 |
|---|---|---|---|
| `INVALID_REQUEST` | 400 | JSON 非法、必需字段缺失或类型、长度、业务类型不满足要求 | 查看响应体的 `error.fields`，核对上游提交内容 |
| `MODEL_CONFIG` | 500 | 模型配置缺失、连接地址格式或可选参数非法 | 核对配置文件和服务实际加载方式 |
| `MODEL_TIMEOUT` | 504 | 模型调用超时或连接超时 | 对照失败耗时、加载的超时配置和网关响应时间 |
| `MODEL_NETWORK` | 502 | 模型连接或网络读取失败 | 检查服务器到模型网关的地址、DNS、网络及连接稳定性 |
| `MODEL_AUTH` | 502 | 网关返回 401 或 403 | 核对密钥及模型访问权限，检查 `providerStatus` |
| `MODEL_RATE_LIMIT` | 502 | 网关返回 429 | 核对供应商调用配额、限流和服务并发 |
| `MODEL_HTTP` | 502 或 504 | 网关返回其他 HTTP 错误，网关 408 映射为本服务 504 | 先查 `providerStatus`，再查网关该时段的记录 |
| `MODEL_INCOMPLETE` | 502 | `finish_reason` 为非 `stop` 或缺失 | 核对 `finishReason`、输出额度和用量。`length` 时优先排查输出截断，其他状态转查网关对应原因 |
| `MODEL_INVALID_JSON` | 502 | 网关响应包装无效，或模型正文无法解析为 JSON 对象 | 查看可用结束状态和用量，必要时由网关侧核对协议与响应格式 |
| `MODEL_SCHEMA` | 502 | 任务输出结构暂时无法处理，达到重试条件时进入下一次尝试 | 核对任务名与契约。可处理的局部任务对象会进入结构修复，主体上下文也有回退处理，最终看请求摘要 |
| `BUSY` | 503 | 同一服务实例已达到案件容量，当前默认为 20 | 查看同一时段请求量、慢请求和模型限流情况 |
| `SERVICE_CLOSED` | 503 | 服务已进入关闭状态 | 查看重启时间与 systemd 进程日志 |
| `TASK_OVERLAP` | 422 | 内部任务重复写入同一路径 | 查看响应中的字段路径，检查任务划分和合并逻辑 |
| `INTERNAL` | 500 | 未预期内部异常 | 根据 `stage`、`exceptionType`、`errorLocation` 和服务器代码提交定位 |

`finishReason=null` 可能来自超时、网络故障、响应缺失或无效结束状态。结合 `errorCode` 判断具体类别。用量为 `null` 时，继续向模型网关查询该时段记录。

## 6. 警告类型与抽取结果

成功摘要中的 `warningCounts` 是校验事件次数。某个字段可能经过多个检查，计数无法直接换算为错误字段数或准确率。需要检查具体字段时，可结合原文、抽取 JSON 和本地 `extract_with_audit()` 结果。

| 警告类型 | 含义与处理 |
|---|---|
| `EVIDENCE_MISSING` | 字段缺少可用内部证据记录，继续结合原文和其他检查处理 |
| `EVIDENCE_INVALID` | 内部证据路径、来源索引或引文无效，该条证据跳过 |
| `SOURCE_CONFLICT` | 来源明确冲突，对应字段清空后再修复结构 |
| `VALUE_UNGROUNDED` | 姓名、数字或金额单位等未通过原文依据检查，对应字段置空 |
| `UNCERTAIN_VALUE` | 具体要素中包含无法确定的表达，对应字段置空 |
| `UNCONFIRMED_NEGATIVE` | 否、无等状态缺少明确否定依据，对应字段置空 |
| `INVALID_DATE` | 完整日期无效，对应日期字段置空 |
| `UNKNOWN_FIELD` | 模型结果出现契约之外的字段，修复时过滤 |
| `INVALID_VALUE` | 模型字段类型或枚举不合法，按结构规则修复或置空 |
| `UNSUPPORTED_CATEGORY` | 列表记录类别超出目标契约，该记录跳过 |
| `PARTY_MISMATCH` | 当事人姓名与明确角色上下文不匹配，相关记录清空或移除 |
| `PARTY_ATTRIBUTE_MISMATCH` | 当事人属性与可核对信息不一致，对应属性置空 |
| `CHOICE_CONFLICT` | 勾选或状态与已知明细冲突，相关选择置空 |
| `AMBIGUOUS_SUBJECT` | 相关请求缺少明确主体，相关选择置空 |
| `ASSIGNMENT_MISMATCH` | 财产、债务或子女归属与可靠原文勾选不一致，归属字段置空 |
| `MEDIATION_POSITION` | 调解选项与原文对应位置不一致，对应槽位置空 |
| `TASK_STRUCTURE_REPAIRED` | 重试后任务仍为局部结构，程序继续补齐、过滤和修复 |
| `ENTITY_CONTEXT_UNAVAILABLE` | 主体上下文结构校验最终未通过，后续任务继续直接依据原文抽取 |
| `ENTITY_CONFLICT` | 主体识别报告了冲突，后续继续结合原文处理 |
| `ENTITY_UNGROUNDED` | 主体姓名或引文无法对应原文，该条共享上下文跳过 |

未知信息按字段规则输出 `null`，只有发生了相应校验事件才累计警告。HTTP 200 表示本次结构化处理完成，字段内容仍需按业务验收要求核对。

## 7. 服务器查询命令

以下命令适用于以 `divorce-dispute` 为服务名的 systemd 部署，可在任意目录查看日志。命令中的请求 ID 可替换为测试页面上的值。

### 7.1 最近记录与实时观察

```bash
sudo journalctl -u divorce-dispute -n 100 --no-pager -o cat
```

```bash
sudo journalctl -u divorce-dispute -f -o cat
```

实时观察结束时按 `Ctrl+C`。

### 7.2 查询某次请求

```bash
sudo journalctl -u divorce-dispute --since "1 hour ago" --no-pager -o cat \
  | grep -F -- '"requestId":"req-div-002"'
```

先找到 `request_finished`，读取状态、阶段和错误码，再按任务查看每次 `model_attempt_failed`。上游重复使用同一个 ID 时，缩小时间范围。

### 7.3 查询近期错误、业务板块和配置

```bash
sudo journalctl -u divorce-dispute --since "1 hour ago" --no-pager -o cat \
  | grep -F -- '"level":"ERROR"'
```

```bash
sudo journalctl -u divorce-dispute --since "1 hour ago" --no-pager -o cat \
  | grep -F -- '"businessType":"divorce-dispute"'
```

```bash
sudo journalctl -u divorce-dispute -b --no-pager -o cat \
  | grep -F -- '"event":"config_loaded"'
```

`-b` 查询当前系统启动周期。一次系统启动中可能有多次服务重启，应结合时间选择对应进程的配置记录。`config_loaded` 没有请求 ID，可按当前请求前最近一次配置加载记录核对。

### 7.4 查询指定北京时间区间

北京时间 2026-11-04 10:00 至 10:15 对应下列 UTC 区间。

```bash
sudo journalctl -u divorce-dispute --utc \
  --since "2026-11-04 02:00:00 UTC" \
  --until "2026-11-04 02:15:00 UTC" \
  --no-pager -o cat
```

### 7.5 核对启动方式和配置文件

```bash
sudo systemctl status divorce-dispute --no-pager -l
sudo systemctl show divorce-dispute -p ExecStart -p WorkingDirectory -p EnvironmentFiles
```

按实际 `EnvironmentFiles` 路径检查允许展示的配置项。以下使用项目部署约定的路径。

```bash
sudo grep -E '^[[:space:]]*QWEN_(MODEL|MAX_TOKENS|TIMEOUT_S|ENABLE_THINKING)=' \
  /etc/divorce-dispute.env
```

配置文件修改并重启后，发起一次合法抽取请求，再从 `config_loaded` 核对实际加载值。配置加载成功表示本地初始化完成，模型网关连通性由真实调用验证。

## 8. 常见排查场景

### 8.1 长案件失败，短案件成功

1. 用请求 ID 找到最终摘要，核对是否为 `MODEL_INCOMPLETE`。
2. 找出失败任务和两次尝试，读取实际 `finishReason`。
3. 若为 `length`，核对 `completionTokens` 与 `maxTokens`，并查询网关实际输出限制。
4. 核对进程是否加载了预期额度，以及该任务是否显式覆盖额度。
5. 用相同案件复测，比较前后日志。若为其他结束状态，按该状态向网关查询。

### 8.2 等待很久才失败

1. 先看请求最终 `errorCode`，按 `MODEL_TIMEOUT`、`MODEL_INCOMPLETE` 等实际类别排查。
2. 比较总耗时与各次失败耗时，检查重试和同一时段并发。
3. 当前流程含主体识别、并行任务、配额等待和清理等待，总耗时可能超过单次模型超时配置。
4. 网关或上游提前断开时，继续核对双方日志及请求时间。

### 8.3 出现失败尝试，最终返回 200

查看 `attempt` 和 `willRetry`，确认是否重试成功。主体上下文结构失败还可能进入原文回退，此时成功摘要可能含 `ENTITY_CONTEXT_UNAVAILABLE`。根据最终结果及警告类型核对抽取质量。

### 8.4 返回 200，但部分字段为 null

先对照原文确认信息是否缺失或含义不明，再查看警告计数。`SOURCE_CONFLICT`、`VALUE_UNGROUNDED` 等可以提示局部置空原因。常规日志保留分类计数，具体字段和引文需结合抽取结果及受控的本地验收核对。

### 8.5 只看到 Uvicorn 访问日志

1. 核对访问是否为抽取接口，健康检查和就绪检查使用 Uvicorn 访问日志。
2. 查看服务实际工作目录、启动命令与部署提交，确认该实例加载了日志更新。
3. 检查时间范围、服务名和进程重启时间，扩大查询范围核对原始输出。
4. 使用一次合法抽取或明确的非法入参测试，观察是否产生请求摘要。

### 8.6 请求最终为 INTERNAL

查看 `stage`、`exceptionType` 和 `errorLocation`。在服务器实际项目目录查看 `git rev-parse HEAD`，再打开对应版本的文件和行号。日志记录的是最内层代码位置，开发排查时结合调用路径和输入条件复现。

## 9. 日志存储、诊断边界与验收

应用统一输出到标准输出，systemd 部署由 journald 收集。历史保留范围、轮转和磁盘配额由服务器 journald 配置决定。正常 HTTP 请求的结果通过响应交付，内部证据明细保存在本次内存审计结果中。本地验收脚本显式执行时才会生成相应报告。

常规日志采用约定字段白名单，保留关联标识、状态和统计。案件正文、证据引文、提示词、模型原始正文、供应商错误正文和密钥均在该白名单之外。未预期异常使用类型与代码位置定位。

更新时需要包含新增的统一日志模块。服务器更新代码并重启后，发起抽取请求验证配置与请求摘要。模型诊断的可用字段取决于网关实际响应。

本地已完成 66 项回归测试和真实 Uvicorn 启动及 HTTP 输出验证。模型截断、重试成功、超时、鉴权失败、内部异常与并发关联使用模拟网关或适配器验证。服务器部署后的诊断仍需通过实际网关调用确认。

验收时检查以下结果。

- 正常请求只有一条业务结束摘要，带正确的请求 ID 和业务类型。
- 配置加载记录能反映该进程的实际默认输出额度。
- 连续两次截断可查到任务、尝试序号、结束状态、发送额度和可用用量。
- 首次失败后成功，可查到失败尝试和最终 200 摘要。
- 并发请求按各自 ID 和任务关联，失败信息能对应正确案件。
- 非法请求、配置失败和内部异常均能定位到相应阶段。

## 10. 实现文件索引

| 模块 | 职责 |
|---|---|
| [event_log.py](C:/Users/admin/Desktop/Divorce_dispute/app/event_log.py) | JSON 输出、字段白名单、请求摘要和安全代码位置 |
| [api.py](C:/Users/admin/Desktop/Divorce_dispute/app/api.py) | 入参解析与模型服务初始化之前的失败摘要 |
| [service.py](C:/Users/admin/Desktop/Divorce_dispute/app/service.py) | 配置加载摘要、抽取调用结束摘要和警告计数 |
| [adapter.py](C:/Users/admin/Desktop/Divorce_dispute/app/adapter.py) | 从网关响应提取允许的诊断信息 |
| [pipeline.py](C:/Users/admin/Desktop/Divorce_dispute/app/pipeline.py) | 模型失败尝试、重试决定、并发上下文与失败阶段 |
| [models.py](C:/Users/admin/Desktop/Divorce_dispute/app/models.py) | 错误内部诊断信息与 HTTP 错误体定义 |

独立验证实例的日志命令将服务名替换为 `divorce-dispute-staging`，部署步骤见 [STAGING_DEPLOYMENT.md](STAGING_DEPLOYMENT.md)。配置摘要中的businessTypes同时列出两个板块。
