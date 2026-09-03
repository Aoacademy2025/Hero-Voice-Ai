# Hero Voice TTS API — GPU image
# Base: CUDA 12.1 + cuDNN runtime (ตรงกับ torch cu121 wheels)
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# libsndfile จำเป็นสำหรับ soundfile, ffmpeg สำหรับ pydub/librosa
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# base image นี้มี torch/torchaudio (cu121) มาแล้ว — ไม่ต้องติดตั้งซ้ำ
# (requirements.txt ตั้งใจไม่ใส่ torch/torchaudio เพื่อกัน pip ติดตั้งทับ
#  เป็นเวอร์ชันที่ ABI ไม่ตรงกับตัวที่ base image มีมาให้)

# 1) ติดตั้ง deps ของ API (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) ติดตั้งแพ็กเกจ omnivoice จากซอร์สในโปรเจกต์
#    --no-deps: omnivoice ประกาศ torch/torchaudio เป็น dependency ด้วย ถ้าไม่กัน
#    pip จะไปติดตั้งทับตัว cu121 wheel ที่ base image เตรียมไว้ (ABI ไม่ตรงกัน)
COPY OmniVoice/ /app/OmniVoice/
RUN pip install --no-cache-dir --no-deps /app/OmniVoice \
    && pip install --no-cache-dir \
        accelerate pydub tensorboardX webdataset numpy soundfile librosa

# 3) โค้ด server + สคริปต์ — ก็อปทั้งโฟลเดอร์ทับซ้อนโครงสร้างเดียวกับ host เป๊ะ (core/, scripts/,
#    data/) แทนที่จะแบนเข้า /app ตรงๆ — server.py ใช้ __file__-relative path หาไฟล์ที่อยู่ติดกัน
#    (studio.html) และไฟล์ข้อมูลที่รากรีโป (data/en_th_transliteration.tsv, model/, voices/) อยู่แล้ว
#    ก็อปให้โครงสร้างตรงกับ host พอดีจะได้ไม่ต้องมี logic แยกสองแบบ
COPY core/ /app/core/
COPY scripts/ /app/scripts/
COPY data/ /app/data/
# โมเดล + เสียง: จะ COPY เข้า image หรือ mount เป็น volume ก็ได้ (ดู DEPLOY.md)
COPY model/ /app/model/
COPY voices/ /app/voices/

# path ให้ server หาโมเดล/เสียงเจอใน container (ตรงกับดีฟอลต์อยู่แล้วเพราะโครงสร้างเหมือน host
# แต่ตั้งชัดเจนไว้กันพลาดถ้าย้ายโฟลเดอร์ในอนาคต)
ENV TTS_MODEL_DIR=/app/model \
    TTS_VOICES_DIR=/app/voices \
    TTS_MAX_CONCURRENCY=2 \
    TTS_CORS_ORIGINS=*
# หมายเหตุ: ตั้ง TTS_API_KEY หรือ TTS_CREDITS_DB ตอน run (อย่า bake key ลง image)

EXPOSE 8000

# 1 worker เพราะโมเดลกิน VRAM + generate ทีละงานอยู่แล้ว
# สเกลด้วยการเพิ่ม container/GPU แทนการเพิ่ม worker ใน process เดียว
CMD ["python", "core/server.py"]
