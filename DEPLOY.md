# Deploy — Hero Voice TTS API

API ให้บริการเสียงสต็อก + ออกแบบเสียง + โคลนเสียง + streaming (engine ภายใน: OmniVoice)
รับ `text` + `voice_id`/`instruct` แล้วคืนเสียง (JSON + base64)

## ไฟล์ในระบบ
| ไฟล์ | หน้าที่ |
|---|---|
| `core/server.py` | FastAPI server (engine registry, โหลดโมเดลครั้งเดียว, pre-encode เสียง) |
| `core/voice_library.py` | คลังเสียงโคลนถาวรต่อผู้ใช้ (SQLite + wav) |
| `core/credits.py` | ระบบ API key + เครดิต + rate-limit (SQLite) |
| `core/text_utils.py` | ตัดข้อความเป็นก้อนสำหรับ streaming + ทับศัพท์คำอังกฤษ + แปลงตัวเลขเป็นคำอ่านไทย |
| `core/gemini_translit.py` | fallback ถามคำทับศัพท์จาก Gemini API สำหรับคำที่ไม่มีในดิก (ปิดเป็นดีฟอลต์) |
| `core/asr_engine.py` | ASR ด้วย faster-whisper (เร็วกว่า transformers Whisper เดิม) |
| `core/watermark.py` | ฝัง audio watermark (AudioSeal) ลงเสียงที่ generate ทุกตัว |
| `scripts/build_voices.py` | สร้างคลังเสียงสต็อก (รันครั้งเดียว → `voices/`) |
| `scripts/manage_keys.py` | CLI จัดการ key/เครดิต |
| `scripts/client_example.py` | ตัวอย่างเรียก API |
| `Dockerfile` / `Dockerfile.cpu` | image สำหรับ deploy (GPU / CPU) |
| `requirements.txt` | dependencies |
| `tests/test_text_utils.py` | pytest สำหรับ text_utils.py (ไม่ต้องมี GPU/โมเดล — รัน `pytest tests/ -v`) |

## Endpoints
| Method | Path | รายละเอียด |
|---|---|---|
| GET | `/health` | สถานะ + device (ไม่ต้อง auth) |
| GET | `/engines` | รายการเอนจิน |
| GET | `/voices` | รายการเสียงที่ให้บริการ |
| POST | `/tts` | `{text, voice_id?/instruct?, ...}` → JSON + audio base64 |
| POST | `/tts/stream` | เหมือน `/tts` แต่ streaming (SSE) |
| POST | `/clone` | โคลนเสียงครั้งเดียว (multipart, ไม่เก็บ) |
| POST | `/voices` | สร้างเสียงโคลนถาวร (multipart) → `cv_...` |
| GET | `/voices/mine` | รายการเสียงโคลนของฉัน |
| DELETE | `/voices/{id}` | ลบเสียงโคลน (เจ้าของ) |
| POST | `/transcribe` | ถอดเสียงเป็นข้อความ (ASR) |
| POST | `/v1/audio/speech` | OpenAI-compatible TTS |
| GET | `/me` | ยอดเครดิตของ key |
| GET | `/docs` | Swagger UI (ทดสอบผ่านเบราว์เซอร์) |

> รายละเอียด request/response ครบใน `API_DOCS.md`

ตัวอย่าง request:
```bash
curl -X POST http://<host>:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"voice_id":"voice_02","text":"สวัสดีครับ"}'
```
response:
```json
{
  "voice_id": "voice_02",
  "text": "สวัสดีครับ",
  "audio_base64": "UklGR...",
  "format": "wav",
  "sample_rate": 24000,
  "duration": 1.8,
  "generation_time": 0.6
}
```

---

## วิธี deploy

### ตัวเลือก 1: Docker บน cloud GPU (แนะนำ)

```bash
# build (ต้องมีโฟลเดอร์ model/ และ voices/ อยู่ในโปรเจกต์แล้ว)
docker build -t herovoice-tts .

# run (ต้องมี NVIDIA Container Toolkit บนเครื่อง host)
docker run --gpus all -p 8000:8000 herovoice-tts
```

> **โมเดล ~3GB จะถูก bake เข้า image** ทำให้ image ใหญ่. ถ้าไม่อยากให้ image ใหญ่
> ลบบรรทัด `COPY model/` ออกจาก Dockerfile แล้ว mount เป็น volume แทน:
> ```bash
> docker run --gpus all -p 8000:8000 \
>   -v /path/to/model:/app/model \
>   -v /path/to/voices:/app/voices \
>   herovoice-tts
> ```

### ตัวเลือก 2: RunPod (Serverless หรือ Pod)
1. push image ขึ้น registry (Docker Hub / GHCR):
   ```bash
   docker tag herovoice-tts <user>/herovoice-tts:latest
   docker push <user>/herovoice-tts:latest
   ```
2. RunPod → สร้าง Pod (เลือก GPU) → ใส่ image → expose port 8000
3. หรือใช้ **RunPod Serverless** ถ้าโหลดไม่คงที่ (จ่ายตามใช้ ถูกกว่า)

### ตัวเลือก 3: รันตรงบน VM (ไม่ใช้ Docker)
```bash
pip install -r requirements.txt
pip install ./OmniVoice
python scripts/build_voices.py --device cuda   # ถ้ายังไม่มี voices/
cd core && uvicorn server:app --host 0.0.0.0 --port 8000
```

---

