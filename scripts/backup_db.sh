#!/usr/bin/env bash
# 每日备份 SQLite 库 -> var/backups/app-YYYYmmdd-HHMMSS.db.gz
#
# 为什么不用 cp/scp app.db:
#   库是 WAL 模式, 此刻的写入可能还在 app.db-wal 里没落盘,
#   直接拷单个 app.db 文件得到的可能是不一致快照。
#   这里走 SQLite 在线 backup API, 拿到的是一致快照 (WAL 安全)。
#
# 由 cron 每日调用 (见 docs/deployment.md 备份小节)。只读源库, 只写 backups 目录。
set -euo pipefail

APP_DIR="/opt/strength-run"
DB="$APP_DIR/var/app.db"
BACKUP_DIR="$APP_DIR/var/backups"
PY="$APP_DIR/venv/bin/python"
KEEP=14   # 保留最近多少份 (库很小, 纯为整洁)

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="$BACKUP_DIR/app-$STAMP.db"

# 一致快照 + 完整性校验 (一次 Python 调用完成)
"$PY" - "$DB" "$TMP" <<'PYEOF'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path)          # backup API 读取一致快照, 不改源库
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
src.close()
chk = sqlite3.connect(dst_path)
ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
chk.close()
dst.close()
if ok != "ok":
    sys.stderr.write("integrity_check failed: %s\n" % ok)
    sys.exit(1)
PYEOF

gzip -f "$TMP"

# 只留最近 KEEP 份, 删更旧的
ls -1t "$BACKUP_DIR"/app-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "[backup] $(date -Is) -> ${TMP}.gz"
