"""
เอนจินรวม "hero" = OmniVoice กำหนดเสียง + VoxCPM2 เป็นตัวพูด (ค่าเริ่มต้นของทุก endpoint)

ระบบลูกค้าส่งแค่ข้อความ + เลือกเสียงจากรายการของ OmniVoice (voice_id สต็อก/ลาว/โคลนถาวร
หรือ instruct คำศัพท์ OmniVoice เช่น "female, low pitch") ไม่ต้องรู้เรื่องเอนจิน:
  - voice_id สต็อก/ลาว  → VoxCPM2 โคลนจากไฟล์เสียงอ้างอิงที่ OmniVoice สร้างไว้ (voices/*.wav)
  - เสียงโคลนถาวร       → VoxCPM2 โคลนจากไฟล์ที่ลูกค้าอัปโหลด
  - instruct            → OmniVoice สร้างตัวอย่างเสียงสั้นๆ ตาม instruct ก่อน แล้ว VoxCPM2 โคลนจาก
                          ตัวอย่างนั้น (cache ต่อ instruct — instruct เดิมได้ทิมเบอร์เดิมทุกครั้ง)
  - VoxCPM2 ล้มเหลวก้อนไหน → OmniVoice สร้างก้อนนั้นแทนด้วยเสียงต้นทางเดียวกัน (resample ให้ตรง) งานไม่ล้ม

ผลเสียงตรงกับไฟล์สารคดี 3 นาที v2 ที่ผู้ใช้อนุมัติ (VoxCPM2 โคลน voice_02) — ดู voxcpm2_engine.py
"""
import os
import sys
import tempfile
import threading
from collections import OrderedDict

import numpy as np
import soundfile as sf


