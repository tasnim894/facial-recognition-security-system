from flask import Flask, render_template, request, redirect, session, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import base64, os, threading, functools, smtplib, requests
import psycopg2
from psycopg2.extras import RealDictCursor
import cloudinary
import cloudinary.uploader
import cloudinary.api
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from recognition_tas3 import reconnaitre_visage
import sys
import logging
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

app = Flask(__name__)

# ══════════════════════════════════════════
#   SÉCURITÉ — CONFIGURATION
# ══════════════════════════════════════════
app.secret_key = os.getenv("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY non définie dans les variables d'environnement !")

# Cookie sécurisé
app.config.update(
    SESSION_COOKIE_HTTPONLY  = True,   # Empêche JS d'accéder au cookie
    SESSION_COOKIE_SAMESITE  = "Lax",  # Protection CSRF
    SESSION_COOKIE_SECURE    = True,   # Cookie uniquement en HTTPS
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8),  # Session expire après 8h
    MAX_CONTENT_LENGTH       = 10 * 1024 * 1024  # Max 10 MB par upload
)

# ══════════════════════════════════════════
#   CONFIG CLOUDINARY
# ══════════════════════════════════════════
cloudinary.config(
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.getenv("CLOUDINARY_API_KEY"),
    api_secret = os.getenv("CLOUDINARY_API_SECRET"),
    secure     = True  # Toujours HTTPS
)

# ══════════════════════════════════════════
#   CONFIG EMAIL
# ══════════════════════════════════════════
MAIL_SERVER   = "smtp.gmail.com"
MAIL_PORT     = 587
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM", "Contrôle d'Accès <app@gmail.com>")

# ══════════════════════════════════════════
#   BASE DE DONNÉES POSTGRESQL
# ══════════════════════════════════════════
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non définie dans les variables d'environnement !")

def get_db():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory = RealDictCursor,
        connect_timeout = 10,
        sslmode = "require"  # Connexion SSL obligatoire
    )
    return conn

def get_config(conn, cle, defaut=None):
    cur = conn.cursor()
    cur.execute("SELECT valeur FROM config WHERE cle=%s", (cle,))
    row = cur.fetchone()
    return row["valeur"] if row else defaut

def set_config(conn, cle, valeur):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO config (cle,valeur) VALUES (%s,%s) "
        "ON CONFLICT(cle) DO UPDATE SET valeur=EXCLUDED.valeur",
        (cle, valeur)
    )

