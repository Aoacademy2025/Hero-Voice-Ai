"""Deploy Hero Voice TTS server ขึ้น RunPod Pod ในคำสั่งเดียว (ไม่ต้อง build Docker)

ทำอะไร: สร้าง Pod (เปิดพอร์ต 8000 ผ่าน RunPod proxy + SSH) → อัปโหลดโค้ด (zip ~10MB) →
รัน scripts/runpod_server_setup.sh บน Pod (ติดตั้ง + โหลดโมเดลจาก HF + รัน server) →
พิมพ์ URL ใช้งาน. Pod จะ "รันค้าง" จนกว่าจะสั่งปิด (ค่าใช้จ่ายเดินตลอด)

ใช้ (PowerShell):
  $env:RUNPOD_API_KEY = "<key>"
  venv\Scripts\python scripts\runpod_deploy_server.py                       # deploy ใหม่
  venv\Scripts\python scripts\runpod_deploy_server.py --volume <NETWORK_VOLUME_ID>   # เก็บโมเดล/เสียงโคลน/เครดิตถาวร
  venv\Scripts\python scripts\runpod_deploy_server.py --update POD_ID       # อัปโหลดโค้ดใหม่ + restart server บน Pod เดิม
  venv\Scripts\python scripts\runpod_deploy_server.py --status              # รายการ Pod + URL
  venv\Scripts\python scripts\runpod_deploy_server.py --stop POD_ID         # หยุดชั่วคราว (เก็บ disk, ค่า GPU หยุด)
  venv\Scripts\python scripts\runpod_deploy_server.py --terminate POD_ID    # ลบทิ้ง

ตัวเลือก: --gpu "<gpu_type_id>"  --tts-key <X-API-Key ของ server>  --engines omnivoice,voxcpm2
         --env KEY=VAL (ซ้ำได้) ส่ง TTS_* อื่นๆ เช่น --env TTS_CREDITS_DB=/workspace/credits.db
"""
import argparse, os, secrets, subprocess, sys, time, zipfile

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(_BASE, "herovoice_code.zip")
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
IMAGE = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"
GPU_CANDIDATES = ["NVIDIA GeForce RTX 3090", "NVIDIA RTX A5000", "NVIDIA GeForce RTX 4090",
                  "NVIDIA RTX A6000", "NVIDIA A40", "NVIDIA L4"]
SSH_OPTS = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15"]
REMOTE_DIR = "/workspace/herovoice"
# โค้ดที่ต้องส่งขึ้น Pod (ไม่เอา model/ — โหลดจาก HF บน Pod, ไม่เอา OmniVoice/.git tests docs)
INCLUDE_DIRS = ["core", "scripts", "data", "voices", "voices_lao"]
INCLUDE_FILES = ["requirements.txt"]
OMNIVOICE_SKIP = {".git", "tests", "docs", "examples", "uv.lock", ".env"}


def build_zip():
    n = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        def add(full):
            nonlocal n
            rel = os.path.relpath(full, _BASE).replace("\\", "/")
            if "__pycache__" in rel or rel.endswith((".pyc", ".bak")):
                return
            data = open(full, "rb").read()
            if rel.endswith((".sh", ".py", ".txt", ".json", ".tsv", ".md", ".html")):
                data = data.replace(b"\r\n", b"\n")
            z.writestr(rel, data); n += 1
        for d in INCLUDE_DIRS:
            for root, _, files in os.walk(os.path.join(_BASE, d)):
                for f in files:
                    add(os.path.join(root, f))
        for f in INCLUDE_FILES:
            add(os.path.join(_BASE, f))
        ov = os.path.join(_BASE, "OmniVoice")
        for root, dirs, files in os.walk(ov):
            dirs[:] = [x for x in dirs if x not in OMNIVOICE_SKIP]
            for f in files:
                if f not in OMNIVOICE_SKIP:
                    add(os.path.join(root, f))
    print(f"[zip] {n} files -> {ZIP} ({os.path.getsize(ZIP)//1024} KB)")


