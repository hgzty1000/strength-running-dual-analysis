# 部署文档 (云端部署操作记录)

- 最后更新: 2026-07-15
- 部署方案: ADR 0001 方案 A (IP 直连, 无 HTTPS, 不备案)
- 初始化部署: Claude (2026-07-11)
- 后续维护: Claude
- 服务器版本: v0.3.1 (2026-07-15) — 分享卡片 PNG 导出 + 窄屏等比缩放修复 + 看板饼图改按距离

## 服务器信息

| 项目 | 值 |
|---|---|
| 厂商 | 阿里云轻量应用服务器 |
| 区域 | 华东 |
| 规格 | 2vCPU / 2GB / 40GB |
| OS | Ubuntu 24.04.2 LTS |
| 公网 IP | `106.14.241.47` |
| 应用地址 | `http://106.14.241.47:8000` |
| 当前版本 | v0.3.1 |

## 应用部署

- 代码路径: `/opt/strength-run/`
- Python: 3.12.3, venv 在 `/opt/strength-run/venv/`
- 服务: systemd `strength-run`, 开机自启 + 异常自动重启
- 启动/停止: `systemctl start|stop|restart strength-run`
- 查看日志: `journalctl -u strength-run -f` 或 `tail -f /opt/strength-run/var/logs/app.log`
- 数据文件: `/opt/strength-run/var/app.db` (SQLite, WAL)

## 安全加固 (2026-07-11)

- SSH 仅密钥登录, 密码登录已关闭 (`PasswordAuthentication no`)
- 公钥已部署: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKtX9tzafmvPvy58GOCzHnAQ5YM6dZkRMoBtvpay++16 hgzty@outlook.com`
- UFW 已启用, 仅开放入站规则: 22/tcp (SSH), 8000/tcp (应用)
- 阿里云安全组: 8000/tcp 已对 `0.0.0.0/0` 开放
- 强密码已设置

## 环境变量 (关键项)

`.env` 位于 `/opt/strength-run/.env`:

| 变量 | 值 | 说明 |
|---|---|---|
| `APP_ENV` | `production` | |
| `APP_BASE_URL` | `http://106.14.241.47:8000` | |
| `HOST` | `0.0.0.0` | 监听所有网卡 |
| `PORT` | `8000` | |
| `ENCRYPTION_MASTER_KEY` | 32 位随机字符串 | 改动会导致旧凭证无法解密 |
| `ALLOW_PUBLIC_SIGNUP` | `false` | 关闭公开注册 |
| `COOKIE_SECURE` | `false` | 方案 C (HTTPS) 时改为 true |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek 平台 (OpenAI 兼容) |
| `LLM_MODEL` | `deepseek-v4-pro` | 平台支持 `deepseek-v4-pro` / `deepseek-v4-flash` |

## 密码管理

修改密码的方法（推荐本地改完再上传，避免 shell 转义问题）:

```bash
# 1. 复制服务器数据库到本地
scp root@106.14.241.47:/opt/strength-run/var/app.db ./var/app.db

# 2. 本地更新密码 hash
python - <<'EOF'
import hashlib, base64, os, sqlite3
password = '新密码'
salt = os.urandom(16)
digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 200_000)
encoded = 'pbkdf2_sha256$200000$%s$%s' % (base64.urlsafe_b64encode(salt).decode('ascii'), base64.urlsafe_b64encode(digest).decode('ascii'))
conn = sqlite3.connect('var/app.db')
conn.execute("UPDATE users SET password_hash=? WHERE username='owner'", (encoded,))
conn.commit()
conn.close()
print('done')
EOF

# 3. 上传替换, 重启服务
ssh root@106.14.241.47 "systemctl stop strength-run"
scp var/app.db root@106.14.241.47:/opt/strength-run/var/app.db
ssh root@106.14.241.47 "systemctl start strength-run"

# 4. 同步 .env (仅影响将来重建库的场景)
# 在服务器上: sed -i 's/OWNER_PASSWORD=.*/OWNER_PASSWORD=新密码/' /opt/strength-run/.env
```

## 数据备份

SQLite 数据库: `/opt/strength-run/var/app.db` (WAL 模式)。

> ⚠️ **不要直接 `cp`/`scp` 单个 `app.db`**: 库是 WAL 模式, 此刻的写入可能还在 `app.db-wal` 里没落盘, 直拷单文件得到的可能是**不一致快照**。必须走 SQLite 在线 backup API 拿一致快照 —— 即下面的 `scripts/backup_db.sh`。

### 同机每日备份 (已配置, 2026-07-12)