# ══════════════════════════════════════════
#   INIT BASE DE DONNÉES
# ══════════════════════════════════════════
def init_db():
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS utilisateurs (
                id SERIAL PRIMARY KEY,
                nom TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('responsable','lecteur')),
                photo TEXT,
                actif INTEGER NOT NULL DEFAULT 1,
                date_creation TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS actions (
                id SERIAL PRIMARY KEY,
                utilisateur_id INTEGER,
                porte TEXT,
                action TEXT,
                temps TEXT,
                statut TEXT DEFAULT 'non_valide',
                photo_capture TEXT,
                valide_par TEXT,
                nom_snapshot TEXT,
                photo_snapshot TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config (
                cle TEXT PRIMARY KEY,
                valeur TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employes_photos (
                id SERIAL PRIMARY KEY,
                nom_employe TEXT NOT NULL,
                photo_url TEXT NOT NULL,
                public_id TEXT,
                date_ajout TEXT
            )
        """)
        # Table pour bloquer les tentatives de connexion
        cur.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id SERIAL PRIMARY KEY,
                ip TEXT,
                email TEXT,
                tentatives INTEGER DEFAULT 1,
                derniere_tentative TEXT,
                bloque_jusqu TEXT
            )
        """)
        conn.commit()
        conn.close()
        print("[DB] Base de données initialisée ✅")
    except Exception as e:
        print(f"[DB ERROR] {e}")

init_db()

# ══════════════════════════════════════════
#   UTILISATEURS PAR DÉFAUT
# ══════════════════════════════════════════
def add_default_users():
    try:
        conn = get_db()
        cur  = conn.cursor()
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # MOT DE PASSE FORT par défaut — à changer après connexion !
        users = [
            ("Hadil", "hadilmiledi884@gmail.com",
             generate_password_hash("Galpharma@2026!"),
             "responsable", "", 1, now),
            ("Tasnim", "tasnimyaich634@gmail.com",
             generate_password_hash("Galpharma@2026!"),
             "lecteur", "", 1, now),
        ]
        for u in users:
            cur.execute(
                "INSERT INTO utilisateurs (nom,email,password,role,photo,actif,date_creation) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(email) DO NOTHING", u
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[INIT USERS] {e}")

add_default_users()

# ══════════════════════════════════════════
#   SÉCURITÉ — PROTECTION BRUTE FORCE
# ══════════════════════════════════════════
MAX_TENTATIVES = 5  # Nombre max de tentatives
BLOCAGE_MINUTES = 15  # Durée de blocage en minutes

def verifier_blocage(ip, email):
    """Vérifie si l'IP ou l'email est bloqué."""
    try:
        conn = get_db(); cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT * FROM login_attempts WHERE ip=%s OR email=%s",
            (ip, email)
        )
        row = cur.fetchone()
        conn.close()
        if row and row["bloque_jusqu"] and row["bloque_jusqu"] > now:
            return True, row["bloque_jusqu"]
        return False, None
    except:
        return False, None

def enregistrer_tentative(ip, email, succes):
    """Enregistre une tentative de connexion."""
    try:
        conn = get_db(); cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if succes:
            cur.execute(
                "DELETE FROM login_attempts WHERE ip=%s OR email=%s",
                (ip, email)
            )
        else:
            cur.execute(
                "SELECT * FROM login_attempts WHERE ip=%s OR email=%s",
                (ip, email)
            )
            row = cur.fetchone()
            if row:
                nouvelles_tentatives = row["tentatives"] + 1
                bloque = None
                if nouvelles_tentatives >= MAX_TENTATIVES:
                    bloque_dt = datetime.now() + timedelta(minutes=BLOCAGE_MINUTES)
                    bloque = bloque_dt.strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    "UPDATE login_attempts SET tentatives=%s, derniere_tentative=%s, bloque_jusqu=%s "
                    "WHERE ip=%s OR email=%s",
                    (nouvelles_tentatives, now, bloque, ip, email)
                )
            else:
                cur.execute(
                    "INSERT INTO login_attempts (ip,email,tentatives,derniere_tentative) VALUES (%s,%s,1,%s)",
                    (ip, email, now)
                )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LOGIN ATTEMPT] {e}")

# ══════════════════════════════════════════
#   AUTH HELPERS
# ══════════════════════════════════════════
def login_required():
    if "user_id" not in session:
        abort(403)

def responsable_requis(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"message": "Non connecté."}), 401
        if session.get("role") != "responsable":
            return jsonify({"message": "Accès réservé au responsable."}), 403
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════
#   HEADERS DE SÉCURITÉ
# ══════════════════════════════════════════
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["X-Frame-Options"]           = "DENY"
    response.headers["X-XSS-Protection"]          = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ══════════════════════════════════════════
