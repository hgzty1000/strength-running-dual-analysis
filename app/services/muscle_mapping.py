"""动作 → 肌群 映射: AI 补全 + 固化复用。

策略:
- 训记动作目录 (catalog) 若给了 type, 直接用作 primary_group, source_type=xunji_type。
- 缺失 type 的动作进入待补列表, 由 AI 判定一次并固化, source_type=ai_inferred。
- 固化后复用, 不每次现场重判。同一动作分类保持稳定。
- demo 阶段 AI 补全可用规则回退 (关键词), 无 LLM 时也能出结果。
"""
from __future__ import annotations

import json

from app.db import db, new_id, now_utc

# 规则回退: 中文动作名关键词 → 肌群。用于无 LLM Key 时的兜底补全。
KEYWORD_GROUPS: list[tuple[tuple[str, ...], str]] = [
    (("卧推", "推胸", "夹胸", "飞鸟", "俯卧撑", "胸"), "胸"),
    (("引体", "划船", "下拉", "硬拉", "背", "耸肩", "山羊"), "背"),
    (("深蹲", "腿举", "箭步", "弓步", "腿屈伸", "腿弯举", "保加利亚", "臀桥", "臀推", "提踵", "腿", "蹲"), "腿"),
    (("肩", "推举", "侧平举", "前平举", "面拉", "耸"), "肩"),
    (("二头", "弯举", "弯曲"), "二头"),
    (("三头", "臂屈伸", "下压", "臂曲伸"), "三头"),
    (("卷腹", "腹", "平板支撑", "卷", "核心"), "腹"),
    (("小腿", "提踵"), "小腿"),
]


def guess_group(action_name: str, xunji_type: str | None) -> tuple[str, str, float]:
    """返回 (primary_group, source_type, confidence)。"""
    if xunji_type:
        return xunji_type, "xunji_type", 1.0
    for keywords, group in KEYWORD_GROUPS:
        for kw in keywords:
            if kw in action_name:
                return group, "ai_inferred", 0.6
    return "未分类", "ai_inferred", 0.2


def get_mapping(user_id: str, action_name: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM exercise_muscle_mappings WHERE user_id=? AND source_system='xunji' AND source_action_name=?",
            (user_id, action_name),
        ).fetchone()
        return dict(row) if row else None


def upsert_mapping(user_id: str, action_name: str, primary_group: str, source_type: str,
                   confidence: float | None = None, secondary: list[str] | None = None,
                   rationale: str | None = None) -> None:
    now = now_utc()
    with db() as conn:
        conn.execute(
            """INSERT INTO exercise_muscle_mappings
            (id,user_id,source_system,source_action_name,primary_group,secondary_groups_json,source_type,confidence,rationale,created_at,updated_at)
            VALUES (?,?,'xunji',?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, source_system, source_action_name) DO UPDATE SET
              primary_group=excluded.primary_group,
              secondary_groups_json=excluded.secondary_groups_json,
              source_type=excluded.source_type,
              confidence=excluded.confidence,
              rationale=excluded.rationale,
              updated_at=excluded.updated_at""",
            (new_id(), user_id, action_name, primary_group,
             json.dumps(secondary, ensure_ascii=False) if secondary else None,
             source_type, confidence, rationale, now, now),
        )


def ensure_mappings_for_user(user_id: str) -> dict[str, int]:
    """为该用户所有已知动作 (catalog + 已同步动作) 补全映射。返回统计。"""
    stats = {"from_type": 0, "inferred": 0, "existing": 0}
    with db() as conn:
        names: dict[str, str | None] = {}
        for row in conn.execute("SELECT action_name, xunji_type FROM xunji_action_catalog WHERE user_id=?", (user_id,)).fetchall():
            names[row["action_name"]] = row["xunji_type"]
        for row in conn.execute("SELECT DISTINCT action_name, xunji_type FROM xunji_movements WHERE user_id=?", (user_id,)).fetchall():
            names.setdefault(row["action_name"], row["xunji_type"])
        existing = {
            r["source_action_name"]
            for r in conn.execute("SELECT source_action_name FROM exercise_muscle_mappings WHERE user_id=? AND source_system='xunji'", (user_id,)).fetchall()
        }
    for name, xtype in names.items():
        if name in existing:
            stats["existing"] += 1
            continue
        group, source_type, confidence = guess_group(name, xtype)
        upsert_mapping(user_id, name, group, source_type, confidence)
        if source_type == "xunji_type":
            stats["from_type"] += 1
        else:
            stats["inferred"] += 1
    return stats


def pending_count(user_id: str) -> int:
    """低置信度 / 未分类 的动作数, 代表待人工确认。"""
    with db() as conn:
        row = conn.execute(
            "SELECT count(*) c FROM exercise_muscle_mappings WHERE user_id=? AND (primary_group='未分类' OR confidence < 0.5)",
            (user_id,),
        ).fetchone()
        return row["c"]


def list_mappings(user_id: str, only_pending: bool = False) -> list[dict]:
    """列出映射。only_pending=True 只返回未分类/低置信度的。"""
    with db() as conn:
        sql = "SELECT * FROM exercise_muscle_mappings WHERE user_id=? AND source_system='xunji'"
        if only_pending:
            sql += " AND (primary_group='未分类' OR confidence < 0.5)"
        sql += " ORDER BY confidence ASC, source_action_name"
        return [dict(r) for r in conn.execute(sql, (user_id,)).fetchall()]


def pending_actions(user_id: str) -> list[str]:
    """返回实际出现在训练里、且未分类/低置信度的动作名。"""
    with db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT m.action_name
            FROM xunji_movements m
            JOIN exercise_muscle_mappings em
              ON em.user_id=m.user_id AND em.source_system='xunji' AND em.source_action_name=m.action_name
            WHERE m.user_id=? AND (em.primary_group='未分类' OR em.confidence < 0.5)
            ORDER BY m.action_name""",
            (user_id,),
        ).fetchall()
        return [r["action_name"] for r in rows]


def ai_classify_pending(user_id: str, llm_key: str | None) -> dict:
    """用 AI 补全未分类/低置信度动作, 固化进映射表。返回统计。"""
    from app.services.llm import classify_muscles

    names = pending_actions(user_id)
    if not names:
        return {"pending": 0, "classified": 0, "message": "没有待补全的动作"}
    if not llm_key:
        return {"pending": len(names), "classified": 0, "message": "未配置 LLM Key, 无法 AI 补全"}
    result = classify_muscles(names, llm_key)
    if not result:
        return {"pending": len(names), "classified": 0, "message": "AI 分类失败或无返回"}
    classified = 0
    for name in names:
        info = result.get(name)
        if not info:
            continue
        upsert_mapping(user_id, name, info["primary"], "ai_inferred",
                       confidence=info.get("confidence", 0.7), secondary=info.get("secondary"),
                       rationale="AI 补全")
        classified += 1
    return {"pending": len(names), "classified": classified,
            "message": f"AI 补全 {classified}/{len(names)} 个动作"}


def correct_mapping(user_id: str, action_name: str, primary_group: str, secondary: list[str] | None = None) -> None:
    """用户手动订正一个动作的肌群, source_type=user_corrected, confidence=1.0。"""
    upsert_mapping(user_id, action_name, primary_group, "user_corrected", confidence=1.0,
                   secondary=secondary, rationale="用户订正")
