"""平台对外 API Key: 签发 / 校验 / 列表 / 吊销 (ADR 0004)。

信任模型: 签发方向类似 LLM Key —— 仅 owner 后台代签 (见 main.py 路由的 owner 判断)。
存储形态: 只存 sha256 哈希, 签发时一次性返回明文, 之后不可再现 (类 GitHub PAT)。
Key 绑定 user_id: 外部 agent 持之只能读该 user 自己的数据, 天然复用多用户隔离。
"""
from __future__ import annotations

import secrets
from typing import Any

from app.config import settings
from app.db import db, new_id, now_utc
from app.security import token_hash


def _prefix_of(raw_key: str) -> str:
    # 存明文前缀 (含 srda_ + 前 6 位随机), 供列表识别是哪个 Key, 不泄露完整 Key
    return raw_key[: len(settings.api_key_prefix) + 6]


def issue_api_key(user_id: str, label: str | None = None) -> dict[str, Any]:
    """签发一个新 Key。返回含**一次性明文** raw_key, 调用方须立即展示且只展示这一次。"""
    raw_key = settings.api_key_prefix + secrets.token_urlsafe(32)
    key_id = new_id()
    now = now_utc()
    with db() as conn:
        conn.execute(
            """INSERT INTO api_keys (id, user_id, key_hash, prefix, label, created_at)
            VALUES (?,?,?,?,?,?)""",
            (key_id, user_id, token_hash(raw_key), _prefix_of(raw_key), (label or "").strip() or None, now),
        )
    return {"id": key_id, "raw_key": raw_key, "prefix": _prefix_of(raw_key), "label": label, "created_at": now}


def list_api_keys(user_id: str) -> list[dict[str, Any]]:
    """列出某 user 的 Key (含已吊销), 不含哈希/明文。"""
    with db() as conn:
        rows = conn.execute(
            """SELECT id, prefix, label, created_at, last_used_at, revoked_at
            FROM api_keys WHERE user_id=? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_api_key(user_id: str, key_id: str) -> bool:
    """吊销某 Key (按 user_id 限定, 防越权吊销他人 Key)。返回是否有改动。"""
    with db() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (now_utc(), key_id, user_id),
        )
        return cur.rowcount > 0


def resolve_api_key(raw_key: str) -> str | None:
    """校验 Bearer token, 返回其绑定的 user_id (且 user 仍 active); 无效/已吊销返回 None。

    命中即更新 last_used_at (供 owner 在列表看使用情况)。
    """
    if not raw_key or not raw_key.startswith(settings.api_key_prefix):
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT k.id, k.user_id FROM api_keys k
            JOIN users u ON u.id=k.user_id
            WHERE k.key_hash=? AND k.revoked_at IS NULL AND u.status='active'""",
            (token_hash(raw_key),),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now_utc(), row["id"]))
        return row["user_id"]
