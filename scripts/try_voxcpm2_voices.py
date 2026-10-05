"""VoxCPM2 อ่านย่อหน้าเดียวกันด้วยเสียงสต็อกหลายตัว (โคลนจาก voices/) + voice design ภาษาอังกฤษ"""
import os, sys, time, json
import soundfile as sf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voxcpm2_compat import load_voxcpm2
from stitch_chunks import trim_silence, fade

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_BASE, "ab_output", "voxcpm2", "voices")
os.makedirs(OUT, exist_ok=True)
SR = 48000

TEXT = ("เช้าวันนี้ท้องฟ้าแจ่มใส อุณหภูมิประมาณ 24 องศา เหมาะกับการออกไปเดินเล่นริมแม่น้ำ "
        "ถ้าใครยังไม่ได้วางแผน ลองแวะร้านกาแฟเล็ก ๆ แถวสะพานเก่าดูนะคะ เขาเปิดตั้งแต่ 7 โมงเช้าค่ะ")

STOCK = ["voice_01", "voice_05", "voice_07", "voice_12", "voice_14", "voice_15", "voice_23", "voice_45"]
DESIGNS = [
    ("design_announcer_male", "(an energetic male radio announcer, bright and confident voice, medium-fast pace)"),
    ("design_elderly_female", "(an elderly woman, warm, slow, slightly raspy storytelling voice)"),
    ("design_whisper_female", "(a young woman whispering softly, intimate ASMR style)"),
]

voices = {v["id"]: v for v in json.load(open(os.path.join(_BASE, "voices", "voices.json"), encoding="utf-8"))}
t0 = time.time(); model = load_voxcpm2("cuda"); print(f"load: {time.time()-t0:.1f}s", flush=True)

def save(name, wav, dt):
    wav = fade(trim_silence(wav.astype("float32"), SR), SR)
    sf.write(os.path.join(OUT, name + ".wav"), wav, SR)
    print(f"{name}: gen {dt:.1f}s, audio {len(wav)/SR:.1f}s", flush=True)

for vid in STOCK:
    v = voices[vid]; t = time.time()
    wav = model.generate(text=TEXT, cfg_value=2.0, inference_timesteps=10,
                         prompt_wav_path=os.path.join(_BASE, "voices", v["ref_audio"]), prompt_text=v["ref_text"])
    save(f"{vid}_{v['instruct'].replace(', ', '_').replace(' ', '-')}", wav, time.time() - t)

for name, desc in DESIGNS:
    t = time.time()
    wav = model.generate(text=desc + TEXT, cfg_value=2.0, inference_timesteps=10)
    save(name, wav, time.time() - t)
print("done", flush=True)
