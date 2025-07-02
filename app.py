# ───────────────────────────────────────────────────────────
# app.py  (Render + Neon Postgres)
# ───────────────────────────────────────────────────────────
import os
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, g
from flask_cors import CORS
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# ── 1. DATABASE_URL 로드 (Render의 Env Var 필요) ─────────────────
DATABASE_URL = os.environ.get(
    "postgresql://neondb_owner:npg_o1NuSfCm2Ukj@ep-quiet-cloud-a9urz30x-pooler.gwc.azure.neon.tech/neondb?sslmode=require&channel_binding=requir",
    # fallback: 직접 하드코딩한 URL (테스트용)
    "postgresql://neondb_owner:npg_o1NuSfCm2Ukj@"
    "ep-quiet-cloud-a9urz30x-pooler.gwc.azure.neon.tech/"
    "neondb?sslmode=require&channel_binding=require"
)
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경 변수가 설정되어 있지 않습니다.")

# ── 2. Flask & CORS ───────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# ── 3. psycopg2 연결 풀 ───────────────────────────────────────
pg_pool = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=6,               # 동시 접속 수가 많지 않으므로 6이면 충분
    dsn=DATABASE_URL,
    cursor_factory=RealDictCursor
)

def get_conn():
    conn = getattr(g, "_pg_conn", None)
    if conn is None:
        conn = g._pg_conn = pg_pool.getconn()
    return conn

@app.teardown_appcontext
def release_conn(exc):
    conn = getattr(g, "_pg_conn", None)
    if conn is not None:
        pg_pool.putconn(conn)

# ── 4. 테이블 생성 (최초 1회) ────────────────────────────────────
def init_db():
    with pg_pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    id       SERIAL PRIMARY KEY,
                    date     DATE,
                    period   TEXT,
                    room     TEXT,
                    grade    TEXT,
                    class    TEXT,
                    password TEXT,
                    detail   TEXT
                );
                """
            )
            conn.commit()

# ── 5. 헬퍼 ────────────────────────────────────────────────────
PERIODS = [
    "1교시", "2교시", "3교시", "4교시",
    "5교시(점심)", "5교시", "6교시",
]

def week_start(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

# ── 6. API 엔드포인트 ──────────────────────────────────────────
@app.route("/api/reservations", methods=["GET"])
def api_get():
    week = request.args.get("week")
    if not week:
        return jsonify({"error": "week param missing"}), 400

    start = datetime.strptime(week, "%Y-%m-%d")
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    result = []
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
                    {"room": r["room"], "grade": r["grade"], "class": r["class"], "detail": r["detail"]}
                    for r in rows
                ]
            result.append(day)
    return jsonify(result)

# ----------------------------------------------------------------

@app.route("/api/reservations", methods=["POST"])
def api_post():
    data = request.json
    date, period, room = data["date"], data["period"], data["room"]
    grade, cls, password = data["grade"], data["class"], data["password"]
    detail = data.get("detail", "")

    conn = get_conn()
    cur = conn.cursor()

    # 주 3회 제한
    ws = week_start(date)
    we = (datetime.strptime(ws, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
          FROM reservations
         WHERE room = %s AND grade = %s AND class = %s
           AND date BETWEEN %s AND %s
        """,
        (room, grade, cls, ws, we),
    )
    if cur.fetchone()["cnt"] >= 3:
        return jsonify({"success": False, "message": "같은 특별실은 주 3회 이상 예약할 수 없습니다."})

    # 운동장 중복 규칙
    def ground_conflict(new_d, ex_details):
        if "전체" in ex_details:
            return True
        if new_d == "전체":
            return bool(ex_details)
        if new_d == "필드":
            return any(d in ("필드", "필드(1/2)") for d in ex_details)
        if new_d == "필드(1/2)":
            return ex_details.count("필드(1/2)") >= 2 or "필드" in ex_details
        if new_d == "트랙":
            return "트랙" in ex_details
        return False

    if room == "운동장":
        cur.execute(
            """
            SELECT detail
              FROM reservations
             WHERE date = %s AND period = %s AND room = %s
            """,
            (date, period, room),
        )
        existing_details = [r["detail"] for r in cur.fetchall()]

        if ground_conflict(detail or "전체", existing_details):
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

    # INSERT
    cur.execute(
        """
        INSERT INTO reservations (date, period, room, grade, class, password, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (date, period, room, grade, cls, password, detail),
    )
    conn.commit()
    return jsonify({"success": True})

# ----------------------------------------------------------------

@app.route("/api/reservations", methods=["DELETE"])
def api_delete():
    data = request.json
    date, period, room = data["date"], data["period"], data["room"]
    grade, cls, password = data.get("grade"), data.get("class"), data["password"]
    admin = password == "admin_override"

    conn = get_conn()
    cur = conn.cursor()
    if admin:
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

# ── 7. 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()  # 최초 실행 시 테이블 보장
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
