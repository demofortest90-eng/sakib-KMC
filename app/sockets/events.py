"""
SocketIO event handlers.

Handles room joining, live-class tab monitoring, browser-permission logging,
and pushing realtime alerts to teachers and admins.

Event contract (must stay in sync with the templates):
  Student emits:  join_class{class_id,name}, leave_class{class_id},
                  tab_state{class_id,hidden,name}, permission_result{class_id,granted,name},
                  exam_event{exam_id,name,event}
  Teacher emits:  monitor_class{class_id}
  Server emits to class room:  student_joined{sid,name,granted},
                  student_left{sid,name}, student_tab_hidden{sid,name},
                  student_tab_visible{sid,name}, permission_status{sid,name,granted}
  Server emits to teachers:  exam_alert{student,event}
  Server emits to admins:    admin_notification{message}
"""
from datetime import datetime

from flask_login import current_user
from flask_socketio import join_room, leave_room, emit

from ..extensions import socketio
from ..models import log_activity


@socketio.on("connect")
def on_connect():
    if current_user.is_authenticated:
        role = current_user.doc.get("role")
        join_room(f"user_{current_user.id}")
        if role == "teacher":
            join_room("teachers")
        if role == "admin":
            join_room("admins")
            join_room("teachers")  # admins also see teacher alerts


@socketio.on("join_class")
def on_join_class(data):
    """A student opens a live class room; teacher sees them as active."""
    if not current_user.is_authenticated:
        return
    room = f"class_{data.get('class_id')}"
    join_room(room)
    emit("student_joined", {
        "sid": current_user.id,
        "name": current_user.doc.get("name", "Student"),
        "granted": None,
    }, room=room)


@socketio.on("leave_class")
def on_leave_class(data):
    if not current_user.is_authenticated:
        return
    room = f"class_{data.get('class_id')}"
    emit("student_left", {
        "sid": current_user.id,
        "name": current_user.doc.get("name", "Student"),
    }, room=room)
    leave_room(room)


@socketio.on("monitor_class")
def on_monitor_class(data):
    """Teacher/admin joins a class room to watch live activity."""
    if current_user.is_authenticated and current_user.doc.get("role") in ("teacher", "admin"):
        join_room(f"class_{data.get('class_id')}")


@socketio.on("tab_state")
def on_tab_state(data):
    """Student reports a visibility change. data = {class_id, hidden, name}"""
    if not current_user.is_authenticated:
        return
    class_id = data.get("class_id")
    hidden = bool(data.get("hidden"))
    kind = "tab_hidden" if hidden else "tab_visible"
    log_activity(current_user.id, None, f"class_{kind}", str(class_id))

    payload = {
        "sid": current_user.id,
        "name": current_user.doc.get("name", "Student"),
        "at": datetime.utcnow().strftime("%H:%M:%S"),
    }
    emit("student_tab_hidden" if hidden else "student_tab_visible",
         payload, room=f"class_{class_id}")

    if hidden:
        emit("admin_notification",
             {"message": f"{payload['name']} switched away during class."},
             room="admins")


@socketio.on("permission_result")
def on_permission_result(data):
    """Student grants or denies browser permissions. data = {class_id, granted, name}"""
    if not current_user.is_authenticated:
        return
    granted = bool(data.get("granted"))
    kind = "permission_allow" if granted else "permission_deny"
    log_activity(current_user.id, None, kind, str(data.get("class_id", "")))

    emit("admin_notification", {
        "message": f"{current_user.doc.get('name')} "
                   f"{'allowed' if granted else 'denied'} class permissions.",
    }, room="admins")
    emit("permission_status", {
        "sid": current_user.id,
        "name": current_user.doc.get("name", "Student"),
        "granted": granted,
    }, room=f"class_{data.get('class_id')}")


@socketio.on("exam_event")
def on_exam_event(data):
    """Suspicious activity during an exam (tab switch, blur, lens, etc.)."""
    if not current_user.is_authenticated:
        return
    log_activity(current_user.id, None,
                 f"exam_{data.get('event', 'event')}", str(data.get("exam_id")))
    emit("admin_notification", {
        "message": f"{current_user.doc.get('name')} — {data.get('event')} during exam.",
    }, room="admins")
    emit("exam_alert", {
        "student": current_user.doc.get("name"),
        "event": data.get("event"),
    }, room="teachers")
