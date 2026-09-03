"""
test_tts.py — สคริปต์ทดสอบเสียง: ไทย / อังกฤษ / ตัวเลข / ปนกัน

รัน server ไว้ก่อน (bash start_all.sh) แล้ว:
  python tests/test_tts.py                          # ใช้ voice_02, num_step 32
  python tests/test_tts.py --voice voice_05         # เปลี่ยนเสียง
  python tests/test_tts.py --voice cv_xxx           # ใช้เสียงโคลนของเรา
  python tests/test_tts.py --num_step 40 --guidance 3 --speed 0.7

ผลไฟล์อยู่ใน test_outputs/  (เปิดฟังเทียบได้)
env: TTS_API_BASE (ดีฟอลต์ http://localhost:8000), TTS_API_KEY (ถ้าเปิด auth)
"""
import argparse
import base64
import os
import sys

import requests

API = os.environ.get("TTS_API_BASE", "http://localhost:8000")
KEY = os.environ.get("TTS_API_KEY", "")
HEADERS = {"X-API-Key": KEY} if KEY else {}
# test_outputs/ อยู่ที่รากรีโป (ไปอีกชั้นจาก tests/) ไม่ได้ย้ายมาด้วยตอนจัดระเบียบโฟลเดอร์
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO_ROOT, "test_outputs")

# เคสทดสอบ: (ชื่อไฟล์, ข้อความ, override params)
CASES = [
    ("01_thai",            "สวัสดีครับ วันนี้อากาศดีมาก ยินดีต้อนรับทุกท่านเข้าสู่ระบบ", {}),
    ("02_english",         "Hello, welcome to Hero Voice text to speech system.", {"language": "English"}),
    ("03_numbers_thai",    "ราคาทั้งหมด 1,250 บาท โทร 081-234-5678 เวลา 9 โมงครึ่ง", {}),
    ("04_mixed_OFF",       "วันนี้เราจะใช้ AI ช่วย download ไฟล์จาก Google Drive", {"mixed_language": False}),
    ("05_mixed_ON",        "วันนี้เราจะใช้ AI ช่วย download ไฟล์จาก Google Drive", {"mixed_language": True}),
    ("06_num_mixed",       "สินค้ารุ่น iPhone 15 Pro ราคา 39,900 บาท ลด 20 เปอร์เซ็นต์", {"mixed_language": True}),
    ("07_english_in_thai", "ผมชอบฟังเพลงบน Spotify และดูหนังบน Netflix ทุกวัน", {"mixed_language": True}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="voice_02")
    ap.add_argument("--engine", default="omnivoice")
    ap.add_argument("--num_step", type=int, default=32)
    ap.add_argument("--guidance", type=float, default=2.5)
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    # เช็ก server + เสียง
    try:
        h = requests.get(f"{API}/health", timeout=5).json()
        print(f"server: {h['status']} · {h.get('device')} · {h.get('num_voices')} เสียง")
    except Exception as e:
        sys.exit(f"เชื่อมต่อ server ไม่ได้ที่ {API} — รัน `bash start_all.sh` ก่อน ({e})")

    print(f"เสียง: {args.voice} | num_step={args.num_step} guidance={args.guidance} speed={args.speed}\n")

    for name, text, override in CASES:
        body = {
            "text": text, "voice_id": args.voice, "engine": args.engine,
            "num_step": args.num_step, "guidance_scale": args.guidance, "speed": args.speed,
        }
        body.update(override)
        try:
            r = requests.post(f"{API}/tts", headers=HEADERS, json=body, timeout=300)
            if r.status_code != 200:
                print(f"  ✗ {name}: {r.status_code} {r.text[:120]}")
                continue
            d = r.json()
            path = os.path.join(OUT_DIR, f"{name}.wav")
            with open(path, "wb") as f:
                f.write(base64.b64decode(d["audio_base64"]))
            tag = " [mixed]" if body.get("mixed_language") else ""
            print(f"  ✓ {name}{tag}: {d['duration']}s (gen {d['generation_time']}s) → test_outputs/{name}.wav")
            print(f"      « {text} »")
        except Exception as e:
            print(f"  ✗ {name}: {e}")

    print(f"\nเสร็จ — เปิดโฟลเดอร์ {OUT_DIR} ฟังเทียบได้เลย")
    print("เทียบ 04_mixed_OFF vs 05_mixed_ON เพื่อดูผลโหมดแยกภาษา")


if __name__ == "__main__":
    main()
