"""
audio_enhance.py — ลดเสียงรบกวนพื้นหลัง + เพิ่มความชัดของเสียงพูด ด้วย Demucs (Meta, MIT)

ใช้แยกแทร็กเสียงพูด (vocals) ออกจากเสียงพื้นหลัง/ดนตรี/สัญญาณรบกวนอื่นๆ แล้วปรับความดังให้
สม่ำเสมอ — เอาไว้ทำความสะอาดไฟล์เสียง ref ที่ผู้ใช้อัปโหลดก่อนโคลนเสียง (คุณภาพ ref ดีขึ้น →
เสียงโคลนดีขึ้น) และใช้กับ endpoint /enhance แบบเดี่ยวๆ ได้ด้วย

โหลดโมเดลแบบ lazy ครั้งแรกที่เรียกใช้ (เหมือน voice_similarity.py/watermark.py)
ถ้าไม่ได้ติดตั้ง demucs หรือประมวลผลไม่สำเร็จ → คืนเสียงเดิมไม่แก้ (ไม่ทำให้ request ล้ม)
"""
import os

import numpy as np

_separator = None
_disabled = False

MODEL_NAME = os.environ.get("TTS_ENHANCE_MODEL", "htdemucs")


def _get_separator():
    global _separator, _disabled
    if _separator is None and not _disabled:
        try:
            import torch
            from demucs.api import Separator

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _separator = Separator(model=MODEL_NAME, device=device, progress=False)
            print(f"[enhance] Demucs ({MODEL_NAME}) loaded ({device})")
        except Exception as e:
            _disabled = True
            print(f"[enhance] ปิดใช้งาน (โหลดไม่สำเร็จ): {e}")
    return _separator


def _normalize_peak(wav: np.ndarray, target: float = 0.95) -> np.ndarray:
    peak = np.abs(wav).max()
    if peak > 1e-6:
        wav = wav * (target / peak)
    return wav


def enhance(input_path: str) -> tuple[np.ndarray, int]:
    """
    ลดเสียงรบกวนพื้นหลัง + normalize ความดัง — คืน (wav float32 mono, sample_rate)
    ล้มเหลว/ปิดใช้งาน → โหลดไฟล์เดิมกลับมาตรงๆ ไม่แก้อะไร (ไม่ throw)
    """
    separator = _get_separator()
    if separator is not None:
        try:
            import torch

            _, stems = separator.separate_audio_file(input_path)
            vocals = stems["vocals"]  # (channels, samples) torch.Tensor
            wav = vocals.mean(dim=0).detach().cpu().numpy().astype(np.float32)  # → mono
            wav = _normalize_peak(wav)
            return wav, separator.samplerate
        except Exception as e:
            print(f"[enhance] แยกเสียงไม่สำเร็จ (ข้าม, ใช้ไฟล์เดิม): {e}")

    import soundfile as sf
    wav, sr = sf.read(input_path, dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    return _normalize_peak(wav), sr
