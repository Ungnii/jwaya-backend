from flask import Flask, request, jsonify, g
from flask_cors import CORS
import sqlite3
from datetime import datetime, timedelta
import os

app = Flask(__name__)
CORS(app)

DATABASE = 'reservations.db'

PERIODS = ["1교시", "2교시", "3교시", "4교시", "5교시(점심)", "5교시", "6교시"]

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                period TEXT,
                room TEXT,
                grade TEXT,
                class TEXT,
                password TEXT,
                detail TEXT
            )
        ''')
        db.commit()

def get_week_start(date_str):
    date = datetime.strptime(date_str, "%Y-%m-%d")
    start = date - timedelta(days=date.weekday())
    return start.strftime("%Y-%m-%d")

@app.route("/api/reservations", methods=["GET"])
def get_reservations():
    week_start = request.args.get("week")
    if not week_start:
        return jsonify({"error": "Missing week parameter"}), 400

    start_date = datetime.strptime(week_start, "%Y-%m-%d")
    dates = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    db = get_db()
    cursor = db.cursor()

    result = []
    for date in dates:
        day_data = {"date": date, "reservations": {}}
        for period in PERIODS:
            cursor.execute("SELECT room, grade, class, detail FROM reservations WHERE date = ? AND period = ?", (date, period))
            day_data["reservations"][period] = [
                {"room": row[0], "grade": row[1], "class": row[2], "detail": row[3]} for row in cursor.fetchall()
            ]
        result.append(day_data)

    return jsonify(result)

@app.route("/api/reservations", methods=["POST"])
def make_reservation():
    data = request.json
    date = data["date"]
    period = data["period"]
    room = data["room"]
    grade = data["grade"]
    cls = data["class"]
    password = data["password"]
    detail = data.get("detail", "")

    db = get_db()
    cursor = db.cursor()

    # ✅ 주간 예약 3회 제한 검사
    week_start = get_week_start(date)
    week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) FROM reservations
        WHERE room = ? AND grade = ? AND class = ?
          AND date BETWEEN ? AND ?
    """, (room, grade, cls, week_start, week_end))
    count = cursor.fetchone()[0]
    if count >= 3:
        return jsonify({"success": False, "message": "같은 특별실은 주 3회 이상 예약할 수 없습니다."})

    # ✅ 중복 예약 방지
    cursor.execute("SELECT * FROM reservations WHERE date = ? AND period = ? AND room = ?", (date, period, room))
    if cursor.fetchone():
        return jsonify({"success": False, "message": "이미 예약된 시간대입니다."})

    cursor.execute(
        "INSERT INTO reservations (date, period, room, grade, class, password, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date, period, room, grade, cls, password, detail)
    )
    db.commit()

    return jsonify({"success": True})

@app.route("/api/reservations/delete", methods=["POST"])
def delete_reservation():
    data = request.json
    date = data["date"]
    period = data["period"]
    room = data["room"]
    grade = data.get("grade")
    cls = data.get("class")
    password = data["password"]
    is_admin = data.get("is_admin", False)

    db = get_db()
    cursor = db.cursor()

    if is_admin:
        cursor.execute("DELETE FROM reservations WHERE date = ? AND period = ? AND room = ?", (date, period, room))
    else:
        cursor.execute(
            "DELETE FROM reservations WHERE date = ? AND period = ? AND room = ? AND grade = ? AND class = ? AND password = ?",
            (date, period, room, grade, cls, password)
        )

    db.commit()
    return jsonify({"success": True})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
