"""端到端回归测试: 用真实 Garmin 样本 + seeded 力量数据跑完整 demo 闭环。

运行: python -m pytest tests/ -v   (或 python tests/test_end_to_end.py 直接跑)
测试使用临时数据库和上传目录, 不污染真实 var/。
不依赖真实训记 API / LLM (力量数据用 seed 脚本, 分析用规则引擎)。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 在导入 app 之前设置环境, 指向临时目录
_TMP = tempfile.mkdtemp(prefix="sx_test_")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "test.db")
os.environ["UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
os.environ["LOG_DIR"] = str(Path(_TMP) / "logs")
os.environ["OWNER_USERNAME"] = "owner"
os.environ["OWNER_PASSWORD"] = "test-pass"
os.environ["ALLOW_PUBLIC_SIGNUP"] = "false"
os.environ["LLM_BASE_URL"] = ""

# 重新加载配置 (config 在导入时快照 env)
import app.config as config_mod  # noqa: E402
config_mod.settings = config_mod.load_settings()
import app.db as db_mod  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import db, init_db  # noqa: E402

GARMIN_DIR = ROOT / "garmin file"

SAMPLE_ZIPS = [
    "2026-06-13_1718_road-run.zip",
    "2026-06-17_1856_track-run.zip",
    "2026-07-07_1924_treadmill-run.zip",
]

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))


def login(client: TestClient) -> None:
    r = client.post("/api/auth/login", data={"username": "owner", "password": "test-pass"}, follow_redirects=False)
    check("login redirects", r.status_code == 303, f"status={r.status_code}")


def run() -> bool:
    init_db()
    client = TestClient(app)

    # 1. 未登录访问受保护页面 -> 重定向登录
    r = client.get("/", follow_redirects=False)
    check("unauth home redirects to login", r.status_code == 303 and r.headers.get("location") == "/login",
          f"status={r.status_code} loc={r.headers.get('location')}")

    # 2. 错误密码
    r = client.post("/api/auth/login", data={"username": "owner", "password": "wrong"}, follow_redirects=False)
    check("wrong password rejected", r.status_code == 400, f"status={r.status_code}")

    # 3. 登录
    login(client)

    # 4. 登录后首页可访问
    r = client.get("/")
    check("home ok after login", r.status_code == 200 and "首页" in r.text, f"status={r.status_code}")

    # 5. 设置用户档案
    r = client.post("/api/settings/profile", data={"height_cm": "175", "weight_kg": "70", "birth_year": "1990", "sex": "male"}, follow_redirects=False)
    check("save profile", r.status_code == 303)

    # 6. 保存凭证 (加密存储 + 掩码)
    r = client.post("/api/settings/credentials/xunji_key", data={"value": "xjllm_testsecret1234567890"}, follow_redirects=False)
    check("save xunji key", r.status_code == 303)
    r = client.get("/settings/credentials")
    check("xunji key masked not full", "xjllm_testsecret1234567890" not in r.text and "xjllm_" in r.text,
          "full key leaked" if "xjllm_testsecret1234567890" in r.text else "")

    # 7. 上传真实 Garmin 样本
    imported = 0
    variants = set()
    for zname in SAMPLE_ZIPS:
        zpath = GARMIN_DIR / zname
        if not zpath.exists():
            # fallback to root copies
            zpath = ROOT / zname
        if not zpath.exists():
            check(f"sample exists {zname}", False, "missing sample")
            continue
        with zpath.open("rb") as f:
            r = client.post("/api/garmin/import", files={"files": (zname, f, "application/zip")}, follow_redirects=False)
        ok = r.status_code == 303
        check(f"import {zname}", ok, f"status={r.status_code}")
        if ok:
            imported += 1
    # verify activities in db
    with db() as conn:
        acts = conn.execute("SELECT activity_variant FROM garmin_activities").fetchall()
        variants = {a["activity_variant"] for a in acts}
    check("garmin activities stored", len(acts) >= 3, f"count={len(acts)}")
    check("running variants detected", {"road_run", "track_run", "treadmill_run"}.issubset(variants),
          f"variants={variants}")

    # 7b. 多文件一次上传 (JSON 明细)
    multi = []
    for zname in ["2026-05-01_2034_road-run.zip", "2026-05-02_1842_treadmill-run.zip"]:
        zp = GARMIN_DIR / zname
        if zp.exists():
            multi.append(("files", (zname, zp.read_bytes(), "application/zip")))
    if multi:
        r = client.post("/api/garmin/import", files=multi, headers={"Accept": "application/json"})
        j = r.json() if r.status_code == 200 else {}
        check("multi-file upload returns per-file results", r.status_code == 200 and len(j.get("results", [])) == len(multi),
              f"status={r.status_code} results={len(j.get('results', []))}")

    # 7c. 跑步类型自动分类 (导入后应已分类; 手动触发再验证)
    r = client.post("/api/garmin/reclassify", headers={"Accept": "application/json"})
    j = r.json() if r.status_code == 200 else {}
    check("run reclassify runs", r.status_code == 200 and j.get("classified", 0) >= 1, f"j={j}")
    with db() as conn:
        types = conn.execute("SELECT DISTINCT run_type FROM running_activity_metrics").fetchall()
        tset = {t["run_type"] for t in types}
    check("run types assigned (not all unknown)", tset and tset != {"mixed_unknown"}, f"types={tset}")

    # 8. 重复上传去重 (以当前实际条数为基准, 再传一次已存在文件, 应不增加)
    with db() as conn:
        before = conn.execute("SELECT count(*) c FROM garmin_activities").fetchone()["c"]
    zpath = GARMIN_DIR / SAMPLE_ZIPS[0]
    if not zpath.exists():
        zpath = ROOT / SAMPLE_ZIPS[0]
    with zpath.open("rb") as f:
        client.post("/api/garmin/import", files={"files": (SAMPLE_ZIPS[0], f, "application/zip")}, follow_redirects=False)
    with db() as conn:
        cnt = conn.execute("SELECT count(*) c FROM garmin_activities").fetchone()["c"]
    check("dedupe keeps single activity per unique key", cnt == before, f"count={cnt} expected={before}")

    # 9. seed 力量数据 (模拟训记同步结果)
    from scripts.seed_strength_from_catalog import seed
    seed_stats = seed("owner")
    check("strength seeded", seed_stats["totals"]["sets"] > 0, f"stats={seed_stats}")
    with db() as conn:
        days = conn.execute("SELECT count(DISTINCT datestr) c FROM xunji_trainings").fetchone()["c"]
        mapped = conn.execute("SELECT count(*) c FROM exercise_muscle_mappings WHERE primary_group != '未分类'").fetchone()["c"]
    check("strength training days present", days >= 5, f"days={days}")
    check("muscle mappings resolved", mapped > 0, f"mapped={mapped}")

    # 10. 配置目标
    r = client.post("/api/goals", data={
        "primary_goal": "running_race_priority",
        "running_goal_text": "备战全马, 目标 3:45",
        "strength_baseline_text": "保持体型和肌肉量",
        "conflict_policy_text": "关键跑课优先于腿训加量",
        "uncertainties_text": "比赛日待定",
        "effective_from": "2026-06-01",
    }, follow_redirects=False)
    check("save goal", r.status_code == 303)

    # 11. 休整标注
    r = client.post("/api/rest-notes", data={
        "start_date": "2026-06-24", "end_date": "2026-06-26", "affected_scope": "legs",
        "note": "膝部不适, 暂停腿训",
    }, follow_redirects=False)
    check("add rest note", r.status_code == 303)

    # 12. 生成分析报告
    r = client.post("/api/analysis/reports", data={"covered_start_date": "", "covered_end_date": ""}, follow_redirects=False)
    check("generate report redirects", r.status_code == 303, f"status={r.status_code}")
    report_loc = r.headers.get("location", "")
    check("report location valid", report_loc.startswith("/reports/"), f"loc={report_loc}")

    # 13. 报告结构化内容
    with db() as conn:
        rep = conn.execute("SELECT * FROM analysis_reports ORDER BY created_at DESC LIMIT 1").fetchone()
    import json as _json
    structured = _json.loads(rep["structured_json"]) if rep else {}
    check("report has core conclusion", bool(structured.get("core_conclusion")))
    check("report has load summary", "strength_total_volume_kg" in structured.get("load_summary", {}))
    check("report load reflects strength", structured["load_summary"]["strength_total_volume_kg"] > 0,
          f"vol={structured.get('load_summary',{}).get('strength_total_volume_kg')}")
    check("report reflects running", structured["load_summary"]["running_total_km"] > 0,
          f"km={structured.get('load_summary',{}).get('running_total_km')}")
    check("report has suggestions", len(structured.get("suggestions", [])) > 0)
    check("rest note referenced in conflicts", any("休整" in c for c in structured.get("double_line_conflicts", [])),
          f"conflicts={structured.get('double_line_conflicts')}")

    # 14. 报告详情页可渲染
    r = client.get(report_loc)
    check("report detail renders", r.status_code == 200 and "核心结论" in r.text, f"status={r.status_code}")

    # 15. 重新分析生成新报告, 旧报告保留
    report_id = report_loc.rsplit("/", 1)[-1]
    r = client.post(f"/api/analysis/reports/{report_id}/reanalyze", follow_redirects=False)
    check("reanalyze redirects", r.status_code == 303)
    with db() as conn:
        report_count = conn.execute("SELECT count(*) c FROM analysis_reports").fetchone()["c"]
    check("reanalyze creates new report (old kept)", report_count == 2, f"count={report_count}")

    # 16. 覆盖页 & 训记页 & 休整页可渲染
    for path, marker in [("/data/coverage", "数据"), ("/data/xunji", "训记"), ("/rest-notes", "休整标注"),
                         ("/goals/current", "目标"), ("/reports", "报告"), ("/analysis/new", "分析")]:
        r = client.get(path)
        check(f"page renders {path}", r.status_code == 200, f"status={r.status_code}")

    # 16b. 首页日历看板
    r = client.get("/")
    check("home calendar renders", r.status_code == 200 and "calendar" in r.text and "回到本月" in r.text,
          f"status={r.status_code}")
    # 指定月份导航
    r = client.get("/?year=2026&month=6")
    check("calendar month nav renders", r.status_code == 200 and "2026 年 6 月" in r.text, f"status={r.status_code}")
    # 力量数据在 6 月, 日历该月应含肌群标签与容量
    check("calendar shows strength cell", ("cal-vol" in r.text), "no strength cell in June")

    # 16b2. 动作肌群管理页 + 手动订正
    r = client.get("/data/muscles")
    check("muscles page renders", r.status_code == 200 and "动作肌群" in r.text, f"status={r.status_code}")
    # 手动订正一个动作
    r = client.post("/api/muscles/correct", data={"action_name": "深蹲", "primary_group": "腿"}, follow_redirects=False)
    check("manual muscle correction", r.status_code == 303)
    with db() as conn:
        m = conn.execute("SELECT source_type, primary_group FROM exercise_muscle_mappings WHERE source_action_name='深蹲'").fetchone()
    check("correction persisted as user_corrected", m and m["source_type"] == "user_corrected" and m["primary_group"] == "腿",
          f"m={dict(m) if m else None}")
    # AI classify endpoint returns JSON gracefully (no LLM configured in test → clear message)
    r = client.post("/api/muscles/ai-classify", headers={"Accept": "application/json"})
    j = r.json() if r.status_code == 200 else {}
    check("ai-classify returns structured result", r.status_code == 200 and "message" in j, f"status={r.status_code} j={j}")

    # 16c. 某天详情页 (seed 里 2026-06-30 有力量)
    r = client.get("/day/2026-06-30")
    check("day detail renders (strength)", r.status_code == 200 and "力量训练" in r.text, f"status={r.status_code}")
    # 有跑步数据的某天 (Garmin 样本 2026-06-13)
    r = client.get("/day/2026-06-13")
    check("day detail renders (running)", r.status_code == 200 and "跑步" in r.text, f"status={r.status_code}")
    # 非法日期
    r = client.get("/day/notadate", follow_redirects=False)
    check("day detail rejects bad date", r.status_code == 400, f"status={r.status_code}")
    # 空白天也能渲染
    r = client.get("/day/2020-01-01")
    check("day detail empty day renders", r.status_code == 200 and "没有训练记录" in r.text, f"status={r.status_code}")

    # 17. 登出
    r = client.post("/api/auth/logout", follow_redirects=False)
    check("logout redirects", r.status_code == 303)
    r = client.get("/", follow_redirects=False)
    check("after logout home redirects", r.status_code == 303)

    # summary
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n==== {passed}/{total} checks passed ====")
    failed = [(n, d) for n, ok, d in RESULTS if not ok]
    for n, d in failed:
        print(f"  FAIL: {n} — {d}")
    return passed == total


def test_end_to_end():
    assert run(), "end-to-end regression failed"


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
