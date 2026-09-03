# Deploy Hero Voice TTS → RunPod

โมเดลเล็ก (~2.5GB VRAM) ใช้ GPU อะไรก็ได้

## 🔰 ถ้ากลัวทำผิดแล้วเสียเงิน — เริ่มที่ "วิธี C" ด้านล่างก่อน

วิธี C ไม่ต้อง build/push Docker image (ไม่มีขั้นตอนที่ทำพลาดแล้วเสียเวลา build ใหม่)
และไม่ต้อง upload โมเดล 3GB จากเครื่องคุณ (โหลดสดจาก Hugging Face บน Pod แทน — เร็วกว่ามาก)
สิ่งที่ต้องย้ายจากเครื่องคุณจริงๆ มีแค่โค้ด + เสียงสต็อก ~10MB

### ✅ กันเสียเงินเปล่า — เช็คก่อนเริ่มเสมอ
1. **เติมเงินเข้า RunPod แค่น้อยๆ ก่อน** (เช่น $10) — ถ้าโค้ดพังจนวนลูปใช้เงิน อย่างมากก็เสียแค่ที่เติมไว้ ไม่ใช่เต็มวงเงินบัตร
2. **เลือก GPU ถูกสุดที่พอใช้ได้ก่อน** (RTX 3090/A4000/L4 — โมเดลเล็กมาก ไม่ต้อง GPU แรง)
3. **จับเวลาตัวเอง** ตั้งใจไว้เลยว่าจะทดสอบกี่นาที (เช่น 30 นาที) แล้วตั้งเตือน
4. **เมื่อเสร็จ ต้องกด "Stop" หรือ "Terminate" ที่ Pod เสมอ** — แค่ปิดแท็บเบราว์เซอร์ **ไม่หยุดการเก็บเงิน**
5. หลัง Stop แล้ว เข้าไปเช็คหน้า **"My Pods"** อีกรอบว่าไม่มี Pod ไหนค้าง "Running" อยู่

---

## วิธี C: ไม่ต้อง Docker, ไม่ต้อง git — โหลดโมเดลสดจาก Hugging Face (แนะนำสำหรับมือใหม่)

### ขั้น 1 (บนเครื่องคุณ ฟรี) — เตรียมไฟล์ที่ต้องอัปโหลด
ไม่ต้องเอา `model/` ไปด้วย (จะโหลดสดบน Pod) — zip แค่นี้ (รักษาโครงสร้างโฟลเดอร์ `core/`/`scripts/` ไว้):
```
core/   (server.py, studio.html, credits.py, text_utils.py, voice_library.py,
         voice_similarity.py, asr_engine.py, audio_enhance.py,
         watermark.py, gemini_translit.py, runpod_handler.py)
scripts/build_voices.py
scripts/manage_keys.py
requirements.txt OmniVoice/ voices/ data/
```
รวมกัน ~10MB — ใส่ในไฟล์ zip เดียว (เช่น `herovoice_code.zip`) — เช่น
`zip -r herovoice_code.zip core/ scripts/build_voices.py scripts/manage_keys.py requirements.txt OmniVoice/ voices/ data/`
(รันจากรากรีโป — จะได้ zip ที่ unzip แล้วโครงสร้างเหมือนบนเครื่อง dev เป๊ะ)

### ขั้น 2 — สร้าง Pod บน RunPod
1. runpod.io → **Deploy** → **Pods**
2. เลือก **template: "RunPod PyTorch 2.x"** (มี CUDA + torch ติดตั้งมาให้แล้ว — ไม่ต้องใช้ image ของเราเอง)
3. เลือก GPU ถูกสุดที่มี (RTX 3090 / A4000 / L4 — พอเหลือเฟือสำหรับโมเดลนี้)
4. Container Disk ตั้ง **20GB+** (กันโมเดล+dependency ไม่พอที่)
5. กด **Deploy**

### ขั้น 3 — เข้า Pod แล้วอัปโหลดไฟล์
1. กด **Connect** → เปิด **Jupyter Lab** (มีมากับ template นี้)
2. ในหน้า Jupyter → คลิก **Upload** → เลือก `herovoice_code.zip` ที่เตรียมไว้
3. เปิด **Terminal** ใน Jupyter แล้วรัน:
```bash
cd /workspace
unzip herovoice_code.zip -d herovoice
cd herovoice
pip install -r requirements.txt
pip install --no-deps ./OmniVoice
pip install accelerate pydub tensorboardX webdataset numpy soundfile librosa

# โหลดโมเดลสดจาก Hugging Face (~3GB, ไม่ต้อง upload จากเครื่องคุณ)
pip install -U "huggingface_hub[cli]"
huggingface-cli download k2-fsa/OmniVoice --local-dir ./model
```

### ขั้น 4 — รัน + ทดสอบ
```bash
export TTS_API_KEY="<ตั้ง key ของคุณเอง>"
export TTS_MODEL_DIR=/workspace/herovoice/model
export TTS_VOICES_DIR=/workspace/herovoice/voices
python core/server.py
```
กลับไปที่หน้า Pod บน RunPod → **Connect** → เปิด **HTTP Service [Port 8000]**
→ ได้ URL เช่น `https://<pod-id>-8000.proxy.runpod.net`

ทดสอบจากเครื่องคุณ:
```bash
curl https://<pod-id>-8000.proxy.runpod.net/health
```
เปิด URL ในเบราว์เซอร์ → ควรเห็นหน้า Hero Voice Studio → ใส่ API key → ลองสร้างเสียง

