"""
Teacher blueprint.

Teachers create classes, exams (MCQ/CQ), demonstrations and missions,
and monitor live classes and exam activity.
"""
import re
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify)
from flask_login import login_required, current_user

from ..decorators import role_required, upload_to_imgbb
from ..models import (users, courses, classes, exams, exam_submissions,
                      demonstrations, missions, activity_logs, oid)

teacher_bp = Blueprint("teacher", __name__, template_folder="../templates/teacher")


@teacher_bp.before_request
@login_required
def _guard():
    if current_user.doc.get("role") != "teacher":
        from flask import abort
        abort(403)


def _my_courses():
    ids = current_user.doc.get("course_ids", [])
    return list(courses().find({"_id": {"$in": ids}}))


def youtube_id(url):
    """Extract a YouTube video id from common URL formats."""
    if not url:
        return ""
    patterns = [
        r"youtu\.be/([\w\-]{11})",
        r"v=([\w\-]{11})",
        r"embed/([\w\-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    # maybe they pasted a bare id
    if re.fullmatch(r"[\w\-]{11}", url.strip()):
        return url.strip()
    return ""


@teacher_bp.route("/")
def dashboard():
    my = _my_courses()
    cids = [c["_id"] for c in my]
    stats = {
        "courses": len(my),
        "classes": classes().count_documents({"course_id": {"$in": cids}}),
        "exams": exams().count_documents({"course_id": {"$in": cids}}),
        "demos": demonstrations().count_documents({"course_id": {"$in": cids}}),
    }
    return render_template("teacher/dashboard.html", stats=stats, courses=my)


# ---------------- Classes ----------------
@teacher_bp.route("/classes", methods=["GET", "POST"])
def manage_classes():
    my = _my_courses()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        link = request.form.get("youtube_link", "").strip()
        desc = request.form.get("description", "").strip()
        course_id = request.form.get("course_id")
        vid = youtube_id(link)
        if not (name and vid and course_id):
            flash("নাম, সঠিক ইউটিউব লিংক ও কোর্স আবশ্যক।", "error")
        else:
            classes().insert_one({
                "course_id": oid(course_id),
                "teacher_id": oid(current_user.id),
                "name": name, "youtube_id": vid, "description": desc,
                "is_live": bool(request.form.get("is_live")),
                "created_at": datetime.utcnow(),
            })
            flash("ক্লাস যোগ করা হয়েছে।", "success")
        return redirect(url_for("teacher.manage_classes"))

    cids = [c["_id"] for c in my]
    rows = list(classes().find({"course_id": {"$in": cids}}).sort("created_at", -1))
    cmap = {str(c["_id"]): c["name"] for c in my}
    for r in rows:
        r["course_name"] = cmap.get(str(r["course_id"]), "?")
    return render_template("teacher/classes.html", classes=rows, courses=my)


@teacher_bp.route("/classes/<cid>/live")
def live_monitor(cid):
    cls = classes().find_one({"_id": oid(cid)})
    return render_template("teacher/live_monitor.html", cls=cls)


# ---------------- Exams ----------------
@teacher_bp.route("/exams", methods=["GET", "POST"])
def manage_exams():
    my = _my_courses()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        etype = request.form.get("type", "mcq")
        course_id = request.form.get("course_id")
        start = request.form.get("start_time")
        end = request.form.get("end_time")

        doc = {
            "title": title, "type": etype, "course_id": oid(course_id),
            "teacher_id": oid(current_user.id),
            "start_time": start, "end_time": end,
            "created_at": datetime.utcnow(), "questions": [],
        }

        if etype == "mcq":
            qtexts = request.form.getlist("q_text")
            for i, qt in enumerate(qtexts):
                opts = [
                    request.form.getlist("q_opt_a")[i],
                    request.form.getlist("q_opt_b")[i],
                    request.form.getlist("q_opt_c")[i],
                    request.form.getlist("q_opt_d")[i],
                ]
                correct = int(request.form.getlist("q_correct")[i])
                marks = int(request.form.getlist("q_marks")[i] or 1)
                doc["questions"].append({
                    "text": qt, "options": opts, "correct": correct, "marks": marks,
                })
        else:  # cq
            qtexts = request.form.getlist("cq_text")
            for qt in qtexts:
                if qt.strip():
                    doc["questions"].append({"text": qt, "marks": 10})

        if title and course_id and doc["questions"]:
            exams().insert_one(doc)
            flash("পরীক্ষা তৈরি হয়েছে।", "success")
        else:
            flash("শিরোনাম, কোর্স ও অন্তত একটি প্রশ্ন পূরণ করুন।", "error")
        return redirect(url_for("teacher.manage_exams"))

    cids = [c["_id"] for c in my]
    rows = list(exams().find({"course_id": {"$in": cids}}).sort("created_at", -1))
    cmap = {str(c["_id"]): c["name"] for c in my}
    for r in rows:
        r["course_name"] = cmap.get(str(r["course_id"]), "?")
        r["submission_count"] = exam_submissions().count_documents({"exam_id": r["_id"]})
    return render_template("teacher/exams.html", exams=rows, courses=my)


@teacher_bp.route("/exams/<eid>/results")
def exam_results(eid):
    ex = exams().find_one({"_id": oid(eid)})
    subs = list(exam_submissions().find({"exam_id": oid(eid)}).sort("score_pct", -1))
    for s in subs:
        u = users().find_one({"_id": s.get("student_id")}, {"name": 1})
        s["student_name"] = u.get("name") if u else "Unknown"
        # collect activity events for this student+exam
        s["events"] = list(activity_logs().find(
            {"student_id": s.get("student_id"), "detail": str(eid)}))
    return render_template("teacher/exam_results.html", exam=ex, subs=subs)


# ---------------- Demonstrations ----------------
@teacher_bp.route("/demos", methods=["GET", "POST"])
def manage_demos():
    my = _my_courses()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        course_id = request.form.get("course_id")
        thumb = request.files.get("thumbnail")
        html_file = request.files.get("html_file")

        thumb_url = upload_to_imgbb(thumb) if thumb and thumb.filename else ""
        html_content = ""
        if html_file and html_file.filename:
            try:
                html_content = html_file.read().decode("utf-8", errors="ignore")
            except Exception:
                html_content = ""

        if name and course_id and html_content:
            demonstrations().insert_one({
                "name": name, "course_id": oid(course_id),
                "teacher_id": oid(current_user.id),
                "thumbnail_url": thumb_url, "html_content": html_content,
                "created_at": datetime.utcnow(),
            })
            flash("ডেমনস্ট্রেশন যোগ করা হয়েছে।", "success")
        else:
            flash("নাম, কোর্স ও একটি HTML ফাইল আবশ্যক।", "error")
        return redirect(url_for("teacher.manage_demos"))

    cids = [c["_id"] for c in my]
    rows = list(demonstrations().find({"course_id": {"$in": cids}}).sort("created_at", -1))
    return render_template("teacher/demos.html", demos=rows, courses=my)


# ---------------- Missions ----------------
@teacher_bp.route("/missions", methods=["GET", "POST"])
def manage_missions():
    my = _my_courses()
    if request.method == "POST":
        missions().insert_one({
            "name": request.form.get("name", "").strip(),
            "description": request.form.get("description", "").strip(),
            "type": request.form.get("type", "class_watch"),
            "target": int(request.form.get("target", 1) or 1),
            "reward_xp": int(request.form.get("reward_xp", 100) or 100),
            "course_id": oid(request.form.get("course_id")),
            "teacher_id": oid(current_user.id),
            "created_at": datetime.utcnow(),
        })
        flash("মিশন তৈরি হয়েছে।", "success")
        return redirect(url_for("teacher.manage_missions"))

    cids = [c["_id"] for c in my]
    rows = list(missions().find({"course_id": {"$in": cids}}).sort("created_at", -1))
    return render_template("teacher/missions.html", missions=rows, courses=my)
