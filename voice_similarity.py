"""
voice_similarity.py — วัดความคล้ายเสียง (speaker similarity) ด้วย Resemblyzer

ใช้เทียบเสียงที่ generate กับ ref_audio ต้นฉบับ ให้คะแนน cosine similarity (0-1, ยิ่งสูงยิ่งคล้าย)
เอาไว้เลือกตัวที่คล้ายที่สุดจากหลาย candidate (best-of-N) ตอน voice cloning — ดู server.py /clone

โหลดโมเดล (VoiceEncoder) แบบ lazy ครั้งแรกที่เรียกใช้ ไม่กระทบ startup time ของ server
ถ้าไม่ได้ติดตั้ง resemblyzer/webrtcvad — import จะ error เฉพาะตอนเรียกใช้ฟีเจอร์นี้เท่านั้น
"""
import numpy as np

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder()
    return _encoder


def embed_file(path: str) -> np.ndarray:
    """สร้าง speaker embedding จากไฟล์เสียง"""
    from resemblyzer import preprocess_wav
    wav = preprocess_wav(path)
    return _get_encoder().embed_utterance(wav)


def embed_array(wav: np.ndarray, sr: int) -> np.ndarray:
    """สร้าง speaker embedding จาก waveform ในแรม (float32, mono)"""
    from resemblyzer import preprocess_wav
    wav = preprocess_wav(wav, source_sr=sr)
    return _get_encoder().embed_utterance(wav)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """คืนค่า cosine similarity ระหว่าง embedding สองตัว (0-1)"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
