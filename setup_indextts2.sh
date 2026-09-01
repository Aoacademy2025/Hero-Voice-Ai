#!/usr/bin/env bash
# setup_indextts2.sh — เตรียม IndexTTS-2 (cloning เหมือนสูง + อารมณ์จริง) แบบแยก venv
# ไม่กระทบ venv/ ของ OmniVoice หลักเลย (ปลอดภัย 100% ต่อ production)
#
# รวมทุก fix ที่เจอมาจากรอบทดลองก่อนหน้า:
#   1. ใช้ --system-site-packages เกาะ torch ที่มีอยู่แล้วในระบบ (กันโหลด torch ซ้ำ ~3.5GB)
#   2. ติดตั้ง indextts แบบ --no-deps แล้วเลือกติดตั้ง dependency เอง (ข้าม torch/torchaudio)
#   3. แพตช์ model_download.py ให้ข้าม conformer_shaw.pt (fairseq format ไม่ได้ใช้จริง) ประหยัด 2.3GB
#
# ก่อนรัน: ต้อง `nvidia-smi` ใช้ได้ปกติก่อน (เช็ค GPU driver ไม่ค้าง) และดิสก์เหลืออย่างน้อย ~10GB
#
# ใช้:
#   bash setup_indextts2.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== เช็ค GPU driver =="
nvidia-smi --query-gpu=name,memory.free --format=csv,noheader || {
  echo "!! nvidia-smi ใช้ไม่ได้ — GPU driver อาจค้างอยู่ ต้องรีบูตเครื่องก่อน"; exit 1; }

echo "== เช็คดิสก์ (ต้องการอย่างน้อย ~10GB) =="
df -h . | tail -1

echo "== สร้าง venv_indextts (เกาะ torch จากระบบ ไม่โหลดซ้ำ) =="
venv/Scripts/python.exe -m venv --system-site-packages venv_indextts

echo "== เช็คว่า torch มองเห็นและใช้ CUDA ได้ =="
venv_indextts/Scripts/python.exe -c "import torch; print('torch', torch.__version__, 'cuda:', torch.cuda.is_available())"

echo "== clone ซอร์ส indextts (แค่โค้ด ไม่ใช่ checkpoint) =="
rm -rf /tmp/index-tts-src
git clone --depth 1 https://github.com/index-tts/index-tts.git /tmp/index-tts-src

echo "== ติดตั้ง indextts package (--no-deps กันดึง torch==2.8.* ที่ไม่มี) =="
venv_indextts/Scripts/pip.exe install --no-deps /tmp/index-tts-src

echo "== ติดตั้ง dependency ที่เหลือ (ข้าม torch/torchaudio โดยตั้งใจ) =="
venv_indextts/Scripts/pip.exe install \
  "accelerate==1.8.1" "cn2an==0.5.22" "cython==3.0.7" "descript-audiotools==0.7.2" \
  "einops>=0.8.1" "ffmpeg-python==0.2.0" "fugashi>=1.2.0" "unidic-lite>=1.0.0" \
  "g2p-en==2.1.0" "jieba==0.42.1" "json5==0.10.0" "keras==2.9.0" \
  "librosa==0.10.2.post1" "matplotlib==3.10.0" "modelscope==1.27.0" "munch==4.0.0" \
  "numba==0.63.0" "numpy==2.2.6" "omegaconf>=2.3.0" "opencv-python==4.9.0.80" \
  "pandas==2.3.2" "safetensors==0.5.2" "sentencepiece>=0.2.1" "tensorboard==2.20.0" \
  "textstat>=0.7.10" "tokenizers==0.21.0" "requests>=2.28" "tqdm>=4.67.1" \
  "transformers==4.52.1" "openai-whisper>=20231117" "wetext>=0.0.9"

echo "== แพตช์ model_download.py ให้ข้าม conformer_shaw.pt (ประหยัด 2.3GB) =="
DL_PY="venv_indextts/lib/site-packages/indextts/utils/model_download.py"
if ! grep -q "allow_patterns" "$DL_PY"; then
  python - "$DL_PY" <<'PYEOF'
import sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
old = 'snapshot_download("facebook/w2v-bert-2.0", local_dir=w2v_dir)'
new = ('snapshot_download("facebook/w2v-bert-2.0", local_dir=w2v_dir,\n'
       '                               allow_patterns=["*.json", "*.safetensors", "*.txt"])')
if old not in content:
    sys.exit("!! ไม่เจอบรรทัดที่จะแพตช์ — เวอร์ชัน indextts อาจเปลี่ยนโครงสร้าง ข้ามการแพตช์นี้ไปก่อน")
content = content.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("แพตช์สำเร็จ")
PYEOF
else
  echo "แพตช์ไว้แล้ว ข้าม"
fi

echo "== ดาวน์โหลด checkpoint หลัก (~5.9GB) จาก Hugging Face =="
venv_indextts/Scripts/python.exe -c "
from huggingface_hub import snapshot_download
snapshot_download('IndexTeam/IndexTTS-2', local_dir='indextts2_model')
print('checkpoint พร้อมแล้ว')
"

echo ""
echo "== เสร็จแล้ว — ทดสอบด้วย: =="
echo "  cd $(pwd)"
echo "  PYTHONUNBUFFERED=1 venv_indextts/Scripts/python.exe -u test_indextts_emotion.py"
