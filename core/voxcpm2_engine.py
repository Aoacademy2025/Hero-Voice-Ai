"""
VoxCPM2 (openbmb/VoxCPM2, Apache-2.0, 2B) — เอนจินที่สองคู่กับ OmniVoice

จุดเด่นเทียบ OmniVoice (ทดสอบแล้ว ดู scripts/try_voxcpm2*.py):
  - อ่านไทยเป็นธรรมชาติกว่า อ่านตัวเลข/เบอร์โทร/ปนอังกฤษได้เองโดยไม่ต้องแยกช่วงภาษา
  - voice design จากคำบรรยายอิสระ (ภาษาอังกฤษ) เช่น "energetic male radio announcer"
  - รองรับลาว
ข้อแลก: ช้ากว่า OmniVoice ~8 เท่า (RTF ~0.86 บน RTX 3090 แบบไม่ compile), VRAM ~6GB

interface เหมือน OmniVoiceEngine (server.py ใช้แทนกันได้): load / list_voices / _run /
build_prompt / build_prompt_from_wav / cache_get|put|drop / transcribe / concat

ข้อกำหนดที่ผู้ใช้ย้ำ: ต้องตัดเงียบระหว่างประโยค/ก้อนเสมอ
  - ภายในก้อน: _tighten_pauses (ฟังก์ชันเดียวกับ OmniVoice)
  - ขอบก้อน: ตัดเงียบหัว/ท้ายเหลือ pad สั้นๆ เท่ากันทุกก้อน (โมเดลสร้างเงียบไม่เท่ากัน 0.1-0.5s)
  - ระหว่างก้อน: concat() เว้นช่องว่างคงที่ 0.45s
ค่าทั้งหมดตรงกับไฟล์สารคดี 3 นาที v2 (scripts/try_voxcpm2_long.py + stitch_chunks.py) ที่ผู้ใช้อนุมัติ:
บรรทัดละท่อน, 10 steps, cfg 2.0, 48kHz, ข้อความดิบ, ไม่หดเงียบในท่อน (เปิดได้ TTS_VOXCPM2_TIGHTEN=1)
"""
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict

import numpy as np
import soundfile as sf
import torch

import asr_engine
import watermark

HF_ID = os.environ.get("TTS_VOXCPM2_MODEL", "openbmb/VoxCPM2")
NATIVE_SR = 48000
CHUNK_GAP_S = float(os.environ.get("TTS_VOXCPM2_CHUNK_GAP", "0.45"))  # = ไฟล์ v2 ที่ผู้ใช้อนุมัติ
EDGE_PAD_S = 0.06
CHUNK_MAX_CHARS = int(os.environ.get("TTS_VOXCPM2_CHUNK_MAX_CHARS", "200"))
PROMPT_CACHE_SIZE = int(os.environ.get("TTS_PROMPT_CACHE_SIZE", "64"))
# torch.compile: เร็วขึ้นมากบน Linux แต่ใช้ไม่ได้บน Windows (ไม่มี triton) — ดีฟอลต์เปิดเฉพาะ Linux
OPTIMIZE = os.environ.get("TTS_VOXCPM2_OPTIMIZE", "0" if os.name == "nt" else "1") != "0"


# ── Windows workaround (ดู scripts/voxcpm2_compat.py) ───────────────────────────────────
def _load_file_memmap(path, *a, **k):
    """safetensors 0.8 mmap → access violation บน Windows: อ่านทีละ tensor จาก numpy memmap แทน"""
    import json, struct
    dts = {"BF16": (np.uint16, torch.bfloat16), "F16": (np.float16, None), "F32": (np.float32, None),
           "I64": (np.int64, None), "I32": (np.int32, None), "BOOL": (np.bool_, None)}
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    mm = np.memmap(path, dtype=np.uint8, mode="r", offset=8 + n)
    out = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        npdt, view = dts[info["dtype"]]
        s0, s1 = info["data_offsets"]
        # ไม่ copy: ชี้ไปที่ไฟล์ตรงๆ ลด RAM สูงสุดตอนโหลดราวครึ่งหนึ่ง (load_state_dict อ่านอย่างเดียว)
        t = torch.from_numpy(np.frombuffer(mm[s0:s1], dtype=npdt).reshape(info["shape"]))
        out[name] = t.view(view) if view else t
    return out


