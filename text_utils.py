"""
text_utils.py — ตัดข้อความยาวเป็น "ก้อนประโยค" สำหรับ streaming

OmniVoice generate ทีละก้อน ยิ่งก้อนสั้น latency ก้อนแรกยิ่งต่ำ (ผู้ใช้ได้ยินเสียงเร็ว)
แต่สั้นเกินไปเสียงจะขาดเป็นท่อน — จึงตัดตามขอบเขตประโยค แล้ว merge ก้อนจิ๋วเข้าด้วยกัน

ภาษาไทยไม่มีช่องว่างระหว่างคำ แต่ใช้:
  - ขึ้นบรรทัดใหม่ = จบย่อหน้า
  - เว้นวรรค " " = จบวลี/ประโยค (คนไทยเว้นวรรคแทน period)
  - เครื่องหมาย . ! ? … ฯ
เราตัดตามสัญญาณเหล่านี้ แล้วรวมให้แต่ละก้อนมีความยาวพอเหมาะ (min..max ตัวอักษร)
"""
import csv
import os
import re

from pythainlp.util import bahttext, num_to_thaiword

# แยกหลังเครื่องหมายจบประโยค (เก็บเครื่องหมายไว้กับก้อนเดิม) หรือขึ้นบรรทัดใหม่
_SENT_SPLIT = re.compile(r"(?<=[\.\!\?…ฯ])\s+|[\r\n]+")


def split_sentences(text: str):
    """ตัดข้อความดิบเป็นรายการประโยค (ยังไม่รวมก้อน)"""
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


def _hard_wrap(s: str, max_chars: int):
    """ประโยคเดี่ยวที่ยาวเกิน max — ตัดย่อยตามเว้นวรรค (ไทย) แล้วค่อยตัดดิบถ้าจำเป็น"""
    if len(s) <= max_chars:
        return [s]
    chunks, cur = [], ""
    for token in re.split(r"(\s+)", s):  # เก็บช่องว่างไว้เพื่อประกอบกลับ
        if len(cur) + len(token) > max_chars and cur.strip():
            chunks.append(cur.strip())
            cur = ""
        cur += token
        # token เดียวยาวเกิน (คำไทยติดกันยาว) → ตัดดิบ
        while len(cur) > max_chars:
            chunks.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


