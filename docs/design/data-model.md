# 数据模型设计

- 日期: 2026-07-09
- 状态: 草案
- 上游文档:
  - [Demo 范围与信息架构设计](./demo-scope-and-information-architecture.md)
  - [低保真 UE 设计](./low-fidelity-ue.md)
  - [技术架构设计](./technical-architecture.md)
  - [CONTEXT.md](../../CONTEXT.md)
- 用途: 将 demo P0 的领域对象落成 SQLite 数据模型草案,为接口设计和实现提供基准。

---

## 1. 设计原则

### 1.1 SQLite,但保持轻量

SQLite 在本项目中定位为**本地结构化文件**,不是重型数据库系统。

使用 SQLite 的原因:
- 训记、Garmin、目标、报告、休整标注之间有明显关系。
- 需要按日期范围、用户、来源、目标版本查询。
- 需要唯一约束和查重。
- 后续从 SQLite 迁移到 PostgreSQL 比从散落 JSON 文件迁移更容易。

避免过度设计:
- demo 表结构保持朴素。
- 复杂报告结构、上下文快照、字段覆盖可先用 JSON 字段。
- 不为了“完美范式化”拆太多表。

### 1.2 所有用户数据带 `user_id`

即使 demo 只有 owner,所有业务表也必须从第一天按 `user_id` 隔离。

包括:
- 训记镜像。
- Garmin 活动。
- 目标配置。
- 休整标注。
- 用户档案。
- 报告。
- 肌群映射。

### 1.3 保留外部来源身份

所有外部来源数据都要保留 source identity,便于查重、重拉、复盘:

- 训记: `datestr`、训练 local id、动作/组原始 id 或原始片段。
- Garmin: FIT 活动开始时间、sport/sub_sport、activity id 如有、file hash。
- 报告: goal_config_version_id、覆盖区间、上下文摘要。

### 1.4 报告是快照

报告生成后不随底层数据自动变化。

底层数据、目标、休整标注、肌群映射变化后,如需更新判断,生成新报告。

### 1.5 JSON 字段只用于边界清楚的复杂结构

适合 JSON 的内容:
- LLM 报告结构化层。
- 报告上下文摘要。
- Garmin 字段覆盖。
- FIT 解析原始摘要。
- 目标配置详情。
- 错误详情。

不适合只放 JSON 的内容:
- 高频查询日期。
- 用户隔离字段。
- 去重键。
- 报告/目标引用关系。
- 训练基础指标。

---

## 2. 命名约定

- 表名使用复数 snake_case。
- 主键统一 `id`。
- 外键使用 `<entity>_id`。
- 时间字段使用 ISO 字符串或 SQLite datetime 文本,统一 UTC 存储。
- 用户可见日期单独保存本地日期字符串时使用 `local_date` 或 `datestr`。
- JSON 字段后缀使用 `_json`。
- 加密字段后缀使用 `_ciphertext`。
- 掩码不入库,展示时动态生成。

---

## 3. 用户与会话

### 3.1 `users`

用途:平台用户。

字段:

```text
id                  text primary key
username            text not null unique
password_hash       text not null
role                text not null            -- owner / user
status              text not null            -- active / disabled
created_at          text not null
updated_at          text not null
last_login_at       text null
```

约束:
- demo 预置一个 owner。
- 公开注册默认关闭。

### 3.2 `sessions`

用途:登录会话。

字段:

```text
id                  text primary key
user_id             text not null references users(id)
session_token_hash  text not null unique
created_at          text not null
expires_at          text not null
revoked_at          text null
user_agent          text null
ip_address          text null
```

说明:
- 存 token hash,不存明文 token。
- demo 可用简单 cookie session,但仍按 user_id 隔离。

### 3.3 `invite_codes`(预留)

Demo 不实现 UI,但可预留表或后续迁移创建。

```text
id                  text primary key
code_hash           text not null unique
created_by_user_id  text references users(id)
max_uses            integer not null default 1
used_count          integer not null default 0
expires_at          text null
disabled_at         text null
created_at          text not null
note                text null
```

---

## 4. 用户档案与凭证

### 4.1 `user_profiles`

用途:静态个人信息,服务营养素建议与分析上下文。

```text
user_id             text primary key references users(id)
height_cm           real null
weight_kg           real null
birth_year          integer null
sex                 text null              -- male / female / other / unspecified
created_at          text not null
updated_at          text not null
```

