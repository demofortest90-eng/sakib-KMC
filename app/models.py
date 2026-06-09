"""
Data layer for EduPlatform.

Thin helpers over PyMongo collections plus a Flask-Login User wrapper.
All documents use Mongo's native ObjectId; helpers return plain dicts.
"""
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import get_db


# --------------------------------------------------------------------------
# Collection accessors
# --------------------------------------------------------------------------
def users():            return get_db()["users"]
def courses():          return get_db()["courses"]
def classes():          return get_db()["classes"]
def notes():            return get_db()["notes"]
def exams():            return get_db()["exams"]
def exam_submissions(): return get_db()["exam_submissions"]
def demonstrations():   return get_db()["demonstrations"]
def study_sessions():   return get_db()["study_sessions"]
def missions():         return get_db()["missions"]
def mission_progress(): return get_db()["mission_progress"]
def badge_defs():       return get_db()["badge_defs"]
def activity_logs():    return get_db()["activity_logs"]
def notifications():    return get_db()["notifications"]
def settings_col():     return get_db()["settings"]


def oid(value):
    """Safely convert a string to ObjectId, returns None on failure."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


# --------------------------------------------------------------------------
# User model (Flask-Login)
# --------------------------------------------------------------------------
class User(UserMixin):
    def __init__(self, doc):
        self.doc = doc
        self.id = str(doc["_id"])

    # convenience proxies -------------------------------------------------
    def __getattr__(self, item):
        # Allows user.name, user.role, etc. for keys stored in the doc
        try:
            return self.doc[item]
        except KeyError:
            raise AttributeError(item)

    @property
    def is_admin(self):    return self.doc.get("role") == "admin"
    @property
    def is_teacher(self):  return self.doc.get("role") == "teacher"
    @property
    def is_student(self):  return self.doc.get("role") == "student"

    def get_id(self):
        return self.id

    # lookups -------------------------------------------------------------
    @staticmethod
    def get(user_id):
        _id = oid(user_id)
        if not _id:
            return None
        doc = users().find_one({"_id": _id})
        return User(doc) if doc else None

    @staticmethod
    def by_email(email):
        doc = users().find_one({"email": email.strip().lower()})
        return User(doc) if doc else None

    @staticmethod
    def by_username(username):
        doc = users().find_one({"username": username})
        return User(doc) if doc else None

    # auth ----------------------------------------------------------------
    def check_password(self, password):
        return check_password_hash(self.doc.get("password_hash", ""), password)


# --------------------------------------------------------------------------
# User creation helpers
# --------------------------------------------------------------------------
def create_student(name, email, password, course_ids, status):
    doc = {
        "name": name.strip(),
        "email": email.strip().lower(),
        "password_hash": generate_password_hash(password),
        "role": "student",
        "status": status,                 # approved | pending | rejected
        "course_ids": [oid(c) for c in course_ids if oid(c)],
        "registered_at": datetime.utcnow(),
        "xp": 0,
        "level": 1,
        "streak": 0,
        "last_login_date": None,
        "theme": "light",
        "badges": [],                     # list of badge keys
        "avatar_url": "",
    }
    res = users().insert_one(doc)
    return str(res.inserted_id)


def create_teacher(name, username, password, course_ids=None):
    doc = {
        "name": name.strip(),
        "username": username.strip(),
        "email": "",
        "password_hash": generate_password_hash(password),
        "role": "teacher",
        "status": "approved",
        "course_ids": [oid(c) for c in (course_ids or []) if oid(c)],
        "registered_at": datetime.utcnow(),
        "theme": "light",
    }
    res = users().insert_one(doc)
    return str(res.inserted_id)


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
def push_notification(user_id, message, kind="info"):
    notifications().insert_one({
        "user_id": oid(user_id),
        "message": message,
        "kind": kind,
        "read": False,
        "created_at": datetime.utcnow(),
    })
    try:
        from .extensions import socketio
        socketio.emit("new_notification", {"message": message, "kind": kind},
                      room=f"user_{user_id}")
    except Exception:
        pass


def log_activity(student_id, course_id, kind, detail=""):
    activity_logs().insert_one({
        "student_id": oid(student_id),
        "course_id": oid(course_id) if course_id else None,
        "kind": kind,
        "detail": detail,
        "timestamp": datetime.utcnow(),
    })


# --------------------------------------------------------------------------
# Indexes + default data
# --------------------------------------------------------------------------
def ensure_indexes():
    try:
        users().create_index("email", unique=True, sparse=True)
        users().create_index("username", sparse=True)
        users().create_index("role")
        classes().create_index("course_id")
        notes().create_index([("student_id", 1), ("class_id", 1)])
        exam_submissions().create_index([("exam_id", 1), ("student_id", 1)])
        study_sessions().create_index([("student_id", 1), ("date", 1)])
        activity_logs().create_index("timestamp")
        notifications().create_index([("user_id", 1), ("read", 1)])
    except Exception as exc:  # pragma: no cover
        print(f"[indexes] warning: {exc}")


DEFAULT_BADGES = [
    {"key": "class_champion", "name": "Class Champion", "icon": "trophy",
     "description": "Complete 10 classes", "type": "class_watch", "target": 10},
    {"key": "note_taker", "name": "Note Taker", "icon": "edit",
     "description": "Take 50 notes", "type": "note_count", "target": 50},
    {"key": "streak_7", "name": "7-Day Streak", "icon": "flame",
     "description": "Log in 7 days in a row", "type": "streak", "target": 7},
    {"key": "speed_solver", "name": "Speed Solver", "icon": "zap",
     "description": "Submit an exam 5 min early", "type": "speed", "target": 5},
    {"key": "perfect_score", "name": "Perfect Score", "icon": "target",
     "description": "Score 100% on an exam", "type": "perfect", "target": 100},
    {"key": "study_marathon", "name": "Study Marathon", "icon": "book",
     "description": "10 hours of self study", "type": "study_time", "target": 36000},
]


def seed_defaults():
    """Create the admin settings doc and default badge definitions once."""
    try:
        if settings_col().count_documents({"_id": "global"}) == 0:
            settings_col().insert_one({"_id": "global", "default_mode": "free"})
        for b in DEFAULT_BADGES:
            if badge_defs().count_documents({"key": b["key"]}) == 0:
                badge_defs().insert_one(b)
    except Exception as exc:  # pragma: no cover
        print(f"[seed] warning: {exc}")