- 脚本: [scripts/backup_db.sh](../scripts/backup_db.sh) —— 用 venv Python 调 SQLite 在线 backup API (WAL 安全) + `PRAGMA integrity_check` 校验 + gzip, 零新依赖。
- cron (root): 每日 `03:17` 跑, 日志追加到 `var/logs/backup.log`。
  ```
  17 3 * * * /opt/strength-run/scripts/backup_db.sh >> /opt/strength-run/var/logs/backup.log 2>&1
  ```
- 产物: `/opt/strength-run/var/backups/app-YYYYmmdd-HHMMSS.db.gz`, 自动保留最近 **14** 份 (库约 4.4M, 压缩后 ~700K, 占用可忽略)。
- 手动跑一次: `ssh root@106.14.241.47 /opt/strength-run/scripts/backup_db.sh`

> **局限**: 同机备份防误删/误改/库损坏, 但**防不了整机/磁盘丢失** (备份与源库同一块盘)。异地保护靠下面的手动拉取。

### 定期手动异地拉取 (防整机丢失)

按需在本地跑, 把最新一份备份拉到服务器之外:

```bash
# 拉取服务器上最新的一份备份到本地 ./backup/
mkdir -p backup
ssh root@106.14.241.47 'ls -1t /opt/strength-run/var/backups/app-*.db.gz | head -1' \
  | xargs -I{} scp root@106.14.241.47:{} ./backup/
```

### 从备份恢复

```bash
# 1. 解压某份备份 (得到一致的 app.db)
gunzip -k app-YYYYmmdd-HHMMSS.db.gz

# 2. 停服务 -> 替换库 -> 起服务 (WAL 文件会由新库重新生成, 无需一起拷)
ssh root@106.14.241.47 "systemctl stop strength-run"
scp app-YYYYmmdd-HHMMSS.db root@106.14.241.47:/opt/strength-run/var/app.db
ssh root@106.14.241.47 "systemctl start strength-run"
```

## 部署更新

更新代码后:

```bash
# 1. 本地打包 (排除不必要文件)
tar --exclude='.git' --exclude='__pycache__' --exclude='.claude' --exclude='var' \
    --exclude='*.pyc' --exclude='.vscode' --exclude='.env' \
    -czf deploy.tar.gz --dereference .

# 2. 上传
scp deploy.tar.gz root@106.14.241.47:/opt/strength-run/

# 3. 服务器解压并重启
ssh root@106.14.241.47 "
  cd /opt/strength-run
  systemctl stop strength-run
  tar xzf deploy.tar.gz
  source venv/bin/activate
  pip install -r requirements.txt   # 如有新依赖
  systemctl start strength-run
"
```

### 部署记录

- **v0.4.0 (2026-07-16)** — 对外只读 API v1 (ADR 0004): 9 个 `/api/v1/*` 端点 (meta/context/days/goals/reports/muscle-map/rest-notes), Bearer `srda_` Key 鉴权, 按 user_id 隔离, 只读不触发 LLM。owner 在「设置/凭证」页可签发/吊销 Key (`/settings/api-keys`)。同批补提交了 v0.3.1 漏进 git 的窄屏修复文件。上线前 WAL 安全备份 (`app-20260716-135954.db.gz`, integrity=ok)。新增 schema: `api_keys` 表 (由 `app/db.py` 建表自动生效, 无手动迁移)。健康检查: 公网 `/api/v1/meta` 真 Key 200 / 无凭证 401 / 假 Key 401, credentials 页 owner 见「对外 API Key」按钮, `/settings/api-keys` 带登录 200。**注意 API 是方案 A 明文 HTTP, 仅自用, 勿把 Key 发他人或在不可信网络使用** (对外开放等方案 C HTTPS, 见 ADR 0001/0004)。
  - **踩坑记录 (严重, 教训比 v0.3.1 更深)**: 本次部署经历了一长串「看起来成功、实则没落地」的假象, 根因有二。**(1) 运行进程配置开关未真正生效**: `/api/v1/*` 一直返回自定义 404「对外 API 未启用」, 查了整条链路才定位到 `.env` 里 `OUTBOUND_API_ENABLED=true` **根本没被写进文件** (多次「已追加」输出是假象), 而 config.py 靠自身 `_load_dotenv()` 读项目根 `.env` (systemd unit **无 `EnvironmentFile=`**, 不注入环境变量)。最终用「写入后立即回读 grep 计数 + 用干净子进程实测 `settings.outbound_api_enabled` 为 True」两道 GATE 才确认落地。**(2) 提交/推送/上传/解压全程被不可靠的命令输出误导**: 出现过多个根本不存在的 commit hash (如 `3c1e8e9`/`5f6c2e1`/`6f395f2`, `git log` 里查无此提交)、「文件已更新」但运行目录 main.py 仍是旧版 (sha256 不符)、旧 uvicorn 进程 (PID 85128) 一直占着 8000 端口没被 restart 替换 (导致公网打到旧代码)。**教训**: ① 关键状态一律「写后回读校验」, 部署包用 `git archive HEAD` 生成并以 **main.py 的 sha256 作为跨机器锚点** (本地算一次、服务器解压后再算一次, 相等才算数); ② 不信任何单条命令的「成功」字样, 用独立查询 (`git ls-remote`/`ss -ltnp`/真 Key 打端点) 二次确认; ③ 重启后必须核对「监听端口的 PID 是新 PID」, 防旧进程占端口; ④ 删 `__pycache__` 清旧字节码。
