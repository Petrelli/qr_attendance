import base64
import io
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import qrcode
from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "attendance.db"
STUDENT_FILE = BASE_DIR / "students.txt"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ====== Editable settings ======
TEACHER_PIN = "123456"   # Default teacher PIN; change this before real use
DEFAULT_SIGNIN_MINUTES = 5
TIMEZONE = timezone.utc   # For Beijing time, use timezone(timedelta(hours=8))
# ==============================

app = Flask(__name__)
app.secret_key = "change-this-to-a-random-secret"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS sign_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            signed_at TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            UNIQUE(session_id, student_id),
            UNIQUE(session_id, device_id)
        );
        """
    )
    db.commit()
    db.close()


def load_students():
    if not STUDENT_FILE.exists():
        return set()
    with open(STUDENT_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def is_teacher_authenticated():
    return request.cookies.get("teacher_pin") == TEACHER_PIN


def now_str():
    return datetime.now(TIMEZONE).isoformat()


def parse_dt(s: str):
    return datetime.fromisoformat(s)


def make_qr_data_uri(content: str):
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


ADMIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Class Attendance - Teacher Panel</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; max-width: 1000px; margin: 30px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin-bottom: 20px; }
    input, button { padding: 10px; margin: 6px 0; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    .flash { background: #f3f8ff; border: 1px solid #bfd8ff; padding: 10px; margin-bottom: 12px; border-radius: 8px; }
    code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }
    a { text-decoration: none; }
  </style>
</head>
<body>
  <h1>Class Attendance - Teacher Panel</h1>

  {% with messages = get_flashed_messages() %}
    {% if messages %}
      {% for m in messages %}
        <div class="flash">{{ m }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  {% if not authenticated %}
    <div class="card">
      <h2>Teacher Login</h2>
      <form method="post" action="{{ url_for('teacher_login') }}">
        <input type="password" name="pin" placeholder="Enter teacher PIN" required>
        <button type="submit">Login</button>
      </form>
      <p>The default PIN is <code>123456</code>. It is recommended to change it in <code>app.py</code>.</p>
    </div>
  {% else %}
    <div class="card">
      <h2>Upload Student List</h2>
      <form method="post" action="{{ url_for('upload_students') }}" enctype="multipart/form-data">
        <input type="file" name="student_file" accept=".txt" required>
        <button type="submit">Upload and Replace students.txt</button>
      </form>
      <p>File format: one student ID per line. Current number of students: <strong>{{ student_count }}</strong></p>
    </div>

    <div class="card">
      <h2>Create Attendance QR Code</h2>
      <form method="post" action="{{ url_for('create_session') }}">
        <div><input type="text" name="course_name" placeholder="Course name, e.g. Communication Networks" required style="width: 320px;"></div>
        <div><input type="number" name="minutes" min="1" max="120" value="{{ default_minutes }}" required> minutes valid</div>
        <button type="submit">Generate QR Code for This Class</button>
      </form>
    </div>

    <div class="card">
      <h2>Attendance Sessions</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Course</th><th>Created At</th><th>Expires At</th><th>Status</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
        {% for s in sessions %}
          <tr>
            <td>{{ s['id'] }}</td>
            <td>{{ s['course_name'] }}</td>
            <td>{{ s['created_at'] }}</td>
            <td>{{ s['expires_at'] }}</td>
            <td>{% if s['is_active'] %}Active{% else %}Closed{% endif %}</td>
            <td>
              <a href="{{ url_for('view_qr', session_id=s['id']) }}">View QR Code</a> |
              <a href="{{ url_for('view_records', session_id=s['id']) }}">Attendance Records</a>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>

    <form method="post" action="{{ url_for('teacher_logout') }}">
      <button type="submit">Logout</button>
    </form>
  {% endif %}
</body>
</html>
"""

QR_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Attendance QR Code</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 30px auto; padding: 0 16px; text-align: center; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 18px; }
    img { max-width: 320px; }
    a { text-decoration: none; }
  </style>
</head>
<body>
  <div class="card">
    <h1>{{ session['course_name'] }}</h1>
    <p>Session ID: {{ session['id'] }}</p>
    <p>Valid until: {{ session['expires_at'] }}</p>
    <p><img src="{{ qr_data }}" alt="Attendance QR Code"></p>
    <p>Attendance link: <a href="{{ sign_url }}">{{ sign_url }}</a></p>
    <p><a href="{{ url_for('view_records', session_id=session['id']) }}">View Live Attendance Records</a></p>
    <p><a href="{{ url_for('admin') }}">Back to Teacher Panel</a></p>
  </div>
