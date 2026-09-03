"""
gemini_translit.py — ใช้ Gemini API แปลงคำอังกฤษที่ไม่มีใน text_utils.ENGLISH_TO_THAI /
BUNDLED_EN_TO_THAI ให้เป็นคำทับศัพท์ไทย โดย cache ผลไว้ในไฟล์
เรียก Gemini แค่ "ครั้งแรกที่เจอคำนั้น" เท่านั้น — ครั้งต่อไปอ่านจาก cache

ปิดอยู่โดยดีฟอลต์ (ไม่กระทบพฤติกรรมเดิม) เปิดใช้งานด้วย:
  export TTS_GEMINI_TRANSLITERATE=1
  export GEMINI_API_KEY=<your key>   # สร้างได้ที่ https://aistudio.google.com/apikey

env ที่ใช้ได้:
  TTS_GEMINI_TRANSLITERATE=1   เปิดใช้ fallback นี้ (ดีฟอลต์ปิด)
  GEMINI_API_KEY               (จำเป็นถ้าเปิดใช้)
  GEMINI_MODEL      (default gemini-2.5-flash-lite — เร็ว/ถูก พอสำหรับงานทับศัพท์คำเดียว)
  GEMINI_TIMEOUT    (default 15 วินาที)
  TRANSLIT_CACHE    (default translit_cache.json)
"""
import json
import os
import re
import threading

_ENABLED = os.environ.get("TTS_GEMINI_TRANSLITERATE", "0") == "1"
_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
_CACHE_PATH = os.environ.get("TRANSLIT_CACHE", "translit_cache.json")
_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "15"))

_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}:generateContent"

_THAI_ONLY_RE = re.compile(r"^[฀-๿\s]+$")
_PROMPT = (
    'แปลงคำภาษาอังกฤษต่อไปนี้เป็น "คำทับศัพท์ภาษาไทย" แบบที่คนไทยอ่านออกเสียงกันจริง '
    "(ทับศัพท์ตามเสียงอ่าน ไม่ใช่แปลความหมาย — เช่น Google -> กูเกิล, Xiaomi -> เสี่ยวมี่)\n"
    "ตอบกลับเฉพาะคำทับศัพท์ภาษาไทยคำเดียว ห้ามอธิบาย ห้ามใส่วงเล็บ ห้ามมีคำอังกฤษปน\n\n"
    "คำ: {word}\nคำทับศัพท์:"
)

_lock = threading.Lock()
_cache = {}
if os.path.exists(_CACHE_PATH):
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    except Exception:
        _cache = {}


def _save_cache():
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[gemini_translit] เขียน cache ไม่สำเร็จ: {e}")


def gemini_transliterate(word: str):
    """
    คืนคำทับศัพท์ไทยจาก Gemini API (ผ่าน cache), หรือ None ถ้า:
    ปิดใช้งานอยู่ / ไม่มี GEMINI_API_KEY / เรียกไม่สำเร็จ / คำตอบดูไม่สมเหตุสมผล
    None แปลว่า "ยังไม่รู้" — ผู้เรียก (text_utils.transliterate_english) จะปล่อยคำเดิมผ่าน
    ไปให้ mixed_language จัดการแบบเดิม ไม่ทำให้แย่ลงกว่าก่อนมี fallback นี้
    """
    if not _ENABLED:
        return None
    if not _API_KEY:
        print("[gemini_translit] เปิด TTS_GEMINI_TRANSLITERATE=1 แต่ไม่ได้ตั้ง GEMINI_API_KEY — ข้าม")
        return None

    key = word.lower()
    with _lock:
        if key in _cache:
            return _cache[key] or None

    out = None
    try:
        import requests  # import ตอนใช้จริงเท่านั้น — ไม่ให้กระทบ path ที่ไม่เปิดใช้ฟีเจอร์นี้
        r = requests.post(
            _API_URL, timeout=_TIMEOUT, params={"key": _API_KEY},
            json={
                "contents": [{"parts": [{"text": _PROMPT.format(word=word)}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 20},
            },
        )
        r.raise_for_status()
        data = r.json()
        candidate = (data["candidates"][0]["content"]["parts"][0]["text"]
                     .strip().splitlines()[0].strip(' "\'.*'))
        if candidate and _THAI_ONLY_RE.match(candidate):
            out = candidate
        else:
            print(f"[gemini_translit] คำตอบไม่ใช่คำทับศัพท์ไทยล้วนสำหรับ '{word}': {candidate!r} — ข้าม")
    except Exception as e:
        print(f"[gemini_translit] เรียก Gemini ไม่สำเร็จสำหรับ '{word}': {e}")

    with _lock:
        _cache[key] = out or ""  # cache ผลว่างด้วย กันยิงซ้ำคำที่แปลไม่ได้ทุก request
        _save_cache()
    return out