- **v0.3.1 (2026-07-15)** — 分享卡片 PNG 导出 + 看板饼图改按距离统计 + 分享卡片窄屏等比缩放修复。上线前 WAL 安全备份 (`app-20260715-061432.db.gz`, integrity=ok);发布包排除 `.env`/`var`/`tools`/训练样本。**新增前端依赖**: `app/static/js/vendor/snapdom.min.js` (v2.15.0, MIT, 133KB, 纯静态无 Python 依赖, 本地 vendor 不走 CDN) —— 唯一一次破「不引前端库」的例, 仅用于分享卡片 DOM→PNG。健康检查通过 (`/` 303、`/login` 200、share 未登录 303、`share.css`/`snapdom.min.js`/`share_export.js`/`share_fit.js` 均 200)。无新依赖 (指 Python), 无 schema 变更。
  - **踩坑记录**: 首轮解压把关键步骤串成一长条 `cd && stop && tar && start`, 中间 `tar` 步静默失败但 `systemctl start` 仍成功, 造成「服务 active 但文件没更新」的假绿灯 (`share_fit.js` 404、css 仍旧版)。教训: **部署解压用 `set -e` 分步执行并逐一 verify 关键文件**, 不把 tar 和 restart 串在一条靠 `&&` 兜底的命令里。另 scp 源用**绝对路径**, 避免 shell cwd 漂移导致传错包。
- **v0.3.0 (2026-07-14)** — 单日训练分享卡片。上线前用 `scripts/backup_db.sh` 做 WAL 安全备份 (`app-20260714-154528.db.gz`);发布包仅含 git 已提交代码 (排除 `.env`/`var`/训练样本);解压重启后健康检查通过 (`/` 302→303 跳登录、`/login` 200、`/day/{date}/share` 未登录 303 跳登录、`share.css` 200)。无新增依赖,无 schema 变更。
- **v0.2.1 (2026-07-12)** — 容量计算修复 (助力式动作 `(体重-助力)×次数`)。

## 代码层面的容量计算约定

- 训记 API **不暴露容量/volume 字段**，只提供每组的 weight/reps/done。
- 容量计算统一入口: `app/services/xunji.py` 的 `compute_set_volume()` (2026-07-12 引入)。
  - 普通动作: `weight × reps`
  - 助力式动作（动作名含「辅助」或「助力」）：`(体重 - 助力重量) × reps`
  - 仅统计 `done=True` 的组（排除计划但未做的组）
  - 无用户体重档案时降级为普通公式 `weight × reps`
- 助力式动作关键词列表: `_ASSISTED_KEYWORDS` 位于 `app/services/xunji.py`。新增辅助类动作只需在此列表补充关键词。
- 涉及容量计算的代码位置 (均已改为调用 `compute_set_volume()`):
  - `app/repositories.py` — `dashboard_stats()`、`month_calendar()`、`day_detail()`
  - `app/services/analysis.py` — `_strength_summary()` (分析报告)
  - 所有 SQL 查询均已加 `AND s.done=1` 过滤

- [ ] 方案 C (HTTPS): 买域名 + ICP 备案 + Let's Encrypt 证书 — 见 ADR 0001
- [x] 定期数据库备份 (2026-07-12: cron 每日 03:17 在线 backup + gzip, 保留 14 份, 见上方备份小节; 异地拉为手动)
- [x] 配置 LLM (2026-07-12: DeepSeek `deepseek-v4-pro`, 连通测试通过 ~1.2s)
- [ ] 改密码 UI (当前无界面, 需命令行改)
- [x] 助力式动作容量修正 (2026-07-12: `compute_set_volume()` 按 `(体重-助力)×次数`, 见上方代码约定)
