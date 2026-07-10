from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.db import db, new_id, now_utc
from app.services.garmin import activity_unique_key, dumps, store_import_file
from app.security import decrypt_secret
from app.services import muscle_mapping
from app.services import xunji as xunji_svc
from app.services.analysis import analyze, build_context


def import_garmin_zip(user_id: str, zip_path: Path, upload_root: Path, original_filename: str | None = None) -> dict:
    import_id = new_id()
    dest_dir = upload_root / "users" / user_id / "garmin" / "imports" / import_id
    zip_dest, fit_dest, file_hash, size, parsed = store_import_file(zip_path, dest_dir)
    unique_key = activity_unique_key(parsed, file_hash)
    now = now_utc()
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM garmin_activities WHERE user_id = ? AND activity_unique_key = ?",
            (user_id, unique_key),
        ).fetchone()
        conn.execute(
            "INSERT INTO garmin_import_files (id,user_id,original_filename,stored_zip_path,stored_fit_path,file_hash,file_size_bytes,status,error_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (import_id, user_id, original_filename or zip_path.name, str(zip_dest), str(fit_dest), file_hash, size, "duplicate" if existing else "imported", None, now),
        )
        if existing:
            return {"status": "duplicate", "activity_id": existing["id"], "duplicate": True, "field_coverage": parsed.field_coverage}
        activity_id = new_id()
        s = parsed.summary
        conn.execute(
            """INSERT INTO garmin_activities
            (id,user_id,import_file_id,activity_unique_key,fit_start_time,local_date,sport,sub_sport,activity_family,activity_variant,elapsed_seconds,timer_seconds,distance_m,calories,gps_available,lap_count,field_coverage_json,raw_summary_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (activity_id, user_id, import_id, unique_key, s["fit_start_time"], s.get("local_date"), s.get("sport"), s.get("sub_sport"), s["activity_family"], s["activity_variant"], s.get("elapsed_seconds"), s.get("timer_seconds"), s.get("distance_m"), s.get("calories"), 1 if s.get("gps_available") else 0, s.get("lap_count"), dumps(parsed.field_coverage), dumps(s), now),
        )
        for lap in parsed.laps:
            conn.execute(
                """INSERT INTO garmin_laps
                (id,user_id,activity_id,lap_index,start_time,elapsed_seconds,timer_seconds,distance_m,avg_speed_mps,avg_hr,max_hr,avg_cadence,avg_power,raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), user_id, activity_id, lap["lap_index"], lap.get("start_time"), lap.get("elapsed_seconds"), lap.get("timer_seconds"), lap.get("distance_m"), lap.get("avg_speed_mps"), lap.get("avg_hr"), lap.get("max_hr"), lap.get("avg_cadence"), lap.get("avg_power"), dumps(lap.get("raw_json"))),
            )
        if parsed.running_metrics:
            m = parsed.running_metrics
            conn.execute(
                """INSERT INTO running_activity_metrics
                (activity_id,user_id,run_context,run_type,avg_pace_sec_per_km,avg_speed_mps,max_speed_mps,avg_hr,max_hr,avg_cadence,max_cadence,avg_power,max_power,elevation_gain_m,elevation_loss_m,temperature_c,temperature_source,metrics_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (activity_id, user_id, m["run_context"], m["run_type"], m.get("avg_pace_sec_per_km"), m.get("avg_speed_mps"), m.get("max_speed_mps"), m.get("avg_hr"), m.get("max_hr"), m.get("avg_cadence"), m.get("max_cadence"), m.get("avg_power"), m.get("max_power"), m.get("elevation_gain_m"), m.get("elevation_loss_m"), m.get("temperature_c"), m.get("temperature_source", "missing"), dumps(m.get("metrics_json")), now, now),
            )
    # 导入后基于个人基线重新分类跑步类型 (running family)
    if parsed.running_metrics:
        try:
            from app.services.run_classify import classify_user_runs
            classify_user_runs(user_id)
        except Exception:  # noqa: BLE001 - 分类失败不影响导入
            pass
    return {"status": "imported", "activity_id": activity_id, "duplicate": False, "field_coverage": parsed.field_coverage}


def list_garmin_activities(user_id: str, limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT a.*, r.run_context, r.run_type, r.avg_pace_sec_per_km, r.avg_hr, r.max_hr, r.avg_cadence, r.avg_power, r.temperature_source
            FROM garmin_activities a LEFT JOIN running_activity_metrics r ON r.activity_id = a.id
            WHERE a.user_id = ? ORDER BY a.fit_start_time DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        from app.services.run_classify import label as _rt_label
        from app.services.garmin import variant_label as _var_label
        out = []
        for r in rows:
            d = dict(r)
            d["run_type_label"] = _rt_label(d.get("run_type"))
            d["variant_label"] = _var_label(d.get("activity_variant"))
            out.append(d)
        return out


def get_activity(user_id: str, activity_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            """SELECT a.*, r.* FROM garmin_activities a LEFT JOIN running_activity_metrics r ON r.activity_id = a.id
            WHERE a.user_id = ? AND a.id = ?""",
            (user_id, activity_id),
        ).fetchone()
        if not row:
            return None
        laps = conn.execute("SELECT * FROM garmin_laps WHERE user_id = ? AND activity_id = ? ORDER BY lap_index", (user_id, activity_id)).fetchall()
        data = dict(row)
        data["laps"] = [dict(l) for l in laps]
        return data


# ---------------- 凭证读取 ----------------

def get_credential(user_id: str, credential_type: str) -> str | None:
    with db() as conn:
        row = conn.execute(
            "SELECT ciphertext, nonce FROM user_credentials WHERE user_id=? AND credential_type=? AND revoked_at IS NULL",
            (user_id, credential_type),
        ).fetchone()
        if not row:
            return None
    try:
        return decrypt_secret(row["ciphertext"], row["nonce"])
    except Exception:  # noqa: BLE001
        return None


# ---------------- 训记同步 ----------------

def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


import threading

# 同步进度 (内存, 按 user_id)。demo 单机足够; 重启即清空。
_SYNC_PROGRESS: dict[str, dict] = {}
_SYNC_LOCK = threading.Lock()


def get_sync_progress(user_id: str) -> dict:
    with _SYNC_LOCK:
        p = _SYNC_PROGRESS.get(user_id)
        return dict(p) if p else {"state": "idle"}


def _set_progress(user_id: str, **kwargs) -> None:
    with _SYNC_LOCK:
        cur = _SYNC_PROGRESS.get(user_id, {})
        cur.update(kwargs)
        _SYNC_PROGRESS[user_id] = cur


def start_xunji_sync(user_id: str, mode: str = "incremental") -> None:
    """启动后台同步线程。若已有同步在跑, 忽略重复触发。"""
    key = get_credential(user_id, "xunji_key")
    if not key:
        raise ValueError("训记 Key 未配置")
    with _SYNC_LOCK:
        cur = _SYNC_PROGRESS.get(user_id)
        if cur and cur.get("state") == "running":
            return  # 已在同步中
        _SYNC_PROGRESS[user_id] = {"state": "running", "mode": mode, "days_done": 0,
                                   "total_days": 0, "current_date": None, "error": None,
                                   "trainings": 0}
    t = threading.Thread(target=_run_sync, args=(user_id, mode, key), daemon=True)
    t.start()


def _run_sync(user_id: str, mode: str, key: str) -> None:
    try:
        result = sync_xunji(user_id, mode, key=key, progress_user_id=user_id)
        _set_progress(user_id, state="done", error=result.get("error"),
                      trainings=result["stats"]["trainings"], days_done=result["stats"]["days_pulled"])
    except Exception as exc:  # noqa: BLE001
        _set_progress(user_id, state="error", error=f"{type(exc).__name__}: {exc}")


def sync_xunji(user_id: str, mode: str = "incremental", key: str | None = None,
               progress_user_id: str | None = None) -> dict:
    """同步训记数据。mode=full 拉最近 60 天; incremental 拉上次同步日当天→今天。"""
    key = key or get_credential(user_id, "xunji_key")
    if not key:
        raise ValueError("训记 Key 未配置")
    client = xunji_svc.XunjiClient(key)
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()

    with db() as conn:
        state = conn.execute("SELECT * FROM xunji_sync_state WHERE user_id=?", (user_id,)).fetchone()

    if mode == "full" or not state or not state["last_synced_datestr"]:
        start = today - timedelta(days=60)
        initial_full = True
    else:
        start = date.fromisoformat(state["last_synced_datestr"])
        initial_full = bool(state["initial_full_done"])

    # 动作目录: 训记 API 不提供 catalog 端点(标准动作名在 GitHub, 读取不返回肌群 type)。
    # 用本地 catalog.json (name->type 映射) 作为肌群来源, 存入本地目录表。
    catalog_count = xunji_svc.load_local_catalog(user_id)

    all_days = list(_daterange(start, today))
    if progress_user_id:
        _set_progress(progress_user_id, total_days=len(all_days))
    stats = {"trainings": 0, "movements": 0, "sets": 0, "days_pulled": 0, "catalog": catalog_count}
    last_ok = state["last_synced_datestr"] if state else None
    error = None
    for datestr in all_days:
        if progress_user_id:
            _set_progress(progress_user_id, current_date=datestr)
        try:
            raw = client.fetch_day(datestr, include_full=True)
            s = xunji_svc.store_training_day(user_id, datestr, raw)
            for k in ("trainings", "movements", "sets"):
                stats[k] += s[k]
            stats["days_pulled"] += 1
            last_ok = datestr
        except xunji_svc.XunjiError as exc:
            error = str(exc)
            break
        if progress_user_id:
            _set_progress(progress_user_id, days_done=stats["days_pulled"], trainings=stats["trainings"])

    xunji_svc.update_sync_state(user_id, last_ok, initial_full_done=initial_full, error=error)
    muscle_mapping.ensure_mappings_for_user(user_id)
    log_operation(user_id, "xunji_sync", "failed" if error else "success",
                  summary=f"days={stats['days_pulled']} trainings={stats['trainings']}", error=error)
    return {"stats": stats, "error": error}


def resync_xunji_day(user_id: str, datestr: str) -> dict:
    key = get_credential(user_id, "xunji_key")
    if not key:
        raise ValueError("训记 Key 未配置")
    client = xunji_svc.XunjiClient(key)
    raw = client.fetch_day(datestr, include_full=True)
    stats = xunji_svc.store_training_day(user_id, datestr, raw)
    muscle_mapping.ensure_mappings_for_user(user_id)
    log_operation(user_id, "xunji_sync", "success", summary=f"resync {datestr}")
    return stats


# ---------------- 休整标注 ----------------

def add_rest_note(user_id: str, start_date: str, end_date: str, affected_scope: str, note: str) -> str:
    now = now_utc()
    rid = new_id()
    with db() as conn:
        conn.execute(
            "INSERT INTO rest_notes (id,user_id,start_date,end_date,affected_scope,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, user_id, start_date, end_date, affected_scope, note, now, now),
        )
    return rid


def list_rest_notes(user_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM rest_notes WHERE user_id=? ORDER BY start_date DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def delete_rest_note(user_id: str, note_id: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM rest_notes WHERE user_id=? AND id=?", (user_id, note_id))


# ---------------- 数据覆盖 ----------------

def data_coverage(user_id: str) -> dict:
    with db() as conn:
        xrow = conn.execute(
            "SELECT min(datestr) a, max(datestr) b, count(DISTINCT datestr) c FROM xunji_trainings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        grow = conn.execute(
            "SELECT min(local_date) a, max(local_date) b, count(*) c FROM garmin_activities WHERE user_id=? AND activity_family='running'",
            (user_id,),
        ).fetchone()
        goal = conn.execute("SELECT id FROM goal_config_versions WHERE user_id=? AND is_current=1", (user_id,)).fetchone()
        latest = conn.execute("SELECT id FROM analysis_reports WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,)).fetchone()
        missing_temp = conn.execute(
            "SELECT count(*) c FROM running_activity_metrics WHERE user_id=? AND temperature_source='missing'",
            (user_id,),
        ).fetchone()
    return {
        "xunji": {"date_range": [xrow["a"], xrow["b"]], "training_days": xrow["c"],
                  "pending_muscle_mappings": muscle_mapping.pending_count(user_id)},
        "garmin": {"date_range": [grow["a"], grow["b"]], "running_activities": grow["c"],
                   "missing_temperature_count": missing_temp["c"]},
        "goals": {"current_configured": goal is not None},
        "reports": {"latest_report_id": latest["id"] if latest else None},
    }


# ---------------- 分析报告 ----------------

def generate_report(user_id: str, start: str | None, end: str | None, trigger_type: str = "new_analysis",
                    reanalysis_of: str | None = None) -> str:
    now = now_utc()
    with db() as conn:
        goal = conn.execute("SELECT * FROM goal_config_versions WHERE user_id=? AND is_current=1", (user_id,)).fetchone()
        if not goal:
            raise ValueError("请先配置当前目标")
        # 默认区间: 覆盖已有数据的最早~最晚, 否则最近 42 天
        bounds = conn.execute(
            """SELECT min(d) a, max(d) b FROM (
                 SELECT datestr d FROM xunji_trainings WHERE user_id=?
                 UNION ALL SELECT local_date d FROM garmin_activities WHERE user_id=? AND local_date IS NOT NULL
               )""",
            (user_id, user_id),
        ).fetchone()
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
    default_start = (bounds["a"] if bounds and bounds["a"] else (today - timedelta(days=42)).isoformat())
    default_end = (bounds["b"] if bounds and bounds["b"] else today.isoformat())
    start = start or default_start
    end = end or default_end

    ctx = build_context(user_id, start, end)
    llm_key = get_credential(user_id, "llm_key")
    result = analyze(ctx, llm_key)

    report_id = new_id()
    with db() as conn:
        conn.execute(
            """INSERT INTO analysis_reports
            (id,user_id,goal_config_version_id,covered_start_date,covered_end_date,status,trigger_type,reanalysis_of_report_id,
             model_provider,model_name,analysis_context_json,structured_json,narrative_md,confidence_json,data_coverage_json,uncertainties_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (report_id, user_id, goal["id"], start, end, "completed", trigger_type, reanalysis_of,
             "llm" if result["structured"].get("llm_enhanced") else "rule_engine",
             "rule_engine" if not result["structured"].get("llm_enhanced") else "llm",
             json.dumps(ctx, ensure_ascii=False, default=str),
             json.dumps(result["structured"], ensure_ascii=False, default=str),
             result["narrative"],
             json.dumps({"confidence": result["confidence"]}, ensure_ascii=False),
             json.dumps(result["data_coverage"], ensure_ascii=False),
             json.dumps(result["uncertainties"], ensure_ascii=False),
             now),
        )
    log_operation(user_id, "llm_analysis", "success", summary=f"report {report_id} {start}~{end}")
    return report_id


# ---------------- 日历看板 ----------------

def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def month_calendar(user_id: str, year: int, month: int) -> dict:
    """返回某月每天的训练摘要, 供日历看板渲染。

    days: { 'YYYY-MM-DD': {strength: {volume, groups[]}, running: {distance_km, count, variants[]}} }
    """
    start, end = _month_bounds(year, month)
    days: dict[str, dict] = {}

    with db() as conn:
        # 力量: 按天聚合容量, 并收集肌群 (经映射)
        rows = conn.execute(
            """SELECT t.datestr, m.action_name, s.weight, s.reps
            FROM xunji_trainings t
            JOIN xunji_movements m ON m.training_id=t.id
            JOIN xunji_sets s ON s.movement_id=m.id
            WHERE t.user_id=? AND t.datestr>=? AND t.datestr<=?""",
            (user_id, start, end),
        ).fetchall()
        # 预取该用户全部映射, 避免逐条查询
        mapping = {
            r["source_action_name"]: r["primary_group"]
            for r in conn.execute(
                "SELECT source_action_name, primary_group FROM exercise_muscle_mappings WHERE user_id=? AND source_system='xunji'",
                (user_id,),
            ).fetchall()
        }
        for r in rows:
            d = r["datestr"]
            entry = days.setdefault(d, {})
            st = entry.setdefault("strength", {"volume": 0.0, "groups": {}})
            vol = (r["weight"] or 0) * (r["reps"] or 0)
            st["volume"] += vol
            group = mapping.get(r["action_name"], "未分类")
            st["groups"][group] = st["groups"].get(group, 0.0) + vol

        # 跑步: 按天聚合 (含跑步类型)
        gruns = conn.execute(
            """SELECT a.local_date, a.fit_start_time, a.distance_m, a.activity_variant, rm.run_type
            FROM garmin_activities a
            LEFT JOIN running_activity_metrics rm ON rm.activity_id=a.id
            WHERE a.user_id=? AND a.activity_family='running'""",
            (user_id,),
        ).fetchall()
        for r in gruns:
            d = (r["local_date"] or (r["fit_start_time"][:10] if r["fit_start_time"] else None))
            if not d or d < start or d > end:
                continue
            entry = days.setdefault(d, {})
            run = entry.setdefault("running", {"distance_km": 0.0, "count": 0, "variants": {}, "run_types": []})
            run["distance_km"] += (r["distance_m"] or 0) / 1000
            run["count"] += 1
            run["variants"][r["activity_variant"]] = run["variants"].get(r["activity_variant"], 0) + 1
            run["run_types"].append(r["run_type"] or "mixed_unknown")

    # 整理: groups 按容量排序取主要几项; 数字取整
    for d, entry in days.items():
        if "strength" in entry:
            st = entry["strength"]
            st["volume"] = round(st["volume"], 1)
            st["groups"] = [g for g, _ in sorted(st["groups"].items(), key=lambda x: -x[1])]
        if "running" in entry:
            from app.services.run_classify import label as _rt_label
            from app.services.garmin import variant_label as _var_label
            run = entry["running"]
            run["distance_km"] = round(run["distance_km"], 2)
            run["variant_labels"] = [_var_label(v) for v in run["variants"].keys()]
            run["variants"] = list(run["variants"].keys())
            # 该天跑步类型标签 (去重, 保序)
            seen = []
            for rt in run.get("run_types", []):
                lbl = _rt_label(rt)
                if lbl not in seen:
                    seen.append(lbl)
            run["type_labels"] = seen
    return {"year": year, "month": month, "start": start, "end": end, "days": days,
            "month_total_volume": round(sum(
                (d.get("strength", {}).get("volume", 0) or 0) for d in days.values()), 1
            ),
            "month_total_distance": round(sum(
                (d.get("running", {}).get("distance_km", 0) or 0) for d in days.values()), 2),
            "month_total_runs": sum(
                (d.get("running", {}).get("count", 0) or 0) for d in days.values()),
            }


def day_detail(user_id: str, datestr: str) -> dict:
    """某天的完整运动详情: 力量训练(动作/组/容量) + 跑步活动。"""
    result: dict = {"date": datestr, "strength": [], "running": []}
    with db() as conn:
        mapping = {
            r["source_action_name"]: r["primary_group"]
            for r in conn.execute(
                "SELECT source_action_name, primary_group FROM exercise_muscle_mappings WHERE user_id=? AND source_system='xunji'",
                (user_id,),
            ).fetchall()
        }
        trainings = conn.execute(
            "SELECT * FROM xunji_trainings WHERE user_id=? AND datestr=? ORDER BY id",
            (user_id, datestr),
        ).fetchall()
        for t in trainings:
            movements = conn.execute(
                "SELECT * FROM xunji_movements WHERE training_id=? ORDER BY movement_index",
                (t["id"],),
            ).fetchall()
            mlist = []
            for m in movements:
                sets = conn.execute(
                    "SELECT * FROM xunji_sets WHERE movement_id=? ORDER BY set_index",
                    (m["id"],),
                ).fetchall()
                vol = sum((s["weight"] or 0) * (s["reps"] or 0) for s in sets)
                mlist.append({
                    "action_name": m["action_name"],
                    "group": mapping.get(m["action_name"], "未分类"),
                    "sets": [dict(s) for s in sets],
                    "volume": round(vol, 1),
                })
            result["strength"].append({
                "id": t["id"], "title": t["title"], "note": t["note"],
                "movements": mlist,
                "volume": round(sum(mm["volume"] for mm in mlist), 1),
            })
        runs = conn.execute(
            """SELECT a.*, r.run_context, r.run_type, r.avg_pace_sec_per_km, r.avg_hr, r.max_hr, r.avg_cadence, r.avg_power, r.temperature_source
            FROM garmin_activities a LEFT JOIN running_activity_metrics r ON r.activity_id=a.id
            WHERE a.user_id=? AND (a.local_date=? OR substr(a.fit_start_time,1,10)=?)
            ORDER BY a.fit_start_time""",
            (user_id, datestr, datestr),
        ).fetchall()
        from app.services.run_classify import label as _rt_label
        from app.services.garmin import variant_label as _var_label
        run_list = []
        for r in runs:
            d = dict(r)
            d["run_type_label"] = _rt_label(d.get("run_type"))
            d["variant_label"] = _var_label(d.get("activity_variant"))
            run_list.append(d)
        result["running"] = run_list
    return result


# ---------------- 操作日志 ----------------

def log_operation(user_id: str | None, operation_type: str, status: str, summary: str | None = None, error: str | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO operation_logs (id,user_id,operation_type,status,summary,error_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (new_id(), user_id, operation_type, status, summary,
             json.dumps({"error": error}, ensure_ascii=False) if error else None, now_utc()),
        )
