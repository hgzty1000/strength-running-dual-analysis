"""对外只读 API v1 (ADR 0004): 只读存量, 不触发 LLM, 不写入。

鉴权: Authorization: Bearer srda_...  → 解析出绑定的 user_id (见 api_keys.resolve_api_key)。
所有端点按该 user_id 取数, 天然复用多用户隔离 —— A 的 Key 拿不到 B 的数据。
所有生产者复用 Web 已有函数 (build_context / day_detail / 查库), 不新造业务逻辑。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api_keys import resolve_api_key
from app.config import settings
from app.db import db
from app.repositories import data_coverage, day_detail, list_rest_notes
from app.services.analysis import build_context
from app.services.muscle_mapping import list_mappings

router = APIRouter(prefix="/api/v1")

DEFAULT_CONTEXT_DAYS = 90


def require_api_user(request: Request) -> str:
    """从 Bearer token 解析 user_id; 失败一律 401 JSON (不跳登录页, 这是 API 不是网页)。"""
    if not settings.outbound_api_enabled:
        raise HTTPException(status_code=404, detail="对外 API 未启用")
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer 凭证")
    raw_key = auth[7:].strip()
    user_id = resolve_api_key(raw_key)
    if not user_id:
        raise HTTPException(status_code=401, detail="凭证无效或已吊销")
    return user_id


ApiUser = Depends(require_api_user)


def _validated_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式错误 (YYYY-MM-DD)") from exc


@router.get("/meta")
def api_meta(user_id: str = ApiUser) -> dict[str, Any]:
    """元数据: 数据覆盖 / 同步状态。"""
    return {"ok": True, "data": data_coverage(user_id)}


@router.get("/context")
def api_context(
    user_id: str = ApiUser,
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict[str, Any]:
    """加工上下文 (喂 LLM 的那份)。默认最近 90 天, 可用 start/end 覆盖。纯输入, 不含已生成报告 (报告走 /reports)。"""
    end_s = _validated_date(end) if end else date.today().isoformat()
    start_s = _validated_date(start) if start else (date.fromisoformat(end_s) - timedelta(days=DEFAULT_CONTEXT_DAYS)).isoformat()
    if start_s > end_s:
        raise HTTPException(status_code=400, detail="start 不能晚于 end")
    return {"ok": True, "data": build_context(user_id, start_s, end_s)}


@router.get("/days/{datestr}")
def api_day(datestr: str, user_id: str = ApiUser) -> dict[str, Any]:
    """某日训练明细。"""
    datestr = _validated_date(datestr)
    return {"ok": True, "data": day_detail(user_id, datestr)}


@router.get("/goals/current")
def api_goal_current(user_id: str = ApiUser) -> dict[str, Any]:
    """当前生效的目标配置。"""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM goal_config_versions WHERE user_id=? AND is_current=1", (user_id,)
        ).fetchone()
    return {"ok": True, "data": dict(row) if row else None}


@router.get("/goals/history")
def api_goal_history(user_id: str = ApiUser) -> dict[str, Any]:
    """目标历史版本 (含当前)。"""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM goal_config_versions WHERE user_id=? ORDER BY version_number DESC", (user_id,)
        ).fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.get("/reports")
def api_reports(user_id: str = ApiUser) -> dict[str, Any]:
    """历史分析报告列表 (摘要, 不含全文以控体积)。"""
    with db() as conn:
        rows = conn.execute(
            """SELECT id, covered_start_date, covered_end_date, status, trigger_type,
            model_provider, model_name, created_at
            FROM analysis_reports WHERE user_id=? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.get("/reports/{report_id}")
def api_report_detail(report_id: str, user_id: str = ApiUser) -> dict[str, Any]:
    """某份报告快照全文 (结构化层 + 叙述层)。"""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM analysis_reports WHERE user_id=? AND id=?", (user_id, report_id)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"ok": True, "data": dict(row)}


@router.get("/muscle-map")
def api_muscle_map(user_id: str = ApiUser) -> dict[str, Any]:
    """动作→肌群映射表。"""
    return {"ok": True, "data": list_mappings(user_id)}


@router.get("/rest-notes")
def api_rest_notes(user_id: str = ApiUser) -> dict[str, Any]:
    """休整标注。"""
    return {"ok": True, "data": list_rest_notes(user_id)}
