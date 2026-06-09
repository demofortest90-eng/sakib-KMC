"""
Admin blueprint.

The admin can see and manage everything: approve/reject students, create
courses and set free/paid mode, add teachers, view activity logs and analytics.
"""
from datetime import datetime, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify)
from flask_login import login_required, current_user

from ..decorators import role_required
from ..models import (users, courses, classes, exams, exam_submissions,
                      demonstrations, activity_logs, notifications, missions,
                      badge_defs, settings_col, create_teacher, push_notification,
                      oid)
from ..extensions import socketio

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")


@admin_bp.before_request
@login_required
def _guard():
    if current_user.doc.get("role") != "admin":
        from flask import abort
        abort(403)


@admin_bp.route("/")
def dashboard():
    stats = {
        "students": users().count_documents({"role": "student"}),
        "teachers": users().count_documents({"role": "teacher"}),
        "courses": courses().count_documents({}),
        "pending": users().count_documents({"role": "student", "status": "pending"}),
        "classes": classes().count_documents({}),
        "exams": exams().count_documents({}),
    }
    # active today = students with a study session today
    today = date.today().isoformat()
    stats["active_today"] = users().count_documents(
        {"role": "student", "last_login_date": {"$ne": None}})

    top = list(users().find({"role": "student"}, {"name": 1, "xp": 1, "level": 1})
               .sort("xp", -1).limit(5))
    recent_logs = list(activity_logs().find().sort("timestamp", -1).limit(15))
    # attach student names to logs
    for lg in recent_logs:
        s = users().find_one({"_id": lg.get("student_id")}, {"name": 1})
        lg["student_name"] = s.get("name") if s else "Unknown"
    return render_template("admin/dashboard.html", stats=stats, top=top,
                           recent_logs=recent_logs, courses=list(courses().find()))


# ---- Settings: default free/paid mode ----
@admin_bp.route("/settings", methods=["POST"])
def update_settings():
    mode = request.form.get("default_mode", "free")
    settings_col().update_one({"_id": "global"},
                              {"$set": {"default_mode": mode}}, upsert=True)
    flash(f"ডিফল্ট রেজিস্ট্রেশন মোড {mode} করা হয়েছে।", "success")
    return redirect(url_for("admin.dashboard"))


# ---- Student approvals ----
@admin_bp.route("/students")
def students():
    rows = list(users().find({"role": "student"}).sort("registered_at", -1))
    # attach course names
    cmap = {str(c["_id"]): c["name"] for c in courses().find()}
    for r in rows:
        r["course_names"] = [cmap.get(str(cid), "?") for cid in r.get("course_ids", [])]
    return render_template("admin/students.html", students=rows)


@admin_bp.route("/students/<sid>/<action>", methods=["POST"])
def student_action(sid, action):
    _id = oid(sid)
    if action == "approve":
        users().update_one({"_id": _id}, {"$set": {"status": "approved"}})
        push_notification(sid, "আপনার একাউন্ট অনুমোদিত হয়েছে! এখন লগইন করতে পারবেন।", "approval")
        flash("শিক্ষার্থী অনুমোদিত হয়েছে।", "success")
    elif action == "reject":
        users().update_one({"_id": _id}, {"$set": {"status": "rejected"}})
        flash("শিক্ষার্থী বাতিল করা হয়েছে।", "warning")
    elif action == "delete":
        users().delete_one({"_id": _id})
        flash("শিক্ষার্থী মুছে ফেলা হয়েছে।", "info")
    return redirect(url_for("admin.students"))


# ---- Courses ----
@admin_bp.route("/courses", methods=["GET", "POST"])
def manage_courses():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip()
        mode = request.form.get("mode", "free")
        if name:
            courses().insert_one({
                "name": name, "description": desc, "mode": mode,
                "teacher_ids": [], "created_at": datetime.utcnow(),
            })
            flash("কোর্স তৈরি হয়েছে।", "success")
        return redirect(url_for("admin.manage_courses"))

    rows = list(courses().find().sort("created_at", -1))
    tmap = {str(t["_id"]): t.get("name") for t in users().find({"role": "teacher"})}
    for r in rows:
        r["teacher_names"] = [tmap.get(str(t), "?") for t in r.get("teacher_ids", [])]
        r["student_count"] = users().count_documents(
            {"role": "student", "course_ids": r["_id"]})
    return render_template("admin/courses.html", courses=rows)


