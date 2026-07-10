"""本地测试用: 从 catalog.json 生成若干训记训练日, 写入指定用户的镜像。

这不是生产同步 (生产走 app.services.xunji.XunjiClient 用真实 Key)。
它让本地/回归测试在没有训记 Key 时也能有力量数据形成双线分析。
用法: python scripts/seed_strength_from_catalog.py [username]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import db, init_db  # noqa: E402
from app.services import muscle_mapping, xunji as xunji_svc  # noqa: E402

CATALOG_PATH = Path(__file__).resolve().parent.parent / "catalog.json"

# 构造几天的训练, 覆盖不同肌群, 贴近用户真实分化训练
SAMPLE_DAYS = {
    "2026-06-23": [("杠铃卧推", [(60, 10), (60, 10), (65, 8)]), ("上斜哑铃卧推", [(22, 12), (22, 10)]), ("器械夹胸", [(40, 15), (40, 12)])],
    "2026-06-27": [("杠铃弯举", [(30, 12), (30, 10)]), ("绳索下压", [(25, 15), (25, 12)]), ("锤式弯举", [(16, 12), (16, 12)])],
    "2026-06-28": [("站姿哑铃推举", [(18, 10), (18, 10)]), ("哑铃侧平举", [(10, 15), (10, 12)]), ("面拉", [(25, 15), (25, 15)])],
    "2026-06-30": [("引体向上", [(0, 8), (0, 7)]), ("杠铃划船", [(60, 10), (60, 10)]), ("高位下拉", [(50, 12), (50, 12)])],
    "2026-07-04": [("深蹲", [(80, 8), (80, 8), (85, 6)]), ("腿举", [(120, 12), (120, 12)]), ("坐姿腿弯举", [(40, 15), (40, 12)])],
    "2026-07-05": [("杠铃卧推", [(62, 10), (62, 9)]), ("下斜哑铃卧推", [(24, 10), (24, 10)])],
}


def catalog_type(catalog: dict[str, str], name: str) -> str | None:
    return catalog.get(name)


def build_day_payload(catalog: dict[str, str], movements: list) -> dict:
    # 贴合真实 train_open_api_v2: res.trains[].movements[].sets[]
    return {
        "success": True,
        "res": {
            "trains": [
                {
                    "localid": 100000,
                    "title": "力量训练",
                    "movements": [
                        {
                            "name": name,
                            "type": catalog.get(name),
                            "sets": [{"weight": w, "unit": "kg", "reps": reps, "done": True} for (w, reps) in sets],
                        }
                        for (name, sets) in movements
                    ],
                }
            ]
        }
    }


def load_catalog_map() -> dict[str, str]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    movements = xunji_svc._extract_movements(data)
    return {m["name"]: m.get("type") for m in movements if m.get("name")}


def seed(username: str = "owner") -> dict:
    init_db()
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            raise SystemExit(f"用户不存在: {username}")
        user_id = row["id"]

    catalog = load_catalog_map()
    # store catalog into mirror
    catalog_movements = [{"name": n, "type": t} for n, t in catalog.items()]
    xunji_svc.store_catalog(user_id, catalog_movements)

    total = {"trainings": 0, "movements": 0, "sets": 0}
    for datestr, movements in SAMPLE_DAYS.items():
        payload = build_day_payload(catalog, movements)
        s = xunji_svc.store_training_day(user_id, datestr, payload)
        for k in total:
            total[k] += s[k]
    stats = muscle_mapping.ensure_mappings_for_user(user_id)
    return {"days": len(SAMPLE_DAYS), "totals": total, "mapping": stats}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "owner"
    print(json.dumps(seed(name), ensure_ascii=False, indent=2))