#   EMAIL
# ══════════════════════════════════════════
def envoyer_email(dest_email, dest_nom, username_login, password_clair, role):
    try:
        conn = get_db()
        mail_user = get_config(conn, "mail_username", MAIL_USERNAME or "")
        mail_pwd  = get_config(conn, "mail_password", MAIL_PASSWORD or "")
        mail_from = get_config(conn, "mail_from", MAIL_FROM or "")
        conn.close()
        if not mail_user or not mail_pwd:
            print("[MAIL] Config email non définie"); return
        role_label = "Responsable" if role == "responsable" else "Lecteur"
        html = f"""<!DOCTYPE html><html lang="fr"><body style="font-family:sans-serif;background:#f0f4fa;padding:40px;">
<div style="max-width:560px;margin:auto;background:white;border-radius:16px;overflow:hidden;">
  <div style="background:linear-gradient(135deg,#0f1e3d,#1e3a8a);padding:32px;text-align:center;">
    <h1 style="color:white;margin:0;">Contrôle d'Accès</h1></div>
  <div style="padding:36px;">
    <p>Bonjour <strong>{dest_nom}</strong>,</p>
    <p style="color:#6b7a99;">Votre compte a été créé. Voici vos identifiants :</p>
    <table width="100%" style="background:#f8faff;border:1px solid #dde4f0;border-radius:12px;">
      <tr><td style="padding:16px 20px;border-bottom:1px solid #dde4f0;">
        <span style="font-size:.75rem;color:#6b7a99;">IDENTIFIANT</span>
        <div style="font-family:monospace;font-weight:700;">{username_login}</div></td></tr>
      <tr><td style="padding:16px 20px;border-bottom:1px solid #dde4f0;">
        <span style="font-size:.75rem;color:#6b7a99;">MOT DE PASSE TEMPORAIRE</span>
        <div style="font-family:monospace;font-weight:700;">{password_clair}</div></td></tr>
      <tr><td style="padding:16px 20px;">
        <span style="font-size:.75rem;color:#6b7a99;">RÔLE</span>
        <div style="font-weight:700;">{role_label}</div></td></tr>
    </table>
    <p style="color:#e53e3e;font-size:0.85rem;margin-top:16px;">
      ⚠️ Changez votre mot de passe dès la première connexion.
    </p>
    <p style="color:#6b7a99;margin-top:24px;">Cordialement,<br>
      <strong style="color:#1a2340;">L'équipe Contrôle d'Accès</strong></p>
  </div></div></body></html>"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Vos identifiants — Contrôle d'Accès"
        msg["From"]    = mail_from
        msg["To"]      = dest_email
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            server.ehlo(); server.starttls()
            server.login(mail_user, mail_pwd)
            server.sendmail(mail_user, dest_email, msg.as_string())
        print(f"[MAIL] Envoyé à {dest_email} ✅")
    except Exception as e:
        print(f"[MAIL ERROR] {e}")

# ══════════════════════════════════════════
#   CLOUDINARY — PRÉPARER DATABASE LOCALE
# ══════════════════════════════════════════
def preparer_database_locale():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT nom_employe, photo_url FROM employes_photos")
        photos = cur.fetchall(); conn.close()
        db_dir = "database"
        os.makedirs(db_dir, exist_ok=True)
        for p in photos:
            nom = p["nom_employe"]; url = p["photo_url"]
            dossier = os.path.join(db_dir, nom)
            os.makedirs(dossier, exist_ok=True)
            dest = os.path.join(dossier, "photo.jpg")
            if not os.path.exists(dest):
                r = requests.get(url, timeout=10)
                with open(dest, "wb") as f:
                    f.write(r.content)
                print(f"[DB] Photo téléchargée : {nom} ✅")
    except Exception as e:
        print(f"[DB LOCALE] {e}")

# ══════════════════════════════════════════
#   ROUTE — LOGIN SÉCURISÉ
# ══════════════════════════════════════════
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email    = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        ip       = request.headers.get("X-Forwarded-For", request.remote_addr)

        # Vérifier blocage brute force
        bloque, bloque_jusqu = verifier_blocage(ip, email)
        if bloque:
            minutes_restantes = max(0, int((
                datetime.strptime(bloque_jusqu, "%Y-%m-%d %H:%M:%S") - datetime.now()
            ).total_seconds() / 60))
            return render_template("login.html",
                error=f"Trop de tentatives. Réessayez dans {minutes_restantes} minutes.")

        # Validation basique
        if not email or not password:
            return render_template("login.html", error="Email et mot de passe requis.")
        if len(password) > 200:
            return render_template("login.html", error="Données invalides.")

        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM utilisateurs WHERE email=%s AND actif=1", (email,))
            user = cur.fetchone(); conn.close()
        except Exception as e:
            print(f"[LOGIN DB] {e}")
            return render_template("login.html", error="Erreur serveur. Réessayez.")

        if user and check_password_hash(user["password"], password):
            enregistrer_tentative(ip, email, succes=True)
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["nom"]     = user["nom"]
            session["role"]    = user["role"]
            session["photo"]   = user["photo"] or ""
            return redirect("/dashboard")

        enregistrer_tentative(ip, email, succes=False)
        return render_template("login.html", error="Email ou mot de passe incorrect.")

    return render_template("login.html")

# ══════════════════════════════════════════
#   ROUTES — DASHBOARD / USERS / EMPLOYES
# ══════════════════════════════════════════
@app.route("/dashboard")
def dashboard():
    login_required()
    return render_template("index.html",
        nom=session["nom"], role=session["role"], photo=session.get("photo",""))

@app.route("/users")
def page_users():
    if "user_id" not in session: return redirect("/")
    if session.get("role") != "responsable": return redirect("/dashboard")
    return render_template("users.html",
        nom=session["nom"], role=session["role"], photo=session.get("photo",""))

@app.route("/employes")
def page_employes():
    if "user_id" not in session: return redirect("/")
    if session.get("role") != "responsable": return redirect("/dashboard")
    return render_template("employes.html",
        nom=session["nom"], role=session["role"])

# ══════════════════════════════════════════
#   API — UTILISATEURS
# ══════════════════════════════════════════
@app.route("/get_users")
@responsable_requis
def get_users():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id,nom,email,role,actif,date_creation FROM utilisateurs "
                "WHERE role!='detecte' ORDER BY id DESC")
    rows = cur.fetchall(); conn.close()
    return jsonify([{
        "id": r["id"], "nom": r["nom"], "username": r["email"],
        "role": r["role"], "actif": bool(r["actif"]),
        "date_creation": r["date_creation"]
    } for r in rows])

@app.route("/add_user", methods=["POST"])
@responsable_requis
def add_user():
    data     = request.get_json()
    nom      = (data.get("nom") or "").strip()
    email    = (data.get("username") or "").strip().lower()
    password = data.get("password","")
    role     = data.get("role","")
    if not nom or not email or not password or role not in ("responsable","lecteur"):
        return jsonify({"message": "Données invalides."}), 400
    if len(password) < 8:
        return jsonify({"message": "Mot de passe trop court (min. 8 caractères)."}), 400
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO utilisateurs (nom,email,password,role,photo,actif,date_creation) "
            "VALUES (%s,%s,%s,%s,%s,1,%s)",
            (nom, email, generate_password_hash(password), role, "", now)
        )
        conn.commit(); conn.close()
    except Exception as e:
        if "unique" in str(e).lower():
            return jsonify({"message": "Cet email est déjà utilisé."}), 409
        return jsonify({"message": "Erreur base de données."}), 500
    threading.Thread(
        target=envoyer_email,
        args=(email, nom, email, password, role), daemon=True
    ).start()
    return jsonify({"message": "Utilisateur créé. Un email a été envoyé."}), 201

@app.route("/update_user/<int:uid>", methods=["PUT"])
@responsable_requis
def update_user(uid):
    data     = request.get_json()
    nom      = (data.get("nom") or "").strip()
    username = (data.get("username") or "").strip().lower()
    password = data.get("password","")
    role     = data.get("role","")
    if not nom or not username or role not in ("responsable","lecteur"):
        return jsonify({"message": "Données invalides."}), 400
    try:
        conn = get_db(); cur = conn.cursor()
        if password:
            if len(password) < 8:
                conn.close()
                return jsonify({"message": "Mot de passe trop court (min. 8 caractères)."}), 400
            cur.execute(
                "UPDATE utilisateurs SET nom=%s,email=%s,password=%s,role=%s WHERE id=%s",
                (nom, username, generate_password_hash(password), role, uid)
            )
        else:
            cur.execute(
                "UPDATE utilisateurs SET nom=%s,email=%s,role=%s WHERE id=%s",
                (nom, username, role, uid)
            )
        conn.commit(); conn.close()
    except Exception as e:
        if "unique" in str(e).lower():
            return jsonify({"message": "Cet email est déjà utilisé."}), 409
        return jsonify({"message": "Erreur base de données."}), 500
    return jsonify({"message": "Utilisateur mis à jour."})

@app.route("/toggle_user/<int:uid>", methods=["POST"])
@responsable_requis
def toggle_user(uid):
    if uid == session.get("user_id"):
        return jsonify({"message": "Impossible de désactiver votre propre compte."}), 403
    data  = request.get_json()
    actif = 1 if data.get("actif") else 0
    conn  = get_db(); cur = conn.cursor()
    cur.execute("UPDATE utilisateurs SET actif=%s WHERE id=%s", (actif, uid))
    conn.commit(); conn.close()
    return jsonify({"message": "Statut mis à jour."})

@app.route("/delete_user/<int:uid>", methods=["DELETE"])
@responsable_requis
def delete_user(uid):
    if uid == session.get("user_id"):
        return jsonify({"message": "Impossible de supprimer votre propre compte."}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM utilisateurs WHERE id=%s", (uid,))
    conn.commit(); conn.close()
    return jsonify({"message": "Utilisateur supprimé."})

# ══════════════════════════════════════════
#   API — PHOTOS EMPLOYÉS
# ══════════════════════════════════════════
@app.route("/get_employes")
@responsable_requis
def get_employes():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM employes_photos ORDER BY id DESC")
    rows = cur.fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/add_employe_photo", methods=["POST"])
@responsable_requis
def add_employe_photo():
    nom        = (request.form.get("nom") or "").strip()
    photo_file = request.files.get("photo")
    if not nom or not photo_file:
        return jsonify({"message": "Nom et photo requis."}), 400
    # Vérifier type de fichier
    allowed = {"image/jpeg","image/png","image/jpg","image/webp"}
    if photo_file.mimetype not in allowed:
        return jsonify({"message": "Format non supporté. Utilisez JPG ou PNG."}), 400
    try:
        result    = cloudinary.uploader.upload(
            photo_file,
            folder    = f"employes/{nom}",
            public_id = f"{nom}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            overwrite = True,
            secure    = True
        )
        photo_url = result["secure_url"]
        public_id = result["public_id"]
        now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO employes_photos (nom_employe,photo_url,public_id,date_ajout) VALUES (%s,%s,%s,%s)",
            (nom, photo_url, public_id, now)
        )
        conn.commit(); conn.close()
        chemin_local = os.path.join("database", nom, "photo.jpg")
        if os.path.exists(chemin_local):
            os.remove(chemin_local)
        return jsonify({"message": "Photo ajoutée avec succès.", "url": photo_url}), 201
    except Exception as e:
        return jsonify({"message": f"Erreur upload : {str(e)}"}), 500

@app.route("/delete_employe_photo/<int:pid>", methods=["DELETE"])
@responsable_requis
def delete_employe_photo(pid):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM employes_photos WHERE id=%s", (pid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"message": "Introuvable."}), 404
    try:
        cloudinary.uploader.destroy(row["public_id"])
    except Exception as e:
        print(f"[CLOUDINARY DELETE] {e}")
    cur.execute("DELETE FROM employes_photos WHERE id=%s", (pid,))
    conn.commit(); conn.close()
    return jsonify({"message": "Photo supprimée."})

# ══════════════════════════════════════════
#   API — CONFIG EMAIL
# ══════════════════════════════════════════
@app.route("/get_email_config")
@responsable_requis
def get_email_config():
    conn = get_db()
    username = get_config(conn, "mail_username", MAIL_USERNAME or "")
    from_    = get_config(conn, "mail_from", MAIL_FROM or "")
    has_pwd  = bool(get_config(conn, "mail_password", ""))
    conn.close()
    return jsonify({"mail_username": username, "mail_from": from_, "has_password": has_pwd})

@app.route("/save_email_config", methods=["POST"])
@responsable_requis
def save_email_config():
    data     = request.get_json()
    username = (data.get("mail_username") or "").strip()
    from_    = (data.get("mail_from") or "").strip()
    password = (data.get("mail_password") or "").strip()
    if not username or not from_:
        return jsonify({"message": "Email et nom expéditeur obligatoires."}), 400
    conn = get_db()
    set_config(conn, "mail_username", username)
    set_config(conn, "mail_from", from_)
    if password:
        set_config(conn, "mail_password", password)
    conn.commit(); conn.close()
    return jsonify({"message": "Configuration email sauvegardée."})

@app.route("/test_email_config", methods=["POST"])
@responsable_requis
def test_email_config():
    data = request.get_json()
    dest = (data.get("dest") or "").strip()
    if not dest:
        return jsonify({"message": "Adresse de test manquante."}), 400
    conn = get_db()
    username = get_config(conn, "mail_username", MAIL_USERNAME or "")
    password = get_config(conn, "mail_password", MAIL_PASSWORD or "")
    from_    = get_config(conn, "mail_from", MAIL_FROM or "")
    conn.close()
    if not username or not password:
        return jsonify({"message": "Configurez d'abord l'email et le mot de passe."}), 400
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Test — Contrôle d'Accès"
        msg["From"]    = from_
        msg["To"]      = dest
        msg.attach(MIMEText(
            f"<p>Test réussi depuis <strong>{username}</strong></p>",
            "html", "utf-8"
        ))
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            server.ehlo(); server.starttls()
            server.login(username, password)
            server.sendmail(username, dest, msg.as_string())
        return jsonify({"message": f"Email de test envoyé à {dest}."})
    except Exception as e:
        return jsonify({"message": f"Échec : {str(e)}"}), 500
def traiter(photo_path, porte, action):
    print("[TRAITER] Début traitement...")
    preparer_database_locale()
    
    conn = get_db()
    cur  = conn.cursor()
    nom_reconnu = "Inconnu"
    
    if not photo_path:
        print("[TRAITER] ❌ Pas de photo")
        conn.close()
        return

    try:
        nom_reconnu = reconnaitre_visage(photo_path)
        print(f"[TRAITER] Résultat: {nom_reconnu}")
    except Exception as e:
        print(f"[TRAITER] ❌ Erreur reconnaissance: {e}")

    # Upload photo sur Cloudinary
    capture_url = f"/{photo_path}"
    try:
        res = cloudinary.uploader.upload(photo_path, folder="captures", secure=True)
        capture_url = res["secure_url"]
        print(f"[TRAITER] ✅ Photo uploadée sur Cloudinary")
    except Exception as e:
        print(f"[TRAITER] ❌ Erreur Cloudinary: {e}")

    cur.execute("SELECT id FROM utilisateurs WHERE nom=%s", (nom_reconnu,))
    user = cur.fetchone()
    utilisateur_id = user["id"] if user else None

    cur.execute("""
        INSERT INTO actions
        (utilisateur_id,porte,action,temps,statut,photo_capture,nom_snapshot,photo_snapshot)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        utilisateur_id, porte, action,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "non_valide", capture_url, nom_reconnu, capture_url
    ))
    conn.commit()
    conn.close()
    print(f"[TRAITER] ✅ Action enregistrée pour {nom_reconnu}")

