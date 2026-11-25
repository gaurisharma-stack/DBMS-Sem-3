"""CareLedger API (MySQL): connects the web frontend to the `healthcare` database.

Run:   uvicorn app:app --reload
Open:  http://localhost:8000
"""
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pymysql
from pymysql.cursors import DictCursor
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="CareLedger API (MySQL)")


class Cur:
    """Small wrapper so queries can be chained: cur.execute(...).fetchone()"""
    def __init__(self, cur): self.cur = cur
    def execute(self, sql, args=None):
        self.cur.execute(sql, args)
        return self.cur
    @property
    def lastrowid(self): return self.cur.lastrowid


@contextmanager
def db():
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "healthcare"), charset="utf8mb4",
        cursorclass=DictCursor, init_command="SET time_zone='+00:00'")   # API talks in UTC
    try:
        with conn.cursor() as cur:
            yield Cur(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- Turn MySQL errors (including trigger/constraint violations) into readable messages ----
@app.exception_handler(pymysql.err.MySQLError)
async def _db_error(_: Request, exc):
    code = exc.args[0] if exc.args else 0
    msg = exc.args[1] if len(exc.args) > 1 else str(exc)
    if code == 1644:  return JSONResponse({"detail": msg}, 409)            # our trigger SIGNALs
    if code == 1062:  return JSONResponse({"detail": "That record already exists (check the phone number)"}, 409)
    if code == 3819:  return JSONResponse({"detail": "A value is outside the allowed range"}, 400)
    if code == 1452:  return JSONResponse({"detail": "Referenced record does not exist"}, 400)
    return JSONResponse({"detail": msg}, 400)


# Doctor schedules are written in clinic time (default IST, UTC+05:30); appointments are stored in UTC.
CLINIC_OFFSET = timedelta(minutes=int(os.getenv("CLINIC_TZ_OFFSET_MIN", "330")))
def to_local(utc_dt): return utc_dt + CLINIC_OFFSET
def to_utc(local_dt): return local_dt - CLINIC_OFFSET
def hm(t): return f"{t.seconds // 3600:02d}:{t.seconds % 3600 // 60:02d}"      # TIME column -> 'HH:MM'


def blank(x): return "" if x is None else x
def z(dt): return dt.isoformat() + "Z" if dt else None          # UTC datetime -> ISO string
def utc(s):                                                      # ISO string -> naive UTC datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


# ======================= READ: everything the UI needs =======================
@app.get("/api/state")
def state():
    with db() as c:
        pts = c.execute("""SELECT patient_id AS id, CONCAT(first_name,' ',last_name) AS n, date_of_birth AS dob,
                                  gender AS sx, blood_group AS bg, phone AS ph
                           FROM patients WHERE is_active ORDER BY patient_id""").fetchall()
        for p in pts:
            p["al"], p["hx"], p["vt"] = [], [], []
        by = {p["id"]: p for p in pts}
        for r in c.execute("SELECT patient_id, allergen AS a, reaction AS r, severity AS s FROM allergies"):
            if r["patient_id"] in by: by[r.pop("patient_id")]["al"].append(r)
        for r in c.execute("SELECT patient_id, condition_name AS c, status AS st FROM medical_history"):
            if r["patient_id"] in by: by[r.pop("patient_id")]["hx"].append(r)
        for r in c.execute("SELECT * FROM patient_vitals ORDER BY recorded_at, vital_id"):
            if r["patient_id"] in by:
                by[r["patient_id"]]["vt"].append({
                    "t": z(r["recorded_at"]), "temp": blank(r["temperature_c"]), "hr": blank(r["heart_rate_bpm"]),
                    "sys": blank(r["systolic_bp"]), "dia": blank(r["diastolic_bp"]), "spo2": blank(r["spo2_percent"]),
                    "glu": blank(r["blood_glucose_mgdl"]), "wt": blank(r["weight_kg"])})

        appts = c.execute("""SELECT a.appointment_id AS id, a.patient_id AS pid, a.doctor_id AS did,
                                    a.start_time AS s, a.end_time AS e, a.status AS st, a.reason, co.diagnosis AS dx
                             FROM appointments a LEFT JOIN consultations co ON co.appointment_id = a.appointment_id
                             ORDER BY a.start_time""").fetchall()
        ab = {a["id"]: a for a in appts}
        for a in appts:
            a["s"], a["e"], a["rx"], a["labs"] = z(a["s"]), z(a["e"]), [], []
        for r in c.execute("""SELECT co.appointment_id AS aid, TRIM(CONCAT(m.generic_name,' ',COALESCE(m.strength,''))) AS m,
                                     i.frequency AS f, i.duration_days AS d
                              FROM prescription_items i
                              JOIN prescriptions p ON p.prescription_id = i.prescription_id
                              JOIN consultations co ON co.consultation_id = p.consultation_id
                              JOIN medications m ON m.medication_id = i.medication_id"""):
            ab[r.pop("aid")]["rx"].append(r)
        for r in c.execute("""SELECT co.appointment_id AS aid, t.test_name AS t, r.result_value AS v, r.flag AS f
                              FROM lab_results r
                              JOIN lab_orders o ON o.order_id = r.order_id
                              JOIN consultations co ON co.consultation_id = o.consultation_id
                              JOIN lab_tests t ON t.test_id = r.test_id"""):
            ab[r.pop("aid")]["labs"].append(r)

        checks = c.execute("SELECT check_id AS id, patient_id AS pid, checked_at AS ts FROM symptom_checks ORDER BY check_id").fetchall()
        cb = {k["id"]: k for k in checks}
        for k in checks:
            k["ts"], k["items"], k["preds"], k["fb"] = z(k["ts"]), [], [], None
        for r in c.execute("SELECT check_id, symptom_id AS s, severity AS v FROM symptom_check_items"):
            cb[r.pop("check_id")]["items"].append(r)
        for r in c.execute("""SELECT ap.check_id, ap.prediction_id, ap.disease_id AS d, ap.confidence AS sc,
                                     ap.prediction_rank AS rk,
                                     (SELECT COUNT(*) FROM symptom_check_items i
                                        JOIN disease_symptoms ds ON ds.symptom_id = i.symptom_id
                                       WHERE i.check_id = ap.check_id AND ds.disease_id = ap.disease_id) AS m,
                                     (SELECT f.was_correct FROM ai_feedback f WHERE f.prediction_id = ap.prediction_id
                                       ORDER BY f.feedback_id DESC LIMIT 1) AS ok
                              FROM ai_predictions ap ORDER BY ap.check_id, ap.prediction_rank"""):
            k = cb[r["check_id"]]
            k["preds"].append({"d": r["d"], "sc": r["sc"], "m": r["m"]})
            if r["rk"] == 1 and r["ok"] is not None:
                k["fb"] = "yes" if r["ok"] else "no"
    return {"patients": pts, "appts": appts, "checks": checks}


# ============ READ: reference data (doctors, Kaggle symptoms/diseases, lab tests) ============
DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

@app.get("/api/meta")
def meta():
    with db() as c:
        specs = c.execute("SELECT specialization_id AS id, name AS n FROM specializations ORDER BY specialization_id").fetchall()
        docs = c.execute("""SELECT doctor_id AS id, CONCAT('Dr. ',first_name,' ',last_name) AS n, qualification AS q,
                                   experience_years AS exp, consultation_fee AS fee, gender AS gd, languages AS lang,
                                   room_no AS room, bio, joined_on AS since
                            FROM doctors WHERE is_active ORDER BY doctor_id""").fetchall()
        dby = {d["id"]: d for d in docs}
        for d in docs:
            d["sp"], d["sch"], d["lv"], d["fee"] = [], [], [], float(d["fee"])
            d["since"] = d["since"].isoformat() if d["since"] else None
            d["done"], d["up"], d["pts"] = 0, 0, 0
        for r in c.execute("SELECT doctor_id, specialization_id FROM doctor_specializations ORDER BY is_primary DESC, specialization_id"):
            if r["doctor_id"] in dby: dby[r["doctor_id"]]["sp"].append(r["specialization_id"])
        for r in c.execute("SELECT doctor_id, day_of_week, start_time, end_time, slot_minutes FROM doctor_schedules ORDER BY doctor_id, day_of_week, start_time"):
            if r["doctor_id"] in dby:
                dby[r["doctor_id"]]["sch"].append(f"{DAYS[r['day_of_week']]} {hm(r['start_time'])}–{hm(r['end_time'])} ({r['slot_minutes']} min slots)")
        # leaves that have not finished yet (compared in clinic time)
        today = to_local(datetime.utcnow()).date()
        for r in c.execute("SELECT doctor_id, leave_from, leave_to, reason FROM doctor_leaves WHERE leave_to >= %s ORDER BY leave_from", (today,)):
            if r["doctor_id"] in dby:
                dby[r["doctor_id"]]["lv"].append({"from": r["leave_from"].isoformat(), "to": r["leave_to"].isoformat(), "why": blank(r["reason"])})
        for r in c.execute("SELECT doctor_id, completed_visits, upcoming_visits, patients FROM v_doctor_workload"):
            if r["doctor_id"] in dby:
                dby[r["doctor_id"]].update(done=int(r["completed_visits"]), up=int(r["upcoming_visits"]), pts=int(r["patients"]))

        syms = c.execute("""SELECT symptom_id AS id, name AS n, body_system AS sys, severity_weight AS w
                            FROM symptoms ORDER BY body_system, name""").fetchall()
        dis = c.execute("""SELECT disease_id AS id, name AS n, icd10_code AS icd, severity_level AS sev,
                                  recommended_specialization_id AS sp, description AS info
                           FROM diseases ORDER BY disease_id""").fetchall()
        dz = {d["id"]: d for d in dis}
        for d in dis: d["prec"] = []
        for r in c.execute("SELECT disease_id, precaution FROM disease_precautions ORDER BY disease_id, step_no"):
            dz[r["disease_id"]]["prec"].append(r["precaution"])
        tests = {r["test_name"]: [float(r["lo"]), float(r["hi"]), r["unit"]] for r in c.execute(
            "SELECT test_name, ref_range_low AS lo, ref_range_high AS hi, unit FROM lab_tests ORDER BY test_id")}
        # Kaggle knowledge base links, drawn by the 3D globe: [disease_id, symptom_id, weight]
        links = [[r["disease_id"], r["symptom_id"], float(r["weight"])]
                 for r in c.execute("SELECT disease_id, symptom_id, weight FROM disease_symptoms")]
    return {"specs": specs, "doctors": docs, "symptoms": syms, "diseases": dis, "tests": tests, "links": links}


@app.get("/api/kaggle")
def kaggle():
    """Everything the 'Kaggle data' page shows, computed live from the imported dataset."""
    with db() as c:
        tot = c.execute("""SELECT (SELECT COUNT(*) FROM kaggle_cases) AS records,
                                  (SELECT COUNT(*) FROM kaggle_case_symptoms) AS pairs,
                                  (SELECT COUNT(DISTINCT disease_id) FROM kaggle_cases) AS diseases,
                                  (SELECT COUNT(DISTINCT symptom_id) FROM kaggle_case_symptoms) AS symptoms,
                                  (SELECT COUNT(*) FROM disease_symptoms) AS links""").fetchone()
        top = c.execute("""SELECT s.name AS n, s.body_system AS sys, s.severity_weight AS w, COUNT(*) AS cnt
                           FROM kaggle_case_symptoms k JOIN symptoms s ON s.symptom_id = k.symptom_id
                           GROUP BY s.symptom_id, s.name, s.body_system, s.severity_weight
                           ORDER BY cnt DESC, s.name LIMIT 12""").fetchall()
        systems = c.execute("""SELECT COALESCE(body_system,'General') AS n, COUNT(*) AS cnt
                               FROM symptoms GROUP BY body_system ORDER BY cnt DESC""").fetchall()
        dis = c.execute("""SELECT d.disease_id AS id, d.name AS n, d.severity_level AS sev, COUNT(DISTINCT kc.case_id) AS cnt,
                                  (SELECT COUNT(*) FROM disease_symptoms x WHERE x.disease_id = d.disease_id) AS nsym
                           FROM diseases d JOIN kaggle_cases kc ON kc.disease_id = d.disease_id
                           GROUP BY d.disease_id, d.name, d.severity_level ORDER BY d.name""").fetchall()
        dby = {d["id"]: d for d in dis}
        for d in dis: d["top"] = []
        for r in c.execute("""SELECT disease_id, name, weight FROM (
                                SELECT ds.disease_id, s.name, ds.weight,
                                       ROW_NUMBER() OVER (PARTITION BY ds.disease_id ORDER BY ds.weight DESC, s.name) AS rn
                                FROM disease_symptoms ds JOIN symptoms s ON s.symptom_id = ds.symptom_id) x
                              WHERE rn <= 4"""):
            dby[r["disease_id"]]["top"].append({"n": r["name"], "w": float(r["weight"])})
        evals = c.execute("""SELECT method, records, top1_accuracy AS t1, top3_accuracy AS t3
                             FROM model_evaluations ORDER BY eval_id""").fetchall()
        for e in evals: e["t1"], e["t3"] = float(e["t1"]), float(e["t3"])
    return {"totals": tot, "top": top, "systems": systems, "diseases": dis, "evals": evals}


# ============================== PATIENTS ==============================
class NewPatient(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: str
    gender: str
    blood_group: str | None = None
    phone: str

@app.post("/api/patients")
def add_patient(p: NewPatient):
    with db() as c:
        c.execute("""INSERT INTO patients (first_name,last_name,date_of_birth,gender,blood_group,phone)
                     VALUES (%s,%s,%s,%s,%s,%s)""",
                  (p.first_name, p.last_name, p.date_of_birth, p.gender, p.blood_group, p.phone))
        return {"id": c.lastrowid}


class Vitals(BaseModel):
    temperature_c: float | None = None
    heart_rate_bpm: int | None = None
    systolic_bp: int | None = None
    diastolic_bp: int | None = None
    spo2_percent: float | None = None
    blood_glucose_mgdl: float | None = None
    weight_kg: float | None = None

@app.post("/api/patients/{pid}/vitals")
def add_vitals(pid: int, v: Vitals):
    with db() as c:
        c.execute("""INSERT INTO patient_vitals (patient_id,temperature_c,heart_rate_bpm,systolic_bp,diastolic_bp,
                                                 spo2_percent,blood_glucose_mgdl,weight_kg)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (pid, v.temperature_c, v.heart_rate_bpm, v.systolic_bp, v.diastolic_bp,
                   v.spo2_percent, v.blood_glucose_mgdl, v.weight_kg))
    return {"ok": True}


# ============================ APPOINTMENTS ============================
class Booking(BaseModel):
    patient_id: int
    doctor_id: int
    start_time: str
    end_time: str
    reason: str = ""


def check_availability(c, doctor_id, start_utc, end_utc):
    """Reject a booking outside the doctor's weekly hours or during a leave (clinic time)."""
    ls, le = to_local(start_utc), to_local(end_utc)
    if ls.date() != le.date():
        raise HTTPException(400, "An appointment must start and end on the same day")
    doc = c.execute("SELECT is_active FROM doctors WHERE doctor_id=%s", (doctor_id,)).fetchone()
    if not doc or not doc["is_active"]:
        raise HTTPException(404, "Doctor not found")
    leave = c.execute("""SELECT reason FROM doctor_leaves WHERE doctor_id=%s AND %s BETWEEN leave_from AND leave_to""",
                      (doctor_id, ls.date())).fetchone()
    if leave:
        raise HTTPException(409, "That doctor is on leave on that date" + (f" ({leave['reason']})" if leave["reason"] else ""))
    dow = (ls.weekday() + 1) % 7                      # Python Mon=0 -> our Sun=0
    ok = any(hm(r["start_time"]) <= ls.strftime("%H:%M") and le.strftime("%H:%M") <= hm(r["end_time"])
             for r in c.execute("SELECT start_time, end_time FROM doctor_schedules WHERE doctor_id=%s AND day_of_week=%s",
                                (doctor_id, dow)).fetchall())
    if not ok:
        raise HTTPException(409, "That time is outside the doctor's working hours")


@app.get("/api/doctors/{did}/slots")
def slots(did: int, day: str):
    """Free and taken appointment slots of one doctor on one date (YYYY-MM-DD, clinic time)."""
    try: d = date.fromisoformat(day)
    except ValueError: raise HTTPException(400, "Use the date format YYYY-MM-DD")
    with db() as c:
        if not c.execute("SELECT 1 FROM doctors WHERE doctor_id=%s AND is_active", (did,)).fetchone():
            raise HTTPException(404, "Doctor not found")
        leave = c.execute("SELECT reason FROM doctor_leaves WHERE doctor_id=%s AND %s BETWEEN leave_from AND leave_to", (did, d)).fetchone()
        if leave:
            return {"date": day, "leave": leave["reason"] or "On leave", "slots": []}
        blocks = c.execute("""SELECT start_time, end_time, slot_minutes FROM doctor_schedules
                              WHERE doctor_id=%s AND day_of_week=%s ORDER BY start_time""", (did, (d.weekday() + 1) % 7)).fetchall()
        day_start = to_utc(datetime.combine(d, datetime.min.time()))
        taken = c.execute("""SELECT start_time, end_time FROM appointments
                             WHERE doctor_id=%s AND status IN ('Scheduled','Completed') AND start_time < %s AND end_time > %s""",
                          (did, day_start + timedelta(days=1), day_start)).fetchall()
    now = datetime.utcnow()
    out = []
    for b in blocks:
        step = timedelta(minutes=b["slot_minutes"])
        t = datetime.combine(d, datetime.min.time()) + b["start_time"]
        end = datetime.combine(d, datetime.min.time()) + b["end_time"]
        while t + step <= end:
            s_utc, e_utc = to_utc(t), to_utc(t + step)
            busy = any(a["start_time"] < e_utc and s_utc < a["end_time"] for a in taken)
            out.append({"t": t.strftime("%H:%M"), "s": s_utc.isoformat() + "Z", "e": e_utc.isoformat() + "Z",
                        "free": (not busy) and s_utc > now})
            t += step
    return {"date": day, "leave": None, "slots": out}


@app.post("/api/appointments")
def book(b: Booking):
    start, end = utc(b.start_time), utc(b.end_time)
    with db() as c:   # hours + leave are checked here; the overlap trigger rejects a double-booked slot
        check_availability(c, b.doctor_id, start, end)
        c.execute("""INSERT INTO appointments (patient_id,doctor_id,start_time,end_time,reason)
                     VALUES (%s,%s,%s,%s,%s)""",
                  (b.patient_id, b.doctor_id, start, end, b.reason))
        return {"id": c.lastrowid}


class StatusChange(BaseModel):
    status: str

@app.patch("/api/appointments/{aid}")
def set_status(aid: int, s: StatusChange):
    with db() as c:
        c.execute("UPDATE appointments SET status=%s WHERE appointment_id=%s", (s.status, aid))
    return {"ok": True}


class Visit(BaseModel):
    diagnosis: str = ""
    medication: str = ""
    frequency: str = ""
    days: int = 5
    lab_test: str = ""
    lab_value: float | None = None

@app.post("/api/appointments/{aid}/complete")
def complete(aid: int, v: Visit):
    with db() as c:   # one transaction: visit, prescription and lab order all succeed or none do
        a = c.execute("SELECT reason, status FROM appointments WHERE appointment_id=%s", (aid,)).fetchone()
        if not a: raise HTTPException(404, "Appointment not found")
        if a["status"] != "Scheduled": raise HTTPException(409, "Only scheduled appointments can be completed")
        c.execute("UPDATE appointments SET status='Completed' WHERE appointment_id=%s", (aid,))
        c.execute("INSERT INTO consultations (appointment_id,chief_complaint,diagnosis) VALUES (%s,%s,%s)",
                  (aid, a["reason"] or "Not recorded", v.diagnosis or "Not recorded"))
        cid = c.lastrowid
        name = v.medication.strip()
        if name:
            m = c.execute("""SELECT medication_id FROM medications
                             WHERE LOWER(TRIM(CONCAT(generic_name,' ',COALESCE(strength,'')))) = LOWER(%s)""", (name,)).fetchone()
            if m:
                mid = m["medication_id"]
            else:
                c.execute("INSERT INTO medications (generic_name) VALUES (%s)", (name,))
                mid = c.lastrowid
            c.execute("INSERT INTO prescriptions (consultation_id) VALUES (%s)", (cid,))
            rxid = c.lastrowid
            c.execute("""INSERT INTO prescription_items (prescription_id,medication_id,dosage,frequency,duration_days)
                         VALUES (%s,%s,'As prescribed',%s,%s)""", (rxid, mid, v.frequency or "As directed", max(v.days, 1)))
        if v.lab_test and v.lab_value is not None:
            t = c.execute("SELECT test_id FROM lab_tests WHERE test_name=%s", (v.lab_test,)).fetchone()
            if not t: raise HTTPException(400, "Unknown lab test")
            c.execute("INSERT INTO lab_orders (consultation_id,status) VALUES (%s,'Completed')", (cid,))
            oid = c.lastrowid
            c.execute("INSERT INTO lab_results (order_id,test_id,result_value) VALUES (%s,%s,%s)",
                      (oid, t["test_id"], v.lab_value))   # trigger fills Low/Normal/High
    return {"ok": True}


# ======================== AI SYMPTOM MATCHING ========================
class Item(BaseModel):
    symptom_id: int
    severity: int

class Check(BaseModel):
    patient_id: int
    items: list[Item]

@app.post("/api/symptom-checks")
def new_check(ch: Check):
    if not ch.items: raise HTTPException(400, "Select at least one symptom")
    with db() as c:
        c.execute("INSERT INTO symptom_checks (patient_id, model_id) VALUES (%s,1)", (ch.patient_id,))
        cid = c.lastrowid
        for i in ch.items:
            c.execute("INSERT INTO symptom_check_items (check_id,symptom_id,severity) VALUES (%s,%s,%s)",
                      (cid, i.symptom_id, i.severity))
        # Scoring happens inside MySQL via the match_diseases stored procedure
        c.execute("CALL match_diseases(%s, 5)", (cid,))
        rows = c.cur.fetchall()
        while c.cur.nextset(): pass          # drain the procedure's status packet
        for rank, r in enumerate(rows, 1):
            sev = c.execute("SELECT severity_level FROM diseases WHERE disease_id=%s", (r["disease_id"],)).fetchone()["severity_level"]
            c.execute("""INSERT INTO ai_predictions (check_id,disease_id,confidence,prediction_rank,urgency)
                         VALUES (%s,%s,%s,%s,%s)""",
                      (cid, r["disease_id"], r["match_score"], rank,
                       "Urgent" if sev >= 4 else "Soon" if sev == 3 else "Routine"))
    return {"id": cid}


class Feedback(BaseModel):
    correct: bool
    doctor_id: int = 1   # replace with the logged-in doctor once you add authentication

@app.post("/api/symptom-checks/{cid}/feedback")
def feedback(cid: int, f: Feedback):
    with db() as c:
        p = c.execute("SELECT prediction_id, disease_id FROM ai_predictions WHERE check_id=%s AND prediction_rank=1", (cid,)).fetchone()
        if not p: raise HTTPException(404, "No prediction to review")
        c.execute("INSERT INTO ai_feedback (prediction_id,doctor_id,was_correct,final_disease_id) VALUES (%s,%s,%s,%s)",
                  (p["prediction_id"], f.doctor_id, f.correct, p["disease_id"] if f.correct else None))
    return {"ok": True}


# Serve the frontend from the same server (no CORS setup needed)
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "frontend"), html=True), name="ui")
