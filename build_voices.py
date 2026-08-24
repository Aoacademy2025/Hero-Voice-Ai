"""
build_voices.py — สร้างคลัง "เสียงสต็อก" ล่วงหน้า (ทำครั้งเดียว, offline)

แนวคิด:
  Voice Design (instruct) ตรงๆ จะให้เสียงไม่เหมือนเดิมทุกครั้ง (มี randomness)
  จึงไม่เหมาะใช้ตอน runtime. วิธีที่ถูกต้องคือ:
    1) ใช้ Voice Design สร้าง "ตัวอย่างเสียง" ของแต่ละ preset (ทำที่นี่)
    2) save เป็นไฟล์ .wav + ref_text ลงโฟลเดอร์ voices/
    3) ตอน runtime (server.py) ใช้ Voice Cloning จากไฟล์เหล่านี้
       → ได้เสียงคงที่ทุกครั้งที่ user เลือก voice เดิม

วิธีใช้:
  python build_voices.py                 # สร้างทุกเสียงใน VOICE_PRESETS
  python build_voices.py --device cuda   # ใช้ GPU (เร็วกว่ามาก)

หลังรันเสร็จจะได้:
  voices/voice_01.wav, voice_02.wav, ...
  voices/voices.json   (เก็บ id, ref_text, คำอธิบาย)
"""
import argparse
import json
import os
import time

import torch
import soundfile as sf
from omnivoice import OmniVoice

MODEL_DIR = r"C:\Users\USER\omnivoice\model"
VOICES_DIR = r"C:\Users\USER\omnivoice\voices"
SAMPLE_RATE = 24000

