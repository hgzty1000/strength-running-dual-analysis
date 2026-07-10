# 技术架构设计

- 日期: 2026-07-09
- 状态: 草案
- 上游文档:
  - [Demo 范围与信息架构设计](./demo-scope-and-information-architecture.md)
  - [低保真 UE 设计](./low-fidelity-ue.md)
  - [CONTEXT.md](../../CONTEXT.md)
  - [ADR 0001: 分阶段部署](../adr/0001-phased-deployment-a-to-c.md)
  - [ADR 0002: 训记只读镜像 + 一条数据一个主人](../adr/0002-xunji-readonly-mirror-single-ownership.md)
  - [ADR 0003: 多用户从第一天建模](../adr/0003-multi-user-from-day-one.md)
  - [ADR 0004: 对外 API 只读预留](../adr/0004-outbound-api-reserved-readonly.md)
- 用途: 把已收敛的 demo 范围、UE 与领域边界转成可实施的技术结构。本文不写具体代码,只固定模块边界、数据流、部署/安全/扩展约束。

---

## 1. 架构目标

Demo 第一版要验证的核心闭环:

```text
训记力量数据
+ Garmin 跑步活动
+ 当前目标配置
+ 极简休整标注
→ 构造分析上下文
→ 调用云端 LLM
→ 生成可存档的双线训练分析报告
→ 识别冲突/过量风险并给出方向性建议
```

技术架构必须满足:

1. **轻量可部署**:适配 2GB 云服务器,不引入重型基础设施。
2. **多用户从第一天建模**:即使 demo 只有 owner,所有数据也必须按 `user_id` 隔离。
3. **环境无关**:协议、域名/IP、CORS、Cookie 策略、LLM 地址/模型等走配置。
4. **凭证安全**:训记 Key、LLM Key、未来平台 API Key 均加密存储,不写日志、不完整回显。
5. **分析层只读数据**:AI 分析、浅追问、目标澄清都不能偷偷改运动数据。
6. **报告可复盘**:报告保存为快照,旧报告不因数据/目标变化被无痕修改。
7. **FIT 扩展性**:Garmin/FIT 入口按通用活动设计,不写死为跑步专用。
8. **对外 API 预留**:demo 不实现实际端点,但上下文生产层和按 `user_id` 数据访问层必须摆正。

---

## 2. 总体形态

### 2.1 推荐 demo 架构

```text
Browser
  ↓ HTTP
Web 单体应用
  ├─ Web UI / SSR 或 SPA 静态资源
  ├─ Auth / Session
  ├─ Xunji Sync
  ├─ Garmin Import
  ├─ Activity Normalization
  ├─ Goal Config
  ├─ Rest Notes
  ├─ Muscle Mapping
  ├─ Analysis Context Builder
  ├─ LLM Analysis
  ├─ Reports
  └─ Settings/Admin
  ↓
SQLite
  ↓
Local File Storage(Garmin zip/fit)

External:
  ├─ Xunji Open API
  └─ Cloud LLM Provider
```

### 2.2 技术取向

- **轻量 Web 单体**:demo 阶段不拆微服务。
- **SQLite**:本地文件数据库,适合单机 demo 与小用户量。
- **本地文件存储**:保存 Garmin 上传原始 zip / 解析后的 fit 引用。
- **云端 LLM API**:本平台不跑本地模型。
- **无 Docker 约束**:沿用 ADR 0001,2GB 服务器 demo 阶段避免引入容器额外负担。

具体框架可在实现计划中再定。本文先固定架构边界,不绑定某个前后端框架。

---

## 3. 运行环境与部署

### 3.1 本地开发

本地先跑通核心闭环:

```text
本地浏览器
→ 本地 Web 单体
→ 本地 SQLite
→ 本地 Garmin 样本上传
→ 真实训记 API
→ 真实云端 LLM API
```

目的:
- 快速调试。
- 用真实训记数据和 Garmin FIT 样本验证分析价值。
- 避免一开始被服务器安全/部署拖慢。

注意:本地调试只是执行顺序,不是架构偷懒的理由。代码从第一天就必须按可云端部署设计,不得写死本地路径、localhost、开发端口或本地专属配置。

### 3.2 本地类生产配置验证

云端部署前,必须先在本地用接近生产的配置跑一遍:

