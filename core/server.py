"""
server.py — Hero Voice TTS API (v2)

โครงแบบ VoiceStudio แต่ใช้โมเดล OmniVoice เป็น engine ภายใน:
  - Engine registry  : รองรับหลายเอนจินใน endpoint เดียว (ตอนนี้มี OmniVoice; เสียบเพิ่มได้)
  - เสียงสต็อก       : pre-encode ครั้งเดียว, clone ตอน runtime (เสียงคงที่)
  - Voice design     : สร้างเสียงจากคำบรรยาย (instruct) — เพศ/อายุ/pitch/whisper/สำเนียง
  - Voice cloning    : /clone อัปโหลด → โคลนครั้งเดียว (ไม่เก็บ)
  - คลังเสียงโคลนถาวร : /voices สร้างเสียงโคลนเก็บไว้ใช้ซ้ำ (per-user, ดู voice_library.py)
  - ASR              : /transcribe ถอดเสียงเป็นข้อความ (auto ref_text ตอนโคลน)
  - Streaming (SSE)  : ตัดข้อความเป็นก้อน แล้วทยอยส่งเสียงทีละก้อน (latency ก้อนแรกต่ำ)
  - OpenAI-compatible: /v1/audio/speech (เสียบแทน OpenAI TTS ได้)
  - เครดิต/rate-limit: คิดเงินตามวินาทีเสียง (เปิดด้วย env TTS_CREDITS_DB)

หมายเหตุ instruct ของ OmniVoice = "ออกแบบเสียง" ไม่ใช่ "อารมณ์"
  รองรับ: gender(male/female), age(child/teenager/young adult/middle-aged/elderly),
          pitch(very low/low/moderate/high/very high pitch), style(whisper),
          accent(american/british/australian accent, ...) — แต่ละหมวด <=1 คำ คั่นด้วย ", "

รัน (จากรากรีโป):
  python scripts/build_voices.py     # ครั้งเดียว สร้างคลังเสียง
  python core/server.py              # http://0.0.0.0:8000  (Swagger: /docs)
"""
import asyncio
import base64
import io
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException, Request,
                     Response, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from omnivoice import OmniVoice
import asr_engine
import audio_enhance
import watermark
from text_utils import chunk_text, normalize_thai_numbers, split_by_language, transliterate_english
from voice_library import VoiceLibrary

# ── config ──────────────────────────────────────────────────────────
# _HERE = โฟลเดอร์นี้ (core/) — ใช้หาไฟล์ที่อยู่ติดกับ server.py เอง (เช่น studio.html)
# _BASE = รากรีโป (พาเรนต์ของ core/) — ใช้เป็นดีฟอลต์ของโฟลเดอร์ข้อมูล (model/voices/ฯลฯ
# ที่อยู่ที่รากรีโป ไม่ได้ย้ายเข้ามาใน core/ ด้วย)
_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
MODEL_DIR = os.environ.get("TTS_MODEL_DIR", os.path.join(_BASE, "model"))
VOICES_DIR = os.environ.get("TTS_VOICES_DIR", os.path.join(_BASE, "voices"))
# เสียงสต็อกภาษาลาว — manifest/โฟลเดอร์แยกจาก voices/ (ดู build_voices_lao.py) โหลดเพิ่มเข้ามา
# เป็น voice_id เดิม (lao_01..lao_04) ถ้าโฟลเดอร์/manifest ไม่มีก็ข้ามไปเงียบๆ (ไม่ error)
LAO_VOICES_DIR = os.environ.get("TTS_LAO_VOICES_DIR", os.path.join(_BASE, "voices_lao"))
SAMPLE_RATE = 24000

# เอาเสียงสำเนียงชาติอื่นออกก่อน (british/american/australian/canadian/indian/chinese/korean
# accent — voice_16, 27-31, 38-43, 48) เหลือแต่เสียงไทย/อังกฤษล้วนที่ยังไม่มีสำเนียงต่างชาติ
# 32, 33 = เสียงกระซิบ (whisper) — ฟังแล้วไม่เป็นธรรมชาติ เอาออกด้วย (ดูคอมเมนต์ใน build_voices.py)
_FOREIGN_ACCENT_IDS = {16, 27, 28, 29, 30, 31, 32, 33, 38, 39, 40, 41, 42, 43, 48}
SERVED_VOICE_IDS = os.environ.get(
    "TTS_VOICE_IDS",
    ",".join(f"voice_{i:02d}" for i in range(1, 49) if i not in _FOREIGN_ACCENT_IDS),
).split(",")

MAX_CONCURRENCY = int(os.environ.get("TTS_MAX_CONCURRENCY", "2"))
CORS_ORIGINS = os.environ.get("TTS_CORS_ORIGINS", "*").split(",")

# คลังเสียงโคลนถาวร (custom voices)
CUSTOM_VOICES_DIR = os.environ.get("TTS_CUSTOM_VOICES_DIR", os.path.join(_BASE, "custom_voices"))
VOICES_DB = os.environ.get("TTS_VOICES_DB", os.path.join(CUSTOM_VOICES_DIR, "voices.db"))
# จำนวน clone-prompt ที่ cache ในแรม (เกินนี้ evict ตัวเก่าสุด) — กันแรมบวมเมื่อมีเสียงเยอะ
PROMPT_CACHE_SIZE = int(os.environ.get("TTS_PROMPT_CACHE_SIZE", "64"))

# ASR (ถอดเสียง) — ดู asr_engine.py (faster-whisper, โหลด lazy ครั้งแรกที่ใช้,
# ปรับโมเดล/device ได้ด้วย env TTS_ASR_MODEL / TTS_ASR_DEVICE / TTS_ASR_COMPUTE_TYPE)

# ตัวคูณความเร็วฐาน: โมเดลพูดช้ากว่าธรรมชาติ → คูณให้ slider 1.0 = ความเร็วคนจริง
# ผู้ใช้ตั้ง speed=1.0 → โมเดลได้ speed = 1.0 * BASE_SPEED. ปรับจูนได้ตามชอบ
# เดิม 1.4 → ลดเป็น 1.15 → ผู้ใช้ทดสอบแล้วยังเร็วไป ลดลงอีกเป็น 1.0 → ยังเร็วไปอีก ผู้ใช้ส่งคลิป
# เสียงต้นแบบมาให้ฟัง (Recording 2026-09-02 003620.mp4) ถอดเสียงแล้ววัดจริง: พูด 33 ตัวอักษร
# ใน ~3.23 วิ (หักช่วงเงียบ/หายใจออก) = ~10.2 ตัวอักษร/วิ เทียบกับ voice_01 เดิมที่ ~15.8
# ตัวอักษร/วิ (42 ตัวอักษร ใน 2.66 วิ ตอน speed=1.0) → ลดลงอีกเป็น 0.65 (~10.2/15.8)
# ปรับต่อได้ตามหูจริง (ตั้ง env TTS_BASE_SPEED หรือใช้ speed slider ใน request ช่วยได้ด้วย)
BASE_SPEED = float(os.environ.get("TTS_BASE_SPEED", "0.65"))

