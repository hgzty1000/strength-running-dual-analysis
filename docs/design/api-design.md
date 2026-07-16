# 接口/API 设计

- 日期: 2026-07-09
- 状态: 草案
- 上游文档:
  - [Demo 范围与信息架构设计](./demo-scope-and-information-architecture.md)
  - [低保真 UE 设计](./low-fidelity-ue.md)
  - [技术架构设计](./technical-architecture.md)
  - [数据模型设计](./data-model.md)
- 用途: 固定 demo P0 Web 单体内部 HTTP 接口边界。本文描述网页端使用的服务端接口,不是未来对外只读 Open API。

---

## 1. 设计原则

1. **Session Web API**:demo 网页端使用 cookie session 鉴权。
2. **所有业务接口按当前 session 的 `user_id` 取数**,不从请求体信任 user_id。
3. **接口返回可展示状态,不返回敏感明文**。
4. **上传/同步/分析均用户主动触发**。
5. **分析接口只触发平台内部 AI 分析;未来对外 API 不复用这些写状态端点。**
6. **错误响应可读但不泄露 Key、Authorization header、内部路径和堆栈。**

统一响应建议:

```json
{
  "ok": true,
  "data": {}
}
```

错误:

```json
{
  "ok": false,
  "error": {
    "code": "GARMIN_PARSE_FAILED",
    "message": "Garmin 文件解析失败",
    "details": {}
  }
}
```

---

## 2. 页面路由

```text
GET  /login
GET  /
GET  /data/xunji
GET  /data/garmin
GET  /data/coverage
GET  /goals/current
GET  /goals/history
GET  /analysis/new
GET  /reports
GET  /reports/{id}
GET  /settings/profile
GET  /settings/credentials
GET  /settings/security
```

页面路由服务同一套响应式页面,不区分 Desktop/Mobile 页面。

---

## 3. Auth / Session

### `POST /api/auth/login`

请求:

```json
{
  "username": "owner",
  "password": "..."
}
```

响应:

```json
{
  "ok": true,
  "data": { "redirect": "/" }
}
```

规则:
- 成功后设置 httpOnly cookie。
- 失败不说明用户名是否存在。

### `POST /api/auth/logout`

清除当前 session。

### `GET /api/me`

返回当前用户摘要:

```json
{
  "id": "...",
  "username": "owner",
  "role": "owner"
}
```

---

## 4. Settings

### `GET /api/settings/profile`

返回用户档案。

### `PUT /api/settings/profile`

请求:

```json
{
  "height_cm": 175,
  "weight_kg": 70,
  "birth_year": 1990,
  "sex": "male"
}
```

规则:
- 不接收目标字段。

### `GET /api/settings/credentials`

返回凭证状态:

```json
{
  "xunji_key": { "configured": true, "masked": "xjllm_****" },
  "llm_key": { "configured": true, "masked": "sk-****" }
}
```

### `PUT /api/settings/credentials/{type}`

`type`: `xunji_key` / `llm_key`

请求:

```json
{ "value": "..." }
```

规则:
- 服务端加密保存。
- 不回显完整值。
- 日志不记录请求体。

---

## 5. Xunji

### `GET /api/xunji/status`

返回:

```json
{
  "key_configured": true,
  "last_successful_sync_at": "...",
  "last_synced_datestr": "2026-07-09",
  "initial_full_done": true,
  "summary": {
    "training_days": 10,
    "trainings": 10,
    "movements": 42,
    "sets": 180,
    "pending_muscle_mappings": 3
  }
}
```

### `POST /api/xunji/sync`

请求:

```json
{ "mode": "incremental" }
```

或:

```json
{ "mode": "full" }
```

规则:
- 用户主动触发。
- 不触发 AI 分析。
- 没有 Key 返回错误。

### `POST /api/xunji/resync-day`

请求:

```json
{ "datestr": "2026-07-01" }
```

规则:
- 覆盖本地该日训记镜像。
- 不写回训记。

