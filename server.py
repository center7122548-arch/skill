#!/usr/bin/env python3
"""ChronoSwap: small, dependency-free web app with a SQLite backend."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from contextlib import closing
from getpass import getpass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DB_PATH = Path(os.environ.get("CHRONOSWAP_DB", str(ROOT / "data" / "chronoswap.sqlite3")))
# บนโฮสต์ออนไลน์ (Render/Railway/Fly.io ฯลฯ) แพลตฟอร์มจะกำหนดตัวแปร PORT มาให้ -> ฟังที่ 0.0.0.0
# บนเครื่องตัวเอง (ไม่มี PORT) ยังใช้ 127.0.0.1:8000 เหมือนเดิม
HOST = os.environ.get("CHRONOSWAP_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("CHRONOSWAP_PORT") or os.environ.get("PORT") or "8000")
COOKIE_SECURE = os.environ.get("CHRONOSWAP_COOKIE_SECURE", "1" if os.environ.get("RENDER") else "0") == "1"
SEED_DEMO = os.environ.get("CHRONOSWAP_SEED_DEMO", "1") != "0"
SMTP_HOST = os.environ.get("CHRONOSWAP_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("CHRONOSWAP_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("CHRONOSWAP_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("CHRONOSWAP_SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("CHRONOSWAP_EMAIL_FROM", SMTP_USER)
BREVO_API_KEY = os.environ.get("CHRONOSWAP_BREVO_API_KEY", "")   # ส่งเมลผ่าน HTTPS (ใช้บนโฮสต์ที่บล็อกพอร์ต SMTP เช่น Render free)
PUBLIC_URL = (os.environ.get("CHRONOSWAP_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
              or f"http://{HOST}:{PORT}").rstrip("/")
BOOTSTRAP_ADMIN_EMAIL = os.environ.get("CHRONOSWAP_ADMIN_EMAIL", "").strip().lower()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("CHRONOSWAP_ADMIN_PASSWORD", "")
DEV_EMAIL_LINKS = os.environ.get("CHRONOSWAP_DEV_EMAIL_LINKS", "1" if HOST in ("127.0.0.1", "localhost") else "0") == "1"
CREDIT_CAP = 20 * 60

# ---- อัลกอริทึมจับคู่ทักษะ (4 ปัจจัย) ----
SLOT_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SLOT_PERIODS = ["m", "a", "e"]  # เช้า บ่าย เย็น
SLOT_PERIOD_WINDOWS = {"m": (6 * 3600, 12 * 3600), "a": (12 * 3600, 18 * 3600), "e": (18 * 3600, 24 * 3600)}
THAILAND_TIMEZONE = timezone(timedelta(hours=7))
VALID_SLOTS = {f"{d}-{p}" for d in SLOT_DAYS for p in SLOT_PERIODS}
SKILL_CATEGORIES = {"เทคโนโลยี", "ภาษา", "ดนตรี", "ออกแบบ", "กีฬา", "ศิลปะ", "วิชาการ", "อื่น ๆ"}
LEVELS = ["เริ่มต้น", "ฝึกฝน", "ขั้นสูง", "ทุกระดับ"]
MATCH_WEIGHTS = {"skill": 0.40, "time": 0.30, "level": 0.15, "rep": 0.15}
MIN_REQUEST_MINUTES = 30  # คำขอเรียนขั้นต่ำ 30 นาที ใช้เป็นเงื่อนไขกรอง "เครดิตพอ"
THAI_DAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
STARTING_CREDIT = 2 * 60
SESSION_SECONDS = 60 * 60 * 24 * 14
PASSWORD_ROUNDS = 310_000
ACTIVE_HOLD_STATES = ("pending", "accepted", "awaiting_confirmation", "disputed")
UNLIMITED_MINUTES = 10 ** 9   # ผู้ดูแลระบบ: เครดิตเวลาไม่จำกัด
UNLIMITED_COINS = 10 ** 9     # ผู้ดูแลระบบ: เหรียญร้านค้าไม่จำกัด
ADMIN_EMAIL = "admin@chronoswap.local"
ADMIN_PASSWORD = "CsAdmin#JQbcE2iB6bJvbN"
LEGACY_DEMO_EMAILS = (
    "demo@chronoswap.local",
    "mentor.design@chronoswap.local",
    "mentor.music@chronoswap.local",
)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ROUNDS)
    return f"pbkdf2_sha256${PASSWORD_ROUNDS}${salt.hex()}${value.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db_connect()) as db, db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            university TEXT NOT NULL DEFAULT 'มหาวิทยาลัยขอนแก่น',
            faculty TEXT NOT NULL DEFAULT '',
            bio TEXT NOT NULL DEFAULT '',
            interests TEXT NOT NULL DEFAULT '[]',
            avatar_data TEXT NOT NULL DEFAULT '',
            avatar_mime TEXT NOT NULL DEFAULT '',
            email_verified INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member','admin')),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            level TEXT NOT NULL,
            description TEXT NOT NULL,
            availability TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            deleted_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL REFERENCES offers(id),
            requester_id INTEGER NOT NULL REFERENCES users(id),
            provider_id INTEGER NOT NULL REFERENCES users(id),
            message TEXT NOT NULL DEFAULT '',
            scheduled_at TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requester_confirmed INTEGER NOT NULL DEFAULT 0,
            provider_confirmed INTEGER NOT NULL DEFAULT 0,
            dispute_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount_minutes INTEGER NOT NULL,
            kind TEXT NOT NULL,
            description TEXT NOT NULL,
            request_id INTEGER REFERENCES requests(id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            item_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS shop_equipped (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            frame TEXT NOT NULL DEFAULT '',
            sticker TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES requests(id),
            reviewer_id INTEGER NOT NULL REFERENCES users(id),
            reviewee_id INTEGER NOT NULL REFERENCES users(id),
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(request_id, reviewer_id)
        );
        CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            request_id INTEGER REFERENCES requests(id) ON DELETE SET NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            read_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_by_requester INTEGER NOT NULL DEFAULT 0,
            read_by_provider INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK (target_type IN ('user','offer')),
            target_id INTEGER NOT NULL,
            request_id INTEGER REFERENCES requests(id) ON DELETE SET NULL,
            reason TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewed','resolved','dismissed')),
            admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            resolution TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS email_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK (purpose IN ('verify_email','password_reset')),
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_offers_active ON offers(active, category);
        CREATE INDEX IF NOT EXISTS idx_requests_people ON requests(requester_id, provider_id, status);
        CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read_at, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_request ON messages(request_id, id);
        CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status, created_at DESC);
        """)
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "avatar_data" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN avatar_data TEXT NOT NULL DEFAULT ''")
        if "avatar_mime" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN avatar_mime TEXT NOT NULL DEFAULT ''")
        if "is_active" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "email_verified" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
        if "desired_level" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN desired_level TEXT NOT NULL DEFAULT ''")
        if "preferred_slots" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN preferred_slots TEXT NOT NULL DEFAULT '[]'")
        # Convert clearly paired legacy interests (category followed by its keyword).
        for old_user in db.execute("SELECT id,interests FROM users").fetchall():
            interests = safe_json_list(old_user["interests"])
            normalized = normalize_interests(interests)
            if interests != normalized:
                db.execute("UPDATE users SET interests=? WHERE id=?",
                           (json.dumps(normalized, ensure_ascii=False), old_user["id"]))
        offer_columns = {row["name"] for row in db.execute("PRAGMA table_info(offers)").fetchall()}
        if "deleted_at" not in offer_columns:
            db.execute("ALTER TABLE offers ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''")
        if "slots" not in offer_columns:
            db.execute("ALTER TABLE offers ADD COLUMN slots TEXT NOT NULL DEFAULT '[]'")
        # ประกาศเดิมที่ยังไม่มีช่วงเวลาแบบมีโครงสร้าง: แปลงจากข้อความช่วงเวลาที่สะดวก (ไม่ลบข้อมูลเดิม)
        for old in db.execute("SELECT id,availability FROM offers WHERE slots='[]'").fetchall():
            db.execute("UPDATE offers SET slots=? WHERE id=?", (json.dumps(infer_slots(old["availability"])), old["id"]))
    if SEED_DEMO:
        seed_demo_data()


DEMO_MEMBERS = [
    # (อีเมล, รหัสผ่าน, ชื่อ, คณะ, คำอธิบาย, ความสนใจ)
    ("somchai@chronoswap.local", "Swap@Somchai26", "สมชาย ใจดี", "วิทยาลัยการคอมพิวเตอร์",
     "ชอบแบ่งปันความรู้ด้านเทคโนโลยีและการทำงานดิจิทัล", ["ภาษา:ภาษาอังกฤษ", "ดนตรี:กีตาร์"]),
    ("napat@chronoswap.local", "Swap@Napat26", "ณภัทร รุ่งเรือง", "คณะวิศวกรรมศาสตร์",
     "สนุกกับการแก้โจทย์ ชอบอธิบายให้เข้าใจง่าย", ["ออกแบบ:Figma", "ภาษา:ภาษาอังกฤษ"]),
    ("mukda@chronoswap.local", "Swap@Mukda26", "มุกดา ศรีแสง", "คณะศิลปกรรมศาสตร์",
     "ช่วยเริ่มต้นใช้งานเครื่องมือออกแบบและจัดทำพอร์ตโฟลิโอ", ["เทคโนโลยี:Python", "เทคโนโลยี:การเขียนโปรแกรม"]),
    ("puri@chronoswap.local", "Swap@Puri26", "ภูริ วงศ์คำ", "คณะมนุษยศาสตร์และสังคมศาสตร์",
     "เรียนรู้ดนตรีได้ทีละขั้น เริ่มจากพื้นฐานที่คุณมี", ["เทคโนโลยี:Excel", "ภาษา:ภาษาอังกฤษ"]),
    ("ploy@chronoswap.local", "Swap@Ploy26", "พลอย แก้วมณี", "คณะบริหารธุรกิจ",
     "ชอบพูดคุยและช่วยกันฝึกภาษา พร้อมแชร์เทคนิคการนำเสนอ", ["เทคโนโลยี:Python", "ศิลปะ:ถ่ายภาพ"]),
    ("arm@chronoswap.local", "Swap@Arm26", "อาร์ม ธนากร", "คณะวิทยาศาสตร์",
     "ถนัดคณิตศาสตร์และสถิติ ช่วยปูพื้นฐานแบบไม่เร่งรีบ", ["ดนตรี:กีตาร์", "ออกแบบ:"]),
    ("fahsai@chronoswap.local", "Swap@Fahsai26", "ฟ้าใส บุญมา", "คณะเกษตรศาสตร์",
     "รักการถ่ายภาพและตัดต่อวิดีโอ อยากแลกเปลี่ยนกับสายเทคโนโลยี", ["เทคโนโลยี:Excel", "วิชาการ:คณิตศาสตร์"]),
]

LEGACY_DEMO_INTERESTS = {
    "somchai@chronoswap.local": ["ภาษาอังกฤษ", "กีตาร์"],
    "napat@chronoswap.local": ["Figma", "ภาษาอังกฤษ"],
    "mukda@chronoswap.local": ["Python", "การเขียนโปรแกรม"],
    "puri@chronoswap.local": ["Excel", "ภาษาอังกฤษ"],
    "ploy@chronoswap.local": ["Python", "ถ่ายภาพ"],
    "arm@chronoswap.local": ["กีตาร์", "ออกแบบ"],
    "fahsai@chronoswap.local": ["Excel", "คณิตศาสตร์"],
}

