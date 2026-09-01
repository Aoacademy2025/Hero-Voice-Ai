"""
test_indextts_emotion.py — สโมคเทส IndexTTS-2 (โคลนเสียง + คุมอารมณ์จริง)

รันใน venv_indextts (ดู setup_indextts2.sh) ไม่ใช่ venv/ หลัก:
  PYTHONUNBUFFERED=1 venv_indextts/Scripts/python.exe -u test_indextts_emotion.py

หมายเหตุจากรอบทดลองก่อนหน้า:
- ต้อง unbuffered (-u / PYTHONUNBUFFERED=1) ไม่งั้นถ้า crash กลางทาง จะไม่เห็น log
  อะไรเลยเพราะ stdout ถูก buffer ไว้ยังไม่ทัน flush
- ต้องปิด/ไม่มี process อื่นใช้ GPU ค้างอยู่ก่อนรัน (เช็คด้วย nvidia-smi ว่า memory.used ~0)
  ไม่งั้น VRAM ไม่พอ (IndexTTS-2 ต้องการ ~6GB, การ์ดเรามีเท่านั้นพอดี)
- torchaudio เวอร์ชันใหม่ (2.11+) พึ่ง torchcodec ซึ่งต้องมี FFmpeg จริงที่เครื่องไม่มี —
  monkeypatch ให้ใช้ soundfile เขียนไฟล์แทน (เหมือนที่โปรเจกต์หลักใช้อยู่แล้ว)
"""
import sys
sys.path.insert(0, ".")

import torchaudio
import soundfile as sf


def _save_via_soundfile(uri, src, sample_rate, *args, **kwargs):
    arr = src.detach().cpu().numpy()
    if arr.ndim == 2:
        arr = arr.T if arr.shape[0] <= 8 else arr  # (channels, frames) -> (frames, channels)
    sf.write(uri, arr, sample_rate)


torchaudio.save = _save_via_soundfile

from indextts.infer_v2 import IndexTTS2

print("[test] loading IndexTTS2 ...", flush=True)
tts = IndexTTS2(cfg_path="indextts2_model/config.yaml", model_dir="indextts2_model", use_fp16=True)
print("[test] loaded, generating happy sample ...", flush=True)

tts.infer(
    spk_audio_prompt="voices/voice_02.wav",
    text="วันนี้เป็นวันที่ดีที่สุดในชีวิตของผมเลยครับ",
    output_path="demo_indextts_happy.wav",
    use_emo_text=True,
    emo_text="happy",
    verbose=True,
)
print("[test] done -> demo_indextts_happy.wav", flush=True)
