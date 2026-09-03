"""
watermark.py — ฝัง audio watermark ที่หูมนุษย์ไม่ได้ยินลงในเสียงที่ generate ทุกตัว
ด้วย AudioSeal (Meta, CC-BY-4.0) — ตรวจสอบย้อนหลังได้ว่าไฟล์เสียงนี้มาจาก AI/ระบบเรา

สำคัญสำหรับบริการที่มีฟีเจอร์โคลนเสียง — ช่วยตรวจจับการเอาไปใช้หลอกลวง (deepfake) และ
รองรับกฎหมายบางประเทศที่เริ่มบังคับให้เสียง AI ต้องมีลายน้ำ

โหลดโมเดลแบบ lazy ครั้งแรกที่เรียกใช้ (เหมือน voice_similarity.py)
ถ้าไม่ได้ติดตั้ง audioseal หรือฝังไม่สำเร็จ → คืนเสียงเดิมไม่แก้ (ไม่ทำให้ request ล้ม)
ปิดได้ทั้งระบบด้วย env TTS_ENABLE_WATERMARK=0
"""
import os

import numpy as np

ENABLED = os.environ.get("TTS_ENABLE_WATERMARK", "1") == "1"

_model = None
_device = None
_disabled = False


def _get_model():
    global _model, _device, _disabled
    if _model is None and not _disabled:
        try:
            import torch
            import torch._dynamo
            from audioseal import AudioSeal

            # ปิด torch.compile ไปเลย — เครื่องที่ไม่มี triton (เช่น dev บน Windows) จะ error รัว
            # ทุก submodule ถ้าปล่อยให้มันลองคอมไพล์แล้วค่อย fallback (แค่ suppress_errors ยัง log เต็ม)
            # รันแบบ eager ตรงๆ เร็วพอสำหรับ watermark (โมเดลเล็กกว่า TTS/ASR มาก)
            torch._dynamo.config.disable = True

            _device = "cuda" if torch.cuda.is_available() else "cpu"
            _model = AudioSeal.load_generator("audioseal_wm_16bits").to(_device)
            _model.eval()
            print(f"[watermark] AudioSeal loaded ({_device})")
        except Exception as e:
            _disabled = True
            print(f"[watermark] ปิดใช้งาน (โหลดไม่สำเร็จ): {e}")
    return _model


def apply(wav: np.ndarray, sample_rate: int) -> np.ndarray:
    """ฝัง watermark ลง wav (float32 mono) — คืน wav ใหม่ ความยาวเท่าเดิม
    ล้มเหลว/ปิดใช้งานด้วย env → คืน wav เดิมเฉยๆ ไม่ throw (ไม่ให้ TTS ล่มเพราะ watermark)"""
    if not ENABLED:
        return wav
    model = _get_model()
    if model is None:
        return wav
    try:
        import torch

        with torch.no_grad():
            x = torch.from_numpy(np.asarray(wav, dtype=np.float32)).to(_device)
            x = x.reshape(1, 1, -1)  # (batch, channels, samples)
            # AudioSeal >=0.2 ไม่รับ/ไม่ resample ตาม sample_rate แล้ว (เป็น no-op, มี
            # deprecation warning ถ้าส่งไป) — โมเดลรองรับ 16k โดยตรง ไม่ต้อง resample เอง
            wm = model.get_watermark(x)
            out = (x + wm).reshape(-1).detach().cpu().numpy()
        return out.astype(np.float32)
    except Exception as e:
        print(f"[watermark] ฝังไม่สำเร็จ (ข้าม): {e}")
        return wav