DEMO_OFFERS = {
    "somchai@chronoswap.local": [
        ("Python สำหรับผู้เริ่มต้น", "เทคโนโลยี", "เริ่มต้น", "ฝึกเขียน Python ตั้งแต่ตัวแปร เงื่อนไข ไปจนถึงโปรเจกต์เล็ก ๆ พร้อมลงมือทำ", "เย็นวันธรรมดา / นัดหมายล่วงหน้า"),
        ("Excel ใช้งานจริง", "เทคโนโลยี", "ทุกระดับ", "จัดตาราง ใช้สูตรพื้นฐาน และทำกราฟสรุปข้อมูลจากโจทย์ที่คุณนำมาได้เลย", "เสาร์–อาทิตย์ / นัดหมายล่วงหน้า"),
    ],
    "napat@chronoswap.local": [
        ("ติวคณิตศาสตร์วิศวกรรมพื้นฐาน", "วิชาการ", "เริ่มต้น", "ทบทวนแคลคูลัสและพีชคณิตเชิงเส้นผ่านโจทย์จริง ทีละขั้นตอน", "จันทร์–พุธ ช่วงเย็น"),
    ],
    "mukda@chronoswap.local": [
        ("ออกแบบ UI ด้วย Figma", "ออกแบบ", "เริ่มต้น", "เรียนรู้การวางโครงหน้าจอ ใช้ component และส่งต่องานออกแบบเป็นขั้นตอน", "จันทร์–พฤหัสบดี ช่วงเย็น"),
        ("จัดพอร์ตโฟลิโอให้น่าสนใจ", "ออกแบบ", "ทุกระดับ", "ช่วยปรับลำดับผลงาน เขียนคำอธิบาย และเลือกงานให้เหมาะกับเป้าหมายของคุณ", "นัดหมายล่วงหน้า"),
    ],
    "puri@chronoswap.local": [
        ("กีตาร์โปร่งสำหรับมือใหม่", "ดนตรี", "เริ่มต้น", "เริ่มจากการจับคอร์ด จังหวะ และเพลงแรกที่เล่นได้จริง ไม่ต้องมีพื้นฐานมาก่อน", "อังคาร / พฤหัสบดี"),
        ("ทฤษฎีดนตรีแบบเข้าใจง่าย", "ดนตรี", "เริ่มต้น", "ทำความเข้าใจโน้ต จังหวะ และคอร์ดผ่านตัวอย่างเพลงที่คุณชอบ", "นัดหมายล่วงหน้า"),
    ],
    "ploy@chronoswap.local": [
        ("สนทนาภาษาอังกฤษในชีวิตประจำวัน", "ภาษา", "ฝึกฝน", "ฝึกคุยจากสถานการณ์ใกล้ตัว พร้อมคำศัพท์และข้อเสนอแนะที่นำไปใช้ต่อได้", "ช่วงเย็น / นัดหมายล่วงหน้า"),
    ],
    "arm@chronoswap.local": [
        ("สถิติเบื้องต้นสำหรับทำรายงาน", "วิชาการ", "เริ่มต้น", "เลือกสถิติให้เหมาะกับข้อมูล อ่านผล และสรุปเป็นภาษาที่คนทั่วไปเข้าใจ", "เสาร์ ช่วงบ่าย"),
    ],
    "fahsai@chronoswap.local": [
        ("ถ่ายภาพด้วยมือถือให้ดูโปร", "ศิลปะ", "เริ่มต้น", "จัดองค์ประกอบ แสง และแต่งภาพเบื้องต้นด้วยแอปที่มีอยู่แล้ว", "เสาร์–อาทิตย์"),
        ("ตัดต่อวิดีโอสั้นสำหรับโซเชียล", "ศิลปะ", "ทุกระดับ", "วางโครงเรื่อง ตัดจังหวะ และใส่เสียงให้คลิปสั้นน่าดู", "นัดหมายล่วงหน้า"),
    ],
}


def purge_users(db: sqlite3.Connection, user_ids: list[int]) -> None:
    """ลบบัญชีและข้อมูลที่ผูกกับบัญชีเหล่านั้นทั้งหมด (เรียงตามลำดับ foreign key)"""
    if not user_ids:
        return
    marks = ",".join("?" for _ in user_ids)
    req_ids = [r[0] for r in db.execute(
        f"SELECT id FROM requests WHERE requester_id IN ({marks}) OR provider_id IN ({marks})", (*user_ids, *user_ids))]
    if req_ids:
        rm = ",".join("?" for _ in req_ids)
        db.execute(f"DELETE FROM reviews WHERE request_id IN ({rm})", req_ids)
        db.execute(f"DELETE FROM ledger WHERE request_id IN ({rm})", req_ids)
        db.execute(f"DELETE FROM messages WHERE request_id IN ({rm})", req_ids)
        db.execute(f"DELETE FROM notifications WHERE request_id IN ({rm})", req_ids)
        db.execute(f"UPDATE reports SET request_id=NULL WHERE request_id IN ({rm})", req_ids)
        db.execute(f"DELETE FROM requests WHERE id IN ({rm})", req_ids)
    db.execute(f"DELETE FROM reviews WHERE reviewer_id IN ({marks}) OR reviewee_id IN ({marks})", (*user_ids, *user_ids))
    db.execute(f"DELETE FROM messages WHERE sender_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM reports WHERE reporter_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM reports WHERE target_type='user' AND target_id IN ({marks})", user_ids)
    offer_ids = [r[0] for r in db.execute(f"SELECT id FROM offers WHERE user_id IN ({marks})", user_ids)]
    if offer_ids:
        om = ",".join("?" for _ in offer_ids)
        db.execute(f"DELETE FROM reports WHERE target_type='offer' AND target_id IN ({om})", offer_ids)
    db.execute(f"DELETE FROM offers WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM ledger WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM shop_purchases WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM shop_equipped WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM admin_audit WHERE admin_id IN ({marks})", user_ids)
    db.execute(f"UPDATE reports SET admin_id=NULL WHERE admin_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM notifications WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM email_tokens WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM sessions WHERE user_id IN ({marks})", user_ids)
    db.execute(f"DELETE FROM users WHERE id IN ({marks})", user_ids)


def seed_demo_data() -> None:
    """สร้างบัญชีตัวอย่าง 7 คน + ผู้ดูแล 1 คน (สร้างเฉพาะที่ยังไม่มี) และลบบัญชีเดโมชุดเก่าทิ้ง"""
    with db_connect() as db:
        legacy = [r[0] for r in db.execute(
            f"SELECT id FROM users WHERE lower(email) IN ({','.join('?' for _ in LEGACY_DEMO_EMAILS)})",
            [e.lower() for e in LEGACY_DEMO_EMAILS])]
        # แอดมินชุดเก่าใช้อีเมลเดียวกับชุดใหม่: ถ้ายังใช้รหัสเดิม (Admin@2026) ให้ลบทิ้งแล้วสร้างใหม่ด้วยรหัสใหม่
        old_admin = db.execute("SELECT id,password_hash FROM users WHERE email=? AND role='admin'", (ADMIN_EMAIL,)).fetchone()
        if old_admin and password_matches("Admin@2026", old_admin["password_hash"]):
            legacy.append(old_admin["id"])
        purge_users(db, legacy)
        created = iso_now()
        for email, password, name, faculty, bio, interests in DEMO_MEMBERS:
            existing = db.execute("SELECT id,interests FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                old_demo_interests = LEGACY_DEMO_INTERESTS.get(email)
                if old_demo_interests and safe_json_list(existing["interests"]) == old_demo_interests:
                    db.execute("UPDATE users SET interests=? WHERE id=?",
                               (json.dumps(interests, ensure_ascii=False), existing["id"]))
                continue
            cur = db.execute(
                "INSERT INTO users(email,password_hash,full_name,faculty,bio,interests,role,email_verified,created_at) VALUES(?,?,?,?,?,?,'member',1,?)",
                (email, password_hash(password), name, faculty, bio, json.dumps(interests, ensure_ascii=False), created),
            )
            db.execute("INSERT INTO ledger(user_id,amount_minutes,kind,description,created_at) VALUES(?,?,?,?,?)",
                       (cur.lastrowid, STARTING_CREDIT, "welcome", "เครดิตต้อนรับสมาชิกใหม่ 2 ชั่วโมง", created))
            for title, category, level, description, availability in DEMO_OFFERS.get(email, []):
                slots = json.dumps(infer_slots(availability))
                db.execute("INSERT INTO offers(user_id,title,category,level,description,availability,slots,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (cur.lastrowid, title, category, level, description, availability, slots, created))
        if not db.execute("SELECT 1 FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone():
            db.execute(
                "INSERT INTO users(email,password_hash,full_name,faculty,bio,interests,role,email_verified,created_at) VALUES(?,?,?,?,?,?,'admin',1,?)",
                (ADMIN_EMAIL, password_hash(ADMIN_PASSWORD), "ผู้ดูแลระบบ ChronoSwap", "ทีมชุมชน",
                 "ดูแลระบบ คำร้อง และกรณีที่ต้องตรวจสอบ", "[]", created),
            )


def reset_all_accounts() -> None:
    """ลบบัญชีและข้อมูลทั้งหมด แล้วสร้างบัญชีตัวอย่างชุดใหม่ (สำรองฐานข้อมูลเดิมไว้ก่อน)"""
    if DB_PATH.exists():
        stamp = now_utc().strftime("%Y%m%d-%H%M%S")
        backup_path = DB_PATH.with_name(f"before-reset-{stamp}.sqlite3")
        src_db = sqlite3.connect(DB_PATH)
        dst_db = sqlite3.connect(backup_path)
        with dst_db:
            src_db.backup(dst_db)
        src_db.close()
        dst_db.close()
        print(f"สำรองฐานข้อมูลเดิมไว้ที่ {backup_path}")
    with db_connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM users")]
        purge_users(db, ids)
        db.execute("DELETE FROM sqlite_sequence")
    if SEED_DEMO:
        seed_demo_data()


def clean_slots(value: object) -> list[str]:
    """ตรวจรายการช่วงเวลา (เช่น "mon-e") ให้อยู่ในชุดที่กำหนด เรียงลำดับคงที่"""
    if not isinstance(value, list):
        return []
    picked = {x for x in value if isinstance(x, str) and x in VALID_SLOTS}
    return [f"{d}-{p}" for d in SLOT_DAYS for p in SLOT_PERIODS if f"{d}-{p}" in picked]


def normalize_interests(value: object) -> list[str]:
    """Store each interest as one category:keyword pair; upgrade clear legacy pairs."""
    if not isinstance(value, list):
        return []
    entries = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    normalized: list[str] = []
    index = 0
    while index < len(entries):
        item = entries[index]
        if ":" in item:
            category, keyword = item.split(":", 1)
            category = category.strip()
            if category in SKILL_CATEGORIES:
                normalized.append(f"{category}:{keyword.strip()}")
            else:
                normalized.append(item)
            index += 1
        elif item in SKILL_CATEGORIES:
            keyword = ""
            if (index + 1 < len(entries) and entries[index + 1] not in SKILL_CATEGORIES
                    and ":" not in entries[index + 1]):
                keyword = entries[index + 1]
                index += 2
            else:
                index += 1
            normalized.append(f"{item}:{keyword}")
        else:
            # Keep ambiguous legacy terms for the user to pair explicitly in their profile.
            normalized.append(item)
            index += 1
    return list(dict.fromkeys(normalized))


def infer_slots(text: str) -> list[str]:
    """แปลงข้อความช่วงเวลาเดิม (เช่น "เย็นวันธรรมดา") เป็นช่วงเวลาแบบมีโครงสร้าง
    ไม่ระบุวัน/ช่วง หรือ "นัดหมายล่วงหน้า" ถือว่ายืดหยุ่น (ทุกช่วง)"""
    t = text or ""
    days: set[int] = set()
    if "วันธรรมดา" in t:
        days.update(range(0, 5))
    for i, name in enumerate(THAI_DAYS):
        if name in t:
            days.add(i)
    pattern = "(" + "|".join(THAI_DAYS) + ")\\s*[–-]\\s*(" + "|".join(THAI_DAYS) + ")"
    for a, b in re.findall(pattern, t):
        i, j = THAI_DAYS.index(a), THAI_DAYS.index(b)
        if i <= j:
            days.update(range(i, j + 1))
    periods = [p for word, p in (("เช้า", "m"), ("บ่าย", "a"), ("เย็น", "e")) if word in t]
    if not days:
        days = set(range(7))
    if not periods:
        periods = list(SLOT_PERIODS)
    return clean_slots([f"{SLOT_DAYS[i]}-{p}" for i in days for p in periods])


def schedule_fits_offer_slots(offer_slots: set[str], scheduled: datetime, duration_minutes: int) -> bool:
    """Check that the entire appointment fits one of the provider's Bangkok-time slots."""
    if not scheduled.tzinfo or duration_minutes <= 0:
        return False
    local_start = scheduled.astimezone(THAILAND_TIMEZONE)
    day = SLOT_DAYS[local_start.weekday()]
    start_second = local_start.hour * 3600 + local_start.minute * 60 + local_start.second
    end_second = start_second + duration_minutes * 60
    for period, (window_start, window_end) in SLOT_PERIOD_WINDOWS.items():
        if f"{day}-{period}" in offer_slots and window_start <= start_second and end_second <= window_end:
            return True
    return False


def match_components(interests: list[str], desired_level: str, wanted: set[str], offer_title: str,
                     offer_category: str, offer_level: str, offer_slots: set[str],
                     rating: float, review_count: int) -> dict:
    """คะแนนแต่ละปัจจัย ค่า 0–1"""
    title = offer_title.lower()
    category = offer_category.strip().lower()
    skill = 0.0
    for interest in normalize_interests(interests):
        if ":" not in interest:
            continue
        wanted_category, wanted_keyword = interest.split(":", 1)
        category_match = wanted_category.strip().lower() == category
        keyword_match = bool(wanted_keyword) and wanted_keyword.lower() in title
        if category_match and keyword_match:
            skill = 1.0
            break
        if category_match:
            skill = max(skill, 0.5)
    time_score = len(wanted & offer_slots) / len(wanted) if wanted else 0.0
    level = 1.0 if (offer_level == "ทุกระดับ" or (desired_level and offer_level == desired_level)) else 0.5
    rep = (rating / 5.0) if review_count else 0.5  # ผู้สอนใหม่เริ่มที่ 0.5 (แก้ Cold Start)
    total = (MATCH_WEIGHTS["skill"] * skill + MATCH_WEIGHTS["time"] * time_score
             + MATCH_WEIGHTS["level"] * level + MATCH_WEIGHTS["rep"] * rep)
    return {"skill": skill, "time": time_score, "level": level, "rep": rep, "total": total}


def safe_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(x) for x in parsed if isinstance(x, str)] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def is_unlimited(db: sqlite3.Connection, user_id: int) -> bool:
    row = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["role"] == "admin")


def credit_totals(db: sqlite3.Connection, user_id: int) -> dict:
    if is_unlimited(db, user_id):
        return {"balance": UNLIMITED_MINUTES, "available": UNLIMITED_MINUTES, "held_out": 0,
                "incoming_reserved": 0, "capacity_remaining": UNLIMITED_MINUTES, "cap": CREDIT_CAP, "unlimited": True}
    balance = int(db.execute("SELECT COALESCE(SUM(amount_minutes),0) FROM ledger WHERE user_id=?", (user_id,)).fetchone()[0])
    marks = ",".join("?" for _ in ACTIVE_HOLD_STATES)
    out_hold = int(db.execute(
        f"SELECT COALESCE(SUM(duration_minutes),0) FROM requests WHERE requester_id=? AND status IN ({marks})",
        (user_id, *ACTIVE_HOLD_STATES),
    ).fetchone()[0])
    in_hold = int(db.execute(
        f"SELECT COALESCE(SUM(duration_minutes),0) FROM requests WHERE provider_id=? AND status IN ({marks})",
        (user_id, *ACTIVE_HOLD_STATES),
    ).fetchone()[0])
    return {"balance": balance, "available": max(0, balance - out_hold), "held_out": out_hold,
            "incoming_reserved": in_hold, "capacity_remaining": max(0, CREDIT_CAP - balance - in_hold), "cap": CREDIT_CAP}


SHOP_ITEMS = [
    {"id": "frame-mint", "kind": "frame", "name": "กรอบมิ้นต์", "price": 30},
    {"id": "frame-sunset", "kind": "frame", "name": "กรอบพระอาทิตย์ตก", "price": 60},
    {"id": "frame-gold", "kind": "frame", "name": "กรอบทอง", "price": 100},
    {"id": "frame-galaxy", "kind": "frame", "name": "กรอบกาแล็กซี", "price": 150},
    {"id": "sticker-star", "kind": "sticker", "name": "ดาวเด่น", "emoji": "⭐", "price": 20},
    {"id": "sticker-fire", "kind": "sticker", "name": "ไฟแรง", "emoji": "🔥", "price": 30},
    {"id": "sticker-art", "kind": "sticker", "name": "นักออกแบบ", "emoji": "🎨", "price": 40},
    {"id": "sticker-rocket", "kind": "sticker", "name": "จรวด", "emoji": "🚀", "price": 50},
    {"id": "sticker-trophy", "kind": "sticker", "name": "ถ้วยรางวัล", "emoji": "🏆", "price": 80},
    {"id": "sticker-gem", "kind": "sticker", "name": "เพชร", "emoji": "💎", "price": 120},
]
SHOP_BY_ID = {item["id"]: item for item in SHOP_ITEMS}
COINS_PER_30_MIN = 10  # เหรียญแยกจากเครดิตเวลา: ได้ทั้งผู้เรียนและผู้สอนเมื่อกิจกรรมสำเร็จ


def shop_payload(db: sqlite3.Connection, user_id: int) -> dict:
    minutes = int(db.execute("SELECT COALESCE(SUM(duration_minutes),0) FROM requests WHERE status='completed' AND (requester_id=? OR provider_id=?)",
                             (user_id, user_id)).fetchone()[0])
    earned = minutes // 30 * COINS_PER_30_MIN
    spent = int(db.execute("SELECT COALESCE(SUM(price),0) FROM shop_purchases WHERE user_id=?", (user_id,)).fetchone()[0])
    owned = {row[0] for row in db.execute("SELECT item_id FROM shop_purchases WHERE user_id=?", (user_id,))}
    eq = db.execute("SELECT frame,sticker FROM shop_equipped WHERE user_id=?", (user_id,)).fetchone()
    equipped = {"frame": eq["frame"] if eq else "", "sticker": eq["sticker"] if eq else ""}
    unlimited = is_unlimited(db, user_id)
    return {"coins": UNLIMITED_COINS if unlimited else earned - spent, "unlimited": unlimited, "earned": earned, "spent": spent, "per_30_min": COINS_PER_30_MIN, "equipped": equipped,
            "items": [dict(item, owned=item["id"] in owned, equipped=item["id"] in equipped.values()) for item in SHOP_ITEMS]}


def cosmetics_for(db: sqlite3.Connection, user_ids) -> dict[int, dict[str, str]]:
    """กรอบ/สติกเกอร์ที่สมาชิกใส่อยู่ — ใช้แสดงให้ทุกคนเห็น ไม่ใช่เฉพาะเจ้าของบัญชี"""
    ids = sorted({int(x) for x in user_ids if x is not None})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    result: dict[int, dict[str, str]] = {}
    for r in db.execute(f"SELECT user_id,frame,sticker FROM shop_equipped WHERE user_id IN ({marks})", ids):
        frame = r["frame"] if r["frame"] in SHOP_BY_ID else ""
        sticker = SHOP_BY_ID[r["sticker"]]["emoji"] if r["sticker"] in SHOP_BY_ID else ""
        result[int(r["user_id"])] = {"frame": frame, "sticker": sticker}
    return result


def avatar_ref(db: sqlite3.Connection, user_id: int) -> str:
    """ลิงก์รูปโปรไฟล์แบบเบา (เบราว์เซอร์แคชได้) สำหรับรายการที่มีหลายคน; ว่างถ้าไม่มีรูป"""
    row = db.execute("SELECT length(avatar_data) AS n FROM users WHERE id=?", (user_id,)).fetchone()
    return f"/api/users/{int(user_id)}/avatar?v={row['n']}" if row and row["n"] else ""


def public_user(db: sqlite3.Connection, user_id: int) -> dict:
    row = db.execute("SELECT id,email,full_name,university,faculty,bio,interests,desired_level,preferred_slots,avatar_data,avatar_mime,role,is_active,email_verified,created_at FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise ApiError("ไม่พบบัญชีผู้ใช้", 404)
    eq = db.execute("SELECT frame,sticker FROM shop_equipped WHERE user_id=?", (user_id,)).fetchone()
    return {"id": row["id"], "email": row["email"], "full_name": row["full_name"],
            "university": row["university"], "faculty": row["faculty"], "bio": row["bio"],
            "interests": normalize_interests(safe_json_list(row["interests"])),
            "desired_level": row["desired_level"], "preferred_slots": clean_slots(safe_json_list(row["preferred_slots"])),
            "avatar_url": f"data:{row['avatar_mime']};base64,{row['avatar_data']}" if row["avatar_data"] and row["avatar_mime"] else "",
            "role": row["role"], "is_active": bool(row["is_active"]), "email_verified": bool(row["email_verified"]), "created_at": row["created_at"],
            "frame": (eq["frame"] if eq else ""), "sticker": SHOP_BY_ID[eq["sticker"]]["emoji"] if eq and eq["sticker"] in SHOP_BY_ID else ""}


def add_notification(db: sqlite3.Connection, user_id: int, kind: str, title: str, message: str,
                     dedupe_key: str, request_id: int | None = None) -> None:
    db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,request_id,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?)",
               (user_id, kind, title, message, request_id, dedupe_key, iso_now()))


