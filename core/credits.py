"""
credits.py — ระบบ API key + เครดิต + rate-limit (เก็บใน SQLite)

ทำให้ TTS API กลายเป็น "บริการ" แบบ Lumina:
  - แต่ละ API key มียอดเครดิตของตัวเอง
  - คิดเครดิตตามวินาทีเสียงที่สร้าง (ปรับสูตรได้)
  - เครดิตหมด → 402 Payment Required
  - rate-limit ต่อ key (กันยิงถี่)

เปิดใช้เมื่อ set env TTS_CREDITS_DB=/path/to/credits.db
ถ้าไม่ตั้ง → ระบบ fallback ไปใช้ TTS_API_KEY เดี่ยว (unlimited) เหมือนเดิม

ข้อจำกัด: rate-limit เก็บในหน่วยความจำของ process → ถ้ารันหลาย worker/หลาย pod
ควรย้ายไป Redis. ส่วนเครดิต (SQLite) ปลอดภัยข้าม process เพราะเขียนแบบ transaction
แต่บนหลาย pod ควรเปลี่ยนเป็น Postgres. (ดีพอสำหรับ single-pod / เริ่มต้น)
"""
import os
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque


class CreditError(Exception):
    """ยอดเครดิตไม่พอ"""


class RateLimitError(Exception):
    """ยิงถี่เกิน rate-limit"""


class CreditStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        # rate-limit: key -> deque[timestamps] (sliding window 60 วิ)
        self._hits = defaultdict(deque)
        self._rl_lock = threading.Lock()
        self._init_db()

    # ── connection ต่อ thread (sqlite ห้ามแชร์ connection ข้าม thread) ──
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def _init_db(self):
        self._conn().executescript(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key         TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                credits     REAL NOT NULL DEFAULT 0,
                unlimited   INTEGER NOT NULL DEFAULT 0,
                rate_per_min INTEGER NOT NULL DEFAULT 60,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  REAL NOT NULL,
                total_used  REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS usage_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                key       TEXT NOT NULL,
                ts        REAL NOT NULL,
                seconds   REAL NOT NULL,
                cost      REAL NOT NULL,
                endpoint  TEXT NOT NULL DEFAULT ''
            );
            """
        )

    # ── จัดการ key (เรียกจาก admin/CLI) ──
    def create_key(self, name="", credits=0.0, unlimited=False, rate_per_min=60) -> str:
        key = "sk_" + secrets.token_urlsafe(32)
        self._conn().execute(
            "INSERT INTO api_keys(key,name,credits,unlimited,rate_per_min,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (key, name, float(credits), int(unlimited), int(rate_per_min), time.time()),
        )
        return key

    def add_credits(self, key: str, amount: float):
        self._conn().execute(
            "UPDATE api_keys SET credits = credits + ? WHERE key = ?", (float(amount), key)
        )

    def get_key(self, key: str):
        row = self._conn().execute(
            "SELECT * FROM api_keys WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def list_keys(self):
        rows = self._conn().execute(
            "SELECT key,name,credits,unlimited,rate_per_min,active,total_used FROM api_keys "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── ตรวจสิทธิ์ก่อน generate ──
    def authorize(self, key: str):
        """คืน dict ของ key ถ้าใช้ได้; ไม่งั้น raise. ยังไม่หักเครดิต (หักหลัง generate เสร็จ)"""
        rec = self.get_key(key)
        if rec is None or not rec["active"]:
            raise PermissionError("API key ไม่ถูกต้องหรือถูกปิดใช้งาน")
        if not rec["unlimited"] and rec["credits"] <= 0:
            raise CreditError("เครดิตหมด — กรุณาเติมเครดิต")
        self._check_rate(key, rec["rate_per_min"])
        return rec

    def _check_rate(self, key: str, rate_per_min: int):
        if rate_per_min <= 0:
            return
        now = time.time()
        with self._rl_lock:
            dq = self._hits[key]
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) >= rate_per_min:
                raise RateLimitError(f"ยิงถี่เกิน {rate_per_min} ครั้ง/นาที")
            dq.append(now)

    # ── หักเครดิตหลัง generate เสร็จ (คิดตามวินาทีเสียง) ──
    def charge(self, key: str, seconds: float, cost_per_second: float, endpoint=""):
        rec = self.get_key(key)
        if rec is None:
            return 0.0
        cost = 0.0 if rec["unlimited"] else round(seconds * cost_per_second, 4)
        conn = self._conn()
        conn.execute("BEGIN")
        try:
            if cost:
                conn.execute(
                    "UPDATE api_keys SET credits = credits - ?, total_used = total_used + ? "
                    "WHERE key = ?",
                    (cost, cost, key),
                )
            conn.execute(
                "INSERT INTO usage_log(key,ts,seconds,cost,endpoint) VALUES(?,?,?,?,?)",
                (key, time.time(), round(seconds, 3), cost, endpoint),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return cost
