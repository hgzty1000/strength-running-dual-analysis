from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db import new_id, now_utc

FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=dt.timezone.utc)
BASE_TYPES = {
    0x00: ("enum", "B", 0xFF), 0x01: ("sint8", "b", 0x7F), 0x02: ("uint8", "B", 0xFF),
    0x83: ("sint16", "h", 0x7FFF), 0x84: ("uint16", "H", 0xFFFF),
    0x85: ("sint32", "i", 0x7FFFFFFF), 0x86: ("uint32", "I", 0xFFFFFFFF),
    0x07: ("string", None, None), 0x88: ("float32", "f", None), 0x89: ("float64", "d", None),
    0x0A: ("uint8z", "B", 0x00), 0x8B: ("uint16z", "H", 0), 0x8C: ("uint32z", "I", 0),
    0x0D: ("byte", "B", None), 0x8E: ("sint64", "q", 0x7FFFFFFFFFFFFFFF),
    0x8F: ("uint64", "Q", 0xFFFFFFFFFFFFFFFF), 0x90: ("uint64z", "Q", 0),
}

SPORT_FAMILY = {1: "running", 2: "cycling", 5: "swimming", 17: "hiking"}
RUN_VARIANT = {0: "road_run", 1: "treadmill_run", 3: "trail_run", 4: "track_run"}
RUN_CONTEXT = {"road_run": "road", "treadmill_run": "treadmill", "track_run": "track", "trail_run": "trail"}

# 跑步场景中文标签 (供前端展示; 区分路跑/跑步机/操场/越野)
VARIANT_LABELS = {
    "road_run": "路跑",
    "treadmill_run": "跑步机",
    "track_run": "操场",
    "trail_run": "越野",
    "unknown": "未知场景",
}
FAMILY_LABELS = {
    "running": "跑步", "cycling": "骑行", "swimming": "游泳", "hiking": "徒步", "unknown": "未知",
}


def variant_label(variant: str | None) -> str:
    """跑步场景枚举 → 中文 (road_run→路跑 等)。"""
    return VARIANT_LABELS.get(variant or "unknown", variant or "未知场景")


def family_label(family: str | None) -> str:
    return FAMILY_LABELS.get(family or "unknown", family or "未知")

@dataclass
class ParsedActivity:
    fit_name: str
    messages: list[tuple[int, dict[int, Any]]]
    summary: dict[str, Any]
    laps: list[dict[str, Any]]
    running_metrics: dict[str, Any] | None
    field_coverage: dict[str, bool]


def fit_time(seconds: int | None) -> dt.datetime | None:
    return FIT_EPOCH + dt.timedelta(seconds=seconds) if seconds is not None else None


def semicircles(value: int | None) -> float | None:
    return value * (180.0 / 2**31) if value is not None else None


def decode_value(raw: bytes, base_type: int, endian: str):
    key = base_type if base_type in BASE_TYPES else (base_type & 0x1F)
    if key not in BASE_TYPES:
        return raw
    name, fmt, invalid = BASE_TYPES[key]
    if name == "string":
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    if fmt is None:
        return raw
    size = struct.calcsize(fmt)
    values = []
    prefix = "<" if endian == "<" else ">"
    for offset in range(0, len(raw), size):
        chunk = raw[offset:offset + size]
        if len(chunk) < size:
            break
        value = struct.unpack(prefix + fmt, chunk)[0]
        if invalid is not None and value == invalid:
            value = None
        values.append(value)
    return values[0] if len(values) == 1 else (values or None)