- 使用环境变量配置数据库路径、上传目录、端口、Cookie 策略、LLM 地址等。
- 使用 production build / production start 方式运行。
- 使用非默认数据目录。
- 关闭公开注册。
- 通过初始化脚本或环境变量创建预置 owner。
- 检查上传大小限制。
- 检查日志不打印训记 Key、LLM Key、Authorization header。
- 检查 SQLite 与上传目录可整体迁移/备份。

这一阶段用于提前暴露云端部署问题,避免本地跑通但上云踩坑。

### 3.3 云服务器 demo:方案 A

按 ADR 0001:

```text
http://<公网IP>:<端口>
```

约束:
- 不绑域名。
- 不备案。
- 不做正规 HTTPS。
- 仅 owner / 可信设备使用。
- 不开放朋友接入。
- 不开放公开注册。
- 朋友输入训记 Key 必须等方案 C(正规 HTTPS)。
- 真实数据上云前必须完成 ADR 0001 的服务器安全加固遗留项。

### 3.4 云端部署架构约束

即使 demo 调试优先本地,架构也必须一开始支持云端部署:

- HOST/PORT 可配置;本地可监听 `127.0.0.1`,云端可监听 `0.0.0.0`。
- APP_BASE_URL 可配置,不能写死 localhost。
- DATABASE_PATH 可配置,SQLite 文件集中放置。
- UPLOAD_DIR 可配置,上传文件不散落在源码目录。
- ENCRYPTION_MASTER_KEY 必须来自环境变量。
- ALLOW_PUBLIC_SIGNUP 默认 false。
- Cookie Secure/SameSite 策略按 HTTP/HTTPS 环境切换。
- 上传文件大小限制可配置。
- owner 初始化方式明确,不能依赖手工改数据库。
- 日志目录可配置,且日志不得泄露凭证。
- 数据文件(app.db + uploads)路径集中,便于手动备份与迁移。

### 3.5 配置项

所有环境相关项必须走配置/环境变量:

- App base URL。
- HTTP/HTTPS 开关。
- Cookie `Secure` / `SameSite` 策略。
- CORS / allowed origin。
- SQLite 文件路径。
- Garmin 上传目录。
- 训记 API base URL。
- LLM provider base URL。
- LLM model。
- 凭证加密主密钥。
- 是否允许公开注册。
- 文件上传大小限制。

---

## 4. 模块边界

### 4.1 Auth / Users

职责:
- 登录 / 退出。
- Session 管理。
- 预置 owner 用户。
- 用户角色: `owner` / `user`。
- 所有业务查询提供当前 `user_id`。

Demo P0:
- 预置 owner。
- 关闭公开注册。
- 不做邀请码 UI。

预留:
- 邀请码注册。
- 用户管理后台。
- 用户禁用/启用。

边界:
- 不做第三方登录。
- 不做手机验证码。
- 不做 2FA。

---

### 4.2 Credentials

职责:
- 存储每用户训记 Key。
- 存储每用户 LLM Key。
- 加密/解密。
- 掩码展示。
- 防止日志泄露。

Key 类型:

```text
xunji_key: 用户自己训记账号通行证
llm_key: 平台所有者给该用户配置的模型商 Key
platform_api_key: 未来只读 API 预留,demo 不实现
```

安全规则:
- 数据库存密文。
- 主密钥来自环境变量。
- API 返回只给掩码。
- 日志不记录明文。
- 错误信息不带明文 Key。
- 没有 LLM Key 的用户不能触发 AI 分析。

---

### 4.3 Xunji Sync

职责:
- 调训记 Open API。
- 首次全量同步。
- 后续增量同步。
- 指定日重拉。
- 同步动作目录。
- 本地缓存训记镜像。

同步策略:
- 手动触发。
- 增量范围:上次同步日当天 → 今天。
- 最近一天永远重拉。
- 历史改动靠指定日重拉。
- 不做后台定时同步。
- 不做滚动重扫。
- 不写回训记。

数据边界:
- 训记镜像只读。
- 主人是训记。
- 要改训练记录回训记 App 改。

---

### 4.4 Muscle Mapping

职责:
- 为力量动作提供肌群归类。
- 训记 catalog 有 `type` 的直接用。
- 缺失 `type` 的动作进入待补列表。
- AI 补一次,固化复用。
- 后续可支持用户修正。

来源标记:

```text
xunji_type
ai_inferred
user_corrected(预留)
external_reference(预留,如 exercises-dataset)
```

边界:
- demo 可后台自动补全,不做管理 UI。
- 同一动作分类必须稳定,不能每次分析现场重判。
- 动作肌群映射变化后,旧报告不自动改;如需更新判断,重新分析生成新报告。

