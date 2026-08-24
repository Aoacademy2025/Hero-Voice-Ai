"""
manage_keys.py — จัดการ API key + เครดิต (ใช้กับระบบเครดิต SQLite)

ต้องชี้ DB เดียวกับ server ผ่าน env TTS_CREDITS_DB

ตัวอย่าง:
  export TTS_CREDITS_DB=/data/credits.db
  python manage_keys.py create --name "ลูกค้า A" --credits 1000 --rate 60
  python manage_keys.py create --name admin --unlimited
  python manage_keys.py add   --key sk_xxx --credits 500
  python manage_keys.py list
"""
import argparse
import os
import sys

from credits import CreditStore


def main():
    db = os.environ.get("TTS_CREDITS_DB")
    if not db:
        sys.exit("ต้องตั้ง env TTS_CREDITS_DB ก่อน (path ไป credits.db)")
    store = CreditStore(db)

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="สร้าง key ใหม่")
    c.add_argument("--name", default="")
    c.add_argument("--credits", type=float, default=0.0)
    c.add_argument("--unlimited", action="store_true")
    c.add_argument("--rate", type=int, default=60, help="rate limit ต่อ นาที")

    a = sub.add_parser("add", help="เติมเครดิตให้ key")
    a.add_argument("--key", required=True)
    a.add_argument("--credits", type=float, required=True)

    sub.add_parser("list", help="ดู key ทั้งหมด")

    args = ap.parse_args()

    if args.cmd == "create":
        key = store.create_key(name=args.name, credits=args.credits,
                               unlimited=args.unlimited, rate_per_min=args.rate)
        print("สร้าง key สำเร็จ:")
        print("  ", key)
        print(f"  name={args.name!r} credits={args.credits} unlimited={args.unlimited} rate={args.rate}/min")
    elif args.cmd == "add":
        store.add_credits(args.key, args.credits)
        rec = store.get_key(args.key)
        print(f"เติมแล้ว — ยอดคงเหลือ: {rec['credits'] if rec else '??'}")
    elif args.cmd == "list":
        rows = store.list_keys()
        if not rows:
            print("(ยังไม่มี key)")
        for r in rows:
            u = "unlimited" if r["unlimited"] else f"{r['credits']:.2f} credits"
            act = "" if r["active"] else " [DISABLED]"
            print(f"{r['key']}  {u}  rate={r['rate_per_min']}/min  used={r['total_used']:.2f}  {r['name']!r}{act}")


if __name__ == "__main__":
    main()
