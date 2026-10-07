"""
AI-Powered Student Planner — Flask backend entry point
========================================================
This file only wires the app together. The logic lives in:

    db.py           SQLite connection, schema, row serialization
    auth.py         Passwordless email login, sessions, bearer tokens
    risk_engine.py  Course risk scoring (grades, trend, urgency, workload, difficulty)
    planner.py      Weekly hour allocation + plain-language briefing
    seed.py         Demo data seeding (per user)
    routes.py       All /api/* endpoints (Flask Blueprint)

Run:
    pip install -r requirements.txt
    python app.py
Server starts on http://127.0.0.1:5000
"""

from flask import Flask, jsonify
from flask_cors import CORS

from db import init_db, close_db
from routes import api

app = Flask(__name__)
# Allow the frontend (served separately, e.g. on :8000) to call this API.
CORS(app, allow_headers=["Content-Type", "Authorization"],
     expose_headers=["Content-Type"], methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

app.teardown_appcontext(close_db)
app.register_blueprint(api)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "ai-student-planner"})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
