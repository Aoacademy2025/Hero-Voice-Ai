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
import re

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


def split_by_language(text: str):
    """
    แยกข้อความเป็นช่วงตาม "สคริปต์" — ไทย vs อังกฤษ (สำหรับ code-switching)
    คืน [(segment_text, lang), ...] โดย lang = "Thai" หรือ "English"

    ตัวเลข/เครื่องหมาย/ช่องว่าง = neutral → เกาะไปกับช่วงก่อนหน้า (ไม่ตัดแยก)
    ใช้กับโหมด mixed_language: generate แต่ละช่วงด้วยภาษาที่ถูก แล้วต่อเสียงกัน
    """
    def script_of(ch):
        o = ord(ch)
        if 0x0E00 <= o <= 0x0E7F:
            return "Thai"
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
