"""ทดลอง VoxCPM2 กับข้อความไทย: voice design + โคลนจากเสียงสต็อก แล้วจับเวลา/VRAM"""
import os, sys, time
import soundfile as sf
import torch
from voxcpm import VoxCPM
# safetensors 0.8.0 mmap crash (access violation) บน Windows และ RAM 16GB ไม่พอถ้าอ่านทั้งไฟล์เป็น bytes
# → parse header เอง แล้ว copy ทีละ tensor จาก numpy memmap (peak RAM ต่ำ)
import json, struct, numpy as np
import voxcpm.model.voxcpm2 as _v2
_DT = {"BF16": (np.uint16, torch.bfloat16), "F16": (np.float16, None), "F32": (np.float32, None),
       "I64": (np.int64, None), "I32": (np.int32, None), "BOOL": (np.bool_, None)}
def _load_file_memmap(path, *a, **k):
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
_v2.load_file = _load_file_memmap
torch.set_default_dtype(torch.bfloat16)  # init โมเดล 2B เป็น bf16 แทน fp32 ประหยัด RAM ครึ่งหนึ่ง

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_BASE, "ab_output", "voxcpm2")
os.makedirs(OUT, exist_ok=True)

TEXT = ("สวัสดีค่ะ ยินดีต้อนรับเข้าสู่บริการฮีโร่วอยซ์ "
        "วันนี้เรามีโปรโมชั่นพิเศษสำหรับลูกค้าใหม่ ลดทันที 20 เปอร์เซ็นต์ "
        "สอบถามเพิ่มเติมได้ที่เบอร์ 02 123 4567 ขอบคุณค่ะ")

device = sys.argv[1] if len(sys.argv) > 1 else "cuda"
t0 = time.time()
model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, device=device, optimize=False)
torch.set_default_dtype(torch.float32)  # คืนค่า default หลังโหลดเสร็จ
print(f"load: {time.time()-t0:.1f}s", flush=True)

ONLY = sys.argv[2].split(",") if len(sys.argv) > 2 else None

def run(name, text, **kw):
    if ONLY and name not in ONLY:
        return
    t = time.time()
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    wav = model.generate(text=text, cfg_value=2.0, inference_timesteps=10, **kw)
    dt = time.time() - t
    sr = 48000
    sf.write(os.path.join(OUT, name), wav, sr)
    dur = len(wav) / sr
    vram = torch.cuda.max_memory_allocated() / 1e9 if device == "cuda" else 0
    print(f"{name}: gen {dt:.1f}s, audio {dur:.1f}s, RTF {dt/dur:.2f}, peak VRAM {vram:.2f}GB", flush=True)

# หมายเหตุ: คำบรรยายเสียงต้องเป็นภาษาอังกฤษ ถ้าใส่เป็นไทยโมเดลจะอ่านมั่ว (ทดสอบแล้ว)
run("design_female_th.wav", "(a young woman, soft warm voice, clear articulation)" + TEXT)
run("design_male_th.wav", "(a middle-aged man, calm, deep voice)" + TEXT)
run("clone_voice02_th.wav", TEXT,
    prompt_wav_path=os.path.join(_BASE, "voices", "voice_02.wav"),
    prompt_text="สวัสดีค่ะ ยินดีให้บริการนะคะ")
run("lao_design.wav", "(ຜູ້ຍິງ ສຽງອ່ອນໂຍນ)ສະບາຍດີ ຍິນດີຕ້ອນຮັບສູ່ບໍລິການຂອງພວກເຮົາ ຂອບໃຈຫຼາຍໆ")
print("done")
