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

รัน:
  python build_voices.py     # ครั้งเดียว สร้างคลังเสียง
  python server.py           # http://0.0.0.0:8000  (Swagger: /docs)
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
from text_utils import chunk_text, split_by_language
from voice_library import VoiceLibrary

# ── config ──────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.environ.get("TTS_MODEL_DIR", os.path.join(_BASE, "model"))
VOICES_DIR = os.environ.get("TTS_VOICES_DIR", os.path.join(_BASE, "voices"))
SAMPLE_RATE = 24000

SERVED_VOICE_IDS = os.environ.get(
    "TTS_VOICE_IDS", ",".join(f"voice_{i:02d}" for i in range(1, 49))
).split(",")

MAX_CONCURRENCY = int(os.environ.get("TTS_MAX_CONCURRENCY", "2"))
CORS_ORIGINS = os.environ.get("TTS_CORS_ORIGINS", "*").split(",")

# คลังเสียงโคลนถาวร (custom voices)
CUSTOM_VOICES_DIR = os.environ.get("TTS_CUSTOM_VOICES_DIR", os.path.join(_BASE, "custom_voices"))
VOICES_DB = os.environ.get("TTS_VOICES_DB", os.path.join(CUSTOM_VOICES_DIR, "voices.db"))
# จำนวน clone-prompt ที่ cache ในแรม (เกินนี้ evict ตัวเก่าสุด) — กันแรมบวมเมื่อมีเสียงเยอะ
PROMPT_CACHE_SIZE = int(os.environ.get("TTS_PROMPT_CACHE_SIZE", "64"))

# ASR (ถอดเสียง) — โหลด lazy ครั้งแรกที่ใช้ (โมเดล Whisper ~1.5GB ดาวน์โหลดแยก)
ASR_MODEL = os.environ.get("TTS_ASR_MODEL", "openai/whisper-large-v3-turbo")

# ตัวคูณความเร็วฐาน: โมเดลพูดช้ากว่าธรรมชาติ → คูณให้ slider 1.0 = ความเร็วคนจริง
# ผู้ใช้ตั้ง speed=1.0 → โมเดลได้ speed = 1.0 * BASE_SPEED. ปรับจูนได้ตามชอบ
BASE_SPEED = float(os.environ.get("TTS_BASE_SPEED", "1.4"))

# เอนจินที่ 2: IndexTTS-2 (cloning เหมือนสูง + อารมณ์) — เปิดด้วย TTS_ENABLE_INDEXTTS=1 (ต้อง GPU + ติดตั้ง)
ENABLE_INDEXTTS = os.environ.get("TTS_ENABLE_INDEXTTS", "") == "1"

# auth: ถ้าตั้ง TTS_CREDITS_DB → ใช้ระบบเครดิต (หลาย key แยกยอด); ไม่งั้นใช้ TTS_API_KEY เดี่ยว
CREDITS_DB = os.environ.get("TTS_CREDITS_DB")
API_KEY = os.environ.get("TTS_API_KEY")
COST_PER_SECOND = float(os.environ.get("TTS_COST_PER_SECOND", "1.0"))  # เครดิต/วินาทีเสียง

