from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import db, init_db, new_id, now_utc
from app.repositories import (
    add_rest_note,
    data_coverage,
    day_detail,
    delete_rest_note,
    generate_report,
    get_activity,
    get_credential,
    get_sync_progress,
    import_garmin_zip,
    list_garmin_activities,
    list_rest_notes,
    month_calendar,
    resync_xunji_day,
    start_xunji_sync,
)
from app.security import decrypt_secret, encrypt_secret, hash_password, mask_secret, new_token, token_hash, verify_password
from app.services.garmin import family_label, variant_label

app = FastAPI(title="力跑双训分析系统 Demo")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["variant_label"] = variant_label
templates.env.globals["family_label"] = family_label
app.mount("/static", StaticFiles(directory="app/static"), name="static")

SESSION_COOKIE = "sx_session"


@app.on_event("startup")
def startup() -> None:
    init_db()


def _wants_html(request: Request) -> bool:
    # 页面路由 (GET, 非 /api/) 是 HTML 页面; 401 时重定向到登录
    return request.method == "GET" and not request.url.path.startswith("/api/")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401 and _wants_html(request):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"ok": False, "error": {"code": exc.status_code, "message": exc.detail}}, status_code=exc.status_code)


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    th = token_hash(token)
    with db() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.session_token_hash=? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.status='active'""",
            (th, now_utc()),
        ).fetchone()
        return dict(row) if row else None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def page(request: Request, name: str, **ctx):
    user = current_user(request)
    if not user and name != "login.html":
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return page(request, "login.html")


@app.post("/api/auth/login")
def login(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? AND status='active'", (username,)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="用户名或密码错误")
        token = new_token()
        sid = new_id()
        now = now_utc()
        expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        conn.execute("INSERT INTO sessions (id,user_id,session_token_hash,created_at,expires_at) VALUES (?,?,?,?,?)", (sid, user["id"], token_hash(token), now, expires))
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, user["id"]))
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite=settings.cookie_same_site, secure=settings.cookie_secure, max_age=14*24*3600)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db() as conn:
            conn.execute("UPDATE sessions SET revoked_at=? WHERE session_token_hash=?", (now_utc(), token_hash(token)))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


import calendar as _calmod


def _build_calendar_grid(year: int, month: int, days_data: dict) -> list[list[dict]]:
    """生成周一起始的日历网格 (list of weeks, each 7 cells)。"""
    cal = _calmod.Calendar(firstweekday=0)  # 0 = Monday
    weeks: list[list[dict]] = []
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            iso = d.isoformat()
            row.append({
                "day": d.day,
                "iso": iso,
                "in_month": d.month == month,
                "data": days_data.get(iso),
            })
        weeks.append(row)
    return weeks


@app.get("/", response_class=HTMLResponse)
def home(request: Request, year: int | None = None, month: int | None = None):
    user = require_user(request)
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
    y = year or today.year
    m = month or today.month
    if m < 1 or m > 12:
        m = today.month
    cal_data = month_calendar(user["id"], y, m)
    weeks = _build_calendar_grid(y, m, cal_data["days"])
    # 上/下月导航
    prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    with db() as conn:
        goal = conn.execute("SELECT * FROM goal_config_versions WHERE user_id=? AND is_current=1", (user["id"],)).fetchone()
        latest_report = conn.execute("SELECT * FROM analysis_reports WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user["id"],)).fetchone()
    return page(request, "home.html", cal=cal_data, weeks=weeks, year=y, month=m,
                prev_y=prev_y, prev_m=prev_m, next_y=next_y, next_m=next_m,
                today_iso=today.isoformat(), goal=goal, latest_report=latest_report)


@app.get("/day/{datestr}", response_class=HTMLResponse)
def day_page(request: Request, datestr: str):
    user = require_user(request)
    import re as _re
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", datestr):
        raise HTTPException(status_code=400, detail="日期格式错误")
    detail = day_detail(user["id"], datestr)
    return page(request, "day.html", detail=detail)


@app.get("/data/garmin", response_class=HTMLResponse)
def garmin_page(request: Request):
    user = require_user(request)
    return page(request, "garmin.html", activities=list_garmin_activities(user["id"], 200))


@app.post("/api/garmin/import")
async def garmin_import(request: Request, files: list[UploadFile] = File(...)):
    user = require_user(request)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    tmp_dir = settings.upload_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    results = []
    counts = {"imported": 0, "duplicate": 0, "failed": 0}
    for file in files:
        name = file.filename or "unknown"
        if not name.lower().endswith(".zip"):
            results.append({"name": name, "status": "failed", "message": "非 zip 文件"})
            counts["failed"] += 1
            continue
        content = await file.read()
        if len(content) > max_bytes:
            results.append({"name": name, "status": "failed", "message": f"超过 {settings.max_upload_mb} MB"})
            counts["failed"] += 1
            continue
        tmp_path = tmp_dir / f"{new_id()}.zip"
        tmp_path.write_bytes(content)
        try:
            result = import_garmin_zip(user["id"], tmp_path, settings.upload_dir, name)
            status = result.get("status", "imported")
            counts[status if status in counts else "imported"] += 1
            results.append({"name": name, "status": status, "message": {"imported": "已导入", "duplicate": "重复,已跳过"}.get(status, status)})
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            results.append({"name": name, "status": "failed", "message": f"解析失败: {type(exc).__name__}"})
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
    # 若是 JSON/fetch 请求, 返回明细; 否则重定向回页面
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"counts": counts, "results": results})
    return RedirectResponse("/data/garmin", status_code=303)


@app.post("/api/garmin/reclassify")
def garmin_reclassify(request: Request):
    from app.services.run_classify import classify_user_runs
    user = require_user(request)
    result = classify_user_runs(user["id"])
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(result)
    return RedirectResponse("/data/garmin", status_code=303)


@app.get("/data/garmin/{activity_id}", response_class=HTMLResponse)
def garmin_detail(request: Request, activity_id: str):
    user = require_user(request)
    activity = get_activity(user["id"], activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    return page(request, "garmin_detail.html", activity=activity)


@app.get("/data/xunji", response_class=HTMLResponse)
def xunji_page(request: Request):
    user = require_user(request)
    with db() as conn:
        state = conn.execute("SELECT * FROM xunji_sync_state WHERE user_id=?", (user["id"],)).fetchone()
        days = conn.execute("SELECT count(DISTINCT datestr) c FROM xunji_trainings WHERE user_id=?", (user["id"],)).fetchone()["c"]
        movements = conn.execute("SELECT count(*) c FROM xunji_movements WHERE user_id=?", (user["id"],)).fetchone()["c"]
        sets = conn.execute("SELECT count(*) c FROM xunji_sets WHERE user_id=?", (user["id"],)).fetchone()["c"]
    key_configured = get_credential(user["id"], "xunji_key") is not None
    return page(request, "xunji.html", state=state, days=days, movements=movements, sets=sets, key_configured=key_configured)


@app.post("/api/xunji/sync")
def xunji_sync_route(request: Request, mode: Annotated[str, Form()]="incremental"):
    user = require_user(request)
    try:
        start_xunji_sync(user["id"], mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/data/xunji", status_code=303)


@app.get("/api/xunji/progress")
def xunji_progress_route(request: Request):
    user = require_user(request)
    return JSONResponse(get_sync_progress(user["id"]))


@app.post("/api/xunji/resync-day")
def xunji_resync_route(request: Request, datestr: Annotated[str, Form()]):
    user = require_user(request)
    try:
        resync_xunji_day(user["id"], datestr)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/data/xunji", status_code=303)


@app.get("/data/coverage", response_class=HTMLResponse)
def coverage_page(request: Request):
    user = require_user(request)
    return page(request, "coverage.html", coverage=data_coverage(user["id"]))


@app.get("/data/muscles", response_class=HTMLResponse)
def muscles_page(request: Request):
    from app.services import muscle_mapping as MM
    user = require_user(request)
    all_maps = MM.list_mappings(user["id"])
    pending = [m for m in all_maps if m["primary_group"] == "未分类" or (m["confidence"] or 0) < 0.5]
    has_llm = get_credential(user["id"], "llm_key") is not None
    groups = ["胸", "背", "腿", "肩", "二头", "三头", "前臂", "臀部", "小腿", "腹", "有氧", "全身"]
    return page(request, "muscles.html", mappings=all_maps, pending=pending, has_llm=has_llm, groups=groups)


@app.post("/api/muscles/ai-classify")
def muscles_ai_classify(request: Request):
    from app.services import muscle_mapping as MM
    user = require_user(request)
    llm_key = get_credential(user["id"], "llm_key")
    result = MM.ai_classify_pending(user["id"], llm_key)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse(result)
    return RedirectResponse("/data/muscles", status_code=303)


@app.post("/api/muscles/correct")
def muscles_correct(request: Request, action_name: Annotated[str, Form()], primary_group: Annotated[str, Form()]):
    from app.services import muscle_mapping as MM
    user = require_user(request)
    MM.correct_mapping(user["id"], action_name, primary_group)
    return RedirectResponse("/data/muscles", status_code=303)


@app.get("/rest-notes", response_class=HTMLResponse)
def rest_notes_page(request: Request):
    user = require_user(request)
    return page(request, "rest_notes.html", notes=list_rest_notes(user["id"]))


@app.post("/api/rest-notes")
def create_rest_note(request: Request, start_date: Annotated[str, Form()], end_date: Annotated[str, Form()],
                     affected_scope: Annotated[str, Form()], note: Annotated[str, Form()]):
    user = require_user(request)
    add_rest_note(user["id"], start_date, end_date, affected_scope, note)
    return RedirectResponse("/rest-notes", status_code=303)


@app.post("/api/rest-notes/{note_id}/delete")
def remove_rest_note(request: Request, note_id: str):
    user = require_user(request)
    delete_rest_note(user["id"], note_id)
    return RedirectResponse("/rest-notes", status_code=303)


@app.get("/settings/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    user = require_user(request)
    with db() as conn:
        profile = conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
    return page(request, "profile.html", profile=profile)


@app.post("/api/settings/profile")
def save_profile(request: Request, height_cm: Annotated[str, Form()]="", weight_kg: Annotated[str, Form()]="", birth_year: Annotated[str, Form()]="", sex: Annotated[str, Form()]=""):
    user = require_user(request)
    import re
    def f(v):
        v = (v or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    def year(v):
        # 容错: 从 "1985"、"1985-08-25"、"1985/08/25" 等中提取 4 位年份
        m = re.search(r"(19|20)\d{2}", v or "")
        return int(m.group(0)) if m else None
    now = now_utc()
    with db() as conn:
        conn.execute("""INSERT INTO user_profiles (user_id,height_cm,weight_kg,birth_year,sex,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET height_cm=excluded.height_cm,weight_kg=excluded.weight_kg,birth_year=excluded.birth_year,sex=excluded.sex,updated_at=excluded.updated_at""",
        (user["id"], f(height_cm), f(weight_kg), year(birth_year), sex or None, now, now))
    return RedirectResponse("/settings/profile", status_code=303)


@app.get("/settings/credentials", response_class=HTMLResponse)
def credentials_page(request: Request):
    user = require_user(request)
    creds = {}
    with db() as conn:
        for row in conn.execute("SELECT * FROM user_credentials WHERE user_id=? AND revoked_at IS NULL", (user["id"],)).fetchall():
            try:
                plain = decrypt_secret(row["ciphertext"], row["nonce"])
                masked = mask_secret(plain)
            except Exception:
                masked = "解密失败"
            creds[row["credential_type"]] = {"configured": True, "masked": masked}
    llm = {"base_url": settings.llm_base_url, "model": settings.llm_model,
           "active": bool(settings.llm_base_url and settings.llm_model)}
    return page(request, "credentials.html", creds=creds, llm=llm)


@app.post("/api/settings/credentials/{credential_type}")
def save_credential(request: Request, credential_type: str, value: Annotated[str, Form()]):
    user = require_user(request)
    if credential_type not in {"xunji_key", "llm_key"}:
        raise HTTPException(status_code=400, detail="不支持的凭证类型")
    ciphertext, nonce, key_version = encrypt_secret(value)
    now = now_utc()
    with db() as conn:
        conn.execute("UPDATE user_credentials SET revoked_at=? WHERE user_id=? AND credential_type=? AND revoked_at IS NULL", (now, user["id"], credential_type))
        conn.execute("INSERT INTO user_credentials (id,user_id,credential_type,ciphertext,nonce,key_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (new_id(), user["id"], credential_type, ciphertext, nonce, key_version, now, now))
    return RedirectResponse("/settings/credentials", status_code=303)


@app.post("/api/settings/credentials/{credential_type}/test")
def test_credential(request: Request, credential_type: str):
    user = require_user(request)
    if credential_type == "llm_key":
        from app.services.llm import test_connection as llm_test
        key = get_credential(user["id"], "llm_key") or ""
        return JSONResponse(llm_test(key))
    if credential_type == "xunji_key":
        from app.services.xunji import XunjiClient
        key = get_credential(user["id"], "xunji_key")
        if not key:
            return JSONResponse({"ok": False, "message": "尚未配置训记 Key"})
        try:
            return JSONResponse(XunjiClient(key).test_connection())
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "message": f"测试失败: {type(exc).__name__}"})
    raise HTTPException(status_code=400, detail="不支持的凭证类型")


@app.get("/goals/current", response_class=HTMLResponse)
def goal_page(request: Request):
    user = require_user(request)
    with db() as conn:
        goal = conn.execute("SELECT * FROM goal_config_versions WHERE user_id=? AND is_current=1", (user["id"],)).fetchone()
        history = conn.execute("SELECT * FROM goal_config_versions WHERE user_id=? ORDER BY version_number DESC", (user["id"],)).fetchall()
    return page(request, "goals.html", goal=goal, history=history)


@app.post("/api/goals")
def save_goal(request: Request, primary_goal: Annotated[str, Form()], running_goal_text: Annotated[str, Form()]="", strength_baseline_text: Annotated[str, Form()]="", conflict_policy_text: Annotated[str, Form()]="", uncertainties_text: Annotated[str, Form()]="", effective_from: Annotated[str, Form()]=""):
    user = require_user(request)
    now = now_utc()
    with db() as conn:
        row = conn.execute("SELECT coalesce(max(version_number),0)+1 v FROM goal_config_versions WHERE user_id=?", (user["id"],)).fetchone()
        version = row["v"]
        conn.execute("UPDATE goal_config_versions SET is_current=0,effective_to=? WHERE user_id=? AND is_current=1", (effective_from or now[:10], user["id"]))
        conn.execute("""INSERT INTO goal_config_versions (id,user_id,version_number,is_current,primary_goal,running_goal_text,strength_baseline_text,conflict_policy_text,uncertainties_text,effective_from,created_by,created_at,confirmed_at,details_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (new_id(), user["id"], version, 1, primary_goal, running_goal_text, strength_baseline_text, conflict_policy_text, uncertainties_text, effective_from or now[:10], "manual", now, now, "{}"))
    return RedirectResponse("/goals/current", status_code=303)


@app.get("/analysis/new", response_class=HTMLResponse)
def analysis_page(request: Request):
    user = require_user(request)
    with db() as conn:
        goal = conn.execute("SELECT * FROM goal_config_versions WHERE user_id=? AND is_current=1", (user["id"],)).fetchone()
        garmin_count = conn.execute("SELECT count(*) c FROM garmin_activities WHERE user_id=?", (user["id"],)).fetchone()["c"]
        strength_days = conn.execute("SELECT count(DISTINCT datestr) c FROM xunji_trainings WHERE user_id=?", (user["id"],)).fetchone()["c"]
        report_count = conn.execute("SELECT count(*) c FROM analysis_reports WHERE user_id=?", (user["id"],)).fetchone()["c"]
        rest_notes = list_rest_notes(user["id"])
    has_llm = get_credential(user["id"], "llm_key") is not None
    return page(request, "analysis.html", goal=goal, garmin_count=garmin_count, strength_days=strength_days,
                report_count=report_count, rest_notes=rest_notes, has_llm=has_llm)


@app.post("/api/analysis/reports")
def create_report(request: Request, covered_start_date: Annotated[str, Form()]="", covered_end_date: Annotated[str, Form()]=""):
    user = require_user(request)
    wants_json = "application/json" in request.headers.get("accept", "")
    try:
        report_id = generate_report(user["id"], covered_start_date or None, covered_end_date or None)
    except ValueError as exc:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        msg = f"生成报告失败: {type(exc).__name__}: {exc}"
        try:
            from app.repositories import log_operation
            log_operation(user["id"], "llm_analysis", "failed", error=msg)
        except Exception:  # noqa: BLE001
            pass
        if wants_json:
            return JSONResponse({"ok": False, "error": msg}, status_code=500)
        raise HTTPException(status_code=500, detail=msg) from exc
    if wants_json:
        return JSONResponse({"ok": True, "redirect": f"/reports/{report_id}"})
    return RedirectResponse(f"/reports/{report_id}", status_code=303)


@app.post("/api/analysis/reports/{report_id}/reanalyze")
def reanalyze_report(request: Request, report_id: str):
    user = require_user(request)
    with db() as conn:
        old = conn.execute("SELECT * FROM analysis_reports WHERE user_id=? AND id=?", (user["id"], report_id)).fetchone()
    if not old:
        raise HTTPException(status_code=404, detail="报告不存在")
    new_report_id = generate_report(user["id"], old["covered_start_date"], old["covered_end_date"],
                                    trigger_type="reanalysis", reanalysis_of=report_id)
    return RedirectResponse(f"/reports/{new_report_id}", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    user = require_user(request)
    with db() as conn:
        reports = conn.execute("SELECT r.*, g.primary_goal, g.version_number AS goal_version FROM analysis_reports r JOIN goal_config_versions g ON g.id=r.goal_config_version_id WHERE r.user_id=? ORDER BY r.created_at DESC", (user["id"],)).fetchall()
    return page(request, "reports.html", reports=reports)


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail(request: Request, report_id: str):
    user = require_user(request)
    with db() as conn:
        report = conn.execute("SELECT r.*, g.primary_goal, g.version_number AS goal_version FROM analysis_reports r JOIN goal_config_versions g ON g.id=r.goal_config_version_id WHERE r.user_id=? AND r.id=?", (user["id"], report_id)).fetchone()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return page(request, "report_detail.html", report=report, structured=json.loads(report["structured_json"]))