# ประโยคกลางๆ (ไม่มีคำลงท้ายเพศ) ยาว ~5-7 วินาที — ใช้ให้ OmniVoice สร้างตัวอย่างเสียงจาก instruct
DESIGN_REF_TEXT = os.environ.get(
    "TTS_HERO_DESIGN_REF_TEXT",
    "วันนี้ท้องฟ้าแจ่มใส อากาศกำลังสบาย เหมาะกับการออกไปเดินเล่นริมแม่น้ำ แล้วแวะร้านกาแฟเล็กๆ ใกล้สะพานเก่า",
)
PROMPT_CACHE_SIZE = int(os.environ.get("TTS_PROMPT_CACHE_SIZE", "64"))
# เสียงอ้างอิงเฉพาะเอนจินนี้ (แทนไฟล์ใน voices/) — VoxCPM2 ลอกบรรยากาศห้องจากไฟล์อ้างอิงด้วย
# ไฟล์สต็อกบางตัวก้อง (เช่น voice_02) ผลจึงก้องตาม → สร้างไฟล์ที่แห้งกว่าไว้ใน voices_hero/
HERO_REFS_DIR = os.environ.get("TTS_HERO_REFS_DIR",
                               os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voices_hero"))


def _with_hero_refs(voices):
    """คืน (สำเนารายการเสียงที่ชี้ไฟล์อ้างอิงไป voices_hero/, {ไฟล์อ้างอิง: taps ลดก้อง})
    เสียงที่ไม่มีใน voices_hero/ คงเดิม | "dereverb_taps" ใน manifest = ลดก้องทั้งไฟล์หลังต่อท่อน (0 = ไม่ลด)"""
    manifest = os.path.join(HERO_REFS_DIR, "voices.json")
    if not os.path.exists(manifest):
        return voices, {}
    import json
    with open(manifest, encoding="utf-8") as f:
        overrides = {v["id"]: v for v in json.load(f)}
    out, dereverb = dict(voices), {}
    for vid, o in overrides.items():
        if vid in voices:
            v = voices[vid]
            ref = os.path.join(HERO_REFS_DIR, o["ref_audio"])
            out[vid] = dict(v, ref_audio=ref, meta=dict(v["meta"], ref_text=o["ref_text"]))
            if int(o.get("dereverb_taps", 0)) > 0:
                dereverb[ref] = int(o["dereverb_taps"])
    print(f"[hero] เสียงอ้างอิงเฉพาะ hero: {sorted(o for o in overrides if o in voices)}"
          f" | ลดก้อง: {sorted(vid for vid, o in overrides.items() if int(o.get('dereverb_taps', 0)) > 0)}", flush=True)
    return out, dereverb


class HybridPrompt:
    """prompt ของ VoxCPM2 + เสียงต้นทาง (ไฟล์อ้างอิงหรือ wav) ไว้สร้าง prompt ของ OmniVoice ตอนต้องสำรอง"""
    __slots__ = ("vox", "ref_path", "ref_text", "wav", "wav_sr", "_omni", "dereverb")

    def __init__(self, vox, ref_path=None, ref_text=None, wav=None, wav_sr=None, dereverb=0):
        self.vox, self.ref_path, self.ref_text = vox, ref_path, ref_text
        self.wav, self.wav_sr, self._omni = wav, wav_sr, None
        self.dereverb = dereverb  # taps ของ WPE ที่ใช้หลังต่อท่อน (0 = ไม่ลดก้อง)

    def omni_prompt(self, omni):
        if self._omni is None:
            if self.ref_path is not None:
                self._omni = omni.build_prompt(self.ref_path, self.ref_text)
            else:
                wav = self.wav
                if self.wav_sr != omni.sample_rate:
                    import librosa
                    wav = librosa.resample(wav, orig_sr=self.wav_sr, target_sr=omni.sample_rate).astype(np.float32)
                self._omni = omni.build_prompt_from_wav(wav, self.ref_text)
        return self._omni


class HybridEngine:
    id = "hero"
    name = "Hero (OmniVoice voices + VoxCPM2)"
    supports_clone = True
    supports_design = True
    free_form_instruct = False  # instruct = คำศัพท์ของ OmniVoice (ตรวจด้วย clean_instruct)

    def __init__(self, omni, vox):
        self.omni, self.vox = omni, vox
        self.sample_rate = vox.sample_rate
        # รายการเสียงของ OmniVoice (+ไฟล์อ้างอิงที่แห้งกว่า และค่าลดก้องต่อเสียง จาก voices_hero/)
        self.voices, self._dereverb = _with_hero_refs(omni.voices)
        self.native_mixed_language = vox.native_mixed_language
        self.chunk_max_chars = vox.chunk_max_chars
        self.split_lines = vox.split_lines
        self._pcache = OrderedDict()              # แยกจาก cache ของเอนจินเดี่ยว (ชนิด prompt ต่างกัน)
        self._lock = threading.Lock()
        self._design_lock = threading.Lock()

    def list_voices(self, language=None):
        return self.omni.list_voices(language=language)

    def build_prompt(self, ref_path, ref_text):
        return HybridPrompt(self.vox.build_prompt(ref_path, ref_text), ref_path=ref_path, ref_text=ref_text,
                            dereverb=self._dereverb.get(ref_path, 0))

    def build_prompt_from_wav(self, wav, ref_text):
        return HybridPrompt(self.vox.build_prompt_from_wav(wav, ref_text), ref_text=ref_text,
                            wav=np.asarray(wav, dtype=np.float32), wav_sr=self.sample_rate)

    def cache_get(self, key):
        with self._lock:
            if key in self._pcache:
                self._pcache.move_to_end(key)
                return self._pcache[key]
        return None

    def cache_put(self, key, prompt):
        with self._lock:
            self._pcache[key] = prompt
            self._pcache.move_to_end(key)
            while len(self._pcache) > PROMPT_CACHE_SIZE:
                self._pcache.popitem(last=False)

    def cache_drop(self, key):
        with self._lock:
            self._pcache.pop(key, None)

    def concat(self, wavs):
        return self.vox.concat(wavs)

    def postprocess(self, wav, prompt):
        """หลังต่อทุกท่อนแล้ว: ลดก้องทั้งไฟล์ (เฉพาะเสียงที่ตั้งไว้ใน voices_hero/)
        ทำตามไฟล์ที่ผู้ใช้อนุมัติทุกขั้น: เสียงที่ฝังลายน้ำรายท่อนแล้ว → ต่อท่อน → WPE ทั้งไฟล์ → จบ
        ข้อแลก: WPE ลบลายน้ำออกบางส่วน การตรวจย้อนหลังว่าไฟล์มาจากระบบเราจะแม่นน้อยลงสำหรับเสียงเหล่านี้"""
        taps = getattr(prompt, "dereverb", 0) if prompt is not None else 0
        if not taps:
            return wav
        import dereverb
        return dereverb.apply(wav, self.sample_rate, taps=taps)

    def transcribe(self, audio_path):
        return self.vox.transcribe(audio_path)

    def design_prompt(self, instruct: str):
        """OmniVoice สร้างตัวอย่างเสียงตาม instruct → เป็นเสียงอ้างอิงของ VoxCPM2 (cache ต่อ instruct)"""
        key = f"design::{instruct}"
        with self._design_lock:
            prompt = self.cache_get(key)
            if prompt is not None:
                return prompt
            wav, _ = self.omni._run(DESIGN_REF_TEXT, instruct=instruct, class_temperature=0.0)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            try:
                sf.write(path, wav, self.omni.sample_rate)
                vox_prompt = self.vox.build_prompt(path, DESIGN_REF_TEXT)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            prompt = HybridPrompt(vox_prompt, ref_text=DESIGN_REF_TEXT, wav=wav, wav_sr=self.omni.sample_rate)
            self.cache_put(key, prompt)
            print(f"[hero] instruct {instruct!r}: OmniVoice สร้างเสียงอ้างอิง → VoxCPM2 พูด", flush=True)
            return prompt

    def _resolve(self, voice_id, clone_prompt, instruct):
        if clone_prompt is not None:
            return clone_prompt
        if voice_id is not None:
            p = self.cache_get(voice_id)
            if p is None:
                v = self.voices[voice_id]
                p = self.build_prompt(v["ref_audio"], v["meta"]["ref_text"])
                self.cache_put(voice_id, p)
            return p
        if instruct:
            return self.design_prompt(instruct)
        return None

    def _run(self, text, *, voice_id=None, clone_prompt=None, instruct=None, language=None,
             speed=1.0, num_step=32, guidance_scale=None, class_temperature=None):
        prompt = self._resolve(voice_id, clone_prompt, instruct)
        try:
            # ฝังลายน้ำทีละท่อนเสมอ แล้วค่อยลดก้องทั้งไฟล์ใน postprocess() — ลำดับเดียวกับไฟล์ที่ทำมือ
            # ซึ่งผู้ใช้อนุมัติว่าไม่ก้อง (ลองแบบลดก้องก่อนแล้วฝังลายน้ำทีหลัง ผู้ใช้ยังได้ยินก้อง)
            return self.vox._run(text, clone_prompt=prompt.vox if prompt else None,
                                 speed=speed, num_step=num_step, guidance_scale=guidance_scale)
        except Exception as e:
            if prompt is None:
                raise
            print(f"[hero] VoxCPM2 ล้มเหลว ({type(e).__name__}: {e}) → OmniVoice สร้างก้อนนี้แทน",
                  file=sys.stderr, flush=True)
            wav, _ = self.omni._run(text, clone_prompt=prompt.omni_prompt(self.omni), language=language,
                                    speed=speed, num_step=num_step, guidance_scale=guidance_scale,
                                    class_temperature=class_temperature)
            if self.omni.sample_rate != self.sample_rate:
                import librosa
                wav = librosa.resample(wav, orig_sr=self.omni.sample_rate,
                                       target_sr=self.sample_rate).astype(np.float32)
            return wav, len(wav) / self.sample_rate
