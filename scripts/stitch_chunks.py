"""ต่อไฟล์เสียงหลายท่อนให้รอยต่อสม่ำเสมอ
- ตัดช่วงเงียบหัว/ท้ายแต่ละท่อน (โมเดลสร้างเงียบไม่เท่ากัน 0.1-0.5 วินาที) ให้เหลือ pad คงที่
- fade in/out สั้น ๆ กันขอบกระด้าง
- แทรกช่องว่างคงที่ระหว่างท่อน (ปรับได้ด้วย --gap) หรือตามที่ระบุต่อท่อนใน gaps

ใช้: python scripts/stitch_chunks.py <prefix ของไฟล์ท่อน> <ไฟล์ออก> [--gap 0.45]
เช่น: python scripts/stitch_chunks.py ab_output/voxcpm2/kafae_doi_voice02_chunk ab_output/voxcpm2/kafae_doi_voice02_v2.wav
"""
import argparse, glob
import numpy as np
import soundfile as sf

def _rms_env(x, sr, win=0.01):
    w = max(1, int(sr * win))
    return np.sqrt(np.convolve(x.astype(np.float64) ** 2, np.ones(w) / w, "same"))

def trim_silence(x, sr, thr_db=-45.0, pad=0.06):
    """ตัดเงียบหัว/ท้าย เหลือ pad วินาที (ถ้ามีเงียบน้อยกว่า pad ก็ไม่เติม)"""
    env = _rms_env(x, sr)
    idx = np.where(env > 10 ** (thr_db / 20))[0]
    if len(idx) == 0:
        return x
    p = int(sr * pad)
    s, e = max(0, idx[0] - p), min(len(x), idx[-1] + p)
    return x[s:e]

def fade(x, sr, ms=8):
    n = min(len(x) // 2, int(sr * ms / 1000))
    if n <= 0:
        return x
    x = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=x.dtype)
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x

def stitch(chunks, sr, gap=0.45, gaps=None, pad=0.06):
    """chunks: list ของ np.ndarray (mono float). gaps: list ความยาวเงียบหลังท่อน i (วินาที) ถ้าไม่ให้ใช้ gap"""
    out = []
    for i, c in enumerate(chunks):
        c = fade(trim_silence(np.asarray(c, dtype=np.float32), sr, pad=pad), sr)
        out.append(c)
        if i < len(chunks) - 1:
            g = gaps[i] if gaps and i < len(gaps) and gaps[i] is not None else gap
            out.append(np.zeros(int(sr * g), dtype=np.float32))
    return np.concatenate(out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix"); ap.add_argument("out")
    ap.add_argument("--gap", type=float, default=0.45, help="ช่องว่างระหว่างท่อน (วินาที)")
    ap.add_argument("--pad", type=float, default=0.06, help="เงียบที่เหลือไว้หัว/ท้ายท่อน (วินาที)")
    a = ap.parse_args()
    files = sorted(glob.glob(a.prefix + "*.wav"))
    assert files, f"no files match {a.prefix}*.wav"
    chunks, sr = [], None
    for f in files:
        x, sr = sf.read(f, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        chunks.append(x)
    y = stitch(chunks, sr, gap=a.gap, pad=a.pad)
    sf.write(a.out, y, sr)
    print(f"{len(files)} chunks -> {a.out}  {len(y)/sr:.1f}s  (gap {a.gap}s, pad {a.pad}s)")
