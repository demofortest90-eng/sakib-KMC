"""
Student blueprint.

Dashboard, custom video player + live notes, exams (MCQ/CQ), demonstrations,
self-study timer, leaderboard, profile and theme toggle.
"""
from datetime import datetime, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, session, abort)
from flask_login import login_required, current_user

from ..decorators import role_required, upload_to_imgbb
from ..models import (users, courses, classes, notes, exams, exam_submissions,
                      demonstrations, study_sessions, missions, mission_progress,
                      badge_defs, notifications, activity_logs, log_activity,
                      push_notification, oid)
from ..gamification import (award_xp, level_progress, leaderboard, student_rank,
                            check_badges, total_study_seconds)
from ..extensions import socketio

student_bp = Blueprint("student", __name__, template_folder="../templates/student")


@student_bp.before_request
@login_required
def _guard():
    if current_user.doc.get("role") != "student":
        abort(403)


def _my_course_ids():
    return current_user.doc.get("course_ids", [])


def _active_course():
    """The course the student is currently working in (chosen at login)."""
    cid = session.get("active_course")
    if cid:
        return courses().find_one({"_id": oid(cid)})
    ids = _my_course_ids()
    if ids:
        return courses().find_one({"_id": ids[0]})
    return None


# ---------------- Dashboard + course switching ----------------
@student_bp.route("/")
def dashboard():
    my_courses = list(courses().find({"_id": {"$in": _my_course_ids()}}))

    # if more than one course and none chosen yet -> ask which one
    if len(my_courses) > 1 and not session.get("active_course"):
        return render_template("student/choose_course.html", courses=my_courses)

    if my_courses and not session.get("active_course"):
        session["active_course"] = str(my_courses[0]["_id"])

    active = _active_course()
    prog = level_progress(current_user.doc.get("xp", 0))
    rank, total = student_rank(current_user.id,
                               active["_id"] if active else None)

    # active missions with progress
    active_missions = []
    if active:
        for m in missions().find({"course_id": active["_id"]}).limit(5):
            mp = mission_progress().find_one(
                {"student_id": oid(current_user.id), "mission_id": m["_id"]})
            active_missions.append({
                "name": m["name"], "description": m.get("description", ""),
                "target": m.get("target", 1),
                "progress": mp.get("progress", 0) if mp else 0,
                "completed": mp.get("completed", False) if mp else False,
                "reward_xp": m.get("reward_xp", 0),
            })

    lb = leaderboard(active["_id"] if active else None, limit=3)
    recent = list(activity_logs().find({"student_id": oid(current_user.id)})
                  .sort("timestamp", -1).limit(6))
    unread = notifications().count_documents(
        {"user_id": oid(current_user.id), "read": False})
    study_total = total_study_seconds(current_user.id)

    return render_template(
        "student/dashboard.html", courses=my_courses, active=active,
        prog=prog, rank=rank, total=total, missions=active_missions,
        leaderboard=lb, recent=recent, unread=unread,
        study_total=study_total, streak=current_user.doc.get("streak", 0),
        badge_count=len(current_user.doc.get("badges", [])))


@student_bp.route("/choose-course/<cid>")
def choose_course(cid):
    if oid(cid) in _my_course_ids():
        session["active_course"] = cid
    return redirect(url_for("student.dashboard"))


# ---------------- Classes / video player ----------------
@student_bp.route("/classes")
def class_list():
    active = _active_course()
    rows = []
    if active:
        rows = list(classes().find({"course_id": active["_id"]}).sort("created_at", -1))
    return render_template("student/classes.html", classes=rows, active=active)


@student_bp.route("/class/<cid>")
def watch_class(cid):
    cls = classes().find_one({"_id": oid(cid)})
    if not cls:
        abort(404)
    my_notes = list(notes().find(
        {"student_id": oid(current_user.id), "class_id": oid(cid)})
        .sort("timestamp_seconds", 1))
    return render_template("student/watch.html", cls=cls, notes=my_notes)


@student_bp.route("/class/<cid>/complete", methods=["POST"])
def complete_class(cid):
    # award once per class
    already = activity_logs().count_documents(
        {"student_id": oid(current_user.id), "kind": "class_complete", "detail": cid})
    if not already:
        log_activity(current_user.id, _active_course()["_id"] if _active_course() else None,
                     "class_complete", cid)
        award_xp(current_user.id, "class_complete")
    return jsonify({"ok": True})


# ---- Notes API (real-time, no reload) ----
@student_bp.route("/api/notes", methods=["POST"])
def save_note():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    cid = data.get("class_id")
    ts = int(data.get("timestamp_seconds", 0))
    if not text or not cid:
        return jsonify({"ok": False, "error": "missing data"}), 400
    res = notes().insert_one({
        "student_id": oid(current_user.id), "class_id": oid(cid),
        "timestamp_seconds": ts, "text": text, "created_at": datetime.utcnow(),
    })
    award_xp(current_user.id, "note")
    socketio.emit("note_saved", {"id": str(res.inserted_id)},
                  room=f"user_{current_user.id}")
    return jsonify({"ok": True, "id": str(res.inserted_id),
                    "timestamp_seconds": ts, "text": text})


