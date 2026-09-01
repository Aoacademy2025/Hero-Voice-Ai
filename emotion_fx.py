"""
emotion_fx.py — ใส่ "อารมณ์" ให้เสียงที่ OmniVoice สร้าง แบบไม่ต้องใช้ GPU

OmniVoice เองไม่รองรับอารมณ์เลย (ดูคอมเมนต์บนสุดของ server.py) — instruct ของมันคือ
"ออกแบบเสียง" (เพศ/อายุ/pitch/whisper/สำเนียง) ซึ่งเป็นคุณสมบัติคงที่ของเสียงคนพูด ไม่ใช่
อารมณ์ตอนพูดประโยคนั้นๆ ส่วนอารมณ์แบบ "จริง" (โมเดลปรับ prosody ให้เข้ากับความหมายประโยค)
ทำได้แค่ผ่าน IndexTTS-2 (engine_indextts.py) ซึ่งต้องมี GPU + ติดตั้งเพิ่ม

โมดูลนี้เป็นทางเลือกสำหรับเครื่องที่ไม่มี GPU (เช่นเครื่อง dev นี้): ปรับ pitch/ความเร็วเสียง
ที่ generate เสร็จแล้วด้วย DSP (librosa) ให้ "ฟังดูมีอารมณ์" มากขึ้น — เป็นการประมาณคร่าวๆ
ด้วยเสียง (pitch/tempo) เท่านั้น ไม่ใช่การปรับ prosody/น้ำเสียงจริงแบบโมเดล จึงไม่เนียนเท่า
IndexTTS-2 แต่ใช้งานได้ทันทีบน CPU

ใช้: apply(wav, sample_rate, emotion) -> wav ใหม่ (โยน ValueError ถ้าไม่รู้จักอารมณ์)
"""
import difflib

import numpy as np

# preset: (pitch_semitones, speed_multiplier) — ปรับได้ตามหูจริง ไม่มีสูตรตายตัว
# แนวคิด: อารมณ์บวก/ตื่นเต้น = pitch สูงขึ้น+เร็วขึ้น, อารมณ์ลบ/หดหู่ = pitch ต่ำลง+ช้าลง,
# โกรธ = เร็วขึ้นแต่ pitch ไม่สูง (หนักแน่นกว่าตื่นเต้น)
_PRESETS = {
    "happy":     (1.5, 1.08),
    "excited":   (2.5, 1.15),
    "sad":       (-2.0, 0.90),
    "angry":     (-1.0, 1.12),
    "calm":      (-0.5, 0.95),
    "fear":      (2.0, 1.10),
    "surprised": (2.0, 1.05),
    "neutral":   (0.0, 1.0),
    "disgust":   (-1.0, 0.95),   # รังเกียจ — โทนต่ำลงนิด ช้าลงนิด (ลังเล/ผลักไส)
    "gentle":    (-0.5, 0.92),   # อ่อนโยน — นุ่ม ช้าลงกว่า calm
    "confident": (0.0, 1.03),    # มั่นใจ — pitch เท่าเดิม แต่จังหวะกระชับขึ้นนิด
    "serious":   (-1.0, 0.93),   # จริงจัง — โทนต่ำ หนักแน่น ช้าลง
    "playful":   (2.0, 1.10),    # ขี้เล่น — สดใส เร็วขึ้น
    "tired":     (-2.5, 0.85),   # เหนื่อย/ง่วง — โทนต่ำมาก ช้ามาก
    "nervous":   (1.0, 1.18),    # ประหม่า/กังวล — เร็วจนรัว pitch ขึ้นนิด
}

# ชื่อไทย → พรีเซ็ตเดียวกัน (UI/ผู้ใช้ไทยส่วนใหญ่จะพิมพ์ไทย)
_TH_ALIASES = {
    "ดีใจ": "happy", "มีความสุข": "happy",
    "ตื่นเต้น": "excited",
    "เศร้า": "sad", "หดหู่": "sad",
    "โกรธ": "angry", "โมโห": "angry",
    "สงบ": "calm", "เรียบเฉย": "calm",
    "กลัว": "fear", "หวาดกลัว": "fear",
    "ประหลาดใจ": "surprised", "ตกใจ": "surprised",
    "ปกติ": "neutral", "เฉยๆ": "neutral",
    "รังเกียจ": "disgust", "ขยะแขยง": "disgust",
    "อ่อนโยน": "gentle", "นุ่มนวล": "gentle",
    "มั่นใจ": "confident",
    "จริงจัง": "serious", "เคร่งขรึม": "serious",
    "ขี้เล่น": "playful", "สนุกสนาน": "playful",
    "เหนื่อย": "tired", "ง่วง": "tired",
    "ประหม่า": "nervous", "กังวล": "nervous", "ตื่นตระหนก": "nervous",
}

ALLOWED = sorted(set(_PRESETS) | set(_TH_ALIASES))


def _resolve(emotion: str) -> str:
    key = emotion.strip().lower()
    key = _TH_ALIASES.get(emotion.strip(), key)  # เช็คไทยแบบไม่ lower (ไทยไม่มี case อยู่แล้ว)
    if key in _PRESETS:
        return key
    sug = difflib.get_close_matches(key, _PRESETS, n=1, cutoff=0.6)
    hint = f" ใกล้เคียงกับ '{sug[0]}' หรือเปล่า?" if sug else ""
    raise ValueError(f"ไม่รู้จักอารมณ์ '{emotion}'.{hint} ใช้ได้: {', '.join(ALLOWED)}")


def apply(wav: np.ndarray, sample_rate: int, emotion: str) -> np.ndarray:
    """ปรับ pitch/ความเร็วตามอารมณ์ที่ระบุ — คืน wav ใหม่ (float32)
    โยน ValueError ถ้าไม่รู้จักชื่ออารมณ์ (ให้ endpoint แปลงเป็น HTTP 422 เอง)"""
    key = _resolve(emotion)
    pitch_steps, speed = _PRESETS[key]

    import librosa

    y = np.asarray(wav, dtype=np.float32)
    if abs(pitch_steps) > 1e-6:
        y = librosa.effects.pitch_shift(y, sr=sample_rate, n_steps=pitch_steps)
    if abs(speed - 1.0) > 1e-3:
        y = librosa.effects.time_stretch(y, rate=speed)
    return y.astype(np.float32)
