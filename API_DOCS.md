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
> — happy/sad/angry ใส่ใน `instruct` **ใช้ไม่ได้** (ระบบไม่มีฟีเจอร์ควบคุมอารมณ์แยกต่างหากแล้ว)

---

## ความสามารถของโมเดล (OmniVoice Capabilities)

เอนจินหลัก `omnivoice` เป็นโมเดล TTS แบบ zero-shot รองรับ **646 ภาษา** ([รายชื่อเต็ม](OmniVoice/docs/languages.md))
ความสามารถด้านล่างมาจากตัวโมเดลเอง — คอลัมน์ "ใช้ผ่าน API นี้ยังไง" บอกว่าตอนนี้เรียกใช้ได้จาก endpoint ไหนบ้าง

| ความสามารถ | รายละเอียด | ใช้ผ่าน API นี้ยังไง |
|---|---|---|
| **หลายภาษา (646 ภาษา)** | ไทย ลาว อังกฤษ จีน ญี่ปุ่น ฯลฯ — ระบุชื่อภาษาเต็ม (`"Thai"`, `"Lao"`) หรือรหัส ISO (`"th"`, `"lo"`) | field `language` ใน `/tts`, `/clone` |
| **สลับภาษากลางประโยค (code-switching)** | ตัดข้อความเป็นช่วงตามสคริปต์ (ไทย/ลาว/อังกฤษ) generate แยกภาษาแล้วต่อเสียง กันโมเดลอ่านผิดภาษา | field `mixed_language` ใน `/tts` (ดีฟอลต์เปิด) — ดู `text_utils.split_by_language` |
| **Voice Cloning (zero-shot)** | โคลนเสียงจากไฟล์อ้างอิง 3-10 วิ ไม่ต้องเทรนโมเดลใหม่ | `/clone` (ครั้งเดียว, best-of-3 + วัด similarity/naturalness อัตโนมัติ), `/voices` (เก็บถาวรใช้ซ้ำ) |
| **Voice Design** | ออกแบบเสียงจากคำบรรยาย: เพศ, อายุ, pitch, กระซิบ, สำเนียง (ไม่ใช่การโคลนจากไฟล์จริง) | field `instruct` ใน `/tts` |
| **สัญลักษณ์ไม่ใช่คำพูด (non-verbal tags)** | แทรกแท็กในข้อความให้ออกเสียงหัวเราะ/ถอนหายใจ/อุทาน เช่น `[laughter]`, `[sigh]`, `[surprise-ah]`, `[question-en]` | ใส่ในข้อความ (`text`) ตรงๆ ได้เลยทุก endpoint — โมเดลอ่านแท็กพวกนี้ตรงๆ ไม่ต้องตั้งค่าเพิ่ม (ดูรายการแท็กทั้งหมดด้านล่าง) |
| **Streaming** | ตัดข้อความเป็นก้อน ทยอยส่งเสียงทีละก้อนแทนที่จะรอจนจบ | `/tts/stream` (SSE) |
| **ASR (ถอดเสียง)** | ถอดไฟล์เสียงเป็นข้อความ | `/transcribe`, และอัตโนมัติตอน `/voices` ถ้าไม่ระบุ `ref_text` |
| **ลดเสียงรบกวน (denoise)** | แยกเสียงพูดออกจากเสียงพื้นหลัง/ดนตรี + normalize ความดัง (Demucs) | `/enhance` แบบเดี่ยวๆ, หรือ `enhance_ref=true` (ดีฟอลต์) ใน `/clone`, `/voices` |
| **แปลงคำอังกฤษ/ตัวเลขเป็นคำอ่านไทย** | กันเสียงสะดุด/เพี้ยนตอนอ่านคำทับศัพท์อังกฤษหรือตัวเลข/จำนวนเงินกลางประโยคไทย | field `transliterate_english`, `normalize_numbers` ใน `/tts` (ดีฟอลต์เปิดทั้งคู่) |
| **ลายน้ำเสียง (watermark)** | ฝังลายน้ำที่ตรวจสอบย้อนกลับได้ในไฟล์เสียงที่สร้างทุกไฟล์ | อัตโนมัติทุก endpoint ที่สร้างเสียง (ดู `watermark.py`) |
| **OpenAI-compatible** | เสียบแทน OpenAI TTS ได้ทันที เปลี่ยนแค่ base URL + key | `/v1/audio/speech` |

