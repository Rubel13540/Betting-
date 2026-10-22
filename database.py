import os
import sqlite3
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    else:
        # লোকাল টেস্টিংয়ের জন্য SQLite
        return sqlite3.connect("bot_data.db")

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            is_vip BOOLEAN DEFAULT FALSE,
            is_approved BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Predictions History Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            fixture_id INT,
            teams TEXT,
            prediction TEXT,
            status TEXT DEFAULT 'PENDING'
        )
    ''')
    
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, username)
    )
    conn.commit()
    conn.close()

def is_vip_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip, is_approved FROM users WHERE user_id = %s", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res and (res[0] or res[1]):
        return True
    return False

def set_vip_status(user_id, status=True):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_vip = %s, is_approved = %s WHERE user_id = %s", (status, status, user_id))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
      