# ── ทับศัพท์คำอังกฤษที่พบบ่อย → เขียนแบบไทยที่คนไทยอ่านกันจริง ──────────
# แก้ปัญหาเสียงเพี้ยน/สะดุดตอนสลับภาษากลางประโยค (ดู mixed_language ใน server.py)
# เจอคำไหนโมเดลอ่านเพี้ยน เพิ่มคำนั้นลงดิกนี้ได้เลย (key ต้องเป็นตัวพิมพ์เล็ก)
ENGLISH_TO_THAI = {
    # เทคโนโลยี/แพลตฟอร์ม
    "ai": "เอไอ", "ml": "เอ็มแอล", "api": "เอพีไอ", "app": "แอป", "apps": "แอปส์",
    "google": "กูเกิล", "facebook": "เฟซบุ๊ก", "youtube": "ยูทูบ", "instagram": "อินสตาแกรม",
    "twitter": "ทวิตเตอร์", "tiktok": "ติ๊กต่อก", "line": "ไลน์", "netflix": "เน็ตฟลิกซ์",
    "spotify": "สปอติฟาย", "iphone": "ไอโฟน", "ipad": "ไอแพด", "android": "แอนดรอยด์",
    "windows": "วินโดวส์", "wifi": "ไวไฟ", "bluetooth": "บลูทูธ", "email": "อีเมล",
    "internet": "อินเทอร์เน็ต", "website": "เว็บไซต์", "online": "ออนไลน์", "offline": "ออฟไลน์",
    "server": "เซิร์ฟเวอร์", "download": "ดาวน์โหลด", "upload": "อัปโหลด", "password": "รหัสผ่าน",
    "username": "ยูสเซอร์เนม", "login": "ล็อกอิน", "logout": "ล็อกเอาต์", "notebook": "โน้ตบุ๊ก",
    "computer": "คอมพิวเตอร์", "laptop": "แล็ปท็อป", "smartphone": "สมาร์ตโฟน",
    "software": "ซอฟต์แวร์", "hardware": "ฮาร์ดแวร์", "update": "อัปเดต", "version": "เวอร์ชัน",
    "drive": "ไดรฟ์", "cloud": "คลาวด์", "file": "ไฟล์", "folder": "โฟลเดอร์",
    # ธุรกิจ/การตลาด
    "marketing": "มาร์เก็ตติ้ง", "brand": "แบรนด์", "content": "คอนเทนต์",
    "influencer": "อินฟลูเอนเซอร์", "live": "ไลฟ์", "streaming": "สตรีมมิ่ง",
    "podcast": "พอดแคสต์", "startup": "สตาร์ทอัพ", "freelance": "ฟรีแลนซ์",
    "meeting": "มีตติ้ง", "project": "โปรเจกต์", "deadline": "เดดไลน์",
    "feedback": "ฟีดแบ็ก", "presentation": "พรีเซนเทชัน",
    # ทั่วไป
    "ok": "โอเค", "okay": "โอเค", "hi": "ไฮ", "hello": "เฮลโล", "bye": "บาย",
    "sorry": "ซอรี่", "please": "พลีส", "coffee": "กาแฟ",
    "percent": "เปอร์เซ็นต์", "percentage": "เปอร์เซ็นต์",

    # ── ชื่อแบรนด์/บริษัท ──────────────────────────────────────────
    # ดิกวิชาการ (BUNDLED_EN_TO_THAI) ไม่ครอบคลุมกลุ่มนี้ เพราะราชบัณฑิตยสภา
    # ไม่รับรองคำทับศัพท์ชื่อแบรนด์เอกชน — คัดจากคำทับศัพท์ที่สื่อไทยใช้เป็น
    # มาตรฐานจริงในทางปฏิบัติ (ไม่ได้ผ่านการตรวจสอบทางการเหมือนดิกด้านบน
    # ถ้าเจอคำไหนผิด/มีสะกดอื่นที่นิยมกว่า แก้ตรงนี้ได้เลย)
    # โทรศัพท์/แกดเจ็ต
    "xiaomi": "เสี่ยวมี่", "huawei": "หัวเว่ย", "samsung": "ซัมซุง", "oppo": "ออปโป้",
    "vivo": "วีโว่", "realme": "เรียลมี", "nokia": "โนเกีย", "sony": "โซนี่",
    "asus": "เอซุส", "acer": "เอเซอร์", "lenovo": "เลอโนโว", "dell": "เดลล์",
    "snapdragon": "สแนปดรากอน", "canon": "แคนนอน", "nikon": "นิคอน",
    "gopro": "โกโปร", "garmin": "การ์มิน", "xbox": "เอ็กซ์บ็อกซ์",
    "playstation": "เพลย์สเตชัน", "nintendo": "นินเทนโด",
    # แพลตฟอร์ม/บริการออนไลน์
    "microsoft": "ไมโครซอฟท์", "amazon": "อเมซอน", "disney": "ดิสนีย์",
    "whatsapp": "วอตส์แอป", "grab": "แกร็บ", "foodpanda": "ฟู้ดแพนด้า",
    "shopee": "ช้อปปี้", "lazada": "ลาซาด้า", "airbnb": "แอร์บีเอ็นบี", "uber": "อูเบอร์",
    "agoda": "อโกด้า", "zoom": "ซูม", "skype": "สไกป์", "discord": "ดิสคอร์ด",
    "twitch": "ทวิช", "steam": "สตีม", "paypal": "เพย์พาล",
    # แฟชั่น/ค้าปลีก/อาหาร
    "adidas": "อาดิดาส", "nike": "ไนกี้", "puma": "พูม่า", "uniqlo": "ยูนิโคล่",
    "zara": "ซาร่า", "starbucks": "สตาร์บัคส์",
    # ยานยนต์
    "tesla": "เทสลา", "toyota": "โตโยต้า", "honda": "ฮอนด้า", "nissan": "นิสสัน",
    # ฮาร์ดแวร์/ตัวย่อคอมพิวเตอร์ทั่วไป
    "usb": "ยูเอสบี", "hdmi": "เอชดีเอ็มไอ", "ram": "แรม", "rom": "รอม",
    "cpu": "ซีพียู", "gpu": "จีพียู", "ssd": "เอสเอสดี", "oled": "โอเลด",
    "usb-c": "ยูเอสบีซี", "mode": "โหมด", "gen": "เจน", "night": "ไนท์",
    # เอไอ/แชทบอท/TTS — คำที่พบบ่อยเวลาพูดถึงตัวโมเดล/ผลิตภัณฑ์นี้เอง
    "chatgpt": "แชตจีพีที", "gpt": "จีพีที", "claude": "คลอด", "gemini": "เจมิไน",
    "openai": "โอเพนเอไอ", "anthropic": "แอนโทรปิก", "llm": "แอลแอลเอ็ม",
    "chatbot": "แชตบอต", "prompt": "พรอมต์", "text to speech": "เท็กซ์ทูสปีช",
    "text-to-speech": "เท็กซ์ทูสปีช", "tts": "ทีทีเอส", "voice clone": "วอยซ์โคลน",
    "deepfake": "ดีปเฟก", "latency": "เลเทนซี", "real-time": "เรียลไทม์",
    "realtime": "เรียลไทม์", "benchmark": "เบนช์มาร์ก",
}