# instruct ที่ OmniVoice ยอมรับ (กันส่งค่ามั่ว → error กลางคัน)
_ALLOWED_INSTRUCT = {
    "male", "female",
    "child", "teenager", "young adult", "middle-aged", "elderly",
    "very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch",
    "whisper",
    "american accent", "british accent", "australian accent", "indian accent",
    "irish accent", "scottish accent", "canadian accent",
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
        self._asr_loaded = False

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
            raise RuntimeError(f"{manifest_path} not found — run `python build_voices.py` first")
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

    def list_voices(self):
        return [
            {"voice_id": vid, "desc": v["meta"].get("desc", ""),
             "instruct": v["meta"].get("instruct", "")}
            for vid, v in self.voices.items()
        ]

    def _run(self, text, *, voice_id=None, clone_prompt=None, instruct=None,
             language=None, speed=1.0, num_step=24, guidance_scale=None,
             class_temperature=None):
        """generate หนึ่งก้อน (blocking) → (wav float32 ndarray, duration_sec)"""
        # คูณความเร็วฐาน (slider 1.0 = ธรรมชาติ) แล้ว clamp ให้อยู่ในช่วงที่โมเดลรับได้
        eff_speed = max(0.31, min(2.99, speed * BASE_SPEED))
        kwargs = dict(text=text, speed=eff_speed,
                      generation_config=self._gcfg(num_step, guidance_scale, class_temperature))
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

    # ── ASR (ถอดเสียง) ──
    def ensure_asr(self):
        if not self._asr_loaded:
            print(f"[omnivoice] loading ASR model: {ASR_MODEL} ...")
            self.model.load_asr_model(ASR_MODEL)
            self._asr_loaded = True

    def transcribe(self, audio_path):
        self.ensure_asr()
        return self.model.transcribe(audio_path)


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


def resolve_ref(voice_id: str, keyrec):
    """คืน (ref_wav_path, ref_text) จาก voice_id — ใช้กับเอนจินที่รับไฟล์ ref ตรงๆ (เช่น IndexTTS)
    รองรับทั้งเสียงสต็อก (จาก manifest ของ omnivoice) และเสียงโคลนถาวร"""
    stock = ENGINES["omnivoice"].voices.get(voice_id)
    if stock is not None:
        return stock["ref_audio"], stock["meta"].get("ref_text", "")
    rec = library.get(voice_id)
    if rec is None or not library.can_use(rec, owner_id(keyrec)):
        raise HTTPException(404, f"ไม่พบเสียง '{voice_id}'")
    return library.audio_path(voice_id), rec.get("ref_text", "")


# ── lifespan ────────────────────────────────────────────────────────
STATE = {"sem": None, "lock": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    eng = OmniVoiceEngine()
    eng.load()
    ENGINES[eng.id] = eng
    # เอนจินเสริม IndexTTS-2 (optional) — โหลดไม่ได้ก็ข้าม ไม่ให้ server ล้ม
    if ENABLE_INDEXTTS:
        try:
            from engine_indextts import IndexTTS2Engine
            ix = IndexTTS2Engine()
            ix.load()
            ENGINES[ix.id] = ix
        except Exception as e:
            print(f"[indextts2] ปิดใช้งาน (โหลดไม่สำเร็จ): {e}")
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
    language: Optional[str] = Field(None, description="เช่น 'Thai', 'English' (ปล่อยว่าง=auto)")
    num_step: int = Field(24, ge=4, le=64, description="สูง=คุณภาพ/ความคล้ายดีขึ้นแต่ช้าลง")
    speed: float = Field(1.0, gt=0.3, lt=3.0)
    guidance_scale: Optional[float] = Field(None, ge=1.0, le=5.0,
        description="คุมความยึดเสียงต้นฉบับ (ดีฟอลต์ 2.0); 3-4 = คล้ายขึ้นแต่เสี่ยงเพี้ยน")
    mixed_language: bool = Field(True,
        description="แยกช่วงไทย/อังกฤษ generate ด้วยภาษาที่ถูกต้องแล้วต่อเสียง (ไทยล้วน=ไม่มีผล). ดีฟอลต์เปิด")
    emotion: Optional[str] = Field(None,
        description="อารมณ์ (เฉพาะเอนจินที่รองรับ เช่น IndexTTS) เช่น 'happy','sad','angry','excited'")


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
STUDIO_PATH = os.path.join(_BASE, "studio.html")


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
         "supports_emotion": getattr(e, "supports_emotion", False),
         "num_voices": len(e.voices)}
        for e in ENGINES.values()
    ]


