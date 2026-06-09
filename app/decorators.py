"""Access-control decorators and small shared utilities."""
from functools import wraps

import requests
from flask import abort, current_app
from flask_login import current_user


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.doc.get("role") not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def upload_to_imgbb(file_storage):
    """Upload a Werkzeug FileStorage to ImgBB, return the hosted URL or None."""
    api_key = current_app.config.get("IMGBB_API_KEY")
    if not api_key or not file_storage:
        return None
    try:
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            params={"key": api_key},
            files={"image": (file_storage.filename, file_storage.stream,
                             file_storage.mimetype)},
            timeout=30,
        )
        data = resp.json()
        if data.get("success"):
            return data["data"]["url"]
    except Exception as exc:  # pragma: no cover
        print(f"[imgbb] upload failed: {exc}")
    return None
