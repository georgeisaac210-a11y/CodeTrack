"""
Weekly plan generator — allocates a student's total available weekly study
hours across their courses, proportional to each course's risk score, then
spreads each course's hours into study sessions across the 7 days of the week
(higher-risk courses get more, earlier sessions).

Also builds the plain-language weekly briefing. Both are scoped by user_id.
"""

from db import get_db, rows_to_list
from risk_engine import build_risk_report, days_until

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def build_weekly_plan(user_id, total_hours):
    db = get_db()
    risk_report = build_risk_report(user_id)
    if not risk_report:
        return {"total_hours": total_hours, "allocations": [], "days": {d: [] for d in DAY_NAMES}}

    # weight = risk_score, with a floor so every course gets at least a little time
    weights = {r["course_id"]: max(r["risk_score"], 5.0) for r in risk_report}
    weight_sum = sum(weights.values())

    allocations = []
    for r in risk_report:
        course_id = r["course_id"]
        share = weights[course_id] / weight_sum
        hours = round(share * total_hours, 1)

        assessments = rows_to_list(
            db.execute(
                "SELECT * FROM assessments WHERE course_id=? ORDER BY due_date", (course_id,)
            ).fetchall()
        )
        next_due = None
        for a in assessments:
            d = days_until(a["due_date"])
            if d is not None and d >= 0:
                next_due = a
                break

        allocations.append({
            "course_id": course_id,
            "course_name": r["course_name"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "top_driver": r["top_driver"],
            "allocated_hours": hours,
            "next_assessment": {
                "title": next_due["title"],
                "type": next_due["type"],
                "due_date": next_due["due_date"],
                "days_left": days_until(next_due["due_date"]),
            } if next_due else None,
        })

    # Distribute each course's hours across the week.
    # Higher-risk courses get sessions spread earlier/more frequently.
    days = {day: [] for day in DAY_NAMES}
    for alloc in allocations:
        hours_left = alloc["allocated_hours"]
        if hours_left <= 0:
            continue
        # number of sessions this week, scaled by risk (more sessions if higher risk)
        if alloc["risk_score"] >= 70:
            session_count = 5
        elif alloc["risk_score"] >= 40:
            session_count = 3
        else:
            session_count = 2
        session_count = max(1, min(session_count, len(DAY_NAMES)))
        per_session = round(hours_left / session_count, 1)

        # spread across days starting Monday, evenly spaced
        step = max(1, len(DAY_NAMES) // session_count)
        placed = 0
        day_idx = 0
        while placed < session_count and day_idx < len(DAY_NAMES):
            day = DAY_NAMES[day_idx]
            days[day].append({
                "course_name": alloc["course_name"],
                "hours": per_session,
                "risk_level": alloc["risk_level"],
            })
            placed += 1
            day_idx += step

    return {
        "total_hours": total_hours,
        "allocations": allocations,
        "days": days,
    }


def generate_summary_text(user_id):
    """Plain-language weekly briefing built from the risk report."""
    report = build_risk_report(user_id)
    if not report:
        return "Add some courses to get your personalized weekly briefing."

    lines = []
    high = [r for r in report if r["risk_level"] == "High"]
    medium = [r for r in report if r["risk_level"] == "Medium"]

    if high:
        names = ", ".join(r["course_name"] for r in high)
        lines.append(f"Priority focus this week: {names}. These show the highest combined risk from grades, recent performance trend, and upcoming deadlines.")
    elif medium:
        names = ", ".join(r["course_name"] for r in medium)
        lines.append(f"Keep an eye on: {names}. Risk is moderate — a bit of extra review time will help.")
    else:
        lines.append("You're in good shape across all courses this week. Keep up the consistent study habits.")

    db = get_db()
    for r in report[:3]:
        assessments = rows_to_list(
            db.execute(
                "SELECT * FROM assessments WHERE course_id=? ORDER BY due_date", (r["course_id"],)
            ).fetchall()
        )
        next_due = None
        for a in assessments:
            d = days_until(a["due_date"])
            if d is not None and d >= 0:
                next_due = a
                break
        if next_due:
            d = days_until(next_due["due_date"])
            due_phrase = "today" if d == 0 else f"in {d} day{'s' if d != 1 else ''}"
            lines.append(
                f"• {r['course_name']}: {next_due['type']} \"{next_due['title']}\" is due {due_phrase} "
                f"(risk {r['risk_score']}/100, driven mainly by {r['top_driver']})."
            )
    return "\n".join(lines)