# class_temperature ดีฟอลต์ของทุก generate — ดีฟอลต์ของโมเดลเองคือ 0 (greedy/deterministic
# token sampling ทุกครั้ง) ซึ่งเป็นสาเหตุหลักที่เสียงจาก /tts (endpoint หลักที่ใช้งานจริงผ่าน voice_id
# ทั้งเสียงสต็อกและเสียงโคลนถาวร) ฟังดูแบน/หุ่นยนต์ — ต่างจาก /clone ที่ตั้ง class_temperature=0.8
# ไว้แล้ว (เพราะมี best-of-N คอยกรองตัวที่แย่ทิ้ง) แต่ /tts ไม่มี best-of-N (generate ครั้งเดียวจบ
# ต่อ request เพราะเรียกบ่อยกว่ามาก) จึงตั้งค่ากลางๆ ต่ำกว่า /clone ไว้ก่อน (ความเสี่ยงออกเสียงเพี้ยน
# สูงขึ้นตามค่านี้ แต่ไม่มีตัวกรองมาเลือกซ้ำเหมือน best-of-N)
# ยังไม่ได้วัดผลจริง (GPU เครื่องนี้ใช้งานไม่ได้ตอนแก้) — ทดสอบฟังเทียบก่อน-หลังแล้วปรับต่อได้
# (ตั้ง env TTS_CLASS_TEMPERATURE หรือส่ง class_temperature เองต่อ request ก็ได้ ดู TTSRequest)
DEFAULT_CLASS_TEMPERATURE = float(os.environ.get("TTS_CLASS_TEMPERATURE", "0.4"))

# auth: ถ้าตั้ง TTS_CREDITS_DB → ใช้ระบบเครดิต (หลาย key แยกยอด); ไม่งั้นใช้ TTS_API_KEY เดี่ยว
CREDITS_DB = os.environ.get("TTS_CREDITS_DB")
API_KEY = os.environ.get("TTS_API_KEY")
COST_PER_SECOND = float(os.environ.get("TTS_COST_PER_SECOND", "1.0"))  # เครดิต/วินาทีเสียง

# instruct ที่ OmniVoice ยอมรับ (กันส่งค่ามั่ว → error กลางคัน)
_ALLOWED_INSTRUCT = {
    "male", "female",
    "child", "teenager", "young adult", "middle-aged", "elderly",
    "very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch",
    "american accent", "british accent", "australian accent", "indian accent",
    "canadian accent", "chinese accent", "korean accent", "japanese accent",
    "portuguese accent", "russian accent",
}

store = None  # CreditStore (ถ้าเปิดใช้)
if CREDITS_DB:
    from credits import CreditError, CreditStore, RateLimitError
    store = CreditStore(CREDITS_DB)

# คลังเสียงโคลนถาวร — เปิดใช้เสมอ
library = VoiceLibrary(VOICES_DB, CUSTOM_VOICES_DIR)