# ══════════════════════════════════════════
#   API — RECONNAISSANCE FACIALE
# ══════════════════════════════════════════
@app.route("/reconnaissance", methods=["POST"])
def reconnaissance():
    print("[ROUTE] /reconnaissance appelée !")
    data = request.json
    if not data:
        print("[ROUTE] ❌ Pas de données reçues")
        return jsonify({"success": False}), 400

    porte        = data.get("porte", "Porte Principale")
    action       = data.get("action", "Ouverture")
    photo_base64 = data.get("photo", "")

    print(f"[ROUTE] Porte: {porte}, Action: {action}")
    print(f"[ROUTE] Photo base64 longueur: {len(photo_base64)} chars")

    photo_path = ""
    if photo_base64 and photo_base64.strip():
        os.makedirs("static/captures", exist_ok=True)
        filename   = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"
        photo_path = f"static/captures/{filename}"
        try:
            b64 = photo_base64.strip().replace("\n","").replace("\r","")
            missing = len(b64) % 4
            if missing: b64 += "=" * (4 - missing)
            with open(photo_path, "wb") as f:
                f.write(base64.b64decode(b64))
            print(f"[ROUTE] ✅ Photo sauvegardée: {photo_path}")
        except Exception as e:
            print(f"[ROUTE] ❌ Erreur sauvegarde: {e}")
            photo_path = ""

    print("[ROUTE] Appel de traiter()...")
    try:
        traiter(photo_path, porte, action)
        print("[ROUTE] ✅ traiter() terminé")
    except Exception as e:
        print(f"[ROUTE] ❌ Erreur dans traiter(): {e}")

    return jsonify({"success": True, "message": "Photo reçue, traitement en cours"})

