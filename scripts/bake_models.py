"""ดาวน์โหลดโมเดลทั้งหมดลง image ตอน docker build (BAKE_MODELS=1) — ใช้กับ image Serverless
ให้ worker เริ่มได้ทันทีโดยไม่ต้องโหลด ~9GB ทุก cold start"""
import os

from huggingface_hub import snapshot_download

snapshot_download("k2-fsa/OmniVoice", local_dir=os.environ["TTS_MODEL_DIR"])
if "voxcpm2" in os.environ.get("TTS_ENGINES", ""):
    snapshot_download("openbmb/VoxCPM2")
# ASR ที่ใช้ตรวจว่าอ่านตรงสคริปต์ (asr_engine.py) — เก็บใน HF cache เดียวกัน
from faster_whisper import WhisperModel
WhisperModel(os.environ.get("TTS_ASR_MODEL", "large-v3-turbo"), device="cpu", compute_type="int8")
print("baked models into", os.environ["TTS_MODEL_DIR"], "and", os.environ["HF_HOME"])