def parse_fit_messages(data: bytes) -> list[tuple[int, dict[int, Any]]]:
    if len(data) < 14:
        raise ValueError("FIT 文件太短")
    header_size = data[0]
    if header_size not in {12, 14}:
        raise ValueError(f"不支持的 FIT header 大小: {header_size}")
    if data[8:12] != b".FIT":
        raise ValueError("不是有效 FIT 文件")
    offset = header_size
    end = len(data) - 2
    definitions: dict[int, tuple[int, str, list[tuple[int, int, int]], list[tuple[int, int, int]]]] = {}
    messages = []
    last_timestamp: int | None = None
    while offset < end:
        header = data[offset]; offset += 1
        if header & 0x80:
            local_type = (header >> 5) & 0x03; time_offset = header & 0x1F; is_definition = False; has_developer = False
        else:
            is_definition = bool(header & 0x40); has_developer = bool(header & 0x20); local_type = header & 0x0F
        if is_definition:
            architecture = data[offset + 1]; offset += 2
            endian = ">" if architecture else "<"
            global_message = struct.unpack(endian + "H", data[offset:offset + 2])[0]; offset += 2
            field_count = data[offset]; offset += 1
            fields = []
            for _ in range(field_count):
                fields.append((data[offset], data[offset + 1], data[offset + 2])); offset += 3
            developer_fields = []
            if has_developer:
                developer_count = data[offset]; offset += 1
                for _ in range(developer_count):
                    developer_fields.append((data[offset], data[offset + 1], data[offset + 2])); offset += 3
            definitions[local_type] = (global_message, endian, fields, developer_fields)
            continue
        if local_type not in definitions:
            raise ValueError(f"FIT data message 缺少 definition: local={local_type}")
        global_message, endian, fields, developer_fields = definitions[local_type]
        message = {}
        for field_num, size, base_type in fields:
            raw = data[offset:offset + size]
            if len(raw) < size:
                raise ValueError("FIT data message 不完整")
            offset += size
            message[field_num] = decode_value(raw, base_type, endian)
        for _field_num, size, _developer_index in developer_fields:
            offset += size
        if header & 0x80 and last_timestamp is not None:
            base = last_timestamp & ~0x1F
            timestamp = base + time_offset
            if timestamp < last_timestamp:
                timestamp += 32
            message[253] = timestamp
            last_timestamp = timestamp
        if isinstance(message.get(253), int):
            last_timestamp = message[253]
        messages.append((global_message, message))
    return messages


def first_int(value) -> int | None:
    return value if isinstance(value, int) else None


def avg(values: list[float | int]) -> float | None:
    return sum(values) / len(values) if values else None


def values(records, field, conv=lambda x: x):
    out = []
    for record in records:
        v = record.get(field)
        if v is not None:
            out.append(conv(v))
    return out


def read_single_fit(zip_path: Path) -> tuple[str, bytes]:
    with zipfile.ZipFile(zip_path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(".fit")]
        if not members:
            raise ValueError("ZIP 内没有 .fit 文件")
        if len(members) > 1:
            raise ValueError("ZIP 内有多个 .fit 文件, demo 不自动处理")
        name = members[0]
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise ValueError("FIT 文件路径不安全")
        return name, archive.read(name)


