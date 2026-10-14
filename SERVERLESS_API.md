# Hero Voice Serverless API

วิธีให้ระบบของเราส่งข้อความเข้าไปแล้วได้ไฟล์เสียงกลับมา ทุกคำขอใช้ OmniVoice เป็นตัวกำหนดเสียงและ VoxCPM2 เป็นตัวพูดโดยอัตโนมัติ เครื่องเปิดเฉพาะตอนมีงาน จ่ายเฉพาะวินาทีที่ใช้

| | |
|---|---|
| Endpoint ID | `76h1rl6xfabatu` |
| ชื่อใน RunPod | `heroai-hero-omnivoice-voxcpm2` |
| เสียงที่ได้ | mp3 48 kHz |
| ปรับปรุง | 14 ต.ค. 2569 |

## สารบัญ

1. [ภาพรวม](#ภาพรวม)
2. [ข้อมูลเชื่อมต่อ](#ข้อมูลเชื่อมต่อ)
3. [ส่งงาน](#ส่งงาน)
4. [รับผล](#รับผล)
5. [รูปแบบผลลัพธ์](#รูปแบบผลลัพธ์)
6. [รายชื่อเสียง](#รายชื่อเสียง)
7. [ตัวอย่างโค้ด](#ตัวอย่างโค้ด)
8. [ข้อจำกัดและเวลา](#ข้อจำกัดและเวลา)
9. [สำหรับผู้ดูแลระบบ](#สำหรับผู้ดูแลระบบ)

---

## ภาพรวม

บริการนี้รันบน RunPod Serverless แบบคิวงาน ระบบของเราส่งงานเข้าไป ได้รหัสงานกลับมาทันที แล้วค่อยไปรับไฟล์เสียงเมื่องานเสร็จ ไม่มีการรอสายค้างระหว่างสร้างเสียง จึงใช้กับสคริปต์ยาวหลายนาทีได้

1. **ส่งงาน**: `POST /run` พร้อมข้อความและเสียง ได้ `id` กลับมาทันที
2. **รอผล**: ถาม `GET /status/{id}` ทุก 2-3 วินาที หรือให้ RunPod ยิง webhook กลับมา
3. **รับไฟล์**: ถอด `output.audio_base64` เป็นไฟล์ mp3 แล้วเก็บหรือส่งต่อ

### สิ่งที่เกิดขึ้นในแต่ละคำขอ

- **เลือกเสียงด้วย `voice_id`**: VoxCPM2 โคลนจากเสียงอ้างอิงของ OmniVoice ตัวนั้นแล้วอ่านข้อความ
- **เลือกเสียงด้วย `instruct`**: OmniVoice สร้างเสียงตามคำสั่งก่อน แล้ว VoxCPM2 โคลนเสียงนั้นมาอ่าน คำสั่งเดิมได้เสียงเดิมทุกครั้ง
- **ถ้า VoxCPM2 ล้มที่ท่อนไหน**: OmniVoice สร้างท่อนนั้นแทนด้วยเสียงเดียวกัน งานไม่ล้ม
- **ตรวจผลอัตโนมัติ**: ทุกท่อนถูกถอดเสียงกลับมาเทียบกับข้อความ ท่อนที่อ่านไม่ตรงจะถูกสร้างใหม่สูงสุด 3 รอบ

---

## ข้อมูลเชื่อมต่อ

| รายการ | ค่า |
|---|---|
| Base URL | `https://api.runpod.ai/v2/76h1rl6xfabatu` |
| การยืนยันตัวตน | header `Authorization: Bearer <RUNPOD_API_KEY>` |
| Content-Type | `application/json` |
| สร้าง API key | runpod.io → Settings → API Keys |

> **เก็บ API key ไว้ฝั่ง backend เท่านั้น** key นี้สั่งงานได้ทั้งบัญชี RunPod ห้ามใส่ในหน้าเว็บ แอปมือถือ หรือ repository ให้ backend ของเราเป็นตัวกลางเรียกบริการนี้

---

## ส่งงาน

`POST /run` ส่ง body เป็น JSON ที่มี `input` อย่างน้อยต้องมี `text` และอย่างใดอย่างหนึ่งระหว่าง `voice_id` กับ `instruct`

```json
{
  "input": {
    "text": "สวัสดีค่ะ ยินดีต้อนรับเข้าสู่บริการของเรา\nวันนี้มีโปรโมชั่นลด 20 เปอร์เซ็นต์ โทร 02 123 4567 ค่ะ",
    "voice_id": "voice_02"
  },
  "webhook": "https://api.example.com/tts/callback?token=SECRET"
}
```

ตอบกลับทันทีด้วยรหัสงาน:

```json
{ "id": "7d530e5f-84b5-4f62-933d-cd55b6e8f884-e2", "status": "IN_QUEUE" }
```

### ฟิลด์ใน `input`

| ฟิลด์ | ชนิด | ค่าเริ่มต้น | ความหมาย |
|---|---|---|---|
| `text` **(จำเป็น)** | string | | ข้อความที่จะอ่าน ไทย ลาว และคำอังกฤษปนได้ ตัวเลข จำนวนเงิน เบอร์โทร เขียนเป็นตัวเลขตามปกติ ขึ้นบรรทัดใหม่ตรงจุดที่อยากให้หยุดเว้นจังหวะ |
| `voice_id` | string | | รหัสเสียงจาก[รายชื่อเสียง](#รายชื่อเสียง) |
| `instruct` | string | | ออกแบบเสียงด้วยคำศัพท์ที่กำหนด คั่นด้วยจุลภาค เช่น `female, low pitch` ใช้แทน `voice_id` |
| `format` | string | `mp3` | `mp3` (192 kbps) หรือ `wav` ถ้าขอ wav แล้วไฟล์ใหญ่เกินขีดจำกัด ระบบส่ง mp3 แทนและบอกใน `output.format` |
| `speed` | number | `1.0` | ความเร็วพูด ราว 0.5 ถึง 2.0 ค่าอื่นนอกจาก 1.0 ทำให้เสียงเป็นธรรมชาติน้อยลงเล็กน้อย |
| `engine` | string | `hero` | ไม่ต้องส่ง ใช้ OmniVoice + VoxCPM2 อัตโนมัติ ส่ง `omnivoice` ถ้าต้องการความเร็วสูงสุด หรือ `voxcpm2` สำหรับใช้ตัวเดียว |
| `num_step` | integer | `32` | คุณภาพเทียบกับความเร็ว ค่าเริ่มต้นตรงกับไฟล์ที่ทดสอบและอนุมัติแล้ว ไม่แนะนำให้เปลี่ยน |
| `guidance_scale` | number | `2.0` | ความยึดเสียงต้นฉบับ ไม่แนะนำให้เปลี่ยน |

`webhook` อยู่นอก `input` และไม่บังคับ ใส่เมื่ออยากให้ RunPod ส่งผลกลับมาเองแทนการถามสถานะ ดู[รับผล](#รับผล)

### โคลนเสียงจากไฟล์ (ใช้ครั้งเดียว)

ส่ง `"mode": "clone"` พร้อมไฟล์เสียงตัวอย่าง 3-10 วินาทีที่แปลงเป็น base64 และข้อความที่พูดในไฟล์นั้น ระบบสร้าง 3 รอบแล้วเลือกรอบที่เหมือนต้นฉบับที่สุด

```json
{
  "input": {
    "mode": "clone",
    "ref_audio_b64": "<base64 ของไฟล์ wav หรือ mp3>",
    "ref_text": "ข้อความที่พูดในไฟล์ตัวอย่าง",
    "text": "ข้อความที่ต้องการให้อ่านด้วยเสียงนี้"
  }
}
```

---

## รับผล

### แบบที่ 1: ถามสถานะ

`GET /status/{id}` ทุก 2-3 วินาที จนสถานะเป็นสถานะจบ ผลเก็บไว้ให้ดึงได้ 30 นาทีหลังงานเสร็จ

| status | ความหมาย | ระบบเราควรทำ |
|---|---|---|
| `IN_QUEUE` | รอ worker ว่าง หรือ worker กำลังเปิดเครื่อง | รอต่อ |
| `IN_PROGRESS` | กำลังสร้างเสียง | รอต่อ |
| `COMPLETED` | เสร็จ ผลอยู่ใน `output` | เช็ก `output.error` ก่อน แล้วถอดไฟล์เสียง |
| `FAILED` | worker ทำงานผิดพลาด | ส่งงานใหม่ 1 ครั้ง ถ้ายังล้มให้แจ้งผู้ดูแล |
| `TIMED_OUT` | ใช้เวลาเกิน 10 นาที | แบ่งข้อความให้สั้นลงแล้วส่งใหม่ |
| `CANCELLED` | ถูกยกเลิกด้วย `POST /cancel/{id}` | ไม่ต้องทำอะไร |

### แบบที่ 2: webhook (แนะนำสำหรับ backend)

ใส่ `"webhook": "https://..."` ตอนส่ง `/run` เมื่องานจบ RunPod จะ POST JSON ชุดเดียวกับผลของ `/status` มาที่ URL นั้น ระบบเราตอบ HTTP 200 กลับไปเป็นอันจบ

> RunPod ไม่ได้เซ็นลายเซ็นใน webhook ให้ใส่ token ลับไว้ใน query string ของ URL แล้วตรวจทุกครั้งที่รับ เช่น `?token=SECRET` และเผื่อทางสำรองด้วยการถาม `/status` ถ้าไม่ได้รับ webhook ภายในเวลาที่กำหนด

---

## รูปแบบผลลัพธ์

ผลของ `/status` เมื่องานเสร็จ:

```json
{
  "id": "7d530e5f-84b5-4f62-933d-cd55b6e8f884-e2",
  "status": "COMPLETED",
  "delayTime": 412,
  "executionTime": 147310,
  "output": {
    "engine": "hero",
    "voice_id": "voice_02",
    "text": "สวัสดีค่ะ ...",
    "audio_base64": "SUQzBAAAAAAAI1RTU0UAAAA...",
    "format": "mp3",
    "sample_rate": 48000,
    "duration": 197.62,
    "generation_time": 146.1,
    "chunks": 19
  }
}
```

| ฟิลด์ใน `output` | ความหมาย |
|---|---|
| `audio_base64` | ไฟล์เสียงทั้งไฟล์ เข้ารหัส base64 ถอดแล้วเขียนลงไฟล์ได้เลย |
| `format` | `mp3` หรือ `wav` ใช้ค่านี้ตั้งนามสกุลไฟล์และ Content-Type |
| `duration` | ความยาวเสียงเป็นวินาที ใช้คิดเครดิตลูกค้าได้ |
| `sample_rate` | 48000 เมื่อใช้ hero |
| `chunks` | จำนวนท่อนที่ระบบตัดข้อความ |
| `similarity_score` | เฉพาะ mode clone ความเหมือนเสียงต้นฉบับ 0 ถึง 1 |
| `error` | มีเมื่อข้อมูลที่ส่งมาใช้ไม่ได้ เช่น ไม่ระบุเสียง ไม่มี voice_id นี้ หรือ instruct มีคำที่ไม่รองรับ แก้ข้อมูลแล้วส่งใหม่ ไม่ต้องลองซ้ำแบบเดิม |

`delayTime` คือเวลารอคิวรวมเวลาเปิดเครื่อง และ `executionTime` คือเวลาสร้างเสียงจริง ทั้งสองเป็นมิลลิวินาที

---

## รายชื่อเสียง

เสียงไทย 33 เสียงและเสียงลาว 7 เสียง ตัวเลขใน `voice_id` ไม่ได้เรียงต่อกัน ให้ใช้รหัสตามรายการนี้เท่านั้น

### ไทย

| voice_id | ชื่อ | ลักษณะ |
|---|---|---|
| `voice_01` | นนท์ | male |
| `voice_02` | นภา | female |
| `voice_03` | ปิติ | male, high pitch |
| `voice_04` | ไพลิน | female, low pitch |
| `voice_05` | วีระ | male, very low pitch |
| `voice_06` | แพรว | female, very high pitch |
| `voice_07` | น้องพลอย | child |
| `voice_08` | แนน | teenager, female |
| `voice_09` | แบงค์ | teenager, male |
| `voice_10` | ณิชา | young adult, female |
| `voice_11` | กรณ์ | young adult, male |
| `voice_12` | ปกรณ์ | middle-aged, male |
| `voice_13` | วริศรา | middle-aged, female |
| `voice_14` | ลุงธานี | elderly, male |
| `voice_15` | ยายบัว | elderly, female |
| `voice_17` | น้องบอล | child, male |
| `voice_18` | น้องมะลิ | child, female |
| `voice_19` | ธาม | male, moderate pitch |
| `voice_20` | พิมพ์ | female, moderate pitch |
| `voice_21` | เก้า | teenager, male, high pitch |
| `voice_22` | ภาคิน | young adult, male, low pitch |
| `voice_23` | พราว | young adult, female, high pitch |
| `voice_24` | อนันต์ | middle-aged, male, very low pitch |
| `voice_25` | ปู่บุญมี | elderly, male, very low pitch |
| `voice_26` | ยายแก้ว | elderly, female, high pitch |
| `voice_34` | เจน | teenager, female, low pitch |
| `voice_35` | เนม | teenager, male, moderate pitch |
| `voice_36` | วรรณา | middle-aged, female, high pitch |
| `voice_37` | น้องขนุน | child, very high pitch |
| `voice_44` | บูม | young adult, male, very high pitch |
| `voice_45` | รินดา | young adult, female, very low pitch |
| `voice_46` | อาทิตย์ | middle-aged, male, high pitch |
| `voice_47` | ยายผ่อง | elderly, female, very low pitch |

### ลาว

| voice_id | ชื่อ | ลักษณะ |
|---|---|---|
| `lao_01` | ผู้ชาย โทนปกติ | male |
| `lao_02` | ผู้หญิง โทนปกติ | female |
| `lao_03` | ผู้ชาย วัยทำงานตอนต้น | young adult, male |
| `lao_04` | ผู้หญิง โทนสูง สดใส | female, high pitch |
| `lao_05` | ผู้ชาย สูงวัย ใจดี | elderly, male, very low pitch |
| `lao_06` | ผู้หญิง วัยรุ่น | teenager, female |
| `lao_07` | ผู้หญิง วัยกลางคน สง่างาม | middle-aged, female, high pitch |

### คำที่ใช้ใน `instruct` ได้

เลือกได้หมวดละไม่เกินหนึ่งคำ คั่นด้วยจุลภาค ใช้ภาษาอังกฤษตัวพิมพ์เล็กตามนี้เท่านั้น คำอื่นจะได้ `output.error`

| หมวด | คำ |
|---|---|
| เพศ | `male` `female` |
| อายุ | `child` `teenager` `young adult` `middle-aged` `elderly` |
| ระดับเสียง | `very low pitch` `low pitch` `moderate pitch` `high pitch` `very high pitch` |
| สำเนียง | `american accent` `british accent` `australian accent` `indian accent` `canadian accent` `chinese accent` `korean accent` `japanese accent` `portuguese accent` `russian accent` |

---

## ตัวอย่างโค้ด

### curl

```bash
curl -s -X POST https://api.runpod.ai/v2/76h1rl6xfabatu/run \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"text": "สวัสดีค่ะ ยินดีต้อนรับ", "voice_id": "voice_02"}}'

# นำ id ที่ได้มาถามสถานะ
curl -s https://api.runpod.ai/v2/76h1rl6xfabatu/status/<id> \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

### Python

```python
import base64, os, time
import requests

API = "https://api.runpod.ai/v2/76h1rl6xfabatu"
H = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}

def tts(text, voice_id=None, instruct=None, timeout_s=900):
    inp = {"text": text}
    if voice_id: inp["voice_id"] = voice_id
    if instruct: inp["instruct"] = instruct
    job = requests.post(f"{API}/run", json={"input": inp}, headers=H, timeout=30).json()["id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = requests.get(f"{API}/status/{job}", headers=H, timeout=30).json()
        if s["status"] == "COMPLETED":
            out = s["output"]
            if "error" in out:
                raise ValueError(out["error"])          # ข้อมูลผิด แก้ก่อนส่งใหม่
            return base64.b64decode(out["audio_base64"]), out["format"]
        if s["status"] in ("FAILED", "CANCELLED", "TIMED_OUT"):
            raise RuntimeError(f"job {job}: {s['status']}")
        time.sleep(3)
    requests.post(f"{API}/cancel/{job}", headers=H, timeout=30)
    raise TimeoutError(job)

audio, fmt = tts("สวัสดีค่ะ ยินดีต้อนรับ", voice_id="voice_02")
open(f"out.{fmt}", "wb").write(audio)
```

### Node.js 18+

```javascript
const API = "https://api.runpod.ai/v2/76h1rl6xfabatu";
const H = { Authorization: `Bearer ${process.env.RUNPOD_API_KEY}`, "Content-Type": "application/json" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function tts({ text, voiceId, instruct, timeoutMs = 900_000 }) {
  const input = { text, ...(voiceId && { voice_id: voiceId }), ...(instruct && { instruct }) };
  const { id } = await (await fetch(`${API}/run`, { method: "POST", headers: H, body: JSON.stringify({ input }) })).json();
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const s = await (await fetch(`${API}/status/${id}`, { headers: H })).json();
    if (s.status === "COMPLETED") {
      if (s.output.error) throw new Error(s.output.error);
      return { audio: Buffer.from(s.output.audio_base64, "base64"), format: s.output.format };
    }
    if (["FAILED", "CANCELLED", "TIMED_OUT"].includes(s.status)) throw new Error(`job ${id}: ${s.status}`);
    await sleep(3000);
  }
  await fetch(`${API}/cancel/${id}`, { method: "POST", headers: H });
  throw new Error(`timeout ${id}`);
}
```

---

## ข้อจำกัดและเวลา

### เวลาที่วัดได้จริง

| สถานการณ์ | รอคิว | สร้างเสียง | เสียงที่ได้ |
|---|---|---|---|
| สารคดี 3 นาที 19 ท่อน (worker เปิดอยู่) | 0 วินาที | 147 วินาที | 197.6 วินาที |
| ประโยคสั้น (งานแรกหลังสร้าง endpoint) | 438 วินาที | 27 วินาที | 7.9 วินาที |

งานแรกหลังสร้าง endpoint ช้าเพราะเครื่องต้องดาวน์โหลด image 12 GB ครั้งแรก ปกติหลังจากนั้นถ้า worker ดับไปแล้ว งานแรกที่เข้ามาจะรอเปิดเครื่องราว 1-3 นาที งานที่ตามมาระหว่าง worker ยังเปิดอยู่เริ่มทันที

### ขีดจำกัด

- **ตั้ง timeout ฝั่งเราอย่างน้อย 10-15 นาที**: เผื่อเวลาเปิดเครื่องรวมกับเวลาสร้างเสียง
- **เสียงยาวไม่เกินราว 6 นาทีต่องาน**: RunPod จำกัดผลลัพธ์ไว้ราว 10 MB ต่องาน ถ้ายาวกว่านั้นให้แบ่งข้อความเป็นหลายงานแล้วต่อไฟล์เอง
- **งานหนึ่งรันได้ไม่เกิน 10 นาที**: สร้างเสียงใช้เวลาราว 0.75 เท่าของความยาวเสียง
- **รันพร้อมกันได้ 2 งาน**: งานที่เกินจะรอคิว ปรับเพิ่มได้ที่ Max workers ของ endpoint
- **ยังไม่รองรับเสียงโคลนถาวร**: ถ้าลูกค้าต้องการใช้เสียงโคลนเดิมซ้ำ ให้ส่ง mode clone พร้อมไฟล์ตัวอย่างทุกครั้ง

### การเขียนข้อความให้ได้เสียงดี

- ขึ้นบรรทัดใหม่ตรงจุดที่อยากให้หยุด ระบบตัดท่อนตามบรรทัดและเว้น 0.45 วินาทีระหว่างท่อน ไม่ขึ้นบรรทัดก็ได้ ระบบจะตัดท่อนละไม่เกิน 200 ตัวอักษรที่ช่องว่างให้เอง
- ไม่ต้องแปลงตัวเลขหรือคำอังกฤษเป็นคำอ่านเอง เขียนตามปกติ เช่น `1,500 บาท` `02 123 4567` `API`
- ใช้ไม้ยมกได้ตามปกติ เช่น `ค่อย ๆ`

### ค่าใช้จ่าย

คิดตามวินาทีที่ worker เปิดอยู่ ทั้งช่วงสร้างเสียงและช่วงรอ 60 วินาทีหลังงานสุดท้ายก่อนดับ ไม่มีงานก็ไม่มีค่าใช้จ่าย สารคดี 3 นาทีใช้เครื่องราว 150 วินาที หรือประมาณ 1-2 บาทบน GPU 24 GB

---

## สำหรับผู้ดูแลระบบ

| รายการ | ค่า |
|---|---|
| Endpoint | `heroai-hero-omnivoice-voxcpm2` · `76h1rl6xfabatu` |
| Template | `6r2mlwzl7p` |
| Image | `ghcr.io/aoacademy2025/hero-voice-ai:serverless-<commit sha>` ฝังโมเดลไว้ในตัว |
| GPU | RTX 3090, A5000, 4090, L4, A4500, 4000 Ada, A40, A6000 |
| Workers | ต่ำสุด 0 · สูงสุด 2 · idle 60 วินาที · FlashBoot เปิด |
| Execution timeout | 600 วินาที |

### อัปเดตเวอร์ชัน

1. push โค้ดเข้า branch `main` ของ repo Hero-Voice-Ai
2. GitHub Actions build image สองตัวให้อัตโนมัติ ราว 10-15 นาที รวมขั้นทดสอบ import ถ้าพังจะล้มที่ CI
3. แก้ Container image ของ template ให้เป็น tag `serverless-<commit sha>` ใหม่ RunPod จะเปลี่ยน worker เป็นเวอร์ชันใหม่ให้เอง

### เมื่องานค้างใน `IN_QUEUE` นานผิดปกติ

- เปิดหน้า endpoint → Workers ถ้า worker ขึ้นแล้วดับซ้ำ ๆ ให้ดู Logs ของ worker นั้น ส่วนใหญ่เป็นปัญหาตอนเริ่ม container
- ถ้ายังแก้ไม่ได้ ตั้ง Max workers เป็น 0 ชั่วคราวเพื่อหยุดค่าใช้จ่าย แล้วค่อยแก้ image
- image ต้องใช้ PyTorch 2.8 ขึ้นไป รุ่น 2.5.1 เคยทำให้ worker พังทุกตัว
