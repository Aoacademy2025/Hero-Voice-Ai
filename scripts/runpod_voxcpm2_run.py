"""รันทดสอบ VoxCPM2 บน RunPod อัตโนมัติจบในคำสั่งเดียว (ระยะ 1 — วัดความเร็วบน GPU จริง)

ทำอะไรบ้าง: สร้าง Pod (GPU 24GB ตัวแรกที่ว่าง) → รอ SSH → อัปโหลด voxcpm2_test.zip →
รัน scripts/runpod_voxcpm2_test.sh บน Pod (แสดง log สดบนจอ) → ดึงผล (wav + log) กลับมาที่
ab_output/voxcpm2_runpod/ → Terminate Pod เสมอ (แม้ error) → พิมพ์ SUMMARY

ใช้ (PowerShell): ตั้ง API key ไว้ใน environment ก่อน แล้วรัน
  $env:RUNPOD_API_KEY = "<key>"
  venv\Scripts\python scripts\runpod_voxcpm2_run.py              # รันทั้งหมด
  venv\Scripts\python scripts\runpod_voxcpm2_run.py --gpu "NVIDIA GeForce RTX 4090"
  venv\Scripts\python scripts\runpod_voxcpm2_run.py --keep        # ไม่ Terminate ตอนจบ (ระวังค่าใช้จ่าย)
  venv\Scripts\python scripts\runpod_voxcpm2_run.py --terminate POD_ID   # ปิด Pod ที่ค้าง

SSH: ใช้ ~/.ssh/id_ed25519 ของเครื่องนี้ (ส่ง public key เข้า Pod ให้เองผ่าน env PUBLIC_KEY)
"""
import argparse, glob, os, subprocess, sys, time, zipfile

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(_BASE, "voxcpm2_test.zip")
RESULT_DIR = os.path.join(_BASE, "ab_output", "voxcpm2_runpod")
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
IMAGE = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"
GPU_CANDIDATES = ["NVIDIA GeForce RTX 3090", "NVIDIA RTX A5000", "NVIDIA GeForce RTX 4090",
                  "NVIDIA L4", "NVIDIA RTX A6000", "NVIDIA A40"]
SSH_OPTS = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15"]


def build_zip():
    files = ["scripts/runpod_voxcpm2_test.sh", "scripts/voxcpm2_compat.py", "scripts/stitch_chunks.py",
             "scripts/try_voxcpm2.py", "scripts/try_voxcpm2_long.py", "scripts/try_voxcpm2_voices.py",
             "data/scripts/kafae_doi.txt", "data/scripts/hero_promo.txt", "voices/voices.json"]
    files += sorted(glob.glob(os.path.join(_BASE, "voices", "*.wav")))
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            full = f if os.path.isabs(f) else os.path.join(_BASE, f)
            rel = os.path.relpath(full, _BASE).replace("\\", "/")
            data = open(full, "rb").read()
            if rel.endswith((".sh", ".py", ".txt")):
                data = data.replace(b"\r\n", b"\n")
            z.writestr(rel, data)
    print(f"[zip] {ZIP} ({os.path.getsize(ZIP)//1024} KB)")


def create_pod(runpod, gpu):
    pubkey = open(SSH_KEY + ".pub", encoding="utf-8").read().strip()
    gpus = [gpu] if gpu else GPU_CANDIDATES
    last = None
    for g in gpus:
        try:
            pod = runpod.create_pod(
                name="voxcpm2-test", image_name=IMAGE, gpu_type_id=g, cloud_type="ALL",
                container_disk_in_gb=30, volume_in_gb=0, ports="22/tcp",
                env={"PUBLIC_KEY": pubkey}, support_public_ip=True, start_ssh=True,
            )
            print(f"[pod] created {pod['id']} on {g}")
            return pod["id"]
        except Exception as e:  # GPU ชนิดนี้ไม่ว่าง → ลองตัวถัดไป
            last = e
            print(f"[pod] {g}: {str(e)[:120]}")
    sys.exit(f"สร้าง Pod ไม่ได้: {last}")