_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:['\-][A-Za-z]+)*")

_BUNDLED_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "data", "en_th_transliteration.tsv")


def _load_bundled_dicts(path: str):
    """
    โหลดดิกทับศัพท์ไทย-อังกฤษจาก wannaphong/thai-english-transliteration-dictionary
    (Apache-2.0 — https://github.com/wannaphong/thai-english-transliteration-dictionary)
    ~3,800 คำ แยกเป็น 2 ชั้นตามคอลัมน์ check:
      - checked   = check == "True" (ตรวจสอบตามหลักราชบัณฑิตยสภาแล้ว) — เชื่อถือได้สูง
      - unchecked = ที่เหลือ (ยังไม่ยืนยันทางการ แต่ส่วนใหญ่เป็นคำทับศัพท์ที่สื่อไทยใช้จริง
        เช่น "bodyguard"->"บอดี้การ์ด") — ใช้เป็นชั้นสำรองรองจาก checked
    คำนึงมีหลายสะกดต่อคำอังกฤษเดียวในดิกต้นฉบับ → เก็บสะกดแรกที่เจอต่อชั้น
    """
    checked, unchecked = {}, {}
    if not os.path.exists(path):
        return checked, unchecked
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            en = (row.get("en") or "").strip().lower()
            th = (row.get("th") or "").strip()
            if not en or not th:
                continue
            if row.get("check") == "True":
                checked.setdefault(en, th)
            else:
                unchecked.setdefault(en, th)
    return checked, unchecked


# ดิกใหญ่จากภายนอก (ตัวสำรอง) — ดิกคัดเองด้านบน (ENGLISH_TO_THAI) มาก่อนเสมอถ้าคำซ้ำกัน
BUNDLED_EN_TO_THAI, BUNDLED_EN_TO_THAI_UNVERIFIED = _load_bundled_dicts(_BUNDLED_DICT_PATH)


def _build_phrase_re(*dicts):
    """
    สร้าง regex จับ "วลีหลายคำ" ที่มีอยู่ในดิก (เช่น "santa claus", "deep learning")
    เรียงยาว->สั้นกันจับวลีสั้นตัดหน้าวลียาวที่ครอบคลุมกว่า (regex alternation จับตัวแรกที่แมตช์)
    _ENGLISH_WORD_RE ทีละคำอย่างเดียวจับพวกนี้ไม่ได้ (ไม่มีช่องว่างในนิยาม token)
    """
    phrases = {k for d in dicts for k in d if " " in k}
    if not phrases:
        return None
    alts = sorted((re.escape(p) for p in phrases), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.IGNORECASE)


_PHRASE_RE = _build_phrase_re(ENGLISH_TO_THAI, BUNDLED_EN_TO_THAI, BUNDLED_EN_TO_THAI_UNVERIFIED)


