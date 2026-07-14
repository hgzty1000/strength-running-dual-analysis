# 交接文档 (Demo 实现现状)

- 日期: 2026-07-14
- 版本: v0.3.0
- 状态: demo 可运行, v0.3.0 功能闭环全通, 回归测试 135/135 通过, **已部署阿里云**
- 项目名: **力跑双训分析系统** (原名"双修运动平台")
- 面向: 接手继续开发/维护的人

本文件描述**已实现的 demo 到底是什么样、怎么跑、边界在哪**。需求与设计背景见 [CONTEXT.md](../CONTEXT.md) 和 [docs/design/](design/)。

---

## 1. 一句话

力跑双训分析系统 — 力量(训记) + 跑步(Garmin) 双线训练分析 demo。整合两条线数据 + 当前目标配置 + 休整标注, 生成可存档的双线分析报告, 识别冲突/过量并给方向性建议。v0.2 新增: AI 目标澄清对话 / 报告浅追问 / 温度—心率热解读 / 首页数据看板 + 粒度切换 + 分布饼图。首页是训记式月历看板, 可点进每天详情。v0.3 新增: 单日训练分享卡片 (7 种视觉方案, 3:4 竖版, 隐私过滤, 手动截图)。

---

## 2. 技术栈

- Python 3.14 + FastAPI + Jinja2 (轻量 Web 单体)
- SQLite (本地文件 `var/app.db`, WAL)
- 原生 HTML/CSS, 无大型 UI 组件库, 一套页面响应式 (桌面侧栏 / 手机汉堡菜单)
- 云端 LLM 可选 (OpenAI 兼容, 当前接 DeepSeek); 无 LLM 时规则引擎兜底
- 依赖见 [requirements.txt](../requirements.txt)（含 httpx）

## 3. 运行

```bash
pip install -r requirements.txt
cp .env.example .env    # 按需改, 首次启动自动建库 + 预置 owner
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 , 用 `.env` 的 `OWNER_USERNAME`/`OWNER_PASSWORD` 登录。

配置见 [.env.example](../.env.example)。LLM 相关: `LLM_BASE_URL` + `LLM_MODEL` 平台级配置 (当前 DeepSeek: `https://api.deepseek.com/v1` + `deepseek-v4-pro`), 用户在「设置→凭证」只填 Key。

## 4. 测试

```bash
python tests/test_end_to_end.py      # 135 项端到端回归, 用临时库, 不污染 var/
```

覆盖: 登录/鉴权、真实 Garmin 导入(多文件+去重)、跑步类型分类、训记 seed、肌群映射(含手动订正+AI补全endpoint)、目标(含历史查看/复用/中文标签)、AI 目标澄清(无 LLM 优雅降级 + `created_by` 标记/白名单)、凭证可用性徽章、轻量 Markdown 渲染(基础语法 + XSS 转义)、休整标注、分析报告(规则引擎)、重新分析、AI 浅追问(无 LLM 优雅降级 + 落库/渲染)、热负荷-心率解读(热档分档/室内排除/心率差)、手工标注气温(设值/清空/越界/非数字)、日历看板、首页数据看板(日/周/月三档各12桶/合计校验/肌群排序/跑步类型分布)、每日详情、单日分享卡片(鉴权重定向/非法+不存在日期/七主题白名单/多跑步聚合稳定性+隐私过滤)、各页面渲染、登出。

---

## 5. 代码结构

