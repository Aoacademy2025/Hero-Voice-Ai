"""
test_runpod_handler.py — ทดสอบ runpod_handler.handler() ตรงๆ บนเครื่อง local

จำลอง job แบบเดียวกับที่ RunPod cloud จะส่งมา (dict {"input": {...}})
ไม่ต้องมีบัญชี RunPod หรือ deploy จริง — ใช้พิสูจน์ว่า logic ถูกต้องก่อนขึ้น cloud

รัน: python tests/test_runpod_handler.py
"""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from runpod_handler import handler

print("\n=== ทดสอบ mode=tts (เสียงสต็อก) ===")
job1 = {"input": {"mode": "tts", "voice_id": "voice_01", "text": "สวัสดีครับ ทดสอบ RunPod handler"}}
out1 = handler(job1)
b1 = out1.pop("audio_base64", None)
print(json.dumps(out1, ensure_ascii=False, indent=2))
if b1:
    open("test_handler_tts.wav", "wb").write(base64.b64decode(b1))
    print("saved: test_handler_tts.wav")

print("\n=== ทดสอบ mode=clone (best-of-N) ===")
ref_path = r"C:\Users\USER\omnivoice\voices\voice_01.wav"
ref_b64 = base64.b64encode(open(ref_path, "rb").read()).decode("ascii")
job2 = {"input": {
    "mode": "clone",
    "ref_audio_b64": ref_b64,
    "ref_text": "สวัสดีครับ ยินดีต้อนรับเข้าสู่บริการของเรา",
    "text": "ทดสอบโคลนเสียงผ่าน RunPod handler",
}}
out2 = handler(job2)
b2 = out2.pop("audio_base64", None)
print(json.dumps(out2, ensure_ascii=False, indent=2))
if b2:
    open("test_handler_clone.wav", "wb").write(base64.b64decode(b2))
    print("saved: test_handler_clone.wav")

print("\n=== ทดสอบ error handling (mode ผิด) ===")
job3 = {"input": {"mode": "bogus"}}
print(json.dumps(handler(job3), ensure_ascii=False))

print("\n=== ทดสอบ error handling (ไม่มี text) ===")
job4 = {"input": {"mode": "tts", "voice_id": "voice_01"}}
print(json.dumps(handler(job4), ensure_ascii=False))
