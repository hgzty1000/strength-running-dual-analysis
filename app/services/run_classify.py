"""跑步类型自动分类 (基于个人基线, 纯规则, 不用 LLM)。

类型: easy(轻松跑) / long(长距离) / tempo(节奏跑) / interval(间歇速度) /
      recovery(恢复跑) / race_test(比赛测试) / mixed_unknown(未知)

原则 (对齐 CONTEXT「个人基线为参照」):
- 用用户自己所有跑步的中位数/分位数作参照, 不套教科书死标准。
- 圈配速波动(CV)是识别间歇的主信号。
- 数据不足时回退 mixed_unknown, 不硬猜。
"""
from __future__ import annotations

import statistics

from app.db import db, now_utc

LABELS = {
    "easy": "轻松跑",
    "long": "长距离",
    "tempo": "节奏跑",
    "interval": "间歇/速度",
    "recovery": "恢复跑",
    "race_test": "比赛/测试",
    "mixed_unknown": "未知",
}


def label(run_type: str | None) -> str:
    return LABELS.get(run_type or "mixed_unknown", "未知")


def _lap_pace_cv(conn, activity_id: str) -> float | None:
    laps = conn.execute(
        "SELECT avg_speed_mps FROM garmin_laps WHERE activity_id=? AND avg_speed_mps IS NOT NULL AND avg_speed_mps>0",
        (activity_id,),
    ).fetchall()
    paces = [1000 / l["avg_speed_mps"] for l in laps]
    if len(paces) < 3:
        return None
    mean = statistics.mean(paces)
    if mean <= 0:
        return None
    return statistics.pstdev(paces) / mean


def classify_user_runs(user_id: str) -> dict:
    """对用户所有跑步重新分类, 更新 running_activity_metrics.run_type。返回统计。"""
    with db() as conn:
        runs = conn.execute(
            """SELECT a.id, a.distance_m, a.timer_seconds,
                      r.avg_hr, r.max_hr, r.avg_pace_sec_per_km, r.avg_speed_mps
               FROM garmin_activities a
               JOIN running_activity_metrics r ON r.activity_id=a.id
               WHERE a.user_id=? AND a.activity_family='running'""",
            (user_id,),
        ).fetchall()
        if not runs:
            return {"total": 0, "classified": 0, "dist": {}}

        # 个人基线 (排除过短的热身/垃圾数据)
        valid = [r for r in runs if (r["distance_m"] or 0) >= 1000]
        dists = [r["distance_m"] / 1000 for r in valid if r["distance_m"]]
        paces = [r["avg_pace_sec_per_km"] for r in valid if r["avg_pace_sec_per_km"]]
        hrs = [r["avg_hr"] for r in valid if r["avg_hr"]]
        med_dist = statistics.median(dists) if dists else 8.0
        med_pace = statistics.median(paces) if paces else 360.0
        # 配速快慢分位 (值越小越快)
        fast_pace = _percentile(paces, 30) if paces else med_pace
        med_hr = statistics.median(hrs) if hrs else 140.0
        max_hr = max(hrs) if hrs else 160.0

        # 预取每个活动的圈配速 CV
        cv_map = {r["id"]: _lap_pace_cv(conn, r["id"]) for r in runs}

        dist_counts: dict[str, int] = {}
        now = now_utc()
        classified = 0
        for r in runs:
            rt = _classify_one(
                distance_km=(r["distance_m"] or 0) / 1000,
                pace=r["avg_pace_sec_per_km"],
                avg_hr=r["avg_hr"],
                max_hr_run=r["max_hr"],
                cv=cv_map.get(r["id"]),
                med_dist=med_dist, med_pace=med_pace, fast_pace=fast_pace,
                med_hr=med_hr, max_hr=max_hr,
            )
            conn.execute(
                "UPDATE running_activity_metrics SET run_type=?, updated_at=? WHERE activity_id=?",
                (rt, now, r["id"]),
            )
            dist_counts[rt] = dist_counts.get(rt, 0) + 1
            classified += 1
    return {"total": len(runs), "classified": classified, "dist": dist_counts}


def _classify_one(distance_km, pace, avg_hr, max_hr_run, cv, med_dist, med_pace, fast_pace, med_hr, max_hr) -> str:
    # 数据太少
    if distance_km < 0.8 and not pace:
        return "mixed_unknown"

    hr_ratio = (avg_hr / max_hr) if (avg_hr and max_hr) else None

    # 1) 间歇/速度: 圈配速波动大 (快慢交替)
    if cv is not None and cv >= 0.35:
        return "interval"

    # 2) 比赛/测试: 心率接近个人最高 + 配速很快 + 波动不大 + 有一定距离
    if (hr_ratio is not None and hr_ratio >= 0.95 and pace and pace <= fast_pace
            and distance_km >= max(3.0, 0.5 * med_dist) and (cv is None or cv < 0.3)):
        return "race_test"

    # 3) 长距离: 距离明显超过个人中位
    if distance_km >= max(16.0, 1.4 * med_dist):
        return "long"

    # 4) 恢复跑: 距离短 + 心率低 + 配速慢
    if (distance_km <= 0.6 * med_dist and avg_hr and avg_hr <= med_hr - 8
            and pace and pace >= med_pace + 20):
        return "recovery"

    # 5) 节奏跑: 配速明显快于中位 + 波动小 + 心率偏高
    if (pace and pace <= fast_pace and (cv is None or cv < 0.25)
            and (hr_ratio is None or hr_ratio >= 0.85)):
        return "tempo"

    # 6) 默认轻松跑
    return "easy"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac
