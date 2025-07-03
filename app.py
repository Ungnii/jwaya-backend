###############################################################################
# app.py  –  Render + Neon PostgreSQL (高速 주간 조회)
###############################################################################
import os
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_caching import Cache
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# ─────────────────────────────────────────────────────────────────────────────
# 1. 환경 설정
# ─────────────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_o1NuSfCm2Ukj@ep-quiet-cloud-a9urz30x-pooler.gwc.azure.neon.tech/neondb?sslmode=require&channel_binding=require"
)
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경 변수가 필요합니다.")

PERIODS = [
    "1교시", "2교시", "3교시", "4교시",
    "5교시(점심)", "5교시", "6교시",
]

# ─────────────────────────────────────────────────────────────────────────────
# 2. Flask, CORS, Cache
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
cache = Cache(app, config={"CACHE_TYPE": "simple", "DEFAULT_TIMEOUT": 15})

# ─────────────────────────────────────────────────────────────────────────────
# 3. psycopg2 연결 풀
# ─────────────────────────────────────────────────────────────────────────────
pg_pool = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=8,
    dsn=DATABASE_URL,
    cursor_factory=RealDictCursor,
)

def get_conn():
    conn = getattr(g, "_pg_conn", None)
    if conn is None:
        conn = g._pg_conn = pg_pool.getconn()
    return conn

@app.teardown_appcontext
def put_conn(exc):
    conn = getattr(g, "_pg_conn", None)
    if conn is not None:
        pg_pool.putconn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# 4. DB 초기화 (테이블 + 인덱스)
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    with pg_pool.getconn() as conn, conn.cursor() as cur:
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
            CREATE INDEX IF NOT EXISTS idx_date_period
            ON reservations (date, period);
            """
        )
        conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
# 5. 유틸
# ─────────────────────────────────────────────────────────────────────────────
def week_start(date_str: str) -> datetime:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d - timedelta(days=d.weekday())

# 운동장 충돌 로직
def is_ground_conflict(new_d: str, existing: list[str]) -> bool:
    if "전체" in existing:
        return True
    if new_d == "전체":
        return bool(existing)
    if new_d == "필드":
        return any(d in ("필드", "필드(1/2)") for d in existing)
    if new_d == "필드(1/2)":
        return existing.count("필드(1/2)") >= 2 or "필드" in existing
    if new_d == "트랙":
        return "트랙" in existing
    return False

# ─────────────────────────────────────────────────────────────────────────────
# 6. API
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/reservations", methods=["GET"])
@cache.cached(timeout=15, query_string=True)   # 주간 데이터 15초 캐싱
def get_reservations():
    week = request.args.get("week")  # Monday 날짜
    if not week:
        return jsonify({"error": "week parameter 필요"}), 400

    start = week_start(week)
    end   = start + timedelta(days=6)

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, period, room, grade, class, detail
              FROM reservations
             WHERE date BETWEEN %s AND %s
            """,
            (start, end),
        )
        rows = cur.fetchall()

    # 파이썬에서 그룹핑
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    day_map = {d: {p: [] for p in PERIODS} for d in dates}

    for r in rows:
        day_map[r["date"].strftime("%Y-%m-%d")][r["period"]].append(
            {
                "room": r["room"],
                "grade": r["grade"],
                "class": r["class"],
                "detail": r["detail"],
            }
        )

    result = [
        {"date": d, "reservations": day_map[d]} for d in dates
    ]
    return jsonify(result)

# ---------------------------------------------------------------------------

@app.route("/api/reservations", methods=["POST"])
def add_reservation():
    d = request.json
    date, period, room = d["date"], d["period"], d["room"]
    grade, cls, pw = d["grade"], d["class"], d["password"]
    detail = d.get("detail", "")

    conn = get_conn()
    cur  = conn.cursor()

    # 주 3회 제한
    ws = week_start(date).strftime("%Y-%m-%d")
    we = (week_start(date) + timedelta(days=6)).strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
          FROM reservations
         WHERE room=%s AND grade=%s AND class=%s
           AND date BETWEEN %s AND %s
        """,
        (room, grade, cls, ws, we),
    )
    if cur.fetchone()["cnt"] >= 3:
        return jsonify({"success": False, "message": "같은 특별실은 주 3회 이상 예약할 수 없습니다."})

    # 중복/충돌 체크
    if room == "운동장":
        cur.execute(
            """
            SELECT detail FROM reservations
             WHERE date=%s AND period=%s AND room=%s
            """,
            (date, period, room),
        )
        details = [r["detail"] for r in cur.fetchall()]
        if is_ground_conflict(detail or "전체", details):
            return jsonify({"success": False, "message": "운동장 구역/시간대가 이미 예약되었습니다."})
    else:
        cur.execute(
            """
            SELECT 1 FROM reservations
             WHERE date=%s AND period=%s AND room=%s
            """,
            (date, period, room),
        )
        if cur.fetchone():
            return jsonify({"success": False, "message": "이미 예약된 시간대입니다."})

    # 삽입
    cur.execute(
        """
        INSERT INTO reservations (date, period, room, grade, class, password, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (date, period, room, grade, cls, pw, detail),
    )
    conn.commit()
    cache.delete_memoized(get_reservations)   # 캐시 무효화
    return jsonify({"success": True})

# ---------------------------------------------------------------------------

@app.route("/api/reservations", methods=["DELETE"])
def del_reservation():
    d = request.json
    date, period, room = d["date"], d["period"], d["room"]
    grade, cls, pw = d.get("grade"), d.get("class"), d["password"]
    admin = pw == "admin_override"

    conn = get_conn()
    cur  = conn.cursor()

    if admin:
        cur.execute(
            "DELETE FROM reservations WHERE date=%s AND period=%s AND room=%s",
            (date, period, room),
        )
    else:
        cur.execute(
            """
            DELETE FROM reservations
             WHERE date=%s AND period=%s AND room=%s
               AND grade=%s AND class=%s AND password=%s
            """,
            (date, period, room, grade, cls, pw),
        )

    deleted = cur.rowcount
    conn.commit()
    cache.delete_memoized(get_reservations)

    if deleted == 0:
        return jsonify({"success": False, "message": "비밀번호가 틀렸거나 예약이 없습니다."})
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────────────────────
# 7. 실행
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()                         # 테이블 & 인덱스 보장
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
