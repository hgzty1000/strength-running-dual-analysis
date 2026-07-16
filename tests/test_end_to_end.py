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
os.environ["OUTBOUND_API_ENABLED"] = "true"

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
    r = client.get("/day/2026-06-30/share", follow_redirects=False)
    check("unauth share redirects to login", r.status_code == 303 and r.headers.get("location") == "/login",
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
    # 6b. 凭证页口径: 能解密的凭证标记「可用」(与 has_llm 一致)
    check("credential shows usable badge", "可用" in r.text, "no usable badge on credentials page")

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

    # 7d. 手工标注气温 (数据源无温度时的补充路径)
    with db() as conn:
        road = conn.execute(
            "SELECT id FROM garmin_activities WHERE activity_variant='road_run' LIMIT 1"
        ).fetchone()
        tread = conn.execute(
            "SELECT id FROM garmin_activities WHERE activity_variant='treadmill_run' LIMIT 1"
        ).fetchone()
    road_id = road["id"] if road else None
    check("has road-run activity for temp test", bool(road_id), "no road_run activity")
    if road_id:
        # 详情页含手工标注表单
        r = client.get(f"/data/garmin/{road_id}")
        check("activity detail has temp form", r.status_code == 200 and "手工标注气温" in r.text,
              f"status={r.status_code}")
        # 设温 → 落库 manual + 值
        r = client.post(f"/api/garmin/{road_id}/temperature", data={"temperature_c": "28.5"},
                        follow_redirects=False)
        check("set temperature redirects", r.status_code == 303, f"status={r.status_code}")
        with db() as conn:
            m = conn.execute("SELECT temperature_c, temperature_source FROM running_activity_metrics WHERE activity_id=?",
                             (road_id,)).fetchone()
        check("temperature saved as manual", m and abs((m["temperature_c"] or 0) - 28.5) < 0.01 and m["temperature_source"] == "manual",
              f"m={dict(m) if m else None}")
        # 越界拒绝
        r = client.post(f"/api/garmin/{road_id}/temperature", data={"temperature_c": "999"},
                        follow_redirects=False)
        check("temperature out-of-range rejected", r.status_code == 400, f"status={r.status_code}")
        # 非数字拒绝
        r = client.post(f"/api/garmin/{road_id}/temperature", data={"temperature_c": "abc"},
                        follow_redirects=False)
        check("temperature non-numeric rejected", r.status_code == 400, f"status={r.status_code}")
        # 清空 → 回 missing
        r = client.post(f"/api/garmin/{road_id}/temperature", data={"temperature_c": ""},
                        follow_redirects=False)
        check("clear temperature redirects", r.status_code == 303, f"status={r.status_code}")
        with db() as conn:
            m = conn.execute("SELECT temperature_c, temperature_source FROM running_activity_metrics WHERE activity_id=?",
                             (road_id,)).fetchone()
        check("temperature cleared to missing", m and m["temperature_c"] is None and m["temperature_source"] == "missing",
              f"m={dict(m) if m else None}")
    if tread:
        # 跑步机详情页注明不参与热解读
        r = client.get(f"/data/garmin/{tread['id']}")
        check("treadmill detail notes indoor exclusion", r.status_code == 200 and "室内跑" in r.text,
              f"status={r.status_code}")

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

    # 10b. AI 目标澄清 (测试环境无 LLM → 验证优雅降级 + 落库标记)
    r = client.get("/goals/clarify")
    check("goal clarify page renders", r.status_code == 200 and "AI 目标澄清" in r.text, f"status={r.status_code}")
    # 无 LLM Key: 页面应给出配置引导, 而非报错
    check("clarify page shows no-llm notice", "尚未配置 LLM Key" in r.text or "未配置 LLM" in r.text, "no notice")
    # message 端点无 LLM 时应返回结构化提示 (200 + ok:false)
    r = client.post("/api/goals/clarify/message", json={"messages": [{"role": "user", "content": "想跑半马"}]})
    j = r.json() if r.status_code == 200 else {}
    check("clarify message degrades gracefully", r.status_code == 200 and j.get("ok") is False and "message" in j,
          f"status={r.status_code} j={j}")
    # draft 端点同样优雅降级
    r = client.post("/api/goals/clarify/draft", json={"messages": [{"role": "user", "content": "想跑半马"}]})
    j = r.json() if r.status_code == 200 else {}
    check("clarify draft degrades gracefully", r.status_code == 200 and j.get("ok") is False,
          f"status={r.status_code} j={j}")
    # message 端点缺用户消息 → 400
    r = client.post("/api/goals/clarify/message", json={"messages": []})
    check("clarify message rejects empty", r.status_code == 400, f"status={r.status_code}")
    # 经 AI 澄清确认的目标: created_by 应标记 ai_clarification, 且成为当前版本
    r = client.post("/api/goals", data={
        "primary_goal": "balanced",
        "running_goal_text": "秋季半马 sub-1:50",
        "strength_baseline_text": "深蹲不掉太多",
        "conflict_policy_text": "比赛周减力量",
        "uncertainties_text": "",
        "effective_from": "2026-07-01",
        "created_by": "ai_clarification",
    }, follow_redirects=False)
    check("save ai-clarified goal", r.status_code == 303)
    with db() as conn:
        g = conn.execute("SELECT created_by, is_current, primary_goal FROM goal_config_versions WHERE user_id=(SELECT id FROM users WHERE username='owner') ORDER BY version_number DESC LIMIT 1").fetchone()
    check("ai goal marked created_by", g and g["created_by"] == "ai_clarification" and g["is_current"] == 1,
          f"g={dict(g) if g else None}")
    # created_by 白名单: 非法值应被归为 manual
    r = client.post("/api/goals", data={"primary_goal": "custom", "created_by": "hacker"}, follow_redirects=False)
    with db() as conn:
        g = conn.execute("SELECT created_by FROM goal_config_versions WHERE user_id=(SELECT id FROM users WHERE username='owner') ORDER BY version_number DESC LIMIT 1").fetchone()
    check("created_by whitelist enforced", g and g["created_by"] == "manual", f"g={dict(g) if g else None}")
    # 复位当前目标为跑步比赛优先 (后续报告测试依赖), created_by 默认 manual
    r = client.post("/api/goals", data={
        "primary_goal": "running_race_priority",
        "running_goal_text": "备战全马, 目标 3:45",
        "strength_baseline_text": "保持体型和肌肉量",
        "conflict_policy_text": "关键跑课优先于腿训加量",
        "uncertainties_text": "比赛日待定",
        "effective_from": "2026-06-01",
    }, follow_redirects=False)
    check("restore manual goal", r.status_code == 303)

    # 10c. 历史目标查看: 可展开列表 + 完整字段 + 复用按钮 + 中文标签
    r = client.get("/goals/current")
    check("goal history expandable", r.status_code == 200 and "goal-version" in r.text and "以此版本填入当前目标" in r.text,
          f"status={r.status_code}")
    check("goal history shows full fields", "力量底线" in r.text and "冲突取舍" in r.text, "detail fields missing")
    # 历史区主目标应经 goal_label 显示中文 (summary 含 gv-head + 中文标签)
    check("goal label localized in history", "gv-head" in r.text and "跑步比赛/成绩优先" in r.text,
          "primary_goal not localized in history")

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
    # 14b. 叙述层经 markdown 渲染 (md-body 容器)
    check("report narrative uses md renderer", "md-body" in r.text, "no md-body container")

    # 14c. 轻量 markdown 渲染器: 基础语法 + XSS 转义
    from app.markdown_lite import render as _md
    check("md bold", "<strong>过量</strong>" in _md("这周**过量**了"))
    check("md heading", "<h5>小结</h5>" in _md("## 小结"))
    check("md list", _md("- a\n- b") == "<ul><li>a</li><li>b</li></ul>")
    check("md ordered list", _md("1. 先减量\n2. 再观察") == "<ol><li>先减量</li><li>再观察</li></ol>")
    check("md inline code", "<code>RPE</code>" in _md("用 `RPE` 衡量"))
    _x = _md("<script>alert(1)</script>")
    check("md escapes script", "<script>" not in _x and "&lt;script&gt;" in _x, f"out={_x}")
    _x2 = _md("<img src=x onerror=alert(1)>")
    check("md escapes html attrs", "<img" not in _x2 and "onerror=alert" in _x2 and "&lt;img" in _x2, f"out={_x2}")
    _x3 = _md("[点我](javascript:alert(1))")
    check("md no link injection", "<a " not in _x3 and "javascript:" in _x3, f"out={_x3}")
    check("md empty safe", _md("") == "" and _md(None) == "")

    # 14d. 热负荷—心率解读 (合成数据单测纯函数, 确定性)
    from app.services.analysis import _heat_band, _heat_summary
    check("heat band normal", _heat_band(20.0) == "normal")
    check("heat band warm", _heat_band(27.0) == "warm")
    check("heat band hot", _heat_band(32.0) == "hot")
    check("heat band none", _heat_band(None) is None)
    # 合成: 2 次凉爽户外 (心率 150), 2 次高温户外 (心率 165), 1 次室内高温 (不计)
    synth = [
        {"indoor": False, "temperature_c": 18.0, "avg_hr": 150},
        {"indoor": False, "temperature_c": 20.0, "avg_hr": 150},
        {"indoor": False, "temperature_c": 31.0, "avg_hr": 165},
        {"indoor": False, "temperature_c": 33.0, "avg_hr": 165},
        {"indoor": True, "temperature_c": 30.0, "avg_hr": 170},
    ]
    hs = _heat_summary(synth)
    check("heat excludes indoor", hs["outdoor_with_temp_count"] == 4, f"count={hs['outdoor_with_temp_count']}")
    check("heat hot count", hs["hot_run_count"] == 2, f"hot={hs['hot_run_count']}")
    check("heat hr delta positive", hs["hot_vs_outdoor_hr_delta"] is not None and hs["hot_vs_outdoor_hr_delta"] > 0,
          f"delta={hs['hot_vs_outdoor_hr_delta']}")
    check("heat band count", hs["band_count"]["hot"] == 2 and hs["band_count"]["normal"] == 2,
          f"bands={hs['band_count']}")
    # 全室内: 不产生热解读样本
    hs_indoor = _heat_summary([{"indoor": True, "temperature_c": 28.0, "avg_hr": 160}])
    check("heat all-indoor empty", hs_indoor["outdoor_with_temp_count"] == 0 and hs_indoor["hot_run_count"] == 0,
          f"hs={hs_indoor}")
    # 报告 structured 带 heat 段
    check("report load_summary has heat", "heat" in structured.get("load_summary", {}),
          f"keys={list(structured.get('load_summary',{}).keys())}")

    # 14d. 报告浅追问
    report_id = report_loc.rsplit("/", 1)[-1]
    # 报告详情页应含追问区; 无 LLM 时给引导, 不报错
    check("report has followup section", "就本报告追问" in r.text, "no followup section")
    check("followup shows no-llm notice", "无法追问" in r.text, "no no-llm notice on followup")
    # 追问端点无 LLM 时优雅降级 (200 + ok:false), 且不落库
    r2 = client.post(f"/api/reports/{report_id}/followup", json={"question": "为什么说力量偏多?"})
    j2 = r2.json() if r2.status_code == 200 else {}
    check("followup degrades gracefully", r2.status_code == 200 and j2.get("ok") is False, f"status={r2.status_code} j={j2}")
    with db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username='owner'").fetchone()["id"]
        fu_after = conn.execute("SELECT count(*) c FROM report_followups WHERE report_id=?", (report_id,)).fetchone()["c"]
    check("followup not persisted without llm", fu_after == 0, f"count={fu_after}")
    # 追问端点缺问题 → 400
    r2 = client.post(f"/api/reports/{report_id}/followup", json={"question": "  "})
    check("followup rejects empty question", r2.status_code == 400, f"status={r2.status_code}")
    # 追问不存在的报告 → 404
    r2 = client.post("/api/reports/nonexistent/followup", json={"question": "x"})
    check("followup 404 on missing report", r2.status_code == 404, f"status={r2.status_code}")
    # 直接用 repo 助手验证落库 + 列表 + 详情页渲染 (含 markdown)
    from app.repositories import add_report_followup, list_report_followups
    add_report_followup(uid, report_id, "为什么力量偏多?", "因为**推**类容量集中。\n- 建议均衡")
    fus = list_report_followups(uid, report_id)
    check("followup persisted via repo", len(fus) == 1 and fus[0]["question"] == "为什么力量偏多?", f"fus={fus}")
    r3 = client.get(report_loc)
    check("followup renders in report page", "为什么力量偏多?" in r3.text and "<strong>推</strong>" in r3.text,
          "stored followup not rendered")

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

    # 16b1. 首页数据看板 (日/周/月三档 + 趋势/肌群/跑步类型) — 聚合函数 + 渲染
    from app.repositories import dashboard_stats
    with db() as conn:
        _owner_id = conn.execute("SELECT id FROM users WHERE username='owner'").fetchone()["id"]
    dash = dashboard_stats(_owner_id, 12)
    check("dashboard has three periods", set(dash["periods"].keys()) == {"day", "week", "month"},
          f"periods={list(dash['periods'].keys())}")
    check("dashboard default is week", dash["default"] == "week", f"default={dash['default']}")
    check("dashboard each period has 12 buckets",
          all(len(dash["periods"][g]["buckets"]) == 12 for g in ("day", "week", "month")),
          f"lens={ {g: len(dash['periods'][g]['buckets']) for g in ('day','week','month')} }")
    wk = dash["periods"]["week"]
    check("dashboard period keys present",
          all(k in wk for k in ("buckets", "muscle_groups", "run_types", "totals")),
          f"keys={list(wk.keys())}")
    check("dashboard week totals reconcile",
          round(sum(b["strength_volume_kg"] for b in wk["buckets"]), 1) == wk["totals"]["strength_volume_kg"],
          "week strength sum != totals")
    check("dashboard muscle groups sorted desc",
          all(wk["muscle_groups"][i]["volume_kg"] >= wk["muscle_groups"][i + 1]["volume_kg"]
              for i in range(len(wk["muscle_groups"]) - 1) if wk["muscle_groups"][i]["group"] != "未分类"),
          f"groups={[g['group'] for g in wk['muscle_groups']]}")
    check("dashboard unclassified last",
          not wk["muscle_groups"] or wk["muscle_groups"][-1]["group"] == "未分类"
          or all(g["group"] != "未分类" for g in wk["muscle_groups"]),
          f"groups={[g['group'] for g in wk['muscle_groups']]}")
    check("dashboard run_types structured",
          all(set(rt.keys()) >= {"type", "label", "distance_km", "count"} for rt in wk["run_types"]),
          f"run_types={wk['run_types']}")
    check("dashboard run_types sorted by distance desc",
          all(wk["run_types"][i]["distance_km"] >= wk["run_types"][i + 1]["distance_km"]
              for i in range(len(wk["run_types"]) - 1)),
          f"run_types={[(t['label'], t['distance_km']) for t in wk['run_types']]}")
    check("dashboard run_type distance reconciles running_km",
          abs(sum(rt["distance_km"] for rt in wk["run_types"]) - wk["totals"]["running_km"]) < 0.5,
          f"sum={sum(rt['distance_km'] for rt in wk['run_types'])} running_km={wk['totals']['running_km']}")
    check("dashboard has_data flag", dash["has_data"] is True, f"has_data={dash['has_data']}")
    # 首页应渲染看板结构 (近期有数据时): 粒度切换 + 内嵌数据 + 饼图容器
    r = client.get("/")
    check("home renders dashboard", r.status_code == 200 and "训练概览" in r.text
          and 'id="dashboard-data"' in r.text and 'data-gran="month"' in r.text
          and 'id="chart-runtype"' in r.text,
          f"status={r.status_code}")

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
    # 16c-share. 分享页只展示训练白名单字段，且七种主题均可切换
    r = client.get("/day/2026-06-30/share")
    forbidden_share_fields = ["引体向上", "杠铃划船", "训练详情", "查看完整活动", "平均步频", "平均功率", "气温来源", "AI 分析"]
    check("share renders strength-only card", r.status_code == 200 and "力量训练" in r.text and "背" in r.text
          and all(field not in r.text for field in forbidden_share_fields), f"status={r.status_code}")
    check("share page provides detail entry", "生成分享卡片" in client.get("/day/2026-06-30").text, "missing share entry")
    r = client.get("/day/2026-06-13/share?theme=mono")
    check("share renders running-only card", r.status_code == 200 and "跑步训练" in r.text and "平均心率" in r.text
          and 'class="poster mono"' in r.text, f"status={r.status_code}")
    r = client.get("/day/2020-01-01/share?theme=unknown")
    check("share renders empty day and falls back theme", r.status_code == 200 and "这一天没有训练记录" in r.text
          and 'class="poster clean"' in r.text, f"status={r.status_code}")
    for share_theme in ["clean", "editorial", "midnight", "ember", "glacier", "citrus", "mono"]:
        r = client.get(f"/day/2026-06-30/share?theme={share_theme}")
        check(f"share theme {share_theme} renders", r.status_code == 200 and f'class="poster {share_theme}"' in r.text,
              f"status={r.status_code}")
    r = client.get("/day/2026-02-30/share", follow_redirects=False)
    check("share rejects impossible date", r.status_code == 400, f"status={r.status_code}")
    r = client.get("/day/notadate/share", follow_redirects=False)
    check("share rejects bad date", r.status_code == 400, f"status={r.status_code}")
    from app.repositories import _aggregate_share_runs
    aggregate = _aggregate_share_runs([
        {"activity_variant": "road_run", "fit_start_time": "2026-07-01T08:00:00", "id": "a",
         "timer_seconds": 600, "distance_m": 2000, "run_type": "easy", "avg_pace_sec_per_km": 300, "avg_hr": 140},
        {"activity_variant": "track_run", "fit_start_time": "2026-07-01T09:00:00", "id": "b",
         "timer_seconds": 900, "distance_m": 3000, "run_type": "tempo", "avg_pace_sec_per_km": 300, "avg_hr": 150},
    ])
    check("share run aggregation is stable and private", aggregate["count"] == 2
          and aggregate["distance_km"] == 5.0 and aggregate["duration_seconds"] == 1500
          and aggregate["avg_pace_sec_per_km"] == 300.0 and aggregate["avg_hr"] == 146
          and aggregate["type_labels"] == ["轻松跑", "节奏跑"]
          and aggregate["context_labels"] == ["路跑", "操场"], f"aggregate={aggregate}")

    # 16c-export. PNG 导出: 有训练页含 vendor 脚本 + 导出按钮, 捕获目标锁定 #poster
    r = client.get("/day/2026-06-30/share")
    check("share page loads snapdom vendor + export script",
          "/static/js/vendor/snapdom.min.js" in r.text and "/static/js/share_export.js" in r.text,
          f"status={r.status_code}")
    check("share page has export button on training day", 'id="save-png-button"' in r.text, "missing export button")
    # 空白天无训练则不出导出按钮 (与「无训练无入口」边界一致)
    r = client.get("/day/2020-01-01/share")
    check("share page hides export button on empty day", 'id="save-png-button"' not in r.text, "export button leaked on empty day")
    # vendor 脚本本体可静态访问 (离线可用, 不依赖 CDN)
    r = client.get("/static/js/vendor/snapdom.min.js")
    check("snapdom vendor asset served", r.status_code == 200 and "SnapDOM" in r.text, f"status={r.status_code}")
    r = client.get("/static/js/share_export.js")
    check("share export script served", r.status_code == 200 and "snapdom" in r.text, f"status={r.status_code}")
    # 窄屏适配脚本: 海报固定 450×600 逻辑尺寸, 靠等比缩放显示完整卡片 (修复移动端裁切)
    r = client.get("/day/2026-06-30/share")
    check("share page loads fit script", "/static/js/share_fit.js" in r.text, f"status={r.status_code}")
    r = client.get("/static/js/share_fit.js")
    check("share fit script served", r.status_code == 200 and "poster-scale" in r.text, f"status={r.status_code}")

    # 非法日期
    r = client.get("/day/notadate", follow_redirects=False)
    check("day detail rejects bad date", r.status_code == 400, f"status={r.status_code}")
    # 空白天也能渲染
    r = client.get("/day/2020-01-01")
    check("day detail empty day renders", r.status_code == 200 and "没有训练记录" in r.text, f"status={r.status_code}")

    # 16d. 跑步机心率强度解读 (_treadmill_summary 纯函数单元测试)
    # 跑步机配速受机器设定/无坡度/无风阻污染, 故以心率为主参照, 与用户自己
    # 跑步机历史比 (个人基线); 样本不足只呈现不下判断。
    from app.services.analysis import _treadmill_summary, TREADMILL_MIN_SAMPLES

    def _tm_run(hr, pace, indoor=True):
        return {"indoor": indoor, "avg_hr": hr, "pace_sec_per_km": pace}

    # (a) 样本不足 (<MIN): 只呈现均值, baseline_ready=False, 不下判断
    few = _treadmill_summary([_tm_run(140, 360), _tm_run(142, 358)])
    check("treadmill few-sample not ready", few["baseline_ready"] is False and few["treadmill_run_count"] == 2,
          f"tm={few}")
    check("treadmill few-sample still shows avg_hr", few["avg_hr"] is not None and few["high_hr_flat_pace_count"] == 0,
          f"tm={few}")

    # (b) 够样本 + 一次"心率高、配速没更快": 应标出 high_hr_flat_pace
    #     基线均心率≈140, 末次 152 (高>+5) 且配速 370 (>=均值, 没更快)
    many = _treadmill_summary([
        _tm_run(138, 360), _tm_run(140, 358), _tm_run(139, 362), _tm_run(152, 370),
    ])
    check("treadmill baseline ready at min samples", many["baseline_ready"] is True
          and many["with_hr_count"] >= TREADMILL_MIN_SAMPLES, f"tm={many}")
    check("treadmill flags high-hr flat-pace run", many["high_hr_flat_pace_count"] >= 1, f"tm={many}")

    # (c) 够样本但心率平稳: 不误报
    steady = _treadmill_summary([
        _tm_run(140, 360), _tm_run(141, 359), _tm_run(139, 361), _tm_run(140, 360),
    ])
    check("treadmill steady no false flag", steady["baseline_ready"] is True
          and steady["high_hr_flat_pace_count"] == 0, f"tm={steady}")

    # (d) 数值守卫: pace 为脏字符串时不崩, 当作缺失
    dirty = _treadmill_summary([
        _tm_run(140, "bad"), _tm_run(141, None), _tm_run(139, 361), _tm_run(150, "x"),
    ])
    check("treadmill numeric guard tolerates dirty pace", dirty["treadmill_run_count"] == 4
          and dirty["avg_hr"] is not None, f"tm={dirty}")

    # (e) 户外跑不计入跑步机聚合
    mixed = _treadmill_summary([_tm_run(140, 360), _tm_run(145, 300, indoor=False)])
    check("treadmill excludes outdoor runs", mixed["treadmill_run_count"] == 1, f"tm={mixed}")

    # 16e. 对外只读 API (ADR 0004): 签发 / 鉴权 / 越权隔离 / 只读 / 吊销 / owner 权限
    from app.api_keys import issue_api_key as _issue_key
    from app.security import hash_password as _hp
    from app.db import now_utc as _now, new_id as _nid

    # owner id (供直接签发, 绕过一次性明文的页面机制)
    with db() as conn:
        owner_id = conn.execute("SELECT id FROM users WHERE username='owner'").fetchone()["id"]

    # (a) owner 登录态可进签发页
    r = client.get("/settings/api-keys")
    check("api keys page visible to owner", r.status_code == 200 and "对外只读 API" in r.text, f"status={r.status_code}")

    # (b) 无 bearer / 坏 bearer -> 401
    r = client.get("/api/v1/meta")
    check("api v1 no bearer -> 401", r.status_code == 401, f"status={r.status_code}")
    r = client.get("/api/v1/meta", headers={"Authorization": "Bearer srda_bogus_key"})
    check("api v1 bad bearer -> 401", r.status_code == 401, f"status={r.status_code}")

    # (c) 签发一个真实 Key (直接调 issue, 拿一次性明文) 并调各只读端点
    fresh = _issue_key(owner_id, "e2e-agent")
    check("api key has srda_ prefix", fresh["raw_key"].startswith("srda_"), f"raw={fresh['raw_key'][:10]}")
    ah = {"Authorization": "Bearer " + fresh["raw_key"]}
    for path, label in [("/api/v1/meta", "meta"), ("/api/v1/context", "context"),
                        ("/api/v1/days/2026-06-13", "day"), ("/api/v1/goals/current", "goals-current"),
                        ("/api/v1/goals/history", "goals-history"), ("/api/v1/muscle-map", "muscle-map"),
                        ("/api/v1/rest-notes", "rest-notes"), ("/api/v1/reports", "reports")]:
        rr = client.get(path, headers=ah)
        check(f"api v1 {label} works", rr.status_code == 200 and rr.json().get("ok") is True,
              f"{path} status={rr.status_code}")

    # context 应含 owner 的力量/跑步聚合 (owner 已导入 garmin 样本)
    ctx = client.get("/api/v1/context", headers=ah).json()["data"]
    check("api v1 context carries data", "running" in ctx and "strength" in ctx and "goal" in ctx,
          f"keys={list(ctx.keys())}")

    # (d) 越权隔离 (核心安全线): 造用户 B + B 的 Key, B 读到的 day detail 不含 owner 数据
    b_id = "userb-" + _nid()[:8]
    with db() as conn:
        conn.execute("INSERT INTO users (id,username,password_hash,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (b_id, "userb-" + b_id[-4:], _hp("x-pass"), "user", "active", _now(), _now()))
    fresh_b = _issue_key(b_id, "b-agent")
    bh = {"Authorization": "Bearer " + fresh_b["raw_key"]}
    rb = client.get("/api/v1/days/2026-06-13", headers=bh)
    b_day = rb.json().get("data", {}) if rb.status_code == 200 else {}
    # owner 该日有跑步; B 无任何数据 -> B 的该日 running 应为空
    check("api v1 cross-user isolation (B cannot see owner data)",
          rb.status_code == 200 and not b_day.get("running") and not b_day.get("strength"),
          f"b_running={b_day.get('running')} b_strength={b_day.get('strength')}")

    # (e) 只读: POST 到 v1 端点 -> 405 (无写路由)
    r = client.post("/api/v1/context", headers=ah)
    check("api v1 rejects POST (read-only)", r.status_code == 405, f"status={r.status_code}")

    # (f) 吊销后立即失效
    revoked = _issue_key(owner_id, "to-revoke")
    rh = {"Authorization": "Bearer " + revoked["raw_key"]}
    check("api v1 fresh key works before revoke",
          client.get("/api/v1/meta", headers=rh).status_code == 200, "should be 200 before revoke")
    with db() as conn:
        client_ok = client.post("/api/settings/api-keys/" + revoked["id"] + "/revoke", follow_redirects=False)
    check("api key revoke redirects", client_ok.status_code == 303, f"status={client_ok.status_code}")
    check("api v1 revoked key -> 401",
          client.get("/api/v1/meta", headers=rh).status_code == 401, "should be 401 after revoke")

    # (g) 非 owner 不能进签发页 / 不能签发: 用 B 登录验 403
    #     (B 密码 x-pass, 先登出 owner 再以 B 登录)
    client.post("/api/auth/logout", follow_redirects=False)
    rb_login = client.post("/api/auth/login", data={"username": "userb-" + b_id[-4:], "password": "x-pass"}, follow_redirects=False)
    check("user B can login", rb_login.status_code == 303, f"status={rb_login.status_code}")
    r = client.get("/settings/api-keys", follow_redirects=False)
    check("api keys page forbidden to non-owner", r.status_code == 403, f"status={r.status_code}")
    r = client.post("/api/settings/api-keys", data={"label": "x"}, follow_redirects=False)
    check("non-owner cannot issue key", r.status_code == 403, f"status={r.status_code}")
    # 恢复 owner 登录, 供后续登出检查
    client.post("/api/auth/logout", follow_redirects=False)
    login(client)

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