@student_bp.route("/api/notes/<cid>")
def list_notes(cid):
    rows = list(notes().find(
        {"student_id": oid(current_user.id), "class_id": oid(cid)})
        .sort("timestamp_seconds", 1))
    return jsonify([{"id": str(n["_id"]), "t": n["timestamp_seconds"],
                     "text": n["text"]} for n in rows])


@student_bp.route("/api/notes/<nid>/delete", methods=["POST"])
def delete_note(nid):
    notes().delete_one({"_id": oid(nid), "student_id": oid(current_user.id)})
    return jsonify({"ok": True})


# ---------------- Exams ----------------
@student_bp.route("/exams")
def exam_list():
    active = _active_course()
    rows = []
    if active:
        rows = list(exams().find({"course_id": active["_id"]}).sort("created_at", -1))
        for ex in rows:
            ex["taken"] = exam_submissions().count_documents(
                {"exam_id": ex["_id"], "student_id": oid(current_user.id)}) > 0
            ex["status"] = _exam_window(ex)
    return render_template("student/exams.html", exams=rows, active=active)


def _exam_window(ex):
    """Return 'upcoming' | 'live' | 'ended'. Missing times => always open."""
    now = datetime.now()
    start = end = None
    try:
        if ex.get("start_time"):
            start = datetime.fromisoformat(ex["start_time"])
    except Exception:
        start = None
    try:
        if ex.get("end_time"):
            end = datetime.fromisoformat(ex["end_time"])
    except Exception:
        end = None
    if start and now < start:
        return "upcoming"
    if end and now > end:
        return "ended"
    return "live"


@student_bp.route("/exam/<eid>")
def take_exam(eid):
    ex = exams().find_one({"_id": oid(eid)})
    if not ex:
        abort(404)
    if exam_submissions().count_documents(
            {"exam_id": oid(eid), "student_id": oid(current_user.id)}):
        flash("আপনি ইতিমধ্যে এই পরীক্ষাটি জমা দিয়েছেন।", "info")
        return redirect(url_for("student.exam_list"))
    window = _exam_window(ex)
    if window != "live":
        wmap = {"upcoming": "এখনো শুরু হয়নি", "ended": "শেষ হয়ে গেছে"}
        flash(f"এই পরীক্ষাটি {wmap.get(window, window)}।", "warning")
        return redirect(url_for("student.exam_list"))
    return render_template("student/take_exam.html", exam=ex)


