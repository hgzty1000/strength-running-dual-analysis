from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import settings
from app.security import hash_password


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def ensure_dirs() -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  user_agent TEXT,
  ip_address TEXT
);

CREATE TABLE IF NOT EXISTS user_profiles (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  height_cm REAL,
  weight_kg REAL,
  birth_year INTEGER,
  sex TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_credentials (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_type TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  nonce TEXT NOT NULL,
  key_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_credentials_active ON user_credentials(user_id, credential_type) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS xunji_sync_state (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  last_successful_sync_at TEXT,
  last_synced_datestr TEXT,
  initial_full_done INTEGER NOT NULL DEFAULT 0,
  last_error_json TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xunji_training_days (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  datestr TEXT NOT NULL,
  source_hash TEXT,
  raw_json TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  UNIQUE(user_id, datestr)
);

CREATE TABLE IF NOT EXISTS xunji_trainings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  training_day_id TEXT NOT NULL REFERENCES xunji_training_days(id) ON DELETE CASCADE,
  xunji_local_id TEXT,
  datestr TEXT NOT NULL,
  title TEXT,
  note TEXT,
  start_at_raw TEXT,
  end_at_raw TEXT,
  calories REAL,
  raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xunji_trainings_user_date ON xunji_trainings(user_id, datestr);

CREATE TABLE IF NOT EXISTS xunji_movements (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  training_id TEXT NOT NULL REFERENCES xunji_trainings(id) ON DELETE CASCADE,
  movement_index INTEGER NOT NULL,
  action_name TEXT NOT NULL,
  xunji_action_id TEXT,
  xunji_type TEXT,
  raw_json TEXT NOT NULL,
  UNIQUE(training_id, movement_index)
);
CREATE INDEX IF NOT EXISTS idx_xunji_movements_action ON xunji_movements(user_id, action_name);

CREATE TABLE IF NOT EXISTS xunji_sets (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  movement_id TEXT NOT NULL REFERENCES xunji_movements(id) ON DELETE CASCADE,
  set_index INTEGER NOT NULL,
  weight REAL,
  weight_unit TEXT,
  reps INTEGER,
  rpe REAL,
  rest_seconds INTEGER,
  done INTEGER,
  raw_json TEXT NOT NULL,
  UNIQUE(movement_id, set_index)
);

CREATE TABLE IF NOT EXISTS xunji_action_catalog (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  xunji_action_id TEXT,
  action_name TEXT NOT NULL,
  xunji_type TEXT,
  raw_json TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  UNIQUE(user_id, action_name)
);

CREATE TABLE IF NOT EXISTS exercise_muscle_mappings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_system TEXT NOT NULL,
  source_action_name TEXT NOT NULL,
  primary_group TEXT NOT NULL,
  secondary_groups_json TEXT,
  source_type TEXT NOT NULL,
  confidence REAL,
  rationale TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, source_system, source_action_name)
);

CREATE TABLE IF NOT EXISTS garmin_import_files (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  original_filename TEXT NOT NULL,
  stored_zip_path TEXT NOT NULL,
  stored_fit_path TEXT,
  file_hash TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL,
  error_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_garmin_import_hash ON garmin_import_files(user_id, file_hash);

CREATE TABLE IF NOT EXISTS garmin_activities (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  import_file_id TEXT NOT NULL REFERENCES garmin_import_files(id),
  activity_unique_key TEXT NOT NULL,
  fit_start_time TEXT NOT NULL,
  local_date TEXT,
  sport TEXT,
  sub_sport TEXT,
  activity_family TEXT NOT NULL,
  activity_variant TEXT NOT NULL,
  elapsed_seconds REAL,
  timer_seconds REAL,
  distance_m REAL,
  calories REAL,
  gps_available INTEGER NOT NULL DEFAULT 0,
  lap_count INTEGER,
  field_coverage_json TEXT NOT NULL,
  raw_summary_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, activity_unique_key)
);
CREATE INDEX IF NOT EXISTS idx_garmin_activities_time ON garmin_activities(user_id, fit_start_time);
CREATE INDEX IF NOT EXISTS idx_garmin_activities_type ON garmin_activities(user_id, activity_family, activity_variant);

CREATE TABLE IF NOT EXISTS garmin_laps (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  activity_id TEXT NOT NULL REFERENCES garmin_activities(id) ON DELETE CASCADE,
  lap_index INTEGER NOT NULL,
  start_time TEXT,
  elapsed_seconds REAL,
  timer_seconds REAL,
  distance_m REAL,
  avg_speed_mps REAL,
  avg_hr REAL,
  max_hr REAL,
  avg_cadence REAL,
  avg_power REAL,
  raw_json TEXT NOT NULL,
  UNIQUE(activity_id, lap_index)
);

CREATE TABLE IF NOT EXISTS running_activity_metrics (
  activity_id TEXT PRIMARY KEY REFERENCES garmin_activities(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  run_context TEXT NOT NULL,
  run_type TEXT NOT NULL,
  avg_pace_sec_per_km REAL,
  avg_speed_mps REAL,
  max_speed_mps REAL,
  avg_hr REAL,
  max_hr REAL,
  avg_cadence REAL,
  max_cadence REAL,
  avg_power REAL,
  max_power REAL,
  elevation_gain_m REAL,
  elevation_loss_m REAL,
  temperature_c REAL,
  temperature_source TEXT NOT NULL DEFAULT 'missing',
  metrics_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_config_versions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0,
  primary_goal TEXT NOT NULL,
  running_goal_text TEXT,
  strength_baseline_text TEXT,
  conflict_policy_text TEXT,
  uncertainties_text TEXT,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_at TEXT NOT NULL,
  details_json TEXT,
  UNIQUE(user_id, version_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_current ON goal_config_versions(user_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS rest_notes (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  affected_scope TEXT NOT NULL,
  note TEXT NOT NULL,
  tags_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rest_notes_range ON rest_notes(user_id, start_date, end_date);

CREATE TABLE IF NOT EXISTS analysis_reports (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goal_config_version_id TEXT NOT NULL REFERENCES goal_config_versions(id),
  covered_start_date TEXT NOT NULL,
  covered_end_date TEXT NOT NULL,
  status TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  reanalysis_of_report_id TEXT REFERENCES analysis_reports(id),
  model_provider TEXT,
  model_name TEXT,
  analysis_context_json TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  narrative_md TEXT NOT NULL,
  confidence_json TEXT,
  data_coverage_json TEXT,
  uncertainties_json TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON analysis_reports(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_range ON analysis_reports(user_id, covered_start_date, covered_end_date);

CREATE TABLE IF NOT EXISTS operation_logs (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  operation_type TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        seed_owner(conn)


def seed_owner(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (settings.owner_username,)).fetchone()
    if existing:
        return
    user_id = new_id()
    now = now_utc()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, status, created_at, updated_at) VALUES (?, ?, ?, 'owner', 'active', ?, ?)",
        (user_id, settings.owner_username, hash_password(settings.owner_password), now, now),
    )
    conn.execute(
        "INSERT INTO user_profiles (user_id, created_at, updated_at) VALUES (?, ?, ?)",
        (user_id, now, now),
    )


def row_to_dict(row: sqlite3.Row | None):
    return dict(row) if row is not None else None
