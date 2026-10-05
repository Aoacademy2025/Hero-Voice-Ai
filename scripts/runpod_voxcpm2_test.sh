#!/bin/bash
# ทดสอบความเร็ว VoxCPM2 บน RunPod (ระยะ 1 — ยังไม่เสียบเข้า server)
#
# บน Pod (template "RunPod PyTorch 2.x", GPU 16GB+):
#   cd /workspace && unzip -o voxcpm2_test.zip -d voxcpm2_test && cd voxcpm2_test
#   bash scripts/runpod_voxcpm2_test.sh
# ผลอยู่ใน ab_output/voxcpm2/ (wav + log) และสรุปเวลาท้ายจอ — zip กลับมาฟังได้ด้วย:
#   zip -r results.zip ab_output/voxcpm2 && ls -la results.zip
set -e
cd "$(dirname "$0")/.."
echo "== GPU =="; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"

echo "== install voxcpm (no heavy deps: funasr/modelscope/gradio ไม่ได้ใช้) =="
pip install -q --no-deps voxcpm==2.0.3
pip install -q "transformers>=4.45" pydantic tqdm inflect wetext addict simplejson sortedcontainers einops soundfile librosa safetensors "huggingface_hub[cli]"

echo "== download openbmb/VoxCPM2 (~4.6GB) =="
python - <<'PY'
from huggingface_hub import snapshot_download
p = snapshot_download("openbmb/VoxCPM2"); print("model at", p)
PY

export PYTHONIOENCODING=utf-8
mkdir -p ab_output/voxcpm2
echo "== 1) short samples (design/clone/lao) =="
python -u scripts/try_voxcpm2.py cuda 2>&1 | tee ab_output/voxcpm2/full_short.log | grep -E "^load:|: gen |Traceback|Error" | tee ab_output/voxcpm2/run_short.log
echo "== 2) 3-minute documentary, 19 chunks =="
python -u scripts/try_voxcpm2_long.py data/scripts/kafae_doi.txt kafae_doi_voice02.wav 2>&1 | tee ab_output/voxcpm2/full_kafae.log | grep -E "^load:|^chunk|Traceback|Error|^done" | tee ab_output/voxcpm2/run_kafae.log
echo "== 2b) same documentary: join 2 lines/chunk, 16 steps, continuation prompting =="
python -u scripts/try_voxcpm2_long.py data/scripts/kafae_doi.txt kafae_doi_cont.wav --join 2 --steps 16 --continue 2>&1 | tee ab_output/voxcpm2/full_cont.log | grep -E "^load:|^chunk|Traceback|Error|^done" | tee ab_output/voxcpm2/run_cont.log
echo "== 3) 8 stock voices + 3 designs =="
python -u scripts/try_voxcpm2_voices.py 2>&1 | tee ab_output/voxcpm2/full_voices.log | grep -E "^load:|: gen |Traceback|Error" | tee ab_output/voxcpm2/run_voices.log

echo
echo "================= SUMMARY ================="
nvidia-smi --query-gpu=name --format=csv,noheader
grep -h "^load:" ab_output/voxcpm2/run_*.log | head -1
grep -h "^done" ab_output/voxcpm2/run_kafae.log ab_output/voxcpm2/run_cont.log
python - <<'PY'
import re
g=a=0.0
for l in open("ab_output/voxcpm2/run_kafae.log", encoding="utf-8"):
    m=re.search(r"gen ([\d.]+)s, audio ([\d.]+)s", l)
    if m: g+=float(m[1]); a+=float(m[2])
if a: print(f"documentary RTF = {g/a:.2f}  (laptop RTX3060 6GB was 12.6)")
PY
echo "==========================================="
echo "เสร็จแล้ว อย่าลืม Stop/Terminate Pod"