def wait_ssh(runpod, pod_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pod = runpod.get_pod(pod_id)
        rt = pod.get("runtime") or {}
        for p in rt.get("ports") or []:
            if p.get("privatePort") == 22 and p.get("isIpPublic"):
                ip, port = p["ip"], p["publicPort"]
                r = subprocess.run(["ssh", *SSH_OPTS, "-p", str(port), f"root@{ip}", "echo ok"],
                                   capture_output=True, text=True)
                if r.stdout.strip() == "ok":
                    print(f"[ssh] ready root@{ip}:{port} ({time.time()-t0:.0f}s)")
                    return ip, port
        time.sleep(10)
        print(f"[pod] waiting... {time.time()-t0:.0f}s", flush=True)
    raise TimeoutError("Pod ไม่พร้อม SSH ภายในเวลาที่กำหนด")


def ssh(ip, port, cmd, stream=False):
    """รันคำสั่งบน Pod — ถ้าไม่ stream จะ raise พร้อม stderr เมื่อคำสั่งล้มเหลว (กันล้มเงียบแล้วไปต่อ)"""
    full = ["ssh", *SSH_OPTS, "-p", str(port), f"root@{ip}", cmd]
    if stream:
        return subprocess.run(full).returncode
    r = subprocess.run(full, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ssh cmd failed ({r.returncode}): {cmd} :: " + r.stderr.strip()[-800:])
    return r.stdout


def scp(src, dst, port):
    subprocess.run(["scp", *SSH_OPTS, "-P", str(port), src, dst], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", help="gpu_type_id เจาะจง (ดีฟอลต์ลองตามลำดับ 3090/A5000/4090/L4/A6000/A40)")
    ap.add_argument("--keep", action="store_true", help="ไม่ Terminate Pod ตอนจบ")
    ap.add_argument("--terminate", metavar="POD_ID", help="แค่ Terminate Pod นี้แล้วจบ")
    a = ap.parse_args()

    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        sys.exit('ไม่พบ RUNPOD_API_KEY — ตั้งก่อน: $env:RUNPOD_API_KEY = "<key>"')
    import runpod
    runpod.api_key = key
    if a.terminate:
        runpod.terminate_pod(a.terminate); print(f"[pod] terminated {a.terminate}"); return

    build_zip()
    pod_id = create_pod(runpod, a.gpu)
    t_start = time.time()
    try:
        ip, port = wait_ssh(runpod, pod_id)
        print("[upload] voxcpm2_test.zip")
        scp(ZIP, f"root@{ip}:/workspace/voxcpm2_test.zip", port)
        ssh(ip, port, "cd /workspace && rm -rf voxcpm2_test && mkdir voxcpm2_test && "
            "python3 -m zipfile -e voxcpm2_test.zip voxcpm2_test && ls voxcpm2_test/scripts")
        print("[run] scripts/runpod_voxcpm2_test.sh (log สดด้านล่าง)\n" + "-" * 60)
        rc = ssh(ip, port, "cd /workspace/voxcpm2_test && bash scripts/runpod_voxcpm2_test.sh 2>&1 | tee run_all.log",
                 stream=True)
        print("-" * 60 + f"\n[run] exit {rc}")
        print("[download] results")
        os.makedirs(RESULT_DIR, exist_ok=True)
        ssh(ip, port, "cd /workspace/voxcpm2_test && python3 -c \"import shutil;shutil.make_archive('results','zip','.','ab_output')\" "
            "&& python3 -m zipfile -t results.zip >/dev/null && ls -la results.zip")
        # run_all.log อยู่นอก ab_output → ดึงแยก
        scp(f"root@{ip}:/workspace/voxcpm2_test/run_all.log", os.path.join(RESULT_DIR, "run_all.log"), port)
        local_zip = os.path.join(RESULT_DIR, "results.zip")
        scp(f"root@{ip}:/workspace/voxcpm2_test/results.zip", local_zip, port)
        with zipfile.ZipFile(local_zip) as z:
            z.extractall(RESULT_DIR)
        print(f"[download] -> {RESULT_DIR}")
    finally:
        if a.keep:
            print(f"[pod] --keep: Pod {pod_id} ยังรันอยู่! ปิดเองด้วย --terminate {pod_id}")
        else:
            try:
                runpod.terminate_pod(pod_id)
                print(f"[pod] terminated {pod_id} (ใช้ไป {(time.time()-t_start)/60:.1f} นาที)")
            except Exception as e:
                print(f"[pod] !! terminate ล้มเหลว: {e} — ไปปิดเองที่ runpod.io/console/pods (id {pod_id})")

    log = os.path.join(RESULT_DIR, "run_all.log")
    if os.path.exists(log):
        txt = open(log, encoding="utf-8", errors="replace").read()
        i = txt.find("================= SUMMARY")
        print("\n" + (txt[i:] if i >= 0 else txt[-1500:]))


if __name__ == "__main__":
    main()
