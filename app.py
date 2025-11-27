import os
import io
import json
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
import PyPDF2
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
import google.generativeai as genai
from config import GEMINI_API_KEY, UPLOAD_FOLDER, ALLOWED_EXTENSIONS

# ---------- Flask App ----------
app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = "super_secret_key"
DB_PATH = os.path.join(os.getcwd(), "evaluations.db")

# ---------- Login Manager ----------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT id, username, password FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
    return User(*row) if row else None

# ---------- Database Setup ----------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                filename TEXT,
                jd TEXT,
                result_json TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

def save_evaluation(user_id, filename, jd, result_json):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO evaluations (user_id, filename, jd, result_json) VALUES (?, ?, ?, ?)",
            (user_id, filename, jd, result_json)
        )

def fetch_all(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT id, filename, date, result_json FROM evaluations WHERE user_id=? ORDER BY date DESC",
            (user_id,)
        ).fetchall()

# ---------- Configure Gemini ----------
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

def safe_parse_json(text):
    try:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
        return json.loads(text)
    except Exception:
        return None

# ---------- Helper Functions ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(file_path):
    text = ""
    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ""
    return text

def evaluate_resume(resume_text, jd_text):
    prompt = f"""
    You are an AI resume evaluation assistant.

    Compare the candidate's resume against the given job description and provide a detailed evaluation.

    Please follow these instructions strictly:
    - Respond with **only valid JSON**, no markdown, no explanations, no code fences.
    - Use this exact JSON structure:
    {{
        "overall_score": (integer from 0-10 representing overall fit),
        "sub_scores": {{
            "skills": (integer 0-10),
            "experience": (integer 0-10),
            "education": (integer 0-10),
            "domain_knowledge": (integer 0-10)
        }},
        "summary": "A 1–2 sentence summary of how well the resume matches the job.",
        "skills": {{
            "matched": ["list", "of", "skills", "found", "in", "resume"],
            "missing": ["list", "of", "important", "skills", "missing"],
            "recommended_improvements": ["list", "of", "specific", "improvements"]
        }}
    }}

    Resume Text:
    {resume_text}

    Job Description:
    {jd_text}
    """

    try:
        ai_response = model.generate_content(prompt)
        parsed = safe_parse_json(ai_response.text)
    except Exception as e:
        print("Gemini evaluation error:", e)
        parsed = None

    if not parsed:
        parsed = {
            "overall_score": 0,
            "sub_scores": {},
            "summary": "⚠ Could not parse AI response or API error occurred.",
            "skills": {"matched": [], "missing": [], "recommended_improvements": []}
        }

    return parsed


def generate_pdf_report(result):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    title_style = ParagraphStyle('Title', parent=styles['Title'], fontSize=18, leading=24, alignment=1)
    elements.append(Paragraph("Resume Evaluation Report", title_style))
    elements.append(Spacer(1, 0.3 * inch))

    # Basic Info
    info_text = f"""
    <b>Filename:</b> {result.get('filename', 'N/A')}<br/>
    <b>Overall Score:</b> {result.get('overall_score', 'N/A')}/10
    """
    elements.append(Paragraph(info_text, styles['Normal']))
    elements.append(Spacer(1, 0.2 * inch))

    # Score Table
    sub = result.get("sub_scores", {})
    data = [
        ["Criteria", "Score (/10)"],
        ["Skills", sub.get("skills", "N/A")],
        ["Experience", sub.get("experience", "N/A")],
        ["Education", sub.get("education", "N/A")],
        ["Domain Knowledge", sub.get("domain_knowledge", "N/A")],
    ]

    table = Table(data, colWidths=[3*inch, 2*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 0.3 * inch))

    # Summary
    summary_text = f"<b>Summary:</b><br/>{result.get('summary', 'No summary available.')}"
    elements.append(Paragraph(summary_text, styles['Normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer

# ---------- Routes ----------
@app.route("/")
def home():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    return redirect(url_for("index"))

@app.route("/upload", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        jd_text = request.form.get("jd", "").strip()
        resumes = request.files.getlist("resumes")
        if not jd_text or not resumes:
            return render_template("index.html", error="⚠ Please provide JD and resumes.")
        results = []
        for file in resumes:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                file.save(path)
                resume_text = extract_text_from_pdf(path)
                analysis = evaluate_resume(resume_text, jd_text)
                analysis["filename"] = filename
                results.append(analysis)
                save_evaluation(current_user.id, filename, jd_text, json.dumps(analysis))
        results.sort(key=lambda r: r.get("overall_score", 0), reverse=True)
        top_score = results[0]["overall_score"] if results else 0
        return render_template("results.html", results=results, top_score=top_score)
    return render_template("index.html")
@app.route("/download/<filename>")
@login_required
def download(filename):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT result_json FROM evaluations WHERE user_id=? AND filename=? ORDER BY id DESC LIMIT 1",
            (current_user.id, filename)
        )
        row = cur.fetchone()
    if not row:
        return "❌ Report not found.", 404
    result = json.loads(row[0])
    pdf_file = generate_pdf_report(result)
    return send_file(pdf_file, as_attachment=True, download_name=f"{filename}_report.pdf", mimetype="application/pdf")

@app.route("/history")
@login_required
def history():
    rows = fetch_all(current_user.id)
    results = [{"filename": r[1], "date": r[2], "result": json.loads(r[3])} for r in rows]
    return render_template("history.html", results=results)

@app.route("/dashboard")
@login_required
def dashboard():
    rows = fetch_all(current_user.id)
    data = []
    for r in rows:
        res = json.loads(r[3])
        data.append({
            "filename": r[1],
            "score": res.get("overall_score", 0),
            "skills": res.get("sub_scores", {}).get("skills", 0),
            "experience": res.get("sub_scores", {}).get("experience", 0),
            "education": res.get("sub_scores", {}).get("education", 0),
            "domain_knowledge": res.get("sub_scores", {}).get("domain_knowledge", 0)
        })
    return render_template("dashboard.html", data=data)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("SELECT id, username, password FROM users WHERE username=? AND password=?", (username, password))
            row = cur.fetchone()
        if row:
            user = User(*row)
            login_user(user)
            return redirect(url_for("index"))
        flash("❌ Invalid credentials.")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        with sqlite3.connect(DB_PATH) as conn:
            try:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
                flash("✅ Registration successful. Please log in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("⚠ Username already exists.")
    return render_template("register.html")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))

# ---------- Run ----------
if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True)
