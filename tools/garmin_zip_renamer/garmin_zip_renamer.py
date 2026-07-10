#!/usr/bin/env python3
"""Local Garmin ZIP renamer.

This tool is intentionally standalone: it reads Garmin-downloaded ZIP files,
extracts minimal FIT metadata from the ZIP member, and renames the ZIP itself.
It does not connect to the platform, does not use the network, and does not
maintain any ledger or database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import struct
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback is not targeted.
    ZoneInfo = None  # type: ignore[assignment]

FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=dt.timezone.utc)
CHINA_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else dt.timezone(dt.timedelta(hours=8))

BASE_TYPES = {
    0x00: ("enum", "B", 0xFF),
    0x01: ("sint8", "b", 0x7F),
    0x02: ("uint8", "B", 0xFF),
    0x83: ("sint16", "h", 0x7FFF),
    0x84: ("uint16", "H", 0xFFFF),
    0x85: ("sint32", "i", 0x7FFFFFFF),
    0x86: ("uint32", "I", 0xFFFFFFFF),
    0x07: ("string", None, None),
    0x88: ("float32", "f", None),
    0x89: ("float64", "d", None),
    0x0A: ("uint8z", "B", 0x00),
    0x8B: ("uint16z", "H", 0x0000),
    0x8C: ("uint32z", "I", 0x00000000),
    0x0D: ("byte", "B", None),
    0x8E: ("sint64", "q", 0x7FFFFFFFFFFFFFFF),
    0x8F: ("uint64", "Q", 0xFFFFFFFFFFFFFFFF),
    0x90: ("uint64z", "Q", 0x0000000000000000),
}

SPORT_LABELS = {
    1: "running",
    2: "cycling",
    5: "swimming",
    17: "hiking",
}

RUN_SUB_SPORT_CONTEXT = {
    0: "road-run",
    1: "treadmill-run",
    4: "track-run",
    6: "trail-run",
}

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class ActivityMeta:
    start_time_utc: dt.datetime
    sport: int | None
    sub_sport: int | None
    elapsed_seconds: float | None = None
    timer_seconds: float | None = None
    distance_meters: float | None = None

    @property
    def context_slug(self) -> str:
        if self.sport == 1:
            return RUN_SUB_SPORT_CONTEXT.get(self.sub_sport, "run")
        if self.sport == 2:
            return "cycling"
        if self.sport == 5:
            return "swimming"
        if self.sport == 17:
            return "hiking"
        return "activity"

    @property
    def sport_label(self) -> str:
        label = SPORT_LABELS.get(self.sport, "unknown")
        if self.sport == 1:
            return f"{label}/{self.context_slug}"
        return label

    def local_time(self) -> dt.datetime:
        return self.start_time_utc.astimezone(CHINA_TZ)

    def duplicate_key(self) -> tuple[dt.datetime, int | None, int | None, int | None, int | None]:
        """Stable local identity for duplicate detection without a ledger."""
        distance = round(self.distance_meters) if self.distance_meters is not None else None
        timer = round(self.timer_seconds) if self.timer_seconds is not None else None
        return (self.start_time_utc, self.sport, self.sub_sport, distance, timer)


def duplicate_key_label(meta: ActivityMeta) -> str:
    distance = round(meta.distance_meters) if meta.distance_meters is not None else "?"
    timer = round(meta.timer_seconds) if meta.timer_seconds is not None else "?"
    return f"{meta.local_time():%Y-%m-%d %H:%M} / {meta.context_slug} / {distance}m / {timer}s"


@dataclass
class PlanItem:
    source: Path
    status: str
    original_name: str
    target_name: str = ""
    target_path: Path | None = None
    activity_time: str = ""
    activity_type: str = ""
    summary: str = ""
    note: str = ""
    meta: ActivityMeta | None = None

    @property
    def can_rename(self) -> bool:
        return self.status in {"OK", "CONFLICT"}


def mark_local_duplicates(items: list[PlanItem]) -> None:
    seen: dict[tuple[dt.datetime, int | None, int | None, int | None, int | None], PlanItem] = {}
    for item in items:
        if item.meta is None or item.status == "ERROR":
            continue
        key = item.meta.duplicate_key()
        first = seen.get(key)
        if first is None:
            seen[key] = item
            continue
        if item.source.name == first.source.name:
            continue
        item.status = "DUPLICATE"
        item.target_name = ""
        item.target_path = None
        item.note = f"duplicate of {first.original_name} ({duplicate_key_label(item.meta)})"


def fit_time(seconds: int | None) -> dt.datetime | None:
    if seconds is None:
        return None
    return FIT_EPOCH + dt.timedelta(seconds=seconds)


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
        chunk = raw[offset : offset + size]
        if len(chunk) < size:
            break
        value = struct.unpack(prefix + fmt, chunk)[0]
        if invalid is not None and value == invalid:
            value = None
        values.append(value)
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def parse_fit_messages(data: bytes) -> list[tuple[int, dict[int, object]]]:
    if len(data) < 14:
        raise ValueError("FIT 文件太短")
    header_size = data[0]
    if header_size not in {12, 14}:
        raise ValueError(f"不支持的 FIT header 大小: {header_size}")
    if data[8:12] != b".FIT":
        raise ValueError("不是有效 FIT 文件")

    offset = header_size
    end = len(data) - 2  # final CRC, ignored for this local utility
    definitions: dict[int, tuple[int, str, list[tuple[int, int, int]], list[tuple[int, int, int]]]] = {}
    messages: list[tuple[int, dict[int, object]]] = []
    last_timestamp: int | None = None

    while offset < end:
        header = data[offset]
        offset += 1
        if header & 0x80:
            local_type = (header >> 5) & 0x03
            time_offset = header & 0x1F
            is_definition = False
            has_developer_fields = False
        else:
            is_definition = bool(header & 0x40)
            has_developer_fields = bool(header & 0x20)
            local_type = header & 0x0F

        if is_definition:
            if offset + 5 > end:
                raise ValueError("FIT definition message 不完整")
            _reserved = data[offset]
            architecture = data[offset + 1]
            offset += 2
            endian = ">" if architecture else "<"
            global_message = struct.unpack(endian + "H", data[offset : offset + 2])[0]
            offset += 2
            field_count = data[offset]
            offset += 1
            fields: list[tuple[int, int, int]] = []
            for _ in range(field_count):
                fields.append((data[offset], data[offset + 1], data[offset + 2]))
                offset += 3
            developer_fields: list[tuple[int, int, int]] = []
            if has_developer_fields:
                developer_count = data[offset]
                offset += 1
                for _ in range(developer_count):
                    developer_fields.append((data[offset], data[offset + 1], data[offset + 2]))
                    offset += 3
            definitions[local_type] = (global_message, endian, fields, developer_fields)
            continue

        if local_type not in definitions:
            raise ValueError(f"FIT data message 缺少 definition: local={local_type}")
        global_message, endian, fields, developer_fields = definitions[local_type]
        message: dict[int, object] = {}
        for field_num, size, base_type in fields:
            raw = data[offset : offset + size]
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
        timestamp_value = message.get(253)
        if isinstance(timestamp_value, int):
            last_timestamp = timestamp_value
        messages.append((global_message, message))

    return messages


def first_int(value) -> int | None:
    if isinstance(value, int):
        return value
    return None


def read_activity_meta_from_fit(data: bytes) -> ActivityMeta:
    messages = parse_fit_messages(data)
    sessions = [msg for global_num, msg in messages if global_num == 18]
    records = [msg for global_num, msg in messages if global_num == 20]
    sports = [msg for global_num, msg in messages if global_num == 12]

    session = sessions[0] if sessions else {}
    sport_message = sports[0] if sports else {}

    start_seconds = first_int(session.get(2))
    if start_seconds is None:
        # Fallback: first record timestamp, then session timestamp.
        for record in records:
            start_seconds = first_int(record.get(253))
            if start_seconds is not None:
                break
    if start_seconds is None:
        start_seconds = first_int(session.get(253))
    start_time = fit_time(start_seconds)
    if start_time is None:
        raise ValueError("无法从 FIT 中读取活动开始时间")

    sport = first_int(session.get(5))
    if sport is None:
        sport = first_int(sport_message.get(0))
    sub_sport = first_int(session.get(6))
    if sub_sport is None:
        sub_sport = first_int(sport_message.get(1))

    elapsed = session.get(7)
    timer = session.get(8)
    distance = session.get(9)
    return ActivityMeta(
        start_time_utc=start_time,
        sport=sport,
        sub_sport=sub_sport,
        elapsed_seconds=elapsed / 1000 if isinstance(elapsed, int) else None,
        timer_seconds=timer / 1000 if isinstance(timer, int) else None,
        distance_meters=distance / 100 if isinstance(distance, int) else None,
    )


def read_single_fit_from_zip(zip_path: Path) -> bytes:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            fit_members = [name for name in archive.namelist() if name.lower().endswith(".fit")]
            if not fit_members:
                raise ValueError("ZIP 内没有 .fit 文件")
            if len(fit_members) > 1:
                raise ValueError(f"ZIP 内有多个 .fit 文件({len(fit_members)}), 第一版不自动处理")
            return archive.read(fit_members[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP 文件损坏或格式不正确") from exc


def sanitize_filename(name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("-", name)
    cleaned = cleaned.strip(" .")
    return cleaned or "activity.zip"


def build_target_name(meta: ActivityMeta) -> str:
    local_time = meta.local_time()
    raw = f"{local_time:%Y-%m-%d_%H%M}_{meta.context_slug}.zip"
    return sanitize_filename(raw)


def unique_target_path(source: Path, target_name: str) -> tuple[Path, bool]:
    target = source.with_name(target_name)
    if target.resolve() == source.resolve():
        return target, False
    if not target.exists():
        return target, False
    stem = target.stem
    suffix = target.suffix
    index = 2
    while True:
        candidate = target.with_name(f"{stem}__{index}{suffix}")
        if candidate.resolve() == source.resolve():
            return candidate, False
        if not candidate.exists():
            return candidate, True
        index += 1


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def format_summary(meta: ActivityMeta) -> str:
    parts = []
    if meta.distance_meters is not None:
        parts.append(f"{meta.distance_meters / 1000:.2f} km")
    if meta.timer_seconds is not None:
        parts.append(f"timer {format_seconds(meta.timer_seconds)}")
    if meta.elapsed_seconds is not None:
        parts.append(f"elapsed {format_seconds(meta.elapsed_seconds)}")
    return ", ".join(parts)


def plan_zip(zip_path: Path) -> PlanItem:
    try:
        fit_bytes = read_single_fit_from_zip(zip_path)
        meta = read_activity_meta_from_fit(fit_bytes)
        target_name = build_target_name(meta)
        target_path, collided = unique_target_path(zip_path, target_name)
        if target_path.resolve() == zip_path.resolve():
            status = "SKIP"
            note = "already named"
        elif collided:
            status = "CONFLICT"
            note = "target exists, suffix will be used"
        else:
            status = "OK"
            note = "ready"
        return PlanItem(
            source=zip_path,
            status=status,
            original_name=zip_path.name,
            target_name=target_path.name,
            target_path=target_path,
            activity_time=meta.local_time().strftime("%Y-%m-%d %H:%M"),
            activity_type=meta.sport_label,
            summary=format_summary(meta),
            note=note,
            meta=meta,
        )
    except Exception as exc:  # intentionally per-file resilient
        return PlanItem(
            source=zip_path,
            status="ERROR",
            original_name=zip_path.name,
            note=str(exc),
        )


def iter_zip_files(folder: Path) -> Iterable[Path]:
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".zip")


def scan_folder(folder: Path) -> list[PlanItem]:
    if not folder.exists():
        raise ValueError(f"目录不存在: {folder}")
    if not folder.is_dir():
        raise ValueError(f"不是目录: {folder}")
    items = [plan_zip(path) for path in iter_zip_files(folder)]
    mark_local_duplicates(items)
    return items


def execute_plan(items: list[PlanItem]) -> tuple[int, list[str]]:
    renamed = 0
    errors: list[str] = []
    for item in items:
        if not item.can_rename:
            continue
        try:
            target_name = build_target_name(item.meta) if item.meta else item.target_name
            target_path, _collided = unique_target_path(item.source, target_name)
            if target_path.resolve() == item.source.resolve():
                continue
            item.source.rename(target_path)
            renamed += 1
        except Exception as exc:
            errors.append(f"{item.original_name}: {exc}")
    return renamed, errors


def print_scan(items: list[PlanItem]) -> None:
    for item in items:
        print(f"[{item.status}] {item.original_name}")
        if item.target_name:
            print(f"  -> {item.target_name}")
        if item.activity_time:
            print(f"  time: {item.activity_time}  type: {item.activity_type}  {item.summary}")
        if item.note:
            print(f"  note: {item.note}")
    print()
    print(
        "Summary:",
        f"total={len(items)}",
        f"renameable={sum(1 for item in items if item.can_rename)}",
        f"duplicates={sum(1 for item in items if item.status == 'DUPLICATE')}",
        f"errors={sum(1 for item in items if item.status == 'ERROR')}",
    )


def run_cli_scan(folder: str) -> int:
    try:
        items = scan_folder(Path(folder))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print_scan(items)
    return 1 if any(item.status == "ERROR" for item in items) else 0


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title("Garmin ZIP 重命名工具")
            self.geometry("1100x620")
            self.items: list[PlanItem] = []

            self.folder_var = tk.StringVar(value=str(Path.cwd()))

            top = ttk.Frame(self, padding=10)
            top.pack(fill=tk.X)
            ttk.Label(top, text="目录:").pack(side=tk.LEFT)
            ttk.Entry(top, textvariable=self.folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
            ttk.Button(top, text="选择目录", command=self.choose_folder).pack(side=tk.LEFT, padx=3)
            ttk.Button(top, text="扫描", command=self.scan).pack(side=tk.LEFT, padx=3)
            self.rename_button = ttk.Button(top, text="执行重命名", command=self.rename, state=tk.DISABLED)
            self.rename_button.pack(side=tk.LEFT, padx=3)

            columns = ("status", "original", "target", "time", "type", "summary", "note")
            self.tree = ttk.Treeview(self, columns=columns, show="headings")
            headings = {
                "status": "状态",
                "original": "原文件名",
                "target": "新文件名",
                "time": "活动时间",
                "type": "类型",
                "summary": "距离/时长",
                "note": "备注",
            }
            widths = {
                "status": 90,
                "original": 180,
                "target": 240,
                "time": 130,
                "type": 150,
                "summary": 190,
                "note": 260,
            }
            for column in columns:
                self.tree.heading(column, text=headings[column])
                self.tree.column(column, width=widths[column], anchor=tk.W)
            self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

            self.status_var = tk.StringVar(value="扫描不会修改文件。执行重命名前会再次确认。")
            ttk.Label(self, textvariable=self.status_var, padding=(10, 0, 10, 10)).pack(fill=tk.X)

        def choose_folder(self) -> None:
            folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.cwd()))
            if folder:
                self.folder_var.set(folder)

        def scan(self) -> None:
            folder = Path(self.folder_var.get())
            try:
                self.items = scan_folder(folder)
            except Exception as exc:
                messagebox.showerror("扫描失败", str(exc))
                return
            self.refresh_tree()
            renameable = sum(1 for item in self.items if item.can_rename)
            errors = sum(1 for item in self.items if item.status == "ERROR")
            duplicates = sum(1 for item in self.items if item.status == "DUPLICATE")
            self.status_var.set(
                f"扫描完成: 共 {len(self.items)} 个 ZIP, 可重命名 {renameable} 个, 重复 {duplicates} 个, 错误 {errors} 个。"
            )
            self.rename_button.configure(state=tk.NORMAL if renameable else tk.DISABLED)

        def refresh_tree(self) -> None:
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            for item in self.items:
                self.tree.insert(
                    "",
                    tk.END,
                    values=(
                        item.status,
                        item.original_name,
                        item.target_name,
                        item.activity_time,
                        item.activity_type,
                        item.summary,
                        item.note,
                    ),
                )

        def rename(self) -> None:
            renameable = sum(1 for item in self.items if item.can_rename)
            if not renameable:
                messagebox.showinfo("无需重命名", "没有可重命名的文件。")
                return
            ok = messagebox.askyesno(
                "确认重命名",
                f"将重命名 {renameable} 个 ZIP 文件。\n\n不会解压、不会删除、不会覆盖现有文件。是否继续?",
            )
            if not ok:
                return
            renamed, errors = execute_plan(self.items)
            if errors:
                messagebox.showwarning("部分失败", f"已重命名 {renamed} 个文件。\n\n" + "\n".join(errors[:10]))
            else:
                messagebox.showinfo("完成", f"已重命名 {renamed} 个文件。")
            self.scan()

    App().mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地 Garmin ZIP 重命名工具")
    parser.add_argument("--scan", metavar="DIR", help="命令行只读扫描目录并打印预览, 不启动 GUI")
    args = parser.parse_args(argv)
    if args.scan:
        return run_cli_scan(args.scan)
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
