"""Chat session storage.

Sessions group messages so the UI can keep several conversations side by side.
The active session is stored in the database instead of the client, so the
voice path (ESP32 -> /file_recorded) writes into whatever session the web UI
currently has selected.
"""

import datetime
import logging
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "tasks.db"

DEFAULT_TITLE = "Nova sessão"
TITLE_MAX_CHARS = 48
ACTIVE_KEY = "active_session_id"

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    """Create the session tables and migrate pre-session messages."""
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        if "session_id" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN session_id INTEGER")
            logger.info("[sessions] added messages.session_id")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
        )

        orphans = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id IS NULL"
        ).fetchone()["n"]
        if orphans:
            first = conn.execute(
                "SELECT created_at FROM messages WHERE session_id IS NULL ORDER BY id LIMIT 1"
            ).fetchone()
            session_id = _insert_session(conn, "Conversa anterior", created_at=first["created_at"])
            conn.execute(
                "UPDATE messages SET session_id = ? WHERE session_id IS NULL", (session_id,)
            )
            logger.info(f"[sessions] migrated {orphans} messages into session {session_id}")

        conn.commit()
    finally:
        conn.close()


def _insert_session(conn: sqlite3.Connection, title: str, created_at: str | None = None) -> int:
    stamp = created_at or _now()
    cur = conn.execute(
        "INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
        (title, stamp, stamp),
    )
    return int(cur.lastrowid)


def _row_to_session(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row["message_count"] if "message_count" in row.keys() else 0,
    }


def list_sessions() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("""
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
            FROM sessions s
            ORDER BY s.updated_at DESC, s.id DESC
        """).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def get_session(session_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("""
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
            FROM sessions s WHERE s.id = ?
        """, (session_id,)).fetchone()
        return _row_to_session(row) if row else None
    finally:
        conn.close()


def create_session(title: str | None = None, activate: bool = True) -> dict:
    conn = _connect()
    try:
        session_id = _insert_session(conn, (title or DEFAULT_TITLE).strip() or DEFAULT_TITLE)
        if activate:
            _set_state(conn, ACTIVE_KEY, str(session_id))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"[sessions] created session {session_id}")
    return get_session(session_id)


def rename_session(session_id: int, title: str) -> dict | None:
    clean = title.strip()[:200] or DEFAULT_TITLE
    conn = _connect()
    try:
        cur = conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (clean, session_id))
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    return get_session(session_id)


def delete_session(session_id: int) -> int | None:
    """Delete a session and its messages. Returns the active session afterwards."""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if cur.rowcount == 0:
            return None
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"[sessions] deleted session {session_id}")
    return active_session_id()


def clear_session(session_id: int):
    conn = _connect()
    try:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id))
        conn.commit()
    finally:
        conn.close()


def _get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def active_session_id() -> int:
    """Current session, falling back to the newest one or a fresh session."""
    conn = _connect()
    try:
        raw = _get_state(conn, ACTIVE_KEY)
        if raw:
            row = conn.execute("SELECT id FROM sessions WHERE id = ?", (int(raw),)).fetchone()
            if row:
                return int(row["id"])
        row = conn.execute("SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT 1").fetchone()
        session_id = int(row["id"]) if row else _insert_session(conn, DEFAULT_TITLE)
        _set_state(conn, ACTIVE_KEY, str(session_id))
        conn.commit()
        return session_id
    finally:
        conn.close()


def set_active_session(session_id: int) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return False
        _set_state(conn, ACTIVE_KEY, str(session_id))
        conn.commit()
        return True
    finally:
        conn.close()


def resolve_session(session_id: int | None) -> int:
    """Validate a client-supplied session id, falling back to the active one."""
    if session_id is None:
        return active_session_id()
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return int(row["id"]) if row else active_session_id()


def _auto_title(conn: sqlite3.Connection, session_id: int, content: str):
    row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row or row["title"] not in (DEFAULT_TITLE, ""):
        return
    clean = " ".join(content.split())
    if not clean:
        return
    title = clean[:TITLE_MAX_CHARS] + ("…" if len(clean) > TITLE_MAX_CHARS else "")
    conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))


def save_message(role: str, content: str, session_id: int | None = None) -> int:
    session_id = resolve_session(session_id)
    conn = _connect()
    try:
        now = _now()
        conn.execute(
            "INSERT INTO messages (role, content, created_at, session_id) VALUES (?, ?, ?, ?)",
            (role, content, now, session_id),
        )
        if role == "user":
            _auto_title(conn, session_id, content)
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
    finally:
        conn.close()
    return session_id


def load_messages(session_id: int | None = None, limit: int = 100) -> list[dict]:
    session_id = resolve_session(session_id)
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()