# ─────────────────────────────────────────────────────────────────────
# กำหนดเสียงสต็อกที่ต้องการ — แก้ลิสต์นี้ได้ตามใจ
#   instruct  : คำบรรยายเสียง — รองรับเฉพาะคำในลิสต์นี้เท่านั้น (ไม่งั้น error):
#               gender : male, female
#               age    : child, teenager, young adult, middle-aged, elderly
#               pitch  : very low pitch, low pitch, moderate pitch,
#                        high pitch, very high pitch
#               style  : whisper
#               accent : american accent, british accent, australian accent, ...
#               *** ไม่มีหมวด "อารมณ์" (happy/sad/gentle ใช้ไม่ได้) ***
#               แต่ละหมวดใส่ได้อย่างมาก 1 คำ, คั่นด้วย ", "
#   ref_text  : ประโยคตัวอย่างที่จะให้เสียงนี้พูดตอนสร้าง (จะกลายเป็น ref_text
#               ใช้ตอน clone — ควรเป็นประโยคธรรมดา ออกเสียงชัด)
# ─────────────────────────────────────────────────────────────────────
VOICE_PRESETS = [
    # --- เพศ x โทนเสียง (pitch) ---
    {
        "id": "voice_01",
        "instruct": "male",
        "ref_text": "สวัสดีครับ ยินดีต้อนรับเข้าสู่บริการของเรา",
        "desc": "เสียงผู้ชาย โทนปกติ",
    },
    {
        "id": "voice_02",
        "instruct": "female",
        "ref_text": "สวัสดีค่ะ ยินดีให้บริการนะคะ",
        "desc": "เสียงผู้หญิง โทนปกติ",
    },
    {
        "id": "voice_03",
        "instruct": "male, high pitch",
        "ref_text": "วันนี้เป็นวันที่ดีมากเลยนะครับ",
        "desc": "เสียงผู้ชาย โทนสูง สดใส",
    },
    {
        "id": "voice_04",
        "instruct": "female, low pitch",
        "ref_text": "ขอบคุณที่ใช้บริการของเรานะคะ",
        "desc": "เสียงผู้หญิง โทนต่ำ นุ่มนวล",
    },
    {
        "id": "voice_05",
        "instruct": "male, very low pitch",
        "ref_text": "ขอเชิญรับฟังประกาศสำคัญครับ",
        "desc": "เสียงผู้ชาย โทนต่ำมาก หนักแน่น",
    },
    {
        "id": "voice_06",
        "instruct": "female, very high pitch",
        "ref_text": "หวัดดีค่ะ วันนี้มีอะไรให้ช่วยไหมคะ",
        "desc": "เสียงผู้หญิง โทนสูงมาก สดใส",
    },
    # --- ช่วงอายุ x เพศ ---
    {
        "id": "voice_07",
        "instruct": "child",
        "ref_text": "สวัสดีครับ ผมชื่อน้องพลอยนะครับ",
        "desc": "เสียงเด็ก",
    },
    {
        "id": "voice_08",
        "instruct": "teenager, female",
        "ref_text": "หวัดดีค่ะ วันนี้อากาศดีจังเลย",
        "desc": "เสียงวัยรุ่นหญิง",
    },
    {
        "id": "voice_09",
        "instruct": "teenager, male",
        "ref_text": "โย่ว วันนี้มีอะไรสนุกๆ บ้างครับ",
        "desc": "เสียงวัยรุ่นชาย",
    },
    {
        "id": "voice_10",
        "instruct": "young adult, female",
        "ref_text": "สวัสดีค่ะ ยินดีที่ได้ร่วมงานด้วยนะคะ",
        "desc": "เสียงผู้หญิงวัยทำงานตอนต้น",
    },
    {
        "id": "voice_11",
        "instruct": "young adult, male",
        "ref_text": "สวัสดีครับ ยินดีที่ได้ร่วมงานด้วยครับ",
        "desc": "เสียงผู้ชายวัยทำงานตอนต้น",
    },
    {
        "id": "voice_12",
        "instruct": "middle-aged, male",
        "ref_text": "เรียนท่านผู้มีเกียรติทุกท่านครับ",
        "desc": "เสียงผู้ชายวัยกลางคน",
    },
    {
        "id": "voice_13",
        "instruct": "middle-aged, female",
        "ref_text": "เรียนท่านผู้มีเกียรติทุกท่านค่ะ",
        "desc": "เสียงผู้หญิงวัยกลางคน",
    },
    {
        "id": "voice_14",
        "instruct": "elderly, male",
        "ref_text": "สวัสดีนะลูก วันนี้เป็นยังไงบ้าง",
        "desc": "เสียงผู้ชายสูงวัย",
    },
    {
        "id": "voice_15",
        "instruct": "elderly, female",
        "ref_text": "สวัสดีจ้ะหลาน วันนี้เป็นยังไงบ้าง",
        "desc": "เสียงผู้หญิงสูงวัย",
    },
    # --- สำเนียงภาษาอังกฤษ (ใช้กับข้อความอังกฤษ) ---
    {
        "id": "voice_16",
        "instruct": "female, british accent",
        "ref_text": "Hello, welcome to our service today.",
        "desc": "เสียงผู้หญิง สำเนียงอังกฤษ (British)",
    },
    # --- เพิ่มเติม: เด็กแยกเพศ, วัยรุ่นชายเพิ่ม, pitch กลาง, สำเนียงเพิ่ม ---
    {
        "id": "voice_17",
        "instruct": "child, male",
        "ref_text": "สวัสดีครับ ผมชอบเล่นฟุตบอลกับเพื่อนๆ ครับ",
        "desc": "เสียงเด็กผู้ชาย",
    },
    {
        "id": "voice_18",
        "instruct": "child, female",
        "ref_text": "สวัสดีค่ะ หนูชอบวาดรูปกับอ่านนิทานค่ะ",
        "desc": "เสียงเด็กผู้หญิง",
    },
    {
        "id": "voice_19",
        "instruct": "male, moderate pitch",
        "ref_text": "ยินดีให้บริการครับ มีอะไรให้ช่วยเหลือไหมครับ",
        "desc": "เสียงผู้ชาย โทนกลาง",
    },
    {
        "id": "voice_20",
        "instruct": "female, moderate pitch",
        "ref_text": "ยินดีให้บริการค่ะ มีอะไรให้ช่วยเหลือไหมคะ",
        "desc": "เสียงผู้หญิง โทนกลาง",
    },
    {
        "id": "voice_21",
        "instruct": "teenager, male, high pitch",
        "ref_text": "เฮ้ย มาเล่นเกมด้วยกันไหมเพื่อน",
        "desc": "เสียงวัยรุ่นชาย โทนสูง กระตือรือร้น",
    },
    {
        "id": "voice_22",
        "instruct": "young adult, male, low pitch",
        "ref_text": "สวัสดีครับ ผมขอนำเสนองานของทีมเราครับ",
        "desc": "เสียงผู้ชายวัยทำงานตอนต้น โทนต่ำ หนักแน่น",
    },
    {
        "id": "voice_23",
        "instruct": "young adult, female, high pitch",
        "ref_text": "สวัสดีค่ะ ดิฉันขอนำเสนองานของทีมเราค่ะ",
        "desc": "เสียงผู้หญิงวัยทำงานตอนต้น โทนสูง กระฉับกระเฉง",
    },
    {
        "id": "voice_24",
        "instruct": "middle-aged, male, very low pitch",
        "ref_text": "เรียนผู้เข้าร่วมประชุมทุกท่านครับ",
        "desc": "เสียงผู้ชายวัยกลางคน โทนต่ำมาก น่าเชื่อถือ",
    },
    {
        "id": "voice_25",
        "instruct": "elderly, male, very low pitch",
        "ref_text": "สมัยก่อนตอนพ่อยังหนุ่ม ชีวิตมันเรียบง่ายกว่านี้เยอะนะลูก",
        "desc": "เสียงผู้ชายสูงวัย โทนต่ำมาก ใจดี",
    },
    {
        "id": "voice_26",
        "instruct": "elderly, female, high pitch",
        "ref_text": "ยายดีใจมากเลยที่หลานมาเยี่ยม",
        "desc": "เสียงผู้หญิงสูงวัย โทนสูง อบอุ่น",
    },
    {
        "id": "voice_27",
        "instruct": "male, british accent",
        "ref_text": "Thank you for choosing us, we are happy to help.",
        "desc": "เสียงผู้ชาย สำเนียงอังกฤษ (British)",
    },
    {
        "id": "voice_28",
        "instruct": "male, american accent",
        "ref_text": "Hello, welcome to our service today.",
        "desc": "เสียงผู้ชาย สำเนียงอเมริกัน",
    },
    {
        "id": "voice_29",
        "instruct": "female, american accent",
        "ref_text": "Thank you for choosing us, we are happy to help.",
        "desc": "เสียงผู้หญิง สำเนียงอเมริกัน",
    },
    {
        "id": "voice_30",
        "instruct": "male, australian accent",
        "ref_text": "It is a beautiful day to start something new.",
        "desc": "เสียงผู้ชาย สำเนียงออสเตรเลีย",
    },
    {
        "id": "voice_31",
        "instruct": "female, australian accent",
        "ref_text": "Feel free to ask if you have any questions.",
        "desc": "เสียงผู้หญิง สำเนียงออสเตรเลีย",
    },
    {
        "id": "voice_32",
        "instruct": "male, whisper",
        "ref_text": "ฟังดีๆ นะครับ เรื่องนี้เป็นความลับระหว่างเรา",
        "desc": "เสียงผู้ชาย กระซิบ",
    },
    # --- ชุดเพิ่มเติมรอบ 2: เติมช่องว่าง ไม่ไล่โทนซ้ำ ---
    {
        "id": "voice_33",
        "instruct": "female, whisper",
        "ref_text": "ฟังดีๆ นะคะ เรื่องนี้เป็นความลับระหว่างเรา",
        "desc": "เสียงผู้หญิง กระซิบ",
    },
    {
        "id": "voice_34",
        "instruct": "teenager, female, low pitch",
        "ref_text": "เดี๋ยวนี้ก็แค่แชทหากันตลอดเลยเนอะ",
        "desc": "เสียงวัยรุ่นหญิง โทนต่ำ เท่ๆ",
    },
    {
        "id": "voice_35",
        "instruct": "teenager, male, moderate pitch",
        "ref_text": "วันนี้มีการบ้านเยอะมากเลยว่ะ",
        "desc": "เสียงวัยรุ่นชาย โทนกลาง",
    },
    {
        "id": "voice_36",
        "instruct": "middle-aged, female, high pitch",
        "ref_text": "ดิฉันขอเชิญทุกท่านร่วมงานในวันเสาร์นี้ค่ะ",
        "desc": "เสียงผู้หญิงวัยกลางคน โทนสูง สง่างาม",
    },
    {
        "id": "voice_37",
        "instruct": "child, very high pitch",
        "ref_text": "หนูอยากไปเที่ยวสวนสนุกจังเลย",
        "desc": "เสียงเด็ก โทนสูงมาก ซุกซน",
    },
    {
        "id": "voice_38",
        "instruct": "male, canadian accent",
        "ref_text": "Please hold on a moment while we process your request.",
        "desc": "เสียงผู้ชาย สำเนียงแคนาดา",
    },
    {
        "id": "voice_39",
        "instruct": "female, canadian accent",
        "ref_text": "Have a wonderful day ahead.",
        "desc": "เสียงผู้หญิง สำเนียงแคนาดา",
    },
    {
        "id": "voice_40",
        "instruct": "male, indian accent",
        "ref_text": "We hope you enjoy your experience with us.",
        "desc": "เสียงผู้ชาย สำเนียงอินเดีย",
    },
    {
        "id": "voice_41",
        "instruct": "female, indian accent",
        "ref_text": "It is a pleasure to meet you today.",
        "desc": "เสียงผู้หญิง สำเนียงอินเดีย",
    },
    {
        "id": "voice_42",
        "instruct": "male, chinese accent",
        "ref_text": "Let me share some good news with you.",
        "desc": "เสียงผู้ชาย สำเนียงจีน",
    },
    {
        "id": "voice_43",
        "instruct": "female, chinese accent",
        "ref_text": "Here is the information you requested.",
        "desc": "เสียงผู้หญิง สำเนียงจีน",
    },
    {
        "id": "voice_44",
        "instruct": "young adult, male, very high pitch",
        "ref_text": "โอ้โห เยี่ยมไปเลยครับ ดีใจด้วยจริงๆ",
        "desc": "เสียงผู้ชายวัยทำงานตอนต้น โทนสูงมาก ตื่นเต้น",
    },
    {
        "id": "voice_45",
        "instruct": "young adult, female, very low pitch",
        "ref_text": "ขอบคุณสำหรับความไว้วางใจนะคะ",
        "desc": "เสียงผู้หญิงวัยทำงานตอนต้น โทนต่ำมาก นุ่มลึก",
    },
    {
        "id": "voice_46",
        "instruct": "middle-aged, male, high pitch",
        "ref_text": "ผมมีเรื่องดีๆ มาบอกทุกคนครับ",
        "desc": "เสียงผู้ชายวัยกลางคน โทนสูง ร่าเริง",
    },
    {
        "id": "voice_47",
        "instruct": "elderly, female, very low pitch",
        "ref_text": "ยายอยู่ตรงนี้เสมอนะ ไม่ต้องห่วง",
        "desc": "เสียงผู้หญิงสูงวัย โทนต่ำมาก อ่อนโยน",
    },
    {
        "id": "voice_48",
        "instruct": "male, korean accent",
        "ref_text": "Have a wonderful day ahead.",
        "desc": "เสียงผู้ชาย สำเนียงเกาหลี",
    },
]

