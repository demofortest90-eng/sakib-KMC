"""
Authentication blueprint.

Handles the registration / login flows for the three roles, including the
free vs paid approval logic described in the project plan.
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, current_app)
from flask_login import login_user, logout_user, login_required, current_user

from ..models import (User, users, courses, create_student, settings_col,
                      push_notification, oid)
from ..gamification import register_daily_login

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")


def _home_for(user):
    role = user.doc.get("role")
    if role == "admin":
        return url_for("admin.dashboard")
    if role == "teacher":
        return url_for("teacher.dashboard")
    return url_for("student.dashboard")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))

    if request.method == "POST":
        mode = request.form.get("mode", "student")

        # ---- Admin login via access code ----
        if mode == "admin":
            code = request.form.get("access_code", "")
            if code == current_app.config["ADMIN_ACCESS_CODE"]:
                admin = users().find_one({"role": "admin"})
                if not admin:
                    # bootstrap a single admin account on first use
                    from werkzeug.security import generate_password_hash
                    res = users().insert_one({
                        "name": "Administrator", "role": "admin",
                        "status": "approved", "theme": "light",
                        "password_hash": generate_password_hash(code),
                    })
                    admin = users().find_one({"_id": res.inserted_id})
                login_user(User(admin))
                return redirect(url_for("admin.dashboard"))
            flash("অ্যাডমিন এক্সেস কোড সঠিক নয়।", "error")
            return redirect(url_for("auth.login"))

        # ---- Teacher login (username + password) ----
        if mode == "teacher":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = User.by_username(username)
            if user and user.doc.get("role") == "teacher" and user.check_password(password):
                login_user(user)
                return redirect(url_for("teacher.dashboard"))
            flash("শিক্ষকের ইউজারনেম বা পাসওয়ার্ড সঠিক নয়।", "error")
            return redirect(url_for("auth.login"))

        # ---- Student login (email + password) ----
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.by_email(email)

        if not user or user.doc.get("role") != "student":
            flash("কোনো একাউন্ট পাওয়া যায়নি। আগে রেজিস্টার করুন।", "error")
            return redirect(url_for("auth.login"))
        if not user.check_password(password):
            flash("পাসওয়ার্ড সঠিক নয়।", "error")
            return redirect(url_for("auth.login"))

        status = user.doc.get("status")
        if status == "pending":
            flash("আপনার একাউন্ট অ্যাডমিনের অনুমোদনের অপেক্ষায় আছে (পেইড কোর্স)।", "warning")
            return redirect(url_for("auth.login"))
        if status == "rejected":
            flash("আপনার রেজিস্ট্রেশন বাতিল হয়েছে। অ্যাডমিনের সাথে যোগাযোগ করুন।", "error")
            return redirect(url_for("auth.login"))

        login_user(user)
        streak = register_daily_login(user.id)
        flash(f"আবার স্বাগতম! বর্তমান স্ট্রিক: {streak} দিন।", "success")
        return redirect(url_for("student.dashboard"))

    course_list = list(courses().find())
    return render_template("auth/login.html", courses=course_list)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))

    course_list = list(courses().find())

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        selected = request.form.getlist("course_ids")

        if not (name and email and password):
            flash("সব ঘর পূরণ করা আবশ্যক।", "error")
            return redirect(url_for("auth.register"))
        if not selected:
            flash("অন্তত একটি কোর্স নির্বাচন করুন।", "error")
            return redirect(url_for("auth.register"))
        if User.by_email(email):
            flash("এই ইমেইলে একটি একাউন্ট আগে থেকেই আছে।", "error")
            return redirect(url_for("auth.register"))

        # Determine approval: if ALL chosen courses are free -> auto approve.
        # If any chosen course is paid -> pending admin approval.
        chosen = list(courses().find({"_id": {"$in": [oid(c) for c in selected if oid(c)]}}))
        any_paid = any(c.get("mode") == "paid" for c in chosen)
        status = "pending" if any_paid else "approved"

        sid = create_student(name, email, password, selected, status)

        if status == "approved":
            flash("রেজিস্ট্রেশন সফল! এখন লগইন করতে পারবেন।", "success")
        else:
            # notify all admins
            for adm in users().find({"role": "admin"}):
                push_notification(str(adm["_id"]),
                                  f"নতুন পেইড রেজিস্ট্রেশন অপেক্ষমান: {name}", "approval")
            flash("রেজিস্ট্রেশন গৃহীত হয়েছে। পেইড কোর্সের জন্য লগইনের আগে অ্যাডমিনের অনুমোদন প্রয়োজন।",
                  "warning")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", courses=course_list)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("আপনি লগআউট করেছেন।", "info")
    return redirect(url_for("auth.login"))
