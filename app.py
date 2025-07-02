##############################################################################
# app.py  –  PostgreSQL 버전
##############################################################################
import os
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, g
from flask_cors import CORS
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

##############################################################################
# 기본 설정
##############################################################################
DATABASE_URL = os.environ.get("DATABASE_URL")  # 예) postgres://user:pass@host:5432/dbname
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경 변수가 설정되지 않았습니다.")

PERIODS = ["1교시", "2교시", "3교시", "4교시", "5교시(점심)", "5교시", "6교시"]

##############################################################################
# Flask & CORS
##############################################################################
app = Flask(__name__)
CORS(app)

##############################################################################
# PostgreSQL 연결 풀
##############################################################################
pg_pool = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=5,
    dsn=DATABASE_URL,
    cursor_factory=RealDictCursor,  # dict 형태로 결과 반환
)

def get_conn():
    """요청 단위 커넥션 획득"""
    conn = getattr(g, "_pg_conn", None)
    if conn is None:
        conn = g._pg_conn = pg_pool.getconn()
    return conn

@app.teardown_appcontext
def release_conn(exc):
    conn = getattr(g, "_pg_conn", None)
    if conn is not None:
        pg_pool.putconn(conn)

##############################################################################
# DB 초기화 (테이블 생성)
##############################################################################
def init_db():
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    id      SERIAL PRIMARY KEY,
                    date    DATE,
                    period  TEXT,
                    room    TEXT,
                    grade   TEXT,
                    class   TEXT,
                    password TEXT,
                    detail  TEXT
                );
                """
            )
            conn.commit()

##############################################################################
# 헬퍼 함수
##############################################################################
def week_start(date_str: str) -> str:
    """주(월요일) 시작일 반환"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = d - timedelta(days=d.weekday())
    return start.strftime("%Y-%m-%d")

##############################################################################
# API
##############################################################################
@app.route("/api/reservations", methods=["GET"])
def get_reservations():
    week = request.args.get("week")
    if not week:
        return jsonify({"error": "week parameter 필요"}), 400

    start = datetime.strptime(week, "%Y-%m-%d")
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    out = []
    conn = get_conn()
    with conn.cursor() as cur:
        for d in dates:
            day = {"date": d, "reservations": {}}
            for p in PERIODS:
                cur.execute(
                    """
                    SELECT room, grade, class, detail
                      FROM reservations
                     WHERE date = %s AND period = %s
                    """,
                    (d, p),
                )
                rows = cur.fetchall()
                day["reservations"][p] = [
                    {
                        "room": r["room"],
                        "grade": r["grade"],
                        "class": r["class"],
                        "detail": r["detail"],
                    }
                    for r in rows
                ]
            out.append(day)

    return jsonify(out)

# ---------------------------------------------------------------------------

@app.route("/api/reservations", methods=["POST"])
def make_reservation():
    data = request.json
    date     = data["date"]
    period   = data["period"]
    room     = data["room"]
    grade    = data["grade"]
    cls      = data["class"]
    password = data["password"]
    detail   = data.get("detail", "")

    conn = get_conn()
    cur  = conn.cursor()

    # ── ① 주 3회 제한 ─────────────────────────────────────────
    ws = week_start(date)
    we = (datetime.strptime(ws, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")

    cur.execute(
        """
        SELECT COUNT(*) FROM reservations
         WHERE room = %s AND grade = %s AND class = %s
           AND date BETWEEN %s AND %s
        """,
        (room, grade, cls, ws, we),
    )
    if cur.fetchone()["count"] >= 3:
        return jsonify({"success": False, "message": "같은 특별실은 주 3회 이상 예약할 수 없습니다."})

    # ── ② 중복/충돌 검사 ────────────────────────────────────
    if room == "운동장":
        cur.execute(
            """
            SELECT detail FROM reservations
             WHERE date = %s AND period = %s AND room = %s
            """,
            (date, period, room),
        )
        existing = [r["detail"] for r in cur.fetchall()]

        def conflict(new_dtl: str, ex: list[str]) -> bool:
            if "전체" in ex:
                return True
            if new_dtl == "전체":
                return bool(ex)
            if new_dtl == "필드":
                return any(d in ("필드", "필드(1/2)") for d in ex)
            if new_dtl == "필드(1/2)":
                return ex.count("필드(1/2)") >= 2 or "필드" in ex
            if new_dtl == "트랙":
                return "트랙" in ex
            return False

        if conflict(detail or "전체", existing):
            return jsonify({"success": False, "message": "운동장 구역/시간대가 이미 예약되었습니다."})
    else:
        cur.execute(
            """
            SELECT 1 FROM reservations
             WHERE date = %s AND period = %s AND room = %s
            """,
            (date, period, room),
        )
        if cur.fetchone():
            return jsonify({"success": False, "message": "이미 예약된 시간대입니다."})

    # ── ③ 삽입 ─────────────────────────────────────────────
    cur.execute(
        """
        INSERT INTO reservations (date, period, room, grade, class, password, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (date, period, room, grade, cls, password, detail),
    )
    conn.commit()
    return jsonify({"success": True})

# ---------------------------------------------------------------------------

@app.route("/api/reservations", methods=["DELETE"])
def delete_reservation():
    data = request.json
    date, period, room = data["date"], data["period"], data["room"]
    grade, cls = data.get("grade"), data.get("class")
    password   = data["password"]
    is_admin   = password == "admin_override"

    conn = get_conn()
    cur  = conn.cursor()

    if is_admin:
        cur.execute(
            "DELETE FROM reservations WHERE date = %s AND period = %s AND room = %s",
            (date, period, room),
        )
    else:
        cur.execute(
            """
            DELETE FROM reservations
             WHERE date = %s AND period = %s AND room = %s
               AND grade = %s AND class = %s AND password = %s
            """,
            (date, period, room, grade, cls, password),
        )
    conn.commit()
    return jsonify({"success": True})

##############################################################################
# 실행
##############################################################################
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