**แท็ก non-verbal ที่รองรับ:** `[laughter]`, `[sigh]`, `[confirmation-en]`, `[question-en]`, `[question-ah]`,
`[question-oh]`, `[question-ei]`, `[question-yi]`, `[surprise-ah]`, `[surprise-oh]`, `[surprise-wa]`,
`[surprise-yo]`, `[dissatisfaction-hnn]` — ตัวอย่าง: `"เดี๋ยวนะ [laughter] ตลกมากเลย"`
> ถ้าเปิด `transliterate_english`/`normalize_numbers` (ดีฟอลต์เปิด) คำในแท็ก (เช่น "laughter") จะถูกสแกนหาในดิกทับศัพท์เหมือนคำอังกฤษทั่วไป — ไม่เคยเจอคำเหล่านี้ในดิกจริง จึงผ่านไปตรงๆ แต่ถ้าอยากชัวร์ 100% ปิด `transliterate_english=false` ตอนใช้แท็กพวกนี้ได้

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
   "supports_clone":true,"supports_design":true,"num_voices":35 }]
```

### `GET /voices?engine=omnivoice&language=` — รายการเสียงสต็อก
ดีฟอลต์ (ไม่ใส่ `language`) คืนเฉพาะ**เสียงชุดหลัก (ไทย/อังกฤษ)** — **ไม่ปนเสียงลาว**
ต้องระบุ `?language=lao` ชัดๆ ถึงจะได้เสียงลาวแยกต่างหาก (ดู [เสียงภาษาลาว](#เสียงภาษาลาว-lao-voices))
```json
[{ "voice_id":"voice_02","desc":"เสียงผู้หญิง โทนปกติ","instruct":"female","language":null,
   "preview_url":"http://<host>/voices/voice_02/preview?engine=omnivoice" }]
