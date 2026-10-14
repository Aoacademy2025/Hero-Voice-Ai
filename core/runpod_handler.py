"""
runpod_handler.py — RunPod Serverless entrypoint (จ่ายเฉพาะตอนมีคำขอ ไม่ต้องเปิดเครื่อง 24 ชม.)

ใช้เอนจินและขั้นตอนเดียวกับ server.py /tts ทุกอย่าง (load_engines, engine_chunks, ASR verify,
engine_concat) — ค่าเริ่มต้นคือเอนจิน hero: OmniVoice กำหนดเสียง + VoxCPM2 เป็นตัวพูด
ผลเสียงจึงเหมือนที่ได้จาก server บน Pod (และไฟล์สารคดี 3 นาที v2 ที่อนุมัติไว้)

เรียก (ต้องมี RunPod API key — auth ชั้นนอกของ RunPod ไม่ต้องมี TTS_API_KEY ของเรา):
  POST https://api.runpod.ai/v2/<ENDPOINT_ID>/run      {"input": {...}}  → {"id": "<job id>"}
  GET  https://api.runpod.ai/v2/<ENDPOINT_ID>/status/<job id>            → {"status":"COMPLETED","output":{...}}
  (ใช้ /run + /status สำหรับงานยาว; /runsync รอผลได้ไม่นาน)

input:
  TTS:   {"text": "...", "voice_id": "voice_02"}                 # เสียงสต็อก/ลาว ของ OmniVoice
         {"text": "...", "instruct": "female, low pitch"}        # ออกแบบเสียงด้วยคำศัพท์ OmniVoice
         optional: engine (hero|omnivoice|voxcpm2, ดีฟอลต์ hero), format (mp3|wav, ดีฟอลต์ mp3),
                   speed, num_step, guidance_scale, language, mixed_language, transliterate_english,
                   normalize_numbers
         สคริปต์ยาว: ขึ้นบรรทัดใหม่ = จุดตัดท่อน (ไม่มีขึ้นบรรทัดก็ได้ ระบบตัดตามความยาวเอง)
  Clone: {"mode": "clone", "ref_audio_b64": "<base64 wav/mp3>", "ref_text": "...", "text": "..."}

output: {"audio_base64", "format", "sample_rate", "duration", "generation_time", "engine", ...}
        error: {"error": "..."}

ขนาดผลลัพธ์: RunPod จำกัด /run ~10MB — ดีฟอลต์จึงเป็น mp3 192kbps (3 นาที ≈ 4.6MB) ถ้าขอ wav
แล้วเกินขนาด จะส่ง mp3 แทนอัตโนมัติ (ดู "format" ในผลลัพธ์)

ทดสอบบนเครื่อง: python tests/test_runpod_handler.py
"""
import base64
import io
import os
import tempfile
import time

import numpy as np

from server import (ENGINES, DEFAULT_ENGINE, load_engines, engine_instruct, engine_chunks,
                    engine_concat, engine_postprocess, _asr_match, VERIFY_ASR, VERIFY_MIN_RATIO, VERIFY_MAX_TRIES,
                    _CLONE_BEST_OF, _BEST_OF_CLASS_TEMPERATURE, wav_bytes, b64)
from text_utils import expand_maiyamok, normalize_thai_numbers, split_by_language, transliterate_english

MAX_B64_BYTES = int(os.environ.get("TTS_SERVERLESS_MAX_B64", str(9_500_000)))  # เผื่อ JSON รอบๆ ให้ไม่เกิน 10MB
MP3_BITRATE = os.environ.get("TTS_MP3_BITRATE", "192k")

# โหลดโมเดลครั้งเดียวตอน worker เริ่ม (cold start) — ไม่ใช่ทุก job
print("[handler] loading engines ...", flush=True)
load_engines()
print(f"[handler] ready — engines {list(ENGINES)}, default {DEFAULT_ENGINE['id']}", flush=True)


def _engine(inp):
    eid = inp.get("engine") or DEFAULT_ENGINE["id"]
    if eid not in ENGINES:
        raise ValueError(f"ไม่พบเอนจิน '{eid}' (มี: {list(ENGINES)})")
    return ENGINES[eid]


def _stock_prompt(eng, voice_id):
    if voice_id not in eng.voices:
        raise ValueError(f"ไม่พบเสียง '{voice_id}' (มี: {list(eng.voices)})")
    prompt = eng.cache_get(voice_id)
    if prompt is None:
        v = eng.voices[voice_id]
        prompt = eng.build_prompt(v["ref_audio"], v["meta"]["ref_text"])
        eng.cache_put(voice_id, prompt)
    return prompt


