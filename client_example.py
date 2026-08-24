"""
client_example.py — ตัวอย่างเรียก Hero Voice TTS API จากที่อื่น

  python client_example.py                       # ใช้ voice_01
  python client_example.py voice_02 "ข้อความ"     # ระบุเสียง+ข้อความ

ตั้ง API key ผ่าน env (ถ้า server เปิด auth):
  export TTS_API_KEY="sk_xxx"
"""
import base64
import os
import sys

import requests

API = os.environ.get("TTS_API_BASE", "http://localhost:8000")
API_KEY = os.environ.get("TTS_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

voice_id = sys.argv[1] if len(sys.argv) > 1 else "voice_01"
text = sys.argv[2] if len(sys.argv) > 2 else "สวัสดีครับ ทดสอบเสียงจาก API"

# ดูเสียงที่มี
print("เสียงที่ให้บริการ:", requests.get(f"{API}/voices", headers=HEADERS).json())

# ขอสร้างเสียง
r = requests.post(f"{API}/tts", headers=HEADERS,
                  json={"voice_id": voice_id, "text": text})
r.raise_for_status()
data = r.json()

print(f"voice_id={data['voice_id']} duration={data['duration']}s "
      f"gen_time={data['generation_time']}s credits={data.get('credits_charged')}")

# ถอด base64 -> เซฟไฟล์
out = f"api_out_{voice_id}.wav"
with open(out, "wb") as f:
    f.write(base64.b64decode(data["audio_base64"]))
print(f"saved: {out}")
