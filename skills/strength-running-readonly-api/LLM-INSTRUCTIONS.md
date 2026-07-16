# 力跑双训分析系统：外部模型只读 API 指令

将本文件作为其他大模型、Agent 或自动化程序的调用约束。它只允许读取平台已有的训练数据和已有报告，**不允许写入、不允许同步、不允许触发分析或平台 LLM**。

## 1. 配置与安全

调用程序在本地安全的环境变量/密钥库中保存以下值，不要把完整 Key 放入对话、提示词、模型输出、代码仓库、命令历史或日志：

```sh
export SRDA_BASE_URL="https://your-platform.example"
export SRDA_API_KEY="<secret issued platform API key>"
```

- `SRDA_BASE_URL`：目标平台根地址，不带末尾 `/`。当前部署地址可能是 `http://106.14.241.47:8000`，但它不是稳定默认值；所有命令应使用 `${SRDA_BASE_URL}`。
- `SRDA_API_KEY`：平台签发的完整 `srda_` Bearer Key。它绑定一个用户，服务器会自动按该用户隔离所有数据。不要传递、猜测或尝试覆盖 `user_id`。
- **不要把 `srda_` Key 与训记 Key 或平台 LLM Key 混用。**
- 当前方案 A 为明文 HTTP；Bearer Key 可能被嗅探。HTTP 仅限所有者自用/可信网络，HTTPS（方案 C）之前不可把 Key 分享给他人或用于不可信网络。

## 2. 认证与响应

每次请求都需要：

```text
Authorization: Bearer ${SRDA_API_KEY}
```

成功响应：

```json
{"ok": true, "data": {}}
```

错误响应：

```json
{"ok": false, "error": {"code": 401, "message": "..."}}
```

`error.code` 是数值 HTTP 状态码。必须检查 HTTP 状态和 `ok`，不要把错误对象当业务数据处理。

## 3. 可读取的端点

| 方法与路径 | 用途 |
|---|---|
| `GET /api/v1/meta` | 数据覆盖范围、同步状态、当前目标和最新报告的元数据 |
| `GET /api/v1/context` | 为分析准备的结构化双训上下文；默认最近 90 天 |
| `GET /api/v1/days/{datestr}` | 某日的力量动作/组/容量和跑步活动详情 |
| `GET /api/v1/goals/current` | 当前生效的目标配置；未配置时 `data` 为 `null` |
| `GET /api/v1/goals/history` | 全部目标版本，含当前版本 |
| `GET /api/v1/reports` | 历史报告摘要列表，不含报告全文 |
| `GET /api/v1/reports/{report_id}` | 一份完整的报告快照（结构化层和叙述层） |
| `GET /api/v1/muscle-map` | 训记动作到肌群的映射 |
| `GET /api/v1/rest-notes` | 休整、伤病、出差等中断/恢复标注 |

### 日期规则

- `/api/v1/days/{datestr}` 的 `datestr` 必须为 `YYYY-MM-DD`。
- `/api/v1/context` 可带 `start=YYYY-MM-DD` 与 `end=YYYY-MM-DD`；二者可以各自省略。
- 未传 `end` 时，结束日期是服务器当天；未传 `start` 时，开始日期是结束日前 90 天。
- `start` 不得晚于 `end`。

## 4. curl 调用配方

先在安全环境中设置第 1 节变量。以下命令都只读，并统一使用 `GET`。

### 读取数据覆盖

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/meta"
```

### 读取默认最近 90 天的加工上下文

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/context"
```

### 按日期范围读取加工上下文

```sh
curl --silent --show-error --fail-with-body --get \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  --data-urlencode "start=2026-07-01" \
  --data-urlencode "end=2026-07-16" \
  "${SRDA_BASE_URL}/api/v1/context"
```

### 读取某日详情

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/days/2026-07-16"
```

### 读取当前目标与历史目标

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/goals/current"

curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/goals/history"
```

### 读取报告摘要与一份完整报告

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/reports"

export SRDA_REPORT_ID="<report id from the report list>"
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/reports/${SRDA_REPORT_ID}"
```

### 读取动作肌群映射与休整标注

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/muscle-map"

curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/rest-notes"
```

## 5. 预期失败

| 情况 | HTTP 状态 | 行为 |
|---|---:|---|
| `OUTBOUND_API_ENABLED` 未启用 | 404 | 在鉴权之前拒绝整个 `/api/v1/*` |
| 缺失或格式错误的 Bearer 凭证 | 401 | 不返回数据 |
| Key 无效、已吊销、或绑定用户不再 active | 401 | 不返回数据 |
| 日期格式无效，或 `start > end` | 400 | 修正日期再重试 |
| `report_id` 不存在或不属于当前 Key 的用户 | 404 | 不得推断其他用户是否有该报告 |
| 对任一 `/api/v1/*` 使用写方法 | 405 | API 没有写入能力 |

## 6. 模型解释纪律

1. 所有返回内容均为 Key 所属用户的私有训练资料，只用于用户授权的分析。
2. `reports/{report_id}` 是当时数据、目标和标注条件下生成的历史快照；新数据不会修改旧报告。
3. 缺失字段、训练空缺和未分类动作必须明确说成“不确定/数据未覆盖”，不能断言用户没有训练或直接归因。
4. 双训解释应同时考虑力量、跑步、当前目标和休整标注；个人历史基线优先于通用教科书阈值。
5. 只给带前提的方向性建议，不提供完整训练计划、精确重量/组次数/配速处方，也不做医学诊断或替代专业医疗建议。
6. 不要回显 API Key、Authorization Header、完整私有数据或不必要的个人信息；根据用户请求最小化引用数据。
7. 需要新的同步、补充事实、目标变更或新分析时，说明该 API 无法完成这些动作，应由用户在平台内主动操作。

## 7. 硬性禁止项

- 禁止 `POST`、`PUT`、`PATCH`、`DELETE` 到 `/api/v1/*`。
- 禁止尝试写入训练记录、训练计划、目标、休整标注、肌群映射或报告。
- 禁止让 API 同步训记/佳明、生成/重新生成报告、触发 LLM 或消耗用户额度。
- 禁止通过 Key 外的任何方式访问另一个用户的数据。
- 禁止在输出、日志、代码或提交中泄露凭证。

## 8. 权威来源

- 路由与参数：[app/api_v1.py](../../app/api_v1.py)
- API Key 绑定与吊销：[app/api_keys.py](../../app/api_keys.py)
- API 设计与部署警告：[docs/design/api-design.md §12](../../docs/design/api-design.md#L423-L451)
- 只读架构决策：[ADR 0004](../../docs/adr/0004-outbound-api-reserved-readonly.md)
