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
#        [--join N] รวม N บรรทัดเป็นท่อนเดียว (ลดจำนวนรอยต่อ)   [--steps S] inference_timesteps (ดีฟอลต์ 10)
#        [--continue] ต่อเนื่อง: ใช้เสียง+ข้อความของท่อนก่อนหน้าเป็น prompt ของท่อนถัดไป (ลดอาการ "เริ่มพูดใหม่" ที่รอยต่อ)
import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("script", nargs="?", default=os.path.join(_BASE, "data", "scripts", "hero_promo.txt"))
_ap.add_argument("out", nargs="?")
_ap.add_argument("--join", type=int, default=1)
_ap.add_argument("--steps", type=int, default=10)
_ap.add_argument("--continue", dest="cont", action="store_true")
_a = _ap.parse_args()
SCRIPT_FILE = _a.script
OUT_NAME = _a.out or os.path.splitext(os.path.basename(SCRIPT_FILE))[0] + "_voice02.wav"
SCRIPT = open(SCRIPT_FILE, encoding="utf-8").read()

lines = [c.strip() for c in SCRIPT.split("\n") if c.strip()]
chunks = [" ".join(lines[i:i + _a.join]) for i in range(0, len(lines), _a.join)]
print(f"{len(chunks)} chunks (join={_a.join}, steps={_a.steps}, continue={_a.cont}), {len(SCRIPT)} chars", flush=True)

t0 = time.time()
model = load_voxcpm2("cuda")
print(f"load: {time.time()-t0:.1f}s", flush=True)

REF_WAV, REF_TEXT = os.path.join(_BASE, "voices", "voice_02.wav"), "สวัสดีค่ะ ยินดีให้บริการนะคะ"
pieces, total_gen = [], 0.0
if _a.cont:
    # prompt cache ของเสียงอ้างอิง (โหมด continuation: prompt_text + prompt_wav) ใช้เป็นฐานทุกท่อน
    tts = model.tts_model
    base_cache = tts.build_prompt_cache(prompt_text=REF_TEXT, prompt_wav_path=REF_WAV)
    cache = base_cache
for i, c in enumerate(chunks):
    t = time.time()
    if _a.cont:
        wav_t, _, gen_feat = tts.generate_with_prompt_cache(
            target_text=c, prompt_cache=cache, cfg_value=2.0, inference_timesteps=_a.steps)
        wav = wav_t.squeeze(0).cpu().numpy()
        # ท่อนถัดไปใช้ prompt = เสียงอ้างอิง + ท่อนที่เพิ่งพูดจบ (ไม่สะสมทุกท่อน กัน cache โตและเสียง drift)
        cache = tts.merge_prompt_cache(base_cache, c, gen_feat)
    else:
        wav = model.generate(text=c, cfg_value=2.0, inference_timesteps=_a.steps,
                             prompt_wav_path=REF_WAV, prompt_text=REF_TEXT)
    dt = time.time() - t; total_gen += dt
    print(f"chunk {i+1}: gen {dt:.1f}s, audio {len(wav)/SR:.1f}s", flush=True)
    sf.write(os.path.join(OUT, f"{os.path.splitext(OUT_NAME)[0]}_chunk{i+1:02d}.wav"), wav, SR)
    pieces.append(wav.astype(np.float32))

# ตัดเงียบหัว/ท้ายให้เท่ากัน + ช่องว่างคงที่ 0.45s (ดู stitch_chunks.py)
full = stitch(pieces, SR, gap=0.45)
sf.write(os.path.join(OUT, OUT_NAME), full, SR)
print(f"done: total audio {len(full)/SR:.1f}s, total gen {total_gen:.1f}s", flush=True)