# ── Engine registry ─────────────────────────────────────────────────
class OmniVoiceEngine:
    """ห่อโมเดล OmniVoice ให้เป็นเอนจินมาตรฐาน (list_voices / generate)"""

    id = "omnivoice"
    name = "OmniVoice"
    sample_rate = SAMPLE_RATE
    supports_clone = True
    supports_design = True

    def __init__(self):
        self.model = None
        self.device = None
        self.voices = {}  # voice_id -> {"prompt", "meta", "preview_path"}
        # LRU cache ของ clone-prompt สำหรับเสียงโคลนถาวร (voice_id -> VoiceClonePrompt)
        self._pcache = OrderedDict()
        self._pcache_lock = threading.Lock()

    def load(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # dtype: cuda→fp16, cpu→bf16 (ประหยัดแรมครึ่งนึงเทียบ fp32, พอสำหรับเครื่องแรมจำกัด)
        # ปรับได้ด้วย env TTS_DTYPE=float32|float16|bfloat16
        _dtype_env = os.environ.get("TTS_DTYPE", "").lower()
        dtype = ({"float32": torch.float32, "float16": torch.float16,
                  "bfloat16": torch.bfloat16}.get(_dtype_env)
                 or (torch.float16 if self.device == "cuda" else torch.bfloat16))
        print(f"[omnivoice] loading model ({self.device}, {dtype})...")
        t = time.time()
        self.model = OmniVoice.from_pretrained(
            MODEL_DIR, device_map=self.device, dtype=dtype, low_cpu_mem_usage=True
        )
        self.model.eval()
        print(f"[omnivoice] model loaded in {time.time()-t:.1f}s")

        manifest_path = os.path.join(VOICES_DIR, "voices.json")
        if not os.path.exists(manifest_path):
            raise RuntimeError(f"{manifest_path} not found — run `python scripts/build_voices.py` first")
        with open(manifest_path, encoding="utf-8") as f:
            manifest = {v["id"]: v for v in json.load(f)}

        for vid in SERVED_VOICE_IDS:
            vid = vid.strip()
            if not vid:
                continue
            if vid not in manifest:
                raise RuntimeError(f"voice '{vid}' not in {manifest_path}")
            v = manifest[vid]
            ref_audio = os.path.join(VOICES_DIR, v["ref_audio"])
            # เก็บแค่ metadata — encode prompt แบบ lazy ตอนใช้ครั้งแรก (ประหยัดแรม/เร็วตอนสตาร์ท)
            self.voices[vid] = {"meta": v, "ref_audio": ref_audio, "preview_path": ref_audio}
        print(f"[omnivoice] {len(self.voices)} stock voices registered (lazy-encode on first use)")

        self._load_extra_manifest(LAO_VOICES_DIR, label="Lao")

    def _load_extra_manifest(self, voices_dir, *, label):
        """โหลด manifest เสียงสต็อกเพิ่มเติม (เช่น voices_lao/) เข้ามาปนกับ self.voices เดิม
        ไม่มีโฟลเดอร์/manifest → ข้ามเงียบๆ (ไม่ error, เป็น feature เสริม)"""
        manifest_path = os.path.join(voices_dir, "voices.json")
        if not os.path.exists(manifest_path):
            return
        with open(manifest_path, encoding="utf-8") as f:
            extra = json.load(f)
        for v in extra:
            vid = v["id"]
            ref_audio = os.path.join(voices_dir, v["ref_audio"])
            self.voices[vid] = {"meta": v, "ref_audio": ref_audio, "preview_path": ref_audio}
        print(f"[omnivoice] +{len(extra)} {label} stock voices registered from {voices_dir}")

    def list_voices(self, language=None):
        """
        ดีฟอลต์ (language=None) → คืนเฉพาะเสียงชุดหลัก (ไทย/อังกฤษ, ไม่มี "language" ใน manifest)
        ไม่ปนเสียงลาวเข้ามาโดยไม่ได้ขอ — แยกชัดเจนตามที่ขอ ต้องระบุ language="lao" ถึงจะเห็น
        """
        out = []
        for vid, v in self.voices.items():
            v_lang = v["meta"].get("language")
            if language is None:
                if v_lang is not None:  # มี language เฉพาะ (เช่นลาว) → ไม่ใส่ในชุดดีฟอลต์
                    continue
            elif (v_lang or "").lower() != language.lower():
                continue
            out.append({"voice_id": vid, "desc": v["meta"].get("desc", ""),
                       "instruct": v["meta"].get("instruct", ""), "language": v_lang})
        return out

    def _run(self, text, *, voice_id=None, clone_prompt=None, instruct=None,
             language=None, speed=1.0, num_step=32, guidance_scale=None,
             class_temperature=None):
        """generate หนึ่งก้อน (blocking) → (wav float32 ndarray, duration_sec)"""
        # คูณความเร็วฐาน (slider 1.0 = ธรรมชาติ) แล้ว clamp ให้อยู่ในช่วงที่โมเดลรับได้
        eff_speed = max(0.31, min(2.99, speed * BASE_SPEED))
        # class_temperature=None (ไม่ระบุ) → ใช้ DEFAULT_CLASS_TEMPERATURE เสมอ (ดูคอมเมนต์ที่นิยาม
        # ค่านี้) กันเสียงแบน/หุ่นยนต์จาก greedy decoding ดีฟอลต์ของโมเดล — ผู้เรียกที่ต้องการ greedy
        # จริงๆ (เช่น debug ให้ผลซ้ำได้) ส่ง class_temperature=0.0 ตรงๆ มาได้
        eff_class_temp = DEFAULT_CLASS_TEMPERATURE if class_temperature is None else class_temperature
        kwargs = dict(text=text, speed=eff_speed,
                      generation_config=self._gcfg(num_step, guidance_scale, eff_class_temp))
        if language:
            kwargs["language"] = language
        if clone_prompt is not None:
            kwargs["voice_clone_prompt"] = clone_prompt
        elif voice_id is not None:
            kwargs["voice_clone_prompt"] = self.voices[voice_id]["prompt"]
        if instruct:
            kwargs["instruct"] = instruct
        with torch.no_grad():
            audio = self.model.generate(**kwargs)
        wav = np.asarray(audio[0], dtype=np.float32)
        wav = watermark.apply(wav, self.sample_rate)
        return wav, len(wav) / self.sample_rate

    @staticmethod
    def _gcfg(num_step, guidance_scale=None, class_temperature=None):
        from omnivoice.models.omnivoice import OmniVoiceGenerationConfig
        cfg = OmniVoiceGenerationConfig(num_step=num_step)
        if guidance_scale is not None:
            cfg.guidance_scale = guidance_scale  # สูง = ยึดเสียงต้นฉบับมากขึ้น (ดีฟอลต์ 2.0)
        if class_temperature is not None:
            # >0 = สุ่มเลือก token (ดีฟอลต์โมเดล 0 = greedy/deterministic)
            # ใช้ตอน best-of-N cloning เพื่อให้แต่ละรอบได้ผลต่างกัน แล้วเลือกตัวที่คล้าย ref สุด
            cfg.class_temperature = class_temperature
        return cfg

    # ── clone-prompt สำหรับเสียงโคลนถาวร (LRU cache) ──
    def build_prompt(self, ref_path, ref_text):
        """encode ไฟล์ ref เป็น VoiceClonePrompt (blocking — เรียกในคิว)"""
        with torch.no_grad():
            return self.model.create_voice_clone_prompt(ref_audio=ref_path, ref_text=ref_text)

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

    # ── ASR (ถอดเสียง) — ผ่าน asr_engine.py (faster-whisper, เร็วกว่า Whisper เดิมมาก) ──
    def transcribe(self, audio_path):
        return asr_engine.transcribe(audio_path)


ENGINES = {}


def get_engine(engine_id: str) -> OmniVoiceEngine:
    eng = ENGINES.get(engine_id)
    if eng is None:
        raise HTTPException(404, f"ไม่พบเอนจิน '{engine_id}' (มี: {list(ENGINES)})")
    return eng


# ── helpers ─────────────────────────────────────────────────────────
def wav_bytes(wav: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, wav, SAMPLE_RATE, format="WAV")
    return buf.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def clean_instruct(instruct: Optional[str]) -> Optional[str]:
    if not instruct:
        return None
    parts = [p.strip().lower() for p in instruct.split(",") if p.strip()]
    bad = [p for p in parts if p not in _ALLOWED_INSTRUCT]
    if bad:
        raise HTTPException(
            422,
            f"instruct มีคำที่ไม่รองรับ: {bad}. "
            f"OmniVoice รองรับเฉพาะ เพศ/อายุ/pitch/whisper/สำเนียง (ไม่มีอารมณ์). "
            f"คำที่ใช้ได้: {sorted(_ALLOWED_INSTRUCT)}",
        )
    return ", ".join(parts)


# ── auth dependency ─────────────────────────────────────────────────
async def auth(x_api_key: str = Header(default=None),
               authorization: str = Header(default=None)):
    """
    คืน record ของ key (dict) หรือ None. ใช้ต่อในการหักเครดิต
    รับได้ทั้ง header 'X-API-Key: <key>' และ 'Authorization: Bearer <key>' (สไตล์ OpenAI)
    """
    if not x_api_key and authorization and authorization.lower().startswith("bearer "):
        x_api_key = authorization[7:].strip()
    if store is not None:
        if not x_api_key:
            raise HTTPException(401, "ต้องส่ง header X-API-Key")
        try:
            return store.authorize(x_api_key)
        except PermissionError as e:
            raise HTTPException(401, str(e))
        except CreditError as e:
            raise HTTPException(402, str(e))
        except RateLimitError as e:
            raise HTTPException(429, str(e))
    # โหมด single key
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "ไม่พบหรือ API key ไม่ถูกต้อง (ใส่ header X-API-Key)")
    return None


def charge(keyrec, seconds: float, endpoint: str):
    if store is not None and keyrec is not None:
        try:
            return store.charge(keyrec["key"], seconds, COST_PER_SECOND, endpoint)
        except Exception as e:
            print(f"[charge] failed: {e}")
    return 0.0


def owner_id(keyrec) -> str:
    """เจ้าของทรัพยากร = API key (โหมดเครดิต) หรือ 'public' (โหมด single/ไม่มี auth)"""
    return keyrec["key"] if (store is not None and keyrec) else "public"


