"""
runpod_handler.py — RunPod Serverless entrypoint

ใช้ logic เดียวกับ server.py (OmniVoiceEngine, best-of-N clone, mixed-language)
แต่ไม่มี FastAPI/HTTP — RunPod เรียก handler(job) ตรงๆ ผ่านคิวของเขาเอง

ต่างจาก server.py (Pod ปกติ):
  - ไม่ต้องมี TTS_API_KEY/เครดิตของเราเอง — RunPod บังคับให้ผู้เรียกต้องมี
    RUNPOD_API_KEY ของบัญชีคุณอยู่แล้วก่อนจะยิง endpoint นี้ได้ (auth ชั้นนอกทำให้แล้ว)
  - รับ ref_audio เป็น base64 ในฟิลด์ ref_audio_b64 (ไม่ใช่ multipart upload)
  - ไม่มีคลังเสียงโคลนถาวร/ASR/streaming — ถ้าต้องการเพิ่มทีหลังได้

รูปแบบ input (job["input"]):
  TTS จากเสียงสต็อก:
    {"mode": "tts", "voice_id": "voice_01", "text": "สวัสดีครับ"}
  TTS แบบออกแบบเสียง (voice design):
    {"mode": "tts", "instruct": "female, high pitch", "text": "..."}
    optional ทั้งคู่: language, speed, num_step, guidance_scale, mixed_language, transliterate_english, normalize_numbers

  Voice cloning (best-of-N อัตโนมัติเหมือน /clone บน server.py):
    {"mode": "clone", "ref_audio_b64": "<base64 wav/mp3>", "ref_text": "...", "text": "..."}
    optional: speed, num_step, guidance_scale

Output (job สำเร็จ):
  {"audio_base64": "...", "format": "wav", "sample_rate": 24000,
   "duration": 1.8, "generation_time": 0.6, "similarity_score": 0.84 (เฉพาะ clone)}
Output (error): {"error": "ข้อความอธิบาย"}

ทดสอบ logic นี้แบบไม่ต้องพึ่ง RunPod cloud เลย:
  python tests/test_runpod_handler.py
"""
import base64
import os
import tempfile
import time

import numpy as np

from server import OmniVoiceEngine, SAMPLE_RATE, wav_bytes, b64, clean_instruct, \
    _CLONE_BEST_OF, _BEST_OF_CLASS_TEMPERATURE
from text_utils import normalize_thai_numbers, split_by_language, transliterate_english

# โหลดโมเดลครั้งเดียวตอน import (cold start ของ worker) — ไม่ใช่ทุก job
print("[handler] loading OmniVoice engine ...")
ENGINE = OmniVoiceEngine()
ENGINE.load()
print(f"[handler] ready — {len(ENGINE.voices)} เสียงสต็อก, device={ENGINE.device}")


def _resolve_stock_prompt(voice_id: str):
    if voice_id not in ENGINE.voices:
        raise ValueError(f"ไม่พบเสียง '{voice_id}' (มี: {list(ENGINE.voices)})")
    prompt = ENGINE.cache_get(voice_id)
    if prompt is None:
        v = ENGINE.voices[voice_id]
        prompt = ENGINE.build_prompt(v["ref_audio"], v["meta"]["ref_text"])
        ENGINE.cache_put(voice_id, prompt)
    return prompt