</body>
</html>
"""

SIGNIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Student Check-in</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; max-width: 560px; margin: 40px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 18px; }
    .flash { background: #fef4f4; border: 1px solid #f4c6c6; padding: 10px; margin-bottom: 12px; border-radius: 8px; }
    .ok { background: #f1fbf3; border-color: #b8e0c2; }
    input, button { padding: 10px; width: 100%; box-sizing: border-box; margin: 8px 0; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Student Check-in</h1>
    <p>Course: {{ session['course_name'] }}</p>
    <p>Valid until: {{ session['expires_at'] }}</p>

    {% if message %}
      <div class="flash {% if success %}ok{% endif %}">{{ message }}</div>
    {% endif %}

    {% if not closed and not success %}
      <form method="post" action="{{ url_for('submit_signin') }}">
        <input type="hidden" name="token" value="{{ token }}">
        <input type="hidden" id="device_id" name="device_id" value="">
        <input type="text" name="student_id" placeholder="Please enter your student ID" required>
        <button type="submit">Submit Check-in</button>
      </form>
    {% endif %}
  </div>

  <script>
    const key = 'attendance_device_id';
    let deviceId = localStorage.getItem(key);
    if (!deviceId) {
      deviceId = 'dev_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem(key, deviceId);
    }
    document.getElementById('device_id')?.setAttribute('value', deviceId);
  </script>
</body>
</html>
"""

RECORDS_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Attendance Records</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; max-width: 1000px; margin: 30px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin-bottom: 20px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    a { text-decoration: none; }
  </style>
</head>
<body>
  <div class="card">
    <h1>{{ session['course_name'] }} - Attendance Records</h1>
    <p>Session ID: {{ session['id'] }}</p>
    <p>Valid until: {{ session['expires_at'] }}</p>
    <p>Number of checked-in students: <strong>{{ records|length }}</strong></p>
    <p>
      <a href="{{ url_for('export_records', session_id=session['id']) }}">Export CSV</a> |
      <a href="{{ url_for('view_qr', session_id=session['id']) }}">Back to QR Code</a> |
      <a href="{{ url_for('admin') }}">Back to Teacher Panel</a>
    </p>
  </div>

  <table>
    <thead>
      <tr>
        <th>Student ID</th>
        <th>Check-in Time</th>
        <th>IP</th>
        <th>User-Agent</th>
      </tr>
    </thead>
    <tbody>
      {% for r in records %}
        <tr>
          <td>{{ r['student_id'] }}</td>
          <td>{{ r['signed_at'] }}</td>
          <td>{{ r['ip_address'] or '' }}</td>
          <td>{{ r['user_agent'] or '' }}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""


@app.route("/")
def home():
    return redirect(url_for("admin"))


@app.route("/admin")
def admin():
    db = get_db()
    sessions = db.execute(
        "SELECT * FROM sessions ORDER BY id DESC"
    ).fetchall()
    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=is_teacher_authenticated(),
        sessions=sessions,
        student_count=len(load_students()),
        default_minutes=DEFAULT_SIGNIN_MINUTES,
    )


@app.route("/teacher-login", methods=["POST"])
def teacher_login():
    pin = request.form.get("pin", "")
    if pin != TEACHER_PIN:
        flash("Incorrect PIN.")
        return redirect(url_for("admin"))
    resp = redirect(url_for("admin"))
    resp.set_cookie("teacher_pin", pin, httponly=True, samesite="Lax")
    flash("Login successful.")
    return resp


@app.route("/teacher-logout", methods=["POST"])
def teacher_logout():
    resp = redirect(url_for("admin"))
    resp.delete_cookie("teacher_pin")
    flash("Logged out.")
    return resp


@app.route("/upload-students", methods=["POST"])
def upload_students():
    if not is_teacher_authenticated():
        flash("Please log in to the teacher panel first.")
        return redirect(url_for("admin"))
    f = request.files.get("student_file")
    if not f or not f.filename:
        flash("Please select a txt file.")
        return redirect(url_for("admin"))
    filename = secure_filename(f.filename)
    if not filename.lower().endswith(".txt"):
        flash("Only .txt files are supported.")
        return redirect(url_for("admin"))
    content = f.read().decode("utf-8", errors="ignore")
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    with open(STUDENT_FILE, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + ("\n" if lines else ""))
    flash("Upload successful. Imported {} student IDs.".format(len(lines)))
    return redirect(url_for("admin"))


@app.route("/create-session", methods=["POST"])
def create_session():
    if not is_teacher_authenticated():
        flash("Please log in to the teacher panel first.")
        return redirect(url_for("admin"))
    if not load_students():
        flash("Please upload students.txt first.")
        return redirect(url_for("admin"))

    course_name = request.form.get("course_name", "").strip()
    minutes = int(request.form.get("minutes", DEFAULT_SIGNIN_MINUTES))
    token = secrets.token_urlsafe(16)
    created_at = datetime.now(TIMEZONE)
    expires_at = created_at + timedelta(minutes=minutes)

    db = get_db()
    db.execute(
        "INSERT INTO sessions (course_name, token, created_at, expires_at, is_active) VALUES (?, ?, ?, ?, 1)",
        (course_name, token, created_at.isoformat(), expires_at.isoformat()),
    )
    db.commit()
    session_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return redirect(url_for("view_qr", session_id=session_id))


@app.route("/session/<int:session_id>/qr")
def view_qr(session_id):
    if not is_teacher_authenticated():
        flash("Please log in to the teacher panel first.")
        return redirect(url_for("admin"))
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        flash("Attendance session does not exist.")
        return redirect(url_for("admin"))
    sign_url = request.url_root.rstrip("/") + url_for("signin", token=session["token"])
    qr_data = make_qr_data_uri(sign_url)
    return render_template_string(QR_TEMPLATE, session=session, sign_url=sign_url, qr_data=qr_data)


