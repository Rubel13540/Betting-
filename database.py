"""
MongoDB database handler for Telegram bot
"""
import os
from pymongo import MongoClient
from datetime import datetime

MONGO_URI = os.getenv("MONGODB_URI")

if not MONGO_URI:
    raise Exception("MONGODB_URI environment variable missing!")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client["kgf_cyber_bot"]

accounts_col = db["accounts"]
users_col = db["users"]
logs_col = db["logs"]


def save_account(chat_id: int, uid: str, name: str, cookie: str, token: str):
    """Save or update a Facebook account for a Telegram user."""
    accounts_col.update_one(
        {"chat_id": chat_id, "fb_uid": uid},
        {
            "$set": {
                "chat_id": chat_id,
                "fb_uid": uid,
                "name": name,
                "cookie": cookie,
                "token": token,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )


def get_accounts(chat_id: int) -> list:
    """Get all saved Facebook accounts for a Telegram user."""
    return list(accounts_col.find({"chat_id": chat_id}))


def get_account(chat_id: int, fb_uid: str):
    """Get a specific Facebook account."""
    return accounts_col.find_one({"chat_id": chat_id, "fb_uid": fb_uid})


def delete_account(chat_id: int, fb_uid: str):
    """Delete a Facebook account."""
    accounts_col.delete_one({"chat_id": chat_id, "fb_uid": fb_uid})


def register_user(chat_id: int, username: str = "", first_name: str = ""):
    """Register a Telegram user."""
    users_col.update_one(
        {"chat_id": chat_id},
        {
            "$set": {
                "chat_id": chat_id,
                "username": username,
                "first_name": first_name,
                "last_seen": datetime.utcnow(),
            },
            "$setOnInsert": {"joined_at": datetime.utcnow()},
        },
        upsert=True,
    )


def log_action(chat_id: int, action: str, details: str = ""):
    """Log an action for debugging / analytics."""
    logs_col.insert_one(
        {
            "chat_id": chat_id,
            "action": action,
            "details": details,
            "timestamp": datetime.utcnow(),
        }
    )