@student_bp.route("/exam/<eid>/submit", methods=["POST"])
def submit_exam(eid):
    ex = exams().find_one({"_id": oid(eid)})
    if not ex:
        abort(404)
    if exam_submissions().count_documents(
            {"exam_id": oid(eid), "student_id": oid(current_user.id)}):
        return redirect(url_for("student.exam_list"))

    sub = {
        "exam_id": oid(eid), "student_id": oid(current_user.id),
        "submitted_at": datetime.utcnow(), "type": ex["type"],
    }

    if ex["type"] == "mcq":
        total = sum(q.get("marks", 1) for q in ex["questions"])
        scored = 0
        answers = []
        for i, q in enumerate(ex["questions"]):
            chosen = request.form.get(f"q_{i}")
            chosen = int(chosen) if chosen is not None and chosen != "" else -1
            correct = chosen == q["correct"]
            if correct:
                scored += q.get("marks", 1)
            answers.append({"chosen": chosen, "correct": correct})
        pct = round((scored / total) * 100) if total else 0
        sub.update({"answers": answers, "score": scored, "total": total,
                    "score_pct": pct})

        # early submission tracking for speed badge
        try:
            end = datetime.fromisoformat(ex["end_time"]) if ex.get("end_time") else None
            if end:
                sub["early_minutes"] = max(0, int((end - datetime.now()).total_seconds() // 60))
        except Exception:
            pass

        exam_submissions().insert_one(sub)
        if pct >= 50:
            award_xp(current_user.id, "exam_pass")
        check_badges(current_user.id)
        flash(f"জমা হয়েছে! স্কোর: {scored}/{total} ({pct}%)", "success")

    else:  # cq -> upload images to ImgBB
        urls = []
        for f in request.files.getlist("answer_images"):
            if f and f.filename:
                url = upload_to_imgbb(f)
                if url:
                    urls.append(url)
        sub.update({"image_urls": urls, "score": None, "total": None,
                    "score_pct": None})
        exam_submissions().insert_one(sub)
        award_xp(current_user.id, "cq_submit")
        flash(f"জমা হয়েছে {len(urls)} টি উত্তরের ছবি।", "success")

    socketio.emit("exam_submit",
                  {"student": current_user.doc.get("name"), "exam": ex["title"]},
                  room="teachers")
    return redirect(url_for("student.exam_result", eid=eid))


@student_bp.route("/exam/<eid>/result")
def exam_result(eid):
    ex = exams().find_one({"_id": oid(eid)})
    sub = exam_submissions().find_one(
        {"exam_id": oid(eid), "student_id": oid(current_user.id)})
    # leaderboard for this exam (mcq only)
    board = []
    if ex and ex["type"] == "mcq":
        cur = exam_submissions().find({"exam_id": oid(eid)}).sort("score_pct", -1).limit(20)
        for i, s in enumerate(cur, 1):
            u = users().find_one({"_id": s["student_id"]}, {"name": 1})
            board.append({"rank": i, "name": u.get("name") if u else "?",
                          "score": s.get("score"), "pct": s.get("score_pct"),
                          "me": s["student_id"] == oid(current_user.id)})
    return render_template("student/exam_result.html", exam=ex, sub=sub, board=board)


# ---------------- Demonstrations ----------------
@student_bp.route("/demos")
def demo_list():
    active = _active_course()
    rows = []
    if active:
        rows = list(demonstrations().find({"course_id": active["_id"]}).sort("created_at", -1))
    return render_template("student/demos.html", demos=rows, active=active)


@student_bp.route("/demo/<did>")
def view_demo(did):
    demo = demonstrations().find_one({"_id": oid(did)})
    if not demo:
        abort(404)
    return render_template("student/demo_view.html", demo=demo)


@student_bp.route("/demo/<did>/raw")
def demo_raw(did):
    """Serve the demo HTML inside a sandboxed iframe."""
    demo = demonstrations().find_one({"_id": oid(did)})
    if not demo:
        abort(404)
    return demo.get("html_content", "")


# ---------------- Self study ----------------
@student_bp.route("/api/study-ping", methods=["POST"])
def study_ping():
    """Called every ~30s while the self-study tab is active."""
    data = request.get_json(force=True)
    seconds = int(data.get("seconds", 0))
    today = date.today().isoformat()
    study_sessions().update_one(
        {"student_id": oid(current_user.id), "date": today},
        {"$inc": {"duration_seconds": seconds},
         "$set": {"last_ping": datetime.utcnow()}},
        upsert=True,
    )
    total = total_study_seconds(current_user.id)
    # award study XP per full hour milestone
    if total and total // 3600 > (total - seconds) // 3600:
        award_xp(current_user.id, "study_hour")
    check_badges(current_user.id)
    return jsonify({"ok": True, "total": total})


# ---------------- Leaderboard ----------------
@student_bp.route("/leaderboard")
def view_leaderboard():
    active = _active_course()
    scope = request.args.get("scope", "course")
    cid = active["_id"] if (active and scope == "course") else None
    rows = leaderboard(cid, limit=100)
    rank, total = student_rank(current_user.id, cid)
    return render_template("student/leaderboard.html", rows=rows, scope=scope,
                           active=active, my_rank=rank, total=total)


# ---------------- Profile + theme ----------------
@student_bp.route("/profile")
def profile():
    badges_all = list(badge_defs().find())
    owned = set(current_user.doc.get("badges", []))
    for b in badges_all:
        b["owned"] = b["key"] in owned
    prog = level_progress(current_user.doc.get("xp", 0))
    rank, total = student_rank(current_user.id)
    stats = {
        "classes": activity_logs().count_documents(
            {"student_id": oid(current_user.id), "kind": "class_complete"}),
        "notes": notes().count_documents({"student_id": oid(current_user.id)}),
        "exams": exam_submissions().count_documents({"student_id": oid(current_user.id)}),
        "study": total_study_seconds(current_user.id),
    }
    return render_template("student/profile.html", badges=badges_all, prog=prog,
                           rank=rank, total=total, stats=stats)


@student_bp.route("/api/theme", methods=["POST"])
def set_theme():
    theme = request.get_json(force=True).get("theme", "light")
    users().update_one({"_id": oid(current_user.id)}, {"$set": {"theme": theme}})
    return jsonify({"ok": True, "theme": theme})


# ---------------- Notifications ----------------
@student_bp.route("/notifications")
def view_notifications():
    rows = list(notifications().find({"user_id": oid(current_user.id)})
                .sort("created_at", -1).limit(50))
    notifications().update_many(
        {"user_id": oid(current_user.id), "read": False}, {"$set": {"read": True}})
    return render_template("student/notifications.html", notes=rows)


@student_bp.route("/api/notifications/count")
def notif_count():
    n = notifications().count_documents(
        {"user_id": oid(current_user.id), "read": False})
    return jsonify({"count": n})