def ssh(ip, port, cmd, stream=False):
    full = ["ssh", *SSH_OPTS, "-p", str(port), f"root@{ip}", cmd]
    if stream:
        return subprocess.run(full).returncode
    r = subprocess.run(full, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ssh cmd failed ({r.returncode}): {cmd} :: " + r.stderr.strip()[-800:])
    return r.stdout


def scp(src, dst, port):
    subprocess.run(["scp", *SSH_OPTS, "-P", str(port), src, dst], check=True)


def pod_url(pod_id):
    return f"https://{pod_id}-8000.proxy.runpod.net"


def ssh_endpoint(runpod, pod_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pod = runpod.get_pod(pod_id)
        for p in (pod.get("runtime") or {}).get("ports") or []:
            if p.get("privatePort") == 22 and p.get("isIpPublic"):
                ip, port = p["ip"], p["publicPort"]
                r = subprocess.run(["ssh", *SSH_OPTS, "-p", str(port), f"root@{ip}", "echo ok"],
                                   capture_output=True, text=True)
                if r.stdout.strip() == "ok":
                    print(f"[ssh] root@{ip}:{port} ({time.time()-t0:.0f}s)")
                    return ip, port
        time.sleep(10); print(f"[pod] waiting... {time.time()-t0:.0f}s", flush=True)
    raise TimeoutError("Pod ไม่พร้อม SSH ภายใน 10 นาที")


def create_pod(runpod, gpu, volume_id, image=None, env=None):
    pubkey = open(SSH_KEY + ".pub", encoding="utf-8").read().strip()
    last = None
    for g in ([gpu] if gpu else GPU_CANDIDATES):
        try:
            if image:  # Docker image ของเราเอง (ไม่มี sshd) — ตั้งค่าทั้งหมดผ่าน env ตอนสร้าง Pod
                kw = dict(name="herovoice-tts", image_name=image, gpu_type_id=g, cloud_type="ALL",
                          container_disk_in_gb=30, ports="8000/http", env=env or {},
                          support_public_ip=True, start_ssh=False)
                if not volume_id:  # โมเดล ~8GB โหลดลง /workspace ตอนสตาร์ท — ให้มี pod volume รองรับ
                    kw.update(volume_in_gb=30, volume_mount_path="/workspace")
            else:
                kw = dict(name="herovoice-tts", image_name=IMAGE, gpu_type_id=g, cloud_type="ALL",
                          container_disk_in_gb=40, ports="8000/http,22/tcp",
                          env={"PUBLIC_KEY": pubkey}, support_public_ip=True, start_ssh=True)
            if volume_id:
                kw.update(network_volume_id=volume_id, volume_mount_path="/workspace")
            pod = runpod.create_pod(**kw)
            print(f"[pod] created {pod['id']} on {g}")
            return pod["id"]
        except Exception as e:
            last = e; print(f"[pod] {g}: {str(e)[:120]}")
    sys.exit(f"สร้าง Pod ไม่ได้: {last}")


def deploy(ip, port, tts_key, engines, extra_env):
    print("[upload] herovoice_code.zip")
    scp(ZIP, "root@%s:/workspace/herovoice_code.zip" % ip, port)
    ssh(ip, port, f"mkdir -p {REMOTE_DIR} && cd {REMOTE_DIR} && rm -rf core scripts data voices voices_lao OmniVoice "
                  f"&& python3 -m zipfile -e /workspace/herovoice_code.zip . && ls core/server.py")
    env = {"TTS_API_KEY": tts_key, "TTS_ENGINES": engines, **extra_env}
    exports = " ".join(f"{k}='{v}'" for k, v in env.items())
    print("[setup] ติดตั้ง + โหลดโมเดล + รัน server (log สดด้านล่าง)\n" + "-" * 60)
    rc = ssh(ip, port, f"cd {REMOTE_DIR} && {exports} bash scripts/runpod_server_setup.sh 2>&1 | tee logs_setup.log",
             stream=True)
    print("-" * 60)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu"); ap.add_argument("--volume", help="Network Volume id (mount /workspace)")
    ap.add_argument("--tts-key", help="X-API-Key ของ server (ดีฟอลต์สุ่มให้)")
    ap.add_argument("--engines", default="omnivoice,voxcpm2", help="ดีฟอลต์เปิดทั้งสองเอนจิน (GPU 16GB+)")
    ap.add_argument("--image", help="Docker image ที่ build แล้ว เช่น ghcr.io/aoacademy2025/hero-voice-ai:latest "
                                    "(ไม่อัปโหลดโค้ด/ไม่ติดตั้ง — โมเดลโหลดตอนสตาร์ทลง /workspace)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VAL เพิ่ม (ซ้ำได้)")
    ap.add_argument("--update", metavar="POD_ID", help="อัปโหลดโค้ดใหม่ + restart บน Pod เดิม")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", metavar="POD_ID"); ap.add_argument("--terminate", metavar="POD_ID")
    a = ap.parse_args()

    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        sys.exit('ไม่พบ RUNPOD_API_KEY — ตั้งก่อน: $env:RUNPOD_API_KEY = "<key>"')
    import runpod
    runpod.api_key = key

    if a.status:
        for p in runpod.get_pods():
            print(f"{p['id']}  {p['name']:<16} {p['desiredStatus']:<8} {p.get('machine', {}).get('gpuDisplayName', '')}"
                  f"  {pod_url(p['id']) if p['desiredStatus'] == 'RUNNING' else ''}")
        return
    if a.stop:
        runpod.stop_pod(a.stop); print(f"[pod] stopped {a.stop} (resume ได้จากเว็บ)"); return
    if a.terminate:
        runpod.terminate_pod(a.terminate); print(f"[pod] terminated {a.terminate}"); return

    tts_key = a.tts_key or ("sk_" + secrets.token_urlsafe(24))
    extra_env = dict(e.split("=", 1) for e in a.env)
    if a.image:
        env = {"TTS_API_KEY": tts_key, "TTS_ENGINES": a.engines, **extra_env}
        pod_id = create_pod(runpod, a.gpu, a.volume, image=a.image, env=env)
        url = pod_url(pod_id)
        print(f"[pod] รอ server (pull image + โหลดโมเดลครั้งแรก ~5-15 นาที) {url}/health")
        import urllib.request
        t0 = time.time()
        while time.time() - t0 < 1800:
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=10) as r:
                    if b'"ok"' in r.read():
                        print(f"\nพร้อมใช้งาน ({time.time()-t0:.0f}s)\n  Studio/API : {url}\n  Swagger    : {url}/docs"
                              f"\n  X-API-Key  : {tts_key}\n  Pod id     : {pod_id}")
                        return
            except Exception:
                pass
            time.sleep(15); print(f"[pod] waiting... {time.time()-t0:.0f}s", flush=True)
        print(f"server ไม่ขึ้นใน 30 นาที — ดู log ที่ runpod.io/console/pods (Pod {pod_id}) แล้ว --terminate ถ้าไม่ใช้")
        return
    build_zip()
    pod_id = a.update or create_pod(runpod, a.gpu, a.volume)
    ip, port = ssh_endpoint(runpod, pod_id)
    rc = deploy(ip, port, tts_key, a.engines, extra_env)
    url = pod_url(pod_id)
    if rc == 0:
        print(f"\nพร้อมใช้งาน\n  Studio/API : {url}\n  Swagger    : {url}/docs\n  X-API-Key  : {tts_key}\n  Pod id     : {pod_id}")
        print(f"\nทดสอบ: curl {url}/health\nหยุด:   venv\\Scripts\\python scripts\\runpod_deploy_server.py --stop {pod_id}")
    else:
        print(f"\nsetup ล้มเหลว (exit {rc}) — Pod {pod_id} ยังรันอยู่ ดู log: ssh root@{ip} -p {port} 'tail -50 {REMOTE_DIR}/logs/server.log'")
        print(f"ปิดถ้าไม่ใช้: --terminate {pod_id}")


if __name__ == "__main__":
    main()
