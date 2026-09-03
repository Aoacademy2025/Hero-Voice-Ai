"""
asr_engine.py — ASR (ถอดเสียง) ด้วย faster-whisper (CTranslate2 backend)

เร็วกว่า transformers Whisper เดิม (ที่ผูกกับ OmniVoice model) หลายเท่าที่คุณภาพเท่ากัน
เพราะรันผ่าน CTranslate2 (int8/float16) แทน PyTorch ตรงๆ — สำคัญเพราะ /transcribe และ
auto ref_text ตอน /clone, /voices เรียก path นี้ทุกครั้ง

โหลดโมเดลแบบ lazy ครั้งแรกที่เรียกใช้ (เหมือน voice_similarity.py) — ไม่กระทบ startup
ปรับได้ด้วย env:
  TTS_ASR_MODEL         ดีฟอลต์ "large-v3-turbo" (ชื่อโมเดลของ faster-whisper —
                        https://github.com/SYSTRAN/faster-whisper)
  TTS_ASR_DEVICE        ดีฟอลต์ auto (cuda ถ้ามี ไม่งั้น cpu)
  TTS_ASR_COMPUTE_TYPE  ดีฟอลต์ float16 (cuda) / int8 (cpu)
"""
import os

MODEL_NAME = os.environ.get("TTS_ASR_MODEL", "large-v3-turbo")

_model = None


def _get_model():
    global _model
    if _model is None:
        import torch
        from faster_whisper import WhisperModel

        device = os.environ.get("TTS_ASR_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
        compute_type = os.environ.get("TTS_ASR_COMPUTE_TYPE") or ("float16" if device == "cuda" else "int8")
        print(f"[asr] loading faster-whisper: {MODEL_NAME} ({device}, {compute_type}) ...")
        _model = WhisperModel(MODEL_NAME, device=device, compute_type=compute_type)
        print("[asr] ready")
    return _model


def transcribe(audio_path: str) -> str:
    """ถอดเสียงไฟล์ → ข้อความ (ต่อทุก segment เข้าด้วยกัน)"""
    segments, _info = _get_model().transcribe(audio_path, beam_size=5)
    return "".join(seg.text for seg in segments).strip()
