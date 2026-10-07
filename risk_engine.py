"""
Risk engine — computes a 0-100 risk score per course from five weighted signals:

    1. Grade level      (30%) - how far current grade is below a "safe" target (85)
    2. Score trend       (25%) - is recent quiz/assignment performance declining?
    3. Deadline urgency  (20%) - how soon is the next high-weight assessment?
    4. Workload pressure (15%) - course workload relative to hours actually logged
    5. Self-rated difficulty (10%) - student's own difficulty rating

Every sub-score is normalized to 0-100 (100 = highest risk) then combined.
This is intentionally transparent/rule-based rather than a black-box ML model,
so results are explainable to the student ("why is this course high risk?").

All queries are scoped by user_id so a report only ever covers the caller's
own courses.
"""

from datetime import datetime, date
from db import get_db, rows_to_list

WEIGHTS = {
    "grade": 0.30,
    "trend": 0.25,
    "urgency": 0.20,
    "workload": 0.15,
    "difficulty": 0.10,
}

SAFE_GRADE_TARGET = 85.0


def days_until(iso_date_str):
    try:
        d = datetime.strptime(iso_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - date.today()).days


def score_grade_component(current_grade):
    """Lower grade -> higher risk. current_grade may be None (no data yet)."""
    if current_grade is None:
        return 50.0  # neutral/unknown risk when no grade data exists
    gap = SAFE_GRADE_TARGET - current_grade
    risk = max(0.0, min(100.0, gap * (100.0 / SAFE_GRADE_TARGET)))
    return risk


def score_trend_component(scores):
    """
    Compares the average of the most recent half of scores vs the earlier half.
    A declining trend raises risk. Fewer than 2 scores -> neutral.
    """
    if len(scores) < 2:
        return 40.0
    ordered = sorted(scores, key=lambda s: s["date_taken"])
    values = [s["score"] for s in ordered]
    mid = len(values) // 2
    early = values[:mid] if mid > 0 else values[:1]
    recent = values[mid:]
    early_avg = sum(early) / len(early)
    recent_avg = sum(recent) / len(recent)
    delta = early_avg - recent_avg  # positive = declining
    # map delta of -30..+30 percentage points to risk 0..100, centered at 40
    risk = 40 + delta * 2.0
    return max(0.0, min(100.0, risk))


def score_urgency_component(assessments):
    """
    Looks at the nearest upcoming assessment weighted by its grade weight.
    Closer + higher-weight => higher urgency risk.
    """
    upcoming = []
    for a in assessments:
        d = days_until(a["due_date"])
        if d is not None and d >= 0:
            upcoming.append((d, a["weight"]))
    if not upcoming:
        return 10.0  # nothing upcoming -> low urgency
    best = None
    for d, weight in upcoming:
        # urgency contribution: closer days -> higher score, scaled by weight/100
        proximity_score = max(0.0, 100.0 - (d * 4.0))  # 0 days=100, 25+ days≈0
        contribution = proximity_score * (0.5 + weight / 100.0)
        if best is None or contribution > best:
            best = contribution
    return max(0.0, min(100.0, best))


def score_workload_component(course, logs):
    """
    Compares logged hours (last 7 days) against the course's expected weekly
    workload. Under-studying relative to workload = higher risk.
    """
    total_logged = sum(l["hours"] for l in logs)
    expected = course["weekly_workload_hours"] or 1
    ratio = total_logged / expected
    if ratio >= 1.0:
        return max(0.0, 20.0 - (ratio - 1.0) * 10.0)  # meeting/exceeding -> low risk
    return min(100.0, (1.0 - ratio) * 100.0)


def score_difficulty_component(course):
    # difficulty is 1-5 self-rated; map to 0-100
    return (course["difficulty"] - 1) / 4.0 * 100.0


def compute_risk_for_course(course, assessments, scores, logs):
    g = score_grade_component(course["current_grade"])
    t = score_trend_component(scores)
    u = score_urgency_component(assessments)
    w = score_workload_component(course, logs)
    d = score_difficulty_component(course)

    total = (
        g * WEIGHTS["grade"]
        + t * WEIGHTS["trend"]
        + u * WEIGHTS["urgency"]
        + w * WEIGHTS["workload"]
        + d * WEIGHTS["difficulty"]
    )
    total = round(max(0.0, min(100.0, total)), 1)

    if total >= 70:
        level = "High"
    elif total >= 40:
        level = "Medium"
    else:
        level = "Low"

    # Identify the single biggest driver, so the UI can say *why*.
    drivers = {
        "grade": g, "trend": t, "urgency": u, "workload": w, "difficulty": d,
    }
    top_driver = max(drivers, key=drivers.get)
    labels = {
        "grade": "grade level",
        "trend": "declining recent scores",
        "urgency": "an imminent deadline",
        "workload": "under-studying vs. workload",
        "difficulty": "self-rated difficulty",
    }

    return {
        "course_id": course["id"],
        "course_name": course["name"],
        "risk_score": total,
        "risk_level": level,
        "top_driver": labels[top_driver],
        "components": {
            "grade_risk": round(g, 1),
            "trend_risk": round(t, 1),
            "urgency_risk": round(u, 1),
            "workload_risk": round(w, 1),
            "difficulty_risk": round(d, 1),
        },
    }


def get_recent_logs(db, course_id, days_back=7):
    rows = db.execute(
        "SELECT * FROM study_logs WHERE course_id=?", (course_id,)
    ).fetchall()
    cutoff = date.today().toordinal() - days_back
    out = []
    for r in rows:
        try:
            d = datetime.strptime(r["log_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d.toordinal() >= cutoff:
            out.append(dict(r))
    return out


def build_risk_report(user_id):
    """Ranked risk report for one user's courses."""
    db = get_db()
    courses = rows_to_list(
        db.execute("SELECT * FROM courses WHERE user_id=?", (user_id,)).fetchall()
    )
    report = []
    for c in courses:
        assessments = rows_to_list(
            db.execute("SELECT * FROM assessments WHERE course_id=?", (c["id"],)).fetchall()
        )
        scores = rows_to_list(
            db.execute("SELECT * FROM scores WHERE course_id=?", (c["id"],)).fetchall()
        )
        logs = get_recent_logs(db, c["id"])
        report.append(compute_risk_for_course(c, assessments, scores, logs))
    report.sort(key=lambda r: r["risk_score"], reverse=True)
    return report