---

## 6. Garmin

### `GET /api/garmin/activities`

查询参数:

```text
family=running&limit=50&offset=0
```

返回活动卡片列表。

### `POST /api/garmin/import`

multipart form:

```text
file=<Garmin zip>
```

响应:

```json
{
  "status": "imported",
  "activity_id": "...",
  "duplicate": false,
  "field_coverage": {
    "heart_rate": true,
    "cadence": true,
    "power": true,
    "temperature": false
  }
}
```

规则:
- 只接受 `.zip`。
- 限制大小。
- 防 zip slip。
- 可导入非跑步 FIT,但只做通用活动存档。
- 上传成功不自动分析。

### `GET /api/garmin/activities/{id}`

返回通用活动详情 + running metrics(如适用) + laps。

---

## 7. Goals

### `GET /api/goals/current`

返回当前目标配置版本。

### `POST /api/goals`

创建新目标版本并设为 current。

请求:

```json
{
  "primary_goal": "running_race_priority",
  "running_goal_text": "备战全马...",
  "strength_baseline_text": "保持体型和肌肉量...",
  "conflict_policy_text": "关键跑课优先...",
  "uncertainties_text": "比赛日期待确认",
  "effective_from": "2026-07-09",
  "details": {}
}
```

规则:
- 不覆盖旧版本。
- 保存时关闭旧 current。

### `GET /api/goals/history`

目标版本列表。

### `GET /api/goals/{id}`

目标版本详情。

---

## 8. Rest Notes

### `GET /api/rest-notes`

查询参数:

```text
start=2026-06-01&end=2026-07-09
```

### `POST /api/rest-notes`

请求:

```json
{
  "start_date": "2026-06-24",
  "end_date": "2026-07-10",
  "affected_scope": "legs",
  "note": "膝部手术后暂停跑步和腿训,上肢正常"
}
```

### `PUT /api/rest-notes/{id}` / `DELETE /api/rest-notes/{id}`

Demo 可实现编辑/删除,或 P0 只实现新增和列表。

---

## 9. Analysis

### `GET /api/analysis/preflight`

查询参数:

```text
start=2026-06-01&end=2026-07-09
```

返回生成分析前的检查:

```json
{
  "goal_config": { "id": "...", "summary": "..." },
  "data_coverage": {},
  "rest_notes": [],
  "warnings": ["气温字段缺失"],
  "can_analyze": true
}
```

### `POST /api/analysis/reports`

请求:

```json
{
  "covered_start_date": "2026-06-01",
  "covered_end_date": "2026-07-09",
  "trigger_type": "new_analysis"
}
```

规则:
- 必须有 LLM Key。
- 必须有当前目标配置。
- 用户主动触发。
- 成功保存 report 并返回 report_id。
- 失败不保存半成品报告。

### `POST /api/analysis/reports/{id}/reanalyze`

基于同一区间或用户指定区间重新分析,生成新报告。

---

## 10. Reports

### `GET /api/reports`

报告列表。

### `GET /api/reports/{id}`

报告详情。

### `DELETE /api/reports/{id}`

Demo 可不做删除。若实现,只做软删除。

---

## 11. Data Coverage

### `GET /api/data/coverage`

返回首页/数据覆盖页摘要:

```json
{
  "xunji": {
    "date_range": ["2026-06-01", "2026-07-09"],
    "training_days": 10,
    "pending_muscle_mappings": 3
  },
  "garmin": {
    "date_range": ["2026-05-01", "2026-07-07"],
    "activities": 48,
    "running_activities": 48,
    "missing_temperature_count": 48
  },
  "goals": { "current_configured": true },
  "reports": { "latest_report_id": "..." }
}
```

---

## 12. 对外只读 API v1 (已实现, ADR 0004)

对外 Open API,供外部 AI agent 只读消费本平台数据。**从 ADR 0004 的"架构预留"转为实际端点**(2026-07-16 上线 v0.4.0)。代码位于 [app/api_v1.py](../../app/api_v1.py) + [app/api_keys.py](../../app/api_keys.py)。