```
app/
  config.py          环境配置 (含 .env 轻量加载)
  db.py              SQLite schema + 连接 + owner 预置
  security.py        密码 hash(pbkdf2) + 凭证加密(AES-GCM) + 掩码
  markdown_lite.py   零依赖轻量 Markdown 渲染 (转义优先, 无 XSS 面); 报告叙述 + AI 对话复用
  main.py            FastAPI 路由 (页面 + /api)
  repositories.py    数据访问 + 业务编排 (同步/导入/日历/报告/近期训练概况/看板聚合)
  services/
    xunji.py         训记 Open API 客户端 + 解析 + 镜像写入
    garmin.py        FIT 解析 (zip→fit→通用活动+running metrics) + 场景/运动类型中文标签
    run_classify.py  跑步类型自动分类 (个人基线, 规则) + label() 中文标签
    muscle_mapping.py 动作→肌群 (catalog/关键词/AI补全/手动订正)
    analysis.py      分析上下文构建 + 规则式双线引擎 + 温度-心率热负荷解读
    llm.py           可选 LLM (叙述增强 + 肌群分类 + 目标澄清 + 报告浅追问 + 连通测试)
  templates/         Jinja2 页面 (含 goal_clarify.html 目标澄清对话)
  static/
    css/app.css      全部样式 (含轻量 Markdown 渲染 / 首页看板 / 分段控件 / 饼图)
    js/
      goal_clarify.js       AI 目标澄清对话 (多轮 + markdown 渲染)
      goal_reuse.js         历史目标「一键复用」填入表单
      report_followup.js    报告浅追问 (落库 + markdown 渲染)
      dashboard.js          首页数据看板 (粒度切换 + 柱状图/肌群条/饼图, 零依赖)
scripts/
  seed_strength_from_catalog.py  本地测试用: 从 catalog.json 造力量数据
tests/
  test_end_to_end.py
catalog.json         训记动作 name→肌群type 映射 (259 条, 本地肌群来源)
garmin file/         真实 Garmin zip 样本 (已从 git 移除, 仅本地保留; 见 .gitignore)
```

## 6. 数据模型

见 [docs/design/data-model.md](design/data-model.md), 实际 schema 在 [app/db.py](../app/db.py)。所有业务表带 `user_id`。要点:
- 训记镜像: `xunji_training_days/trainings/movements/sets/action_catalog` + `xunji_sync_state`
- Garmin: `garmin_import_files/activities/laps` + `running_activity_metrics`
- `exercise_muscle_mappings` 动作肌群固化表
- `goal_config_versions` 目标版本 / `rest_notes` 休整标注 / `user_profiles`
- `analysis_reports` 报告快照 (结构化层存 JSON 字段)
- `user_credentials` 加密凭证 / `operation_logs`

---

## 7. 已实现功能 (对照 demo P0)

