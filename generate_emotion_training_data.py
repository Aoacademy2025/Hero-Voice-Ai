"""
⚠️ หยุดใช้ไว้ก่อน (2026-09-02) — IndexTTS-2 อ่านภาษาไทยไม่ได้จริง
BPE tokenizer ของมันมองข้อความไทยทั้งประโยคเป็น "unknown token" ตัวเดียว (ดู warning
"input text contains 1 unknown tokens" ใน indextts_service log) ทำให้เสียงที่ได้เป็นแค่
เสียงพึมพำ ไม่ใช่คำพูดจริง (เช็คด้วย ASR แล้วได้แค่ "hmm") — ใช้สร้างข้อมูลเทรนภาษาไทย
ไม่ได้เลย ต้องหาโมเดลอื่นที่รองรับภาษาไทยจริง หรือใช้เสียงคนอัดจริงแทน ก่อนจะกลับมาใช้ไฟล์นี้

generate_emotion_training_data.py — สร้างชุดข้อมูลเทรน "อารมณ์" ให้ OmniVoice
โดยใช้ IndexTTS-2 (ผ่าน indextts_service.py) เป็นตัว generate เสียงต้นแบบ

แนวคิด: OmniVoice เรียนรู้ instruct จากคู่ (instruct text, เสียงจริงที่ตรงกับ instruct นั้น)
ตอนเทรน (ดู OmniVoice/omnivoice/data/processor.py) — ถ้าป้อนคู่ instruct="...,happy" กับ
เสียงที่ IndexTTS-2 พูดด้วยอารมณ์ happy จริง มากพอ โมเดลควรเรียนรู้คำว่า "happy" ได้
เหมือนที่เรียนรู้ "female"/"high pitch" ได้ (กลไกเดียวกันเป๊ะ ไม่ใช่ของใหม่)

ก่อนรัน ต้องมี indextts_service.py รันอยู่แล้ว:
  PYTHONIOENCODING=utf-8 venv_indextts/Scripts/python.exe indextts_service.py

รัน (จาก venv หลัก — ใช้แค่ requests, ไม่ต้องพึ่ง indextts):
  venv/Scripts/python.exe generate_emotion_training_data.py

Resume ได้เอง — ข้ามตัวอย่างที่ generate ไปแล้ว (เช็คจากไฟล์ที่มีอยู่)
ผลลัพธ์:
  emotion_training_data/audio/*.wav        เสียงที่ generate
  emotion_training_data/manifest.jsonl     JSONL สำหรับป้อน extract_audio_tokens.py ต่อ
  emotion_training_data/errors.jsonl       รายการที่ generate ไม่สำเร็จ (ถ้ามี)
"""
import json
import os
import time

import requests

SERVICE_URL = os.environ.get("INDEXTTS_SERVICE_URL", "http://localhost:8001")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emotion_training_data")
AUDIO_DIR = os.path.join(OUT_DIR, "audio")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.jsonl")
ERRORS_PATH = os.path.join(OUT_DIR, "errors.jsonl")

# เสียงต้นแบบ — คัดให้หลากหลาย gender/pitch จาก voices.json ที่มีอยู่แล้ว
# (instruct ของแต่ละเสียงเอามาจาก voices.json ตรงๆ กันเขียนผิด/ไม่ตรงกับที่ระบบใช้จริง)
VOICE_IDS = ["voice_01", "voice_02", "voice_03", "voice_04"]

# อารมณ์หลักที่ IndexTTS-2 รองรับผ่าน emo_text (ดู qwen0.6bemo4-merge)
# "neutral" ไม่ส่ง emo_text เลย (ใช้เป็นเคสฐาน กันโมเดลลืมพูดปกติ)
EMOTIONS = ["happy", "sad", "angry", "excited", "calm", None]

TEXTS = [
    "วันนี้อากาศดีมากเลยครับ",
    "ผมอยากไปเที่ยวทะเลจังเลย",
    "งานที่ทำอยู่ใกล้เสร็จแล้วนะ",
    "เธอทำแบบนี้กับฉันได้ยังไง",
    "เราต้องรีบไปแล้ว รถจะออกอยู่แล้ว",
    "ขอบคุณมากนะที่ช่วยเหลือฉันมาตลอด",
    "เรื่องนี้มันเกินไปแล้วจริงๆ",
    "พรุ่งนี้มีประชุมสำคัญตอนเช้า",
]


def _emotion_to_instruct_suffix(emotion):
    return emotion if emotion else None


def main():
    os.makedirs(AUDIO_DIR, exist_ok=True)

    with open("voices/voices.json", encoding="utf-8") as f:
        voice_meta = {v["id"]: v for v in json.load(f)}

    # โหลด manifest เดิม (ถ้ามี) เพื่อ resume — เก็บ id ที่ทำสำเร็จแล้ว
    done_ids = set()
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    done_ids.add(json.loads(line)["id"])

    jobs = []
    for voice_id in VOICE_IDS:
        if voice_id not in voice_meta:
            print(f"[skip] {voice_id} ไม่อยู่ใน voices.json")
            continue
        ref_audio = os.path.join("voices", voice_meta[voice_id]["ref_audio"])
        base_instruct = voice_meta[voice_id].get("instruct", "")
        for emotion in EMOTIONS:
            for ti, text in enumerate(TEXTS):
                emo_tag = emotion or "neutral"
                sample_id = f"{voice_id}_{emo_tag}_{ti:02d}"
                jobs.append((sample_id, voice_id, ref_audio, base_instruct, emotion, text))

    total = len(jobs)
    todo = [j for j in jobs if j[0] not in done_ids]
    print(f"[gen] ทั้งหมด {total} ตัวอย่าง, ทำไปแล้ว {total - len(todo)}, เหลือ {len(todo)}")

    manifest_f = open(MANIFEST_PATH, "a", encoding="utf-8")
    errors_f = open(ERRORS_PATH, "a", encoding="utf-8")

    for i, (sample_id, voice_id, ref_audio, base_instruct, emotion, text) in enumerate(todo):
        t0 = time.time()
        audio_path = os.path.join(AUDIO_DIR, f"{sample_id}.wav")
        instruct = f"{base_instruct}, {emotion}" if emotion else base_instruct
        try:
            with open(ref_audio, "rb") as f:
                data = {"text": text, "speed": "1.0"}
                if emotion:
                    data.update(emotion=emotion, emo_alpha="0.7")  # 0.7 กันเสียงเวอร์เกินไป (ดูผลทดสอบก่อนหน้า)
                r = requests.post(f"{SERVICE_URL}/synth",
                                   files={"ref_audio": f}, data=data, timeout=180)
            r.raise_for_status()
            with open(audio_path, "wb") as f:
                f.write(r.content)

            entry = {
                "id": sample_id,
                "audio_path": os.path.abspath(audio_path),
                "text": text,
                "instruct": instruct,
                "language_id": "th",
            }
            manifest_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            manifest_f.flush()
            dt = time.time() - t0
            print(f"[{i+1}/{len(todo)}] {sample_id} -> {dt:.1f}s  instruct={instruct!r}", flush=True)
        except Exception as e:
            errors_f.write(json.dumps({"id": sample_id, "error": str(e)}, ensure_ascii=False) + "\n")
            errors_f.flush()
            print(f"[{i+1}/{len(todo)}] {sample_id} FAILED: {e}", flush=True)

    manifest_f.close()
    errors_f.close()
    print(f"[gen] เสร็จแล้ว — manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
