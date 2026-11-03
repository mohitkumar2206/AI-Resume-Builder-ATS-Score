import os
import re
import sqlite3
import secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backend.ats_engine import analyze_resume
from backend.resume_parser import extract_resume_text
from backend.email_service import send_contact_notification
from backend.ai_service import improve_bullet_points, generate_summary, get_ai_ats_feedback


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

IS_VERCEL = bool(os.getenv("VERCEL"))
if IS_VERCEL:
    DATABASE = "/tmp/app.db"
    UPLOAD_DIR = "/tmp/uploads"
    GENERATED_DIR = "/tmp/generated_resumes"
else:
    DATABASE = os.path.join(BASE_DIR, "database", "app.db")
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    GENERATED_DIR = os.path.join(BASE_DIR, "generated_resumes")
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024

app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR,
    static_url_path="/static"
)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-development-secret")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 43200
os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

GENERAL_EMAIL = os.getenv("GENERAL_EMAIL", "support@example.com")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "help@example.com")


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contact_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                email_status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                job_title TEXT,
                score INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)


init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to access that page.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.context_processor
def inject_globals():
    return {
        "general_email": GENERAL_EMAIL,
        "support_email": SUPPORT_EMAIL,
        "current_user": session.get("user_name"),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/builder", methods=["GET", "POST"])
def builder():
    template_arg = request.args.get("template")
    stored_data = session.get("resume_data", {})
    current_template = template_arg or stored_data.get("template") or session.get("resume_template", "modern-blue")

    if request.method == "POST":
        data = {key: request.form.get(key, "").strip() for key in [
            "full_name", "job_title", "email", "phone", "location",
            "linkedin", "github", "summary", "education", "experience",
            "skills", "projects", "certifications"
        ]}
        chosen_template = request.form.get("template", current_template)
        data["template"] = chosen_template
        session["resume_data"] = data
        session["resume_template"] = chosen_template
        if not data["full_name"]:
            flash("Please enter your full name.", "danger")
            return render_template("builder.html", data=data, template=chosen_template)
        return render_template("resume_preview.html", data=data, template=chosen_template)

    data = dict(stored_data)
    data["template"] = current_template
    return render_template("builder.html", data=data, template=current_template)


@app.route("/ats", methods=["GET", "POST"])
def ats_checker():
    if request.method == "POST":
        resume_file = request.files.get("resume")
        job_description = request.form.get("job_description", "").strip()
        job_title = request.form.get("job_title", "").strip()

        if not resume_file or not resume_file.filename:
            flash("Choose a resume file first.", "danger")
            return render_template("ats_checker.html")
        if not allowed_file(resume_file.filename):
            flash("Supported formats are PDF, DOCX and TXT.", "danger")
            return render_template("ats_checker.html")
        if not job_description:
            flash("Paste the job description so the score can be calculated against it.", "danger")
            return render_template("ats_checker.html")

        safe_name = secure_filename(resume_file.filename)
        unique_name = f"{secrets.token_hex(8)}_{safe_name}"
        saved_path = os.path.join(UPLOAD_DIR, unique_name)
        resume_file.save(saved_path)

        try:
            resume_text = extract_resume_text(saved_path)
        except Exception as exc:
            app.logger.exception("Resume parsing failed")
            flash(f"Could not read this file. Try a text-based PDF, DOCX or TXT. Details: {exc}", "danger")
            return render_template("ats_checker.html")
        finally:
            # Files are processed for this request only; do not retain uploaded CVs.
            if os.path.exists(saved_path):
                try:
                    os.remove(saved_path)
                except OSError:
                    pass

        if len(resume_text.strip()) < 40:
            flash("Very little text was extracted. If this is a scanned PDF, OCR is needed before analysis.", "warning")
            return render_template("ats_checker.html")

        result = analyze_resume(resume_text, job_description, job_title)
        try:
            result["ai_tips"] = get_ai_ats_feedback(resume_text, job_description, result.get("missing_keywords", []))
        except Exception:
            result["ai_tips"] = []

        with get_db() as db:
            db.execute(
                "INSERT INTO analyses (user_id, job_title, score, created_at) VALUES (?, ?, ?, ?)",
                (session.get("user_id"), job_title or "Not specified", result["overall_score"],
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))
            )
        return render_template("ats_result.html", result=result, job_title=job_title)
    return render_template("ats_checker.html")


@app.route("/api/ai/enhance", methods=["POST"])
def api_ai_enhance():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "").strip()
    role = payload.get("role", "").strip()
    response = improve_bullet_points(text, role)
    return response, 200 if response.get("success") else 400


@app.route("/api/ai/summary", methods=["POST"])
def api_ai_summary():
    payload = request.get_json(silent=True) or {}
    job_title = payload.get("job_title", "").strip()
    skills = payload.get("skills", "").strip()
    experience = payload.get("experience", "").strip()
    response = generate_summary(job_title, skills, experience)
    return response, 200 if response.get("success") else 400


@app.route("/templates")
def resume_templates():
    return render_template("templates.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        category = request.form.get("category", "Suggestion").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()

        if not all([name, email, subject, message]):
            flash("Please complete all required fields.", "danger")
            return render_template("contact.html")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Please enter a valid email address.", "danger")
            return render_template("contact.html")
        if len(message) > 5000:
            flash("Please keep your message under 5,000 characters.", "danger")
            return render_template("contact.html")

        with get_db() as db:
            cursor = db.execute("""
                INSERT INTO contact_messages
                (name, email, category, subject, message, created_at, email_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, email, category, subject, message,
                  datetime.now(timezone.utc).isoformat(timespec="seconds"), "pending"))
            message_id = cursor.lastrowid

        # Database save is independent from email delivery.
        email_ok, email_detail = send_contact_notification(
            name=name, sender_email=email, category=category, subject=subject,
            message=message, general_email=GENERAL_EMAIL, support_email=SUPPORT_EMAIL
        )
        with get_db() as db:
            db.execute("UPDATE contact_messages SET email_status = ? WHERE id = ?",
                       ("sent" if email_ok else "failed", message_id))

        if email_ok:
            flash("Thank you! Your message was saved and email notifications were sent.", "success")
        else:
            flash("Your message was saved successfully, but email delivery is not configured or failed. The site owner must configure SMTP in .env.", "warning")
            app.logger.warning("Contact email delivery: %s", email_detail)
        return redirect(url_for("contact"))
    return render_template("contact.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 8:
            flash("Enter a name, valid email and password of at least 8 characters.", "danger")
            return render_template("register.html")
        try:
            with get_db() as db:
                db.execute("INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                           (name, email, generate_password_hash(password),
                            datetime.now(timezone.utc).isoformat(timespec="seconds")))
            flash("Account created. You can now log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "danger")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            flash("Welcome back!", "success")
            return redirect(url_for("index"))
        flash("Email or password is incorrect.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/history")
@login_required
def history():
    with get_db() as db:
        rows = db.execute(
            "SELECT job_title, score, created_at FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (session["user_id"],)
        ).fetchall()
    return render_template("history.html", rows=rows)


@app.errorhandler(413)
def too_large(_error):
    flash("File is too large. Maximum upload size is 8 MB.", "danger")
    return redirect(url_for("ats_checker"))


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "true").lower() == "true")
