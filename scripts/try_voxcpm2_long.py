"""VoxCPM2 อ่านสคริปต์ไทยยาว ~1 นาที ด้วยเสียงโคลน voice_02 — ตัดเป็นท่อน แล้วต่อด้วย silence สั้น ๆ"""
import os, sys, time, re
import numpy as np
import soundfile as sf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voxcpm2_compat import load_voxcpm2
from stitch_chunks import stitch

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_BASE, "ab_output", "voxcpm2")
SR = 48000

# ใช้: python scripts/try_voxcpm2_long.py [ไฟล์ข้อความ (1 บรรทัด = 1 ท่อน)] [ชื่อไฟล์ output]
SCRIPT_FILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_BASE, "data", "scripts", "hero_promo.txt")
OUT_NAME = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(os.path.basename(SCRIPT_FILE))[0] + "_voice02.wav"
SCRIPT = open(SCRIPT_FILE, encoding="utf-8").read()

chunks = [c.strip() for c in SCRIPT.split("\n") if c.strip()]
print(f"{len(chunks)} chunks, {len(SCRIPT)} chars", flush=True)

t0 = time.time()
model = load_voxcpm2("cuda")
print(f"load: {time.time()-t0:.1f}s", flush=True)

pieces, total_gen = [], 0.0
for i, c in enumerate(chunks):
    t = time.time()
    wav = model.generate(text=c, cfg_value=2.0, inference_timesteps=10,
                         prompt_wav_path=os.path.join(_BASE, "voices", "voice_02.wav"),
                         prompt_text="สวัสดีค่ะ ยินดีให้บริการนะคะ")
    dt = time.time() - t; total_gen += dt
    print(f"chunk {i+1}: gen {dt:.1f}s, audio {len(wav)/SR:.1f}s", flush=True)
    sf.write(os.path.join(OUT, f"{os.path.splitext(OUT_NAME)[0]}_chunk{i+1:02d}.wav"), wav, SR)
    pieces.append(wav.astype(np.float32))

# ตัดเงียบหัว/ท้ายให้เท่ากัน + ช่องว่างคงที่ 0.45s (ดู stitch_chunks.py)
full = stitch(pieces, SR, gap=0.45)
sf.write(os.path.join(OUT, OUT_NAME), full, SR)
print(f"done: total audio {len(full)/SR:.1f}s, total gen {total_gen:.1f}s", flush=True)