def main():
    p = argparse.ArgumentParser(description="สร้างคลังเสียงสต็อก (Voice Design)")
    p.add_argument("--device", default=None,
                   help="cuda / cpu (ดีฟอลต์: auto)")
    p.add_argument("--num_step", type=int, default=32,
                   help="diffusion steps ตอนสร้าง (สูง=คุณภาพดีสำหรับเสียงต้นแบบ)")
    args = p.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device == "cuda" else torch.float32
    os.makedirs(VOICES_DIR, exist_ok=True)

    print(f"โหลดโมเดล ({device}, {dtype})...")
    t = time.time()
    model = OmniVoice.from_pretrained(
        MODEL_DIR, device_map=device, dtype=dtype, low_cpu_mem_usage=True
    )
    print(f"โหลดเสร็จใน {time.time()-t:.1f}s")

    manifest = []
    for v in VOICE_PRESETS:
        wav_path = os.path.join(VOICES_DIR, f"{v['id']}.wav")
        if os.path.exists(wav_path):
            # มีไฟล์อยู่แล้ว → ข้าม (สร้างใหม่จะได้เสียงไม่เหมือนเดิม)
            print(f"[{v['id']}] มีไฟล์อยู่แล้ว ข้าม")
        else:
            print(f"\n[{v['id']}] สร้างเสียง: instruct='{v['instruct']}'")
            t = time.time()
            # Voice Design: ใช้ instruct สร้างเสียงต้นแบบ พูดประโยค ref_text
            audio = model.generate(
                text=v["ref_text"],
                instruct=v["instruct"],
                num_step=args.num_step,
            )
            sf.write(wav_path, audio[0], SAMPLE_RATE)
            dur = len(audio[0]) / SAMPLE_RATE
            print(f"  -> {wav_path} ({dur:.1f}s, ใช้ {time.time()-t:.1f}s)")

        manifest.append({
            "id": v["id"],
            "desc": v["desc"],
            "instruct": v["instruct"],
            "ref_audio": f"{v['id']}.wav",
            "ref_text": v["ref_text"],
        })

    manifest_path = os.path.join(VOICES_DIR, "voices.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] บันทึก manifest: {manifest_path} ({len(manifest)} เสียง)")
    print("ถัดไป: รัน server.py เพื่อเปิด API")


if __name__ == "__main__":
    main()