## ค่า config (env vars)
| env | ดีฟอลต์ | ความหมาย |
|---|---|---|
| `TTS_MODEL_DIR` | `./model` | โฟลเดอร์โมเดล |
| `TTS_VOICES_DIR` | `./voices` | โฟลเดอร์เสียงสต็อก |
| `TTS_VOICE_IDS` | `voice_01..voice_48` | เสียงที่เปิดให้บริการ |
| `TTS_MAX_CONCURRENCY` | `2` | งาน generate พร้อมกันสูงสุด |
| `TTS_CORS_ORIGINS` | `*` | โดเมนที่เรียกได้ (production ควรระบุจริง) |
| `TTS_API_KEY` | — | โหมด single-key (unlimited) — ตั้งเพื่อบังคับ auth |
| `TTS_CREDITS_DB` | — | ตั้ง path → เปิดโหมดเครดิต (หลาย key แยกยอด + rate-limit) |
| `TTS_COST_PER_SECOND` | `1.0` | เครดิตที่หักต่อวินาทีเสียง (โหมดเครดิต) |
| `TTS_CUSTOM_VOICES_DIR` | `./custom_voices` | โฟลเดอร์เก็บเสียงโคลนถาวร (ควร mount volume) |
| `TTS_VOICES_DB` | `<custom_voices>/voices.db` | SQLite เมทาดาทาเสียงโคลน |
| `TTS_PROMPT_CACHE_SIZE` | `64` | จำนวน clone-prompt ที่ cache ในแรม |
| `TTS_ASR_MODEL` | `large-v3-turbo` | โมเดล ASR — ชื่อโมเดลของ faster-whisper (ดู `core/asr_engine.py`), โหลด lazy |
| `TTS_ASR_DEVICE` | auto (`cuda`/`cpu`) | บังคับ device ที่ ASR รัน แยกจาก TTS ได้ |
| `TTS_ASR_COMPUTE_TYPE` | `float16` (cuda) / `int8` (cpu) | ความละเอียด compute ของ faster-whisper |
| `TTS_ENABLE_WATERMARK` | `1` (เปิด) | ฝัง audio watermark (AudioSeal) ลงเสียงที่ generate ทุกตัว — ตั้ง `0` เพื่อปิด (ดู `core/watermark.py`) |
| `TTS_GEMINI_TRANSLITERATE` | `0` (ปิด) | เปิด fallback ถามคำทับศัพท์จาก Gemini API สำหรับคำอังกฤษที่ไม่มีในดิก (ดู `core/gemini_translit.py`) — ต้องตั้ง `GEMINI_API_KEY` ด้วย |
| `GEMINI_API_KEY` | — | จำเป็นถ้าเปิด `TTS_GEMINI_TRANSLITERATE=1` — สร้างฟรีที่ https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | โมเดล Gemini ที่ใช้ทับศัพท์ |

> **Auth:** ถ้าไม่ตั้งทั้ง `TTS_API_KEY` และ `TTS_CREDITS_DB` = ไม่บังคับ auth (เหมาะ dev เท่านั้น)
> ก่อนเปิด public **ต้อง**ตั้งอย่างใดอย่างหนึ่ง

---

## Spec เครื่องที่แนะนำ (สรุปจากที่ประเมินไว้)

โมเดลเล็ก (~3GB) โหลด <1,000 req/วัน + รอคิวได้:

| ระดับ | Spec | Provider แนะนำ | ราคา/เดือน (โดยประมาณ) |
|---|---|---|---|
| **ประหยัดสุด** | Serverless (จ่ายตามใช้) | RunPod Serverless | **~$10–30** |
| **GPU 24/7 ถูก** | RTX 3060 12GB / RTX 4090 | Vast.ai / RunPod Community | **~$80–300** |
| เผื่อโหลดโต | L4 24GB / L40S | RunPod / Lambda | ~$440–630 |

- **VRAM ที่ใช้จริง (fp16):** ~3–4GB โหลดโมเดล + buffer → GPU 8GB+ พอสบาย
- `server.py` โหลด fp16 อัตโนมัติเมื่อเจอ CUDA (fp32 บน CPU)
- สเกลรับโหลดสูง = เพิ่มจำนวน container/GPU หลัง load balancer (แต่ละตัว 1 worker)

> ⚠️ ราคาเป็นค่าประมาณ ควรเช็คหน้าราคาจริงของ provider อีกครั้งก่อนตัดสินใจ

---

## หมายเหตุ
- โมเดลไม่ thread-safe → server generate ทีละงาน (มี lock). รับ concurrent ได้ด้วยการเข้าคิว
- อยากได้เสียงเพิ่ม/เปลี่ยนโทน: แก้ `VOICE_PRESETS` ใน `scripts/build_voices.py` (ใช้เฉพาะคำ instruct ที่โมเดลรองรับ — gender/age/pitch/whisper/accent) แล้วรันใหม่
- **API key auth ทำแล้ว** — เปิดผ่าน env `TTS_API_KEY` (single) หรือ `TTS_CREDITS_DB` (credits) ดูตาราง env ด้านบน
- โหมดเครดิตเก็บใน SQLite (ดีพอสำหรับ single-pod) — ถ้าสเกลหลาย pod ควรย้ายเครดิตไป Postgres และ rate-limit ไป Redis
- ⚠️ **ต้อง mount volume** สำหรับข้อมูลที่ต้องอยู่ถาวร ไม่งั้นหายเมื่อ pod รีสตาร์ต:
  - `custom_voices/` (เสียงโคลนถาวร) + `credits.db` (เครดิต) — เช่น RunPod Network Volume
  - ครั้งแรกที่ใช้ `/transcribe` หรือโคลนแบบ auto ref_text จะโหลด Whisper (~1.5GB) — เก็บ `HF_HOME` ไว้ใน volume ด้วยจะได้ไม่โหลดซ้ำ
