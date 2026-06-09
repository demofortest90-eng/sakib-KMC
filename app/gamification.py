"""
Gamification engine: XP awards, levels, streaks, badge unlocking, leaderboards.
"""
from datetime import datetime, date, timedelta

from .extensions import socketio
from .models import (
    users, notes, classes, study_sessions, badge_defs, oid,
    push_notification, exam_submissions,
)

# XP awarded per action (from the plan)
XP_TABLE = {
    "class_complete": 20,
    "exam_pass": 50,
    "cq_submit": 30,
    "daily_login": 10,
    "mission_complete": 100,
    "note": 5,
    "study_hour": 25,
}

# Level thresholds: (level, min_xp, title)
LEVELS = [
    (1, 0, "Beginner"),
    (2, 100, "Explorer"),
    (3, 300, "Scholar"),
    (4, 600, "Expert"),
    (5, 1000, "Master"),
]


def level_for_xp(xp):
    current = LEVELS[0]
    for lvl in LEVELS:
        if xp >= lvl[1]:
            current = lvl
    return current  # (level, min_xp, title)


def level_progress(xp):
    """Return dict with current level info and progress to next level."""
    lvl, lo, title = level_for_xp(xp)
    # find next threshold
    nxt = None
    for l in LEVELS:
        if l[0] == lvl + 1:
            nxt = l
            break
    if nxt:
        span = nxt[1] - lo
        pct = int(((xp - lo) / span) * 100) if span else 100
        return {"level": lvl, "title": title, "xp": xp,
                "next_xp": nxt[1], "pct": min(pct, 100), "max": False}
    return {"level": lvl, "title": title, "xp": xp,
            "next_xp": xp, "pct": 100, "max": True}


def award_xp(student_id, reason, amount=None):
    """Add XP for an action, recompute level, notify if level up."""
    amt = amount if amount is not None else XP_TABLE.get(reason, 0)
    if amt == 0:
        return
    _id = oid(student_id)
    user = users().find_one({"_id": _id})
    if not user:
        return
    old_level = user.get("level", 1)
    new_xp = user.get("xp", 0) + amt
    new_level = level_for_xp(new_xp)[0]
    users().update_one({"_id": _id},
                       {"$set": {"xp": new_xp, "level": new_level}})
    if new_level > old_level:
        title = level_for_xp(new_xp)[2]
        push_notification(student_id, f"Level up! You reached Level {new_level} — {title}", "level")
        socketio.emit("level_up", {"level": new_level, "title": title},
                      room=f"user_{student_id}")
    check_badges(student_id)


def register_daily_login(student_id):
    """Update streak on login. Returns the new streak count."""
    _id = oid(student_id)
    user = users().find_one({"_id": _id})
    if not user:
        return 0
    today = date.today()
    last = user.get("last_login_date")
    if isinstance(last, datetime):
        last = last.date()

    if last == today:
        return user.get("streak", 0)  # already counted today

    if last == today - timedelta(days=1):
        streak = user.get("streak", 0) + 1
    else:
        streak = 1

    users().update_one(
        {"_id": _id},
        {"$set": {"streak": streak,
                  "last_login_date": datetime.combine(today, datetime.min.time())}},
    )
    award_xp(student_id, "daily_login")
    return streak


def check_badges(student_id):
    """Evaluate all badge conditions and unlock any newly earned ones."""
    _id = oid(student_id)
    user = users().find_one({"_id": _id})
    if not user:
        return
    owned = set(user.get("badges", []))

    for b in badge_defs().find():
        if b["key"] in owned:
            continue
        earned = False
        btype = b.get("type")
        target = b.get("target", 0)

        if btype == "class_watch":
            done = activity_count(student_id, "class_complete")
            earned = done >= target
        elif btype == "note_count":
            earned = notes().count_documents({"student_id": _id}) >= target
        elif btype == "streak":
            earned = user.get("streak", 0) >= target
        elif btype == "study_time":
            total = total_study_seconds(student_id)
            earned = total >= target
        elif btype == "perfect":
            earned = exam_submissions().count_documents(
                {"student_id": _id, "score_pct": {"$gte": 100}}) > 0
        elif btype == "speed":
            earned = exam_submissions().count_documents(
                {"student_id": _id, "early_minutes": {"$gte": target}}) > 0

        if earned:
            users().update_one({"_id": _id}, {"$addToSet": {"badges": b["key"]}})
            push_notification(student_id, f"Badge unlocked: {b['name']}!", "badge")
            socketio.emit("badge_unlocked",
                          {"name": b["name"], "icon": b["icon"]},
                          room=f"user_{student_id}")


def activity_count(student_id, kind):
    from .models import activity_logs
    return activity_logs().count_documents(
        {"student_id": oid(student_id), "kind": kind})


def total_study_seconds(student_id):
    pipeline = [
        {"$match": {"student_id": oid(student_id)}},
        {"$group": {"_id": None, "total": {"$sum": "$duration_seconds"}}},
    ]
    res = list(study_sessions().aggregate(pipeline))
    return res[0]["total"] if res else 0


# --------------------------------------------------------------------------
# Leaderboards
# --------------------------------------------------------------------------
def leaderboard(course_id=None, limit=50):
    query = {"role": "student"}
    if course_id:
        query["course_ids"] = oid(course_id)
    cur = users().find(query, {"name": 1, "xp": 1, "level": 1,
                               "streak": 1, "badges": 1, "avatar_url": 1}) \
        .sort("xp", -1).limit(limit)
    rows = []
    for i, u in enumerate(cur, start=1):
        rows.append({
            "rank": i,
            "id": str(u["_id"]),
            "name": u.get("name", "Student"),
            "xp": u.get("xp", 0),
            "level": u.get("level", 1),
            "streak": u.get("streak", 0),
            "badges": u.get("badges", []),
            "avatar_url": u.get("avatar_url", ""),
        })
    return rows


def student_rank(student_id, course_id=None):
    rows = leaderboard(course_id, limit=10000)
    for r in rows:
        if r["id"] == str(student_id):
            return r["rank"], len(rows)
    return None, len(rows)