def has_schedule_conflict(db: sqlite3.Connection, user_ids: tuple[int, ...], scheduled: datetime,
                          duration_minutes: int, exclude_request_id: int | None = None) -> bool:
    if not user_ids:
        return False
    marks = ",".join("?" for _ in user_ids)
    states = ",".join("?" for _ in ACTIVE_HOLD_STATES)
    sql = f"""SELECT 1 FROM requests
        WHERE status IN ({states}) AND (requester_id IN ({marks}) OR provider_id IN ({marks}))
          AND julianday(scheduled_at) < julianday(?, '+' || ? || ' minutes')
          AND julianday(scheduled_at, '+' || duration_minutes || ' minutes') > julianday(?)"""
    params: list[object] = [*ACTIVE_HOLD_STATES, *user_ids, *user_ids,
                            scheduled.isoformat(timespec="seconds"), duration_minutes,
                            scheduled.isoformat(timespec="seconds")]
    if exclude_request_id is not None:
        sql += " AND id<>?"
        params.append(exclude_request_id)
    return db.execute(sql + " LIMIT 1", params).fetchone() is not None


def issue_email_token(user_id: int, purpose: str) -> str:
    token = secrets.token_urlsafe(32)
    created = iso_now()
    hours = 24 if purpose == "verify_email" else 1
    with db_connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE email_tokens SET used_at=? WHERE user_id=? AND purpose=? AND used_at=''",
                   (created, user_id, purpose))
        db.execute("INSERT INTO email_tokens(user_id,purpose,token_hash,expires_at,created_at) VALUES(?,?,?,?,?)",
                   (user_id, purpose, hashlib.sha256(token.encode()).hexdigest(),
                    (now_utc() + timedelta(hours=hours)).isoformat(timespec="seconds"), created))
    return token


def deliver_email_token(user_id: int, purpose: str) -> tuple[bool, str]:
    with db_connect() as db:
        recipient = db.execute("SELECT email,full_name FROM users WHERE id=?", (user_id,)).fetchone()
    if not recipient:
        return False, ""
    token = issue_email_token(user_id, purpose)
    action = "verify" if purpose == "verify_email" else "reset"
    link = f"{PUBLIC_URL}/#{action}_token={token}"
    subject = "ยืนยันอีเมล ChronoSwap" if purpose == "verify_email" else "ตั้งรหัสผ่าน ChronoSwap ใหม่"
    instruction = "ยืนยันอีเมล" if purpose == "verify_email" else "ตั้งรหัสผ่านใหม่"
    text_body = f"สวัสดี {recipient['full_name']}\n\nกดลิงก์นี้เพื่อ{instruction}:\n{link}\n\nลิงก์นี้หมดอายุใน {24 if purpose == 'verify_email' else 1} ชั่วโมง หากคุณไม่ได้เป็นผู้เริ่มคำขอ สามารถละเว้นอีเมลนี้ได้"
    if BREVO_API_KEY and EMAIL_FROM:
        try:
            payload = json.dumps({
                "sender": {"name": "ChronoSwap", "email": EMAIL_FROM},
                "to": [{"email": recipient["email"], "name": recipient["full_name"]}],
                "subject": subject,
                "textContent": text_body,
            }).encode("utf-8")
            request = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=payload, method="POST",
                                             headers={"api-key": BREVO_API_KEY, "content-type": "application/json",
                                                      "accept": "application/json"})
            with urllib.request.urlopen(request, timeout=15) as response:
                if 200 <= response.status < 300:
                    return True, ""
            return False, link if DEV_EMAIL_LINKS else ""
        except (OSError, urllib.error.URLError) as error:
            print(f"ChronoSwap email delivery failed (Brevo API): {type(error).__name__} {getattr(error, 'code', '')}")
            return False, link if DEV_EMAIL_LINKS else ""
    if SMTP_HOST and EMAIL_FROM:
        try:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = EMAIL_FROM
            message["To"] = recipient["email"]
            message.set_content(text_body)
            if SMTP_PORT == 465:
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=ssl.create_default_context()) as server:
                    if SMTP_USER:
                        server.login(SMTP_USER, SMTP_PASSWORD)
                    server.send_message(message)
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                    server.starttls(context=ssl.create_default_context())
                    if SMTP_USER:
                        server.login(SMTP_USER, SMTP_PASSWORD)
                    server.send_message(message)
            return True, ""
        except (OSError, smtplib.SMTPException) as error:
            print(f"ChronoSwap email delivery failed: {type(error).__name__}")
            return False, link if DEV_EMAIL_LINKS else ""
    return False, link if DEV_EMAIL_LINKS else ""


