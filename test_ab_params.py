"""
test_ab_params.py — สร้างไฟล์เสียงชุด A/B เทียบ num_step / guidance_scale หลายค่า
บนประโยคเดียวกัน เพื่อฟังเทียบเลือก default ที่เนียนสุดโดยไม่ช้าเกินจำเป็น

ใช้ voice_id เดียวกับที่ server.py ใช้จริง (ผ่าน build_prompt เหมือน /tts)
รันแล้วไฟล์จะอยู่ใน ab_output/<voice_id>__step<N>__gs<G>.wav — เปิดฟังเทียบเอง

วิธีรัน: python test_ab_params.py [voice_id] [text ไทยเอง]
"""
import os
import sys
import time

import soundfile as sf

from server import OmniVoiceEngine, SAMPLE_RATE
from text_utils import normalize_thai_numbers, transliterate_english

OUT_DIR = "ab_output"

DEFAULT_TEXT = "สวัสดีครับ วันนี้อากาศดีมากเลยนะครับ"

# ชุด (num_step, guidance_scale) ที่จะทดสอบ — None = ใช้ default ของโมเดล (2.0)
# ตัดให้สั้นลง (เดิม 4x4 ค่า x ประโยคยาว) เพราะ CPU-only inference ช้ามาก — เอาไว้ฟังเทียบ
# คร่าวๆ ก่อน ถ้าอยากได้ค่าอื่นเพิ่ม ปรับ list นี้แล้วรันใหม่ทีละชุดได้
STEP_CANDIDATES = [16, 32]
GUIDANCE_CANDIDATES = [None, 3.0]


def main():
    voice_id = sys.argv[1] if len(sys.argv) > 1 else "voice_01"
    text = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TEXT
    text = transliterate_english(text)
    text = normalize_thai_numbers(text)
    print(f"[ab] text ที่ normalize แล้ว: {text!r}")

    os.makedirs(OUT_DIR, exist_ok=True)

    print("[ab] loading engine ...")
    eng = OmniVoiceEngine()
    eng.load()
    print("[ab] ready")

    v = eng.voices[voice_id]
    prompt = eng.build_prompt(v["ref_audio"], v["meta"]["ref_text"])

    print(f"\n=== เทียบ num_step (guidance_scale=default) ===")
    for step in STEP_CANDIDATES:
        t0 = time.time()
        wav, dur = eng._run(text, clone_prompt=prompt, language="Thai", num_step=step)
        elapsed = time.time() - t0
        out = os.path.join(OUT_DIR, f"{voice_id}__step{step}__gsdefault.wav")
        sf.write(out, wav, SAMPLE_RATE)
        print(f"  step={step:>3}  gen_time={elapsed:5.2f}s  audio_dur={dur:5.2f}s  rtf={elapsed/dur:5.2f}  -> {out}")

    print(f"\n=== เทียบ guidance_scale (num_step=32) ===")
    for gs in GUIDANCE_CANDIDATES:
        t0 = time.time()
        wav, dur = eng._run(text, clone_prompt=prompt, language="Thai", num_step=32, guidance_scale=gs)
        elapsed = time.time() - t0
        tag = "default" if gs is None else str(gs)
        out = os.path.join(OUT_DIR, f"{voice_id}__step32__gs{tag}.wav")
        sf.write(out, wav, SAMPLE_RATE)
        print(f"  gs={tag:>7}  gen_time={elapsed:5.2f}s  audio_dur={dur:5.2f}s  rtf={elapsed/dur:5.2f}  -> {out}")

    print(f"\n[ab] เสร็จแล้ว — ไฟล์ทั้งหมดอยู่ใน {OUT_DIR}/ เปิดฟังเทียบเลือกค่าที่เนียนสุดได้เลย")


if __name__ == "__main__":
    main()
