"""ทดสอบ RunPod Serverless endpoint ของ Hero Voice (ส่งงานผ่าน /run แล้วถาม /status จนเสร็จ)

ใช้ (PowerShell):
  $env:RUNPOD_API_KEY = "<key>"
  venv\\Scripts\\python scripts\\runpod_serverless_test.py <ENDPOINT_ID>                 # ประโยคสั้น + สารคดี 3 นาที
  venv\\Scripts\\python scripts\\runpod_serverless_test.py <ENDPOINT_ID> --short         # เฉพาะประโยคสั้น

ผลบันทึกที่ ab_output/serverless/ (mp3) — งานแรกหลังว่างนานจะรวม cold start (worker เริ่ม + โหลดโมเดล)
"""
import argparse, base64, os, sys, time

import requests

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_BASE, "ab_output", "serverless")


def run_job(endpoint, key, payload, name):
    h = {"Authorization": f"Bearer {key}"}
    api = f"https://api.runpod.ai/v2/{endpoint}"
    t0 = time.time()
    r = requests.post(f"{api}/run", json={"input": payload}, headers=h, timeout=60)
    r.raise_for_status()
    job = r.json()["id"]
    print(f"[{name}] job {job} queued", flush=True)
    last = None
    while True:
        s = requests.get(f"{api}/status/{job}", headers=h, timeout=60).json()
        st = s.get("status")
        if st != last:
            print(f"[{name}] {st} ({time.time()-t0:.0f}s)", flush=True); last = st
        if st in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            break
        time.sleep(3)
    out = s.get("output") or {}
    if st != "COMPLETED" or "error" in out:
        print(f"[{name}] ล้มเหลว: {out.get('error') or s}")
        return False
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{name}.{out['format']}")
    open(path, "wb").write(base64.b64decode(out["audio_base64"]))
    print(f"[{name}] เสร็จ {time.time()-t0:.0f}s (รอคิว {s.get('delayTime', 0)/1000:.0f}s, รัน {s.get('executionTime', 0)/1000:.0f}s) "
          f"engine={out['engine']} {out['duration']}s {out['format']} {out['sample_rate']}Hz -> {path}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("endpoint"); ap.add_argument("--short", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("RUNPOD_API_KEY") or sys.exit('ตั้งก่อน: $env:RUNPOD_API_KEY = "<key>"')
    ok = run_job(a.endpoint, key, {"text": "สวัสดีค่ะ ยินดีต้อนรับเข้าสู่บริการฮีโร่วอยซ์ วันนี้มีโปรโมชั่นลด 20 เปอร์เซ็นต์ โทร 02 123 4567 ค่ะ",
                                   "voice_id": "voice_02"}, "short_voice02")
    if ok and not a.short:
        text = "\n".join(l.strip() for l in open(os.path.join(_BASE, "data", "scripts", "kafae_doi.txt"), encoding="utf-8") if l.strip())
        run_job(a.endpoint, key, {"text": text, "voice_id": "voice_02"}, "kafae_doi_3min")


if __name__ == "__main__":
    main()