```
`GET /voices?language=lao`:
```json
[{ "voice_id":"lao_01","desc":"เสียงผู้ชาย ภาษาลาว โทนปกติ","instruct":"male","language":"Lao",
   "preview_url":"http://<host>/voices/lao_01/preview?engine=omnivoice" }]
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
  "num_step": 32,
  "speed": 1.0
}
```
| field | required | default | รายละเอียด |
|---|---|---|---|
| `text` | ✅ | — | ข้อความ |
| `voice_id` | ✳️ | — | เสียงสต็อก (ดู `/voices`) |
| `instruct` | ✳️ | — | ออกแบบเสียง เช่น `"female, high pitch, british accent"` |
| `engine` | ❌ | omnivoice | เลือกเอนจิน |
| `language` | ❌ | auto | เช่น `"Thai"`, `"English"`, `"Lao"` |
| `num_step` | ❌ | 32 | diffusion steps (4–64) สูง=ดีขึ้นแต่ช้า |
| `speed` | ❌ | 1.0 | ความเร็ว (0.3–3.0) |
| `guidance_scale` | ❌ | 2.0 | คุมความยึดเสียงต้นฉบับ; 3-4 = คล้ายขึ้นแต่เสี่ยงเพี้ยน |
| `class_temperature` | ❌ | 0.4 | คุมความหลากหลายของโทนเสียง — 0 = greedy (ผลซ้ำเดิมทุกครั้ง แต่แบน/หุ่นยนต์), สูงขึ้น = เป็นธรรมชาติขึ้นแต่เสี่ยงเพี้ยน |
| `mixed_language` | ❌ | true | แยกช่วงไทย/ลาว/อังกฤษ generate คนละภาษาแล้วต่อเสียง (กันโมเดลอ่านผิดภาษา) |
| `transliterate_english` | ❌ | true | แปลงคำอังกฤษที่พบบ่อย (เช่น "Google"→"กูเกิล") เป็นคำทับศัพท์ไทยก่อนอ่าน — กันเสียงสะดุด/เพี้ยนตรงรอยต่อภาษา ดูดิกคำที่รองรับใน `text_utils.ENGLISH_TO_THAI` (เพิ่มคำเองได้) |
| `normalize_numbers` | ❌ | true | แปลงตัวเลข/จำนวนเงิน/เบอร์โทร เป็นคำอ่านภาษาไทยก่อนอ่าน (เช่น "1,250 บาท"→"หนึ่งพันสองร้อยห้าสิบบาทถ้วน", "081-234-5678"→อ่านทีละหลัก) — กันสคริปต์กับเสียงที่ได้ไม่ตรงกัน |

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
| `ref_audio` | file | ✅ | ไฟล์เสียง แนะนำ 3–10 วิ (wav/mp3) — สั้นกว่า 2 วิ = ปฏิเสธ (422), ยาวกว่า 15 วิ = ตัดอัตโนมัติเหลือ 12 วิแรก |
| `ref_text` | string | ✅ | ข้อความที่พูดในไฟล์ ref |
| `text` | string | ✅ | ข้อความที่อยากให้พูด |
| `engine` | string | ❌ | ดีฟอลต์ omnivoice |
| `language` | string | ❌ | — |
| `num_step` | int | ❌ | 32 |
| `guidance_scale` | float | ❌ | ดีฟอลต์ 2.0 — คุมความยึดเสียงต้นฉบับ; 3-4 = คล้ายขึ้นแต่เสี่ยงเพี้ยน |
| `speed` | float | ❌ | 1.0 |
| `enhance_ref` | bool | ❌ | true — ลดเสียงรบกวน/normalize ไฟล์ ref อัตโนมัติก่อนโคลน (ปิดได้ถ้าไฟล์สะอาดอยู่แล้ว) |

ไม่เก็บไฟล์ ref ถาวร — ใช้ครั้งเดียวแล้วลบ
ภายในสร้างเสียง 3 รอบ (best-of-3) แล้วเลือกตัวที่คล้ายเสียงต้นฉบับที่สุดให้อัตโนมัติ — ไม่ต้องตั้งค่าเพิ่ม

---

## คลังเสียงโคลนถาวร (Custom Voices)

ต่างจาก `/clone` (ครั้งเดียว) — ที่นี่โคลนแล้ว **เก็บไว้ใช้ซ้ำ** ได้เหมือนเสียงสต็อก
เสียงเป็นของเจ้าของ key (คนอื่นมองไม่เห็น/ใช้ไม่ได้) `voice_id` ขึ้นต้นด้วย `cv_`

### `POST /voices` — สร้างเสียงโคลนถาวร (multipart/form-data)
| field | type | required | รายละเอียด |
|---|---|---|---|
| `ref_audio` | file | ✅ | ไฟล์เสียง แนะนำ 3–10 วิ (wav/mp3 — ระบบแปลงเป็น wav 24k ให้) — สั้นกว่า 2 วิ = ปฏิเสธ (422), ยาวกว่า 15 วิ = ตัดอัตโนมัติเหลือ 12 วิแรก |
| `name` | string | ❌ | ชื่อเสียง |
| `ref_text` | string | ❌ | ข้อความในไฟล์ — **เว้นว่าง = ถอดอัตโนมัติด้วย ASR** |
| `engine` | string | ❌ | ดีฟอลต์ omnivoice |
| `enhance_ref` | bool | ❌ | true — ลดเสียงรบกวน/normalize ไฟล์ ref อัตโนมัติก่อนเก็บ (ปิดได้ถ้าไฟล์สะอาดอยู่แล้ว) |

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

## เสียงภาษาลาว (Lao Voices)

โมเดล OmniVoice รองรับภาษาลาว (`lo`/`lao`) เป็นหนึ่งใน 646 ภาษาโดยตรงอยู่แล้ว ใช้งานได้ 2 แบบ:

1. **เสียงสต็อกลาวสำเร็จรูป** — เลือกผ่าน `voice_id` ตรงๆ เหมือนเสียงไทย (ดูตารางด้านล่าง) **แยก
   จากเสียงชุดหลักเสมอ** — `GET /voices` ดีฟอลต์จะไม่คืนเสียงลาวมาปนด้วย ต้องขอผ่าน `?language=lao`
2. **Voice Design / Voice Cloning แบบไม่ใช้เสียงสต็อก** — `/tts` ด้วย `instruct` + `language: "Lao"`,
   หรือ `/clone`/`/voices` ด้วยไฟล์เสียงลาวของจริง + `language: "Lao"` — เหมือนภาษาอื่นทุกอย่าง

### คลังเสียงสต็อกภาษาลาว (`voices_lao/`)

แยกเก็บต่างหากจากเสียงสต็อกไทยใน `voices/` โดยตั้งใจ (ไม่ปนรหัส `voice_XX`, ไม่ปนกันในไฟล์
manifest เดียวกัน) — สร้างด้วย `build_voices_lao.py` (คู่กับ `build_voices.py` ของฝั่งไทย) และ**เสียบเข้า
`server.py` เป็นเสียงสต็อกใช้งานได้จริงแล้ว** ผ่าน `OmniVoiceEngine._load_extra_manifest()` (โหลดต่อจาก
เสียงชุดหลัก, ปรับโฟลเดอร์ได้ด้วย env `TTS_LAO_VOICES_DIR`)

| id | คำอธิบาย | instruct | สถานะ |
|---|---|---|---|
| `lao_01` | ชาย โทนปกติ | male | ✅ ใช้งานได้ |
| `lao_02` | หญิง โทนปกติ | female | ✅ ใช้งานได้ |
| `lao_03` | ชาย วัยทำงานตอนต้น | young adult, male | ✅ ใช้งานได้ |
| `lao_04` | หญิง โทนสูง สดใส | female, high pitch | ✅ ใช้งานได้ |
| `lao_05` | ชาย สูงวัย ใจดี | elderly, male, very low pitch | ✅ ใช้งานได้ |
| `lao_06` | หญิง วัยรุ่น | teenager, female | ✅ ใช้งานได้ |
| `lao_07` | หญิง วัยกลางคน สง่างาม | middle-aged, female, high pitch | ✅ ใช้งานได้ |

> เอาเสียงกระซิบ (`lao_08`, male/whisper) ออกแล้ว — เหมือนที่เอาออกฝั่งไทยไปก่อนหน้านี้ (ฟังแล้วไม่เป็นธรรมชาติ)
> ไฟล์ `.wav` เดิมยังอยู่ใน `voices_lao/` เผื่อเอากลับมาใช้ทีหลัง

ใช้เหมือนเสียงสต็อกไทยทุกประการ:
```json
{ "voice_id": "lao_01", "text": "ສະບາຍດີ ມື້ນີ້ອາກາດດີຫຼາຍເລີຍ" }
```
ไม่ต้องส่ง `language` เอง — ถ้าไม่ระบุมา ระบบ fallback ไปใช้ `"Lao"` จาก manifest ของเสียงนั้นให้อัตโนมัติ
(กันเคสออกเสียงผิดตอนข้อความมีแต่ตัวเลข/สัญลักษณ์ที่เดาภาษาจาก unicode ไม่ได้) ส่วนข้อความที่เป็นตัวอักษร
ลาวจริง (unicode 0x0E80–0x0EFF) ระบบ `mixed_language` ที่เปิดอยู่โดยดีฟอลต์ตรวจจับให้เองอยู่แล้วเช่นกัน

ตอนนี้เปิดใช้งาน 7 เสียง — เพิ่มเสียงลาวใหม่ได้โดยเติม preset ใน `VOICE_PRESETS` ของ `build_voices_lao.py`
แล้วรันซ้ำ (เสียงเดิมที่มีไฟล์อยู่แล้วจะข้ามอัตโนมัติ ไม่สร้างทับ):
```bash
venv/Scripts/python.exe build_voices_lao.py --device cuda
```

---

## ASR — ถอดเสียงเป็นข้อความ

### `POST /transcribe` (multipart/form-data)
| field | type | required | รายละเอียด |
|---|---|---|---|
| `audio` | file | ✅ | ไฟล์เสียงที่จะถอด |
| `engine` | string | ❌ | ดีฟอลต์ omnivoice |

**Response** `200` → `{ "text": "ข้อความที่ถอดได้" }`

> ใช้ faster-whisper (CTranslate2 backend, เร็วกว่า transformers Whisper เดิมมาก) — ครั้งแรกจะโหลด
> โมเดล (~1.5GB, ดาวน์โหลดครั้งเดียว) request แรกช้ากว่าปกติ — ปรับโมเดล/device ได้ด้วย env
> `TTS_ASR_MODEL` / `TTS_ASR_DEVICE` / `TTS_ASR_COMPUTE_TYPE` (ดู `asr_engine.py`)

---

## ลดเสียงรบกวน + เพิ่มความชัด (Enhance)

### `POST /enhance` (multipart/form-data)
แยกเสียงพูดออกจากเสียงพื้นหลัง/ดนตรี/สัญญาณรบกวน (Demucs) + normalize ความดัง — คืนไฟล์เสียงที่
ทำความสะอาดแล้ว ใช้เดี่ยวๆ ได้ หรือปล่อยให้ `/clone`, `/voices` เรียกให้อัตโนมัติผ่าน `enhance_ref`
(ดีฟอลต์เปิดอยู่แล้วที่สองจุดนั้น — ใช้ `/enhance` ตรงๆ เมื่อต้องการแค่ทำความสะอาดไฟล์โดยไม่โคลนเสียง)

| field | type | required | รายละเอียด |
|---|---|---|---|
| `audio` | file | ✅ | ไฟล์เสียงที่ต้องการลดเสียงรบกวน/เพิ่มความชัด |

**Response** `200`
```json
{ "audio_base64": "UklGR...", "format": "wav", "sample_rate": 44100, "duration": 3.2 }
```
> ล้มเหลว/ไม่ได้ติดตั้ง Demucs → ยัง `200` ปกติแต่คืนไฟล์เดิม (normalize ความดังอย่างเดียว) ไม่ throw
> ปรับโมเดลได้ด้วย env `TTS_ENHANCE_MODEL` (ดีฟอลต์ `htdemucs`, ดู `audio_enhance.py`)

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
- **เสียงที่เปิด:** ปรับผ่าน env `TTS_VOICE_IDS` (เสียงชุดหลัก) / `TTS_LAO_VOICES_DIR` (เสียงลาว) — ดู `DEPLOY.md`
- Swagger UI ทดสอบสดที่ `GET /docs`
