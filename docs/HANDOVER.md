# 交接文档 (Demo 实现现状)

- 日期: 2026-07-10
- 版本: v0.1 (已 git tag, commit `45823ee`)
- 状态: demo 可运行, 核心闭环全通, 回归测试 51/51 通过
- 项目名: **力跑双训分析系统** (原名"双修运动平台")
- 面向: 接手继续开发/维护的人

本文件描述**已实现的 demo 到底是什么样、怎么跑、边界在哪**。需求与设计背景见 [CONTEXT.md](../CONTEXT.md) 和 [docs/design/](design/)。

---

## 1. 一句话

力跑双训分析系统 — 力量(训记) + 跑步(Garmin) 双线训练分析 demo。整合两条线数据 + 当前目标配置 + 休整标注, 生成可存档的双线分析报告, 识别冲突/过量并给方向性建议。首页是训记式月历看板, 可点进每天详情。

---

## 2. 技术栈

- Python 3.14 + FastAPI + Jinja2 (轻量 Web 单体)
- SQLite (本地文件 `var/app.db`, WAL)
- 原生 HTML/CSS, 无大型 UI 组件库, 一套页面响应式 (桌面侧栏 / 手机汉堡菜单)
- 云端 LLM 可选 (OpenAI 兼容, 当前接 DeepSeek); 无 LLM 时规则引擎兜底
- 依赖见 [requirements.txt](../requirements.txt)

## 3. 运行

```bash
pip install -r requirements.txt
cp .env.example .env    # 按需改, 首次启动自动建库 + 预置 owner
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 , 用 `.env` 的 `OWNER_USERNAME`/`OWNER_PASSWORD` 登录。

配置见 [.env.example](../.env.example)。LLM 相关: `LLM_BASE_URL` + `LLM_MODEL` 平台级配置 (当前 DeepSeek: `https://api.deepseek.com/v1` + `deepseek-v4-flash`), 用户在「设置→凭证」只填 Key。

## 4. 测试

```bash
python tests/test_end_to_end.py      # 51 项端到端回归, 用临时库, 不污染 var/
```

覆盖: 登录/鉴权、真实 Garmin 导入(多文件+去重)、跑步类型分类、训记 seed、肌群映射(含手动订正+AI补全endpoint)、目标、休整标注、分析报告(规则引擎)、重新分析、日历看板、每日详情、各页面渲染、登出。

---

## 5. 代码结构

```
app/
  config.py          环境配置 (含 .env 轻量加载)
  db.py              SQLite schema + 连接 + owner 预置
  security.py        密码 hash(pbkdf2) + 凭证加密(AES-GCM) + 掩码
  main.py            FastAPI 路由 (页面 + /api)
  repositories.py    数据访问 + 业务编排 (同步/导入/日历/报告)
  services/
    xunji.py         训记 Open API 客户端 + 解析 + 镜像写入
    garmin.py        FIT 解析 (zip→fit→通用活动+running metrics) + 场景/运动类型中文标签
    run_classify.py  跑步类型自动分类 (个人基线, 规则) + label() 中文标签
    muscle_mapping.py 动作→肌群 (catalog/关键词/AI补全/手动订正)
    analysis.py      分析上下文构建 + 规则式双线引擎 (叙述层场景已中文化)
    llm.py           可选 LLM (叙述增强 + 肌群分类 + 连通测试)
  templates/         Jinja2 页面
  static/css/app.css 全部样式
scripts/
  seed_strength_from_catalog.py  本地测试用: 从 catalog.json 造力量数据
tests/
  test_end_to_end.py
catalog.json         训记动作 name→肌群type 映射 (259 条, 本地肌群来源)
garmin file/         48 个真实 Garmin zip 样本
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
| 当前目标配置 | ✅ | 手动编辑, 版本化 |
| 休整标注 | ✅ | 极简: 日期范围/影响范围/备注 |
| 用户档案 | ✅ | 身高/体重/年份/性别; 不存目标 |
| AI 分析报告 | ✅ | 规则引擎双线判断 + LLM 叙述增强; fetch 进度+错误提示 |
| 方向性建议 | ✅ | 加/减/维持 + 排布约束 + 目标取舍, 不出完整计划 |
| 历史报告 / 重新分析 | ✅ | 快照, 旧报告不改 |
| 首页月历看板 | ✅ | 训记式, 力量容量+肌群标签/跑步类型+距离; 翻月; 点进每日详情 |
| 每日详情页 | ✅ | 当天力量(动作/组/容量) + 跑步, 可再进完整活动 |

## 8. 明确未做 / 后续 (P1+)

- AI 目标澄清对话 (目标目前手动填, 入口预留)
- AI 浅追问
- 营养素建议 (用户档案已备好 BMR 输入)
- 对外只读 API 实际端点 (上下文生产层已独立, 架构预留)
- 邀请码注册 UI / 用户管理后台
- 手动运动记录 (预留不开发)
- 天气补全 (GPS+时间查历史气温, 低优先级)
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

- demo 本地优先; 架构已按云端可部署设计 (全配置化, 不写死路径/localhost)。
- 云端走方案 A: `http://<公网IP>:<端口>` IP 直连, 仅 owner/可信设备, 关闭公开注册。
- 朋友填训记 Key 必须等方案 C (HTTPS)。
- 上真实数据前须完成服务器安全加固 (ADR 0001 遗留)。

## 11. 已知事项

- 离群跑步数据 (如中途长暂停导致配速异常) 会按距离归类, 不做特殊剔除。
- 训记读取 API 不返回肌群 type, 肌群靠本地 `catalog.json` + AI 补全; 若用户有 catalog 外的新动作, 走关键词/AI。
- 跑步类型是启发式规则, 非绝对; 用户可重分类, 但暂无逐条手动改类型的 UI。
- `sub_sport=3` (trail_run) 已补入映射; 若 Garmin 新固件引入新的 sub_sport 值, 需在 `app/services/garmin.py` `RUN_VARIANT` 字典补条目。
- `.env` 含明文 owner 密码和 Key 占位, 已在 `.gitignore`; 生产务必改 `ENCRYPTION_MASTER_KEY` 和密码。

## 12. 文档索引

- 需求/术语: [CONTEXT.md](../CONTEXT.md)
- 讨论过程备份: [docs/discussions/2026-07-08-可行性讨论备份.md](discussions/2026-07-08-可行性讨论备份.md)
- ADR: [docs/adr/](adr/) (部署/训记镜像/多用户/对外API)
- 设计: [docs/design/](design/) (范围/UE/架构/数据模型/接口)