def transliterate_english(text: str, dictionary: dict = None) -> str:
    """
    แทนคำอังกฤษที่รู้จักด้วยคำทับศัพท์ไทย — ให้โมเดลอ่านเป็นไทยล้วนรวดเดียว
    ไม่ต้องตัดสลับภาษา (ลดรอยสะดุด/เพี้ยนของ mixed_language)

    ลำดับค้นหา (หยุดที่ชั้นแรกที่เจอ):
      1. ดิกคัดเอง (ENGLISH_TO_THAI) — คำย่อ/แบรนด์ที่คุมเองว่าถูก
      2. ดิกภายนอกที่รับรองแล้ว (BUNDLED_EN_TO_THAI) — ผ่านหลักราชบัณฑิตยสภา
      3. ดิกภายนอกที่ยังไม่รับรอง (BUNDLED_EN_TO_THAI_UNVERIFIED) — สื่อไทยใช้จริงแต่ไม่ทางการ
      4. Gemini API ถ้าเปิด TTS_GEMINI_TRANSLITERATE=1 (ดู gemini_translit.py)
    ไม่เจอเลย = ปล่อยผ่านเป็นอังกฤษเดิม ตกไปให้ mixed_language/split_by_language จัดการ
    แยก generate ด้วยภาษาอังกฤษตามปกติ (ไม่เสี่ยงทับศัพท์มั่วสำหรับคำที่ไม่รู้จัก)
    """
    d = dictionary or ENGLISH_TO_THAI

    def _lookup(key):
        return (d.get(key) or BUNDLED_EN_TO_THAI.get(key)
                or BUNDLED_EN_TO_THAI_UNVERIFIED.get(key))

    def _sub_phrase(m):
        return _lookup(m.group(0).lower()) or m.group(0)

    def _sub_word(m):
        w = m.group(0)
        th = _lookup(w.lower())
        if th:
            return th
        from gemini_translit import gemini_transliterate  # lazy import — ไม่กระทบ path ที่ไม่เปิดใช้
        return gemini_transliterate(w) or w

    if _PHRASE_RE is not None:
        text = _PHRASE_RE.sub(_sub_phrase, text)
    return _ENGLISH_WORD_RE.sub(_sub_word, text)