说明:
- 不存目标。
- 不追踪体重趋势。
- 年龄可通过 birth_year 或 birth_date 计算;demo 为避免隐私和精度需求,可用 birth_year。

### 4.2 `user_credentials`

用途:每用户凭证加密存储 (AES-GCM)。仅用于训记 Key 与 LLM Key;平台对外 API Key 使用独立的 `api_keys` 表 (hash-only, 见 §4.2b)。

```text
id                  text primary key
user_id             text not null references users(id)
credential_type     text not null          -- xunji_key / llm_key
ciphertext          text not null
nonce               text not null
key_version         text not null
created_at          text not null
updated_at          text not null
last_used_at        text null
revoked_at          text null
```

约束:

```text
unique(user_id, credential_type) where revoked_at is null
```

安全:
- 明文只在内存中短暂使用。
- 日志不输出明文。
- API 返回只显示是否配置和掩码。

### 4.2b `api_keys` (平台对外 API Key, hash-only)

用途:平台签发给外部 Agent 的只读 API Key。**明文仅签发时一次性显示**,之后不可再现;库内存 SHA-256 hash。

```text
id                  text primary key
user_id             text not null references users(id)
key_hash            text not null          -- SHA-256(plaintext)
prefix              text not null          -- "srda_" + 前6位随机字符, 供识别
label               text null              -- 用户自定义标签
created_at          text not null
last_used_at        text null              -- 每次鉴权命中即更新
revoked_at          text null              -- 非 NULL 即为已吊销
```

约束:
- 无 unique 跨 Key 约束 — 用户可持有多个活跃 Key,可分别吊销。
- `resolve_api_key(raw_key)` → SHA-256 → 查库 → 返回 `user_id` (仅当未吊销且 user 状态 active)。

与 `user_credentials` 的关键区别:
- **不可解密**:不存明文,不存可逆密文。Key 丢失只能吊销后重签,无法恢复。
- **多 Key**:每用户不限一个 Key,适合不同 Agent/场景分别管理。
- **绑定 user_id**:天然复用多用户隔离,A 的 Key 物理上读不到 B 的数据。

---

## 5. 训记镜像

### 5.1 `xunji_sync_state`

用途:记录每用户训记同步状态。

```text
user_id                 text primary key references users(id)
last_successful_sync_at text null
last_synced_datestr     text null
initial_full_done       integer not null default 0
last_error_json         text null
updated_at              text not null
```

### 5.2 `xunji_training_days`

用途:按日期保存训记日级同步结果。

```text
id                  text primary key
user_id             text not null references users(id)
datestr             text not null          -- YYYY-MM-DD from Xunji
source_hash         text null              -- local computed hash for change detection if useful
raw_json            text not null
synced_at           text not null
```

约束:

```text
unique(user_id, datestr)
```

说明:
- 指定日重拉覆盖该日 raw_json 和解析表。
- raw_json 保留便于后续字段补解析。

### 5.3 `xunji_trainings`

用途:训记训练对象。

```text
id                  text primary key
user_id             text not null references users(id)
training_day_id     text not null references xunji_training_days(id)
xunji_local_id      text null
datestr             text not null
title               text null
note                text null
start_at_raw        text null              -- 保留但分析不用作时长依据
end_at_raw          text null
calories            real null
raw_json            text not null
```

约束候选:

```text
unique(user_id, datestr, xunji_local_id)
```

如果 local_id 不稳定或缺失,实现时需用 datestr + raw index 兜底。

### 5.4 `xunji_movements`

用途:训记训练中的动作。

```text
id                  text primary key
user_id             text not null references users(id)
training_id         text not null references xunji_trainings(id)
movement_index      integer not null
action_name         text not null
xunji_action_id     text null
xunji_type          text null
raw_json            text not null
```

约束:

```text
unique(training_id, movement_index)
```

### 5.5 `xunji_sets`

用途:训记动作下的组。

```text
id                  text primary key
user_id             text not null references users(id)
movement_id         text not null references xunji_movements(id)
set_index           integer not null
weight              real null
weight_unit         text null
reps                integer null
rpe                 real null
rest_seconds        integer null
done                integer null
raw_json            text not null
```

约束:

```text
unique(movement_id, set_index)
```

说明:
- 容量计算使用 weight × reps,不使用训记起止时间判断训练时长。
- done=false 的组是否纳入分析,由上下文构建规则决定。

### 5.6 `xunji_action_catalog`

用途:训记动作目录缓存。

