#!/bin/bash
# ติดตั้ง + รัน Hero Voice TTS server บน RunPod Pod (เรียกโดย scripts/runpod_deploy_server.py ผ่าน SSH
# หรือรันเองใน Web Terminal ของ Pod ก็ได้)
#
#   cd /workspace/herovoice && bash scripts/runpod_server_setup.sh
#
# env ที่รับ: TTS_API_KEY (จำเป็น), TTS_ENGINES (ดีฟอลต์ omnivoice), และ TTS_* อื่นๆ ส่งผ่านได้หมด
# ถ้า /workspace เป็น Network Volume: venv/โมเดล/HF cache อยู่บน volume → รอบหน้าไม่ต้องติดตั้ง/โหลดซ้ำ
set -e
cd "$(dirname "$0")/.."
APP=$(pwd)
WS=/workspace
export HF_HOME=${HF_HOME:-$WS/hf}
export TTS_MODEL_DIR=${TTS_MODEL_DIR:-$WS/model}
export TTS_CUSTOM_VOICES_DIR=${TTS_CUSTOM_VOICES_DIR:-$WS/custom_voices}
export TTS_VOICES_DB=${TTS_VOICES_DB:-$WS/custom_voices/voices.db}
export TTS_CORS_ORIGINS=${TTS_CORS_ORIGINS:-*}
export PYTHONIOENCODING=utf-8
[ -n "$TTS_API_KEY" ] || { echo "ต้องตั้ง TTS_API_KEY ก่อน"; exit 1; }
echo "$TTS_API_KEY" > "$WS/.tts_key"; chmod 600 "$WS/.tts_key"

echo "== GPU =="; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"

# ── ffmpeg/libsndfile (image PyTorch เปล่าไม่มี — pydub/librosa/audio_enhance ต้องใช้) ───────────
if ! command -v ffmpeg >/dev/null; then
  echo "== apt install ffmpeg =="
  apt-get update -qq >/dev/null && apt-get install -y -qq ffmpeg libsndfile1 >/dev/null
fi

# ── venv บน volume (สืบทอด torch จาก image ด้วย --system-site-packages) ─────────────────
if [ ! -x "$WS/venv/bin/python" ]; then
  echo "== create venv $WS/venv =="
  python -m venv --system-site-packages "$WS/venv"
fi
source "$WS/venv/bin/activate"

STAMP="$WS/venv/.installed-$(md5sum requirements.txt OmniVoice/pyproject.toml | md5sum | cut -c1-8)"
if [ ! -f "$STAMP" ]; then
  echo "== pip install (ครั้งแรก / requirements เปลี่ยน) =="
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  pip install -q --no-deps ./OmniVoice
  pip install -q accelerate pydub tensorboardX webdataset python-multipart huggingface_hub
  if [[ ",$TTS_ENGINES," == *",voxcpm2,"* ]]; then
    pip install -q --no-deps voxcpm==2.0.3
    pip install -q inflect wetext addict simplejson sortedcontainers einops
  fi
  touch "$STAMP"
else
  echo "== pip: ติดตั้งแล้ว ข้าม =="
fi

# ── โมเดล ───────────────────────────────────────────────────────────────────────────────
if [ ! -f "$TTS_MODEL_DIR/config.json" ]; then
  echo "== download k2-fsa/OmniVoice -> $TTS_MODEL_DIR =="
  python - <<'PY'
import os
from huggingface_hub import snapshot_download
snapshot_download("k2-fsa/OmniVoice", local_dir=os.environ["TTS_MODEL_DIR"])
PY
fi
if [[ ",$TTS_ENGINES," == *",voxcpm2,"* ]]; then
  python - <<'PY'
from huggingface_hub import snapshot_download
print("VoxCPM2 at", snapshot_download("openbmb/VoxCPM2"))
PY
fi

# ── รัน server (background, log ที่ $APP/logs/server.log) ────────────────────────────────
mkdir -p "$APP/logs" "$TTS_CUSTOM_VOICES_DIR"
pkill -f "core/server.py" 2>/dev/null || true
sleep 1
nohup python core/server.py > "$APP/logs/server.log" 2>&1 &
echo "server pid $!"

echo "== waiting for /health =="
for i in $(seq 1 120); do
  if curl -s -m 2 http://localhost:8000/health | grep -q '"ok"'; then
    curl -s http://localhost:8000/health; echo
    echo "READY"
    exit 0
  fi
  sleep 5
done
echo "server ไม่ขึ้นใน 10 นาที — log ท้าย:"; tail -40 "$APP/logs/server.log"; exit 1
