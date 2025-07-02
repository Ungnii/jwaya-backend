from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

DB_NAME = "reservations.db"
ADMIN_ID = "Jwaya"
ADMIN_PW = "jwaya1234"

# DB 초기화
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                period TEXT NOT NULL,
                room TEXT NOT NULL,
                grade INTEGER,
                class INTEGER,
                password TEXT,
                extra TEXT
            )
        ''')
        conn.commit()

# 특정 주간의 월~금 날짜 리스트 생성
def get_week_dates(date_str):
    base = datetime.strptime(date_str, "%Y-%m-%d")
    start = base - timedelta(days=base.weekday())  # 월요일
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]

# ✅ 예약 등록
@app.route('/api/reserve', methods=['POST'])
def reserve():
    data = request.json
    date = data['date']
    period = data['period']
    room = data['room']
    grade = data['grade']
    classroom = data['class']
    password = data['password']
    extra = data.get('extra', '')

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT * FROM reservations WHERE date=? AND period=? AND room=?''',
                  (date, period, room))
        if c.fetchone():
            return jsonify({"success": False, "message": "이미 예약된 시간대입니다."})

        c.execute('''
            INSERT INTO reservations (date, period, room, grade, class, password, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (date, period, room, grade, classroom, password, extra))
        conn.commit()
        return jsonify({"success": True})

# ✅ 주간 예약 조회
@app.route('/api/reservations', methods=['GET'])
def get_reservations():
    week_date = request.args.get("week")
    week_dates = get_week_dates(week_date)

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT * FROM reservations WHERE date IN ({})'''.format(
            ','.join('?' * len(week_dates))
        ), week_dates)
        rows = c.fetchall()

        reservations = []
        for row in rows:
            reservations.append({
                "id": row[0],
                "date": row[1],
                "period": row[2],
                "room": row[3],
                "grade": row[4],
                "class": row[5],
                "extra": row[7]
            })
        return jsonify({"reservations": reservations})

# ✅ 예약 삭제
@app.route('/api/cancel', methods=['POST'])
def cancel():
    data = request.json
    res_id = data['id']
    password = data['password']
    is_admin = data.get('admin', False)

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT password FROM reservations WHERE id=?', (res_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"success": False, "message": "존재하지 않는 예약입니다."})

        if not is_admin and row[0] != password:
            return jsonify({"success": False, "message": "비밀번호가 틀렸습니다."})

        c.execute('DELETE FROM reservations WHERE id=?', (res_id,))
        conn.commit()
        return jsonify({"success": True})

# ✅ 관리자 로그인
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    if data['id'] == ADMIN_ID and data['password'] == ADMIN_PW:
        return jsonify({"success": True})
    return jsonify({"success": False})

# 시작 시 DB 초기화
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
