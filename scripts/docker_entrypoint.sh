#!/bin/bash
# entrypoint ของ Docker image: โหลดโมเดลที่ยังไม่มีลง /workspace แล้วรัน server
# (ครั้งแรก ~3-5 นาทีตามความเร็วเน็ตของเครื่อง, ครั้งต่อไปบน Network Volume เดิมข้ามได้ทันที)
set -e
cd /app
[ -n "$TTS_API_KEY" ] || [ -n "$TTS_CREDITS_DB" ] || echo "[entrypoint] คำเตือน: ไม่ได้ตั้ง TTS_API_KEY/TTS_CREDITS_DB — server จะสุ่ม key ให้ (ดูใน log)"
mkdir -p "$TTS_MODEL_DIR" "$HF_HOME" "$TTS_CUSTOM_VOICES_DIR"

if [ ! -f "$TTS_MODEL_DIR/config.json" ]; then
  echo "[entrypoint] download k2-fsa/OmniVoice -> $TTS_MODEL_DIR"
  python -c "import os; from huggingface_hub import snapshot_download as d; d('k2-fsa/OmniVoice', local_dir=os.environ['TTS_MODEL_DIR'])"
fi
if [[ ",$TTS_ENGINES," == *",voxcpm2,"* ]]; then
  echo "[entrypoint] ensure openbmb/VoxCPM2 in $HF_HOME"
  python -c "from huggingface_hub import snapshot_download as d; d('openbmb/VoxCPM2')"
fi

echo "[entrypoint] starting server (engines: $TTS_ENGINES)"
exec python core/server.py
