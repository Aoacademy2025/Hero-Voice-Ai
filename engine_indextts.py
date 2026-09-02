"""
engine_indextts.py — เอนจินที่ 2: IndexTTS-2 (cloning เหมือนสูง + คุมอารมณ์)

เรียกผ่าน HTTP ไปที่ indextts_service.py ซึ่งรันแยก process ต่างหาก (venv_indextts)
เหตุผล: indextts ต้องการ torch เวอร์ชันใหม่กว่าที่ OmniVoice (venv/ หลัก) ใช้ —
import รวม process เดียวกับ server.py หลักจะชนกัน (ABI ไม่ตรง) ต้องแยกกันเสมอ

ก่อนใช้งาน ต้องรัน indextts_service.py ไว้ก่อน (ดู setup_indextts2.sh สำหรับ
ขั้นตอนเตรียม venv_indextts + ดาวน์โหลด checkpoint):
  PYTHONIOENCODING=utf-8 venv_indextts/Scripts/python.exe indextts_service.py

เปิดใช้งานฝั่ง server.py หลัก:
  export TTS_ENABLE_INDEXTTS=1
  export INDEXTTS_SERVICE_URL=http://localhost:8001   # ดีฟอลต์อยู่แล้ว เปลี่ยนถ้ารันคนละเครื่อง

⚠️ VRAM: การ์ดจอเล็ก (6GB) รันคู่กับ OmniVoice พร้อมกันไม่พอ — ต้องปิด server หลักก่อน
รัน indextts_service.py เสมอถ้าอยู่ GPU เดียวกัน (บน RunPod GPU ใหญ่กว่าจะรันพร้อมกันได้)

interface ให้ตรงกับที่ server เรียก:
  .id .name .sample_rate .supports_clone .supports_design .supports_emotion
  .load()
  .list_voices() -> []                      (ไม่มีเสียงสต็อกของตัวเอง)
  .synth(text, ref_wav, ref_text=None, emotion=None, speed=1.0) -> (np.ndarray float32, 24000)
"""
import io
import os

import numpy as np
import requests
import soundfile as sf

import watermark

SAMPLE_RATE = 24000
SERVICE_URL = os.environ.get("INDEXTTS_SERVICE_URL", "http://localhost:8001")
_TIMEOUT = float(os.environ.get("INDEXTTS_SERVICE_TIMEOUT", "180"))


class IndexTTS2Engine:
    id = "indextts2"
    name = "IndexTTS-2"
    sample_rate = SAMPLE_RATE
    supports_clone = True
    supports_design = False
    supports_emotion = True  # คุมอารมณ์ผ่านข้อความ (emo_text) — เติมช่องว่างที่ OmniVoice ทำไม่ได้

    def __init__(self):
        self.voices = {}  # ไม่มีเสียงสต็อกของตัวเอง (ใช้ ref จากคลังกลาง)

    def load(self):
        """เช็คว่า indextts_service.py รันอยู่และพร้อมจริง — ไม่ได้โหลดโมเดลในโปรเซสนี้"""
        r = requests.get(f"{SERVICE_URL}/health", timeout=5)
        r.raise_for_status()
        status = r.json().get("status")
        if status != "ok":
            raise RuntimeError(f"indextts_service ที่ {SERVICE_URL} ยังไม่พร้อม (status={status})")
        print(f"[indextts2] เชื่อมกับ indextts_service สำเร็จ ({SERVICE_URL})")

    def list_voices(self):
        return []

    def synth(self, text, ref_wav, ref_text=None, emotion=None, speed=1.0):
        """เรียก indextts_service.py → คืน (wav float32 @24k, 24000). ref_text ไม่จำเป็นสำหรับ IndexTTS"""
        with open(ref_wav, "rb") as f:
            files = {"ref_audio": (os.path.basename(ref_wav), f, "audio/wav")}
            data = {"text": text, "speed": str(speed)}
            if emotion:
                data["emotion"] = emotion
            r = requests.post(f"{SERVICE_URL}/synth", files=files, data=data, timeout=_TIMEOUT)
        r.raise_for_status()
        wav, sr = sf.read(io.BytesIO(r.content), dtype="float32")
        wav = watermark.apply(np.asarray(wav, dtype=np.float32), sr)
        return wav, sr
