"""Shared extension instances, imported across the app to avoid circular imports."""
from flask_socketio import SocketIO
from flask_login import LoginManager
from pymongo import MongoClient

socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")
login_manager = LoginManager()

# Filled in by create_app()
mongo_client = None
db = None


def init_db(uri, db_name):
    global mongo_client, db
    mongo_client = MongoClient(uri, serverSelectionTimeoutMS=20000)
    db = mongo_client[db_name]
    return db


def get_db():
    return db
