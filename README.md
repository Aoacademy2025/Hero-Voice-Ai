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
- 🔀 **หลายภาษา / ปรับความเร็ว / ปรับคุณภาพ (num_step)** ต่อ request

- 🎭 **อารมณ์ (emotion)** + cloning เหมือนสูง — ผ่านเอนจินเสริม **IndexTTS-2** (optional, ต้อง GPU — ดู DEPLOY.md)

> **หมายเหตุ:** OmniVoice `instruct` = *ออกแบบเสียง* (เพศ/อายุ/pitch/whisper/สำเนียง) ไม่ใช่อารมณ์
> ส่วน "อารมณ์" (happy/sad/angry) และ cloning เหมือนเป๊ะ ใช้เอนจิน **IndexTTS-2** แทน

## เริ่มใช้งาน

```bash
# 1) ติดตั้ง (แนะนำติดตั้ง torch จาก index ที่ตรงกับ CUDA/CPU ก่อน — ดู Dockerfile)
pip install -r requirements.txt
pip install ./OmniVoice

# 2) สร้างคลังเสียงสต็อก (ครั้งเดียว)
python build_voices.py --device cuda      # หรือ --device cpu

# 3) รัน server
bash start_all.sh                          # หรือ: python server.py
```

เปิดใช้งาน:
- 🎛️ **Web UI (Studio)** — http://localhost:8000/ (ลองทุกฟีเจอร์: สร้างเสียง/ออกแบบ/โคลน/ถอดเสียง)
- 📖 **Swagger UI** — http://localhost:8000/docs

## โครงสร้างไฟล์

| ไฟล์ | หน้าที่ |
|---|---|
| `server.py` | FastAPI server + engine registry |
| `build_voices.py` | สร้างคลังเสียงสต็อก → `voices/` |
| `voice_library.py` | คลังเสียงโคลนถาวรต่อผู้ใช้ (SQLite + ไฟล์ wav) |
| `credits.py` | ระบบ API key + เครดิต + rate-limit (SQLite) |
| `manage_keys.py` | CLI จัดการ key/เครดิต |
| `text_utils.py` | ตัดข้อความเป็นก้อนสำหรับ streaming |
| `client_example.py` / `web_example.html` | ตัวอย่างเรียกใช้ |
| `Dockerfile` / `Dockerfile.cpu` | image สำหรับ deploy (GPU / CPU) |

## เอกสาร

- **[API_DOCS.md](API_DOCS.md)** — endpoints, request/response, ตัวอย่างครบ
- **[DEPLOY.md](DEPLOY.md)** — วิธี deploy (Docker / RunPod / VM), env vars, spec เครื่อง

## License

โค้ดในโปรเจกต์นี้เป็นของเราเอง — โมเดล OmniVoice เป็น Apache-2.0 (ใช้เชิงพาณิชย์ได้)
