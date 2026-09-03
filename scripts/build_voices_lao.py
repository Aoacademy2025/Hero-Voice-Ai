"""
build_voices_lao.py — สร้างคลัง "เสียงสต็อกภาษาลาว" ล่วงหน้า (ทำครั้งเดียว, offline)

แยกเป็นสคริปต์/โฟลเดอร์/manifest ของตัวเอง (voices_lao/) — ไม่ปนกับ voices/voices.json
ของภาษาไทย/อังกฤษ (build_voices.py) กันสับสนเรื่อง id และการนับจำนวนเสียง

หมายเหตุสำคัญ: โมเดล OmniVoice ไม่มี "lao accent" อยู่ในลิสต์ accent ที่รองรับ
(ดู OmniVoice/omnivoice/utils/voice_design.py — american/british/australian/chinese/
canadian/indian/korean/portuguese/russian/japanese accent เท่านั้น) จึงใช้ instruct
แค่เพศ/อายุ/pitch แล้วให้ "language": "Lao" เป็นตัวกำหนดการออกเสียง — โมเดลจะออกเสียง
ตามข้อมูลฝึกภาษาลาวจริงของโมเดล (lo/lao มีอยู่ใน docs/lang_id_name_map.tsv) ไม่ใช่เสียง
ไทยพูดเลียนสำเนียงลาว

วิธีใช้:
  python build_voices_lao.py                 # สร้างทุกเสียงใน VOICE_PRESETS
  python build_voices_lao.py --device cuda   # ใช้ GPU (เร็วกว่ามาก)

หลังรันเสร็จจะได้:
  voices_lao/lao_01.wav, lao_02.wav, ...
  voices_lao/voices.json   (เก็บ id, ref_text, language, คำอธิบาย)
"""
import argparse
import json
import os
import time

import torch
import soundfile as sf
from omnivoice import OmniVoice

MODEL_DIR = r"C:\Users\USER\omnivoice\model"
VOICES_DIR = r"C:\Users\USER\omnivoice\voices_lao"
SAMPLE_RATE = 24000

# ─────────────────────────────────────────────────────────────────────
# สร้างมาแค่ไม่กี่เสียงก่อน (ตามที่ขอ) — เพิ่มเสียงอื่นในลิสต์นี้ได้ทีหลัง
#   instruct  : เพศ/อายุ/pitch/whisper เท่านั้น (ไม่มี "lao accent" ในโมเดล)
#   language  : "Lao" เสมอ — ให้โมเดลออกเสียง ref_text เป็นภาษาลาวจริง
#   ref_text  : ประโยคภาษาลาวที่จะให้เสียงนี้พูดตอนสร้าง (กลายเป็น ref_text ใช้ตอน clone)
# ─────────────────────────────────────────────────────────────────────
VOICE_PRESETS = [
    {
        "id": "lao_01",
        "instruct": "male",
        "language": "Lao",
        "ref_text": "ສະບາຍດີ ຍິນດີຕ້ອນຮັບເຂົ້າສູ່ບໍລິການຂອງພວກເຮົາ",
        "desc": "เสียงผู้ชาย ภาษาลาว โทนปกติ",
    },
    {
        "id": "lao_02",
        "instruct": "female",
        "language": "Lao",
        "ref_text": "ສະບາຍດີ ຍິນດີໃຫ້ບໍລິການທ່ານໃນມື້ນີ້",
        "desc": "เสียงผู้หญิง ภาษาลาว โทนปกติ",
    },
    {
        "id": "lao_03",
        "instruct": "young adult, male",
        "language": "Lao",
        "ref_text": "ຂອບໃຈທີ່ໃຊ້ບໍລິການ ຫວັງວ່າຈະໄດ້ຮ່ວມງານກັນອີກ",
        "desc": "เสียงผู้ชาย ภาษาลาว วัยทำงานตอนต้น",
    },
    {
        "id": "lao_04",
        "instruct": "female, high pitch",
        "language": "Lao",
        "ref_text": "ສະບາຍດີ ມີຫຍັງໃຫ້ຊ່ວຍບໍ່ ມື້ນີ້ອາກາດດີຫຼາຍເລີຍ",
        "desc": "เสียงผู้หญิง ภาษาลาว โทนสูง สดใส",
    },
    # --- ชุดเพิ่มเติมรอบ 2 ---
    {
        "id": "lao_05",
        "instruct": "elderly, male, very low pitch",
        "language": "Lao",
        "ref_text": "ສະບາຍດີລູກ ມື້ນີ້ເປັນແນວໃດແດ່",
        "desc": "เสียงผู้ชาย ภาษาลาว สูงวัย ใจดี",
    },
    {
        "id": "lao_06",
        "instruct": "teenager, female",
        "language": "Lao",
        "ref_text": "ສະບາຍດີເດີ ມື້ນີ້ອາກາດງາມຫຼາຍເນາະ",
        "desc": "เสียงผู้หญิง ภาษาลาว วัยรุ่น",
    },
    {
        "id": "lao_07",
        "instruct": "middle-aged, female, high pitch",
        "language": "Lao",
        "ref_text": "ຂໍເຊີນທ່ານຜູ້ມີກຽດທຸກທ່ານເຂົ້າຮ່ວມງານ",
        "desc": "เสียงผู้หญิง ภาษาลาว วัยกลางคน สง่างาม",
    },
    # เอาเสียงกระซิบออก (lao_08 male/whisper) — เหมือนที่เอาออกฝั่งไทยไปแล้ว (ดูคอมเมนต์ใน
    # build_voices.py) ไฟล์ .wav เดิมยังอยู่ใน voices_lao/ เผื่อเอากลับมาใช้ทีหลัง แค่ไม่สร้างซ้ำ/
    # ไม่เสิร์ฟจาก server.py แล้ว (ลบ entry ออกจาก voices_lao/voices.json ด้วย)
]


def main():
    p = argparse.ArgumentParser(description="สร้างคลังเสียงสต็อกภาษาลาว (Voice Design)")
    p.add_argument("--device", default=None, help="cuda / cpu (ดีฟอลต์: auto)")
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
            print(f"[{v['id']}] มีไฟล์อยู่แล้ว ข้าม")
        else:
            print(f"\n[{v['id']}] สร้างเสียง: instruct='{v['instruct']}' language='{v['language']}'")
            t = time.time()
            audio = model.generate(
                text=v["ref_text"],
                instruct=v["instruct"],
                language=v["language"],
                num_step=args.num_step,
            )
            sf.write(wav_path, audio[0], SAMPLE_RATE)
            dur = len(audio[0]) / SAMPLE_RATE
            print(f"  -> {wav_path} ({dur:.1f}s, ใช้ {time.time()-t:.1f}s)")

        manifest.append({
            "id": v["id"],
            "desc": v["desc"],
            "instruct": v["instruct"],
            "language": v["language"],
            "ref_audio": f"{v['id']}.wav",
            "ref_text": v["ref_text"],
        })

    manifest_path = os.path.join(VOICES_DIR, "voices.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] บันทึก manifest: {manifest_path} ({len(manifest)} เสียง)")


if __name__ == "__main__":
    main()