```text
id                  text primary key
user_id             text not null references users(id)
xunji_action_id     text null
action_name         text not null
xunji_type          text null
raw_json            text not null
synced_at           text not null
```

约束:

```text
unique(user_id, action_name)
```

---

## 6. 动作肌群映射

### 6.1 `exercise_muscle_mappings`

用途:平台固化的动作→肌群映射。

```text
id                  text primary key
user_id             text not null references users(id)
source_system       text not null          -- xunji / manual_future / external_reference
source_action_name  text not null
primary_group       text not null
secondary_groups_json text null            -- string[]
source_type         text not null          -- xunji_type / ai_inferred / user_corrected / external_reference
confidence          real null
rationale           text null
created_at          text not null
updated_at          text not null
```

约束:

```text
unique(user_id, source_system, source_action_name)
```

说明:
- demo 按用户隔离,避免用户修正互相污染。
- 后续可增加全局参考映射表,但 P0 不需要。

---

## 7. Garmin 活动

### 7.1 `garmin_import_files`

用途:记录上传文件与解析状态。

```text
id                  text primary key
user_id             text not null references users(id)
original_filename   text not null
stored_zip_path     text not null
stored_fit_path     text null
file_hash           text not null
file_size_bytes     integer not null
status              text not null          -- imported / duplicate / failed
error_json          text null
created_at          text not null
```

索引:

```text
index(user_id, file_hash)
```

### 7.2 `garmin_activities`

用途:通用 Garmin 活动事实,不写死跑步。

```text
id                  text primary key
user_id             text not null references users(id)
import_file_id      text not null references garmin_import_files(id)
activity_unique_key text not null
fit_start_time      text not null
local_date          text null
sport               text null
sub_sport           text null
activity_family     text not null          -- running / cycling / swimming / hiking / unknown
activity_variant    text not null          -- road_run / treadmill_run / track_run / trail_run / unknown
elapsed_seconds     real null
timer_seconds       real null
distance_m          real null
calories            real null
gps_available       integer not null default 0
lap_count           integer null
field_coverage_json text not null
raw_summary_json    text not null
created_at          text not null
```

约束:

```text
unique(user_id, activity_unique_key)
```

activity_unique_key 生成候选:

```text
Garmin activity id if present
else fit_start_time + sport/sub_sport + distance/timer hash
else file_hash fallback
```

### 7.3 `garmin_laps`

用途:活动 lap / 分段摘要。

```text
id                  text primary key
user_id             text not null references users(id)
activity_id         text not null references garmin_activities(id)
lap_index           integer not null
start_time          text null
elapsed_seconds     real null
timer_seconds       real null
distance_m          real null
avg_speed_mps       real null
avg_hr              real null
max_hr              real null
avg_cadence         real null
avg_power           real null
raw_json            text not null
```

约束:

```text
unique(activity_id, lap_index)
```

### 7.4 `running_activity_metrics`

用途:running family 专属指标。

```text
activity_id         text primary key references garmin_activities(id)
user_id             text not null references users(id)
run_context         text not null          -- road / treadmill / track / trail / unknown
run_type            text not null          -- easy / long / tempo / interval / recovery / race_test / mixed_unknown
avg_pace_sec_per_km real null
avg_speed_mps       real null
max_speed_mps       real null
avg_hr              real null
max_hr              real null
avg_cadence         real null
max_cadence         real null
avg_power           real null
max_power           real null
elevation_gain_m    real null
elevation_loss_m    real null
temperature_c       real null
temperature_source  text not null default 'missing' -- fit_native / weather_api_future / missing
metrics_json        text null
created_at          text not null
updated_at          text not null
```

说明:
- 气温缺失不影响导入。
- 天气补全后续低优先级,不进 demo。
- 跑步机海拔通常不参与判断。

---

## 8. 目标与休整

### 8.1 `goal_config_versions`

用途:当前目标配置版本。

```text
id                  text primary key
user_id             text not null references users(id)
version_number      integer not null
is_current          integer not null default 0
primary_goal        text not null
running_goal_text   text null
strength_baseline_text text null
conflict_policy_text text null
uncertainties_text  text null
effective_from      text not null
effective_to        text null
created_by          text not null          -- manual / ai_clarification_future
created_at          text not null
confirmed_at        text not null
details_json        text null
```

约束:

```text
unique(user_id, version_number)
```

