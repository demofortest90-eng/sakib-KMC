"""
EduPlatform - Flask application factory.

Wires up config, MongoDB, Flask-Login, Flask-SocketIO, blueprints,
and database indexes.
"""
import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, redirect, url_for, render_template

from .extensions import socketio, login_manager, init_db
from .models import User, ensure_indexes, seed_defaults

load_dotenv()


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
    app.config["MONGO_URI"] = os.getenv("MONGO_URI", "")
    app.config["MONGO_DB_NAME"] = os.getenv("MONGO_DB_NAME", "eduplat")
    app.config["IMGBB_API_KEY"] = os.getenv("IMGBB_API_KEY", "")
    app.config["ADMIN_ACCESS_CODE"] = os.getenv("ADMIN_ACCESS_CODE", "123")
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB uploads

    # --- Database ---
    init_db(app.config["MONGO_URI"], app.config["MONGO_DB_NAME"])
    ensure_indexes()
    seed_defaults()

    # --- Login manager ---
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(user_id)

    # --- SocketIO ---
    socketio.init_app(app)

    # --- Blueprints ---
    from .auth.routes import auth_bp
    from .admin.routes import admin_bp
    from .teacher.routes import teacher_bp
    from .student.routes import student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(teacher_bp, url_prefix="/teacher")
    app.register_blueprint(student_bp, url_prefix="/student")

    # Register socket event handlers
    from .sockets import events  # noqa: F401

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    # --- Error handlers (Bangla) ---
    def _err(code, title, msg):
        return render_template("errors/error.html", code=code, title=title, message=msg), code

    @app.errorhandler(400)
    def e400(e):
        return _err(400, "ভুল অনুরোধ", "আপনার পাঠানো অনুরোধটি বোঝা যায়নি।")

    @app.errorhandler(401)
    def e401(e):
        return _err(401, "প্রবেশের অনুমতি নেই", "এই পেজ দেখতে আগে লগইন করুন।")

    @app.errorhandler(403)
    def e403(e):
        return _err(403, "নিষিদ্ধ", "এই পেজে আপনার প্রবেশাধিকার নেই।")

    @app.errorhandler(404)
    def e404(e):
        return _err(404, "পেজ খুঁজে পাওয়া যায়নি", "আপনি যে পেজটি খুঁজছেন সেটি নেই বা সরিয়ে ফেলা হয়েছে।")

    @app.errorhandler(413)
    def e413(e):
        return _err(413, "ফাইল অনেক বড়", "আপলোড করা ফাইলটির আকার সীমার চেয়ে বেশি।")

    @app.errorhandler(500)
    def e500(e):
        return _err(500, "সার্ভার সমস্যা", "দুঃখিত, কিছু একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।")

    # Jinja helpers
    @app.template_filter("dt")
    def _fmt_dt(value):
        if not value:
            return ""
        if isinstance(value, str):
            return value
        return value.strftime("%d %b %Y, %I:%M %p")

    @app.context_processor
    def inject_globals():
        return {"now": datetime.utcnow()}

    return app
