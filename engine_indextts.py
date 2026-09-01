"""
engine_indextts.py — เอนจินที่ 2: IndexTTS-2 (cloning เหมือนสูง)

เป็น optional engine — โหลดเฉพาะเมื่อ:
  1) ตั้ง env TTS_ENABLE_INDEXTTS=1
  2) ติดตั้งแพ็กเกจ indextts + ดาวน์โหลด checkpoint แล้ว
ถ้าโหลดไม่ได้ server จะข้ามไป (OmniVoice ยังทำงานปกติ)

ติดตั้งบน RunPod (GPU):
  pip install indextts            # หรือจาก repo index-tts/index-tts
  # ดาวน์โหลด checkpoint ไป /models/indextts2
  export TTS_ENABLE_INDEXTTS=1
  export INDEXTTS_MODEL_DIR=/models/indextts2
  export INDEXTTS_CFG=/models/indextts2/config.yaml

จุดที่อาจต้องปรับตามเวอร์ชัน: เมธอด infer() ของ IndexTTS2 (ดู _synth_to_file)
— รวมการเรียก vendor ไว้ที่เดียวเพื่อแก้ง่าย

interface ให้ตรงกับที่ server เรียก:
  .id .name .sample_rate .supports_clone .supports_design
  .load()
  .list_voices() -> []                      (ไม่มีเสียงสต็อกของตัวเอง)
  .synth(text, ref_wav, ref_text=None, speed=1.0) -> (np.ndarray float32, 24000)
"""
import io
import os
import tempfile

import numpy as np
import soundfile as sf

import watermark

SAMPLE_RATE = 24000
MODEL_DIR = os.environ.get("INDEXTTS_MODEL_DIR", "/models/indextts2")
CFG_PATH = os.environ.get("INDEXTTS_CFG", os.path.join(MODEL_DIR, "config.yaml"))
USE_FP16 = os.environ.get("INDEXTTS_FP16", "1") == "1"


class IndexTTS2Engine:
    id = "indextts2"
    name = "IndexTTS-2"
    sample_rate = SAMPLE_RATE
    supports_clone = True
    supports_design = False

    def __init__(self):
        self.tts = None
        self.device = None
        self.voices = {}      # ไม่มีเสียงสต็อกของตัวเอง (ใช้ ref จากคลังกลาง)

    def load(self):
        import torch
        from indextts.infer_v2 import IndexTTS2  # โหลด lazy — ถ้าไม่ติดตั้งจะ ImportError

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if not os.path.exists(CFG_PATH):
            raise RuntimeError(f"IndexTTS config ไม่พบที่ {CFG_PATH} (ตั้ง INDEXTTS_CFG/INDEXTTS_MODEL_DIR)")
        print(f"[indextts2] loading ({self.device}) from {MODEL_DIR} ...")
        self.tts = IndexTTS2(cfg_path=CFG_PATH, model_dir=MODEL_DIR, use_fp16=USE_FP16)
        print("[indextts2] ready")

    def list_voices(self):
        return []

    def _synth_to_file(self, text, ref_wav, out_path):
        """เรียก vendor API — รวมไว้ที่เดียว (ปรับตามเวอร์ชัน IndexTTS ที่ติดตั้งได้)"""
        self.tts.infer(spk_audio_prompt=ref_wav, text=text, output_path=out_path, verbose=False)

    def synth(self, text, ref_wav, ref_text=None, speed=1.0):
        """สร้างเสียง → คืน (wav float32 @24k, 24000). ref_text ไม่จำเป็นสำหรับ IndexTTS"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name
        try:
            self._synth_to_file(text, ref_wav, out_path)
            wav, sr = sf.read(out_path, dtype="float32")
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)
        if wav.ndim > 1:                     # stereo → mono
            wav = wav.mean(axis=1)
        if sr != SAMPLE_RATE:                # resample ให้เป็น 24k (canonical ของระบบ)
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=SAMPLE_RATE)
        if abs(speed - 1.0) > 1e-3:          # ปรับความเร็ว (คง pitch)
            import librosa
            wav = librosa.effects.time_stretch(wav, rate=speed)
        wav = watermark.apply(np.asarray(wav, dtype=np.float32), SAMPLE_RATE)
        return wav, SAMPLE_RATE