---

### 4.5 Garmin Import

职责:
- 接收 Garmin zip 上传。
- 校验文件。
- 解压并定位 `.fit`。
- 解析 FIT。
- 查重。
- 保存通用 Garmin 活动。
- 标准化 activity family / variant。
- 对 running family 提取跑步指标。

处理流程:

```text
Upload zip
→ validate zip
→ extract/read fit safely
→ parse generic FIT messages
→ derive Garmin activity unique key
→ deduplicate by user_id + unique key
→ save raw file ref + generic activity
→ normalize sport/sub_sport
→ if running family: save running metrics
→ mark field coverage
```

安全规则:
- 限制文件大小。
- 只接受 `.zip`。
- 限制 zip 内文件数量。
- 防 zip slip。
- 不解压到可执行目录。
- 解析失败不暴露内部堆栈给用户。
- 原始文件按 `user_id` 隔离。

查重候选:
- FIT 内活动开始时间。
- Garmin activity id 如有。
- sport/sub_sport。
- 距离/时长辅助。
- 文件 hash 兜底。

---

### 4.6 Activity Normalization

职责:
- 将不同来源活动归一为平台可分析对象。
- 不把 Garmin/FIT 写死为跑步。
- 标准化 `activity_family` / `activity_variant`。

示例:

```text
running / road_run
running / treadmill_run
running / track_run
running / trail_run
cycling / unknown
swimming / unknown
hiking / unknown
unknown / unknown
```

Demo P0:
- 完整支持 running family 的基础指标解释。
- 非跑步 FIT 只做通用解析、存档、类型标记与元数据展示。
- 非跑步 FIT 暂不进入双线负荷矩阵或专项建议。

---

### 4.7 Goal Config

职责:
- 管理当前目标配置。
- 版本化保存。
- 提供分析时的判断尺。

字段方向:
- 主目标。
- 跑步目标说明。
- 力量底线说明。
- 冲突取舍说明。
- 生效日期。
- 备注/不确定项。
- 生成方式:手动 / AI 目标澄清(未来)。

规则:
- 修改目标保存新版本,不无痕覆盖。
- 报告引用生成时的目标版本。
- AI 目标澄清 demo 不实现,但模块位置预留。

---



### 4.8 Rest Notes

职责:
- 解释某段训练空缺/异常。
- 作为分析上下文。

最小字段:
- 日期范围。
- 影响范围:跑步 / 力量 / 腿部 / 全部 / 自定义。
- 备注。

边界:
- 休整标注解释过去/某段事实。
- 当前/未来判断尺归目标配置。
- 同一事件若两者都影响,拆成休整标注 + 目标配置分别保存。

---

### 4.9 User Profile

职责:
- 保存静态个人信息。
- 为后续营养素建议提供公式输入。

字段:
- 身高。
- 体重。
- 年龄。
- 性别。

边界:
- 不存目标。
- 不做体重趋势。
- 不收集体脂、睡眠、静息心率等第三层不可靠数据。

---

### 4.10 Analysis Context Builder

职责:
- 将数据层转换成 LLM 可消费的结构化上下文。
- AI 分析与未来只读 API 共用这一层。
- 控制数据外流最小化。

输入:
- 训记镜像。
- 肌群映射。
- Garmin 活动与 running metrics。
- 当前目标配置版本。
- 休整标注。
- 用户档案。
- 历史报告摘要(必要时)。

输出:
- 覆盖区间。
- 数据覆盖情况。
- 力量摘要。
- 跑步摘要。
- 双线负荷输入。
- 目标配置摘要。
- 休整标注摘要。
- 不确定项/缺失字段。

边界:
- 不调用 LLM。
- 不修改数据。
- 不绑定网页 session,只接受 `user_id` 与范围参数。
- 未来对外 API 读取的主要也是这一份上下文。

---

### 4.11 LLM Analysis

职责:
- 调云端大模型生成分析报告。
- 将结构化上下文转为结构化报告 + 叙述层。

触发规则:
- 用户主动点击分析。
- 或用户确认的重新分析。
- 不做后台自动分析。
- 同步/上传不自动分析。
- 对外 API 不触发分析。

可能调用 LLM 的能力:
- AI 分析报告。
- 动作肌群补全。
- 未来 AI 目标澄清。
- 未来浅追问。

