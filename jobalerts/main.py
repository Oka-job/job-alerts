"""Daily entry point:  python -m jobalerts.main [--dry-run] [--sample]"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import notify, pipeline, sources
from .util import load_config

IST = timezone(timedelta(hours=5, minutes=30))


def load_sample(path, now):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    jobs = []
    for r in raw:
        r = dict(r)
        days = r.pop("days_ago", None)
        r["posted"] = None if days is None else now - timedelta(days=days)
        jobs.append(sources.mk(**r))
    return jobs, {"sample": f"{len(jobs)} jobs"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build the message but send nothing and save nothing")
    ap.add_argument("--sample", action="store_true", help="use built-in sample listings instead of the internet")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--data-dir", default="docs/data")
    ap.add_argument("--out-dir", default="out")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date().isoformat()
    date_label = now.astimezone(IST).strftime("%a %d %b")

    print("Fetching jobs...")
    if a.sample:
        raw, health = load_sample(os.path.join(os.path.dirname(__file__), "..", "tests", "sample_jobs.json"), now)
        seen, history = {}, []
    else:
        raw, health = sources.collect(cfg)
        seen, history = pipeline.load_state(a.data_dir)

    candidates, drops = pipeline.run_filters(raw, cfg, now, seen)
    picked, flagged = pipeline.select(candidates, cfg)
    print(f"{len(raw)} fetched, {len(candidates)} passed filters, {len(picked)} selected, {len(flagged)} flagged suspicious")
    for reason, n in drops.most_common():
        print(f"   dropped {n:>4}: {reason}")

    msg = notify.build(picked, flagged, health, date_label, os.environ.get("TRACKER_URL", ""))
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "preview.txt"), "w", encoding="utf-8") as f:
        f.write(msg["text"])
    with open(os.path.join(a.out_dir, "preview.html"), "w", encoding="utf-8") as f:
        f.write(msg["html"])
    with open(os.path.join(a.out_dir, "preview_telegram.html"), "w", encoding="utf-8") as f:
        f.write("\n\n-----\n\n".join(msg["telegram"]))

    if a.dry_run or a.sample:
        print(f"Dry run: preview written to {a.out_dir}/ ; nothing sent, nothing saved.")
        return 0

    print("Sending...")
    ok_tg = notify.send_telegram(msg["telegram"])
    ok_em = notify.send_email(msg["subject"], msg["html"], msg["text"])
    if ok_tg or ok_em:
        pipeline.save_state(a.data_dir, seen, history, picked + flagged, today)
        print("Saved history for the tracker.")
        return 0
    print("Nothing was delivered, so history was NOT saved (the same jobs will be tried again tomorrow).")
    return 1 if (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("EMAIL_USER")) else 0


if __name__ == "__main__":
    sys.exit(main())
