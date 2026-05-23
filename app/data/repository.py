from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Callable
import json
import sqlite3
import threading
import uuid

from PySide6.QtCore import QObject, Signal

from app.data.entities import CalendarEventItem, NotificationItem, PluginItem, SessionLogItem, TaskItem, FocusTickItem, UserProfileItem

CURRENT_SCHEMA_VERSION = 6


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_meta (
    plugin_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    estimated_minutes INTEGER NOT NULL DEFAULT 30,
    due_at TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'todo',
    scheduled_start TEXT,
    scheduled_end TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    energy_cost INTEGER NOT NULL DEFAULT 3,
    focus_score_hint REAL,
    recurrence_rule TEXT,
    parent_task_id TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_start, scheduled_end);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    created_at TEXT NOT NULL,
    read_at TEXT,
    action_key TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at DESC);

CREATE TABLE IF NOT EXISTS plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'local',
    task_id TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    notes TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_window ON calendar_events(starts_at, ends_at);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mode TEXT NOT NULL DEFAULT 'focus',
    planned_task_id TEXT,
    outcome TEXT NOT NULL DEFAULT 'running',
    focus_score_avg REAL,
    distraction_events INTEGER NOT NULL DEFAULT 0,
    absent_seconds INTEGER NOT NULL DEFAULT 0,
    target_minutes INTEGER,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at DESC);