不会调用 LLM 的能力:
- 训记同步。
- Garmin 上传/解析。
- 数据覆盖查看。
- 对外只读 API。

---

### 4.12 Reports

职责:
- 保存报告快照。
- 展示当前/历史报告。
- 支持重新分析生成新报告。

报告结构:
- 元信息。
- 目标配置版本。
- 覆盖区间。
- 数据覆盖。
- 分析前提。
- 核心结论。
- 双线负荷摘要。
- 冲突/过量风险。
- 力量判断。
- 跑步判断。
- 方向性建议。
- 观察指标。
- 不确定项/置信度。
- 叙述层正文。

规则:
- 旧报告不自动改。
- 底层数据/目标/休整标注/动作映射变化后,如需更新,重新分析生成新报告。
- 报告引用当时的目标配置版本与上下文摘要。

---

### 4.13 External Read-only API Reserved

Demo 不实现实际端点,但架构必须预留:

- Analysis Context Builder 独立成层。
- 数据访问按 `user_id` 解耦,不绑死网页 session。
- 未来 API 只读返回平台已拥有/已算好的存量:
  - 原始训练数据。
  - 加工上下文。
  - 当前/历史目标配置。
  - 历史报告。
  - 休整标注。
  - 动作肌群映射。
  - 元数据。

明确排除:
- 外部 agent 写回。
- 外部 API 触发分析。
- 外部 API 消耗平台 LLM Key。

---

## 5. 核心数据流

### 5.1 训记同步流

```text
User clicks sync
→ load encrypted Xunji Key
→ call Xunji API by date range
→ save/update Xunji mirror
→ sync action catalog
→ enqueue/mark missing muscle mappings
→ update sync metadata
```

不触发 AI 分析。

---

### 5.2 动作肌群补全流

```text
Missing Xunji action type detected
→ build action classification prompt
→ call LLM with user LLM Key
→ save action → muscle mapping
→ reuse mapping in future analyses
```

注意:
- 该流程会消耗 LLM。
- 应由用户操作触发或在分析前明确提示/确认触发。
- demo 可做成“分析前补全缺失肌群”。

---

### 5.3 Garmin 上传流

```text
User uploads Garmin zip
→ validate upload
→ read/extract FIT safely
→ parse generic FIT
→ identify sport/sub_sport
→ deduplicate
→ save Garmin activity
→ if running: save running metrics
→ show import result and field coverage
```

不触发 AI 分析。

---

### 5.4 目标配置流

```text
User edits goal config
→ validate minimum fields
→ save new goal_config_version
→ mark as current
→ future reports use new version
```

旧目标版本保留。

---

### 5.5 AI 分析流

```text
User opens Analysis page
→ choose range(default 6 weeks)
→ show data coverage + goal version + rest notes
→ user clicks Generate
→ build analysis context by user_id + range
→ call LLM with current user's LLM Key
→ validate/parse report structure
→ save report snapshot
→ navigate to report detail
```

---

### 5.6 重新分析流

```text
User updates data / goal / rest note
→ user clicks re-analyze
→ build fresh context
→ call LLM
→ save new report
→ old report remains unchanged
```

---

## 6. 数据存储分区

> 本节是逻辑分区,不是最终表结构。具体字段在数据模型设计文档中细化。

### 6.1 用户与身份

- users
- sessions
- roles
- invite_codes(预留)

### 6.2 凭证

- user_credentials
  - xunji_key encrypted
  - llm_key encrypted
  - platform_api_key encrypted(预留)

### 6.3 训记镜像

- xunji_sync_state
- xunji_training_days
- xunji_trainings
- xunji_movements
- xunji_sets
- xunji_action_catalog

### 6.4 肌群映射

- exercise_muscle_mappings
- muscle_mapping_sources

### 6.5 Garmin 活动

- garmin_import_files
- garmin_activities
- garmin_activity_laps
- running_activity_metrics
- garmin_field_coverage

未来扩展:
- cycling_activity_metrics
- swimming_activity_metrics

### 6.6 目标与上下文

- goal_config_versions
- rest_notes
- user_profiles

### 6.7 分析

- analysis_reports
- analysis_report_sections
- analysis_context_snapshots / context_summary

---

## 7. 文件存储

### 7.1 存储对象

Garmin 上传建议保留:
- 原始 zip。
- 解析出的 FIT 或 FIT 内容引用。
- 文件 hash。
- 解析状态。

### 7.2 路径原则

```text
storage/
  users/
    <user_id>/
      garmin/
        imports/
          <import_id>/
            original.zip
            activity.fit
```

