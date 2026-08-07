import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from research_brain.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    capture_date TEXT NOT NULL,
    method TEXT NOT NULL,
    segment TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    source_path TEXT,
    ingested_at TEXT NOT NULL,
    pipeline_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS problem_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    priority_score REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_area_id INTEGER NOT NULL REFERENCES problem_areas(id),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    support_count INTEGER NOT NULL DEFAULT 0,
    contradict_count INTEGER NOT NULL DEFAULT 0,
    confidence_score REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS evidence_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id INTEGER NOT NULL REFERENCES insights(id),
    capture_id INTEGER NOT NULL REFERENCES captures(id),
    stance TEXT NOT NULL CHECK(stance IN ('support', 'contradict', 'neutral')),
    quote TEXT NOT NULL,
    signal_summary TEXT NOT NULL,
    severity REAL NOT NULL,
    segment TEXT NOT NULL,
    method TEXT NOT NULL,
    directness TEXT NOT NULL DEFAULT 'direct_quote' CHECK(directness IN ('direct_quote', 'secondhand')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_area_id INTEGER NOT NULL REFERENCES problem_areas(id),
    statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypothesis_insight_links (
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
    insight_id INTEGER NOT NULL REFERENCES insights(id),
    PRIMARY KEY (hypothesis_id, insight_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_links_insight ON evidence_links(insight_id);
CREATE INDEX IF NOT EXISTS idx_insights_problem_area ON insights(problem_area_id);
CREATE INDEX IF NOT EXISTS idx_hypotheses_problem_area ON hypotheses(problem_area_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------

def insert_capture(
    conn: sqlite3.Connection,
    *,
    title: str,
    capture_date: str,
    method: str,
    segment: str,
    raw_text: str,
    source_path: str | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO captures (title, capture_date, method, segment, raw_text, source_path, ingested_at, pipeline_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (title, capture_date, method, segment, raw_text, source_path, now_iso()),
    )
    return cur.lastrowid


def set_capture_status(conn: sqlite3.Connection, capture_id: int, status: str) -> None:
    conn.execute("UPDATE captures SET pipeline_status = ? WHERE id = ?", (status, capture_id))


def get_capture(conn: sqlite3.Connection, capture_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()


# ---------------------------------------------------------------------------
# Problem areas
# ---------------------------------------------------------------------------

def list_problem_areas(conn: sqlite3.Connection, order_by_priority: bool = False) -> list[sqlite3.Row]:
    order = "ORDER BY priority_score DESC" if order_by_priority else "ORDER BY id"
    return conn.execute(f"SELECT * FROM problem_areas {order}").fetchall()


def get_problem_area(conn: sqlite3.Connection, problem_area_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM problem_areas WHERE id = ?", (problem_area_id,)).fetchone()


def insert_problem_area(conn: sqlite3.Connection, *, title: str, description: str | None) -> int:
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO problem_areas (title, description, priority_score, created_at, updated_at)
           VALUES (?, ?, 0.0, ?, ?)""",
        (title, description, ts, ts),
    )
    return cur.lastrowid


def update_problem_area_priority(conn: sqlite3.Connection, problem_area_id: int, priority_score: float) -> None:
    conn.execute(
        "UPDATE problem_areas SET priority_score = ?, updated_at = ? WHERE id = ?",
        (priority_score, now_iso(), problem_area_id),
    )


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def list_active_insights(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, problem_area_id, title, summary FROM insights WHERE status = 'active' ORDER BY id"
    ).fetchall()


def list_insights(
    conn: sqlite3.Connection, problem_area_id: int | None = None, order_by: str = "id"
) -> list[sqlite3.Row]:
    order = {
        "confidence": "ORDER BY confidence_score DESC",
        "recency": "ORDER BY updated_at DESC",
    }.get(order_by, "ORDER BY id")
    if problem_area_id is not None:
        return conn.execute(
            f"SELECT * FROM insights WHERE problem_area_id = ? {order}", (problem_area_id,)
        ).fetchall()
    return conn.execute(f"SELECT * FROM insights {order}").fetchall()


def get_insight(conn: sqlite3.Connection, insight_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()


def insert_insight(
    conn: sqlite3.Connection, *, problem_area_id: int, title: str, summary: str
) -> int:
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO insights (problem_area_id, title, summary, support_count, contradict_count,
                                  confidence_score, created_at, updated_at, status)
           VALUES (?, ?, ?, 0, 0, 0.0, ?, ?, 'active')""",
        (problem_area_id, title, summary, ts, ts),
    )
    return cur.lastrowid


def update_insight_scores(
    conn: sqlite3.Connection, insight_id: int, *, support_count: int, contradict_count: int, confidence_score: float
) -> None:
    conn.execute(
        """UPDATE insights SET support_count = ?, contradict_count = ?, confidence_score = ?, updated_at = ?
           WHERE id = ?""",
        (support_count, contradict_count, confidence_score, now_iso(), insight_id),
    )


# ---------------------------------------------------------------------------
# Evidence links
# ---------------------------------------------------------------------------

def insert_evidence_link(
    conn: sqlite3.Connection,
    *,
    insight_id: int,
    capture_id: int,
    stance: str,
    quote: str,
    signal_summary: str,
    severity: float,
    segment: str,
    method: str,
    directness: str = "direct_quote",
) -> int:
    cur = conn.execute(
        """INSERT INTO evidence_links (insight_id, capture_id, stance, quote, signal_summary, severity,
                                        segment, method, directness, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (insight_id, capture_id, stance, quote, signal_summary, severity, segment, method, directness, now_iso()),
    )
    return cur.lastrowid


def get_evidence_links_for_insight(conn: sqlite3.Connection, insight_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evidence_links WHERE insight_id = ? ORDER BY created_at", (insight_id,)
    ).fetchall()


def get_evidence_links_for_problem_area(conn: sqlite3.Connection, problem_area_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT el.* FROM evidence_links el
           JOIN insights i ON i.id = el.insight_id
           WHERE i.problem_area_id = ?
           ORDER BY el.created_at""",
        (problem_area_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Hypotheses
# ---------------------------------------------------------------------------

def list_hypotheses(conn: sqlite3.Connection, problem_area_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM hypotheses WHERE problem_area_id = ? ORDER BY id", (problem_area_id,)
    ).fetchall()


def insert_hypothesis(conn: sqlite3.Connection, *, problem_area_id: int, statement: str) -> int:
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO hypotheses (problem_area_id, statement, status, created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?)""",
        (problem_area_id, statement, ts, ts),
    )
    return cur.lastrowid


def update_hypothesis_statement(conn: sqlite3.Connection, hypothesis_id: int, statement: str) -> None:
    conn.execute(
        "UPDATE hypotheses SET statement = ?, updated_at = ? WHERE id = ?",
        (statement, now_iso(), hypothesis_id),
    )


def link_hypothesis_insight(conn: sqlite3.Connection, hypothesis_id: int, insight_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO hypothesis_insight_links (hypothesis_id, insight_id) VALUES (?, ?)",
        (hypothesis_id, insight_id),
    )


def get_motivating_insights(conn: sqlite3.Connection, hypothesis_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT i.* FROM insights i
           JOIN hypothesis_insight_links hil ON hil.insight_id = i.id
           WHERE hil.hypothesis_id = ?
           ORDER BY i.confidence_score DESC""",
        (hypothesis_id,),
    ).fetchall()


def list_all_hypotheses_by_priority(
    conn: sqlite3.Connection, problem_area_id: int | None = None, status: str = "active"
) -> list[sqlite3.Row]:
    """All hypotheses joined with their problem area, ordered by problem-area priority
    (highest first) then hypothesis id — the "what should product look at first" view."""
    query = """
        SELECT h.*, pa.title AS problem_area_title, pa.priority_score AS problem_area_priority
        FROM hypotheses h
        JOIN problem_areas pa ON pa.id = h.problem_area_id
        WHERE h.status = ?
    """
    params: list = [status]
    if problem_area_id is not None:
        query += " AND h.problem_area_id = ?"
        params.append(problem_area_id)
    query += " ORDER BY pa.priority_score DESC, h.problem_area_id, h.id"
    return conn.execute(query, params).fetchall()
