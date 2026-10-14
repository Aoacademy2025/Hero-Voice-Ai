"""
ลดเสียงก้องหลังสร้างเสียงเสร็จ ด้วย WPE (Weighted Prediction Error, nara_wpe)

ที่มา: เสียง voice_02 (นภา) บน VoxCPM2 ก้องในบางสคริปต์ (เช่นโฆษณา) แม้เปลี่ยนไฟล์อ้างอิงแล้ว —
ทดสอบแล้วไม่ได้มาจากลายน้ำ/mp3/torch.compile/เครื่อง Serverless ผู้ใช้ฟังแล้วเลือก WPE taps=20
บนไฟล์ทั้งไฟล์ ("Serverless ลดก้องระดับแรง อันนี้ใช้ได้") → ใช้ค่านี้เป็นค่าเริ่มต้น

WPE ประเมินเสียงก้องที่ค้างตามหลังจากเฟรมก่อนหน้าแล้วหักออก ไฟล์ยิ่งยาวยิ่งได้ผล (ไฟล์อ้างอิง 2s แทบไม่ได้ผล
แต่ไฟล์ 57s ได้ผลชัด) จึงใช้กับเสียงที่ต่อทุกท่อนแล้ว ไม่ใช่ทีละท่อน
ไม่ได้ติดตั้ง nara_wpe → คืนเสียงเดิม (ไม่ทำให้งานล้ม)
"""
import time

import numpy as np

_FFT, _SHIFT = 1024, 256
_FREQ_BATCH = 64


def apply(wav: np.ndarray, sample_rate: int, taps: int = 20, delay: int = 3, iterations: int = 3) -> np.ndarray:
    if taps <= 0 or len(wav) < sample_rate:  # สั้นกว่า 1 วินาที ประเมินก้องไม่ได้
        return wav
    try:
        from nara_wpe.wpe import wpe
        from nara_wpe.utils import stft, istft
    except ImportError as e:
        print(f"[dereverb] ข้าม (ไม่มี nara_wpe): {e}")
        return wav
    t = time.time()
    x = np.asarray(wav, dtype=np.float32)
    Y = stft(x[None, :], size=_FFT, shift=_SHIFT).transpose(2, 0, 1)          # (freq, ch, frames)
    Z, dev = None, "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            # GPU: เร็วกว่า CPU ~7 เท่า ผลเท่ากัน (ต่าง -73 dB) — WPE แต่ละย่านความถี่อิสระกัน จึงแบ่งทำทีละ
            # _FREQ_BATCH ย่าน กัน VRAM ไม่พอ (ทำทั้งหมดพร้อมกันกิน >3GB กับเสียง 60 วินาที)
            from nara_wpe.torch_wpe import wpe_v6
            Yt = torch.from_numpy(Y).to("cuda")
            Z = np.concatenate([wpe_v6(Yt[f:f + _FREQ_BATCH], taps=taps, delay=delay, iterations=iterations,
                                       statistics_mode="full").cpu().numpy()
                                for f in range(0, Yt.shape[0], _FREQ_BATCH)], axis=0)
            del Yt
            dev = "cuda"
    except Exception as e:  # VRAM ไม่พอ/torch_wpe ใช้ไม่ได้ → ทำบน CPU แทน
        print(f"[dereverb] GPU ใช้ไม่ได้ ({type(e).__name__}) → CPU", flush=True)
        Z = None
    if Z is None:
        Z = wpe(Y, taps=taps, delay=delay, iterations=iterations, statistics_mode="full")
    Z = Z.transpose(1, 2, 0)
    y = istft(Z, size=_FFT, shift=_SHIFT)[0][: len(x)]
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    peak_in, peak_out = float(np.abs(x).max()), float(np.abs(y).max()) + 1e-9
    y = (y * (peak_in / peak_out)).astype(np.float32)                           # คงระดับเสียงเดิม
    print(f"[dereverb] WPE taps={taps} {len(x)/sample_rate:.1f}s in {time.time()-t:.1f}s ({dev})", flush=True)
    return y
