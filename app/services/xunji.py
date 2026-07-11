"""训记 (Xunji) 同步客户端与解析。

设计:
- XunjiClient 封装对训记 Open API 的只读调用(真实 Key 使用)。
- 解析层把训记返回的训练日数据拆成 训练 / 动作 / 组 三层,写入本地镜像。
- 同步不写回训记;镜像只读。
- 网络细节(具体 endpoint/字段)可能随训记 API 调整,这里做防御式解析:
  尽量从常见字段名推断,缺失则安全跳过,不因单字段异常整体失败。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.db import db, new_id, now_utc


# ---- 助力式动作判断 ----


_ASSISTED_KEYWORDS = ["辅助", "助力"]


def is_assisted_exercise(action_name: str) -> bool:
    """通过动作名称关键词判断是否为助力式（辅助）动作。"""
    return any(kw in action_name for kw in _ASSISTED_KEYWORDS)


def compute_set_volume(
    weight: float | None,
    reps: int | None,
    action_name: str = "",
    body_weight_kg: float | None = None,
) -> float:
    """计算单组容量。

    助力式动作（辅助引体/双杠臂屈伸等）：(体重 - 助力重量) × 次数。
    负重式动作：重量 × 次数。
    无体重数据时降级为普通公式。
    """
    w = weight or 0.0
    r = reps or 0
    if is_assisted_exercise(action_name) and body_weight_kg and body_weight_kg > 0:
        return max(0.0, body_weight_kg - w) * r
    return w * r


class XunjiError(Exception):
    pass


class XunjiClient:
    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 30.0):
        if not api_key:
            raise XunjiError("训记 Key 未配置")
        self.api_key = api_key
        self.base_url = (base_url or settings.xunji_api_base_url).rstrip("/")
        self.timeout = timeout

    SCHEMA_VERSION = "train_open_api_v2"
    READ_PATH = "/api_trains_for_llm_v2"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    def fetch_day(self, datestr: str, include_full: bool = True) -> dict[str, Any]:
        """读取某天训练。返回原始 JSON (含 res.trains)。"""
        url = f"{self.base_url}{self.READ_PATH}"
        body = {"schema_version": self.SCHEMA_VERSION, "datestr": datestr, "include_full_data": bool(include_full)}
        try:
            resp = httpx.post(url, headers=self._headers(), json=body, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise XunjiError(f"训记 {datestr} 数据获取失败: HTTP {exc.response.status_code}") from exc
        except Exception as exc:  # noqa: BLE001
            raise XunjiError(f"训记 {datestr} 数据获取失败: {type(exc).__name__}") from exc
        if isinstance(data, dict) and data.get("success") is False:
            msg = data.get("message") or data.get("error") or "训记返回 success=false"
            raise XunjiError(str(msg))
        return data

    def test_connection(self) -> dict[str, Any]:
        """最小连通性测试: 读取今天(轻量), 验证 base_url/key 可用。"""
        import time
        from datetime import datetime, timedelta, timezone

        today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        url = f"{self.base_url}{self.READ_PATH}"
        body = {"schema_version": self.SCHEMA_VERSION, "datestr": today, "include_full_data": False}
        start = time.monotonic()
        try:
            resp = httpx.post(url, headers=self._headers(), json=body, timeout=15.0)
        except httpx.TimeoutException:
            return {"ok": False, "message": "请求超时 (15s)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"连接失败: {type(exc).__name__}"}
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                return {"ok": False, "message": "返回非 JSON", "latency_ms": latency}
            if data.get("success") is False:
                msg = str(data.get("message") or data.get("error") or "")
                low = msg.lower()
                if "apikey" in low or "invalid" in low or "missing" in low:
                    return {"ok": False, "message": f"Key 无效: {msg}", "latency_ms": latency}
                if "vip" in low or "会员" in msg:
                    return {"ok": False, "message": f"需要会员权限: {msg}", "latency_ms": latency}
                return {"ok": False, "message": msg or "success=false", "latency_ms": latency}
            trains = _find_trainings(data)
            return {"ok": True, "message": f"可用, 今日 {len(trains)} 条训练记录", "latency_ms": latency}
        if resp.status_code in (401, 403):
            return {"ok": False, "message": f"鉴权失败 (HTTP {resp.status_code}), 请检查训记 Key", "latency_ms": latency}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": latency}


def _extract_movements(data: Any) -> list[dict[str, Any]]:
    """从 catalog 数据中提取动作列表, 兼容 {res:{movements:[...]}} 与 {movements:[...]}。"""
    if isinstance(data, dict):
        res = data.get("res", data)
        if isinstance(res, dict):
            movements = res.get("movements")
            if isinstance(movements, list):
                return movements
    if isinstance(data, list):
        return data
    return []


def load_local_catalog(user_id: str) -> int:
    """从本地 catalog.json 载入动作目录 (name->type) 到本地表。返回条数。"""
    import json as _json
    from pathlib import Path

    catalog_path = Path(__file__).resolve().parent.parent.parent / "catalog.json"
    if not catalog_path.exists():
        return 0
    try:
        data = _json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    movements = _extract_movements(data)
    return store_catalog(user_id, movements)


# ------------ 解析训记训练日 → 三层结构 ------------

def parse_training_day(raw_day: Any) -> list[dict[str, Any]]:
    """把一天的训记数据 (train_open_api_v2) 解析成 trainings 列表。

    真实结构: res.trains[].movements[].sets[]。
    - set 字段: weight/unit/reps/time/rpe/done, 记录型动作在 sets[].metrics。
    - 超级组/递减组: sets[].items[].set 里有 weight/unit/reps/time/metrics。
    - note 可能是对象或 JSON 字符串。
    对未知结构做防御, 尽量不抛异常。
    """
    trainings_raw = _find_trainings(raw_day)
    result: list[dict[str, Any]] = []
    for t in trainings_raw:
        if not isinstance(t, dict):
            continue
        movements_raw = t.get("movements") or []
        movements: list[dict[str, Any]] = []
        for m in movements_raw if isinstance(movements_raw, list) else []:
            if not isinstance(m, dict):
                continue
            sets: list[dict[str, Any]] = []
            for s in m.get("sets") or []:
                if not isinstance(s, dict):
                    continue
                # 超级组/递减组: 展开 items[].set 为多组
                items = s.get("items")
                if isinstance(items, list) and items:
                    for item in items:
                        sub = item.get("set") if isinstance(item, dict) else None
                        if isinstance(sub, dict):
                            sets.append(_parse_set(sub))
                else:
                    sets.append(_parse_set(s))
            movements.append({
                "action_name": m.get("name") or "未知动作",
                "action_id": None,  # 训记不暴露内部 key
                "type": m.get("type") or m.get("difficulty") and None or None,
                "sets": sets,
                "raw": m,
            })
        note = t.get("note")
        note_text = None
        if isinstance(note, dict):
            note_text = note.get("text")
        elif isinstance(note, str):
            note_text = note
        result.append({
            "local_id": _str(t.get("localid")),
            "title": t.get("title") or t.get("name"),
            "note": note_text,
            "start": _str(t.get("start")),
            "end": _str(t.get("end")),
            "calories": _num(t.get("calories") or t.get("kcal")),
            "movements": movements,
            "raw": t,
        })
    return result


def _parse_set(s: dict[str, Any]) -> dict[str, Any]:
    weight = _num(s.get("weight"))
    if weight is None:
        weight = _num(s.get("weight_kg"))
    return {
        "weight": weight,
        "weight_unit": s.get("unit") or s.get("weight_unit"),
        "reps": _int(s.get("reps")),
        "rpe": _num(s.get("rpe")),
        "rest_seconds": _int(s.get("rest") or s.get("rest_seconds") or s.get("duration_s")),
        "done": 1 if s.get("done") in (True, 1, "1") else (0 if s.get("done") in (False, 0, "0") else None),
        "raw": s,
    }


def _find_trainings(raw_day: Any) -> list[Any]:
    if isinstance(raw_day, dict):
        res = raw_day.get("res", raw_day)
        if isinstance(res, dict):
            # 真实结构 res.trains
            for key in ("trains", "trainings", "records", "list", "data"):
                v = res.get(key)
                if isinstance(v, list):
                    return v
            if res.get("movements"):
                return [res]
        if isinstance(res, list):
            return res
    if isinstance(raw_day, list):
        return raw_day
    return []


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _str(v: Any) -> str | None:
    return str(v) if v is not None else None


# ------------ 写入本地镜像 ------------

def store_catalog(user_id: str, movements: list[dict[str, Any]]) -> int:
    now = now_utc()
    count = 0
    with db() as conn:
        for m in movements:
            name = m.get("name") or m.get("action_name")
            if not name:
                continue
            conn.execute(
                """INSERT INTO xunji_action_catalog (id,user_id,xunji_action_id,action_name,xunji_type,raw_json,synced_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(user_id, action_name) DO UPDATE SET xunji_type=excluded.xunji_type, raw_json=excluded.raw_json, synced_at=excluded.synced_at""",
                (new_id(), user_id, _str(m.get("id") or m.get("action_id")), name, m.get("type"),
                 json.dumps(m, ensure_ascii=False), now),
            )
            count += 1
    return count


def store_training_day(user_id: str, datestr: str, raw_day: Any) -> dict[str, int]:
    """覆盖式写入某天: 删除该天旧的 training_day 及级联数据, 再重新写入。"""
    trainings = parse_training_day(raw_day)
    now = now_utc()
    stats = {"trainings": 0, "movements": 0, "sets": 0}
    with db() as conn:
        old = conn.execute("SELECT id FROM xunji_training_days WHERE user_id=? AND datestr=?", (user_id, datestr)).fetchone()
        if old:
            conn.execute("DELETE FROM xunji_training_days WHERE id=?", (old["id"],))  # cascades
        day_id = new_id()
        conn.execute(
            "INSERT INTO xunji_training_days (id,user_id,datestr,source_hash,raw_json,synced_at) VALUES (?,?,?,?,?,?)",
            (day_id, user_id, datestr, None, json.dumps(raw_day, ensure_ascii=False), now),
        )
        for t in trainings:
            training_id = new_id()
            conn.execute(
                """INSERT INTO xunji_trainings (id,user_id,training_day_id,xunji_local_id,datestr,title,note,start_at_raw,end_at_raw,calories,raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (training_id, user_id, day_id, t.get("local_id"), datestr, t.get("title"), t.get("note"),
                 t.get("start"), t.get("end"), t.get("calories"), json.dumps(t.get("raw"), ensure_ascii=False)),
            )
            stats["trainings"] += 1
            for mi, m in enumerate(t["movements"]):
                movement_id = new_id()
                conn.execute(
                    """INSERT INTO xunji_movements (id,user_id,training_id,movement_index,action_name,xunji_action_id,xunji_type,raw_json)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (movement_id, user_id, training_id, mi, m["action_name"], m.get("action_id"), m.get("type"),
                     json.dumps(m.get("raw"), ensure_ascii=False)),
                )
                stats["movements"] += 1
                for si, s in enumerate(m["sets"]):
                    conn.execute(
                        """INSERT INTO xunji_sets (id,user_id,movement_id,set_index,weight,weight_unit,reps,rpe,rest_seconds,done,raw_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (new_id(), user_id, movement_id, si, s.get("weight"), s.get("weight_unit"), s.get("reps"),
                         s.get("rpe"), s.get("rest_seconds"), s.get("done"), json.dumps(s.get("raw"), ensure_ascii=False)),
                    )
                    stats["sets"] += 1
    return stats


def update_sync_state(user_id: str, last_datestr: str | None, initial_full_done: bool | None = None,
                      error: str | None = None) -> None:
    now = now_utc()
    with db() as conn:
        existing = conn.execute("SELECT user_id FROM xunji_sync_state WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            fields = ["last_successful_sync_at=?", "updated_at=?"]
            params: list[Any] = [now, now]
            if last_datestr is not None:
                fields.append("last_synced_datestr=?")
                params.append(last_datestr)
            if initial_full_done is not None:
                fields.append("initial_full_done=?")
                params.append(1 if initial_full_done else 0)
            fields.append("last_error_json=?")
            params.append(json.dumps({"error": error}, ensure_ascii=False) if error else None)
            params.append(user_id)
            conn.execute(f"UPDATE xunji_sync_state SET {', '.join(fields)} WHERE user_id=?", params)
        else:
            conn.execute(
                "INSERT INTO xunji_sync_state (user_id,last_successful_sync_at,last_synced_datestr,initial_full_done,last_error_json,updated_at) VALUES (?,?,?,?,?,?)",
                (user_id, now if not error else None, last_datestr, 1 if initial_full_done else 0,
                 json.dumps({"error": error}, ensure_ascii=False) if error else None, now),
            )
