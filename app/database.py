"""SQLite persistence helpers for the local single-user web app."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import shutil
from pathlib import Path
from typing import Any, Iterable

from core.logging_config import LOG_INFO, LOG_DEBUG


BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = BASE_DIR / "data"
if os.environ.get("DMPROJECT_DATA_DIR"):
    DATA_DIR = Path(os.environ["DMPROJECT_DATA_DIR"])
elif os.name == "nt":
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "DMProject"
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "dmproject"
DB_PATH = DATA_DIR / "dmproject.db"
SCHEMA_VERSION = 9
_SCHEMA_READY = False
_INIT_IN_PROGRESS = False


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row-style access."""
    _ensure_data_dir()
    # 10s busy timeout: the web app, the fuel-price thread and the notifier
    # process all write to this file.
    connection = sqlite3.connect(DB_PATH, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_data_dir() -> None:
    """Create the user data directory and migrate the legacy repo-local DB once."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.environ.get("DMPROJECT_DATA_DIR"):
        return
    legacy_db_path = LEGACY_DATA_DIR / "dmproject.db"
    if not DB_PATH.exists() and legacy_db_path.exists():
        shutil.copyfile(legacy_db_path, DB_PATH)
    if DB_PATH.exists():
        try:
            os.chmod(DB_PATH, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def init_db() -> None:
    """Create the application schema when it does not exist yet."""
    global _SCHEMA_READY, _INIT_IN_PROGRESS
    if _INIT_IN_PROGRESS:
        return
    _INIT_IN_PROGRESS = True
    LOG_INFO(f"initialising database at {DB_PATH}")
    try:
        with get_connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address_text TEXT,
                location_name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                pickup_mode TEXT NOT NULL DEFAULT 'home',
                pickup_location_name TEXT,
                pickup_address_text TEXT,
                pickup_latitude REAL,
                pickup_longitude REAL,
                has_car INTEGER NOT NULL DEFAULT 0,
                vehicle_type TEXT NOT NULL DEFAULT 'car',
                fuel_type TEXT,
                consumption_l_per_100km REAL,
                total_seats INTEGER,
                habit_score REAL NOT NULL DEFAULT 0,
                role_tag TEXT NOT NULL DEFAULT 'standard',
                availability_tag TEXT NOT NULL DEFAULT 'available',
                pickup_flexible INTEGER NOT NULL DEFAULT 1,
                priority_rank INTEGER NOT NULL DEFAULT 100,
                active_in_trip INTEGER NOT NULL DEFAULT 1,
                outbound_earliest_time TEXT,
                outbound_latest_time TEXT,
                return_earliest_time TEXT,
                return_latest_time TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS geocode_cache (
                query TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reverse_geocode_cache (
                cache_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS saved_destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address_text TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                target_arrival_time TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS saved_meetup_spots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address_text TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                is_free_parking INTEGER NOT NULL DEFAULT 1,
                has_ev_charging INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS saved_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trip_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_name TEXT NOT NULL,
                trip_date TEXT,
                preset_name TEXT,
                notes TEXT,
                snapshot_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trip_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                trip_name TEXT NOT NULL,
                destination_name TEXT NOT NULL,
                destination_address_text TEXT,
                destination_latitude REAL NOT NULL,
                destination_longitude REAL NOT NULL,
                target_arrival_time TEXT,
                deadline_at TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trip_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invite_id INTEGER NOT NULL,
                response_token TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'submitted',
                name TEXT NOT NULL,
                address_text TEXT,
                location_name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                pickup_mode TEXT NOT NULL DEFAULT 'home',
                pickup_location_name TEXT,
                pickup_address_text TEXT,
                pickup_latitude REAL,
                pickup_longitude REAL,
                pickup_flexible INTEGER NOT NULL DEFAULT 1,
                has_car INTEGER NOT NULL DEFAULT 0,
                vehicle_type TEXT NOT NULL DEFAULT 'car',
                fuel_type TEXT,
                consumption_l_per_100km REAL,
                total_seats INTEGER,
                role_tag TEXT NOT NULL DEFAULT 'standard',
                outbound_earliest_time TEXT,
                outbound_latest_time TEXT,
                return_earliest_time TEXT,
                return_latest_time TEXT,
                imported_participant_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(invite_id) REFERENCES trip_invites(id)
            );

            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                dedupe_key TEXT,
                delivery_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                delivered_at TEXT
            );
            """
            )
            _ensure_column(connection, "participants", "address_text", "TEXT")
            _ensure_column(connection, "participants", "pickup_mode", "TEXT NOT NULL DEFAULT 'home'")
            _ensure_column(connection, "participants", "pickup_location_name", "TEXT")
            _ensure_column(connection, "participants", "pickup_address_text", "TEXT")
            _ensure_column(connection, "participants", "pickup_latitude", "REAL")
            _ensure_column(connection, "participants", "pickup_longitude", "REAL")
            _ensure_column(connection, "participants", "role_tag", "TEXT NOT NULL DEFAULT 'standard'")
            _ensure_column(connection, "participants", "availability_tag", "TEXT NOT NULL DEFAULT 'available'")
            _ensure_column(connection, "participants", "pickup_flexible", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(connection, "participants", "priority_rank", "INTEGER NOT NULL DEFAULT 100")
            _ensure_column(connection, "participants", "active_in_trip", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(connection, "participants", "outbound_earliest_time", "TEXT")
            _ensure_column(connection, "participants", "outbound_latest_time", "TEXT")
            _ensure_column(connection, "participants", "return_earliest_time", "TEXT")
            _ensure_column(connection, "participants", "return_latest_time", "TEXT")
            _ensure_column(connection, "participants", "force_drive_alone", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "participants", "vehicle_type", "TEXT NOT NULL DEFAULT 'car'")
            _ensure_column(connection, "trip_responses", "pickup_mode", "TEXT NOT NULL DEFAULT 'home'")
            _ensure_column(connection, "trip_responses", "pickup_location_name", "TEXT")
            _ensure_column(connection, "trip_responses", "pickup_address_text", "TEXT")
            _ensure_column(connection, "trip_responses", "pickup_latitude", "REAL")
            _ensure_column(connection, "trip_responses", "pickup_longitude", "REAL")
            _ensure_column(connection, "trip_responses", "pickup_flexible", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(connection, "trip_responses", "food_stop_vote", "TEXT")
            _ensure_column(connection, "trip_responses", "vehicle_type", "TEXT NOT NULL DEFAULT 'car'")
            _ensure_column(connection, "saved_destinations", "target_arrival_time", "TEXT")
            _ensure_column(connection, "trip_history", "preset_name", "TEXT")
            connection.execute(
                """
            DELETE FROM saved_destinations
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM saved_destinations
                GROUP BY lower(name), coalesce(lower(address_text), ''), round(latitude, 5), round(longitude, 5), coalesce(target_arrival_time, '')
            )
                """
            )
            connection.commit()
            _set_schema_version(connection, SCHEMA_VERSION)
        _SCHEMA_READY = True
        LOG_INFO(f"database schema ready — version {SCHEMA_VERSION}")
    finally:
        _INIT_IN_PROGRESS = False


def ensure_schema_ready() -> None:
    """Lazily create the schema for code paths that bypass FastAPI startup hooks."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    _ensure_data_dir()
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as connection:
                row = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'app_settings'
                    """
                ).fetchone()
            if row is not None:
                _SCHEMA_READY = True
                return
        except sqlite3.DatabaseError:
            pass
    init_db()


def _ensure_column(
    connection: sqlite3.Connection, table_name: str, column_name: str, column_type: str
) -> None:
    """Add a missing column when migrating an existing local database."""
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing_columns = {row["name"] for row in rows}
    if column_name not in existing_columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )
        connection.commit()


def _set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    """Persist the current schema version for lightweight migrations."""
    connection.execute(
        """
        INSERT INTO schema_meta(key, value)
        VALUES('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(version),),
    )
    connection.commit()


def fetch_all(query: str, parameters: Iterable[Any] = ()) -> list[sqlite3.Row]:
    """Run a query and return all rows."""
    ensure_schema_ready()
    with get_connection() as connection:
        cursor = connection.execute(query, tuple(parameters))
        return list(cursor.fetchall())


def fetch_one(query: str, parameters: Iterable[Any] = ()) -> sqlite3.Row | None:
    """Run a query and return the first row, if present."""
    ensure_schema_ready()
    with get_connection() as connection:
        cursor = connection.execute(query, tuple(parameters))
        return cursor.fetchone()


def execute(query: str, parameters: Iterable[Any] = ()) -> None:
    """Run a write statement."""
    LOG_DEBUG(f"SQL execute: {query.strip()[:80]}")
    ensure_schema_ready()
    with get_connection() as connection:
        connection.execute(query, tuple(parameters))
        connection.commit()


def execute_insert(query: str, parameters: Iterable[Any] = ()) -> int:
    """Run a write statement and return the inserted row id."""
    LOG_DEBUG(f"SQL insert: {query.strip()[:80]}")
    ensure_schema_ready()
    with get_connection() as connection:
        cursor = connection.execute(query, tuple(parameters))
        connection.commit()
        return int(cursor.lastrowid)


def set_setting(key: str, value: Any) -> None:
    """Persist a JSON-encoded application setting."""
    execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, json.dumps(value)),
    )


def get_setting(key: str, default: Any = None) -> Any:
    """Load and decode a JSON-encoded setting."""
    row = fetch_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    if row is None:
        return default
    return json.loads(row["value"])