# ── แปลงตัวเลขเป็นคำอ่านภาษาไทย ─────────────────────────────────────
# ปัญหาที่แก้: โมเดลอ่านตัวเลขดิบ ("15", "1,250", "081-234-5678") ไม่คงที่ —
# บางทีอ่านทีละหลักแบบอังกฤษ บางทีข้าม/เพี้ยน ทำให้เสียงที่ได้ไม่ตรงกับสคริปต์
# แก้แบบเดียวกับคำอังกฤษ: เขียนเป็น "คำอ่านไทย" ให้ชัดก่อนส่งเข้าโมเดล
_THAI_DIGIT_WORDS = ["ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]

# เบอร์โทรไทย (มือถือ/บ้าน) — อ่าน "ทีละหลัก" ตามธรรมเนียมไทย ไม่ใช่อ่านเป็นจำนวน
# รองรับทั้งแบบมีตัวคั่น (081-234-5678 / 081 234 5678) และมือถือ 10 หลักติดกัน (0812345678)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[-\s]\d{3}[-\s]\d{3,4}(?!\d)|(?<!\d)0\d{9}(?!\d)")
# จำนวนเงิน (ตัวเลข + "บาท" ต่อท้าย) — ใช้ bahttext อ่านรวมหน่วยให้ถูกหลัก (ถ้วน/สตางค์)
# กินคำว่า "บาท" เดิมไปด้วย (ไม่ใช้ lookahead) เพราะ bahttext() คืนคำว่า "...บาทถ้วน" มาเองแล้ว
_CURRENCY_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*บาท")
# ตัวเลข + เครื่องหมาย % — กินเครื่องหมายไปด้วยแล้วต่อท้ายด้วยคำว่า "เปอร์เซ็นต์" เอง
# (ไม่พึ่งพาว่าใน ENGLISH_TO_THAI จะแปลคำว่า "percent" ไว้แล้วหรือเปล่า)
_PERCENT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*%")
# ตัวเลขทั่วไปที่เหลือ (คั่นด้วย , ได้, มีทศนิยมได้)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _digits_to_words(digits: str) -> str:
    return " ".join(_THAI_DIGIT_WORDS[int(c)] for c in digits if c.isdigit())


def _number_to_words(num_str: str) -> str:
    num_str = num_str.replace(",", "")
    if "." in num_str:
        int_part, dec_part = num_str.split(".", 1)
        int_words = num_to_thaiword(int(int_part)) if int_part else _THAI_DIGIT_WORDS[0]
        dec_words = _digits_to_words(dec_part)
        return f"{int_words} จุด {dec_words}"
    return num_to_thaiword(int(num_str))


def normalize_thai_numbers(text: str) -> str:
    """
    แปลงตัวเลขในข้อความเป็นคำอ่านภาษาไทย ก่อนส่งเข้าโมเดล — กันปัญหาสคริปต์กับ
    เสียงที่ได้ไม่ตรงกัน (โมเดลอ่านเลขดิบไม่คงที่) ใช้ pythainlp.util คำนวณคำอ่าน

    ลำดับ: เบอร์โทร (อ่านทีละหลัก) -> จำนวนเงิน "...บาท" (bahttext) -> "...%" -> เลขทั่วไปที่เหลือ
    ต้องทำเบอร์โทร/เงิน/เปอร์เซ็นต์ก่อนเลขทั่วไป ไม่งั้นเลขในนั้นจะถูกอ่านเป็นจำนวนผิดความหมาย
    """
    text = _PHONE_RE.sub(lambda m: _digits_to_words(m.group(0)), text)

    def _currency_sub(m):
        digits = re.match(r"\d[\d,]*(?:\.\d+)?", m.group(0)).group(0)
        return bahttext(float(digits.replace(",", "")))

    text = _CURRENCY_RE.sub(_currency_sub, text)

    def _percent_sub(m):
        digits = re.match(r"\d[\d,]*(?:\.\d+)?", m.group(0)).group(0)
        return f"{_number_to_words(digits)} เปอร์เซ็นต์"

    text = _PERCENT_RE.sub(_percent_sub, text)
    text = _NUMBER_RE.sub(lambda m: _number_to_words(m.group(0)), text)
    return text


def split_by_language(text: str):
    """
    แยกข้อความเป็นช่วงตาม "สคริปต์" — ไทย/ลาว vs อังกฤษ (สำหรับ code-switching)
    คืน [(segment_text, lang), ...] โดย lang = "Thai", "Lao" หรือ "English"

    ตัวเลข/เครื่องหมาย/ช่องว่าง = neutral → เกาะไปกับช่วงก่อนหน้า (ไม่ตัดแยก)
    ใช้กับโหมด mixed_language: generate แต่ละช่วงด้วยภาษาที่ถูก แล้วต่อเสียงกัน
    """
    def script_of(ch):
        o = ord(ch)
        if 0x0E00 <= o <= 0x0E7F:
            return "Thai"
        if 0x0E80 <= o <= 0x0EFF:
            return "Lao"
        if ("a" <= ch.lower() <= "z"):
            return "English"
        return None  # neutral (เลข/สัญลักษณ์/ช่องว่าง)

    segments = []  # [[text, lang]]
    for ch in text:
        s = script_of(ch)
        if not segments:
            segments.append([ch, s or "Thai"])
            continue
        cur = segments[-1]
        if s is None or s == cur[1]:
            cur[0] += ch                       # neutral หรือภาษาเดิม → ต่อท้าย
        else:
            segments.append([ch, s])           # เปลี่ยนภาษา → ช่วงใหม่
    # รวมช่วงที่เหลือแต่ช่องว่าง/สัญลักษณ์เข้ากับช่วงข้างเคียง (กันเศษเสียงสั้นๆ)
    out = []
    for seg, lang in segments:
        if seg.strip() and any(script_of(c) for c in seg):
            out.append((seg, lang))
        elif out:
            out[-1] = (out[-1][0] + seg, out[-1][1])
        elif seg.strip():
            out.append((seg, lang))
    return out or [(text, "Thai")]


def chunk_text(text: str, min_chars: int = 60, max_chars: int = 220):
    """
    คืนรายการก้อนข้อความสำหรับป้อน generate ทีละก้อน

    - รวมประโยคสั้นๆ ต่อกันจนถึง min_chars (ลดจำนวน generate call)
    - ไม่ให้ก้อนไหนเกิน max_chars (กัน generate ก้อนใหญ่จนช้า/หน่วง)
    """
    sentences = split_sentences(text)
    chunks, buf = [], ""
    for sent in sentences:
        for piece in _hard_wrap(sent, max_chars):
            if not buf:
                buf = piece
            elif len(buf) + 1 + len(piece) <= max_chars:
                buf = f"{buf} {piece}"
            else:
                chunks.append(buf)
                buf = piece
            if len(buf) >= min_chars:
                chunks.append(buf)
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks
