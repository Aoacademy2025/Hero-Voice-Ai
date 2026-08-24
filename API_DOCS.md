# Hero Voice TTS API (v2)

API แปลงข้อความเป็นเสียงพูด (Thai/multilingual) — เสียงสต็อก, ออกแบบเสียง (voice design),
โคลนเสียง, และ streaming พร้อมระบบเครดิต/rate-limit
(engine ภายใน: OmniVoice — เสียบเอนจินอื่นเพิ่มได้)

- **Content-Type:** `application/json` (ยกเว้น `/clone` = `multipart/form-data`)
- **CORS:** เปิด (`*` โดยดีฟอลต์ — production ควรจำกัดเป็นโดเมนจริง)
- **Auth:** ส่ง header `X-API-Key: <key>` ทุก request (ยกเว้น `GET /health`)

> **หมายเหตุ instruct:** `instruct` = *ออกแบบเสียง* ไม่ใช่ *อารมณ์*
> รองรับเฉพาะ: เพศ (male/female), อายุ (child/teenager/young adult/middle-aged/elderly),
> pitch (very low/low/moderate/high/very high pitch), whisper, สำเนียง (british accent ฯลฯ)
> — happy/sad/angry **ใช้ไม่ได้**

---

## Authentication & เครดิต

มี 2 โหมด (เลือกด้วย env ฝั่ง server):

| โหมด | เปิดเมื่อ | พฤติกรรม |
|---|---|---|
| **Single key** | ตั้ง `TTS_API_KEY` | 1 key ใช้ได้ไม่จำกัด (เหมาะ dev/ภายใน) |
| **Credits** | ตั้ง `TTS_CREDITS_DB` | หลาย key แยกยอดเครดิต + rate-limit + คิดเงินตามวินาทีเสียง |

โหมด Credits: จัดการ key ด้วย `manage_keys.py`
```bash
export TTS_CREDITS_DB=/data/credits.db
python manage_keys.py create --name "ลูกค้า A" --credits 1000 --rate 60
python manage_keys.py create --name admin --unlimited
python manage_keys.py add --key sk_xxx --credits 500
python manage_keys.py list
```
เครดิตถูกหักตาม **วินาทีเสียงที่สร้าง × `TTS_COST_PER_SECOND`** (ดีฟอลต์ 1.0)
เครดิตหมด → `402`, ยิงถี่เกิน rate → `429`

---

## Endpoints

### `GET /health` — สถานะ (ไม่ต้อง auth)
```json
{ "status":"ok", "device":"cuda", "engines":["omnivoice"],
  "num_voices":48, "credits_enabled":true, "max_concurrency":2 }
```

### `GET /me` — ดูยอดเครดิตของ key ตัวเอง
```json
{ "credits_enabled":true, "name":"ลูกค้า A", "credits":842.5,
  "unlimited":false, "rate_per_min":60, "total_used":157.5 }
```

### `GET /engines` — รายการเอนจิน
```json
[{ "id":"omnivoice","name":"OmniVoice","sample_rate":24000,
   "supports_clone":true,"supports_design":true,"num_voices":48 }]
```

### `GET /voices?engine=omnivoice` — รายการเสียงสต็อก
```json
[{ "voice_id":"voice_02","desc":"เสียงผู้หญิง โทนปกติ","instruct":"female",
   "preview_url":"http://<host>/voices/voice_02/preview?engine=omnivoice" }]
```

### `GET /voices/{voice_id}/preview` — ไฟล์เสียงตัวอย่าง (audio/wav)
ใส่ใน `<audio controls src="{preview_url}">` ได้เลย

### `POST /tts` — สร้างเสียง (endpoint หลัก)
```json
{
  "text": "ข้อความที่ต้องการแปลงเป็นเสียง",
  "voice_id": "voice_02",
  "engine": "omnivoice",
  "instruct": null,
  "language": null,
  "num_step": 16,
  "speed": 1.0
}
```
| field | required | default | รายละเอียด |
|---|---|---|---|
| `text` | ✅ | — | ข้อความ |
| `voice_id` | ✳️ | — | เสียงสต็อก (ดู `/voices`) |
| `instruct` | ✳️ | — | ออกแบบเสียง เช่น `"female, high pitch, british accent"` |
| `engine` | ❌ | omnivoice | เลือกเอนจิน |
| `language` | ❌ | auto | เช่น `"Thai"`, `"English"` |
| `num_step` | ❌ | 16 | diffusion steps (4–64) สูง=ดีขึ้นแต่ช้า |
| `speed` | ❌ | 1.0 | ความเร็ว (0.3–3.0) |

✳️ ต้องมี `voice_id` **หรือ** `instruct` อย่างน้อยหนึ่ง

**Response** `200`
```json
{ "engine":"omnivoice","voice_id":"voice_02","text":"...",
  "audio_base64":"UklGR...","format":"wav","sample_rate":24000,
  "duration":2.85,"generation_time":1.2,"credits_charged":2.85 }
```

### `POST /tts/stream` — Streaming (SSE)
เหมือน `/tts` แต่ตัดข้อความเป็นก้อน แล้ว **ทยอยส่งเสียงทีละก้อน** (ผู้ใช้ได้ยินเร็วขึ้น)
Response = `text/event-stream` แต่ละ event:
```
data: {"index":0,"total":5,"text":"ก้อนแรก...","audio_base64":"UklGR...","duration":1.9}

data: {"index":1,"total":5,...}

data: {"done":true,"total_duration":9.4,"credits_charged":9.4}
```
ฝั่ง client เล่นเสียงแต่ละก้อนต่อกันได้เลย (decode base64 → เล่นเรียงตาม index)

### `POST /clone` — โคลนเสียงจากไฟล์ (multipart/form-data)
| field | type | required | รายละเอียด |
|---|---|---|---|
| `ref_audio` | file | ✅ | ไฟล์เสียง 3–10 วิ (wav/mp3) |
| `ref_text` | string | ✅ | ข้อความที่พูดในไฟล์ ref |
| `text` | string | ✅ | ข้อความที่อยากให้พูด |
| `engine` | string | ❌ | ดีฟอลต์ omnivoice |
| `language` | string | ❌ | — |
| `num_step` | int | ❌ | 16 |
| `speed` | float | ❌ | 1.0 |

ไม่เก็บไฟล์ ref ถาวร — ใช้ครั้งเดียวแล้วลบ

---

## คลังเสียงโคลนถาวร (Custom Voices)

ต่างจาก `/clone` (ครั้งเดียว) — ที่นี่โคลนแล้ว **เก็บไว้ใช้ซ้ำ** ได้เหมือนเสียงสต็อก
เสียงเป็นของเจ้าของ key (คนอื่นมองไม่เห็น/ใช้ไม่ได้) `voice_id` ขึ้นต้นด้วย `cv_`

### `POST /voices` — สร้างเสียงโคลนถาวร (multipart/form-data)
| field | type | required | รายละเอียด |
|---|---|---|---|
| `ref_audio` | file | ✅ | ไฟล์เสียง 3–10 วิ (wav/mp3 — ระบบแปลงเป็น wav 24k ให้) |
| `name` | string | ❌ | ชื่อเสียง |
| `ref_text` | string | ❌ | ข้อความในไฟล์ — **เว้นว่าง = ถอดอัตโนมัติด้วย ASR** |
| `engine` | string | ❌ | ดีฟอลต์ omnivoice |

**Response** `200`
```json
{ "voice_id":"cv_AbC123...","name":"เสียงพี่","ref_text":"สวัสดีครับ",
  "preview_url":"http://<host>/voices/cv_AbC123.../preview" }
```
จากนั้นใช้ใน `/tts` ได้เลย: `{"voice_id":"cv_AbC123...","text":"..."}`

### `GET /voices/mine` — รายการเสียงโคลนของฉัน
```json
[{ "voice_id":"cv_AbC123...","name":"เสียงพี่","ref_text":"สวัสดีครับ",
   "created_at":1750000000.0,"preview_url":"http://<host>/voices/cv_AbC123.../preview" }]
```

### `DELETE /voices/{voice_id}` — ลบเสียงโคลน (เฉพาะเจ้าของ)
```json
{ "deleted":"cv_AbC123..." }
```

---

## ASR — ถอดเสียงเป็นข้อความ

### `POST /transcribe` (multipart/form-data)
| field | type | required | รายละเอียด |
|---|---|---|---|
| `audio` | file | ✅ | ไฟล์เสียงที่จะถอด |
| `engine` | string | ❌ | ดีฟอลต์ omnivoice |

**Response** `200` → `{ "text": "ข้อความที่ถอดได้" }`

> ครั้งแรกจะโหลดโมเดล Whisper (~1.5GB, ดาวน์โหลดครั้งเดียว) — request แรกช้ากว่าปกติ

---

## OpenAI-compatible

### `POST /v1/audio/speech`
เข้ากันได้กับ OpenAI TTS API — client เดิมเปลี่ยนแค่ base URL + key
รับ auth ได้ทั้ง `Authorization: Bearer <key>` และ `X-API-Key`

**Request**
```json
{ "model":"hero-voice-1", "input":"ข้อความ", "voice":"voice_02",
  "response_format":"mp3", "speed":1.0 }
```
- `voice` = voice_id (สต็อก/โคลน) · `response_format` = mp3/wav/flac/opus/aac/pcm
- **Response** = ไฟล์เสียงดิบ (binary) ไม่ใช่ JSON

```python
from openai import OpenAI
client = OpenAI(base_url="http://<host>:8000/v1", api_key="<key>")
client.audio.speech.create(model="hero-voice-1", voice="voice_02",
                           input="สวัสดีครับ").stream_to_file("out.mp3")
```

> mp3/opus/aac ต้องมี ffmpeg ใน container (มากับ image อยู่แล้ว) — ถ้าไม่มีใช้ `wav`/`flac`/`pcm`

---

## Errors
| code | สาเหตุ |
|---|---|
| `401` | ไม่ส่ง / ส่ง `X-API-Key` ผิด |
| `402` | เครดิตหมด (โหมด credits) |
| `404` | `voice_id` / `engine` ไม่มี |
| `422` | body ไม่ครบ / instruct มีคำที่ไม่รองรับ / โคลนไม่สำเร็จ |
| `429` | ยิงถี่เกิน rate-limit (โหมด credits) |

---

## ตัวอย่าง

### cURL
```bash
curl -X POST http://<host>:8000/tts \
  -H "Content-Type: application/json" -H "X-API-Key: <key>" \
  -d '{"voice_id":"voice_02","text":"สวัสดีครับ"}' \
  | jq -r .audio_base64 | base64 -d > out.wav
```

### JavaScript (streaming)
```javascript
const res = await fetch("http://<host>:8000/tts/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": "<key>" },
  body: JSON.stringify({ voice_id: "voice_02", text: "ข้อความยาวๆ..." }),
});
const reader = res.body.getReader();
const dec = new TextDecoder();
let buf = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  const events = buf.split("\n\n");
  buf = events.pop();  // เก็บเศษที่ยังไม่ครบ event
  for (const line of events) {
    if (!line.startsWith("data: ")) continue;
    const evt = JSON.parse(line.slice(6));
    if (evt.done) { console.log("เสร็จ", evt.total_duration); continue; }
    const bytes = Uint8Array.from(atob(evt.audio_base64), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    new Audio(url).play();  // (จริงควรต่อคิวเล่นเรียงตาม index)
  }
}
```

---

## หมายเหตุการใช้งาน
- **สร้างเสียงทีละงาน** — โมเดลประมวลผลแบบ serialize (มีคิวภายใน)
- **ความเร็ว:** GPU ~1 วิ/ประโยค, CPU ~25–30 วิ/ประโยค
- **เสียงที่เปิด:** ปรับผ่าน env `TTS_VOICE_IDS` — ดู `DEPLOY.md`
- Swagger UI ทดสอบสดที่ `GET /docs`