@app.get("/voices", dependencies=[Depends(auth)])
async def list_voices(request: Request, engine: str = "omnivoice"):
    eng = get_engine(engine)
    base = str(request.base_url).rstrip("/")
    out = eng.list_voices()
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

    # เอนจินอื่นที่รับ ref ตรงๆ (เช่น IndexTTS) — ใช้ .synth() รองรับอารมณ์
    if eng.id != "omnivoice":
        if not req.voice_id:
            raise HTTPException(422, f"เอนจิน '{eng.id}' ต้องระบุ voice_id (เสียง ref)")
        ref_wav, ref_text = resolve_ref(req.voice_id, keyrec)
        t = time.time()
        wav, _ = await _generate_serialized(eng.synth, req.text, ref_wav,
                                            ref_text, req.emotion, req.speed)
        gen_time = time.time() - t
        duration = len(wav) / SAMPLE_RATE
        cost = charge(keyrec, duration, "tts")
        return TTSResponse(engine=eng.id, voice_id=req.voice_id, text=req.text,
                           audio_base64=b64(wav_bytes(wav)), duration=round(duration, 2),
                           generation_time=round(gen_time, 2), credits_charged=cost)

    if not req.voice_id and not req.instruct:
        raise HTTPException(422, "ต้องระบุ voice_id (สต็อก/โคลน) หรือ instruct (ออกแบบเสียง)")
    instruct = clean_instruct(req.instruct)
    clone_prompt = await resolve_clone_prompt(eng, req.voice_id, keyrec) if req.voice_id else None

    t = time.time()
    if req.mixed_language:
        # แยกช่วงไทย/อังกฤษ → generate แต่ละช่วงด้วยภาษาที่ถูก (เสียงเดียวกัน) → ต่อเสียง
        wavs = []
        for seg, lang in split_by_language(req.text):
            w, _ = await _generate_serialized(
                eng._run, seg, clone_prompt=clone_prompt, instruct=instruct,
                language=lang, speed=req.speed, num_step=req.num_step,
                guidance_scale=req.guidance_scale,
            )
            wavs.append(w)
        wav = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
        duration = len(wav) / SAMPLE_RATE
    else:
        wav, duration = await _generate_serialized(
            eng._run, req.text, clone_prompt=clone_prompt, instruct=instruct,
            language=req.language, speed=req.speed, num_step=req.num_step,
            guidance_scale=req.guidance_scale,
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
    chunks = chunk_text(req.text)

    async def gen():
        total_dur = 0.0
        for i, chunk in enumerate(chunks):
            wav, dur = await _generate_serialized(
                eng._run, chunk, clone_prompt=clone_prompt, instruct=instruct,
                language=req.language, speed=req.speed, num_step=req.num_step,
                guidance_scale=req.guidance_scale,
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
    # guidance_scale สูงขึ้นเล็กน้อยจาก default ของโมเดล (2.0) เพื่อยึดเสียงต้นฉบับ
    # มากขึ้นสำหรับงาน cloning โดยเฉพาะ — ไม่ขึ้นถึง 3-4 ที่เสี่ยงเสียงเพี้ยน (ดู TTSRequest.guidance_scale)
    guidance_scale: Optional[float] = Form(2.5, ge=1.0, le=5.0),
    speed: float = Form(1.0, gt=0.3, lt=3.0),
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

    def _clone_run():
        with torch.no_grad():
            prompt = eng.model.create_voice_clone_prompt(ref_audio=tmp_path, ref_text=ref_text)

        from voice_similarity import embed_file, embed_array, cosine_sim
        ref_emb = embed_file(tmp_path)
        best_wav, best_dur, best_score = None, None, -1.0
        for _ in range(_CLONE_BEST_OF):
            wav, dur = eng._run(text, clone_prompt=prompt, language=language, speed=speed,
                                num_step=num_step, guidance_scale=guidance_scale,
                                class_temperature=_BEST_OF_CLASS_TEMPERATURE)
            score = cosine_sim(ref_emb, embed_array(wav, SAMPLE_RATE))
            if score > best_score:
                best_wav, best_dur, best_score = wav, dur, score
        return best_wav, best_dur, best_score

    try:
        t = time.time()
        wav, duration, sim_score = await _generate_serialized(_clone_run)
        gen_time = time.time() - t
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
