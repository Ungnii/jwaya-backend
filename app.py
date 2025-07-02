from flask import Flask, request, jsonify, g
from flask_cors import CORS
import sqlite3
from datetime import datetime, timedelta
import os

app = Flask(__name__)
CORS(app)

DATABASE = "reservations.db"
PERIODS = ["1교시","2교시","3교시","4교시","5교시(점심)","5교시","6교시"]

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exc):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db(); c=db.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS reservations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, period TEXT, room TEXT,
            grade TEXT, class TEXT, password TEXT, detail TEXT)""")
        db.commit()

def get_week_start(s):
    d = datetime.strptime(s, "%Y-%m-%d")
    start = d - timedelta(days=d.weekday())
    return start.strftime("%Y-%m-%d")

@app.route("/api/reservations", methods=["GET"])
def get_res():
    ws = request.args.get("week")
    if not ws: return jsonify({"error":"week param"}),400
    start = datetime.strptime(ws,"%Y-%m-%d")
    dates=[(start+timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    db=get_db(); cur=db.cursor(); out=[]
    for d in dates:
        day={"date":d,"reservations":{}}
        for p in PERIODS:
            cur.execute("SELECT room,grade,class,detail FROM reservations WHERE date=? AND period=?", (d,p))
            day["reservations"][p]=[{"room":r[0],"grade":r[1],"class":r[2],"detail":r[3]} for r in cur.fetchall()]
        out.append(day)
    return jsonify(out)

@app.route("/api/reservations", methods=["POST"])
def make_res():
    data=request.json
    date,period,room=data["date"],data["period"],data["room"]
    grade,cls,password=data["grade"],data["class"],data["password"]
    detail=data.get("detail","")

    db=get_db(); cur=db.cursor()

    # 주 3회 제한
    ws=get_week_start(date)
    we=(datetime.strptime(ws,"%Y-%m-%d")+timedelta(days=6)).strftime("%Y-%m-%d")
    cur.execute("""SELECT COUNT(*) FROM reservations
                   WHERE room=? AND grade=? AND class=? AND date BETWEEN ? AND ?""",
                (room,grade,cls,ws,we))
    if cur.fetchone()[0]>=3:
        return jsonify({"success":False,"message":"같은 특별실은 주 3회 이상 예약할 수 없습니다."})

    # 중복 검사 (운동장만 세부 규칙)
    if room=="운동장":
        cur.execute("SELECT detail FROM reservations WHERE date=? AND period=? AND room=?", (date,period,room))
        exist=[r[0] for r in cur.fetchall()]

        def conflict(newD,ex):
            if "전체" in ex: return True
            if newD=="전체": return bool(ex)
            if newD=="필드": return any(d in ("필드","필드(1/2)") for d in ex)
            if newD=="필드(1/2)": return ex.count("필드(1/2)")>=2 or "필드" in ex
            if newD=="트랙": return "트랙" in ex
            return False

        if conflict(detail or "전체", exist):
            return jsonify({"success":False,"message":"운동장 구역/시간대가 이미 예약되었습니다."})
    else:
        cur.execute("SELECT 1 FROM reservations WHERE date=? AND period=? AND room=?", (date,period,room))
        if cur.fetchone():
            return jsonify({"success":False,"message":"이미 예약된 시간대입니다."})

    cur.execute("""INSERT INTO reservations(date,period,room,grade,class,password,detail)
                   VALUES(?,?,?,?,?,?,?)""",
                (date,period,room,grade,cls,password,detail))
    db.commit()
    return jsonify({"success":True})

@app.route("/api/reservations", methods=["DELETE"])
def del_res():
    data=request.json
    date,period,room=data["date"],data["period"],data["room"]
    grade,cls,password=data.get("grade"),data.get("class"),data["password"]
    admin=password=="admin_override"

    db=get_db(); cur=db.cursor()
    if admin:
        cur.execute("DELETE FROM reservations WHERE date=? AND period=? AND room=?", (date,period,room))
    else:
        cur.execute("""DELETE FROM reservations WHERE date=? AND period=? AND room=?
                       AND grade=? AND class=? AND password=?""",
                    (date,period,room,grade,cls,password))
    db.commit()
    return jsonify({"success":True})

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
