import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.getcwd(), "evaluations.db")

def _connect():
    """Return a sqlite3 connection. Caller may use context manager."""
    return sqlite3.connect(DB_PATH)

def init_db():
    """Create tables for evaluations and users if they don't exist."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                jd TEXT,
                result_json TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password_hash TEXT,
                fullname TEXT,
                email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

# -----------------------
# Evaluations helpers
# -----------------------
def save_evaluation(filename, jd, result_json):
    """Insert evaluation JSON for a given filename and job description."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO evaluations (filename, jd, result_json) VALUES (?, ?, ?)",
            (filename, jd, result_json)
        )

def fetch_all():
    """Return all evaluations as a list of rows (id, filename, date, result_json), newest first."""
    with _connect() as conn:
        return conn.execute(
            "SELECT id, filename, date, result_json FROM evaluations ORDER BY date DESC"
        ).fetchall()

def fetch_latest_by_filename(filename):
    """Return the most recent evaluation row (result_json) for a given filename, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT result_json FROM evaluations WHERE filename = ? ORDER BY id DESC LIMIT 1",
            (filename,)
        ).fetchone()
    return row[0] if row else None

# -----------------------
# User management helpers
# -----------------------
def create_user(username, password, fullname=None, email=None):
    """
    Create a new user with a hashed password.
    Returns True if created successfully, False if username already exists.
    """
    password_hash = generate_password_hash(password)
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, fullname, email) VALUES (?, ?, ?, ?)",
                (username, password_hash, fullname, email)
            )
        return True
    except sqlite3.IntegrityError:
        return False

def find_user_by_id(user_id):
    """
    Return a tuple (id, username, password_hash, fullname, email) or None.
    Matches the shape expected by app's user loader.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, fullname, email FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    return row

def find_user_by_username(username):
    """
    Return a tuple (id, username, password_hash, fullname, email) or None.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, fullname, email FROM users WHERE username = ?",
            (username,)
        ).fetchone()
    return row

def verify_user_password(username, password):
    """Return True if username exists and password matches, else False."""
    row = find_user_by_username(username)
    if not row:
        return False
    stored_hash = row[2]
    return check_password_hash(stored_hash, password)