@app.route("/signin")
def signin():
    token = request.args.get("token", "").strip()
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if not session:
        return render_template_string(
            SIGNIN_TEMPLATE,
            session={"course_name": "Unknown Course", "expires_at": "-"},
            token=token,
            message="Invalid QR code.",
            success=False,
            closed=True,
        )

    expired = (not session["is_active"]) or (datetime.now(TIMEZONE) > parse_dt(session["expires_at"]))
    return render_template_string(
        SIGNIN_TEMPLATE,
        session=session,
        token=token,
        message="The check-in session is closed." if expired else None,
        success=False,
        closed=expired,
    )


@app.route("/submit-signin", methods=["POST"])
def submit_signin():
    token = request.form.get("token", "").strip()
    student_id = request.form.get("student_id", "").strip()
    device_id = request.form.get("device_id", "").strip()

    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if not session:
        return render_template_string(
            SIGNIN_TEMPLATE,
            session={"course_name": "Unknown Course", "expires_at": "-"},
            token=token,
            message="Invalid QR code.",
            success=False,
            closed=True,
        )

    if (not session["is_active"]) or (datetime.now(TIMEZONE) > parse_dt(session["expires_at"])):
        return render_template_string(
            SIGNIN_TEMPLATE,
            session=session,
            token=token,
            message="Check-in has expired.",
            success=False,
            closed=True,
        )

    if not student_id:
        return render_template_string(
            SIGNIN_TEMPLATE,
            session=session,
            token=token,
            message="Student ID cannot be empty.",
            success=False,
            closed=False,
        )

    if student_id not in load_students():
        return render_template_string(
            SIGNIN_TEMPLATE,
            session=session,
            token=token,
            message="Student ID is not in the list. Check-in failed.",
            success=False,
            closed=False,
        )

    existing_student = db.execute(
        "SELECT 1 FROM sign_records WHERE session_id=? AND student_id=?",
        (session["id"], student_id),
    ).fetchone()
    if existing_student:
        return render_template_string(
            SIGNIN_TEMPLATE,
            session=session,
            token=token,
            message="This student ID has already checked in.",
            success=False,
            closed=False,
        )

    if not device_id:
        return render_template_string(
            SIGNIN_TEMPLATE,
            session=session,
            token=token,
            message="Device ID is missing. Please use a normal browser and scan again.",
            success=False,
            closed=False,
        )

    existing_device = db.execute(
        "SELECT 1 FROM sign_records WHERE session_id=? AND device_id=?",
        (session["id"], device_id),
    ).fetchone()
    if existing_device:
        return render_template_string(
            SIGNIN_TEMPLATE,
            session=session,
            token=token,
            message="This phone has already been used for check-in.",
            success=False,
            closed=False,
        )

    db.execute(
        "INSERT INTO sign_records (session_id, student_id, device_id, signed_at, ip_address, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
        (
            session["id"],
            student_id,
            device_id,
            now_str(),
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent"),
        ),
    )
    db.commit()
    return render_template_string(
        SIGNIN_TEMPLATE,
        session=session,
        token=token,
        message="Check-in successful.",
        success=True,
        closed=True,
    )


@app.route("/session/<int:session_id>/records")
def view_records(session_id):
    if not is_teacher_authenticated():
        flash("Please log in to the teacher panel first.")
        return redirect(url_for("admin"))
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        flash("Attendance session does not exist.")
        return redirect(url_for("admin"))
    records = db.execute(
        "SELECT * FROM sign_records WHERE session_id=? ORDER BY signed_at ASC",
        (session_id,),
    ).fetchall()
    return render_template_string(RECORDS_TEMPLATE, session=session, records=records)


@app.route("/session/<int:session_id>/export")
def export_records(session_id):
    if not is_teacher_authenticated():
        flash("Please log in to the teacher panel first.")
        return redirect(url_for("admin"))
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        flash("Attendance session does not exist.")
        return redirect(url_for("admin"))
    rows = db.execute(
        "SELECT student_id, signed_at, ip_address, user_agent FROM sign_records WHERE session_id=? ORDER BY signed_at ASC",
        (session_id,),
    ).fetchall()
    filename = f"attendance_session_{session_id}.csv"
    filepath = UPLOAD_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("student_id,signed_at,ip_address,user_agent\n")
        for r in rows:
            ua = (r["user_agent"] or "").replace('"', '""')
            ip = (r["ip_address"] or "").replace('"', '""')
            sid = (r["student_id"] or "").replace('"', '""')
            sat = (r["signed_at"] or "").replace('"', '""')
            f.write(f'"{sid}","{sat}","{ip}","{ua}"\n')
    return send_from_directory(str(UPLOAD_DIR), filename, as_attachment=True)


if __name__ == "__main__":
    init_db()
    if not STUDENT_FILE.exists():
        STUDENT_FILE.write_text("20230001\n20230002\n20230003\n", encoding="utf-8")
    app.run(host="0.0.0.0", port=5001, debug=True)