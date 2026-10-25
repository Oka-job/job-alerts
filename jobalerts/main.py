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

    raw_alert = [j for j in raw if j.get("from_alert")]
    raw_main = [j for j in raw if not j.get("from_alert")]
    candidates, drops = pipeline.run_filters(raw_main, cfg, now, seen)
    picked, flagged = pipeline.select(candidates, cfg)
    print(f"{len(raw)} fetched, {len(candidates)} passed filters, {len(picked)} selected, {len(flagged)} flagged suspicious")
    for reason, n in drops.most_common():
        print(f"   dropped {n:>4}: {reason}")

    misses = getattr(drops, "misses", [])
    if misses:
        print(f"Jobs with a title in his field that were still dropped ({len(misses)}), first 25:")
        for reason, title, company, src in misses[:25]:
            print(f"   - {title} | {company} | {src} | {reason}")
    if candidates:
        print("Best candidates that passed the filters (score before the 50% cut-off):")
        for j in sorted(candidates, key=lambda x: -x["score"])[:10]:
            print(f"   - {j['score']}% | {j['title']} | {j.get('company') or '?'} | {j['source']}")
    # Jobs from alert emails: same filters, but they wait in a queue and go out 10 a day in a separate message.
    blocked = set(seen) | {j["key"] for j in picked + flagged}
    a_cands, a_drops = pipeline.run_filters(raw_alert, cfg, now, blocked)
    a_good = [j for j in a_cands if not j["suspicious"] and j["score"] >= cfg.get("strict_min_score", 50)]
    queue = pipeline.load_queue(a.data_dir) if not a.sample else []
    queue = pipeline.update_queue(queue, a_good, blocked, today, cfg.get("alert_queue_max_days", 21))
    queue = [q for q in queue if q["key"] not in blocked]
    a_picked, queue_left = pipeline.pick_from_queue(queue, cfg, now)
    print(f"Alert emails: {len(raw_alert)} jobs read, {len(a_good)} passed filters, "
          f"{len(a_picked)} going out today, {len(queue_left)} waiting for later days")
    for reason, n in a_drops.most_common(8):
        print(f"   alert dropped {n:>4}: {reason}")
    a_msg = None
    if a_picked:
        note = f"{len(queue_left)} more waiting for the next days." if queue_left else ""
        a_msg = notify.build(a_picked, [], {}, date_label, os.environ.get("TRACKER_URL", ""),
                             heading="Jobs from your alert emails", note=note)

    msg = notify.build(picked, flagged, health, date_label, os.environ.get("TRACKER_URL", ""))
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "preview.txt"), "w", encoding="utf-8") as f:
        f.write(msg["text"])
    with open(os.path.join(a.out_dir, "preview.html"), "w", encoding="utf-8") as f:
        f.write(msg["html"])
    with open(os.path.join(a.out_dir, "preview_telegram.html"), "w", encoding="utf-8") as f:
        f.write("\n\n-----\n\n".join(msg["telegram"]))

    if a_msg:
        with open(os.path.join(a.out_dir, "preview_alerts.txt"), "w", encoding="utf-8") as f:
            f.write(a_msg["text"])

    if a.dry_run or a.sample:
        print(f"Dry run: preview written to {a.out_dir}/ ; nothing sent, nothing saved.")
        return 0

    print("Sending...")
    ok_tg = notify.send_telegram(msg["telegram"])
    ok_em = notify.send_email(msg["subject"], msg["html"], msg["text"])
    ok_a = False
    if a_msg:
        ok_a = notify.send_telegram(a_msg["telegram"])
        ok_a = notify.send_email(a_msg["subject"], a_msg["html"], a_msg["text"]) or ok_a
    if ok_tg or ok_em or ok_a:
        sent = (picked + flagged) if (ok_tg or ok_em) else []
        if ok_a:
            sent = sent + a_picked
        else:
            queue_left = queue          # nothing delivered: keep them queued
        pipeline.save_state(a.data_dir, seen, history, sent, today)
        pipeline.save_queue(a.data_dir, queue_left)
        print("Saved history for the tracker.")
        return 0
    print("Nothing was delivered, so history was NOT saved (the same jobs will be tried again tomorrow).")
    return 1 if (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("EMAIL_USER")) else 0


if __name__ == "__main__":
    sys.exit(main())
