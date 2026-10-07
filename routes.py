"""
API routes — all Flask endpoints, registered onto the app in app.py.
Pure request/response wiring; the logic lives in db.py, auth.py,
risk_engine.py, planner.py and seed.py.

Every data endpoint is scoped to the authenticated user: courses carry a
user_id, and every query filters on it, so one student can never read or
modify another's records.
"""

from flask import Blueprint, jsonify, request
from datetime import date

from db import get_db, row_to_dict, rows_to_list
from auth import (
    current_user, destroy_session, bearer_token, create_login_code,
    create_session, get_or_create_user, is_valid_email, normalize_email,
    send_code_email, verify_login_code,
)
from risk_engine import build_risk_report
from planner import build_weekly_plan, generate_summary_text
from seed import seed_demo_data

api = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def require_user():
    """
    Returns (user, None) when authenticated, or (None, (response, status)).
    Every data route calls this first.
    """
    db = get_db()
    user = current_user(db, request)
    if not user:
        return None, (jsonify({"error": "Not authenticated"}), 401)
    return user, None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@api.route("/auth/request", methods=["POST"])
def auth_request():
    """Step 1 of login: validate the email and issue a 6-digit code."""
    data = request.get_json(force=True) or {}
    email = normalize_email(data.get("email"))
    if not is_valid_email(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    db = get_db()
    get_or_create_user(db, email, data.get("name"))
    code = create_login_code(db, email)
    sent = send_code_email(email, code)

    payload = {
        "message": f"We sent a login code to {email}.",
        "email": email,
        "code_delivered": sent,
    }
    # Demo mode: no mail provider is wired up, so return the code directly.
    if not sent:
        payload["demo_code"] = code
        payload["message"] = "Demo mode: use the code below to sign in."
    return jsonify(payload)


@api.route("/auth/verify", methods=["POST"])
def auth_verify():
    """Step 2 of login: exchange email + code for a session token."""
    data = request.get_json(force=True) or {}
    email = normalize_email(data.get("email"))
    code = data.get("code")
    db = get_db()

    ok, reason = verify_login_code(db, email, code)
    if not ok:
        return jsonify({"error": reason}), 400

    user = get_or_create_user(db, email, data.get("name"))
    token = create_session(db, user["id"])
    return jsonify({
        "token": token,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
    })


@api.route("/auth/me", methods=["GET"])
def auth_me():
    user, err = require_user()
    if err:
        return err
    return jsonify({"id": user["id"], "email": user["email"], "name": user["name"]})


@api.route("/auth/logout", methods=["POST"])
def auth_logout():
    destroy_session(get_db(), bearer_token(request))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@api.route("/courses", methods=["GET"])
def list_courses():
    user, err = require_user()
    if err:
        return err
    db = get_db()
    rows = db.execute(
        "SELECT * FROM courses WHERE user_id=? ORDER BY id", (user["id"],)
    ).fetchall()
    return jsonify(rows_to_list(rows))


@api.route("/courses", methods=["POST"])
def create_course():
    user, err = require_user()
    if err:
        return err
    data = request.get_json(force=True)
    db = get_db()
    cur = db.execute(
        """INSERT INTO courses (user_id, name, code, credit_hours, weekly_workload_hours, current_grade, difficulty)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            user["id"],
            data.get("name"),
            data.get("code", ""),
            float(data.get("credit_hours", 3)),
            float(data.get("weekly_workload_hours", 3)),
            data.get("current_grade"),
            int(data.get("difficulty", 3)),
        ),
    )
    db.commit()
    new_row = db.execute("SELECT * FROM courses WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(new_row)), 201


@api.route("/courses/<int:course_id>", methods=["PUT"])
def update_course(course_id):
    user, err = require_user()
    if err:
        return err
    data = request.get_json(force=True)
    db = get_db()
    existing = db.execute(
        "SELECT * FROM courses WHERE id=? AND user_id=?", (course_id, user["id"])
    ).fetchone()
    if not existing:
        return jsonify({"error": "Course not found"}), 404
    merged = {**row_to_dict(existing), **data}
    db.execute(
        """UPDATE courses SET name=?, code=?, credit_hours=?, weekly_workload_hours=?,
           current_grade=?, difficulty=? WHERE id=? AND user_id=?""",
        (
            merged["name"], merged["code"], float(merged["credit_hours"]),
            float(merged["weekly_workload_hours"]), merged["current_grade"],
            int(merged["difficulty"]), course_id, user["id"],
        ),
    )
    db.commit()
    updated = db.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
    return jsonify(row_to_dict(updated))


@api.route("/courses/<int:course_id>", methods=["DELETE"])
def delete_course(course_id):
    user, err = require_user()
    if err:
        return err
    db = get_db()
    db.execute("DELETE FROM courses WHERE id=? AND user_id=?", (course_id, user["id"]))
    db.commit()
    return jsonify({"deleted": course_id})


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------

def owns_course(db, user, course_id):
    return db.execute(
        "SELECT 1 FROM courses WHERE id=? AND user_id=?", (course_id, user["id"])
    ).fetchone() is not None


@api.route("/assessments", methods=["GET"])
def list_assessments():
    user, err = require_user()
    if err:
        return err
    db = get_db()
    rows = db.execute(
        """SELECT a.*, c.name AS course_name FROM assessments a
           JOIN courses c ON c.id = a.course_id
           WHERE c.user_id=? ORDER BY a.due_date""",
        (user["id"],),
    ).fetchall()
    return jsonify(rows_to_list(rows))


@api.route("/assessments", methods=["POST"])
def create_assessment():
    user, err = require_user()
    if err:
        return err
    data = request.get_json(force=True)
    db = get_db()
    course_id = int(data["course_id"])
    if not owns_course(db, user, course_id):
        return jsonify({"error": "Course not found"}), 404
    cur = db.execute(
        """INSERT INTO assessments (course_id, title, type, due_date, weight)
           VALUES (?, ?, ?, ?, ?)""",
        (
            course_id,
            data.get("title", "Untitled"),
            data.get("type", "assignment"),
            data["due_date"],
            float(data.get("weight", 10)),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM assessments WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@api.route("/assessments/<int:aid>", methods=["PUT"])
def update_assessment(aid):
    user, err = require_user()
    if err:
        return err
    data = request.get_json(force=True)
    db = get_db()
    existing = db.execute(
        """SELECT a.* FROM assessments a JOIN courses c ON c.id = a.course_id
           WHERE a.id=? AND c.user_id=?""",
        (aid, user["id"]),
    ).fetchone()
    if not existing:
        return jsonify({"error": "Assessment not found"}), 404
    merged = {**row_to_dict(existing), **data}
    if not owns_course(db, user, int(merged["course_id"])):
        return jsonify({"error": "Course not found"}), 404
    db.execute(
        """UPDATE assessments SET course_id=?, title=?, type=?, due_date=?, weight=?
           WHERE id=?""",
        (
            int(merged["course_id"]), merged["title"], merged["type"],
            merged["due_date"], float(merged["weight"]), aid,
        ),
    )
    db.commit()
    updated = db.execute("SELECT * FROM assessments WHERE id=?", (aid,)).fetchone()
    return jsonify(row_to_dict(updated))


@api.route("/assessments/<int:aid>", methods=["DELETE"])
def delete_assessment(aid):
    user, err = require_user()
    if err:
        return err
    db = get_db()
    db.execute(
        """DELETE FROM assessments WHERE id=? AND course_id IN
           (SELECT id FROM courses WHERE user_id=?)""",
        (aid, user["id"]),
    )
    db.commit()
    return jsonify({"deleted": aid})


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

@api.route("/scores", methods=["GET"])
def list_scores():
    user, err = require_user()
    if err:
        return err
    db = get_db()
    course_id = request.args.get("course_id")
    if course_id:
        rows = db.execute(
            """SELECT s.* FROM scores s JOIN courses c ON c.id = s.course_id
               WHERE c.user_id=? AND s.course_id=? ORDER BY s.date_taken""",
            (user["id"], course_id),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT s.* FROM scores s JOIN courses c ON c.id = s.course_id
               WHERE c.user_id=? ORDER BY s.date_taken""",
            (user["id"],),
        ).fetchall()
    return jsonify(rows_to_list(rows))


@api.route("/scores", methods=["POST"])
def create_score():
    user, err = require_user()
    if err:
        return err
    data = request.get_json(force=True)
    db = get_db()
    course_id = int(data["course_id"])
    if not owns_course(db, user, course_id):
        return jsonify({"error": "Course not found"}), 404
    cur = db.execute(
        "INSERT INTO scores (course_id, label, score, date_taken) VALUES (?, ?, ?, ?)",
        (
            course_id,
            data.get("label", "Quiz"),
            float(data["score"]),
            data.get("date_taken", date.today().isoformat()),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM scores WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@api.route("/scores/<int:sid>", methods=["DELETE"])
def delete_score(sid):
    user, err = require_user()
    if err:
        return err
    db = get_db()
    db.execute(
        """DELETE FROM scores WHERE id=? AND course_id IN
           (SELECT id FROM courses WHERE user_id=?)""",
        (sid, user["id"]),
    )
    db.commit()
    return jsonify({"deleted": sid})


# ---------------------------------------------------------------------------
# Study logs
# ---------------------------------------------------------------------------

@api.route("/study-logs", methods=["GET"])
def list_study_logs():
    user, err = require_user()
    if err:
        return err
    db = get_db()
    rows = db.execute(
        """SELECT l.* FROM study_logs l JOIN courses c ON c.id = l.course_id
           WHERE c.user_id=? ORDER BY l.log_date DESC""",
        (user["id"],),
    ).fetchall()
    return jsonify(rows_to_list(rows))


@api.route("/study-logs", methods=["POST"])
def create_study_log():
    user, err = require_user()
    if err:
        return err
    data = request.get_json(force=True)
    db = get_db()
    course_id = int(data["course_id"])
    if not owns_course(db, user, course_id):
        return jsonify({"error": "Course not found"}), 404
    cur = db.execute(
        "INSERT INTO study_logs (course_id, hours, log_date) VALUES (?, ?, ?)",
        (
            course_id,
            float(data["hours"]),
            data.get("log_date", date.today().isoformat()),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM study_logs WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


# ---------------------------------------------------------------------------
# Risk engine / weekly plan / summary
# ---------------------------------------------------------------------------

@api.route("/risk", methods=["GET"])
def api_risk():
    user, err = require_user()
    if err:
        return err
    return jsonify(build_risk_report(user["id"]))


@api.route("/plan", methods=["GET"])
def api_plan():
    user, err = require_user()
    if err:
        return err
    try:
        hours = float(request.args.get("hours", 20))
    except ValueError:
        hours = 20.0
    return jsonify(build_weekly_plan(user["id"], hours))


@api.route("/summary", methods=["GET"])
def api_summary():
    user, err = require_user()
    if err:
        return err
    return jsonify({"summary": generate_summary_text(user["id"])})


# ---------------------------------------------------------------------------
# Dashboard overview (single call for the whole dashboard)
# ---------------------------------------------------------------------------

@api.route("/overview", methods=["GET"])
def api_overview():
    user, err = require_user()
    if err:
        return err
    try:
        hours = float(request.args.get("hours", 20))
    except ValueError:
        hours = 20.0
    return jsonify({
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "summary": generate_summary_text(user["id"]),
        "risk": build_risk_report(user["id"]),
        "plan": build_weekly_plan(user["id"], hours),
    })


# ---------------------------------------------------------------------------
# Demo data seeding (scoped to the logged-in user)
# ---------------------------------------------------------------------------

@api.route("/seed", methods=["POST"])
def api_seed():
    user, err = require_user()
    if err:
        return err
    names = seed_demo_data(user["id"])
    return jsonify({"seeded": True, "courses": names})