def validate_avatar(value: object) -> tuple[str, str]:
    if value == "":
        return "", ""
    if not isinstance(value, str) or len(value) > 750_000:
        raise ApiError("รูปโปรไฟล์มีขนาดใหญ่เกินไป")
    match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise ApiError("รองรับเฉพาะรูป JPG, PNG หรือ WebP")
    mime = f"image/{match.group(1)}"
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("ข้อมูลรูปโปรไฟล์ไม่ถูกต้อง")
    if len(raw) > 512_000:
        raise ApiError("รูปโปรไฟล์มีขนาดใหญ่เกินไป")
    valid_signature = ((mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")) or
                       (mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or
                       (mime == "image/webp" and len(raw) > 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))
    if not valid_signature:
        raise ApiError("ชนิดไฟล์รูปโปรไฟล์ไม่ตรงกับข้อมูลรูป")
    return match.group(2), mime


def request_row(db: sqlite3.Connection, row: sqlite3.Row, viewer_id: int | None = None) -> dict:
    offer = db.execute("SELECT title,category FROM offers WHERE id=?", (row["offer_id"],)).fetchone()
    requester = db.execute("SELECT full_name FROM users WHERE id=?", (row["requester_id"],)).fetchone()
    provider = db.execute("SELECT full_name FROM users WHERE id=?", (row["provider_id"],)).fetchone()
    reviewed = db.execute("SELECT 1 FROM reviews WHERE request_id=? AND reviewer_id=?", (row["id"], viewer_id)).fetchone() if viewer_id else None
    cos = cosmetics_for(db, (row["requester_id"], row["provider_id"]))
    return {"id": row["id"], "offer_id": row["offer_id"], "offer_title": offer["title"] if offer else "ทักษะที่แลกเปลี่ยน",
            "category": offer["category"] if offer else "", "requester_id": row["requester_id"], "provider_id": row["provider_id"],
            "requester_name": requester["full_name"] if requester else "", "provider_name": provider["full_name"] if provider else "",
            "message": row["message"], "scheduled_at": row["scheduled_at"], "duration_minutes": row["duration_minutes"],
            "status": row["status"], "requester_confirmed": bool(row["requester_confirmed"]),
            "provider_confirmed": bool(row["provider_confirmed"]), "dispute_note": row["dispute_note"],
            "reviewed_by_viewer": bool(reviewed),
            "requester_frame": cos.get(row["requester_id"], {}).get("frame", ""),
            "requester_sticker": cos.get(row["requester_id"], {}).get("sticker", ""),
            "requester_avatar": avatar_ref(db, row["requester_id"]),
            "provider_frame": cos.get(row["provider_id"], {}).get("frame", ""),
            "provider_sticker": cos.get(row["provider_id"], {}).get("sticker", ""),
            "provider_avatar": avatar_ref(db, row["provider_id"]),
            "viewer_role": "requester" if viewer_id == row["requester_id"] else ("provider" if viewer_id == row["provider_id"] else "admin"),
            "created_at": row["created_at"]}


def offer_rows(db: sqlite3.Connection, user_id: int | None, query: str = "", category: str = "", mine: bool = False,
               recommend: bool = False) -> list[dict]:
    if recommend and user_id and credit_totals(db, user_id)["available"] < MIN_REQUEST_MINUTES:
        return []  # เงื่อนไขกรอง: ผู้เรียนต้องมีเครดิตพอสำหรับคำขอขั้นต่ำ
    where = ["o.active=1", "o.deleted_at=''"]
    params: list[object] = []
    if mine and user_id:
        where.append("o.user_id=?")
        params.append(user_id)
    elif not mine:
        where.append("u.role='member' AND u.is_active=1")
        if user_id:
            where.append("o.user_id<>?")
            params.append(user_id)
    if query:
        where.append("(o.title LIKE ? OR o.description LIKE ? OR o.category LIKE ? OR u.full_name LIKE ?)")
        pattern = f"%{query[:80]}%"
        params.extend([pattern, pattern, pattern, pattern])
    if category and category != "ทั้งหมด":
        where.append("o.category=?")
        params.append(category)
    rows = db.execute(f"""
        SELECT o.*, u.full_name, u.faculty, u.bio, u.avatar_data, u.avatar_mime,
          COALESCE((SELECT AVG(r.rating) FROM reviews r JOIN requests q ON q.id=r.request_id WHERE r.reviewee_id=u.id AND q.provider_id=u.id),0) AS rating,
          (SELECT COUNT(*) FROM reviews r JOIN requests q ON q.id=r.request_id WHERE r.reviewee_id=u.id AND q.provider_id=u.id) AS review_count
        FROM offers o JOIN users u ON u.id=o.user_id
        WHERE {' AND '.join(where)}
        ORDER BY o.created_at DESC, o.id DESC LIMIT 100
    """, params).fetchall()
    interests: list[str] = []
    desired_level = ""
    wanted: set[str] = set()
    if user_id:
        user = db.execute("SELECT interests,desired_level,preferred_slots FROM users WHERE id=?", (user_id,)).fetchone()
        interests = normalize_interests(safe_json_list(user["interests"])) if user else []
        desired_level = (user["desired_level"] if user else "") or ""
        wanted = set(clean_slots(safe_json_list(user["preferred_slots"]))) if user else set()
    cos = cosmetics_for(db, (row["user_id"] for row in rows))
    ranked = []
    for row in rows:
        comp = None
        if user_id and not mine:
            comp = match_components(interests, desired_level, wanted, row["title"], row["category"], row["level"],
                                   set(clean_slots(safe_json_list(row["slots"]))), float(row["rating"]), row["review_count"])
        raw_score = comp["total"] if comp else 0.0
        score = int(raw_score * 100 + 0.5)
        item = {"id": row["id"], "user_id": row["user_id"], "title": row["title"], "category": row["category"],
                       "level": row["level"], "description": row["description"], "availability": row["availability"],
                       "slots": clean_slots(safe_json_list(row["slots"])),
                       "provider_name": row["full_name"], "faculty": row["faculty"], "provider_bio": row["bio"],
                       "avatar_url": f"data:{row['avatar_mime']};base64,{row['avatar_data']}" if row["avatar_data"] and row["avatar_mime"] else "",
                       "frame": cos.get(row["user_id"], {}).get("frame", ""), "sticker": cos.get(row["user_id"], {}).get("sticker", ""),
                       "rating": round(float(row["rating"]), 1), "review_count": row["review_count"], "match_score": score,
                       "matched": bool(comp and comp["skill"] > 0),
                       "score_detail": ({k: int(comp[k] * 100 + 0.5) for k in ("skill", "time", "level", "rep")} if comp else None)}
        ranked.append((raw_score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
    return [item for _, item in ranked]


def post_ledger(db: sqlite3.Connection, user_id: int, amount: int, kind: str, description: str, request_id: int | None) -> None:
    db.execute("INSERT INTO ledger(user_id,amount_minutes,kind,description,request_id,created_at) VALUES(?,?,?,?,?,?)",
               (user_id, amount, kind, description, request_id, iso_now()))


def record_admin_audit(db: sqlite3.Connection, admin_id: int, action: str, target_type: str,
                       target_id: int, summary: str) -> None:
    db.execute("INSERT INTO admin_audit(admin_id,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?)",
               (admin_id, action, target_type, target_id, summary[:240], iso_now()))


def settle_request(db: sqlite3.Connection, row: sqlite3.Row, admin_override: bool = False) -> None:
    marks = ",".join("?" for _ in ACTIVE_HOLD_STATES)
    requester_unlimited = is_unlimited(db, row["requester_id"])
    provider_unlimited = is_unlimited(db, row["provider_id"])
    if not requester_unlimited:
        other_out = int(db.execute(
            f"SELECT COALESCE(SUM(duration_minutes),0) FROM requests WHERE requester_id=? AND id<>? AND status IN ({marks})",
            (row["requester_id"], row["id"], *ACTIVE_HOLD_STATES),
        ).fetchone()[0])
        requester_balance = int(db.execute("SELECT COALESCE(SUM(amount_minutes),0) FROM ledger WHERE user_id=?", (row["requester_id"],)).fetchone()[0])
        if requester_balance - other_out < row["duration_minutes"]:
            raise ApiError("เครดิตที่ใช้ได้ไม่เพียงพอสำหรับยืนยันกิจกรรมนี้", 409)
    if not provider_unlimited:
        other_in = int(db.execute(
            f"SELECT COALESCE(SUM(duration_minutes),0) FROM requests WHERE provider_id=? AND id<>? AND status IN ({marks})",
            (row["provider_id"], row["id"], *ACTIVE_HOLD_STATES),
        ).fetchone()[0])
        provider_balance = int(db.execute("SELECT COALESCE(SUM(amount_minutes),0) FROM ledger WHERE user_id=?", (row["provider_id"],)).fetchone()[0])
        if provider_balance + other_in + row["duration_minutes"] > CREDIT_CAP:
            raise ApiError("เครดิตของผู้สอนเต็ม 20 ชั่วโมง จึงยังโอนเครดิตไม่ได้", 409)
    minutes = int(row["duration_minutes"])
    title = db.execute("SELECT title FROM offers WHERE id=?", (row["offer_id"],)).fetchone()[0]
    if not requester_unlimited:
        post_ledger(db, row["requester_id"], -minutes, "lesson_spent", f"ใช้เครดิตเรียน: {title}", row["id"])
    if not provider_unlimited:
        post_ledger(db, row["provider_id"], minutes, "lesson_earned", f"ได้รับเครดิตจากการสอน: {title}", row["id"])
    db.execute("UPDATE requests SET status='completed',requester_confirmed=1,provider_confirmed=1,updated_at=? WHERE id=?",
               (iso_now(), row["id"]))


def valid_text(value: object, label: str, limit: int, minimum: int = 0) -> str:
    if not isinstance(value, str):
        raise ApiError(f"กรุณากรอก{label}")
    cleaned = value.strip()
    if len(cleaned) < minimum or len(cleaned) > limit:
        raise ApiError(f"{label}ต้องมีความยาว {minimum}–{limit} ตัวอักษร")
    return cleaned


def parse_schedule(value: object) -> datetime:
    if not isinstance(value, str):
        raise ApiError("กรุณาเลือกวันและเวลานัดหมาย")
    try:
        scheduled = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if scheduled.tzinfo is None:
            raise ValueError("timezone missing")
        return scheduled.astimezone(timezone.utc)
    except ValueError:
        raise ApiError("วันและเวลานัดหมายไม่ถูกต้อง")


class Handler(BaseHTTPRequestHandler):
    server_version = "ChronoSwap/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def send_json(self, status: int, payload: dict, extra_headers: list[tuple[str, str]] | None = None) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def send_attachment(self, content: bytes, filename: str, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(content)

    def read_body(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("รูปแบบคำขอไม่ถูกต้อง")
        if size > 1_000_000:
            raise ApiError("ข้อมูลคำขอมีขนาดใหญ่เกินไป", 413)
        raw = self.rfile.read(size) if size else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (UnicodeDecodeError, ValueError):
            raise ApiError("ข้อมูลคำขอไม่ถูกต้อง")

    def cookie_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get("chronoswap_session")
            return morsel.value if morsel else None
        except Exception:
            return None

    def current_user(self, required: bool = True) -> dict | None:
        token = self.cookie_token()
        if not token:
            if required:
                raise ApiError("กรุณาเข้าสู่ระบบก่อนใช้งาน", 401)
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with db_connect() as db:
            row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1",
                             (token_hash, iso_now())).fetchone()
            if not row:
                if required:
                    raise ApiError("เซสชันหมดอายุ กรุณาเข้าสู่ระบบอีกครั้ง", 401)
                return None
            return dict(row)

    def session_cookie(self, token: str, max_age: int) -> str:
        secure = "; Secure" if COOKIE_SECURE else ""
        return f"chronoswap_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}"

    def dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/"):
            if method != "GET":
                self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self.serve_static(path)
            return
        body = self.read_body() if method in ("POST", "PATCH", "PUT") else {}
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health" and method == "GET":
                self.send_json(200, {"ok": True, "service": "ChronoSwap"})
            elif re.fullmatch(r"/api/users/\d+", path) and method == "GET":
                self.member_profile(path)
            elif re.fullmatch(r"/api/users/\d+/avatar", path) and method == "GET":
                self.user_avatar(path)
            elif path == "/api/shop" and method == "GET":
                self.shop_get()
            elif path == "/api/shop/buy" and method == "POST":
                self.shop_buy(body)
            elif path == "/api/shop/equip" and method == "POST":
                self.shop_equip(body)
            elif path == "/api/session" and method == "GET":
                user = self.current_user(required=False)
                if user:
                    with db_connect() as db:
                        data = public_user(db, user["id"])
                        data["credits"] = credit_totals(db, user["id"])
                    self.send_json(200, {"user": data})
                else:
                    self.send_json(200, {"user": None})
            elif path == "/api/register" and method == "POST":
                self.register(body)
            elif path == "/api/login" and method == "POST":
                self.login(body)
            elif path == "/api/email/verify" and method == "POST":
                self.verify_email(body)
            elif path == "/api/email/resend" and method == "POST":
                self.resend_verification()
            elif path == "/api/password-reset/request" and method == "POST":
                self.request_password_reset(body)
            elif path == "/api/password-reset/complete" and method == "POST":
                self.complete_password_reset(body)
            elif path == "/api/logout" and method == "POST":
                self.logout()
            elif path == "/api/offers" and method == "GET":
                user = self.current_user(required=False)
                with db_connect() as db:
                    result = offer_rows(db, user["id"] if user else None, query.get("q", [""])[0], query.get("category", [""])[0])
                self.send_json(200, {"offers": result})
            elif path == "/api/offers" and method == "POST":
                self.create_offer(body)
            elif path == "/api/dashboard" and method == "GET":
                self.dashboard()
            elif path == "/api/calendar" and method == "GET":
                self.calendar(query)
            elif path == "/api/notifications" and method == "GET":
                self.get_notifications()
            elif path == "/api/notifications/read" and method == "POST":
                self.read_notifications(body)
            elif path == "/api/export" and method == "GET":
                self.export_user_data()
            elif path == "/api/reports" and method == "POST":
                self.create_report(body)
            elif path == "/api/profile" and method == "PATCH":
                self.update_profile(body)
            elif path == "/api/requests" and method == "GET":
                self.get_requests()
            elif path == "/api/requests" and method == "POST":
                self.create_request(body)
            elif path.startswith("/api/offers/") and method == "DELETE":
                self.archive_offer(path)
            elif path.startswith("/api/requests/") and path.endswith("/messages"):
                if method == "GET":
                    self.get_messages(path)
                elif method == "POST":
                    self.send_message(path, body)
                else:
                    self.send_json(405, {"error": "ไม่รองรับวิธีนี้"})
            elif path == "/api/admin/backup" and method == "GET":
                self.download_backup()
            elif path.startswith("/api/admin/offers/") and method == "DELETE":
                self.admin_delete_offer(path)
            elif path.startswith("/api/requests/"):
                self.request_action(path, method, body)
            elif path == "/api/admin" and method == "GET":
                self.admin_dashboard(query)
            elif path.startswith("/api/admin/") and method == "POST":
                self.admin_action(path, body)
            else:
                self.send_json(404, {"error": "ไม่พบเส้นทางที่ร้องขอ"})
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.IntegrityError as error:
            message = "ข้อมูลซ้ำหรือไม่สามารถทำรายการนี้ได้"
            if "users.email" in str(error):
                message = "อีเมลนี้มีบัญชีอยู่แล้ว"
            self.send_json(409, {"error": message})
        except Exception as error:  # keep failures visible in the local server console
            print(f"ChronoSwap error: {type(error).__name__}: {error}")
            self.send_json(500, {"error": "ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"})

    def member_profile(self, path: str) -> None:
        """โปรไฟล์ผู้สอนที่ใครก็ดูได้: ข้อมูลทั่วไป คะแนนรีวิว รีวิวล่าสุด และคอร์ดที่เปิดสอนทั้งหมด (ไม่เปิดเผยอีเมล)"""
        member_id = int(path.split("/")[3])
        viewer = self.current_user(required=False)
        with db_connect() as db:
            row = db.execute("SELECT id FROM users WHERE id=? AND role='member' AND is_active=1", (member_id,)).fetchone()
            if not row:
                raise ApiError("ไม่พบโปรไฟล์นี้", 404)
            person = public_user(db, member_id)
            stats = db.execute("""
                SELECT COALESCE(AVG(r.rating),0) AS rating, COUNT(*) AS n
                FROM reviews r JOIN requests q ON q.id=r.request_id
                WHERE r.reviewee_id=? AND q.provider_id=?""", (member_id, member_id)).fetchone()
            taught = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(duration_minutes),0) AS m FROM requests WHERE provider_id=? AND status='completed'",
                                (member_id,)).fetchone()
            learned = int(db.execute("SELECT COUNT(*) FROM requests WHERE requester_id=? AND status='completed'", (member_id,)).fetchone()[0])
            reviews = [{"rating": r["rating"], "comment": r["comment"], "reviewer_name": r["full_name"],
                        "offer_title": r["title"] or "", "created_at": r["created_at"]} for r in db.execute("""
                SELECT r.rating, r.comment, r.created_at, u.full_name, o.title
                FROM reviews r
                JOIN requests q ON q.id=r.request_id AND q.provider_id=r.reviewee_id
                JOIN users u ON u.id=r.reviewer_id
                LEFT JOIN offers o ON o.id=q.offer_id
                WHERE r.reviewee_id=? ORDER BY r.id DESC LIMIT 30""", (member_id,))]
            offers = [{"id": o["id"], "user_id": member_id, "title": o["title"], "category": o["category"], "level": o["level"],
                       "description": o["description"], "availability": o["availability"],
                       "slots": clean_slots(safe_json_list(o["slots"]))} for o in db.execute(
                "SELECT * FROM offers WHERE user_id=? AND active=1 AND deleted_at='' ORDER BY created_at DESC, id DESC", (member_id,))]
        self.send_json(200, {"profile": {
            "id": member_id, "full_name": person["full_name"], "university": person["university"], "faculty": person["faculty"],
            "bio": person["bio"], "interests": person["interests"], "avatar_url": person["avatar_url"],
            "frame": person["frame"], "sticker": person["sticker"], "member_since": person["created_at"],
            "rating": round(float(stats["rating"]), 1), "review_count": stats["n"],
            "taught_sessions": taught["n"], "taught_minutes": taught["m"], "learned_sessions": learned,
            "is_self": bool(viewer and viewer["id"] == member_id),
            "offers": offers, "reviews": reviews}})

    def user_avatar(self, path: str) -> None:
        user_id = int(path.split("/")[3])
        with db_connect() as db:
            row = db.execute("SELECT avatar_data,avatar_mime FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or not row["avatar_data"] or row["avatar_mime"] not in ("image/jpeg", "image/png", "image/webp"):
            self.send_error(404)
            return
        content = base64.b64decode(row["avatar_data"])
        self.send_response(200)
        self.send_header("Content-Type", row["avatar_mime"])
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def serve_static(self, path: str) -> None:
        static_files = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/mark.svg": "mark.svg"}
        filename = static_files.get(path)
        if not filename:
            self.send_error(404)
            return
        file_path = WEB / filename
        content = file_path.read_bytes()
        content_type = "text/html; charset=utf-8" if filename.endswith(".html") else ("text/css; charset=utf-8" if filename.endswith(".css") else ("image/svg+xml" if filename.endswith(".svg") else "text/javascript; charset=utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def register(self, body: dict) -> None:
        name = valid_text(body.get("full_name"), "ชื่อ", 80, 2)
        email = valid_text(body.get("email"), "อีเมล", 160, 5).lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ApiError("รูปแบบอีเมลไม่ถูกต้อง")
        password = body.get("password", "")
        if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
            raise ApiError("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
        faculty = valid_text(body.get("faculty", ""), "คณะ", 100)
        created = iso_now()
        with db_connect() as db:
            cur = db.execute("INSERT INTO users(email,password_hash,full_name,faculty,created_at) VALUES(?,?,?,?,?)",
                             (email, password_hash(password), name, faculty, created))
            user_id = cur.lastrowid
            post_ledger(db, user_id, STARTING_CREDIT, "welcome", "เครดิตต้อนรับสมาชิกใหม่ 2 ชั่วโมง", None)
        sent, link = deliver_email_token(user_id, "verify_email")
        extra = {"verification_email_sent": sent}
        if link:
            extra["verification_url"] = link
        self.create_session(user_id, extra)

    def create_session(self, user_id: int, extra: dict | None = None) -> None:
        token = secrets.token_urlsafe(32)
        expiry = (now_utc() + timedelta(seconds=SESSION_SECONDS)).isoformat(timespec="seconds")
        with db_connect() as db:
            db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), user_id, expiry))
            user = public_user(db, user_id)
            user["credits"] = credit_totals(db, user_id)
        payload = {"user": user, **(extra or {})}
        self.send_json(200, payload, [("Set-Cookie", self.session_cookie(token, SESSION_SECONDS))])

    def verify_email(self, body: dict) -> None:
        token = body.get("token")
        if not isinstance(token, str) or len(token) < 20 or len(token) > 200:
            raise ApiError("ลิงก์ยืนยันอีเมลไม่ถูกต้องหรือหมดอายุ", 400)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,user_id FROM email_tokens WHERE token_hash=? AND purpose='verify_email' AND used_at='' AND expires_at>?",
                             (digest, iso_now())).fetchone()
            if not row:
                raise ApiError("ลิงก์ยืนยันอีเมลไม่ถูกต้องหรือหมดอายุ", 400)
            db.execute("UPDATE email_tokens SET used_at=? WHERE id=?", (iso_now(), row["id"]))
            db.execute("UPDATE users SET email_verified=1 WHERE id=?", (row["user_id"],))
        self.send_json(200, {"ok": True, "message": "ยืนยันอีเมลเรียบร้อยแล้ว"})

    def resend_verification(self) -> None:
        user = self.current_user()
        if user["email_verified"]:
            self.send_json(200, {"ok": True, "already_verified": True})
            return
        sent, link = deliver_email_token(user["id"], "verify_email")
        payload = {"ok": True, "email_sent": sent}
        if link:
            payload["verification_url"] = link
        self.send_json(200, payload)

    def request_password_reset(self, body: dict) -> None:
        email = str(body.get("email", "")).strip().lower()
        payload = {"ok": True, "message": "หากอีเมลนี้มีบัญชี ระบบจะส่งลิงก์ตั้งรหัสผ่านใหม่ให้"}
        with db_connect() as db:
            row = db.execute("SELECT id FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
        if row:
            _sent, link = deliver_email_token(row["id"], "password_reset")
            if link:
                payload["reset_url"] = link
        self.send_json(200, payload)

    def complete_password_reset(self, body: dict) -> None:
        token = body.get("token")
        password = body.get("password", "")
        if not isinstance(token, str) or len(token) < 20 or len(token) > 200:
            raise ApiError("ลิงก์ตั้งรหัสผ่านไม่ถูกต้องหรือหมดอายุ", 400)
        if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
            raise ApiError("รหัสผ่านต้องมี 8–128 ตัวอักษร")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,user_id FROM email_tokens WHERE token_hash=? AND purpose='password_reset' AND used_at='' AND expires_at>?",
                             (digest, iso_now())).fetchone()
            if not row:
                raise ApiError("ลิงก์ตั้งรหัสผ่านไม่ถูกต้องหรือหมดอายุ", 400)
            db.execute("UPDATE email_tokens SET used_at=? WHERE id=?", (iso_now(), row["id"]))
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(password), row["user_id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
        self.send_json(200, {"ok": True, "message": "เปลี่ยนรหัสผ่านแล้ว กรุณาเข้าสู่ระบบอีกครั้ง"})

    def login(self, body: dict) -> None:
        email = str(body.get("email", "")).strip().lower()
        password = body.get("password", "")
        with db_connect() as db:
            row = db.execute("SELECT id,password_hash,is_active FROM users WHERE email=?", (email,)).fetchone()
        if row and not row["is_active"]:
            raise ApiError("บัญชีนี้ถูกระงับการใช้งาน กรุณาติดต่อผู้ดูแลระบบ", 403)
        if not row or not isinstance(password, str) or not password_matches(password, row["password_hash"]):
            raise ApiError("อีเมลหรือรหัสผ่านไม่ถูกต้อง", 401)
        self.create_session(row["id"])

    def logout(self) -> None:
        token = self.cookie_token()
        if token:
            with db_connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        self.send_json(200, {"ok": True}, [("Set-Cookie", self.session_cookie("", 0))])

    def dashboard(self) -> None:
        user = self.current_user()
        with db_connect() as db:
            current = public_user(db, user["id"])
            current["credits"] = credit_totals(db, user["id"])
            mine = db.execute("SELECT * FROM offers WHERE user_id=? AND deleted_at='' ORDER BY active DESC, created_at DESC", (user["id"],)).fetchall()
            own_offers = [{"id": x["id"], "title": x["title"], "category": x["category"], "level": x["level"],
                           "description": x["description"], "availability": x["availability"], "active": bool(x["active"])} for x in mine]
            marks = ",".join("?" for _ in ACTIVE_HOLD_STATES)
            reqs = db.execute(f"SELECT * FROM requests WHERE requester_id=? OR provider_id=? ORDER BY created_at DESC LIMIT 100",
                              (user["id"], user["id"])).fetchall()
            requests = [request_row(db, x, user["id"]) for x in reqs]
            ledger = db.execute("SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
            movements = [{"amount_minutes": x["amount_minutes"], "kind": x["kind"], "description": x["description"], "created_at": x["created_at"]} for x in ledger]
            completed = db.execute("SELECT COUNT(*) FROM requests WHERE (requester_id=? OR provider_id=?) AND status='completed'", (user["id"], user["id"])).fetchone()[0]
            pending = db.execute(f"SELECT COUNT(*) FROM requests WHERE (requester_id=? OR provider_id=?) AND status IN ({marks})", (user["id"], user["id"], *ACTIVE_HOLD_STATES)).fetchone()[0]
            recommendations = offer_rows(db, user["id"], "", "", recommend=True)[:6]
        self.send_json(200, {"user": current, "offers": own_offers, "requests": requests, "ledger": movements,
                             "recommendations": recommendations, "stats": {"completed": completed, "active_requests": pending}})

    def shop_get(self) -> None:
        user = self.current_user()
        with db_connect() as db:
            self.send_json(200, shop_payload(db, user["id"]))

    def shop_buy(self, body: dict) -> None:
        user = self.current_user()
        item = SHOP_BY_ID.get(body.get("item_id"))
        if not item:
            raise ApiError("ไม่พบสินค้านี้", 404)
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = shop_payload(db, user["id"])
            if any(x["id"] == item["id"] and x["owned"] for x in state["items"]):
                raise ApiError("คุณมีไอเทมนี้แล้ว", 409)
            if state["coins"] < item["price"]:
                raise ApiError(f"เหรียญไม่พอ ต้องใช้ {item['price']} เหรียญ", 409)
            db.execute("INSERT INTO shop_purchases(user_id,item_id,price,created_at) VALUES(?,?,?,?)",
                       (user["id"], item["id"], item["price"], iso_now()))
        self.send_json(200, {"ok": True})

    def shop_equip(self, body: dict) -> None:
        user = self.current_user()
        kind, item_id = body.get("kind"), body.get("item_id") or ""
        if kind not in ("frame", "sticker"):
            raise ApiError("ประเภทไอเทมไม่ถูกต้อง")
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if item_id:
                item = SHOP_BY_ID.get(item_id)
                owned = db.execute("SELECT 1 FROM shop_purchases WHERE user_id=? AND item_id=?", (user["id"], item_id)).fetchone()
                if not item or item["kind"] != kind or not owned:
                    raise ApiError("ยังไม่มีไอเทมนี้", 409)
            db.execute("INSERT OR IGNORE INTO shop_equipped(user_id) VALUES(?)", (user["id"],))
            column = "frame" if kind == "frame" else "sticker"
            db.execute(f"UPDATE shop_equipped SET {column}=? WHERE user_id=?", (item_id, user["id"]))
        self.send_json(200, {"ok": True})

    def calendar(self, query: dict) -> None:
        user = self.current_user()
        month = query.get("month", [now_utc().strftime("%Y-%m")])[0]
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ApiError("รูปแบบเดือนในปฏิทินไม่ถูกต้อง")
        try:
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
            end = datetime(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1, tzinfo=timezone.utc)
        except ValueError:
            raise ApiError("รูปแบบเดือนในปฏิทินไม่ถูกต้อง")
        with db_connect() as db:
            rows = db.execute("""SELECT * FROM requests
                WHERE (requester_id=? OR provider_id=?)
                  AND status IN ('pending','accepted','awaiting_confirmation','disputed')
                  AND scheduled_at>=? AND scheduled_at<?
                ORDER BY scheduled_at ASC LIMIT 200""",
                (user["id"], user["id"], (start - timedelta(days=1)).isoformat(timespec="seconds"),
                 (end + timedelta(days=1)).isoformat(timespec="seconds"))).fetchall()
            events = [request_row(db, row, user["id"]) for row in rows]
        self.send_json(200, {"month": month, "events": events})

    def get_notifications(self) -> None:
        user = self.current_user()
        now = now_utc()
        reminder_end = now + timedelta(hours=24)
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT id,requester_id,provider_id,scheduled_at FROM requests
                WHERE status='accepted' AND scheduled_at>=? AND scheduled_at<=?""",
                (now.isoformat(timespec="seconds"), reminder_end.isoformat(timespec="seconds"))).fetchall()
            for row in rows:
                when = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
                display_time = when.astimezone().strftime("%d/%m %H:%M")
                for recipient in (row["requester_id"], row["provider_id"]):
                    add_notification(db, recipient, "reminder", "ใกล้ถึงเวลานัดหมาย",
                                     f"กิจกรรม #{row['id']} เริ่ม {display_time}",
                                     f"reminder:{row['id']}:{row['scheduled_at']}:{recipient}", row["id"])
            notifications = db.execute("SELECT id,kind,title,message,request_id,created_at,read_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",
                                       (user["id"],)).fetchall()
        self.send_json(200, {"notifications": [{"id": row["id"], "kind": row["kind"], "title": row["title"],
            "message": row["message"], "request_id": row["request_id"], "created_at": row["created_at"],
            "is_read": bool(row["read_at"])} for row in notifications]})

    def read_notifications(self, body: dict) -> None:
        user = self.current_user()
        notification_id = body.get("id")
        with db_connect() as db:
            if notification_id is None:
                db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at=''", (iso_now(), user["id"]))
            else:
                try:
                    notification_id = int(notification_id)
                except (TypeError, ValueError):
                    raise ApiError("ไม่พบการแจ้งเตือน")
                db.execute("UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at=''",
                           (iso_now(), notification_id, user["id"]))
        self.send_json(200, {"ok": True})

    def export_user_data(self) -> None:
        user = self.current_user()
        with db_connect() as db:
            profile = public_user(db, user["id"])
            offers = [dict(row) for row in db.execute("SELECT id,title,category,level,description,availability,active,created_at FROM offers WHERE user_id=? ORDER BY id", (user["id"],)).fetchall()]
            requests = [dict(row) for row in db.execute("SELECT id,offer_id,requester_id,provider_id,message,scheduled_at,duration_minutes,status,requester_confirmed,provider_confirmed,dispute_note,created_at,updated_at FROM requests WHERE requester_id=? OR provider_id=? ORDER BY id", (user["id"], user["id"])).fetchall()]
            ledger = [dict(row) for row in db.execute("SELECT id,amount_minutes,kind,description,request_id,created_at FROM ledger WHERE user_id=? ORDER BY id", (user["id"],)).fetchall()]
            messages = [dict(row) for row in db.execute("SELECT m.id,m.request_id,m.sender_id,m.body,m.created_at FROM messages m JOIN requests r ON r.id=m.request_id WHERE r.requester_id=? OR r.provider_id=? ORDER BY m.id", (user["id"], user["id"])).fetchall()]
            reports = [dict(row) for row in db.execute("SELECT id,target_type,target_id,request_id,reason,details,status,created_at,updated_at FROM reports WHERE reporter_id=? ORDER BY id", (user["id"],)).fetchall()]
        payload = {"exported_at": iso_now(), "profile": profile, "offers": offers, "requests": requests,
                   "credit_ledger": ledger, "messages": messages, "reports_submitted": reports}
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"chronoswap-account-{user['id']}.json"
        self.send_attachment(content, filename, "application/json; charset=utf-8")

    def create_report(self, body: dict) -> None:
        user = self.current_user()
        if not user["email_verified"]:
            raise ApiError("กรุณายืนยันอีเมลก่อนส่งรายงาน", 403)
        target_type = body.get("target_type")
        reason = valid_text(body.get("reason"), "เหตุผลที่รายงาน", 120, 3)
        details = valid_text(body.get("details", ""), "รายละเอียด", 800)
        try:
            target_id = int(body.get("target_id"))
            request_id = int(body["request_id"]) if body.get("request_id") is not None else None
        except (TypeError, ValueError):
            raise ApiError("เลือกรายการที่ต้องการรายงาน")
        if target_type not in ("user", "offer") or target_id <= 0:
            raise ApiError("เลือกรายการที่ต้องการรายงาน")
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if target_type == "user":
                target = db.execute("SELECT id FROM users WHERE id=? AND role='member' AND is_active=1", (target_id,)).fetchone()
            else:
                target = db.execute("SELECT id,user_id FROM offers WHERE id=?", (target_id,)).fetchone()
                if target and target["user_id"] == user["id"]:
                    raise ApiError("ไม่สามารถรายงานประกาศของตนเองได้")
            if not target or (target_type == "user" and target_id == user["id"]):
                raise ApiError("ไม่พบสมาชิกหรือประกาศที่รายงาน", 404)
            if request_id is not None:
                request = db.execute("SELECT requester_id,provider_id FROM requests WHERE id=?", (request_id,)).fetchone()
                if not request or user["id"] not in (request["requester_id"], request["provider_id"]):
                    raise ApiError("ไม่พบกิจกรรมที่เกี่ยวข้อง", 404)
            duplicate = db.execute("SELECT id FROM reports WHERE reporter_id=? AND target_type=? AND target_id=? AND status IN ('open','reviewed') LIMIT 1",
                                   (user["id"], target_type, target_id)).fetchone()
            if duplicate:
                raise ApiError("คุณส่งรายงานรายการนี้และกำลังรอตรวจสอบอยู่แล้ว", 409)
            cur = db.execute("INSERT INTO reports(reporter_id,target_type,target_id,request_id,reason,details,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                             (user["id"], target_type, target_id, request_id, reason, details, iso_now(), iso_now()))
            report_id = cur.lastrowid
            add_notification(db, user["id"], "report", "ส่งรายงานแล้ว", "ทีมผู้ดูแลจะตรวจสอบรายงานของคุณ",
                             f"report-submitted:{report_id}")
        self.send_json(201, {"ok": True, "id": report_id})

    def messages_request(self, path: str) -> tuple[dict, sqlite3.Row]:
        user = self.current_user()
        parts = path.strip("/").split("/")
        try:
            request_id = int(parts[2])
        except (IndexError, ValueError):
            raise ApiError("ไม่พบกิจกรรม", 404)
        with db_connect() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise ApiError("ไม่พบกิจกรรม", 404)
        if user["id"] not in (row["requester_id"], row["provider_id"]):
            raise ApiError("ไม่มีสิทธิ์อ่านข้อความของกิจกรรมนี้", 403)
        return user, row

    def get_messages(self, path: str) -> None:
        user, request = self.messages_request(path)
        read_column = "read_by_requester" if user["id"] == request["requester_id"] else "read_by_provider"
        with db_connect() as db:
            db.execute(f"UPDATE messages SET {read_column}=1 WHERE request_id=? AND sender_id<>?", (request["id"], user["id"]))
            rows = db.execute("SELECT m.id,m.sender_id,m.body,m.created_at,u.full_name AS sender_name FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.request_id=? ORDER BY m.id DESC LIMIT 100",
                              (request["id"],)).fetchall()
            db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND request_id=? AND kind='message' AND read_at=''",
                       (iso_now(), user["id"], request["id"]))
            cos = cosmetics_for(db, (row["sender_id"] for row in rows))
            refs = {sid: avatar_ref(db, sid) for sid in {row["sender_id"] for row in rows}}
        messages = [{"id": row["id"], "sender_id": row["sender_id"], "sender_name": row["sender_name"],
                     "sender_frame": cos.get(row["sender_id"], {}).get("frame", ""),
                     "sender_sticker": cos.get(row["sender_id"], {}).get("sticker", ""),
                     "sender_avatar": refs.get(row["sender_id"], ""),
                     "body": row["body"], "created_at": row["created_at"]} for row in reversed(rows)]
        self.send_json(200, {"messages": messages})

    def send_message(self, path: str, body: dict) -> None:
        user, request = self.messages_request(path)
        if not user["email_verified"]:
            raise ApiError("กรุณายืนยันอีเมลก่อนส่งข้อความ", 403)
        if request["status"] not in ACTIVE_HOLD_STATES and request["status"] != "completed":
            raise ApiError("แชทเปิดได้สำหรับคำขอที่กำลังดำเนินการหรือเสร็จสิ้นแล้ว", 409)
        text = valid_text(body.get("body"), "ข้อความ", 2000, 1)
        other = request["provider_id"] if user["id"] == request["requester_id"] else request["requester_id"]
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("INSERT INTO messages(request_id,sender_id,body,created_at) VALUES(?,?,?,?)",
                             (request["id"], user["id"], text, iso_now()))
            message_id = cur.lastrowid
            add_notification(db, other, "message", "มีข้อความใหม่", f"คุณได้รับข้อความเกี่ยวกับกิจกรรม #{request['id']}",
                             f"message:{message_id}", request["id"])
        self.send_json(201, {"ok": True, "id": message_id})

    def download_backup(self) -> None:
        user = self.current_user()
        if user["role"] != "admin":
            raise ApiError("เฉพาะผู้ดูแลระบบเท่านั้นที่สำรองฐานข้อมูลได้", 403)
        with tempfile.TemporaryDirectory(prefix="chronoswap-backup-") as directory:
            path = Path(directory) / "chronoswap-backup.sqlite3"
            with db_connect() as source:
                target = sqlite3.connect(path)
                source.backup(target)
                target.close()
            content = path.read_bytes()
        filename = f"chronoswap-backup-{now_utc().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        self.send_attachment(content, filename, "application/vnd.sqlite3")

    def update_profile(self, body: dict) -> None:
        user = self.current_user()
        name = valid_text(body.get("full_name"), "ชื่อ", 80, 2)
        university = valid_text(body.get("university", ""), "มหาวิทยาลัย", 120)
        faculty = valid_text(body.get("faculty", ""), "คณะ", 100)
        bio = valid_text(body.get("bio", ""), "แนะนำตัว", 500)
        interests = body.get("interests", [])
        if not isinstance(interests, list) or len(interests) > 8 or any(not isinstance(x, str) or len(x.strip()) > 50 for x in interests):
            raise ApiError("เพิ่มทักษะที่อยากเรียนได้ไม่เกิน 8 รายการ")
        cleaned_interests = normalize_interests(interests)
        avatar_supplied = "avatar_url" in body
        avatar_data, avatar_mime = validate_avatar(body.get("avatar_url")) if avatar_supplied else ("", "")
        with db_connect() as db:
            if avatar_supplied:
                db.execute("UPDATE users SET full_name=?,university=?,faculty=?,bio=?,interests=?,avatar_data=?,avatar_mime=? WHERE id=?",
                           (name, university, faculty, bio, json.dumps(cleaned_interests, ensure_ascii=False), avatar_data, avatar_mime, user["id"]))
            else:
                db.execute("UPDATE users SET full_name=?,university=?,faculty=?,bio=?,interests=? WHERE id=?",
                           (name, university, faculty, bio, json.dumps(cleaned_interests, ensure_ascii=False), user["id"]))
            if "desired_level" in body:
                level_value = body.get("desired_level") or ""
                if level_value not in LEVELS + [""]:
                    raise ApiError("ระดับที่อยากเรียนไม่ถูกต้อง")
                db.execute("UPDATE users SET desired_level=? WHERE id=?", (level_value, user["id"]))
            if "preferred_slots" in body:
                db.execute("UPDATE users SET preferred_slots=? WHERE id=?", (json.dumps(clean_slots(body.get("preferred_slots"))), user["id"]))
        self.send_json(200, {"ok": True})

    def create_offer(self, body: dict) -> None:
        user = self.current_user()
        if not user["email_verified"]:
            raise ApiError("กรุณายืนยันอีเมลก่อนลงประกาศทักษะ", 403)
        if user["role"] != "member":
            raise ApiError("บัญชีผู้ดูแลไม่สามารถลงประกาศทักษะได้", 403)
        title = valid_text(body.get("title"), "ชื่อทักษะ", 100, 3)
        category = valid_text(body.get("category"), "หมวดหมู่", 40, 2)
        level = valid_text(body.get("level"), "ระดับ", 30, 2)
        description = valid_text(body.get("description"), "รายละเอียด", 600, 12)
        availability = valid_text(body.get("availability", ""), "ช่วงเวลาที่สะดวก", 120)
        with db_connect() as db:
            active = db.execute("SELECT COUNT(*) FROM offers WHERE user_id=? AND active=1", (user["id"],)).fetchone()[0]
            if active >= 12:
                raise ApiError("ลงประกาศที่เปิดใช้งานได้ไม่เกิน 12 รายการ")
            slots = clean_slots(body.get("slots")) or infer_slots(availability)
            cur = db.execute("INSERT INTO offers(user_id,title,category,level,description,availability,slots,created_at) VALUES(?,?,?,?,?,?,?,?)",
                             (user["id"], title, category, level, description, availability, json.dumps(slots), iso_now()))
        self.send_json(201, {"id": cur.lastrowid})

    def archive_offer(self, path: str) -> None:
        user = self.current_user()
        try:
            offer_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            raise ApiError("ไม่พบประกาศ", 404)
        with db_connect() as db:
            row = db.execute("SELECT user_id FROM offers WHERE id=?", (offer_id,)).fetchone()
            if not row:
                raise ApiError("ไม่พบประกาศ", 404)
            if row["user_id"] != user["id"]:
                raise ApiError("ไม่มีสิทธิ์แก้ไขประกาศนี้", 403)
            db.execute("UPDATE offers SET active=0 WHERE id=?", (offer_id,))
        self.send_json(200, {"ok": True})

    def get_requests(self) -> None:
        user = self.current_user()
        with db_connect() as db:
            rows = db.execute("SELECT * FROM requests WHERE requester_id=? OR provider_id=? ORDER BY created_at DESC LIMIT 100", (user["id"], user["id"])).fetchall()
            result = [request_row(db, row, user["id"]) for row in rows]
        self.send_json(200, {"requests": result})

    def create_request(self, body: dict) -> None:
        user = self.current_user()
        if not user["email_verified"]:
            raise ApiError("กรุณายืนยันอีเมลก่อนส่งคำขอเรียน", 403)
        try:
            offer_id = int(body.get("offer_id"))
            duration = int(body.get("duration_minutes"))
        except (TypeError, ValueError):
            raise ApiError("ข้อมูลการนัดหมายไม่ครบถ้วน")
        if duration < 30 or duration > 180 or duration % 30:
            raise ApiError("เลือกเวลาเรียนครั้งละ 30 นาที ถึง 3 ชั่วโมง โดยเพิ่มครั้งละ 30 นาที")
        scheduled = parse_schedule(body.get("scheduled_at"))
        if scheduled <= now_utc() + timedelta(minutes=15):
            raise ApiError("เลือกเวลานัดหมายล่วงหน้าอย่างน้อย 15 นาที")
        if scheduled > now_utc() + timedelta(days=180):
            raise ApiError("เลือกเวลานัดหมายภายใน 6 เดือน")
        message = valid_text(body.get("message", ""), "ข้อความถึงผู้สอน", 400)
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            offer = db.execute("SELECT * FROM offers WHERE id=? AND active=1", (offer_id,)).fetchone()
            if not offer or offer["user_id"] == user["id"]:
                raise ApiError("ไม่พบประกาศที่เลือก หรือไม่สามารถขอเรียนกับประกาศของตนเองได้", 404)
            offer_slots = set(clean_slots(safe_json_list(offer["slots"])))
            if not schedule_fits_offer_slots(offer_slots, scheduled, duration):
                raise ApiError("เวลานัดหมายต้องอยู่ภายในช่วงที่ผู้สอนระบุ (เช้า 06:00–12:00, บ่าย 12:00–18:00, เย็น 18:00–24:00) กรุณาเลือกเวลาใหม่", 409)
            learner = credit_totals(db, user["id"])
            teacher = credit_totals(db, offer["user_id"])
            if learner["available"] < duration:
                raise ApiError("เครดิตที่ใช้ได้ไม่พอ ลองลดเวลาเรียนหรือรับสอนเพื่อสะสมเครดิตก่อน")
            if teacher["capacity_remaining"] < duration:
                raise ApiError("ผู้สอนมีเครดิตใกล้เต็มแล้ว ลองเลือกประกาศอื่น")
            if has_schedule_conflict(db, (user["id"], offer["user_id"]), scheduled, duration):
                raise ApiError("ช่วงเวลานี้มีนัดหมายหรือคำขออื่นอยู่แล้ว กรุณาเลือกเวลาใหม่", 409)
            cur = db.execute("INSERT INTO requests(offer_id,requester_id,provider_id,message,scheduled_at,duration_minutes,status,created_at,updated_at) VALUES(?,?,?,?,?,?, 'pending',?,?)",
                             (offer_id, user["id"], offer["user_id"], message, scheduled.isoformat(timespec="seconds"), duration, iso_now(), iso_now()))
            request_id = cur.lastrowid
            add_notification(db, offer["user_id"], "request", "มีคำขอเรียนใหม่",
                             f"มีคนส่งคำขอเรียนทักษะ {offer['title']}", f"request:{request_id}:new:{offer['user_id']}", request_id)
        self.send_json(201, {"id": request_id, "message": "ส่งคำขอแล้ว ระบบกันเครดิตไว้จนกว่าคำขอจะจบ"})

    def request_action(self, path: str, method: str, body: dict) -> None:
        if method not in ("POST", "PATCH"):
            self.send_json(405, {"error": "ไม่รองรับวิธีนี้"})
            return
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "requests":
            self.send_json(404, {"error": "ไม่พบคำขอ"})
            return
        try:
            request_id = int(parts[2])
        except ValueError:
            raise ApiError("ไม่พบคำขอ", 404)
        action = parts[3]
        user = self.current_user()
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise ApiError("ไม่พบคำขอ", 404)
            is_requester = user["id"] == row["requester_id"]
            is_provider = user["id"] == row["provider_id"]
            is_admin = user["role"] == "admin"
            if action in ("accept", "decline"):
                if not is_provider or row["status"] != "pending":
                    raise ApiError("คำขอนี้ไม่อยู่ในสถานะที่ตอบรับได้", 409)
                if action == "accept":
                    credits = credit_totals(db, row["provider_id"])
                    if not credits.get("unlimited") and credits["balance"] + row["duration_minutes"] > CREDIT_CAP:
                        raise ApiError("เครดิตของผู้สอนเต็มแล้ว ไม่สามารถรับเครดิตเพิ่มจากกิจกรรมนี้ได้", 409)
                    scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
                    if has_schedule_conflict(db, (row["requester_id"], row["provider_id"]), scheduled,
                                             row["duration_minutes"], request_id):
                        raise ApiError("ช่วงเวลานี้ชนกับนัดหมายอื่นแล้ว กรุณาปฏิเสธคำขอนี้และนัดเวลาใหม่", 409)
                    db.execute("UPDATE requests SET status='accepted',updated_at=? WHERE id=?", (iso_now(), request_id))
                    add_notification(db, row["requester_id"], "accepted", "ผู้สอนตอบรับคำขอแล้ว",
                                     "เปิดปฏิทินเพื่อตรวจสอบวันและเวลานัดหมาย", f"request:{request_id}:accepted:{row['requester_id']}", request_id)
                else:
                    db.execute("UPDATE requests SET status='rejected',updated_at=? WHERE id=?", (iso_now(), request_id))
                    add_notification(db, row["requester_id"], "rejected", "ผู้สอนไม่สะดวกรับคำขอนี้",
                                     "เครดิตที่กันไว้ถูกคืนแล้ว คุณเลือกทักษะหรือเวลานัดหมายใหม่ได้", f"request:{request_id}:rejected:{row['requester_id']}", request_id)
            elif action == "cancel":
                if not (is_requester or is_provider) or row["status"] not in ("pending", "accepted", "awaiting_confirmation"):
                    raise ApiError("คำขอนี้ไม่สามารถยกเลิกได้", 409)
                db.execute("UPDATE requests SET status='cancelled',updated_at=? WHERE id=?", (iso_now(), request_id))
                other = row["provider_id"] if is_requester else row["requester_id"]
                add_notification(db, other, "cancelled", "มีการยกเลิกนัดหมาย",
                                 f"กิจกรรม {request_id} ถูกยกเลิก", f"request:{request_id}:cancelled:{other}", request_id)
            elif action == "confirm":
                if not (is_requester or is_provider) or row["status"] not in ("accepted", "awaiting_confirmation"):
                    raise ApiError("ยืนยันกิจกรรมได้หลังผู้สอนตอบรับคำขอแล้ว", 409)
                column = "requester_confirmed" if is_requester else "provider_confirmed"
                db.execute(f"UPDATE requests SET {column}=1,status='awaiting_confirmation',updated_at=? WHERE id=?", (iso_now(), request_id))
                updated = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
                if updated["requester_confirmed"] and updated["provider_confirmed"]:
                    settle_request(db, updated)
                other = row["provider_id"] if is_requester else row["requester_id"]
                title = "กิจกรรมเสร็จสมบูรณ์" if updated["requester_confirmed"] and updated["provider_confirmed"] else "อีกฝ่ายยืนยันกิจกรรมแล้ว"
                add_notification(db, other, "confirmed", title,
                                 "ตรวจสอบสถานะกิจกรรมและเครดิตเวลาได้ในหน้ากิจกรรม", f"request:{request_id}:confirm:{other}:{updated['requester_confirmed']}:{updated['provider_confirmed']}", request_id)
            elif action == "dispute":
                if not (is_requester or is_provider) or row["status"] not in ("accepted", "awaiting_confirmation"):
                    raise ApiError("แจ้งปัญหาได้ระหว่างรอยืนยันกิจกรรม", 409)
                note = valid_text(body.get("note"), "รายละเอียดปัญหา", 500, 8)
                db.execute("UPDATE requests SET status='disputed',dispute_note=?,updated_at=? WHERE id=?", (note, iso_now(), request_id))
                other = row["provider_id"] if is_requester else row["requester_id"]
                add_notification(db, other, "dispute", "มีการแจ้งปัญหากิจกรรม",
                                 "ผู้ดูแลจะตรวจสอบข้อมูลจากทั้งสองฝ่าย", f"request:{request_id}:dispute:{other}", request_id)
            elif action == "resolve":
                if not is_admin or row["status"] != "disputed":
                    raise ApiError("เฉพาะผู้ดูแลที่จัดการกรณีพิพาทได้", 403)
                outcome = body.get("outcome")
                if outcome == "complete":
                    settle_request(db, row, admin_override=True)
                    record_admin_audit(db, user["id"], "resolve_complete", "request", request_id,
                                       f"ปิดกรณีคำขอ #{request_id}: ยืนยันว่ากิจกรรมเสร็จสิ้น")
                elif outcome == "cancel":
                    db.execute("UPDATE requests SET status='cancelled',updated_at=? WHERE id=?", (iso_now(), request_id))
                    record_admin_audit(db, user["id"], "resolve_cancel", "request", request_id,
                                       f"ปิดกรณีคำขอ #{request_id}: ยกเลิกและคืนเครดิตที่กันไว้")
                else:
                    raise ApiError("เลือกผลการตรวจสอบไม่ถูกต้อง")
            elif action == "no-show":
                if not is_provider or row["status"] != "accepted":
                    raise ApiError("ผู้สอนเป็นผู้รายงานกรณีไม่มาตามนัดได้", 409)
                scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
                if scheduled + timedelta(minutes=row["duration_minutes"]) > now_utc():
                    raise ApiError("รายงานไม่มาตามนัดได้หลังเวลาจบกิจกรรม")
                db.execute("UPDATE requests SET status='cancelled_no_show',updated_at=? WHERE id=?", (iso_now(), request_id))
                add_notification(db, row["requester_id"], "no_show", "มีรายงานว่าไม่มาตามนัด",
                                 "คุณสามารถติดต่อผู้ดูแลผ่านกิจกรรมเพื่อชี้แจงข้อมูล", f"request:{request_id}:no-show:{row['requester_id']}", request_id)
                learner_credits = credit_totals(db, row["requester_id"])
                teacher_credits = credit_totals(db, row["provider_id"])
                penalty = min(30, learner_credits["available"], teacher_credits["capacity_remaining"])
                if penalty > 0:
                    if not learner_credits.get("unlimited"):
                        post_ledger(db, row["requester_id"], -penalty, "no_show_penalty", "หักเครดิตกรณีไม่มาตามนัด", request_id)
                    if not teacher_credits.get("unlimited"):
                        post_ledger(db, row["provider_id"], penalty, "no_show_compensation", "ได้รับชดเชยกรณีผู้เรียนไม่มาตามนัด", request_id)
            elif action == "review":
                if is_provider and not is_requester:
                    raise ApiError("ผู้สอนไม่ต้องรีวิวผู้เรียน ผู้เรียนเป็นผู้รีวิวผู้สอนหลังกิจกรรมเสร็จสิ้น", 403)
                if not is_requester or row["status"] != "completed":
                    raise ApiError("รีวิวได้หลังทั้งสองฝ่ายยืนยันกิจกรรมเสร็จสิ้น")
                try:
                    rating = int(body.get("rating"))
                except (TypeError, ValueError):
                    raise ApiError("เลือกคะแนนรีวิว 1–5 ดาว")
                if rating < 1 or rating > 5:
                    raise ApiError("เลือกคะแนนรีวิว 1–5 ดาว")
                comment = valid_text(body.get("comment", ""), "ความคิดเห็น", 400)
                reviewee = row["provider_id"]
                db.execute("INSERT INTO reviews(request_id,reviewer_id,reviewee_id,rating,comment,created_at) VALUES(?,?,?,?,?,?)",
                           (request_id, user["id"], reviewee, rating, comment, iso_now()))
            else:
                self.send_json(404, {"error": "ไม่รู้จักการดำเนินการนี้"})
                return
        self.send_json(200, {"ok": True})

    def admin_dashboard(self, query: dict | None = None) -> None:
        user = self.current_user()
        if user["role"] != "admin":
            raise ApiError("หน้านี้สำหรับผู้ดูแลระบบ", 403)
        query = query or {}
        member_search = query.get("members", [""])[0].strip()[:80]
        offer_search = query.get("offers", [""])[0].strip()[:80]
        with db_connect() as db:
            rows = db.execute("SELECT * FROM requests WHERE status='disputed' ORDER BY updated_at ASC").fetchall()
            disputes = [request_row(db, row, user["id"]) for row in rows]
            member_count = db.execute("SELECT COUNT(*) FROM users WHERE role='member'").fetchone()[0]
            offer_count = db.execute("SELECT COUNT(*) FROM offers o JOIN users u ON u.id=o.user_id WHERE o.active=1 AND u.is_active=1").fetchone()[0]
            completed_count = db.execute("SELECT COUNT(*) FROM requests WHERE status='completed'").fetchone()[0]
            report_rows = db.execute("""SELECT r.id,r.target_type,r.target_id,r.request_id,r.reason,r.details,r.status,r.created_at,
                    reporter.full_name AS reporter_name,
                    CASE WHEN r.target_type='user' THEN target.full_name ELSE offering.title END AS target_name,
                    CASE WHEN r.target_type='user' THEN target.id ELSE offering.user_id END AS target_user_id
                FROM reports r JOIN users reporter ON reporter.id=r.reporter_id
                LEFT JOIN users target ON r.target_type='user' AND target.id=r.target_id
                LEFT JOIN offers offering ON r.target_type='offer' AND offering.id=r.target_id
                WHERE r.status IN ('open','reviewed') ORDER BY r.created_at ASC LIMIT 200""").fetchall()
            members_sql = "SELECT id,email,full_name,faculty,created_at,is_active FROM users WHERE role='member'"
            members_args: tuple = ()
            if member_search:
                members_sql += " AND (full_name LIKE ? COLLATE NOCASE OR email LIKE ? COLLATE NOCASE OR faculty LIKE ? COLLATE NOCASE)"
                pattern = f"%{member_search}%"
                members_args = (pattern, pattern, pattern)
            members_sql += " ORDER BY is_active DESC,full_name COLLATE NOCASE LIMIT 500"
            members = db.execute(members_sql, members_args).fetchall()
            offers_sql = "SELECT o.id,o.title,o.category,o.active,o.user_id,u.full_name,u.email,u.is_active AS owner_active FROM offers o JOIN users u ON u.id=o.user_id WHERE u.role='member' AND o.deleted_at=''"
            offers_args: tuple = ()
            if offer_search:
                offers_sql += " AND (o.title LIKE ? COLLATE NOCASE OR o.category LIKE ? COLLATE NOCASE OR u.full_name LIKE ? COLLATE NOCASE OR u.email LIKE ? COLLATE NOCASE)"
                pattern = f"%{offer_search}%"
                offers_args = (pattern, pattern, pattern, pattern)
            offers_sql += " ORDER BY o.active DESC,o.created_at DESC LIMIT 500"
            offers = db.execute(offers_sql, offers_args).fetchall()
            audit_rows = db.execute("SELECT a.id,a.action,a.target_type,a.target_id,a.summary,a.created_at,u.full_name AS admin_name FROM admin_audit a JOIN users u ON u.id=a.admin_id ORDER BY a.id DESC LIMIT 50").fetchall()
        self.send_json(200, {"disputes": disputes,
            "members": [{"id": x["id"], "email": x["email"], "full_name": x["full_name"], "faculty": x["faculty"], "created_at": x["created_at"], "is_active": bool(x["is_active"])} for x in members],
            "offers": [{"id": x["id"], "title": x["title"], "category": x["category"], "active": bool(x["active"]), "user_id": x["user_id"], "provider_name": x["full_name"], "provider_email": x["email"], "owner_active": bool(x["owner_active"])} for x in offers],
            "audit": [{"id": x["id"], "action": x["action"], "target_type": x["target_type"], "target_id": x["target_id"], "summary": x["summary"], "created_at": x["created_at"], "admin_name": x["admin_name"]} for x in audit_rows],
            "reports": [{"id": x["id"], "target_type": x["target_type"], "target_id": x["target_id"],
                         "request_id": x["request_id"], "reason": x["reason"], "details": x["details"],
                         "status": x["status"], "created_at": x["created_at"], "reporter_name": x["reporter_name"],
                         "target_name": x["target_name"] or "รายการที่ถูกลบ", "target_user_id": x["target_user_id"]} for x in report_rows],
            "stats": {"members": member_count, "offers": offer_count, "completed": completed_count,
                      "disputes": len(disputes), "reports": len(report_rows)}})

    def admin_delete_offer(self, path: str) -> None:
        user = self.current_user()
        if user["role"] != "admin":
            raise ApiError("หน้านี้สำหรับผู้ดูแลระบบ", 403)
        try:
            offer_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            raise ApiError("ไม่พบประกาศ", 404)
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            offer = db.execute("SELECT title FROM offers WHERE id=? AND deleted_at=''", (offer_id,)).fetchone()
            if not offer:
                raise ApiError("ไม่พบประกาศ", 404)
            db.execute("UPDATE offers SET active=0,deleted_at=? WHERE id=?", (iso_now(), offer_id))
            record_admin_audit(db, user["id"], "offer_delete", "offer", offer_id, f"ลบประกาศทักษะ {offer['title']}")
        self.send_json(200, {"ok": True})

    def admin_action(self, path: str, body: dict) -> None:
        user = self.current_user()
        if user["role"] != "admin":
            raise ApiError("หน้านี้สำหรับผู้ดูแลระบบ", 403)
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0:2] != ["api", "admin"]:
            raise ApiError("ไม่พบรายการ", 404)
        resource = parts[2]
        try:
            item_id = int(parts[3])
        except ValueError:
            raise ApiError("ไม่พบรายการ", 404)
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if resource == "reports":
                status = body.get("status")
                if status not in ("reviewed", "resolved", "dismissed"):
                    raise ApiError("เลือกสถานะรายงานไม่ถูกต้อง")
                report = db.execute("SELECT id,target_type,target_id,status FROM reports WHERE id=?", (item_id,)).fetchone()
                if not report:
                    raise ApiError("ไม่พบรายงานนี้", 404)
                resolution = valid_text(body.get("resolution", ""), "บันทึกการตรวจสอบ", 300)
                db.execute("UPDATE reports SET status=?,admin_id=?,resolution=?,updated_at=? WHERE id=?",
                           (status, user["id"], resolution, iso_now(), item_id))
                record_admin_audit(db, user["id"], f"report_{status}", "report", item_id,
                                   f"อัปเดตรายงาน #{item_id} เป็น {status}")
            elif resource in ("users", "offers"):
                active = body.get("active")
                if not isinstance(active, bool):
                    raise ApiError("สถานะที่เลือกไม่ถูกต้อง")
                if resource == "users":
                    member = db.execute("SELECT role,full_name,is_active FROM users WHERE id=?", (item_id,)).fetchone()
                    if not member or member["role"] != "member":
                        raise ApiError("ไม่พบสมาชิกที่จัดการได้", 404)
                    db.execute("UPDATE users SET is_active=? WHERE id=?", (int(active), item_id))
                    if bool(member["is_active"]) != active:
                        label = "เปิดใช้งาน" if active else "ระงับ"
                        record_admin_audit(db, user["id"], "user_activate" if active else "user_suspend", "user", item_id,
                                           f"{label}บัญชีสมาชิก {member['full_name']}")
                    if not active:
                        db.execute("DELETE FROM sessions WHERE user_id=?", (item_id,))
                        cancellable = tuple(status for status in ACTIVE_HOLD_STATES if status != "disputed")
                        marks = ",".join("?" for _ in cancellable)
                        db.execute(f"UPDATE requests SET status='cancelled',updated_at=? WHERE (requester_id=? OR provider_id=?) AND status IN ({marks})",
                                   (iso_now(), item_id, item_id, *cancellable))
                else:
                    offer = db.execute("SELECT o.id,o.title,o.active,u.is_active FROM offers o JOIN users u ON u.id=o.user_id WHERE o.id=? AND u.role='member'", (item_id,)).fetchone()
                    if not offer:
                        raise ApiError("ไม่พบประกาศ", 404)
                    if active and not offer["is_active"]:
                        raise ApiError("เปิดประกาศไม่ได้ขณะที่บัญชีผู้สอนถูกระงับ", 409)
                    db.execute("UPDATE offers SET active=? WHERE id=?", (int(active), item_id))
                    if bool(offer["active"]) != active:
                        label = "เปิด" if active else "ปิด"
                        record_admin_audit(db, user["id"], "offer_activate" if active else "offer_close", "offer", item_id,
                                           f"{label}ประกาศทักษะ {offer['title']}")
            else:
                raise ApiError("ไม่รู้จักรายการที่เลือก", 404)
        self.send_json(200, {"ok": True})

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PATCH(self) -> None:
        self.dispatch("PATCH")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")


def bootstrap_admin() -> None:
    """สร้างผู้ดูแลจากตัวแปร CHRONOSWAP_ADMIN_EMAIL / CHRONOSWAP_ADMIN_PASSWORD (สำหรับโฮสต์ที่ไม่มี shell)
    สร้างเฉพาะเมื่อยังไม่มีบัญชีอีเมลนี้ ไม่เขียนทับรหัสผ่านเดิม"""
    if not BOOTSTRAP_ADMIN_EMAIL and not BOOTSTRAP_ADMIN_PASSWORD:
        return
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", BOOTSTRAP_ADMIN_EMAIL) or len(BOOTSTRAP_ADMIN_PASSWORD) < 12:
        print("ข้าม CHRONOSWAP_ADMIN_EMAIL/PASSWORD: อีเมลไม่ถูกต้องหรือรหัสผ่านสั้นกว่า 12 ตัวอักษร")
        return
    with db_connect() as db:
        if db.execute("SELECT 1 FROM users WHERE email=?", (BOOTSTRAP_ADMIN_EMAIL,)).fetchone():
            return
        db.execute("INSERT INTO users(email,password_hash,full_name,role,email_verified,created_at) VALUES(?,?,?,'admin',1,?)",
                   (BOOTSTRAP_ADMIN_EMAIL, password_hash(BOOTSTRAP_ADMIN_PASSWORD), "ผู้ดูแลระบบ", iso_now()))
    print("สร้างบัญชีผู้ดูแลจากตัวแปรสภาพแวดล้อมแล้ว")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_db()
    bootstrap_admin()
    if len(sys.argv) > 1 and sys.argv[1] == "--create-admin":
        email = input("อีเมลผู้ดูแลระบบ: ").strip().lower()
        name = input("ชื่อที่แสดง: ").strip()
        password = getpass("รหัสผ่าน (อย่างน้อย 12 ตัวอักษร): ")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(name) < 2 or len(password) < 12:
            raise SystemExit("ข้อมูลไม่ถูกต้อง: ตรวจอีเมล ชื่อ และความยาวรหัสผ่าน")
        with db_connect() as db:
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise SystemExit("อีเมลนี้มีบัญชีอยู่แล้ว")
            db.execute("INSERT INTO users(email,password_hash,full_name,role,email_verified,created_at) VALUES(?,?,?,'admin',1,?)",
                       (email, password_hash(password), name, iso_now()))
        print("สร้างบัญชีผู้ดูแลแล้ว")
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--change-password":
        email = sys.argv[2].strip().lower()
        password = getpass("รหัสผ่านใหม่ (อย่างน้อย 12 ตัวอักษร): ")
        if len(password) < 12:
            raise SystemExit("รหัสผ่านต้องมีอย่างน้อย 12 ตัวอักษร")
        with db_connect() as db:
            cursor = db.execute("UPDATE users SET password_hash=? WHERE email=?", (password_hash(password), email))
            if cursor.rowcount == 0:
                raise SystemExit("ไม่พบบัญชีนี้")
            db.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE email=?)", (email,))
        print("เปลี่ยนรหัสผ่านแล้ว; เซสชันเดิมถูกออกจากระบบ")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--reset-accounts":
        if input("ลบบัญชีและข้อมูลทั้งหมด แล้วสร้างบัญชีตัวอย่างใหม่? พิมพ์ YES เพื่อยืนยัน: ").strip() != "YES":
            raise SystemExit("ยกเลิกแล้ว ไม่มีการเปลี่ยนแปลง")
        reset_all_accounts()
        print("ลบบัญชีเก่าทั้งหมดและสร้างบัญชีตัวอย่างชุดใหม่แล้ว")
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("ChronoSwap commands: python server.py [--create-admin | --change-password EMAIL | --reset-accounts]")
        print("Set CHRONOSWAP_SEED_DEMO=0 before first launch to skip sample accounts.")
        return
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 56)
    print("ChronoSwap พร้อมใช้งาน")
    print(f"เปิดเว็บ: http://{HOST}:{PORT}")
    print(f"ฐานข้อมูล: {DB_PATH}")
    print("หยุดเซิร์ฟเวอร์ด้วย Ctrl+C")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nปิด ChronoSwap แล้ว")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
