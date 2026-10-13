"""สร้าง RunPod Serverless endpoint ของ Hero Voice (template + endpoint) ด้วย API — ไม่ต้องกดในเว็บ

ใช้ (PowerShell):
  $env:RUNPOD_API_KEY = "<key>"
  venv\\Scripts\\python scripts\\runpod_serverless_deploy.py
  venv\\Scripts\\python scripts\\runpod_serverless_deploy.py --name heroai-hero-v2 --max-workers 3

ค่าเริ่มต้น: image :serverless (โมเดลฝังในตัว), GPU 24GB (3090/A5000/4090), active workers 0
(ไม่จ่ายตอนไม่มีคำขอ), idle 60s, FlashBoot เปิด
"""
import argparse, os, sys, time

IMAGE = "ghcr.io/aoacademy2025/hero-voice-ai:serverless"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="heroai-hero-omnivoice-voxcpm2")
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--gpus", default="AMPERE_24,ADA_24", help="RunPod GPU pool ids (คั่นด้วย ,)")
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--idle", type=int, default=60, help="วินาทีที่ worker รอคำขอถัดไปก่อนดับ")
    a = ap.parse_args()
    key = os.environ.get("RUNPOD_API_KEY") or sys.exit('ตั้งก่อน: $env:RUNPOD_API_KEY = "<key>"')
    import runpod
    runpod.api_key = key

    tpl_name = f"{a.name}-tpl-{time.strftime('%Y%m%d-%H%M')}"  # ชื่อ template ต้องไม่ซ้ำ
    tpl = runpod.create_template(name=tpl_name, image_name=a.image, container_disk_in_gb=30,
                                 is_serverless=True, env={"TTS_MODE": "serverless"})
    print(f"[template] {tpl['id']} {tpl_name} ({a.image})")
    ep = runpod.create_endpoint(name=a.name, template_id=tpl["id"], gpu_ids=a.gpus,
                                workers_min=0, workers_max=a.max_workers, idle_timeout=a.idle,
                                scaler_type="QUEUE_DELAY", scaler_value=4, flashboot=True)
    print(f"[endpoint] created {ep['id']} name={a.name} gpus={a.gpus} workers 0..{a.max_workers} idle {a.idle}s")
    print(f"\nEndpoint ID : {ep['id']}\n/run        : https://api.runpod.ai/v2/{ep['id']}/run"
          f"\n/status     : https://api.runpod.ai/v2/{ep['id']}/status/<job id>"
          f"\nทดสอบ       : venv\\Scripts\\python scripts\\runpod_serverless_test.py {ep['id']}")


if __name__ == "__main__":
    main()
