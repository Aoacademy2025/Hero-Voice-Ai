"""
indextts_service.py — IndexTTS-2 เป็น microservice แยกจาก server.py หลัก

เหตุผลที่แยก: indextts ต้องการ torch เวอร์ชันใหม่กว่าที่ OmniVoice (venv/ หลัก) ใช้
— import รวม process เดียวกันจะชน (ABI/เวอร์ชันขัดกัน) ต้องรันคนละ process/venv
engine_indextts.py ในโปรเจกต์หลักเรียก service นี้ผ่าน HTTP แทนการ import ตรง

รัน (ต้องมี venv_indextts พร้อมแล้ว — ดู setup_indextts2.sh):
  PYTHONIOENCODING=utf-8 venv_indextts/Scripts/python.exe indextts_service.py

ปรับได้ด้วย env:
  INDEXTTS_MODEL_DIR    ดีฟอลต์ ./indextts2_model
  INDEXTTS_CFG          ดีฟอลต์ <model_dir>/config.yaml
  INDEXTTS_SERVICE_PORT ดีฟอลต์ 8001
  INDEXTTS_FP16         ดีฟอลต์ 1

หมายเหตุจากรอบทดลอง (ดู test_indextts_emotion.py คอมเมนต์ประกอบ):
  - ต้องรันด้วย PYTHONIOENCODING=utf-8 ไม่งั้น debug print ของ indextts เองจะ
    UnicodeEncodeError ตายกลางทางบน Windows console (cp874)
  - torchaudio ใหม่พึ่ง torchcodec/FFmpeg ที่เครื่องนี้ไม่มี — monkeypatch ให้ใช้
    soundfile เขียนไฟล์แทน (เหมือนโปรเจกต์หลักใช้ soundfile อยู่แล้วทุกที่)
  - VRAM การ์ดนี้ (6GB) พอสำหรับ IndexTTS-2 ตัวเดียวเท่านั้น — ต้องปิด OmniVoice
    server หลักก่อนรัน service นี้เสมอถ้าอยู่การ์ดเดียวกัน
"""
import io
import os
import tempfile

import soundfile as sf
import torchaudio


def _save_via_soundfile(uri, src, sample_rate, *args, **kwargs):
    arr = src.detach().cpu().numpy()
    if arr.ndim == 2:
        arr = arr.T if arr.shape[0] <= 8 else arr  # (channels, frames) -> (frames, channels)
    sf.write(uri, arr, sample_rate)


torchaudio.save = _save_via_soundfile  # ต้องแพตช์ก่อน import indextts

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import Response

from indextts.infer_v2 import IndexTTS2

MODEL_DIR = os.environ.get("INDEXTTS_MODEL_DIR", os.path.join(os.path.dirname(__file__), "indextts2_model"))
CFG_PATH = os.environ.get("INDEXTTS_CFG", os.path.join(MODEL_DIR, "config.yaml"))
USE_FP16 = os.environ.get("INDEXTTS_FP16", "1") == "1"
PORT = int(os.environ.get("INDEXTTS_SERVICE_PORT", "8001"))
SAMPLE_RATE = 24000

app = FastAPI(title="IndexTTS-2 Service")
_tts = None


@app.on_event("startup")
def _load():
    global _tts
    print(f"[indextts-service] loading model from {MODEL_DIR} ...")
    _tts = IndexTTS2(cfg_path=CFG_PATH, model_dir=MODEL_DIR, use_fp16=USE_FP16)
    print("[indextts-service] ready")


@app.get("/health")
def health():
    return {"status": "ok" if _tts is not None else "loading"}


@app.post("/synth")
async def synth(
    text: str = Form(...),
    ref_audio: UploadFile = File(...),
    emotion: str = Form(None),
    speed: float = Form(1.0),
    emo_alpha: float = Form(1.0),
    # emo_alpha คุมความแรงของอารมณ์ (0-1) — ดีฟอลต์ 1.0 ของโมเดลออกแรงเต็มที่จนเสียงไม่เป็นธรรมชาติ
    # ลองค่าต่ำกว่านี้ (เช่น 0.6) ถ้าอยากให้ฟังดูธรรมชาติขึ้นแต่ยังได้ยินอารมณ์อยู่
):
    """สร้างเสียง → คืนไฟล์ wav (binary) ตรงๆ — ไม่ต้อง base64 (คุยกันในเครื่องเดียว/LAN)"""
    suffix = os.path.splitext(ref_audio.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await ref_audio.read())
        ref_path = tmp.name
    out_path = tempfile.mktemp(suffix=".wav")
    try:
        kwargs = dict(spk_audio_prompt=ref_path, text=text, output_path=out_path, verbose=False)
        if emotion:
            kwargs.update(use_emo_text=True, emo_text=emotion)  # คุมอารมณ์ด้วยคำบรรยาย
        _tts.infer(**kwargs)
        wav, sr = sf.read(out_path, dtype="float32")
    finally:
        for p in (ref_path, out_path):
            if os.path.exists(p):
                os.remove(p)

    if wav.ndim > 1:  # stereo -> mono
        wav = wav.mean(axis=1)
    if sr != SAMPLE_RATE:
        import librosa
        wav = librosa.resample(wav, orig_sr=sr, target_sr=SAMPLE_RATE)
        sr = SAMPLE_RATE
    if abs(speed - 1.0) > 1e-3:  # ปรับความเร็ว (คง pitch) — ทำที่นี่ ไม่ใช่ฝั่ง client
        import librosa
        wav = librosa.effects.time_stretch(wav, rate=speed)

    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
