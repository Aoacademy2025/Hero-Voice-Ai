# Hero Voice TTS

บริการแปลงข้อความเป็นเสียงพูด (Thai / multilingual) แบบ self-hosted — รันโมเดลเอง
ไม่ต้องพึ่งคลาวด์เจ้าอื่น ออกแบบให้ยกขึ้น RunPod / cloud GPU ได้ทันที

**engine ภายใน:** [OmniVoice](https://github.com/k2-fsa) (Apache-2.0) — ห่อด้วย engine registry
ที่เสียบเอนจินอื่นเพิ่มได้ในอนาคต

## ความสามารถ

- 🎙️ **เสียงสต็อก** 48 เสียง (pre-encode ครั้งเดียว → เสียงคงที่ทุกครั้ง)
- 🎨 **ออกแบบเสียง (voice design)** จากคำบรรยาย — เพศ / อายุ / pitch / whisper / สำเนียง
- 🧬 **โคลนเสียง** — ทั้งแบบครั้งเดียว (`/clone`) และ **คลังเสียงโคลนถาวร** ต่อผู้ใช้ (`/voices`)
- ✍️ **ASR** (`/transcribe`) — ถอดเสียงเป็นข้อความ + auto ref_text ตอนโคลน
- ⚡ **Streaming (SSE)** — ทยอยส่งเสียงทีละก้อน ผู้ใช้ได้ยินเร็วขึ้น
- 🔌 **OpenAI-compatible** (`/v1/audio/speech`) — เสียบแทน OpenAI TTS ได้
- 💳 **ระบบเครดิต + rate-limit** — หลาย API key แยกยอด คิดเงินตามวินาทีเสียง
- 🔀 **หลายภาษา (ไทย/ลาว/อังกฤษ) / ปรับความเร็ว / ปรับคุณภาพ (num_step)** ต่อ request
- 🗣️ **ทับศัพท์คำอังกฤษ + แปลงตัวเลขเป็นคำอ่านไทย** ก่อนอ่าน — กันเสียงเพี้ยน/สะดุดตอนสลับภาษา
  หรือสคริปต์กับเสียงไม่ตรงกันตอนมีตัวเลข/เบอร์โทร/จำนวนเงิน (ดู `text_utils.py`)
- 🔒 **Audio watermark** (AudioSeal) ฝังในเสียงที่ generate ทุกตัว — ตรวจสอบย้อนหลังได้ว่ามาจาก AI

> **หมายเหตุ:** OmniVoice `instruct` = *ออกแบบเสียง* (เพศ/อายุ/pitch/whisper/สำเนียง) ไม่ใช่อารมณ์
> ระบบไม่มีฟีเจอร์ควบคุมอารมณ์ (happy/sad/angry) แยกต่างหากแล้ว

## เริ่มใช้งาน

```bash
# 1) ติดตั้ง (แนะนำติดตั้ง torch จาก index ที่ตรงกับ CUDA/CPU ก่อน — ดู Dockerfile)
pip install -r requirements.txt
pip install ./OmniVoice

# 2) สร้างคลังเสียงสต็อก (ครั้งเดียว)
python scripts/build_voices.py --device cuda      # หรือ --device cpu

# 3) รัน server
bash start_all.sh                          # หรือ: python core/server.py
```

เปิดใช้งาน:
- 🎛️ **Web UI (Studio)** — http://localhost:8000/ (ลองทุกฟีเจอร์: สร้างเสียง/ออกแบบ/โคลน/ถอดเสียง)
- 📖 **Swagger UI** — http://localhost:8000/docs

## โครงสร้างไฟล์

| ไฟล์ | หน้าที่ |
|---|---|
| `core/server.py` | FastAPI server + engine registry |
| `core/runpod_handler.py` | เอนทรีพอยต์สำหรับ RunPod Serverless (ใช้ logic เดียวกับ server.py) |
| `core/voice_library.py` | คลังเสียงโคลนถาวรต่อผู้ใช้ (SQLite + ไฟล์ wav) |
| `core/credits.py` | ระบบ API key + เครดิต + rate-limit (SQLite) |
| `core/text_utils.py` | ตัดข้อความเป็นก้อนสำหรับ streaming + ทับศัพท์คำอังกฤษ + แปลงตัวเลขเป็นคำอ่านไทย |
| `core/gemini_translit.py` | fallback ทับศัพท์ผ่าน Gemini API สำหรับคำที่ไม่มีในดิก (ปิดเป็นดีฟอลต์) |
| `core/asr_engine.py` | ASR ด้วย faster-whisper |
| `core/watermark.py` | ฝัง audio watermark (AudioSeal) |
| `core/studio.html` | หน้า Web UI ทดลองใช้ทุกฟีเจอร์ (เสิร์ฟจาก server เอง) |
| `scripts/build_voices.py` | สร้างคลังเสียงสต็อก → `voices/` |
| `scripts/manage_keys.py` | CLI จัดการ key/เครดิต |
| `scripts/client_example.py` / `web_example.html` | ตัวอย่างเรียกใช้ |
| `Dockerfile` / `Dockerfile.cpu` | image สำหรับ deploy (GPU / CPU) |
| `tests/test_text_utils.py` | pytest สำหรับ text_utils.py — รัน `pytest tests/ -v` (ไม่ต้องมี GPU/โมเดล) |

## เอกสาร

- **[API_DOCS.md](API_DOCS.md)** — endpoints, request/response, ตัวอย่างครบ
- **[DEPLOY.md](DEPLOY.md)** — วิธี deploy (Docker / RunPod / VM), env vars, spec เครื่อง

## License

โค้ดในโปรเจกต์นี้เป็นของเราเอง — โมเดล OmniVoice เป็น Apache-2.0 (ใช้เชิงพาณิชย์ได้)