# ══════════════════════════════════════════
#   API — ACTIONS
# ══════════════════════════════════════════
@app.route("/get_actions")
def get_actions():
    login_required()
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT actions.id,
               COALESCE(actions.nom_snapshot, utilisateurs.nom, 'Inconnu') as nom,
               COALESCE(actions.photo_snapshot, utilisateurs.photo, '') as photo,
               actions.porte, actions.action, actions.temps,
               actions.statut, actions.photo_capture, actions.valide_par
        FROM actions
        LEFT JOIN utilisateurs ON actions.utilisateur_id = utilisateurs.id
        ORDER BY actions.id DESC LIMIT 100
    """)
    rows = cur.fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/valider/<int:id>", methods=["POST"])
def valider(id):
    login_required()
    if session.get("role") != "responsable": abort(403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE actions SET statut='valide',valide_par=%s WHERE id=%s",
                (session["nom"], id))
    conn.commit(); conn.close()
    return jsonify({"success": True})

@app.route("/delete_action/<int:id>", methods=["DELETE"])
def delete_action(id):
    login_required()
    if session.get("role") != "responsable": abort(403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM actions WHERE id=%s", (id,))
    conn.commit(); conn.close()
    return jsonify({"success": True})

@app.route("/clear_actions", methods=["POST"])
def clear_actions():
    login_required()
    if session.get("role") != "responsable": abort(403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM actions")
    conn.commit(); conn.close()
    return jsonify({"success": True})

# ══════════════════════════════════════════
#   LOGOUT
# ══════════════════════════════════════════
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.errorhandler(403)
def forbidden(e):
    return render_template("login.html", error="Accès interdit."), 403

@app.errorhandler(404)
def not_found(e):
    return redirect("/")

@app.errorhandler(413)
def too_large(e):
    return jsonify({"message": "Fichier trop volumineux (max 10 MB)."}), 413

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
