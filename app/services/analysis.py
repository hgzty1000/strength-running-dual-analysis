"""分析上下文构建 + 规则式双线分析引擎 + 可选 LLM。

设计要点 (对齐 CONTEXT / 设计文档):
- 上下文构建层独立, 只按 user_id + 区间取数, 不绑定 web session。AI 分析与未来只读 API 共用。
- 分析只读数据, 不修改运动数据。
- 容量计算: 见 `app/services/xunji.py` compute_set_volume(); 助力式按(体重-助力)reps。
- 个人基线: 与用户自己的滚动基线比, 不套教科书标准。
- 报告声明数据覆盖、前提、置信度、不确定项。
- 无 LLM Key 时用规则引擎兜底, 保证 demo 可跑; 有 LLM 时可增强叙述层。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db import db
from app.services import xunji as xunji_svc
from app.services.muscle_mapping import get_mapping

LOWER_BODY_GROUPS = {"腿", "臀部", "小腿"}

# 热负荷分档阈值 (户外跑气温, 摄氏度)。仅用于方向性解读, 非精确处方。
HEAT_WARM_C = 25.0
HEAT_HOT_C = 30.0
# 室内场景不参与热负荷解读 (跑步机温度是设备/体表读数, 非气象温度)。
INDOOR_RUN_CONTEXTS = {"treadmill"}


def _heat_band(temp_c: float | None) -> str | None:
    """按气温分档: normal / warm / hot。无温度返回 None。"""
    if temp_c is None:
        return None
    if temp_c >= HEAT_HOT_C:
        return "hot"
    if temp_c >= HEAT_WARM_C:
        return "warm"
    return "normal"


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def build_context(user_id: str, start: str, end: str) -> dict[str, Any]:
    """构建分析上下文, 只读。返回结构化字典。"""
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    ctx: dict[str, Any] = {
        "range": {"start": start, "end": end},
        "strength": _strength_summary(user_id, start, end),
        "running": _running_summary(user_id, start_d, end_d),
        "rest_notes": _rest_notes(user_id, start, end),
        "goal": _current_goal(user_id),
        "profile": _profile(user_id),
    }
    ctx["coverage"] = _coverage(ctx)
    return ctx


def _strength_summary(user_id: str, start: str, end: str) -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            """SELECT t.datestr, m.action_name, s.weight, s.reps, s.rpe, s.done
            FROM xunji_trainings t
            JOIN xunji_movements m ON m.training_id=t.id
            JOIN xunji_sets s ON s.movement_id=m.id
            WHERE t.user_id=? AND t.datestr>=? AND t.datestr<=? AND s.done=1""",
            (user_id, start[:10], end[:10]),
        ).fetchall()
    group_volume: dict[str, float] = {}
    group_sets: dict[str, int] = {}
    training_days: set[str] = set()
    total_volume = 0.0
    unmapped: set[str] = set()
    # 取用户体重，助力式动作需要
    try:
        with db() as conn2:
            bw = conn2.execute(
                "SELECT weight_kg FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            body_weight = bw["weight_kg"] if bw else None
    except Exception:
        body_weight = None
    for r in rows:
        training_days.add(r["datestr"])
        mapping = get_mapping(user_id, r["action_name"])
        group = mapping["primary_group"] if mapping else "未分类"
        if group == "未分类":
            unmapped.add(r["action_name"])
        volume = xunji_svc.compute_set_volume(
            r["weight"], r["reps"], r["action_name"], body_weight
        )
        group_volume[group] = group_volume.get(group, 0.0) + volume
        group_sets[group] = group_sets.get(group, 0) + 1
        total_volume += volume
    push = group_volume.get("胸", 0) + group_volume.get("肩", 0) + group_volume.get("三头", 0)
    pull = group_volume.get("背", 0) + group_volume.get("二头", 0)
    lower = sum(group_volume.get(g, 0) for g in LOWER_BODY_GROUPS)
    return {
        "training_days": sorted(training_days),
        "training_day_count": len(training_days),
        "total_volume_kg": round(total_volume, 1),
        "group_volume": {k: round(v, 1) for k, v in sorted(group_volume.items(), key=lambda x: -x[1])},
        "group_sets": group_sets,
        "push_volume": round(push, 1),
        "pull_volume": round(pull, 1),
        "lower_volume": round(lower, 1),
        "unmapped_actions": sorted(unmapped),
    }


def _running_summary(user_id: str, start_d: date | None, end_d: date | None) -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            """SELECT a.local_date, a.fit_start_time, a.distance_m, a.timer_seconds, a.activity_variant,
                      r.avg_hr, r.max_hr, r.avg_power, r.run_context, r.run_type, r.temperature_c, r.temperature_source, r.avg_pace_sec_per_km
            FROM garmin_activities a
            LEFT JOIN running_activity_metrics r ON r.activity_id=a.id
            WHERE a.user_id=? AND a.activity_family='running'
            ORDER BY a.fit_start_time""",
            (user_id,),
        ).fetchall()
    from app.services.run_classify import label as _rt_label
    runs = []
    total_km = 0.0
    total_seconds = 0.0
    variant_count: dict[str, int] = {}
    type_count: dict[str, int] = {}
    outdoor_missing_temp = 0  # 仅户外跑缺气温才算缺口 (室内跑无所谓气象温度)
    for r in rows:
        d = _parse_date(r["local_date"] or r["fit_start_time"])
        if start_d and d and d < start_d:
            continue
        if end_d and d and d > end_d:
            continue
        km = (r["distance_m"] or 0) / 1000
        total_km += km
        total_seconds += r["timer_seconds"] or 0
        variant_count[r["activity_variant"]] = variant_count.get(r["activity_variant"], 0) + 1
        rt_label = _rt_label(r["run_type"])
        type_count[rt_label] = type_count.get(rt_label, 0) + 1
        context = r["run_context"] or "unknown"
        is_indoor = context in INDOOR_RUN_CONTEXTS
        temp_c = r["temperature_c"] if (r["temperature_source"] or "missing") != "missing" else None
        if not is_indoor and temp_c is None:
            outdoor_missing_temp += 1
        runs.append({
            "date": r["local_date"] or (r["fit_start_time"][:10] if r["fit_start_time"] else None),
            "variant": r["activity_variant"],
            "run_context": context,
            "run_type": rt_label,
            "distance_km": round(km, 2),
            "avg_hr": r["avg_hr"],
            "avg_power": r["avg_power"],
            "pace_sec_per_km": r["avg_pace_sec_per_km"],
            "temperature_c": round(temp_c, 1) if temp_c is not None else None,
            "indoor": is_indoor,
        })
    hrs = [r["avg_hr"] for r in runs if r["avg_hr"]]
    return {
        "run_count": len(runs),
        "total_km": round(total_km, 2),
        "total_hours": round(total_seconds / 3600, 2),
        "variant_count": variant_count,
        "type_count": type_count,
        "avg_hr_overall": round(sum(hrs) / len(hrs), 1) if hrs else None,
        "missing_temperature_count": outdoor_missing_temp,
        "heat": _heat_summary(runs),
        "runs": runs,
    }


def _heat_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """户外跑热负荷聚合。室内跑不参与。用于方向性心率解读, 非精确处方。

    返回: 户外有温样本数、各热档次数、高温(warm+hot)跑均温与均心率、
    以及高温跑相对全部户外跑的心率差 (正值表示高温跑心率更高)。
    """
    outdoor = [r for r in runs if not r["indoor"] and r["temperature_c"] is not None]
    band_count: dict[str, int] = {"normal": 0, "warm": 0, "hot": 0}
    for r in outdoor:
        band = _heat_band(r["temperature_c"])
        if band:
            band_count[band] += 1
    hot_runs = [r for r in outdoor if _heat_band(r["temperature_c"]) in ("warm", "hot")]
    hot_hr = [r["avg_hr"] for r in hot_runs if r["avg_hr"]]
    outdoor_hr = [r["avg_hr"] for r in outdoor if r["avg_hr"]]
    hot_avg_hr = round(sum(hot_hr) / len(hot_hr), 1) if hot_hr else None
    outdoor_avg_hr = round(sum(outdoor_hr) / len(outdoor_hr), 1) if outdoor_hr else None
    hot_avg_temp = round(sum(r["temperature_c"] for r in hot_runs) / len(hot_runs), 1) if hot_runs else None
    hr_delta = round(hot_avg_hr - outdoor_avg_hr, 1) if (hot_avg_hr is not None and outdoor_avg_hr is not None) else None
    return {
        "outdoor_with_temp_count": len(outdoor),
        "band_count": band_count,
        "hot_run_count": len(hot_runs),
        "hot_avg_temp_c": hot_avg_temp,
        "hot_avg_hr": hot_avg_hr,
        "outdoor_avg_hr": outdoor_avg_hr,
        "hot_vs_outdoor_hr_delta": hr_delta,
    }


def _rest_notes(user_id: str, start: str, end: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM rest_notes WHERE user_id=? AND NOT (end_date < ? OR start_date > ?) ORDER BY start_date",
            (user_id, start[:10], end[:10]),
        ).fetchall()
        return [{"start": r["start_date"], "end": r["end_date"], "scope": r["affected_scope"], "note": r["note"]} for r in rows]


def _current_goal(user_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM goal_config_versions WHERE user_id=? AND is_current=1", (user_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"], "version": row["version_number"], "primary_goal": row["primary_goal"],
            "running_goal_text": row["running_goal_text"], "strength_baseline_text": row["strength_baseline_text"],
            "conflict_policy_text": row["conflict_policy_text"], "uncertainties_text": row["uncertainties_text"],
        }


def _profile(user_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        return {"height_cm": d.get("height_cm"), "weight_kg": d.get("weight_kg"), "birth_year": d.get("birth_year"), "sex": d.get("sex")}


def _coverage(ctx: dict[str, Any]) -> dict[str, Any]:
    strength = ctx["strength"]
    running = ctx["running"]
    warnings = []
    if strength["training_day_count"] == 0:
        warnings.append("区间内无力量训练数据, 双线判断以跑步为主, 置信度下降")
    if running["run_count"] == 0:
        warnings.append("区间内无跑步数据, 双线冲突判断置信度下降")
    if strength["unmapped_actions"]:
        warnings.append(f"有 {len(strength['unmapped_actions'])} 个动作未映射肌群, 力量分组可能不完整")
    if running["missing_temperature_count"]:
        warnings.append(f"{running['missing_temperature_count']} 次户外跑缺气温, 该部分高温影响无法判断")
    if not ctx["goal"]:
        warnings.append("未配置当前目标, 无法按判断尺给出取舍")
    return {
        "strength_available": strength["training_day_count"] > 0,
        "running_available": running["run_count"] > 0,
        "goal_available": ctx["goal"] is not None,
        "warnings": warnings,
    }


# ---------------- 规则式双线分析引擎 ----------------

def analyze_rule_based(ctx: dict[str, Any]) -> dict[str, Any]:
    strength = ctx["strength"]
    running = ctx["running"]
    goal = ctx["goal"]
    rest_notes = ctx["rest_notes"]
    primary_goal = goal["primary_goal"] if goal else "balanced"

    risks: list[str] = []
    suggestions: list[str] = []
    conflicts: list[str] = []

    # 下肢负荷叠加: 跑步 + 腿训
    lower_volume = strength["lower_volume"]
    run_km = running["total_km"]
    run_days = {r["date"] for r in running["runs"] if r["date"]}
    strength_days = set(strength["training_days"])
    overlap_days = run_days & strength_days

    if run_km > 0 and lower_volume > 0 and overlap_days:
        conflicts.append(f"有 {len(overlap_days)} 天同时安排了跑步和力量训练, 需关注下肢恢复窗口")

    # 推拉平衡
    push = strength["push_volume"]
    pull = strength["pull_volume"]
    if push > 0 and pull > 0:
        ratio = push / pull if pull else 0
        if ratio > 1.8:
            risks.append(f"推类容量明显高于拉类 (推/拉≈{ratio:.1f}), 存在推拉失衡风险")
            suggestions.append("下次上肢训练优先补背部/拉类容量")
        elif ratio < 0.55:
            risks.append(f"拉类容量明显高于推类 (推/拉≈{ratio:.1f})")
            suggestions.append("适当补充推类容量以平衡")

    # 跑量突增 (与区间日均粗比)
    if running["run_count"] >= 3 and run_km > 0:
        suggestions.append(f"区间跑量约 {run_km:.1f} km / {running['run_count']} 次, 关注跑量爬升是否过快")

    # 热负荷—心率解读 (仅户外跑; 带前提, 只做方向性归因, 不改写心率值)
    heat = running.get("heat", {})
    if heat.get("hot_run_count", 0) > 0 and heat.get("hot_vs_outdoor_hr_delta") is not None:
        delta = heat["hot_vs_outdoor_hr_delta"]
        if delta >= 3:
            risks.append(
                f"{heat['hot_run_count']} 次高温户外跑(均温 {heat['hot_avg_temp_c']}°C)平均心率比"
                f"其他户外跑高约 {delta:.0f} bpm, 可能是热负荷推高心率而非强度上升, 评估强度时建议校正"
            )

    # 目标导向取舍
    if primary_goal == "running_race_priority":
        suggestions.append("当前跑步比赛优先: 关键跑课优先, 腿训以维持为主, 不建议加量")
        if lower_volume > 0 and overlap_days:
            suggestions.append("关键跑课前 24-48 小时避免高容量腿训")
    elif primary_goal == "strength_physique_priority":
        suggestions.append("当前力量/体型优先: 控制跑量避免挤占下肢恢复, 保证蛋白与总热量")
    elif primary_goal == "recovery_priority":
        suggestions.append("当前恢复优先: 缺训不按问题判断, 以低风险恢复为主")
    elif primary_goal == "fat_loss_priority":
        suggestions.append("当前减脂优先: 保持热量赤字但不过大, 优先保留力量维持肌肉")
    else:
        suggestions.append("当前双线平衡: 两边都不建议突然加量, 关注冲突与恢复窗口")

    # 休整标注解释
    if rest_notes:
        for rn in rest_notes:
            conflicts.append(f"休整标注 {rn['start']}~{rn['end']} ({rn['scope']}): 相关缺训已解释, 不按训练懈怠判断")

    # 未分类提示
    if strength["unmapped_actions"]:
        risks.append(f"{len(strength['unmapped_actions'])} 个动作未映射肌群, 力量分组可能偏差")

    # 核心结论
    parts = []
    if strength["training_day_count"]:
        parts.append(f"力量 {strength['training_day_count']} 天/总容量 {strength['total_volume_kg']:.0f}kg")
    if running["run_count"]:
        parts.append(f"跑步 {running['run_count']} 次/{run_km:.1f}km")
    core = "本区间 " + ("; ".join(parts) if parts else "数据不足") + "。"
    if conflicts:
        core += " 主要关注点: " + conflicts[0]
    else:
        core += " 未见明显双线冲突。"

    # 置信度
    coverage = ctx["coverage"]
    confidence = "high"
    if coverage["warnings"]:
        confidence = "medium"
    if not coverage["strength_available"] or not coverage["running_available"]:
        confidence = "low"

    structured = {
        "core_conclusion": core,
        "premise": f"按目标『{_goal_label(primary_goal)}』判断; 容量优先, 以个人区间数据为参照。",
        "load_summary": {
            "strength_total_volume_kg": strength["total_volume_kg"],
            "strength_group_volume": strength["group_volume"],
            "push_pull": {"push": push, "pull": pull},
            "lower_volume": lower_volume,
            "running_total_km": run_km,
            "running_count": running["run_count"],
            "running_variants": running["variant_count"],
            "heat": running.get("heat", {}),
        },
        "double_line_conflicts": conflicts or ["未见明显双线排布冲突"],
        "risks": risks or ["未见突出风险"],
        "suggestions": suggestions,
        "confidence": confidence,
        "uncertainties": coverage["warnings"],
    }
    narrative = _build_narrative(ctx, structured)
    return {"structured": structured, "narrative": narrative, "confidence": confidence,
            "data_coverage": coverage, "uncertainties": coverage["warnings"]}


def _goal_label(g: str) -> str:
    return {
        "balanced": "双线平衡", "running_race_priority": "跑步比赛/成绩优先",
        "strength_physique_priority": "力量/体型优先", "recovery_priority": "恢复优先",
        "fat_loss_priority": "减脂/体重控制优先", "custom": "自定义",
    }.get(g, g)


def _build_narrative(ctx: dict[str, Any], structured: dict[str, Any]) -> str:
    from app.services.garmin import variant_label
    r = ctx["range"]
    s = ctx["strength"]
    run = ctx["running"]
    variant_text = ", ".join(
        f"{variant_label(k)} {v}次" for k, v in run.get("variant_count", {}).items()
    ) or "无"
    lines = [
        f"本次分析覆盖 {r['start']} 至 {r['end']}。",
        structured["premise"],
        f"力量方面, 记录 {s['training_day_count']} 个训练日, 总容量约 {s['total_volume_kg']:.0f} kg;"
        f"主要肌群容量: {', '.join(f'{k} {v:.0f}kg' for k, v in list(s['group_volume'].items())[:4]) or '无'}。",
        f"跑步方面, 共 {run['run_count']} 次, 合计约 {run['total_km']:.1f} km, "
        f"场景分布: {variant_text}。",
        structured["core_conclusion"],
    ]
    if structured["suggestions"]:
        lines.append("建议: " + "; ".join(structured["suggestions"]))
    if structured["uncertainties"]:
        lines.append("不确定项: " + "; ".join(structured["uncertainties"]))
    return "\n\n".join(lines)


def analyze(ctx: dict[str, Any], llm_key: str | None = None) -> dict[str, Any]:
    """入口: 有 LLM Key 时尝试 LLM 增强, 失败或无 Key 时用规则引擎。"""
    base = analyze_rule_based(ctx)
    if llm_key and __import__("app.config", fromlist=["settings"]).settings.llm_base_url:
        try:
            from app.services.llm import enhance_narrative
            enhanced = enhance_narrative(ctx, base["structured"], llm_key)
            if enhanced:
                base["narrative"] = enhanced
                base["structured"]["llm_enhanced"] = True
        except Exception:  # noqa: BLE001 - LLM 失败不影响报告
            base["structured"]["llm_enhanced"] = False
    return base