### ขั้น 5 — **เสร็จแล้วต้อง Stop Pod ทันที**
กลับไปหน้า RunPod → **My Pods** → กด **Stop** (หรือ **Terminate** ถ้าไม่ใช้ต่อแล้ว)
> Terminate จะลบ Pod ทิ้งถาวร (ต้องอัปโหลดไฟล์ใหม่ครั้งหน้า) — Stop แค่หยุดชั่วคราว เสียค่า storage เล็กน้อยต่อเดือนแต่ข้อมูลยังอยู่

**ถ้าวิธี C ใช้งานได้ดีแล้ว** ค่อยพิจารณาวิธี A (Docker image ทำซ้ำได้ง่ายกว่าสำหรับ production จริง) ในขั้นถัดไป

---

## วิธี A: Docker image (สำหรับ production ที่ต้อง deploy ซ้ำหลายรอบ)

### 1. Build image (ทำบนเครื่องคุณ — ต้องมี Docker Desktop)
```bash
cd c:/Users/USER/omnivoice
docker build -t <dockerhub-user>/herovoice:latest .
```
> image จะ ~6GB (โมเดล 2.3GB bake อยู่ข้างใน) build ครั้งแรกนานหน่อย

### 2. Push ขึ้น Docker Hub
```bash
docker login
docker push <dockerhub-user>/herovoice:latest
```
(หรือใช้ GHCR: `ghcr.io/<github-user>/herovoice:latest`)

### 3. สร้าง Pod บน RunPod
1. runpod.io → **Deploy** → **Pods** → เลือก GPU
   - ประหยัด: RTX 3090 / A4000 / L4 (พอเหลือเฟือ)
   - เร็ว: RTX 4090 / A5000
2. **Edit Template:**
   - Container Image = `<dockerhub-user>/herovoice:latest`
   - **Expose HTTP Ports** = `8000`
   - Container Disk = 15 GB+
3. **Environment Variables** (สำคัญ — ตั้ง auth ก่อนเปิด public):
   ```
   TTS_API_KEY = <ตั้ง key ของคุณ>
   TTS_CORS_ORIGINS = *          (หรือโดเมนจริง)
   ```
4. **Deploy** → รอ Pod ขึ้น → กด **Connect** → เปิด **HTTP Service [Port 8000]**
   → ได้ URL แบบ `https://<pod-id>-8000.proxy.runpod.net` = หน้า Studio + API

### 4. ทดสอบ
```bash
curl https://<pod-id>-8000.proxy.runpod.net/health
```
เปิด URL ในเบราว์เซอร์ → หน้า Studio → ใส่ API key ในช่อง → ลองสร้างเสียง

---

## ข้อมูลถาวร (custom voices / credits) — ต้องใช้ Network Volume

ถ้าไม่ผูก volume เสียงโคลน + เครดิตจะ**หายเมื่อ Pod รีสตาร์ต**

1. RunPod → **Storage** → สร้าง **Network Volume** (region เดียวกับ Pod) เช่น 20GB
2. ตอนสร้าง Pod → attach volume (mount ที่ `/workspace`)
3. เพิ่ม env ให้ชี้ข้อมูลไป volume:
   ```
   TTS_CUSTOM_VOICES_DIR = /workspace/custom_voices
   TTS_VOICES_DB         = /workspace/custom_voices/voices.db
   TTS_CREDITS_DB        = /workspace/credits.db      (ถ้าใช้โหมดเครดิต)
   HF_HOME               = /workspace/hf              (cache โมเดล ASR ~1.5GB ไม่ให้โหลดซ้ำ)
   ```

---

## วิธี B: ไม่ build image เอง (ใช้ RunPod PyTorch template + git)

เหมาะถ้าไม่อยาก build/push image ใหญ่

1. สร้าง Pod ด้วย template **RunPod PyTorch 2.x** (มี CUDA + torch อยู่แล้ว)
2. เข้า Web Terminal ของ Pod:
   ```bash
   cd /workspace
   git clone <repo-url> herovoice && cd herovoice
   pip install -r requirements.txt
   pip install --no-deps ./OmniVoice
   pip install accelerate pydub soundfile librosa
   ```
3. เอา **model/** + **voices/** ขึ้นไป (โมเดล 2.3GB):
   - ใช้ `runpodctl send` จากเครื่องคุณ, หรือ
   - ดาวน์โหลดจาก HF/cloud storage ลง `/workspace/herovoice/model`
4. รัน:
   ```bash
   export TTS_API_KEY=<key>
   python core/server.py
   ```
5. Expose port 8000 ในตั้งค่า Pod เหมือนวิธี A

---

## ค่าใช้จ่ายโดยประมาณ (เช็คราคาจริงอีกครั้ง)
| แบบ | GPU | ราคา |
|---|---|---|
| Pod ค้างไว้ 24/7 | RTX 3090/A4000 (community) | ~$0.2–0.4/ชม (~$150–290/เดือน) |
| เปิดใช้เป็นครั้ง | เปิด/ปิด Pod เอง | จ่ายเฉพาะตอนเปิด |
| Serverless | ต้องทำ handler เพิ่ม | จ่ายตามวินาทีที่ใช้ (ถูกสุดถ้าโหลดไม่คงที่) |

> Pod ธรรมดาเหมาะกับ API ที่มี UI + คนใช้ต่อเนื่อง. Serverless ถูกกว่าถ้าโหลดกระจาย แต่มี cold start และต้องเขียน handler เพิ่ม (บอกได้ถ้าต้องการ)