async def resolve_clone_prompt(eng, voice_id: str, keyrec):
    """
    คืน clone-prompt จาก voice_id — รองรับทั้งเสียงสต็อกและเสียงโคลนถาวร (custom)
    เสียง custom: เช็คสิทธิ์เจ้าของ + encode (cache ในแรม)
    """
    # เสียงสต็อก — encode แบบ lazy + cache
    if voice_id in eng.voices:
        prompt = eng.cache_get(voice_id)
        if prompt is None:
            v = eng.voices[voice_id]
            prompt = await _generate_serialized(eng.build_prompt, v["ref_audio"], v["meta"]["ref_text"])
            eng.cache_put(voice_id, prompt)
        return prompt
    # เสียงโคลนถาวร — เช็คสิทธิ์เจ้าของ + encode + cache
    rec = library.get(voice_id)
    if rec is None or not library.can_use(rec, owner_id(keyrec)):
        raise HTTPException(404, f"ไม่พบเสียง '{voice_id}' (ดู /voices และ /voices/mine)")
    prompt = eng.cache_get(voice_id)
    if prompt is None:
        prompt = await _generate_serialized(eng.build_prompt,
                                            library.audio_path(voice_id), rec["ref_text"])
        eng.cache_put(voice_id, prompt)
    return prompt


