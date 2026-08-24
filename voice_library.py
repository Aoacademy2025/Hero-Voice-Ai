"""
voice_library.py — คลังเสียงโคลนถาวร (custom voice) ต่อผู้ใช้

ต่างจาก /clone (ad-hoc ใช้ครั้งเดียว): ที่นี่ผู้ใช้อัปโหลดไฟล์เสียงอ้างอิง 1 ครั้ง
เราเก็บไฟล์ + metadata ไว้ แล้วได้ voice_id กลับมา → เอาไปใช้ซ้ำใน /tts ได้เหมือนเสียงสต็อก
(แบบเดียวกับ "โคลนเสียงแล้วเก็บไว้" ของ Lumina)

- เก็บ metadata ใน SQLite, เก็บไฟล์ wav ใน CUSTOM_VOICES_DIR/{voice_id}.wav
- เป็นของเจ้าของ (owner = api key หรือ 'public') — คนอื่นมองไม่เห็น/ลบไม่ได้
- prompt (encode ของ ref) ไม่ได้เก็บที่นี่ — server ทำ LRU cache ในแรม แล้ว encode
  จากไฟล์ wav เมื่อใช้ครั้งแรก (ดู server.OmniVoiceEngine)
"""
import os
import secrets
import sqlite3
import threading
import time


class VoiceLibrary:
    def __init__(self, db_path: str, audio_dir: str):
        self.db_path = db_path
        self.audio_dir = audio_dir
        os.makedirs(audio_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._local = threading.local()
        self._init_db()

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
            CREATE TABLE IF NOT EXISTS custom_voices (
                voice_id   TEXT PRIMARY KEY,
                owner      TEXT NOT NULL DEFAULT 'public',
                name       TEXT NOT NULL DEFAULT '',
                ref_text   TEXT NOT NULL DEFAULT '',
                filename   TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_owner ON custom_voices(owner);
            """
        )

    def audio_path(self, voice_id: str) -> str:
        return os.path.join(self.audio_dir, f"{voice_id}.wav")

    def create(self, owner: str, name: str, ref_text: str, wav_bytes: bytes) -> dict:
        voice_id = "cv_" + secrets.token_urlsafe(12)
        path = self.audio_path(voice_id)
        with open(path, "wb") as f:
            f.write(wav_bytes)
        self._conn().execute(
            "INSERT INTO custom_voices(voice_id,owner,name,ref_text,filename,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (voice_id, owner, name, ref_text, os.path.basename(path), time.time()),
        )
        return self.get(voice_id)

    def get(self, voice_id: str):
        row = self._conn().execute(
            "SELECT * FROM custom_voices WHERE voice_id = ?", (voice_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, owner: str):
        """คืนเสียงของ owner นี้ + เสียง public"""
        rows = self._conn().execute(
            "SELECT voice_id,owner,name,ref_text,created_at FROM custom_voices "
            "WHERE owner = ? OR owner = 'public' ORDER BY created_at DESC",
            (owner,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, voice_id: str, owner: str) -> bool:
        """ลบได้เฉพาะเจ้าของ — คืน True ถ้าลบจริง"""
        rec = self.get(voice_id)
        if rec is None or rec["owner"] != owner:
            return False
        self._conn().execute("DELETE FROM custom_voices WHERE voice_id = ?", (voice_id,))
        try:
            os.remove(self.audio_path(voice_id))
        except OSError:
            pass
        return True

    def can_use(self, rec: dict, owner: str) -> bool:
        """เจ้าของ หรือ เสียง public เท่านั้นที่ใช้ได้"""
        return rec is not None and (rec["owner"] == owner or rec["owner"] == "public")
