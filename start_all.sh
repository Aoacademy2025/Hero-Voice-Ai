#!/bin/bash
# start_all.sh — รัน Hero Voice TTS API (port 8000)
#
# วิธีใช้:
#   bash start_all.sh
#
# ตั้งค่า auth (เลือกอย่างใดอย่างหนึ่ง) ก่อนรัน:
#   export TTS_API_KEY="<key ของคุณ>"        # โหมด single key
#   export TTS_CREDITS_DB="/data/credits.db"  # โหมดเครดิต (หลาย key)
# ถ้าไม่ตั้ง TTS_API_KEY จะสุ่มให้ 1 อัน (dev) แล้วพิมพ์ออกมา
#
# หยุด: Ctrl+C (หรือ kill PID ที่พิมพ์ไว้)

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"

# ── auth: ใช้ค่าจาก env ถ้ามี ไม่งั้นสุ่มให้ (เฉพาะโหมด single key) ──
if [ -z "$TTS_CREDITS_DB" ]; then
  export TTS_API_KEY="${TTS_API_KEY:-$(python -c 'import secrets;print("sk_"+secrets.token_urlsafe(32))')}"
fi

(
  source "$REPO_DIR/venv/Scripts/activate"
  cd "$REPO_DIR"
  python core/server.py
) > "$LOG_DIR/server.log" 2>&1 &
PID=$!
echo "Hero Voice TTS -> http://localhost:8000  (PID $PID, log: logs/server.log)"

echo ""
if [ -n "$TTS_CREDITS_DB" ]; then
  echo "โหมดเครดิต: DB = $TTS_CREDITS_DB  (จัดการ key ด้วย manage_keys.py)"
else
  echo "X-API-Key: $TTS_API_KEY"
fi
echo ""
echo "รอโมเดลโหลด แล้วเช็คสถานะ:  curl http://localhost:8000/health"
echo "หยุด: kill $PID"

wait
