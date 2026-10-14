# Hero Voice TTS API — GPU image (OmniVoice + VoxCPM2)
#
# โมเดลไม่ได้ bake ลง image (OmniVoice 3GB + VoxCPM2 4.6GB) — scripts/docker_entrypoint.sh
# ดาวน์โหลดจาก Hugging Face ตอนสตาร์ทครั้งแรกลง /workspace (mount Network Volume ไว้ที่นั่น
# แล้วรอบหน้าไม่ต้องโหลดซ้ำ) ทำให้ image เล็กพอ build บน GitHub Actions ได้ และอัปเดตโค้ดเร็ว
#
# build:  docker build -t herovoice-tts .
#         docker build --build-arg WITH_VOXCPM2=0 -t herovoice-tts:omni .   # OmniVoice อย่างเดียว
# run:    docker run --gpus all -p 8000:8000 -e TTS_API_KEY=<key> -v herovoice:/workspace herovoice-tts
#
# RunPod Serverless (จ่ายเฉพาะตอนมีคำขอ): image tag :serverless build ด้วย
#   --build-arg TTS_MODE_DEFAULT=serverless --build-arg BAKE_MODELS=1 --build-arg MODELS_DIR=/models
# → รัน core/runpod_handler.py แทน server และฝังโมเดลไว้ใน image (cold start ไม่ต้องดาวน์โหลด 8GB)
#
# torch 2.8 (cu128): ตรงกับ Pod ที่ทดสอบผ่าน — 2.5.1 import transformers รุ่นปัจจุบันไม่ผ่าน
# (GenerationMixin) และไม่รองรับ GPU Blackwell ที่ RunPod Serverless ใช้
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

# libsndfile: soundfile | ffmpeg: pydub/librosa | gcc/g++: torch.compile (inductor) ของ VoxCPM2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg git curl gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# base image มี torch/torchaudio (cu121) แล้ว — requirements.txt ตั้งใจไม่ใส่ torch กันติดตั้งทับ
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# omnivoice/voxcpm ติดตั้งแบบ --no-deps: ทั้งคู่ประกาศ torch/gradio/funasr/modelscope ฯลฯ เป็น dependency
# ซึ่งจะติดตั้ง torch ทับ (ABI ไม่ตรง) และดึงของหนักที่ server ไม่ได้ใช้
COPY OmniVoice/ /app/OmniVoice/
RUN pip install --no-cache-dir --no-deps /app/OmniVoice \
    && pip install --no-cache-dir accelerate pydub tensorboardX webdataset python-multipart huggingface_hub runpod nara_wpe

ARG WITH_VOXCPM2=1
RUN if [ "$WITH_VOXCPM2" = "1" ]; then \
        pip install --no-cache-dir --no-deps voxcpm==2.0.3 \
        && pip install --no-cache-dir inflect wetext addict simplejson sortedcontainers einops ; \
    fi

# ข้อมูลที่ต้องอยู่ถาวร (โมเดล, HF cache, เสียงโคลน, เครดิต) อยู่ใต้ /workspace ทั้งหมด
#  (serverless: โมเดลฝังใน /models ตอน build แทน — ดู BAKE_MODELS ด้านล่าง)
ARG MODELS_DIR=/workspace
ARG TTS_MODE_DEFAULT=server
ENV TTS_MODE=${TTS_MODE_DEFAULT} \
    TTS_ENGINES=omnivoice,voxcpm2 \
    TTS_MODEL_DIR=${MODELS_DIR}/model \
    HF_HOME=${MODELS_DIR}/hf \
    TTS_VOICES_DIR=/app/voices \
    TTS_LAO_VOICES_DIR=/app/voices_lao \
    TTS_CUSTOM_VOICES_DIR=/workspace/custom_voices \
    TTS_VOICES_DB=/workspace/custom_voices/voices.db \
    TTS_MAX_CONCURRENCY=2 \
    TTS_CORS_ORIGINS=* \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1
# ตั้ง TTS_API_KEY หรือ TTS_CREDITS_DB ตอน run (อย่า bake key ลง image)

# ฝังโมเดลทั้งหมดไว้ใน image (OmniVoice, VoxCPM2, faster-whisper ของ ASR verify) สำหรับ Serverless
ARG BAKE_MODELS=0
COPY scripts/bake_models.py /app/scripts/bake_models.py
RUN if [ "$BAKE_MODELS" = "1" ]; then python /app/scripts/bake_models.py; fi

# โครงสร้างเหมือน host (server.py หาไฟล์แบบ __file__-relative)
COPY core/ /app/core/
COPY scripts/ /app/scripts/
COPY data/ /app/data/
COPY voices/ /app/voices/
COPY voices_lao/ /app/voices_lao/
COPY voices_hero/ /app/voices_hero/
RUN sed -i 's/\r$//' /app/scripts/*.sh && chmod +x /app/scripts/docker_entrypoint.sh

# ทดสอบ import ทุกโมดูลที่ server/handler ใช้ตอน build — dependency พังให้ CI ล้มตรงนี้
# (เคยพังบน RunPod Serverless: transformers import GenerationMixin ไม่ผ่านกับ torch 2.5.1)
RUN cd /app/core && python -c "import transformers; from transformers import AutoFeatureExtractor, GenerationMixin; \
import omnivoice, voxcpm, faster_whisper, runpod, nara_wpe, server, hybrid_engine, voxcpm2_engine, dereverb; \
print('import check ok: torch', __import__('torch').__version__, 'transformers', transformers.__version__)"

EXPOSE 8000
# 1 process: โมเดลกิน VRAM และ generate ทีละงานอยู่แล้ว — สเกลด้วยการเพิ่ม Pod
ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
