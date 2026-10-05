"""Workaround สำหรับรัน VoxCPM2 บน Windows / RAM 16GB
- safetensors 0.8.0 load_file (mmap) → access violation บน Windows
- อ่านทั้งไฟล์เป็น bytes ก็กิน RAM เกิน → parse header เอง แล้ว copy ทีละ tensor จาก numpy memmap
- init โมเดล 2B เป็น bf16 ระหว่างโหลด (fp32 กิน RAM 8GB)
"""
import json, struct
import numpy as np
import torch

_DT = {"BF16": (np.uint16, torch.bfloat16), "F16": (np.float16, None), "F32": (np.float32, None),
       "I64": (np.int64, None), "I32": (np.int32, None), "BOOL": (np.bool_, None)}


def load_file_memmap(path, *a, **k):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    mm = np.memmap(path, dtype=np.uint8, mode="r", offset=8 + n)
    out = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        npdt, tview = _DT[info["dtype"]]
        s0, s1 = info["data_offsets"]
        arr = np.frombuffer(mm[s0:s1], dtype=npdt).reshape(info["shape"]).copy()
        t = torch.from_numpy(arr)
        out[name] = t.view(tview) if tview else t
    return out


def load_voxcpm2(device="cuda"):
    import voxcpm.model.voxcpm2 as _v2
    from voxcpm import VoxCPM
    _v2.load_file = load_file_memmap
    torch.set_default_dtype(torch.bfloat16)
    try:
        return VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, device=device, optimize=False)
    finally:
        torch.set_default_dtype(torch.float32)
