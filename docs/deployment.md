# 部署文档 (云端部署操作记录)

- 最后更新: 2026-07-12
- 部署方案: ADR 0001 方案 A (IP 直连, 无 HTTPS, 不备案)
- 初始化部署: Claude (2026-07-11)
- 后续维护: Claude
- 服务器版本: v0.2.1 (2026-07-12) — 含容量计算修复

## 服务器信息

| 项目 | 值 |
|---|---|
| 厂商 | 阿里云轻量应用服务器 |
| 区域 | 华东 |
| 规格 | 2vCPU / 2GB / 40GB |
| OS | Ubuntu 24.04.2 LTS |
| 公网 IP | `106.14.241.47` |
| 应用地址 | `http://106.14.241.47:8000` |
| 当前版本 | v0.2.1 |

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
| `LLM_BASE_URL` | (空) | 当前未配置 |
| `LLM_MODEL` | (空) | 当前未配置 |

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

SQLite 数据库: `/opt/strength-run/var/app.db`。备份示例:

```bash
# 从服务器拉取
scp root@106.14.241.47:/opt/strength-run/var/app.db ./backup/app-$(date +%Y%m%d).db

# 或用 rsync
rsync -avz root@106.14.241.47:/opt/strength-run/var/ ./backup/var/
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
- [ ] 定期数据库备份
- [ ] 配置 LLM (DeepSeek 或其他 OpenAI 兼容平台)
- [ ] 改密码 UI (当前无界面, 需命令行改)
- [ ] 助力式动作容量修正: 需要用户体重 + 动作类型判断，区分「负重式」与「助力式」——见上方代码约定
