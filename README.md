# 力跑双训分析系统 Demo

力量 + 跑步双线训练总控 demo。整合训记(力量)与 Garmin(跑步)数据, 基于当前目标配置和休整标注, 生成可存档的双线分析报告, 识别冲突/过量风险并给出方向性建议。

设计文档见 [docs/](docs/) 与 [CONTEXT.md](CONTEXT.md)。**交接/现状速览见 [docs/HANDOVER.md](docs/HANDOVER.md)。**

## 技术栈

- Python + FastAPI + Jinja2 (轻量 Web 单体)
- SQLite (本地结构化文件, `var/app.db`)
- 原生 HTML/CSS, 不用大型 UI 组件库
- 云端 LLM 可选 (OpenAI 兼容); 无 LLM Key 时用规则引擎兜底

## 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env        # 按需修改 OWNER_PASSWORD 等
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 , 用 `.env` 里的 `OWNER_USERNAME` / `OWNER_PASSWORD` 登录。首次启动自动建库并预置 owner。

## 首次使用路径

1. 设置 → 用户档案 / 凭证 (训记 Key、LLM Key, 可点「测试可用性」)
2. 数据 → Garmin: 上传 Garmin 原始 zip (支持多文件一次上传, 自动去重 + 跑步类型分类)
3. 数据 → 训记: 配置 Key 后同步力量数据 (后台同步 + 进度条)
4. 数据 → 动作肌群: 对未分类动作「AI 一键补全」或手动订正
5. 目标 → 配置当前目标
6. 休整标注: 按需记录缺训/伤病
7. 分析 → 生成报告 (进度提示) → 报告页复盘
8. 首页 = 月历看板, 每天显示力量容量+肌群 / 跑步类型+距离, 点格子看当天详情

## 主要功能

- **首页月历看板**: 训记式月历, 力量(容量kg+肌群标签)、跑步(类型+距离); 翻月; 点进每日详情
- **训记同步**: 后台线程 + 实时进度; 增量/全量/指定日重拉; 只读镜像
- **Garmin 导入**: 多文件上传; zip→fit 解析; 去重; 户外/操场/跑步机场景识别
- **跑步类型分类**: 轻松/长距离/节奏/间歇/恢复/比赛测试, 基于个人基线自动判定
- **动作肌群**: 训记type优先 → 关键词 → AI 补全 → 用户订正, 固化复用
- **AI 分析报告**: 规则引擎双线负荷/冲突/过量判断 + LLM 叙述增强; 快照存档 + 重新分析
- **目标 / 休整标注 / 用户档案 / 数据覆盖**

## 配置项 (环境变量)

见 [.env.example](.env.example)。关键项:

- `DATABASE_PATH` / `UPLOAD_DIR` / `LOG_DIR` — 数据与文件位置 (可迁移/备份)
- `ENCRYPTION_MASTER_KEY` — 凭证加密主密钥 (生产必须改)
- `ALLOW_PUBLIC_SIGNUP` — 默认 false, demo 不开放注册
- `COOKIE_SECURE` — HTTP(方案A) 用 false, HTTPS(方案C) 用 true
- `LLM_BASE_URL` / `LLM_MODEL` — 留空则用规则引擎
- `MAX_UPLOAD_MB` — 上传大小限制

## 测试

```bash
python tests/test_end_to_end.py
```

端到端回归: 登录、Garmin 导入(真实样本)、去重、力量 seed、目标、休整标注、
分析报告(规则引擎)、重新分析、各页面渲染、登出。使用临时库, 不污染 `var/`。

## 边界 (demo 阶段)

- 训记镜像只读, 不写回。
- 分析只读数据, 不修改运动记录。
- 报告是快照; 底层变化后通过「重新分析」生成新报告, 旧报告保留。
- 不做主动教练/完整训练计划; 只给方向性建议。
- 手动记录、饮食记录、对外只读 API、AI 目标澄清、浅追问为后续能力。
- 天气补全(GPS+时间查历史天气)为低优先级后续项。
