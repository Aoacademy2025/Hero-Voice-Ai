"""
OmniVoice - สคริปต์ใช้งานง่าย (รันบน CPU, ใช้โมเดล local)

วิธีใช้:
  # 1) Auto Voice - ให้โมเดลเลือกเสียงเอง
  python tts.py --text "สวัสดีครับ ทดสอบเสียง" --out out.wav

  # 2) Voice Cloning - โคลนเสียงจากไฟล์ตัวอย่าง (ต้องใส่ ref_text)
  python tts.py --text "ข้อความที่อยากพูด" --ref_audio ref.wav --ref_text "ข้อความในไฟล์ตัวอย่าง" --out out.wav

  # 3) Voice Design - ออกแบบเสียงด้วยคำอธิบาย
  python tts.py --text "Hello world" --instruct "female, british accent" --out out.wav

ตัวเลือกเพิ่มเติม:
  --num_step 32     จำนวน diffusion steps (16=เร็ว, 32=คุณภาพสูงขึ้น — default หลัง A/B ฟังเทียบ)
  --speed 1.0       ความเร็วพูด (>1 เร็วขึ้น, <1 ช้าลง)
"""
import argparse
import sys
import io
import time
import torch
import soundfile as sf
from omnivoice import OmniVoice

# บังคับ stdout เป็น UTF-8 เพื่อให้แสดงภาษาไทยถูกต้องบน Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# โฟลเดอร์โมเดล local (ไม่ต้องโหลดจากอินเทอร์เน็ต)
MODEL_DIR = r"C:\Users\USER\omnivoice\model"


def main():
    p = argparse.ArgumentParser(description="OmniVoice TTS (CPU, local model)")
    p.add_argument("--text", required=True, help="ข้อความที่จะแปลงเป็นเสียง")
    p.add_argument("--out", default="output.wav", help="ไฟล์เสียงผลลัพธ์ (.wav)")
    p.add_argument("--ref_audio", default=None, help="ไฟล์เสียงตัวอย่างสำหรับโคลน (3-10 วิ)")
    p.add_argument("--ref_text", default=None, help="ข้อความที่ตรงกับ ref_audio")
    p.add_argument("--instruct", default=None, help="คำอธิบายเสียง เช่น 'female, british accent'")
    p.add_argument("--num_step", type=int, default=32, help="diffusion steps (16=เร็ว, 32=คุณภาพ)")
    p.add_argument("--speed", type=float, default=1.0, help="ความเร็วพูด")
    args = p.parse_args()

    print(f"กำลังโหลดโมเดลจาก {MODEL_DIR} (CPU)...")
    t = time.time()
    model = OmniVoice.from_pretrained(MODEL_DIR, device_map="cpu", dtype=torch.float32)
    print(f"โหลดโมเดลเสร็จใน {time.time()-t:.1f}s")

    # สร้าง kwargs ตามโหมด
    gen_kwargs = dict(text=args.text, num_step=args.num_step, speed=args.speed)
    if args.ref_audio:
        mode = "Voice Cloning"
        gen_kwargs["ref_audio"] = args.ref_audio
        if args.ref_text:
            gen_kwargs["ref_text"] = args.ref_text
        else:
            print("⚠️  ไม่ได้ใส่ --ref_text อาจต้องใช้ Whisper (ซึ่งยังไม่ได้ติดตั้ง)")
            print("    แนะนำให้ใส่ --ref_text เพื่อเลี่ยงการโหลด ASR")
    elif args.instruct:
        mode = "Voice Design"
        gen_kwargs["instruct"] = args.instruct
    else:
        mode = "Auto Voice"

    print(f"โหมด: {mode}")
    print(f"กำลังสร้างเสียง...")
    t = time.time()
    audio = model.generate(**gen_kwargs)
    gen_time = time.time() - t

    sf.write(args.out, audio[0], 24000)
    dur = len(audio[0]) / 24000
    print(f"[OK] เสร็จ! เสียงยาว {dur:.1f}s | ใช้เวลาสร้าง {gen_time:.1f}s")
    print(f"     บันทึกที่: {args.out}")


if __name__ == "__main__":
    main()