| 模块 | 状态 | 说明 |
|---|---|---|
| 登录 / 预置 owner / 关闭注册 | ✅ | session cookie, 多用户建模 |
| 凭证 (训记 Key / LLM Key) | ✅ | AES-GCM 加密, 掩码, 测试可用性按钮 |
| 训记同步 | ✅ | 后台线程 + 进度条; 增量/全量/指定日重拉; 只读镜像 |
| 动作→肌群映射 | ✅ | catalog type 优先 → 关键词 → AI补全 → 用户订正; 「数据→动作肌群」页 |
| Garmin 导入 | ✅ | 多文件上传+进度+逐个结果; 去重; 通用活动+running metrics |
| 跑步类型分类 | ✅ | 轻松/长距离/节奏/间歇/恢复/比赛测试; 个人基线规则; 自动+手动 |
| 跑步场景标签 | ✅ | FIT sub_sport → 路跑/跑步机/操场/越野; 分析叙述层+报告场景区均中文化 |
| 月历统计 | ✅ | 日历上方月度总容量+月总跑量统计, 翻月自动变化 |
| 当前目标配置 | ✅ | 手动编辑, 版本化; 历史版本可展开看完整字段 + 「以此版本填入当前目标」复用(填表单待确认, 不无痕覆盖) |
| AI 目标澄清对话 | ✅ | v0.2; 多轮对话 → 结构化草案 → 可编辑表单确认落库; 喂近期训练概况; 对话不落库; 无 LLM 置灰引导; `created_by=ai_clarification` 标记 |
| 轻量 Markdown 渲染 | ✅ | v0.2; `app/markdown_lite.py` 零依赖, 转义优先(无 XSS 面); 报告叙述层 + AI 对话回复复用; 支持标题/粗斜体/列表/行内代码, 不做链接 |
| 凭证可用性口径 | ✅ | v0.2; 凭证页与 `has_llm`/`get_credential` 统一用「能否解密」判断; 换主密钥后旧凭证显示「无法解密·请重新保存」 |
| 休整标注 | ✅ | 极简: 日期范围/影响范围/备注 |
| 用户档案 | ✅ | 身高/体重/年份/性别; 不存目标 |
| AI 分析报告 | ✅ | 规则引擎双线判断 + LLM 叙述增强; fetch 进度+错误提示 |
| 温度—心率解读 | ✅ | v0.2; 用 FIT 自带气温对户外跑分热档(normal/warm/hot); 高温跑心率明显偏高时提示「可能热负荷而非强度上升」; 室内跑不参与热解读; 纯分析层, 不发网络请求 |
| 气温手工标注 | ✅ | v0.2; 活动详情页可手工填/清气温(`temperature_source=manual`), 供设备未记录气温(本样本设备即无温度)时补录; 与 FIT 原生温度走同一热解读链路; 范围校验 -50~60°C |
| 方向性建议 | ✅ | 加/减/维持 + 排布约束 + 目标取舍, 不出完整计划 |
| 历史报告 / 重新分析 | ✅ | 快照, 旧报告不改 |
| AI 浅追问 | ✅ | v0.2; 报告详情页内就该报告快照自然语言追问; 只读不改数据/目标; 问答挂 `report_followups` 持久化; markdown 渲染; 无 LLM 置灰 |
| 首页月历看板 | ✅ | 训记式, 力量容量+肌群标签/跑步类型+距离; 翻月; 点进每日详情 |
| 首页数据看板 | ✅ | v0.2; 日历上方训练概览: 日/周/月粒度切换(各 12 桶, 前端切换零请求); 力量容量趋势 + 跑量趋势(柱状图) + 肌群容量分布(横向条形) + 跑步类型分布(SVG 环形饼图); 纯只读聚合, 零第三方图表库; 周一起始与日历一致 |
| 每日详情页 | ✅ | 当天力量(动作/组/容量) + 跑步, 可再进完整活动 |
| 单日训练分享卡片 | ✅ | v0.3; 每日详情页「生成分享卡片」→ 独立 3:4 竖版海报 (`/day/{date}/share`), 供手动截图; 7 种视觉方案 `?theme=` 切换; 隐私过滤: 力量只出训练部位 + 总容量, 跑步只出类型/场景/距离/用时/配速/心率, 多跑步稳定聚合; 不含动作明细/GPS/地点/分圈/海拔/气温/功率/步频/活动 ID/AI 建议; 独立模板+独立 CSS, 不复用后台侧栏, 不污染 app.css |

## 8. 明确未做 / 后续 (P1+)

- ~~AI 目标澄清对话~~ ✅ 已于 v0.2 实现 (见上表)
- ~~AI 浅追问~~ ✅ 已于 v0.2 实现 (见上表)
- 营养素建议 (用户档案已备好 BMR 输入)
- 对外只读 API 实际端点 (上下文生产层已独立, 架构预留)
- 邀请码注册 UI / 用户管理后台
- 手动运动记录 (预留不开发)
- 天气补全 (湿度/风等 FIT 无的气象项, 需 GPS+时间查历史天气 API; 会引入对外网络请求边界, 低优先级)。注: FIT 自带气温已用于温度—心率解读 (见上表), 此项仅剩 FIT 没有的气象维度
- 更多 Garmin 运动类型解释器 (骑行/游泳/越野跑; 通用活动层已支持存档)
- 训记写回 / 外部 agent 写回 (明确不做)

## 9. 关键边界 (勿破)

- 训记镜像只读, 不写回训记。
- 分析/浅追问只读数据, 不改运动记录。
- 报告是快照; 底层变化后通过「重新分析」出新报告, 旧报告保留。
- 内部 LLM 调用只由用户主动触发; 同步/上传不自动分析。
- 凭证加密存储, 不写日志, 不完整回显。
- 不做主动教练/完整训练计划/医疗建议。

## 10. 部署 (ADR 0001)

- 已部署: 阿里云轻量应用服务器 (华东, 2vCPU/2GB/40GB, Ubuntu 24.04); 当前线上版本 v0.3.0 (2026-07-14)
- 公网地址: `http://106.14.241.47:8000` (方案 A: IP 直连, 无 HTTPS)
- 服务管理: systemd `strength-run`, 开机自启 + 异常重启
- 代码路径: `/opt/strength-run/`
- 日志: `/opt/strength-run/var/logs/app.log`
- 安全: SSH 仅密钥登录 (密码登录已关闭), UFW 仅开放 22+8000/tcp
- LLM 已配置: DeepSeek (`https://api.deepseek.com/v1` + `deepseek-v4-pro`), 连通测试通过
- 云端走方案 A: `http://<公网IP>:<端口>` IP 直连, 仅 owner/可信设备, 关闭公开注册。
- 朋友填训记 Key 必须等方案 C (HTTPS)。