@admin_bp.route("/courses/<cid>/mode", methods=["POST"])
def toggle_course_mode(cid):
    course = courses().find_one({"_id": oid(cid)})
    if course:
        new_mode = "paid" if course.get("mode") == "free" else "free"
        courses().update_one({"_id": oid(cid)}, {"$set": {"mode": new_mode}})
        flash(f"কোর্সের মোড {new_mode} করা হয়েছে।", "success")
    return redirect(url_for("admin.manage_courses"))


@admin_bp.route("/courses/<cid>/delete", methods=["POST"])
def delete_course(cid):
    courses().delete_one({"_id": oid(cid)})
    flash("কোর্স মুছে ফেলা হয়েছে।", "info")
    return redirect(url_for("admin.manage_courses"))


# ---- Teachers ----
@admin_bp.route("/teachers", methods=["GET", "POST"])
def manage_teachers():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        course_ids = request.form.getlist("course_ids")
        if not (name and username and password):
            flash("নাম, ইউজারনেম ও পাসওয়ার্ড আবশ্যক।", "error")
        elif users().find_one({"username": username}):
            flash("এই ইউজারনেম আগেই ব্যবহৃত হয়েছে।", "error")
        else:
            tid = create_teacher(name, username, password, course_ids)
            # assign teacher to chosen courses
            for c in course_ids:
                courses().update_one({"_id": oid(c)},
                                     {"$addToSet": {"teacher_ids": oid(tid)}})
            flash("শিক্ষক যোগ করা হয়েছে।", "success")
        return redirect(url_for("admin.manage_teachers"))

    rows = list(users().find({"role": "teacher"}).sort("registered_at", -1))
    cmap = {str(c["_id"]): c["name"] for c in courses().find()}
    for r in rows:
        r["course_names"] = [cmap.get(str(cid), "?") for cid in r.get("course_ids", [])]
    course_list = list(courses().find())
    return render_template("admin/teachers.html", teachers=rows, courses=course_list)


@admin_bp.route("/teachers/<tid>/delete", methods=["POST"])
def delete_teacher(tid):
    users().delete_one({"_id": oid(tid), "role": "teacher"})
    flash("শিক্ষক মুছে ফেলা হয়েছে।", "info")
    return redirect(url_for("admin.manage_teachers"))


# ---- Custom notifications from admin ----
@admin_bp.route("/notify", methods=["POST"])
def send_notification():
    message = request.form.get("message", "").strip()
    target = request.form.get("target", "all")  # 'all' or a course id
    if not message:
        flash("বার্তা লিখুন।", "error")
        return redirect(url_for("admin.dashboard"))
    query = {"role": "student"}
    if target != "all" and oid(target):
        query["course_ids"] = oid(target)
    count = 0
    for s in users().find(query, {"_id": 1}):
        push_notification(str(s["_id"]), message, "admin")
        count += 1
    flash(f"{count} জন শিক্ষার্থীকে নোটিফিকেশন পাঠানো হয়েছে।", "success")
    return redirect(url_for("admin.dashboard"))


# ---- Activity logs (permissions, tab switches, exam events) ----
@admin_bp.route("/logs")
def logs():
    rows = list(activity_logs().find().sort("timestamp", -1).limit(200))
    for lg in rows:
        s = users().find_one({"_id": lg.get("student_id")}, {"name": 1})
        lg["student_name"] = s.get("name") if s else "Unknown"
    return render_template("admin/logs.html", logs=rows)


@admin_bp.route("/exams")
def exam_overview():
    rows = list(exams().find().sort("created_at", -1))
    for ex in rows:
        ex["submission_count"] = exam_submissions().count_documents({"exam_id": ex["_id"]})
        c = courses().find_one({"_id": ex.get("course_id")}, {"name": 1})
        ex["course_name"] = c.get("name") if c else "?"
    return render_template("admin/exams.html", exams=rows)


@admin_bp.route("/exams/<eid>/submissions")
def exam_submissions_view(eid):
    ex = exams().find_one({"_id": oid(eid)})
    subs = list(exam_submissions().find({"exam_id": oid(eid)}).sort("score_pct", -1))
    for s in subs:
        u = users().find_one({"_id": s.get("student_id")}, {"name": 1})
        s["student_name"] = u.get("name") if u else "Unknown"
    return render_template("admin/exam_submissions.html", exam=ex, subs=subs)