实现注意:
- 同一用户只能有一个 current 版本。SQLite 可用应用逻辑或 partial unique index 保证。
- 保存新版本时旧 current 置为 false,必要时填 effective_to。

### 8.2 `rest_notes`

用途:解释某段训练缺失/异常。

```text
id                  text primary key
user_id             text not null references users(id)
start_date          text not null
end_date            text not null
affected_scope      text not null          -- running / strength / legs / all / custom
note                text not null
tags_json           text null              -- P1
created_at          text not null
updated_at          text not null
```

索引:

```text
index(user_id, start_date, end_date)
```

边界:
- 解释过去事实。
- 当前/未来判断尺归 goal_config_versions。

---

## 9. 分析报告

### 9.1 `analysis_reports`

用途:保存 AI 分析报告快照。

```text
id                  text primary key
user_id             text not null references users(id)
goal_config_version_id text not null references goal_config_versions(id)
covered_start_date  text not null
covered_end_date    text not null
status              text not null          -- completed / failed
trigger_type        text not null          -- new_analysis / reanalysis
reanalysis_of_report_id text null references analysis_reports(id)
model_provider      text null
model_name          text null
analysis_context_json text not null        -- 上下文摘要,不是数据库全量
structured_json     text not null          -- 固定区块结构化层
narrative_md        text not null          -- 叙述层
confidence_json     text null
data_coverage_json  text null
uncertainties_json  text null
error_json          text null
created_at          text not null
```

索引:

```text
index(user_id, created_at)
index(user_id, covered_start_date, covered_end_date)
index(user_id, goal_config_version_id)
```

说明:
- P0 先用 `structured_json` 承载报告结构,避免过早拆 `analysis_report_sections`。
- 旧报告不自动改。

---

## 10. 导入/同步日志

### 10.1 `operation_logs`

用途:轻量记录关键操作,便于 demo debug。不要存敏感明文。

```text
id                  text primary key
user_id             text null references users(id)
operation_type      text not null          -- xunji_sync / garmin_import / llm_analysis / muscle_mapping
status              text not null          -- success / failed
summary             text null
error_json          text null
created_at          text not null
```

注意:
- 不写训记 Key。
- 不写 LLM Key。
- 不写 Authorization header。

---

## 11. 关键唯一约束与索引汇总

### 11.1 唯一约束

```text
users.username
sessions.session_token_hash
user_credentials(user_id, credential_type) active only
xunji_training_days(user_id, datestr)
xunji_trainings(user_id, datestr, xunji_local_id) when local_id exists
xunji_movements(training_id, movement_index)
xunji_sets(movement_id, set_index)
xunji_action_catalog(user_id, action_name)
exercise_muscle_mappings(user_id, source_system, source_action_name)
garmin_activities(user_id, activity_unique_key)
garmin_laps(activity_id, lap_index)
goal_config_versions(user_id, version_number)
```

### 11.2 常用索引

```text
all business tables: user_id
xunji_trainings(user_id, datestr)
xunji_movements(user_id, action_name)
garmin_activities(user_id, fit_start_time)
garmin_activities(user_id, activity_family, activity_variant)
rest_notes(user_id, start_date, end_date)
analysis_reports(user_id, created_at)
analysis_reports(user_id, covered_start_date, covered_end_date)
```

---

## 12. P0 可简化点

为了避免过度设计,P0 可以简化:

1. 报告区块不拆表,先存 `structured_json`。
2. Garmin record 逐秒点不入库,只存 summary + laps + running metrics。若后续需要更细分析,可从原始 FIT 重新解析。
3. FIT 原始完整解析结果不全量入库,保留 raw_summary_json 与原始文件。
4. 动作肌群映射按用户隔离,不做全局共享映射。
5. invite_codes 可后置迁移创建,不影响 P0。
6. operation_logs 保持轻量,不做完整审计系统。

---

## 13. 待实现时确认的细节

这些不是大方向分歧,实现时可按实际框架敲定:

- ID 生成方式:UUID / nanoid / cuid。
- 时间存储格式:统一 UTC ISO 文本。
- 密码 hash 算法:Argon2 / bcrypt / scrypt。
- 凭证加密算法:AES-GCM 或 XChaCha20-Poly1305。
- SQLite migration 工具。
- 是否启用 SQLite WAL。
- JSON 字段是否做 schema validation。

---

## 14. 下一步

数据模型之后,继续:

1. 接口/API 设计。
2. 技术栈确认与项目脚手架。
3. 数据库 migration。
4. P0 垂直切片实现。