## 11. 已知事项

- 离群跑步数据 (如中途长暂停导致配速异常) 会按距离归类, 不做特殊剔除。
- 训记读取 API 不返回肌群 type, 肌群靠本地 `catalog.json` + AI 补全; 若用户有 catalog 外的新动作, 走关键词/AI。
- 跑步类型是启发式规则, 非绝对; 用户可重分类, 但暂无逐条手动改类型的 UI。
- `sub_sport=3` (trail_run) 已补入映射; 若 Garmin 新固件引入新的 sub_sport 值, 需在 `app/services/garmin.py` `RUN_VARIANT` 字典补条目。
- `.env` 含明文 owner 密码和 Key 占位, 已在 `.gitignore`; 生产务必改 `ENCRYPTION_MASTER_KEY` 和密码。
- 密码与 `.env` 同步: 修改密码需同时更新数据库 hash 和 `/opt/strength-run/.env` 的 `OWNER_PASSWORD` (仅影响重建库场景)，推荐本地改完上传避免 shell `$` 转义问题。
- 容量计算: 参见 [deployment.md](deployment.md#代码层面的容量计算约定)。助力式动作（辅助引体/双杠臂屈伸）按 `(体重-助力重量)×reps` 计算；仅统计 done=1 的组。

## 12. 隐私清理记录 (2026-07-13)

本仓库在早期历史中曾出现真实 Garmin 活动样本 (`garmin file/` 下多份 zip)。这些文件不含 API key, 但属于**个人训练隐私数据**(活动时间 / 距离 / 心率 / 可能的 GPS 轨迹等), 不适合公开仓库长期保留。

2026-07-13 已执行一次完整清理:

- 将仓库临时切为 **private** 后操作。
- 用 `git filter-branch --index-filter` 重写历史, 从**全部分支与 tags** 删除 `garmin file/` 路径。
- 随后删除 `refs/original/*`、清空 reflog、执行 `git gc --prune=now --aggressive` 做最终收口。
- 重新 force-push 主分支与 tags, 并删除远端临时备份分支 `backup/pre-scrub-20260713-170225`。

清理后的只读审计结论:

- 当前 HEAD 与当前可见历史中, **`garmin file/` 路径已不存在**。
- 未发现 `.env`、`var/app.db`、SQLite/zip/fit 等私有数据文件被当前 HEAD 跟踪。
- 未发现真实 **LLM API key / 训记 API key** 明文进入仓库; 唯一扫到的是测试假值 `xjllm_testsecret1234567890`。

仍需保留的现实提醒:

- 如果仓库在清理前曾公开过, 不能排除旧历史已被外部 clone / fork / 缓存; 该风险**无法通过本次清理完全逆转**。
- 因此若后续重新公开, 建议默认假设“旧样本历史可能曾暴露”,并视需要轮换外部服务 key。

## 13. 文档索引

- 需求/术语: [CONTEXT.md](../CONTEXT.md)
- 讨论过程备份: [docs/discussions/2026-07-08-可行性讨论备份.md](discussions/2026-07-08-可行性讨论备份.md)
- ADR: [docs/adr/](adr/) (部署/训记镜像/多用户/对外API)
- 设计: [docs/design/](design/) (范围/UE/架构/数据模型/接口)
- 部署: [docs/deployment.md](deployment.md) (云端部署操作记录)

## 14. 工作方式 (接手开发必读)

- **磨需求比写代码重要**;大改动先出方案再动手。
- 每次开发完跑 `python tests/test_end_to_end.py`,修到全绿。
- 改 SQLite schema 要考虑已有数据迁移 (`CREATE TABLE IF NOT EXISTS` 安全)。
- 所有环境项走配置,不写死路径/localhost。
- 不引第三方前端/图表库 (项目一贯基调,已有零依赖自绘先例)。