def _do_tts(inp: dict) -> dict:
    text = inp.get("text")
    if not text:
        raise ValueError("ต้องระบุ text")
    voice_id = inp.get("voice_id")
    instruct = clean_instruct(inp.get("instruct"))
    if not voice_id and not instruct:
        raise ValueError("ต้องระบุ voice_id (เสียงสต็อก) หรือ instruct (ออกแบบเสียง)")
    clone_prompt = _resolve_stock_prompt(voice_id) if voice_id else None

    speed = float(inp.get("speed", 1.0))
    num_step = int(inp.get("num_step", 24))
    guidance_scale = inp.get("guidance_scale")
    language = inp.get("language")
    mixed_language = inp.get("mixed_language", True)
    if inp.get("transliterate_english", True):
        text = transliterate_english(text)
    if inp.get("normalize_numbers", True):
        text = normalize_thai_numbers(text)

    t = time.time()
    if mixed_language:
        # แยกช่วงไทย/อังกฤษ generate ด้วยภาษาที่ถูกต้อง แล้วต่อเสียง (เหมือน server.py /tts)
        wavs = []
        for seg, lang in split_by_language(text):
            w, _ = ENGINE._run(seg, clone_prompt=clone_prompt, instruct=instruct,
                               language=lang, speed=speed, num_step=num_step,
                               guidance_scale=guidance_scale)
            wavs.append(w)
        wav = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
    else:
        wav, _ = ENGINE._run(text, clone_prompt=clone_prompt, instruct=instruct,
                             language=language, speed=speed, num_step=num_step,
                             guidance_scale=guidance_scale)
    gen_time = time.time() - t
    duration = len(wav) / SAMPLE_RATE

    return {
        "voice_id": voice_id, "text": text,
        "audio_base64": b64(wav_bytes(wav)), "format": "wav", "sample_rate": SAMPLE_RATE,
        "duration": round(duration, 2), "generation_time": round(gen_time, 2),
    }


def _do_clone(inp: dict) -> dict:
    ref_b64 = inp.get("ref_audio_b64")
    ref_text = inp.get("ref_text")
    text = inp.get("text")
    if not ref_b64 or not ref_text or not text:
        raise ValueError("ต้องระบุ ref_audio_b64, ref_text, text ทั้งสามอย่าง")

    speed = float(inp.get("speed", 1.0))
    num_step = int(inp.get("num_step", 32))          # เท่ากับ default ของ server.py /clone
    guidance_scale = inp.get("guidance_scale", 2.5)   # เท่ากับ default ของ server.py /clone

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(base64.b64decode(ref_b64))
            tmp_path = tmp.name

        prompt = ENGINE.model.create_voice_clone_prompt(ref_audio=tmp_path, ref_text=ref_text)

        # best-of-N เหมือน server.py /clone เป๊ะ — generate หลายรอบ เลือกตัวที่คล้าย ref สุด
        from voice_similarity import embed_file, embed_array, cosine_sim
        ref_emb = embed_file(tmp_path)

        t = time.time()
        best_wav, best_score = None, -1.0
        for _ in range(_CLONE_BEST_OF):
            wav, _ = ENGINE._run(text, clone_prompt=prompt, speed=speed, num_step=num_step,
                                 guidance_scale=guidance_scale,
                                 class_temperature=_BEST_OF_CLASS_TEMPERATURE)
            score = cosine_sim(ref_emb, embed_array(wav, SAMPLE_RATE))
            if score > best_score:
                best_wav, best_score = wav, score
        gen_time = time.time() - t
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    duration = len(best_wav) / SAMPLE_RATE
    return {
        "text": text, "audio_base64": b64(wav_bytes(best_wav)), "format": "wav",
        "sample_rate": SAMPLE_RATE, "duration": round(duration, 2),
        "generation_time": round(gen_time, 2), "similarity_score": round(best_score, 4),
    }


def handler(job):
    inp = job.get("input") or {}
    mode = inp.get("mode", "tts")
    try:
        if mode == "tts":
            return _do_tts(inp)
        if mode == "clone":
            return _do_clone(inp)
        return {"error": f"mode ไม่รู้จัก: {mode!r} (ใช้ 'tts' หรือ 'clone')"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    # import runpod เฉพาะตอนรันจริง (กันไว้ไม่ให้ .start() ถูกเรียกตอน import
    # เข้าไปทดสอบ handler() ตรงๆ ผ่าน test_runpod_handler.py)
    import runpod
    runpod.serverless.start({"handler": handler})