### 7.3 安全原则

- 路径不能使用用户上传文件名直接拼接。
- zip 解压必须防路径穿越。
- 上传目录不作为静态可执行目录。
- 下载/查看原始文件如未来开放,必须做鉴权。

---

## 8. LLM 调用与费用边界

### 8.1 Key 使用

- 平台级配置:LLM provider base URL、model。
- 用户级配置:LLM Key。
- 调用时使用当前用户的 LLM Key。

### 8.2 调用触发

必须由用户主动触发或确认:
- 生成分析。
- 重新分析。
- 动作肌群补全。
- 未来目标澄清。
- 未来浅追问。

禁止:
- 上传后自动分析。
- 同步后自动分析。
- 后台定时分析。
- 外部 API 触发分析。

### 8.3 数据外流控制

- 只发送必要区间。
- 发送结构化上下文,不发送数据库全量。
- 不发送 Key。
- 不发送与本次分析无关的历史明细。

---

## 9. 安全与访问控制

### 9.1 公网 demo 风险

方案 A 是公网 HTTP,必须控制入口:

- 预置 owner。
- 关闭公开注册。
- 邀请码机制预留。
- 朋友输入训记 Key 必须等 HTTPS。

### 9.2 权限规则

- 所有业务查询必须带当前 `user_id`。
- 用户只能访问自己的数据。
- owner/admin 功能单独判断角色。
- 上传文件、报告、目标版本、凭证都按 user_id 隔离。

### 9.3 服务器安全

上线真实数据 / 家人使用前必须完成 ADR 0001 遗留项:
- 更换强密码。
- 建议 SSH 密钥登录。
- 关闭密码登录。
- 不使用已泄露旧密码。
- 最小开放端口。

---

## 10. 前端结构约束

来自低保真 UE:

- 一套页面、一套路由、一套组件。
- **不使用大型 UI 组件库**:demo UI 层尽量使用原生 HTML/CSS 与自写轻量组件实现,不引入 Ant Design、Material UI、Element、Naive UI、Bootstrap 组件库等大而全设计系统。
- 可保留必要基础工具库(如路由、日期处理、FIT 解析、加密/HTTP 等),但页面组件、布局、样式和响应式行为由项目自控。
- 基础组件自写即可:Button、Input、Select、Card、Tabs、Alert、Toast、FileInput、ActivityCard、ReportSection 等。
- 样式优先使用普通 CSS / CSS Modules / CSS variables,避免被组件库主题和响应式机制绑架。
- 桌面端左侧侧边栏。
- 手机端顶部汉堡菜单。
- 不做 `DesktopXxxPage` / `MobileXxxPage` 两套页面。
- 列表优先卡片化,减少响应式分叉。
- 报告页使用统一结构化区块卡片。

页面路由建议:

```text
/login
/
/data/xunji
/data/garmin
/data/coverage
/goals/current
/goals/history
/analysis/new
/reports
/reports/:id
/settings/profile
/settings/credentials
/settings/security
/admin/*(预留)
```

具体路由可在接口/前端设计中再细化。

---

## 11. 错误处理原则

### 11.1 同步错误

- 展示可理解错误。
- 不展示 Key。
- 不因某天失败导致已成功天数回滚,除非事务要求。

### 11.2 FIT 解析错误

- 文件不合法:提示格式错误。
- ZIP 无 FIT:提示 Garmin 原始格式不正确。
- 重复活动:提示已存在。
- 字段缺失:不算失败,进入 field coverage。

### 11.3 LLM 错误

- 分析失败不保存半成品报告。
- 展示失败原因类别:Key 缺失、调用失败、结构化返回失败等。
- 可重试。

---

## 12. 后续扩展点

### P1 / 后续能力

- AI 目标澄清。
- AI 浅追问。
- 营养素建议。
- 外部只读 API 实际端点。
- 邀请码注册 UI。
- 用户管理后台。
- 动作肌群人工修正 UI。
- 天气补全(低优先级)。
- 更多 Garmin 运动类型解释器。

### 不碰的线

- 外部 agent 写回。
- 训记写回。
- 饮食记录。
- 完整训练计划。
- 医疗/康复建议。

---

## 13. 下一步

技术架构之后,建议继续:

1. 数据模型设计: [data-model.md](./data-model.md)。
2. 接口/API 设计: [api-design.md](./api-design.md)。
3. 技术栈确认与项目脚手架。