def parse_activity(zip_path: Path) -> ParsedActivity:
    fit_name, data = read_single_fit(zip_path)
    messages = parse_fit_messages(data)
    by: dict[int, list[dict[int, Any]]] = {}
    for global_num, message in messages:
        by.setdefault(global_num, []).append(message)
    sessions = by.get(18, [])
    records = by.get(20, [])
    laps_raw = by.get(19, [])
    sports = by.get(12, [])
    session = sessions[0] if sessions else {}
    sport_message = sports[0] if sports else {}
    start_seconds = first_int(session.get(2)) or next((first_int(r.get(253)) for r in records if first_int(r.get(253)) is not None), None) or first_int(session.get(253))
    start_time = fit_time(start_seconds)
    if start_time is None:
        raise ValueError("无法读取活动开始时间")
    sport = first_int(session.get(5)) if first_int(session.get(5)) is not None else first_int(sport_message.get(0))
    sub_sport = first_int(session.get(6)) if first_int(session.get(6)) is not None else first_int(sport_message.get(1))
    family = SPORT_FAMILY.get(sport, "unknown")
    if family == "running":
        variant = RUN_VARIANT.get(sub_sport, "unknown")
    else:
        variant = "unknown"
    hrs = values(records, 3)
    cads = values(records, 4)
    powers = values(records, 7)
    temps = values(records, 13)
    speeds = values(records, 73, lambda x: x / 1000) or values(records, 6, lambda x: x / 1000)
    alts = values(records, 78, lambda x: x / 5 - 500) or values(records, 2, lambda x: x / 5 - 500)
    gps_count = sum(1 for r in records if r.get(0) is not None and r.get(1) is not None)
    distance_m = (session.get(9) / 100) if isinstance(session.get(9), int) else None
    timer_seconds = (session.get(8) / 1000) if isinstance(session.get(8), int) else None
    elapsed_seconds = (session.get(7) / 1000) if isinstance(session.get(7), int) else None
    calories = session.get(11) if isinstance(session.get(11), int) else None
    summary = {
        "fit_start_time": start_time.isoformat(),
        "local_date": start_time.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat(),
        "sport": str(sport) if sport is not None else None,
        "sub_sport": str(sub_sport) if sub_sport is not None else None,
        "activity_family": family,
        "activity_variant": variant,
        "elapsed_seconds": elapsed_seconds,
        "timer_seconds": timer_seconds,
        "distance_m": distance_m,
        "calories": calories,
        "gps_available": gps_count > 0,
        "lap_count": len(laps_raw),
        "gps_points": gps_count,
    }
    field_coverage = {
        "heart_rate": bool(hrs), "cadence": bool(cads), "power": bool(powers), "temperature": bool(temps),
        "altitude": bool(alts), "gps": gps_count > 0, "laps": bool(laps_raw), "speed": bool(speeds),
    }
    laps = []
    for idx, lap in enumerate(laps_raw):
        laps.append({
            "lap_index": idx,
            "start_time": fit_time(first_int(lap.get(2))).isoformat() if first_int(lap.get(2)) is not None else None,
            "elapsed_seconds": lap.get(7) / 1000 if isinstance(lap.get(7), int) else None,
            "timer_seconds": lap.get(8) / 1000 if isinstance(lap.get(8), int) else None,
            "distance_m": lap.get(9) / 100 if isinstance(lap.get(9), int) else None,
            "avg_speed_mps": lap.get(110) / 1000 if isinstance(lap.get(110), int) else (lap.get(13) / 1000 if isinstance(lap.get(13), int) else None),
            "avg_hr": lap.get(15) if isinstance(lap.get(15), int) else None,
            "max_hr": lap.get(16) if isinstance(lap.get(16), int) else None,
            "avg_cadence": lap.get(17) if isinstance(lap.get(17), int) else None,
            "avg_power": lap.get(19) if isinstance(lap.get(19), int) else None,
            "raw_json": lap,
        })
    running_metrics = None
    if family == "running":
        avg_speed = session.get(124) / 1000 if isinstance(session.get(124), int) else avg(speeds)
        max_speed = session.get(125) / 1000 if isinstance(session.get(125), int) else (max(speeds) if speeds else None)
        running_metrics = {
            "run_context": RUN_CONTEXT.get(variant, "unknown"),
            "run_type": "mixed_unknown",
            "avg_pace_sec_per_km": (1000 / avg_speed) if avg_speed else None,
            "avg_speed_mps": avg_speed,
            "max_speed_mps": max_speed,
            "avg_hr": session.get(16) if isinstance(session.get(16), int) else avg(hrs),
            "max_hr": session.get(17) if isinstance(session.get(17), int) else (max(hrs) if hrs else None),
            "avg_cadence": session.get(18) if isinstance(session.get(18), int) else avg(cads),
            "max_cadence": session.get(19) if isinstance(session.get(19), int) else (max(cads) if cads else None),
            "avg_power": session.get(20) if isinstance(session.get(20), int) else avg(powers),
            "max_power": session.get(21) if isinstance(session.get(21), int) else (max(powers) if powers else None),
            "elevation_gain_m": session.get(22) if isinstance(session.get(22), int) else None,
            "elevation_loss_m": session.get(23) if isinstance(session.get(23), int) else None,
            "temperature_c": avg(temps),
            "temperature_source": "fit_native" if temps else "missing",
            "metrics_json": {"record_count": len(records), "gps_points": gps_count, "alt_min": min(alts) if alts else None, "alt_max": max(alts) if alts else None},
        }
    return ParsedActivity(fit_name, messages, summary, laps, running_metrics, field_coverage)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def store_import_file(zip_path: Path, dest_dir: Path) -> tuple[Path, Path, str, int, ParsedActivity]:
    digest = sha256_file(zip_path)
    size = zip_path.stat().st_size
    parsed = parse_activity(zip_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_dest = dest_dir / "original.zip"
    fit_dest = dest_dir / "activity.fit"
    shutil.copyfile(zip_path, zip_dest)
    _, fit_bytes = read_single_fit(zip_path)
    fit_dest.write_bytes(fit_bytes)
    return zip_dest, fit_dest, digest, size, parsed


def activity_unique_key(parsed: ParsedActivity, file_hash: str) -> str:
    s = parsed.summary
    pieces = [s.get("fit_start_time"), s.get("sport"), s.get("sub_sport"), round(s.get("distance_m") or 0), round(s.get("timer_seconds") or 0)]
    if pieces[0]:
        return "fit:" + ":".join(map(str, pieces))
    return "file:" + file_hash


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