CREATE TABLE IF NOT EXISTS focus_ticks (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    p_drift_mean REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    session_id TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_focus_ticks_started_at ON focus_ticks(started_at);
CREATE INDEX IF NOT EXISTS idx_focus_ticks_session_id ON focus_ticks(session_id);
CREATE INDEX IF NOT EXISTS idx_focus_ticks_session_window ON focus_ticks(session_id, started_at, ended_at);



CREATE TABLE IF NOT EXISTS coach_reports (
    id TEXT PRIMARY KEY,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_coach_reports_week ON coach_reports(week_start, week_end);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    avatar_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
"""


class ThreadSafeConnection:
    def __init__(self, db_path: Path) -> None:
        super().__setattr__("_db_path", db_path)
        super().__setattr__("_local", threading.local())

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(str(self._db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return self._local.conn

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._conn, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                return attr(*args, **kwargs)
            return wrapper
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._conn, name, value)


class SqliteRepository(QObject):
    tasks_changed = Signal()
    notifications_changed = Signal()
    sessions_changed = Signal()
    calendar_changed = Signal()
    plugins_changed = Signal()
    profile_changed = Signal()

    def __init__(self, database_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.RLock()
        self._db_path = Path(database_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = ThreadSafeConnection(self._db_path)
        self._plugin_migrations: dict[str, Callable[[sqlite3.Connection, int], int]] = {}
        self._init_schema()

    @contextmanager
    def transaction(self):
        with self._lock:
            in_trans = self._conn.in_transaction
            if not in_trans:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                if not in_trans:
                    self._conn.commit()
            except Exception:
                if not in_trans:
                    self._conn.rollback()
                raise

    def close(self) -> None:
        try:
            self._conn.execute("PRAGMA optimize")
        except Exception:
            pass
        self._conn.close()

    def _init_schema(self) -> None:
        # Execute schema SQL, but handle the case where task_history has old schema
        # We need to execute statements individually to handle errors gracefully
        
        # First, check if task_history exists with old schema
        try:
            cols = self._conn.execute("PRAGMA table_info(task_history)").fetchall()
            col_names = [c[1] for c in cols]
            if cols and ('from_status' in col_names or 'to_status' in col_names or 'happened_at' in col_names):
                # Old schema detected, drop it so it can be recreated
                self._conn.execute("DROP TABLE IF EXISTS task_history")
                self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet
        
        # Now execute the schema
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        self._conn.execute(
            "INSERT OR IGNORE INTO app_meta(key, value) VALUES('schema_version', '0')"
        )
        self._conn.commit()
        self._run_core_migrations()

    def _run_core_migrations(self) -> None:
        # Define core migrations declaratively.
        # Each migration specifies:
        # - version: int
        # - check_fn: Callable[[], bool] -> returns True if migration features are ALREADY present
        # - migrate_fn: Callable[[], None] -> performs the schema alteration
        
        def check_v4() -> bool:
            try:
                self._conn.execute("SELECT 1 FROM focus_ticks LIMIT 1")
                return True
            except sqlite3.OperationalError:
                return False

        def migrate_v4() -> None:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS focus_ticks (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    p_drift_mean REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    session_id TEXT,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_focus_ticks_started_at ON focus_ticks(started_at);
                CREATE INDEX IF NOT EXISTS idx_focus_ticks_session_id ON focus_ticks(session_id);
                CREATE INDEX IF NOT EXISTS idx_focus_ticks_session_window ON focus_ticks(session_id, started_at, ended_at);
                """
            )

        def check_v5() -> bool:
            try:
                self._conn.execute("SELECT 1 FROM profiles LIMIT 1")
                return True
            except sqlite3.OperationalError:
                return False

        def migrate_v5() -> None:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    avatar_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )

        def check_v6() -> bool:
            try:
                self._conn.execute("SELECT target_minutes FROM sessions LIMIT 1")
                return True
            except sqlite3.OperationalError:
                return False

        def migrate_v6() -> None:
            try:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN target_minutes INTEGER")
            except sqlite3.OperationalError:
                pass  # already exists

        migrations = [
            (4, check_v4, migrate_v4),
            (5, check_v5, migrate_v5),
            (6, check_v6, migrate_v6),
        ]

        # Read current version baseline from DB
        try:
            row = self._conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
            db_version = int(row["value"]) if row else 0
        except sqlite3.OperationalError:
            db_version = 0

        max_applied_version = db_version

        for version, check_fn, migrate_fn in migrations:
            # We run the migration if:
            # 1. The db_version in app_meta is lower than this migration's version
            # OR 2. The structural check determines that the features are physically missing
            is_present = check_fn()
            
            if db_version < version or not is_present:
                if not is_present:
                    with self.transaction():
                        migrate_fn()
                max_applied_version = max(max_applied_version, version)
            else:
                max_applied_version = max(max_applied_version, version)

        # Sync the app_meta schema_version to match the actual verified max version
        if max_applied_version > db_version:
            with self.transaction():
                self._conn.execute(
                    "INSERT OR REPLACE INTO app_meta(key, value) VALUES('schema_version', ?)",
                    (str(max_applied_version),),
                )

    def register_migration(self, plugin_id: str, migrate_fn) -> None:
        self._plugin_migrations[plugin_id] = migrate_fn
        self.run_plugin_migration(plugin_id)

    def register_plugin_migration(self, plugin_id: str, migrate_fn) -> None:
        self.register_migration(plugin_id, migrate_fn)

    def run_plugin_migration(self, plugin_id: str) -> None:
        migrate_fn = self._plugin_migrations.get(plugin_id)
        if migrate_fn is None:
            return
        row = self._conn.execute(
            "SELECT schema_version FROM plugin_meta WHERE plugin_id=?",
            (plugin_id,),
        ).fetchone()
        current = int(row["schema_version"]) if row else 0
        new_version = migrate_fn(self._conn, current)
        now = _ts(_utcnow())
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO plugin_meta(plugin_id, schema_version, enabled, updated_at)
                VALUES(?, ?, 1, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                updated_at=excluded.updated_at
                """,
                (plugin_id, new_version, now),
            )

    def ensure_plugin_table(self, plugin_id: str, create_sql: str) -> None:
        if "create table" not in create_sql.lower():
            raise ValueError("Plugin create_sql must contain CREATE TABLE")
        with self.transaction():
            self._conn.executescript(create_sql)

    def set_task_plugin_value(self, task_id: str, plugin_id: str, key: str, value: Any) -> None:
        task = self.task_by_id(task_id)
        if not task:
            return
        plugins = task.meta.setdefault("plugins", {})
        payload = plugins.setdefault(plugin_id, {})
        payload[key] = value
        self.update_task(task)

    def get_task_plugin_payload(self, task_id: str, plugin_id: str) -> dict[str, Any]:
        task = self.task_by_id(task_id)
        if not task:
            return {}
        return task.meta.get("plugins", {}).get(plugin_id, {})

    def all_tasks(self) -> list[TaskItem]:
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY COALESCE(scheduled_start, due_at, created_at) ASC, priority DESC"
        ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def task_by_id(self, task_id: str) -> TaskItem | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def add_task(self, task: TaskItem) -> TaskItem:
        now = _utcnow()
        if task.created_at is None:
            task.created_at = now
        if task.updated_at is None:
            task.updated_at = now
        with self.transaction():
            self._insert_task(task)
            self._insert_task_history(task.id, "created", None, task.status, payload={"title": task.title})
        self.tasks_changed.emit()
        return task

    def create_task(
        self,
        title: str,
        description: str = "",
        estimated_minutes: int = 30,
        tags: list[str] | None = None,
        parent_task_id: str | None = None,
        energy_cost: int = 3,
        source: str = "manual",
        **extra: Any,
    ) -> TaskItem:
        now = _utcnow()
        task = TaskItem(
            id=extra.get("id") or f"task-{uuid.uuid4().hex[:12]}",
            title=title,
            description=description,
            estimated_minutes=estimated_minutes,
            due_at=extra.get("due_at"),
            priority=int(extra.get("priority", 3) or 3),
            tags=tags or [],
            status=extra.get("status", "todo"),
            scheduled_start=extra.get("scheduled_start"),
            scheduled_end=extra.get("scheduled_end"),
            created_at=now,
            updated_at=now,
            completed_at=extra.get("completed_at"),
            source=source,
            energy_cost=energy_cost,
            focus_score_hint=extra.get("focus_score_hint"),
            recurrence_rule=extra.get("recurrence_rule"),
            parent_task_id=parent_task_id,
            meta=extra.get("meta") or {},
        )
        return self.add_task(task)

    def update_task(self, task: TaskItem) -> TaskItem:
        existing = self.task_by_id(task.id)
        if existing is None:
            raise KeyError(f"Task not found: {task.id}")
        task.updated_at = _utcnow()
        if task.status == "done" and task.completed_at is None:
            task.completed_at = task.updated_at
        if task.status != "done":
            task.completed_at = None
        with self.transaction():
            self._conn.execute(
                """
                UPDATE tasks SET
                title=?, description=?, estimated_minutes=?, due_at=?, priority=?, tags_json=?,
                status=?, scheduled_start=?, scheduled_end=?, updated_at=?, completed_at=?, source=?,
                energy_cost=?, focus_score_hint=?, recurrence_rule=?, parent_task_id=?, meta_json=?
                WHERE id=?
                """,
                (
                    task.title,
                    task.description,
                    task.estimated_minutes,
                    _ts(task.due_at),
                    task.priority,
                    _dumps(task.tags),
                    task.status,
                    _ts(task.scheduled_start),
                    _ts(task.scheduled_end),
                    _ts(task.updated_at),
                    _ts(task.completed_at),
                    task.source,
                    task.energy_cost,
                    task.focus_score_hint,
                    task.recurrence_rule,
                    task.parent_task_id,
                    _dumps(task.meta),
                    task.id,
                ),
            )
            self._insert_task_history(
                task.id,
                "updated",
                existing.status,
                task.status,
                payload={"title": task.title},
            )
        self.tasks_changed.emit()
        return task

    def delete_task(self, task_id: str) -> None:
        with self.transaction():
            self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self._insert_task_history(task_id, "deleted", None, None, payload={})
        self.tasks_changed.emit()

    def all_notifications(self, unread_only: bool = False, limit: int | None = None) -> list[NotificationItem]:
        query = "SELECT * FROM notifications"
        if unread_only:
            query += " WHERE read_at IS NULL"
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += f" LIMIT {limit}"
        rows = self._conn.execute(query).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def mark_notification_read(self, notification_id: str) -> bool:
        with self.transaction():
            result = self._conn.execute(
                "UPDATE notifications SET read_at=? WHERE id=?",
                (_ts(_utcnow()), notification_id),
            ).rowcount
        if result > 0:
            self.notifications_changed.emit()
        return result > 0

    def mark_all_notifications_read(self) -> int:
        with self.transaction():
            result = self._conn.execute(
                "UPDATE notifications SET read_at=? WHERE read_at IS NULL",
                (_ts(_utcnow()),),
            ).rowcount
        if result > 0:
            self.notifications_changed.emit()
        return result

    def delete_notification(self, notification_id: str) -> bool:
        with self.transaction():
            result = self._conn.execute(
                "DELETE FROM notifications WHERE id=?",
                (notification_id,),
            ).rowcount
        if result > 0:
            self.notifications_changed.emit()
        return result > 0

    def notification_by_id(self, notification_id: str) -> NotificationItem | None:
        row = self._conn.execute(
            "SELECT * FROM notifications WHERE id=?",
            (notification_id,),
        ).fetchone()
        return self._notification_from_row(row) if row else None

    def recent_notification_summary(self, hours: int) -> dict[str, int]:
        from datetime import timedelta
        cutoff_dt = _utcnow() - timedelta(hours=hours)
        rows = self._conn.execute(
            "SELECT level, COUNT(*) as count FROM notifications WHERE created_at >= ? GROUP BY level",
            (_ts(cutoff_dt),),
        ).fetchall()
        return {row["level"]: row["count"] for row in rows}

    def add_notification(self, notification: NotificationItem) -> NotificationItem:
        created_at = notification.created_at or _utcnow()
        with self.transaction():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO notifications(
                    id, title, message, level, created_at, read_at, action_key, meta_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.id,
                    notification.title,
                    notification.message,
                    notification.level,
                    _ts(created_at),
                    _ts(notification.read_at),
                    notification.action_key,
                    _dumps(notification.meta),
                ),
            )
        self.notifications_changed.emit()
        return notification

    def all_sessions(self) -> list[SessionLogItem]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
        return [self._session_from_row(row) for row in rows]

    def session_by_id(self, session_id: str) -> SessionLogItem | None:
        """Single indexed row lookup — O(1) compared to scanning all_sessions()."""
        row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return self._session_from_row(row) if row else None

    def add_session(self, session: SessionLogItem) -> SessionLogItem:
        with self.transaction():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO sessions(
                    id, started_at, ended_at, mode, planned_task_id, outcome,
                    focus_score_avg, distraction_events, absent_seconds, target_minutes, meta_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    _ts(session.started_at),
                    _ts(session.ended_at),
                    session.mode,
                    session.planned_task_id,
                    session.outcome,
                    session.focus_score_avg,
                    session.distraction_events,
                    session.absent_seconds,
                    getattr(session, "target_minutes", None),
                    _dumps(session.meta),
                ),
            )
        self.sessions_changed.emit()
        return session

    def add_calendar_event(self, event: CalendarEventItem) -> CalendarEventItem:
        with self.transaction():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO calendar_events(
                    id, title, starts_at, ends_at, source, task_id, all_day, location, notes, meta_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.title,
                    _ts(event.starts_at),
                    _ts(event.ends_at),
                    event.source,
                    event.task_id,
                    1 if event.all_day else 0,
                    event.location,
                    event.notes,
                    _dumps(event.meta),
                ),
            )
        self.calendar_changed.emit()
        return event

    def all_calendar_events(self) -> list[CalendarEventItem]:
        rows = self._conn.execute(
            "SELECT * FROM calendar_events ORDER BY starts_at ASC"
        ).fetchall()
        return [self._calendar_from_row(row) for row in rows]

    def add_plugin(self, plugin: PluginItem) -> PluginItem:
        with self.transaction():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO plugins(id, name, version, description, enabled, meta_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    plugin.id,
                    plugin.name,
                    plugin.version,
                    plugin.description,
                    1 if plugin.enabled else 0,
                    _dumps(plugin.meta),
                ),
            )
        self.plugins_changed.emit()
        return plugin

    def all_plugins(self) -> list[PluginItem]:
        rows = self._conn.execute("SELECT * FROM plugins ORDER BY name ASC").fetchall()
        return [self._plugin_from_row(row) for row in rows]

    def add_coach_report(self, week_start: datetime, week_end: datetime, summary_text: str, meta: dict[str, Any] | None = None) -> None:
        report_id = f"coach-{uuid.uuid4().hex[:12]}"
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO coach_reports(id, week_start, week_end, created_at, summary_text, meta_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    _ts(week_start),
                    _ts(week_end),
                    _ts(_utcnow()),
                    summary_text,
                    _dumps(meta or {}),
                ),
            )

    def coach_reports_between(self, week_start: datetime, week_end: datetime) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM coach_reports
            WHERE week_start >= ? AND week_end <= ?
            ORDER BY week_start DESC
            """,
            (_ts(week_start), _ts(week_end)),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_coach_report(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM coach_reports
            ORDER BY created_at DESC, week_end DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def reset_all_data(self) -> None:
        with self.transaction():
            for table in [
                "task_history",
                "tasks",
                "notifications",
                "calendar_events",
                "sessions",
                "focus_ticks",
                "plugins",
                "coach_reports",
                "plugin_meta",
            ]:
                self._conn.execute(f"DELETE FROM {table}")
        self.tasks_changed.emit()
        self.notifications_changed.emit()
        self.sessions_changed.emit()
        self.calendar_changed.emit()
        self.plugins_changed.emit()

    def seed_fake_data(self) -> None:
        if not self.all_tasks():
            self.create_task(
                title="Finish OS assignment 2",
                description="Kernel scheduling and report polish.",
                estimated_minutes=120,
                tags=["os", "study"],
                priority=4,
            )
            self.create_task(
                title="Prepare weekly review",
                description="Summarize focus patterns.",
                estimated_minutes=45,
                tags=["review"],
                priority=3,
            )
        if not self.all_sessions():
            now = _utcnow()
            self.add_session(
                SessionLogItem(
                    id=f"session-{uuid.uuid4().hex[:12]}",
                    started_at=now,
                    ended_at=now,
                    mode="focus",
                    outcome="completed",
                    focus_score_avg=0.78,
                    distraction_events=2,
                    absent_seconds=90,
                    meta={"seeded": True},
                )
            )
        if not self.all_notifications():
            self.add_notification(
                NotificationItem(
                    id=f"notif-{uuid.uuid4().hex[:12]}",
                    title="Welcome",
                    message="Demo data seeded successfully.",
                    level="info",
                    created_at=_utcnow(),
                    meta={"seeded": True},
                )
            )

    def _insert_task(self, task: TaskItem) -> None:
        self._conn.execute(
            """
            INSERT INTO tasks(
                id, title, description, estimated_minutes, due_at, priority, tags_json,
                status, scheduled_start, scheduled_end, created_at, updated_at, completed_at,
                source, energy_cost, focus_score_hint, recurrence_rule, parent_task_id, meta_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.title,
                task.description,
                task.estimated_minutes,
                _ts(task.due_at),
                task.priority,
                _dumps(task.tags),
                task.status,
                _ts(task.scheduled_start),
                _ts(task.scheduled_end),
                _ts(task.created_at),
                _ts(task.updated_at),
                _ts(task.completed_at),
                task.source,
                task.energy_cost,
                task.focus_score_hint,
                task.recurrence_rule,
                task.parent_task_id,
                _dumps(task.meta),
            ),
        )

    def _insert_task_history(
        self,
        task_id: str,
        event_type: str,
        previous_status: str | None,
        new_status: str | None,
        payload: dict[str, Any],
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO task_history(task_id, event_type, previous_status, new_status, payload_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (task_id, event_type, previous_status, new_status, _dumps(payload), _ts(_utcnow())),
        )

    def _task_from_row(self, row: sqlite3.Row) -> TaskItem:
        return TaskItem(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            estimated_minutes=int(row["estimated_minutes"]),
            due_at=_dt(row["due_at"]),
            priority=int(row["priority"]),
            tags=_loads(row["tags_json"], []),
            status=row["status"],
            scheduled_start=_dt(row["scheduled_start"]),
            scheduled_end=_dt(row["scheduled_end"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            completed_at=_dt(row["completed_at"]),
            source=row["source"],
            energy_cost=int(row["energy_cost"]),
            focus_score_hint=row["focus_score_hint"],
            recurrence_rule=row["recurrence_rule"],
            parent_task_id=row["parent_task_id"],
            meta=_loads(row["meta_json"], {}),
        )

    def _notification_from_row(self, row: sqlite3.Row) -> NotificationItem:
        return NotificationItem(
            id=row["id"],
            title=row["title"],
            message=row["message"],
            level=row["level"],
            created_at=_dt(row["created_at"]),
            read_at=_dt(row["read_at"]),
            action_key=row["action_key"],
            meta=_loads(row["meta_json"], {}),
        )

    def _plugin_from_row(self, row: sqlite3.Row) -> PluginItem:
        return PluginItem(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            description=row["description"],
            enabled=bool(row["enabled"]),
            meta=_loads(row["meta_json"], {}),
        )

    def _calendar_from_row(self, row: sqlite3.Row) -> CalendarEventItem:
        return CalendarEventItem(
            id=row["id"],
            title=row["title"],
            starts_at=_dt(row["starts_at"]) or _utcnow(),
            ends_at=_dt(row["ends_at"]) or _utcnow(),
            source=row["source"],
            task_id=row["task_id"],
            all_day=bool(row["all_day"]),
            location=row["location"],
            notes=row["notes"],
            meta=_loads(row["meta_json"], {}),
        )

    def _session_from_row(self, row: sqlite3.Row) -> SessionLogItem:
        return SessionLogItem(
            id=row["id"],
            started_at=_dt(row["started_at"]) or _utcnow(),
            ended_at=_dt(row["ended_at"]),
            mode=row["mode"],
            planned_task_id=row["planned_task_id"],
            outcome=row["outcome"],
            focus_score_avg=row["focus_score_avg"],
            distraction_events=int(row["distraction_events"]),
            absent_seconds=int(row["absent_seconds"]),
            target_minutes=row["target_minutes"] if "target_minutes" in row.keys() else None,
            meta=_loads(row["meta_json"], {}),
        )
    def add_focus_tick(self, tick: FocusTickItem) -> FocusTickItem:
        with self.transaction():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO focus_ticks(
                    id, started_at, ended_at, p_drift_mean, sample_count, session_id, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tick.id,
                    _ts(tick.started_at),
                    _ts(tick.ended_at),
                    float(tick.p_drift_mean),
                    int(tick.sample_count),
                    tick.session_id,
                    _dumps(tick.meta),
                ),
            )
        return tick

    def get_profile(self) -> UserProfileItem:
        row = self._conn.execute("SELECT * FROM profiles LIMIT 1").fetchone()
        if row:
            return UserProfileItem(
                id=row["id"],
                display_name=row["display_name"],
                avatar_path=row["avatar_path"],
                created_at=_dt(row["created_at"]),
                updated_at=_dt(row["updated_at"]),
                meta=_loads(row["meta_json"], {}),
            )
        else:
            # Default profile
            now = _utcnow()
            return UserProfileItem(
                id="default_profile",
                display_name="abte user",
                avatar_path="",
                created_at=now,
                updated_at=now,
            )

    def update_profile(self, profile: UserProfileItem) -> UserProfileItem:
        now = _utcnow()
        if not profile.created_at:
            profile.created_at = now
        profile.updated_at = now

        with self.transaction():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO profiles(
                    id, display_name, avatar_path, created_at, updated_at, meta_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id,
                    profile.display_name,
                    profile.avatar_path,
                    _ts(profile.created_at),
                    _ts(profile.updated_at),
                    _dumps(profile.meta),
                ),
            )
        self.profile_changed.emit()
        return profile

    def focus_ticks_between(
        self,
        start: datetime,
        end: datetime,
        *,
        session_id: str | None = None,
    ) -> list[FocusTickItem]:
        if session_id:
            rows = self._conn.execute(
                """
                SELECT * FROM focus_ticks
                WHERE started_at >= ? AND ended_at <= ? AND session_id = ?
                ORDER BY started_at ASC
                """,
                (_ts(start), _ts(end), session_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM focus_ticks
                WHERE started_at >= ? AND ended_at <= ?
                ORDER BY started_at ASC
                """,
                (_ts(start), _ts(end)),
            ).fetchall()
        return [self._focus_tick_from_row(row) for row in rows]

    def _focus_tick_from_row(self, row: sqlite3.Row) -> FocusTickItem:
        return FocusTickItem(
            id=row["id"],
            started_at=_dt(row["started_at"]) or _utcnow(),
            ended_at=_dt(row["ended_at"]) or _utcnow(),
            p_drift_mean=float(row["p_drift_mean"]),
            sample_count=int(row["sample_count"]),
            session_id=row["session_id"],
            meta=_loads(row["meta_json"], {}),
        )