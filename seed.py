"""
Demo data seeding — clears this user's existing data and loads four realistic
sample courses with assessments, quiz score histories (each with a visible
declining trend), and recent study logs. Used by POST /api/seed.

Scoped to the calling user, so seeding one account never touches another's.
"""

from datetime import date, timedelta
from db import get_db


def seed_demo_data(user_id):
    db = get_db()
    # only this user's rows
    db.execute(
        """DELETE FROM study_logs WHERE course_id IN (SELECT id FROM courses WHERE user_id=?)""",
        (user_id,),
    )
    db.execute(
        """DELETE FROM scores WHERE course_id IN (SELECT id FROM courses WHERE user_id=?)""",
        (user_id,),
    )
    db.execute(
        """DELETE FROM assessments WHERE course_id IN (SELECT id FROM courses WHERE user_id=?)""",
        (user_id,),
    )
    db.execute("DELETE FROM courses WHERE user_id=?", (user_id,))
    db.commit()

    today = date.today()

    courses = [
        ("Data Structures & Algorithms", "CS201", 4, 8, 68, 4),
        ("Calculus II", "MATH152", 4, 6, 74, 4),
        ("Intro to Psychology", "PSY101", 3, 3, 88, 2),
        ("Organic Chemistry", "CHEM241", 4, 7, 61, 5),
    ]
    course_ids = {}
    for name, code, credits, workload, grade, diff in courses:
        cur = db.execute(
            """INSERT INTO courses (user_id, name, code, credit_hours, weekly_workload_hours, current_grade, difficulty)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name, code, credits, workload, grade, diff),
        )
        course_ids[name] = cur.lastrowid

    assessments = [
        ("Data Structures & Algorithms", "Midterm Exam", "exam", today + timedelta(days=6), 25),
        ("Data Structures & Algorithms", "Assignment 4: Trees", "assignment", today + timedelta(days=2), 10),
        ("Calculus II", "Problem Set 7", "assignment", today + timedelta(days=3), 8),
        ("Calculus II", "Final Exam", "exam", today + timedelta(days=30), 35),
        ("Intro to Psychology", "Reading Quiz 5", "quiz", today + timedelta(days=4), 5),
        ("Organic Chemistry", "Lab Report 3", "assignment", today + timedelta(days=1), 12),
        ("Organic Chemistry", "Midterm Exam", "exam", today + timedelta(days=9), 30),
    ]
    for course_name, title, atype, due, weight in assessments:
        db.execute(
            "INSERT INTO assessments (course_id, title, type, due_date, weight) VALUES (?, ?, ?, ?, ?)",
            (course_ids[course_name], title, atype, due.isoformat(), weight),
        )

    scores = [
        ("Data Structures & Algorithms", [("Quiz 1", 75, -20), ("Quiz 2", 70, -12), ("Quiz 3", 62, -4)]),
        ("Calculus II", [("Quiz 1", 80, -18), ("Quiz 2", 76, -9), ("Quiz 3", 73, -3)]),
        ("Intro to Psychology", [("Quiz 1", 85, -15), ("Quiz 2", 90, -6)]),
        ("Organic Chemistry", [("Quiz 1", 66, -16), ("Quiz 2", 58, -8), ("Quiz 3", 55, -2)]),
    ]
    for course_name, quiz_list in scores:
        for label, score, offset in quiz_list:
            db.execute(
                "INSERT INTO scores (course_id, label, score, date_taken) VALUES (?, ?, ?, ?)",
                (course_ids[course_name], label, score, (today + timedelta(days=offset)).isoformat()),
            )

    logs = [
        ("Data Structures & Algorithms", 3, -2),
        ("Calculus II", 4, -1),
        ("Intro to Psychology", 3, -3),
        ("Organic Chemistry", 2, -1),
    ]
    for course_name, hours, offset in logs:
        db.execute(
            "INSERT INTO study_logs (course_id, hours, log_date) VALUES (?, ?, ?)",
            (course_ids[course_name], hours, (today + timedelta(days=offset)).isoformat()),
        )

    db.commit()
    return list(course_ids.keys())