# ── audio helpers ──────────────────────────────────────────────────────────────────────
def _trim_edges(x, sr, thr_db=-45.0, pad=EDGE_PAD_S):
    w = max(1, int(sr * 0.01))
    env = np.sqrt(np.convolve(x.astype(np.float64) ** 2, np.ones(w) / w, "same"))
    idx = np.where(env > 10 ** (thr_db / 20))[0]
    if len(idx) == 0:
        return x
    p = int(sr * pad)
    return x[max(0, idx[0] - p):min(len(x), idx[-1] + p)]


def _fade(x, sr, ms=8):
    n = min(len(x) // 2, int(sr * ms / 1000))
    if n <= 0:
        return x
    x = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=x.dtype)
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def _has_non_ascii_letters(s):
    return any(ord(ch) > 127 and ch.isalpha() for ch in s)


class VoxCPM2Engine:
    id = "voxcpm2"
    name = "VoxCPM2"
    supports_clone = True
    supports_design = True
    # อ่านไทยปนอังกฤษได้เองในก้อนเดียว — server ไม่ต้องแยกช่วงภาษา (การแยกเพิ่มรอยต่อโดยไม่จำเป็น)
    native_mixed_language = True
    # instruct เป็นคำบรรยายอิสระภาษาอังกฤษได้ ไม่จำกัดแค่คำศัพท์ของ OmniVoice
    free_form_instruct = True
    chunk_max_chars = CHUNK_MAX_CHARS
    # ตัดท่อนตามบรรทัดของสคริปต์ (บรรทัดละท่อน) เหมือนไฟล์ v2 — ผู้เขียนสคริปต์คุมจังหวะได้เอง
    split_lines = True

    def __init__(self, sample_rate: int, tighten_pauses=None, voices=None):
        # server ทั้งระบบทำงานที่ sample_rate เดียว (24k) — resample จาก 48k ตอนท้าย _run
        self.sample_rate = sample_rate
        self._tighten = tighten_pauses
        self.voices = voices if voices is not None else {}  # แชร์ manifest เสียงสต็อกกับ OmniVoice
        self.model = None
        self.device = None
        self._pcache = OrderedDict()
        self._pcache_lock = threading.Lock()

    def load(self):
        from voxcpm import VoxCPM
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        t = time.time()
        # TF32 สำหรับส่วน fp32 (AudioVAE decode) — torch.compile เตือนว่ายังไม่เปิด
        torch.set_float32_matmul_precision("high")
        kw = dict(load_denoiser=False, device=self.device)
        if os.name == "nt":
            import voxcpm.model.voxcpm2 as _v2
            _v2.load_file = _load_file_memmap
            torch.set_default_dtype(torch.bfloat16)  # init 2B params เป็น bf16 (fp32 กิน RAM ~8GB)
        try:
            try:
                self.model = VoxCPM.from_pretrained(HF_ID, optimize=OPTIMIZE, **kw)
            except Exception as e:
                if not OPTIMIZE:
                    raise
                print(f"[voxcpm2] optimize=True ล้มเหลว ({type(e).__name__}: {e}) → โหลดใหม่แบบไม่ compile",
                      file=sys.stderr)
                self.model = VoxCPM.from_pretrained(HF_ID, optimize=False, **kw)
        finally:
            torch.set_default_dtype(torch.float32)
        self.tts = self.model.tts_model
        print(f"[voxcpm2] model loaded in {time.time()-t:.1f}s ({self.device}, optimize={OPTIMIZE}), "
              f"{len(self.voices)} shared stock voices")

    def list_voices(self, language=None):
        out = []
        for vid, v in self.voices.items():
            v_lang = v["meta"].get("language")
            if language is None:
                if v_lang is not None:
                    continue
            elif (v_lang or "").lower() != language.lower():
                continue
            out.append({"voice_id": vid, "desc": v["meta"].get("desc", ""),
                        "instruct": v["meta"].get("instruct", ""), "language": v_lang})
        return out

    # ── prompt (= prompt cache ของ VoxCPM2: เสียงอ้างอิงที่ encode แล้ว + ข้อความ) ──
    def build_prompt(self, ref_path, ref_text):
        with torch.no_grad():
            return self.tts.build_prompt_cache(prompt_text=ref_text, prompt_wav_path=ref_path)

    def build_prompt_from_wav(self, wav: np.ndarray, ref_text: str):
        """ใช้ล็อกทิมเบอร์โหมด voice design ข้ามก้อน (ก้อนแรกกลายเป็นเสียงอ้างอิงของก้อนถัดไป)"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            sf.write(path, wav, self.sample_rate)
            return self.build_prompt(path, ref_text)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def cache_get(self, voice_id):
        with self._pcache_lock:
            if voice_id in self._pcache:
                self._pcache.move_to_end(voice_id)
                return self._pcache[voice_id]
        return None

    def cache_put(self, voice_id, prompt):
        with self._pcache_lock:
            self._pcache[voice_id] = prompt
            self._pcache.move_to_end(voice_id)
            while len(self._pcache) > PROMPT_CACHE_SIZE:
                self._pcache.popitem(last=False)

    def cache_drop(self, voice_id):
        with self._pcache_lock:
            self._pcache.pop(voice_id, None)

    # ── generate ──
    def _run(self, text, *, voice_id=None, clone_prompt=None, instruct=None, language=None,
             speed=1.0, num_step=32, guidance_scale=None, class_temperature=None,
             apply_watermark=True):
        """generate หนึ่งก้อน (blocking) → (wav float32 @ self.sample_rate, duration_sec)
        language/class_temperature ไม่ใช้ (VoxCPM2 ตรวจภาษาเอง, ไม่มี class sampling)
        apply_watermark=False: ผู้เรียกจะฝังลายน้ำเองหลังประมวลผลต่อ (เช่นลดก้องทั้งไฟล์ — ดู hybrid_engine)"""
        if clone_prompt is None and voice_id is not None:
            v = self.voices[voice_id]
            clone_prompt = self.cache_get(voice_id)
            if clone_prompt is None:
                clone_prompt = self.build_prompt(v["ref_audio"], v["meta"]["ref_text"])
                self.cache_put(voice_id, clone_prompt)
        target = text
        # คำบรรยายเสียงต้องเป็นอังกฤษ (ทดสอบแล้ว: คำบรรยายไทยทำให้โมเดลอ่านมั่วทั้งประโยค)
        # ใส่เฉพาะตอนไม่มีเสียงอ้างอิง — มี prompt แล้วทิมเบอร์มาจาก prompt
        if instruct and clone_prompt is None:
            if _has_non_ascii_letters(instruct):
                print(f"[voxcpm2] ข้าม instruct ที่ไม่ใช่ภาษาอังกฤษ: {instruct!r}", file=sys.stderr)
            else:
                target = f"({instruct}){text}"
        # num_step ของ OmniVoice (ดีฟอลต์ 32) → inference_timesteps ของ VoxCPM2 (ดีฟอลต์ 10)
        steps = max(4, min(30, round(num_step * 10 / 32)))
        with torch.no_grad():
            wav_t, _, _ = self.tts.generate_with_prompt_cache(
                target_text=target, prompt_cache=clone_prompt,
                inference_timesteps=steps, cfg_value=guidance_scale or 2.0,
                retry_badcase=True,
            )
        wav = wav_t.squeeze().float().cpu().numpy().astype(np.float32)
        wav = _trim_edges(wav, NATIVE_SR)
        if abs(speed - 1.0) > 0.05:
            import librosa
            wav = librosa.effects.time_stretch(wav, rate=float(speed))
        if self.sample_rate != NATIVE_SR:
            import librosa
            wav = librosa.resample(wav, orig_sr=NATIVE_SR, target_sr=self.sample_rate).astype(np.float32)
        if self._tighten is not None:
            wav = self._tighten(wav, self.sample_rate)
        wav = _fade(wav, self.sample_rate)
        if apply_watermark:
            wav = watermark.apply(wav, self.sample_rate)
        return wav, len(wav) / self.sample_rate

    def concat(self, wavs):
        """ต่อก้อนด้วยช่องว่างคงที่ (ขอบแต่ละก้อนถูกตัดเงียบ+fade แล้วใน _run) — ไม่ crossfade
        เพราะขอบก้อนเหลือเงียบแค่ ~60ms ถ้า crossfade 80ms แบบ OmniVoice จะทับเนื้อเสียงพูด"""
        if not wavs:
            return np.zeros(0, dtype=np.float32)
        gap = np.zeros(int(self.sample_rate * CHUNK_GAP_S), dtype=np.float32)
        out = []
        for i, w in enumerate(wavs):
            if i:
                out.append(gap)
            out.append(np.asarray(w, dtype=np.float32))
        return np.concatenate(out)

    def transcribe(self, audio_path):
        return asr_engine.transcribe(audio_path)