def _encode(wav, sr, fmt):
    """คืน (base64, format) — wav ที่ใหญ่เกินเพดานของ RunPod สลับเป็น mp3 อัตโนมัติ"""
    fmt = (fmt or "mp3").lower()
    if fmt == "wav":
        data = b64(wav_bytes(wav, sr))
        if len(data) <= MAX_B64_BYTES:
            return data, "wav"
        print(f"[handler] wav {len(data)/1e6:.1f}MB เกินเพดาน → ส่ง mp3 แทน", flush=True)
    from pydub import AudioSegment
    pcm = (np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    AudioSegment(pcm, frame_rate=sr, sample_width=2, channels=1).export(buf, format="mp3", bitrate=MP3_BITRATE)
    data = b64(buf.getvalue())
    if len(data) > MAX_B64_BYTES:
        raise ValueError(f"เสียงยาวเกินเพดานผลลัพธ์ของ RunPod ({len(data)/1e6:.1f}MB แม้เป็น mp3) — แบ่งสคริปต์เป็นหลายงาน")
    return data, "mp3"


def _generate(eng, chunks, langs, prompt, instruct, speed, num_step, guidance_scale):
    """เหมือน server._generate_chunks (sync): ตรวจทุกท่อนด้วย ASR แล้วสร้างซ้ำถ้าอ่านไม่ตรง"""
    wavs, active = [], prompt
    for chunk, lang in zip(chunks, langs):
        best = None
        for _ in range(VERIFY_MAX_TRIES if VERIFY_ASR else 1):
            wav, _ = eng._run(chunk, clone_prompt=active, instruct=instruct, language=lang,
                              speed=speed, num_step=num_step, guidance_scale=guidance_scale)
            if not VERIFY_ASR:
                break
            ratio = _asr_match(wav, chunk, eng.sample_rate)
            if ratio is None:
                break
            if best is None or ratio > best[0]:
                best = (ratio, wav)
            if ratio >= VERIFY_MIN_RATIO:
                break
            print(f"[handler] ท่อนอ่านไม่ตรง (ratio={ratio:.2f}) → สร้างซ้ำ", flush=True)
        if best is not None:
            wav = best[1]
        if active is None and instruct:
            active = eng.build_prompt_from_wav(wav, chunk)
        wavs.append(wav)
    return engine_concat(eng, wavs)


def _do_tts(inp: dict) -> dict:
    text = inp.get("text")
    if not text:
        raise ValueError("ต้องระบุ text")
    eng = _engine(inp)
    voice_id = inp.get("voice_id")
    instruct = engine_instruct(eng, inp.get("instruct"))
    if not voice_id and not instruct:
        raise ValueError("ต้องระบุ voice_id (เสียงสต็อก) หรือ instruct (ออกแบบเสียง)")
    prompt = _stock_prompt(eng, voice_id) if voice_id else None
    if prompt is None and instruct and hasattr(eng, "design_prompt"):
        prompt = eng.design_prompt(instruct)

    speed = float(inp.get("speed", 1.0))
    num_step = int(inp.get("num_step", 32))
    guidance_scale = inp.get("guidance_scale")
    language = inp.get("language") or (eng.voices.get(voice_id, {}).get("meta", {}).get("language")
                                       if voice_id else None)
    native = getattr(eng, "native_mixed_language", False)
    src = text
    if inp.get("transliterate_english", True) and not native:
        text = transliterate_english(text)
    if not native:
        text = expand_maiyamok(text)
        if inp.get("normalize_numbers", True):
            text = normalize_thai_numbers(text)

    t = time.time()
    if inp.get("mixed_language", True) and not native:
        segs = split_by_language(text)
        chunks, langs = [s for s, _ in segs], [l for _, l in segs]
    else:
        chunks = engine_chunks(eng, text)
        langs = [language] * len(chunks)
    wav = _generate(eng, chunks, langs, prompt, instruct, speed, num_step, guidance_scale)
    wav = engine_postprocess(eng, wav, prompt)  # ลดก้องเฉพาะเสียงที่ตั้งไว้ (เช่นนภา) แล้วฝังลายน้ำ
    gen_time = time.time() - t
    audio, fmt = _encode(wav, eng.sample_rate, inp.get("format"))
    return {
        "engine": eng.id, "voice_id": voice_id, "text": src,
        "audio_base64": audio, "format": fmt, "sample_rate": eng.sample_rate,
        "duration": round(len(wav) / eng.sample_rate, 2), "generation_time": round(gen_time, 2),
        "chunks": len(chunks),
    }


def _do_clone(inp: dict) -> dict:
    ref_b64, ref_text, text = inp.get("ref_audio_b64"), inp.get("ref_text"), inp.get("text")
    if not ref_b64 or not ref_text or not text:
        raise ValueError("ต้องระบุ ref_audio_b64, ref_text, text ทั้งสามอย่าง")
    eng = _engine(inp)
    speed = float(inp.get("speed", 1.0))
    num_step = int(inp.get("num_step", 32))
    guidance_scale = inp.get("guidance_scale", 2.5)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(base64.b64decode(ref_b64))
            tmp_path = tmp.name
        prompt = eng.build_prompt(tmp_path, ref_text)
        from voice_similarity import embed_file, embed_array, cosine_sim
        ref_emb = embed_file(tmp_path)
        t = time.time()
        best_wav, best_score = None, -1.0
        for _ in range(_CLONE_BEST_OF):
            wav, _ = eng._run(text, clone_prompt=prompt, speed=speed, num_step=num_step,
                              guidance_scale=guidance_scale, class_temperature=_BEST_OF_CLASS_TEMPERATURE)
            score = cosine_sim(ref_emb, embed_array(wav, eng.sample_rate))
            if score > best_score:
                best_wav, best_score = wav, score
        gen_time = time.time() - t
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
    audio, fmt = _encode(best_wav, eng.sample_rate, inp.get("format"))
    return {
        "engine": eng.id, "text": text, "audio_base64": audio, "format": fmt,
        "sample_rate": eng.sample_rate, "duration": round(len(best_wav) / eng.sample_rate, 2),
        "generation_time": round(gen_time, 2), "similarity_score": round(float(best_score), 4),
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
        detail = getattr(e, "detail", None)  # HTTPException จาก engine_instruct
        return {"error": str(detail or e)}


if __name__ == "__main__":
    import runpod
    runpod.serverless.start({"handler": handler})