# ── lifespan ────────────────────────────────────────────────────────
STATE = {"sem": None, "lock": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    eng = OmniVoiceEngine()
    eng.load()
    ENGINES[eng.id] = eng
    STATE["sem"] = asyncio.Semaphore(MAX_CONCURRENCY)
    STATE["lock"] = asyncio.Lock()
    yield


app = FastAPI(title="Hero Voice TTS API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])
app.router.lifespan_context = lifespan


async def _generate_serialized(fn, *args, **kwargs):
    """รัน generate ในคิว (โมเดลไม่ thread-safe) แล้วคืนผลจาก thread"""
    async with STATE["sem"]:
        async with STATE["lock"]:
            return await asyncio.to_thread(fn, *args, **kwargs)


# ── models ──────────────────────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice_id: Optional[str] = Field(None, description="รหัสเสียงสต็อก (ดู /voices)")
    engine: str = Field("omnivoice")
    instruct: Optional[str] = Field(None, description="ออกแบบเสียง เช่น 'female, high pitch'")
    language: Optional[str] = Field(None, description="เช่น 'Thai', 'English', 'Lao' (ปล่อยว่าง=auto)")
    # เดิม default=24 ต่ำกว่า num_step=32 ที่ใช้สร้างเสียงสต็อกใน build_voices.py (และต่ำกว่า
    # default ของโมเดลเอง=32) ทำให้เสียงสต็อก/เสียงเพี้ยนไม่เป็นธรรมชาติ (บั๊กเดียวกับที่เคย
    # เจอใน /clone — ดูคอมเมนต์ที่ CloneRequest.num_step) ปรับกลับมาให้ตรงกัน
    num_step: int = Field(32, ge=4, le=64, description="สูง=คุณภาพ/ความคล้ายดีขึ้นแต่ช้าลง")
    speed: float = Field(1.0, gt=0.3, lt=3.0)
    guidance_scale: Optional[float] = Field(None, ge=1.0, le=5.0,
        description="คุมความยึดเสียงต้นฉบับ (ดีฟอลต์ 2.0); 3-4 = คล้ายขึ้นแต่เสี่ยงเพี้ยน")
    class_temperature: Optional[float] = Field(None, ge=0.0, le=2.0,
        description="คุมความหลากหลายของโทนเสียง (ดีฟอลต์ระบบ = DEFAULT_CLASS_TEMPERATURE, "
                    "ไม่ใช่ 0 ของโมเดลเอง — กันเสียงแบน/หุ่นยนต์จาก greedy decoding); "
                    "0 = greedy (ผลซ้ำเดิมทุกครั้ง แต่แบนกว่า), สูงขึ้น = หลากหลายขึ้นแต่เสี่ยงเพี้ยน")
    mixed_language: bool = Field(True,
        description="แยกช่วงไทย/ลาว/อังกฤษ generate ด้วยภาษาที่ถูกต้องแล้วต่อเสียง (ไทยล้วน=ไม่มีผล). ดีฟอลต์เปิด")
    transliterate_english: bool = Field(True,
        description="แปลงคำอังกฤษที่พบบ่อย (ดูดิกใน text_utils.ENGLISH_TO_THAI) เป็นคำทับศัพท์ไทยก่อนอ่าน "
                    "ลดปัญหาเสียงเพี้ยน/สะดุดตอนสลับภาษา — คำที่ไม่มีในดิกยังอ่านผ่าน mixed_language แบบเดิม")
    normalize_numbers: bool = Field(True,
        description="แปลงตัวเลข (จำนวน/เงินบาท/เบอร์โทร) เป็นคำอ่านภาษาไทยก่อนอ่าน "
                    "กันปัญหาสคริปต์กับเสียงที่ได้ไม่ตรงกันตอนมีตัวเลข (ดู text_utils.normalize_thai_numbers)")


class TTSResponse(BaseModel):
    engine: str
    voice_id: Optional[str]
    text: str
    audio_base64: str
    format: str = "wav"
    sample_rate: int = SAMPLE_RATE
    duration: float
    generation_time: float
    credits_charged: float = 0.0


# ── endpoints ───────────────────────────────────────────────────────
STUDIO_PATH = os.path.join(_HERE, "studio.html")


@app.get("/", include_in_schema=False)
async def studio():
    """หน้า Web UI ทดลองใช้ทุกฟีเจอร์ (เสิร์ฟจาก server เอง = ไม่มีปัญหา CORS)"""
    if os.path.exists(STUDIO_PATH):
        return FileResponse(STUDIO_PATH, media_type="text/html")
    from fastapi.responses import HTMLResponse
    return HTMLResponse("<h1>Hero Voice TTS</h1><p>ดู API ที่ <a href='/docs'>/docs</a></p>")


@app.get("/health")
async def health():
    eng = ENGINES.get("omnivoice")
    return {
        "status": "ok" if eng and eng.model is not None else "loading",
        "device": eng.device if eng else None,
        "engines": list(ENGINES),
        "num_voices": len(eng.voices) if eng else 0,
        "credits_enabled": store is not None,
        "max_concurrency": MAX_CONCURRENCY,
    }


@app.get("/engines", dependencies=[Depends(auth)])
async def engines():
    return [
        {"id": e.id, "name": e.name, "sample_rate": e.sample_rate,
         "supports_clone": e.supports_clone, "supports_design": e.supports_design,
         "num_voices": len(e.voices)}
        for e in ENGINES.values()
    ]


@app.get("/voices", dependencies=[Depends(auth)])
async def list_voices(request: Request, engine: str = "omnivoice",
                       language: Optional[str] = None):
    """ดีฟอลต์คืนเฉพาะเสียงชุดหลัก (ไทย/อังกฤษ) — ส่ง ?language=lao เพื่อดูเสียงลาวแยกต่างหาก"""
    eng = get_engine(engine)
    base = str(request.base_url).rstrip("/")
    out = eng.list_voices(language=language)
    for v in out:
        v["preview_url"] = f"{base}/voices/{v['voice_id']}/preview?engine={engine}"
    return out


@app.get("/voices/{voice_id}/preview")  # ไม่ต้อง auth — <audio src> แนบ header ไม่ได้
async def voice_preview(voice_id: str, engine: str = "omnivoice"):
    eng = get_engine(engine)
    voice = eng.voices.get(voice_id)
    if voice is not None:
        path = voice["preview_path"]
    else:
        # เสียงโคลนถาวร — เสิร์ฟไฟล์ ref (voice_id เป็น token สุ่ม เดายาก)
        rec = library.get(voice_id)
        if rec is None:
            raise HTTPException(404, f"ไม่พบเสียง '{voice_id}'")
        path = library.audio_path(voice_id)
    return FileResponse(path, media_type="audio/wav",
                        filename=f"{voice_id}_preview.wav",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/me", dependencies=[])
async def me(keyrec=Depends(auth)):
    """ดูยอดเครดิตของ key ตัวเอง"""
    if store is None:
        return {"credits_enabled": False}
    return {"credits_enabled": True, "name": keyrec["name"],
            "credits": keyrec["credits"], "unlimited": bool(keyrec["unlimited"]),
            "rate_per_min": keyrec["rate_per_min"], "total_used": keyrec["total_used"]}


@app.post("/tts", response_model=TTSResponse)
async def tts(req: TTSRequest, keyrec=Depends(auth)):
    eng = get_engine(req.engine)

    if not req.voice_id and not req.instruct:
        raise HTTPException(422, "ต้องระบุ voice_id (สต็อก/โคลน) หรือ instruct (ออกแบบเสียง)")
    instruct = clean_instruct(req.instruct)
    clone_prompt = await resolve_clone_prompt(eng, req.voice_id, keyrec) if req.voice_id else None
    text = transliterate_english(req.text) if req.transliterate_english else req.text
    if req.normalize_numbers:
        text = normalize_thai_numbers(text)

    t = time.time()
    if req.mixed_language:
        # แยกช่วงไทย/อังกฤษ → generate แต่ละช่วงด้วยภาษาที่ถูก (เสียงเดียวกัน) → ต่อเสียง
        # (หลัง transliterate แล้วคำอังกฤษที่รู้จักจะกลายเป็นไทย เหลือแค่คำที่ไม่มีในดิกที่ยังตัดช่วง)
        wavs = []
        for seg, lang in split_by_language(text):
            w, _ = await _generate_serialized(
                eng._run, seg, clone_prompt=clone_prompt, instruct=instruct,
                language=lang, speed=req.speed, num_step=req.num_step,
                guidance_scale=req.guidance_scale, class_temperature=req.class_temperature,
            )
            wavs.append(w)
        wav = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
        duration = len(wav) / SAMPLE_RATE
    else:
        # req.language ไม่ระบุ → ใช้ "language" จาก manifest ของ voice_id เอง ถ้ามี (เช่นเสียงลาว
        # ที่ language="Lao" เสมอ — ดู voices_lao/voices.json) กัน auto-detect ของโมเดลพลาดตอน
        # ข้อความมีแต่ตัวเลข/สัญลักษณ์ที่บอกภาษาจาก unicode ไม่ได้
        lang = req.language or (eng.voices.get(req.voice_id, {}).get("meta", {}).get("language")
                                if req.voice_id else None)
        wav, duration = await _generate_serialized(
            eng._run, text, clone_prompt=clone_prompt, instruct=instruct,
            language=lang, speed=req.speed, num_step=req.num_step,
            guidance_scale=req.guidance_scale, class_temperature=req.class_temperature,
        )
    gen_time = time.time() - t
    cost = charge(keyrec, duration, "tts")
    return TTSResponse(
        engine=eng.id, voice_id=req.voice_id, text=req.text,
        audio_base64=b64(wav_bytes(wav)), duration=round(duration, 2),
        generation_time=round(gen_time, 2), credits_charged=cost,
    )


@app.post("/tts/stream")
async def tts_stream(req: TTSRequest, keyrec=Depends(auth)):
    """
    Streaming (SSE): ตัดข้อความเป็นก้อน แล้วส่งเสียงทีละก้อนทันทีที่ generate เสร็จ
    แต่ละ event = JSON: {index,total,text,audio_base64,duration}
    event สุดท้าย: {done:true,total_duration,credits_charged}
    """
    eng = get_engine(req.engine)
    if not req.voice_id and not req.instruct:
        raise HTTPException(422, "ต้องระบุ voice_id หรือ instruct")
    instruct = clean_instruct(req.instruct)
    clone_prompt = await resolve_clone_prompt(eng, req.voice_id, keyrec) if req.voice_id else None
    text = transliterate_english(req.text) if req.transliterate_english else req.text
    if req.normalize_numbers:
        text = normalize_thai_numbers(text)
    chunks = chunk_text(text)
    # ดูคอมเมนต์เดียวกันใน /tts — fallback เป็น language ของ voice_id เอง (เช่นเสียงลาว) ถ้าไม่ระบุ
    lang = req.language or (eng.voices.get(req.voice_id, {}).get("meta", {}).get("language")
                            if req.voice_id else None)

    async def gen():
        total_dur = 0.0
        for i, chunk in enumerate(chunks):
            wav, dur = await _generate_serialized(
                eng._run, chunk, clone_prompt=clone_prompt, instruct=instruct,
                language=lang, speed=req.speed, num_step=req.num_step,
                guidance_scale=req.guidance_scale, class_temperature=req.class_temperature,
            )
            total_dur += dur
            evt = {"index": i, "total": len(chunks), "text": chunk,
                   "audio_base64": b64(wav_bytes(wav)), "duration": round(dur, 2)}
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        cost = charge(keyrec, total_dur, "tts/stream")
        done = {"done": True, "total_duration": round(total_dur, 2),
                "credits_charged": cost}
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


class CloneResponse(BaseModel):
    text: str
    audio_base64: str
    format: str = "wav"
    sample_rate: int = SAMPLE_RATE
    duration: float
    generation_time: float
    credits_charged: float = 0.0
    similarity_score: Optional[float] = Field(
        None, description="ความคล้ายเสียงกับ ref_audio ของตัวที่เลือกส่งกลับ (0-1, ยิ่งสูงยิ่งคล้าย)")


# ── best-of-N (internal, ไม่เปิดให้ผู้ใช้เลือก) ──
# ทดสอบจริงด้วย ref_audio+text ชุดเดียวกัน วัด speaker similarity เทียบ N=1..5:
#   N=1: 0.79   N=2: 0.84   N=3: 0.84   N=4: 0.81   N=5: 0.86 (แกว่งแบบสุ่ม)
# กระโดดใหญ่สุดคือ N=1→2 หลังจากนั้นแกว่งไม่คุ้มเวลาที่เพิ่ม (เวลาโตเป็นเส้นตรงตาม N)
# เลือก N=3 เป็นค่ากลาง — ได้ประโยชน์เต็มจาก N=2 + กันเคสสุ่มได้ตัวแย่ทั้งคู่
_CLONE_BEST_OF = 3
_BEST_OF_CLASS_TEMPERATURE = 0.8  # ต้อง >0 ไม่งั้นทุกรอบได้ผลเหมือนกัน (greedy)

# ── ตัวชี้วัดความเป็นธรรมชาติ (naturalness) สำหรับ best-of-N ─────────────
# ปัญหา: การเลือก "ตัวที่ดีที่สุด" จากเดิมใช้แค่ speaker similarity (คล้าย ref_audio มากสุด)
# ซึ่งมีจุดอ่อน — ถ้า ref_audio ที่ผู้ใช้ส่งมาเป็นประโยคสั้นๆ อ่านแบบราบเรียบ (ไม่มีขึ้นลงของเสียง)
# ตัวที่ "คล้าย ref มากที่สุด" มักเป็นตัวที่ราบเรียบที่สุดด้วยเหมือนกัน — ทำให้ระบบเลือกตัวหุ่นยนต์/
# ไม่เป็นธรรมชาติซ้ำๆ ทั้งที่มี candidate อื่นที่ออกเสียงเป็นธรรมชาติกว่าแต่คะแนนความคล้ายต่ำกว่านิดหน่อย
# แก้โดยเพิ่มคะแนน "pitch variation" (ส่วนเบี่ยงเบนมาตรฐานของ f0 หน่วย semitone) เข้าไปถ่วงน้ำหนัก
# ร่วมกับ similarity — ตัวที่มีทำนองเสียงขึ้นลงมากกว่า (ธรรมชาติกว่า) ได้คะแนนเพิ่ม ไม่ถูกตัดออกเพียง
# เพราะคล้าย ref น้อยกว่าตัวที่แบนที่สุดนิดเดียว
# หมายเหตุ: ค่า _NATURALNESS_WEIGHT นี้เป็นการประมาณตามหลักการ (CFG guidance ที่สูงเกินไป/greedy
# sampling ทำให้เสียงแบน) ยังไม่ได้วัดผลจริงแบบ N=1..5 ด้านบน (GPU เครื่องนี้ใช้งานไม่ได้ตอนแก้)
# ควรทดสอบฟังเทียบก่อน-หลังจริงแล้วปรับตัวเลขนี้ต่อ
_NATURALNESS_WEIGHT = 0.15


def _pitch_variation(wav: np.ndarray, sr: int) -> float:
    """ประมาณความหลากหลายของทำนองเสียง (ส่วนเบี่ยงเบนมาตรฐานของ f0 หน่วย semitone)
    ยิ่งสูง = เสียงมีขึ้นลงเป็นธรรมชาติมากกว่า, ใกล้ 0 = เสียงราบเรียบ/หุ่นยนต์
    error/ไม่มีช่วงที่ออกเสียงพอ → คืน 0.0 (ไม่ throw ทำให้ best-of-N ล้ม)"""
    try:
        import librosa
        f0, voiced_flag, _ = librosa.pyin(
            wav, sr=sr, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6")
        )
        f0 = f0[voiced_flag.astype(bool)]
        f0 = f0[~np.isnan(f0)]
        if len(f0) < 4:
            return 0.0
        semitones = 12 * np.log2(f0 / np.median(f0))
        return float(np.std(semitones))
    except Exception:
        return 0.0

# ── คุณภาพ ref_audio ─────────────────────────────────────────────────
# เมื่อ "อีกฝั่ง" (พาร์ทเนอร์/ลูกค้า) เป็นคนส่งไฟล์เสียงมาให้โคลน เราควบคุมคุณภาพไฟล์ต้นทาง
# ไม่ได้ (มือถือ, มีเสียงรบกวน, ยาว/สั้นเกินไป) — เอนจินยึดตามคำแนะนำที่ทุก endpoint บอกไว้แล้วว่า
# "3-10 วิ" แต่ไม่เคยบังคับจริง จึงตรวจ/แก้ให้อัตโนมัติที่นี่แทนที่จะพึ่งให้ผู้ส่งไฟล์ทำถูกเอง:
#   - สั้นกว่า _REF_MIN_SEC: ปฏิเสธ (สั้นเกินไปจนจับลักษณะเสียงของคนพูดไม่ได้แม่นยำ)
#   - ยาวกว่า _REF_MAX_SEC: ตัดสั้นลงอัตโนมัติเหลือ _REF_TRIM_SEC วิแรก (ไม่ปฏิเสธ ผู้ส่งไฟล์
#     ไม่จำเป็นต้องรู้ข้อจำกัดนี้มาก่อน — ยาวเกินไม่ได้ช่วยให้โคลนดีขึ้น แค่ทำให้ช้าลงเฉยๆ)
_REF_MIN_SEC = 2.0
_REF_MAX_SEC = 15.0
_REF_TRIM_SEC = 12.0


def _ensure_ref_quality(path: str) -> float:
    """ตรวจความยาวไฟล์ ref_audio ที่ path (in-place) → คืนความยาว (วินาที) หลังตรวจ/ตัด
    ปฏิเสธ (422) ถ้าสั้นเกินไป, ตัดอัตโนมัติถ้ายาวเกินไป — ใช้ร่วมกันทั้ง /clone และ /voices"""
    info = sf.info(path)
    duration = info.duration
    if duration < _REF_MIN_SEC:
        raise HTTPException(
            422,
            f"ไฟล์เสียงอ้างอิงสั้นเกินไป ({duration:.1f} วินาที) — "
            f"ต้องการอย่างน้อย {_REF_MIN_SEC:.0f} วินาทีเพื่อให้จับลักษณะเสียงได้แม่นยำ",
        )
    if duration > _REF_MAX_SEC:
        wav, sr = sf.read(path, dtype="float32")
        wav = wav[: int(_REF_TRIM_SEC * sr)]
        sf.write(path, wav, sr, format="WAV")
        return _REF_TRIM_SEC
    return duration


@app.post("/clone", response_model=CloneResponse)
async def clone(
    ref_audio: UploadFile = File(..., description="ไฟล์เสียงตัวอย่าง 3-10 วิ (wav/mp3)"),
    ref_text: str = Form(..., min_length=1),
    text: str = Form(..., min_length=1),
    engine: str = Form("omnivoice"),
    language: Optional[str] = Form(None),
    # เดิม default=16 (ครึ่งเดียวของค่าที่ใช้สร้างเสียงสต็อกใน build_voices.py และ
    # ต่ำกว่า default ของโมเดลเอง=32) ทำให้เสียงโคลนแตก/เพี้ยนกว่าเสียงสต็อกอย่างชัดเจน
    # ปรับกลับมาเท่ากับที่ใช้สร้างเสียงสต็อก
    num_step: int = Form(32, ge=4, le=64),
    # เดิม 2.5 (สูงกว่า default ของโมเดล 2.0) เพื่อยึดเสียงต้นฉบับมากขึ้นสำหรับงาน cloning —
    # แต่ guidance_scale ที่สูงเกินไปมีผลข้างเคียงแบบเดียวกับ CFG ใน diffusion ทั่วไป: ยึดเงื่อนไข
    # แน่นเกินจนเสียงขาดความหลากหลายตามธรรมชาติ ฟังดูแบน/หุ่นยนต์ (ผู้ใช้รายงานว่าเสียงโคลนไม่เป็น
    # ธรรมชาติ) ปรับกลับมาที่ default ของโมเดล (2.0) ให้มีพื้นที่ออกเสียงเป็นธรรมชาติมากขึ้น
    # ยังไม่ได้วัดผลจริง (GPU เครื่องนี้ใช้งานไม่ได้ตอนแก้) — ลองฟังเทียบก่อน-หลังได้เมื่อ GPU กลับมา
    guidance_scale: Optional[float] = Form(2.0, ge=1.0, le=5.0),
    speed: float = Form(1.0, gt=0.3, lt=3.0),
    # ดีฟอลต์เปิด (เดิม False) — "อีกฝั่ง" ที่ส่งไฟล์มาโคลนมักไม่รู้/ไม่สนใจพารามิเตอร์นี้
    # เราจึงทำความสะอาดให้อัตโนมัติเสมอเพื่อการันตีคุณภาพโคลนที่ดีที่สุดโดยไม่ต้องพึ่งฝั่งผู้ส่ง
    # (enhance() ออกแบบให้ fail-safe — ถ้า Demucs ใช้ไม่ได้/พังกลางทาง คืนไฟล์เดิมเสมอ ไม่ throw)
    enhance_ref: bool = Form(True,
        description="ลดเสียงรบกวนพื้นหลัง/เพิ่มความชัดของไฟล์ ref ก่อนโคลน (ดู /enhance) "
                    "เปิดอยู่โดยดีฟอลต์ — ปิดได้ถ้าไฟล์สะอาดอยู่แล้วและอยากให้เร็วขึ้น"),
    keyrec=Depends(auth),
):
    """
    โคลนเสียงจากไฟล์อัปโหลด — ไม่เก็บไฟล์ถาวร
    ภายในระบบจะ generate หลายรอบแล้วเลือกตัวที่คล้าย ref_audio ที่สุดให้อัตโนมัติ
    (ดู _CLONE_BEST_OF) — ไม่ต้องตั้งค่าอะไรเพิ่ม
    """
    eng = get_engine(engine)
    suffix = os.path.splitext(ref_audio.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await ref_audio.read())
        tmp_path = tmp.name

    try:
        _ensure_ref_quality(tmp_path)  # ปฏิเสธถ้าสั้นเกินไป / ตัดอัตโนมัติถ้ายาวเกินไป

        if enhance_ref:
            try:
                ewav, esr = await asyncio.to_thread(audio_enhance.enhance, tmp_path)
                sf.write(tmp_path, ewav, esr, format="WAV")
            except Exception as e:
                print(f"[clone] enhance_ref ล้มเหลว (ใช้ไฟล์เดิม): {e}")

        def _clone_run():
            with torch.no_grad():
                prompt = eng.model.create_voice_clone_prompt(ref_audio=tmp_path, ref_text=ref_text)

            from voice_similarity import embed_file, embed_array, cosine_sim
            ref_emb = embed_file(tmp_path)
            # generate ทั้ง N ตัวก่อน ค่อยให้คะแนน — ต้องรู้ pitch variation ของทุกตัวก่อนจะ
            # normalize ข้ามกันได้ (ดู _NATURALNESS_WEIGHT ด้านบน)
            candidates = []  # [(wav, dur, sim_score, pitch_var), ...]
            for _ in range(_CLONE_BEST_OF):
                wav, dur = eng._run(text, clone_prompt=prompt, language=language, speed=speed,
                                    num_step=num_step, guidance_scale=guidance_scale,
                                    class_temperature=_BEST_OF_CLASS_TEMPERATURE)
                sim = cosine_sim(ref_emb, embed_array(wav, SAMPLE_RATE))
                pitch_var = _pitch_variation(wav, SAMPLE_RATE)
                candidates.append((wav, dur, sim, pitch_var))

            pitch_vars = [c[3] for c in candidates]
            lo, hi = min(pitch_vars), max(pitch_vars)
            spread = hi - lo
            best_wav, best_dur, best_sim, best_blended = None, None, None, -1.0
            for wav, dur, sim, pitch_var in candidates:
                pitch_norm = (pitch_var - lo) / spread if spread > 1e-6 else 0.0
                blended = sim + _NATURALNESS_WEIGHT * pitch_norm
                if blended > best_blended:
                    best_wav, best_dur, best_sim, best_blended = wav, dur, sim, blended
            return best_wav, best_dur, best_sim

        t = time.time()
        wav, duration, sim_score = await _generate_serialized(_clone_run)
        gen_time = time.time() - t
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"โคลนเสียงไม่สำเร็จ: {e}")
    finally:
        os.remove(tmp_path)

    cost = charge(keyrec, duration, "clone")
    return CloneResponse(text=text, audio_base64=b64(wav_bytes(wav)),
                         duration=round(duration, 2), generation_time=round(gen_time, 2),
                         credits_charged=cost, similarity_score=round(sim_score, 4))


# ── คลังเสียงโคลนถาวร (custom voices) ────────────────────────────────
def _load_wav_bytes(path: str) -> bytes:
    """อ่านไฟล์เสียงฟอร์แมตใดก็ได้ → wav 24k mono (normalize ก่อนเก็บ)"""
    import librosa
    y, _ = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    buf = io.BytesIO()
    sf.write(buf, y, SAMPLE_RATE, format="WAV")
    return buf.getvalue()


class CreateVoiceResponse(BaseModel):
    voice_id: str
    name: str
    ref_text: str
    preview_url: Optional[str] = None


@app.post("/voices", response_model=CreateVoiceResponse)
async def create_voice(
    request: Request,
    ref_audio: UploadFile = File(..., description="ไฟล์เสียงตัวอย่าง 3-10 วิ (wav/mp3)"),
    name: str = Form("", description="ชื่อเสียง (แสดงให้ผู้ใช้)"),
    ref_text: Optional[str] = Form(None, description="ข้อความในไฟล์ ref — เว้นว่าง = ถอดอัตโนมัติ (ASR)"),
    engine: str = Form("omnivoice"),
    # ดีฟอลต์เปิด (เดิม False) — เหตุผลเดียวกับ /clone: เจ้าของเสียงที่ส่งไฟล์มาไม่จำเป็นต้องรู้จัก
    # พารามิเตอร์นี้ แต่เราอยากการันตีคุณภาพเสียงโคลนถาวรที่ดีที่สุดให้เสมอ
    enhance_ref: bool = Form(True,
        description="ลดเสียงรบกวนพื้นหลัง/เพิ่มความชัดของไฟล์ ref ก่อนเก็บ (ดู /enhance) "
                    "เปิดอยู่โดยดีฟอลต์ — ปิดได้ถ้าไฟล์สะอาดอยู่แล้วและอยากให้เร็วขึ้น"),
    keyrec=Depends(auth),
):
    """
    สร้าง "เสียงโคลนถาวร" จากไฟล์อัปโหลด → เก็บไว้ใช้ซ้ำได้เหมือนเสียงสต็อก
    คืน voice_id (ขึ้นต้น cv_) เอาไปใส่ใน /tts ได้เลย
    """
    eng = get_engine(engine)
    suffix = os.path.splitext(ref_audio.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await ref_audio.read())
        tmp_path = tmp.name
    try:
        _ensure_ref_quality(tmp_path)  # ปฏิเสธถ้าสั้นเกินไป / ตัดอัตโนมัติถ้ายาวเกินไป

        if enhance_ref:
            try:
                ewav, esr = await asyncio.to_thread(audio_enhance.enhance, tmp_path)
                sf.write(tmp_path, ewav, esr, format="WAV")
            except Exception as e:
                print(f"[voices] enhance_ref ล้มเหลว (ใช้ไฟล์เดิม): {e}")

        if not ref_text or not ref_text.strip():
            ref_text = (await _generate_serialized(eng.transcribe, tmp_path)).strip()
            if not ref_text:
                raise HTTPException(422, "ถอดเสียง ref อัตโนมัติไม่สำเร็จ — กรุณาระบุ ref_text เอง")
        wav = await asyncio.to_thread(_load_wav_bytes, tmp_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"อ่านไฟล์เสียงไม่สำเร็จ: {e}")
    finally:
        os.remove(tmp_path)

    rec = library.create(owner=owner_id(keyrec), name=name, ref_text=ref_text, wav_bytes=wav)
    base = str(request.base_url).rstrip("/")
    return CreateVoiceResponse(
        voice_id=rec["voice_id"], name=rec["name"], ref_text=rec["ref_text"],
        preview_url=f"{base}/voices/{rec['voice_id']}/preview",
    )


@app.get("/voices/mine")
async def my_voices(request: Request, keyrec=Depends(auth)):
    """รายการเสียงโคลนถาวรของ key ตัวเอง (+ เสียง public)"""
    base = str(request.base_url).rstrip("/")
    out = library.list(owner_id(keyrec))
    for v in out:
        v["preview_url"] = f"{base}/voices/{v['voice_id']}/preview"
    return out


@app.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str, keyrec=Depends(auth)):
    """ลบเสียงโคลนถาวร (เฉพาะเจ้าของ)"""
    if not library.delete(voice_id, owner_id(keyrec)):
        raise HTTPException(404, f"ไม่พบเสียง '{voice_id}' หรือไม่ใช่เจ้าของ")
    ENGINES["omnivoice"].cache_drop(voice_id)
    return {"deleted": voice_id}


# ── ASR (ถอดเสียง) ───────────────────────────────────────────────────
@app.post("/transcribe")
async def transcribe_ep(
    audio: UploadFile = File(..., description="ไฟล์เสียงที่จะถอดเป็นข้อความ"),
    engine: str = Form("omnivoice"),
    keyrec=Depends(auth),
):
    eng = get_engine(engine)
    suffix = os.path.splitext(audio.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        text = await _generate_serialized(eng.transcribe, tmp_path)
    except Exception as e:
        raise HTTPException(422, f"ถอดเสียงไม่สำเร็จ: {e}")
    finally:
        os.remove(tmp_path)
    return {"text": text}


class EnhanceResponse(BaseModel):
    audio_base64: str
    format: str = "wav"
    sample_rate: int
    duration: float


# ── Enhance (ลดเสียงรบกวน + เพิ่มความชัด) ──────────────────────────────
@app.post("/enhance", response_model=EnhanceResponse)
async def enhance_ep(
    audio: UploadFile = File(..., description="ไฟล์เสียงที่ต้องการลดเสียงรบกวน/เพิ่มความชัด"),
    keyrec=Depends(auth),
):
    """แยกเสียงพูดออกจากเสียงพื้นหลัง/รบกวน (Demucs) + normalize ความดัง — คืนไฟล์เสียงที่ทำความสะอาดแล้ว
    ใช้เดี่ยวๆ ได้ หรือเอาไปเป็น ref_audio ต่อใน /clone, /voices (ดู enhance_ref param)"""
    suffix = os.path.splitext(audio.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        wav, sr = await asyncio.to_thread(audio_enhance.enhance, tmp_path)
    except Exception as e:
        raise HTTPException(422, f"ลดเสียงรบกวนไม่สำเร็จ: {e}")
    finally:
        os.remove(tmp_path)
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return EnhanceResponse(audio_base64=b64(buf.getvalue()), sample_rate=sr,
                           duration=round(len(wav) / sr, 2))


# ── OpenAI-compatible endpoint ───────────────────────────────────────
def _encode_audio(wav: np.ndarray, fmt: str):
    """แปลง wav float → bytes ตามฟอร์แมต OpenAI (คืน (bytes, content_type))"""
    fmt = (fmt or "wav").lower()
    if fmt in ("wav", "flac", "ogg"):
        buf = io.BytesIO()
        sf.write(buf, wav, SAMPLE_RATE, format={"ogg": "OGG"}.get(fmt, fmt.upper()))
        return buf.getvalue(), {"wav": "audio/wav", "flac": "audio/flac",
                                "ogg": "audio/ogg"}[fmt]
    if fmt == "pcm":
        return (np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes(), "audio/pcm"
    if fmt in ("mp3", "opus", "aac"):
        try:
            from pydub import AudioSegment
            pcm = (np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes()
            seg = AudioSegment(pcm, frame_rate=SAMPLE_RATE, sample_width=2, channels=1)
            buf = io.BytesIO()
            seg.export(buf, format=fmt)
            return buf.getvalue(), {"mp3": "audio/mpeg", "opus": "audio/opus",
                                    "aac": "audio/aac"}[fmt]
        except Exception as e:
            raise HTTPException(422, f"ฟอร์แมต {fmt} ต้องมี ffmpeg/pydub: {e}")
    raise HTTPException(422, f"response_format ไม่รองรับ: {fmt}")


class OpenAISpeechRequest(BaseModel):
    model: str = Field("hero-voice-1", description="ไม่ใช้จริง — เผื่อ compatibility")
    input: str = Field(..., min_length=1, description="ข้อความ")
    voice: str = Field(..., description="voice_id (สต็อก/โคลน)")
    response_format: str = Field("mp3", description="mp3/wav/flac/opus/aac/pcm")
    speed: float = Field(1.0, ge=0.25, le=4.0)


@app.post("/v1/audio/speech")
async def openai_speech(req: OpenAISpeechRequest, keyrec=Depends(auth)):
    """เข้ากันได้กับ OpenAI TTS API — ให้ client ที่เขียนไว้คุยกับ OpenAI เปลี่ยนแค่ base URL"""
    eng = get_engine("omnivoice")
    speed = min(max(req.speed, 0.31), 2.99)  # จำกัดตามช่วงที่โมเดลรับได้
    clone_prompt = await resolve_clone_prompt(eng, req.voice, keyrec)
    wav, duration = await _generate_serialized(
        eng._run, req.input, clone_prompt=clone_prompt, speed=speed,
    )
    charge(keyrec, duration, "v1/audio/speech")
    data, ctype = _encode_audio(wav, req.response_format)
    return Response(content=data, media_type=ctype)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