**开关**:受环境变量 `OUTBOUND_API_ENABLED` 门控(默认 `false`)。未启用时所有 `/api/v1/*` 返回 `404 {"code":404,"message":"对外 API 未启用"}`。生产 `.env` 已设 `OUTBOUND_API_ENABLED=true`。
> ⚠️ 部署注意:systemd unit **无 `EnvironmentFile=`**,靠 [app/config.py](../../app/config.py) 的 `_load_dotenv()` 读项目根 `.env`。改开关后必须 `systemctl restart` 才生效;仅改 `.env` 不重启无效。

**鉴权**:`Authorization: Bearer srda_...`。Key 绑定 `user_id`,agent 只能读该用户自己的数据(复用 ADR 0003 多用户隔离,不引入新信任模型)。
- 缺 Bearer / Key 无效或已吊销 → `401`。
- Key 每用户可签发多个,`srda_` 前缀,明文仅签发时一次性显示,库内存哈希,可单独吊销。
- 签发/吊销:owner 在 `GET /settings/api-keys` 页面操作(`POST /api/settings/api-keys`、`POST /api/settings/api-keys/{key_id}/revoke`)。

**边界**:只读存量,不写入、不触发 LLM 分析(见 ADR 0004)。所有端点复用 Web 已有生产者(`build_context`/`day_detail`/查库),不新造业务逻辑。POST 到只读端点 → `405`。

```text
GET /api/v1/meta                元数据: 数据覆盖 / 同步状态
GET /api/v1/context             加工上下文 (喂 LLM 那份); 默认最近 90 天, ?start=&end= 覆盖
GET /api/v1/days/{datestr}      某日训练明细 (YYYY-MM-DD)
GET /api/v1/goals/current       当前生效目标配置
GET /api/v1/goals/history       目标历史版本 (含当前)
GET /api/v1/reports             历史分析报告列表 (摘要, 不含全文)
GET /api/v1/reports/{report_id} 某份报告快照全文 (结构化层 + 叙述层)
GET /api/v1/muscle-map          动作→肌群映射表
GET /api/v1/rest-notes          休整标注
```

统一响应包络:成功 `{"ok":true,"data":...}`;错误 `{"ok":false,"error":{"code":...,"message":...}}`。

> 安全提醒:当前是方案 A(明文 HTTP,见 ADR 0001),Bearer Key 在信道上可被嗅探。对外开放/分享 Key 需等方案 C(HTTPS)。

---

## 13. Admin 预留

Demo P0 不实现 UI,但未来端点可能包括:

```text
GET/POST /api/admin/invite-codes
GET      /api/admin/users
PUT      /api/admin/users/{id}/status
GET/PUT  /api/admin/muscle-mappings
```

P0 不做。

---

## 14. 安全要求

- 所有 `/api/*` 除 login 外都需要 session(`/api/v1/*` 例外:走 Bearer Key,不用 session)。
- 所有查询都按当前 `user_id` 过滤(session 或 API Key 解析而来)。
- owner/admin 端点额外检查 role。
- 上传接口限大小、限类型。
- 错误不带内部堆栈。
- 日志不记录凭证明文。
- 外部 API(§12)只读存量,不复用写状态接口,不触发 LLM 分析。

---

## 15. 下一步

接口设计之后(已完成的垂直切片见 HANDOVER):

1. ~~选择技术栈~~ ✅ Python 3.14 + FastAPI + Jinja2。
2. ~~搭建项目脚手架~~ ✅。
3. ~~建立 SQLite migration~~ ✅。
4. ~~实现 owner 登录 + 设置 + Garmin 导入的第一条垂直切片~~ ✅。
5. 剩余路线见 [HANDOVER](../HANDOVER.md) §8:HTTPS(方案 C)、营养素建议、邀请码注册 UI 等。
