"""
Database layer — SQLite connection handling, schema, and row serialization.

The courses table carries a user_id: every course (and by cascade every
assessment, score and study log) belongs to exactly one user, so the API can
scope all reads and writes to the logged-in student.
"""

import sqlite3
import os
from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "planner.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_codes (
    email TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT,
    credit_hours REAL NOT NULL DEFAULT 3,
    weekly_workload_hours REAL NOT NULL DEFAULT 3,
    current_grade REAL,          -- 0-100, latest known grade/average in the course
    difficulty INTEGER NOT NULL DEFAULT 3   -- 1 (easy) - 5 (hard), self-rated
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'assignment',   -- assignment | exam | quiz | project
    due_date TEXT NOT NULL,                    -- ISO date YYYY-MM-DD
    weight REAL NOT NULL DEFAULT 10             -- % of final grade
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    label TEXT NOT NULL,          -- e.g. "Quiz 1", "Midterm"
    score REAL NOT NULL,          -- percentage 0-100
    date_taken TEXT NOT NULL      -- ISO date
);

CREATE TABLE IF NOT EXISTS study_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    hours REAL NOT NULL,
    log_date TEXT NOT NULL        -- ISO date
);

CREATE INDEX IF NOT EXISTS idx_courses_user ON courses(user_id);
CREATE INDEX IF NOT EXISTS idx_assessments_course ON assessments(course_id);
CREATE INDEX IF NOT EXISTS idx_scores_course ON scores(course_id);
CREATE INDEX IF NOT EXISTS idx_logs_course ON study_logs(course_id);
"""


def init_db():
    """Create tables if they don't exist yet. Called once at startup."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    # Older databases may predate the user_id column; add it if missing.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(courses)").fetchall()}
    if "user_id" not in cols:
        try:
            conn.execute("ALTER TABLE courses ADD COLUMN user_id INTEGER")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def get_db():
    """Return a per-request SQLite connection, stored on Flask's `g`."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exception=None):
    """Close the per-request connection. Registered as a teardown handler in app.py."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def rows_to_list(rows):
    return [row_to_dict(r) for r in rows]
