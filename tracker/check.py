"""One tracking run: search every date pair, record prices, send alerts.

Run from the repo root:  python -m tracker.check
"""

import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notify
from .search import make_client, search_with_retry

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
LATEST = ROOT / "docs" / "data" / "latest.json"
HISTORY = ROOT / "docs" / "data" / "history.json"
STATE = ROOT / "data" / "state.json"

TRACKS = {"best": "Cheapest (any airline)", "auh": "Via Abu Dhabi"}


def load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path: Path, obj, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        path.write_text(json.dumps(obj, separators=(",", ":")))
    else:
        path.write_text(json.dumps(obj, indent=2))


def date_pairs(cfg):
    today = date.today()
    d = date.fromisoformat(cfg["depart_from"])
    end = date.fromisoformat(cfg["depart_to"])
    while d <= end:
        if d > today:
            for n in range(cfg["return_after_days_min"], cfg["return_after_days_max"] + 1):
                yield d.isoformat(), (d + timedelta(days=n)).isoformat()
        d += timedelta(days=1)


def via(itin, code):
    return code in itin["path"][1:-1]


def fmt_itin(it):
    stops = " → ".join(it["path"])
    return f"€{it['price']} · {escape(', '.join(it['airlines']))} · {stops}"


def fmt_dates(c):
    d = datetime.fromisoformat(c["depart"]).strftime("%a %d %b")
    r = datetime.fromisoformat(c["return"]).strftime("%a %d %b")
    return f"{d} → {r}"


def run_searches(cfg, prev_latest):
    client = make_client()
    prev = {(c["depart"], c["return"]): c for c in prev_latest.get("combos", [])}
    combos, failures = [], []
    pairs = list(date_pairs(cfg))
    limit = int(os.environ.get("MAX_COMBOS", "0"))
    if limit:
        pairs = pairs[:limit]
    for i, (dep, ret) in enumerate(pairs):
        if i:
            time.sleep(random.uniform(2.5, 5.5))
        try:
            url, items = search_with_retry(client, cfg, dep, ret)
            auh_items = [it for it in items if via(it, cfg["watch_via"])]
            auh_url = url
            if not auh_items:
                time.sleep(random.uniform(2, 4))
                auh_url, auh_items = search_with_retry(client, cfg, dep, ret, via=cfg["watch_via"])
                auh_items = [it for it in auh_items if via(it, cfg["watch_via"])]
                # Google's top results can miss a cheaper connection the filtered search finds.
                items = sorted(items + auh_items, key=lambda i: i["price"])
            combos.append({
                "depart": dep,
                "return": ret,
                "best": items[0] if items else None,
                "auh": auh_items[0] if auh_items else None,
                "options": items[:5],
                "url": url,
                "auh_url": auh_url,
                "stale": False,
            })
            b = items[0]["price"] if items else None
            a = auh_items[0]["price"] if auh_items else None
            print(f"{dep} -> {ret}: best €{b}  via {cfg['watch_via']} €{a}")
        except Exception as e:
            print(f"FAILED {dep} -> {ret}: {e}", file=sys.stderr)
            failures.append(f"{dep}->{ret}")
            if (dep, ret) in prev:
                combos.append({**prev[(dep, ret)], "stale": True})
    return combos, failures


def track_min(combos, track):
    vals = [(c[track]["price"], c) for c in combos if c.get(track) and not c.get("stale")]
    return min(vals, key=lambda v: v[0]) if vals else (None, None)


def evaluate_alerts(cfg, state, runs, now_run, now_utc):
    """Return a list of (severity, line) alerts and update state in place."""
    alerts = []
    cutoff = (now_utc - timedelta(hours=24)).isoformat()
    for track, label in TRACKS.items():
        now = now_run.get(track)
        if now is None:
            continue
        past = [r[track] for r in runs if r.get(track) is not None]
        past24 = [r[track] for r in runs if r.get(track) is not None and r["t"] >= cutoff]
        tstate = state.setdefault("target", {}).setdefault(track, {})

        if now <= cfg["target_price"]:
            last_price = tstate.get("price")
            last_at = tstate.get("at")
            hours = (now_utc - datetime.fromisoformat(last_at)).total_seconds() / 3600 if last_at else None
            if last_price is None or now <= last_price - 5 or hours >= cfg["target_realert_hours"]:
                sev = 4 if now <= cfg["strong_target_price"] else 3
                alerts.append((sev, f"{label}: €{now} is below your €{cfg['target_price'] if sev == 3 else cfg['strong_target_price']} target"))
                tstate.update(price=now, at=now_utc.isoformat())
        else:
            tstate.clear()

        if past and now < min(past):
            alerts.append((2, f"{label}: new lowest price €{now} (previous low €{min(past)})"))
        elif past24 and now <= min(past24) - cfg["drop_alert_eur"]:
            alerts.append((1, f"{label}: dropped to €{now} (24h low was €{min(past24)})"))
    return alerts


HEADINGS = {4: "🚨 BUY NOW", 3: "✅ Below your target", 2: "📉 New lowest price", 1: "⬇️ Price drop"}


def snapshot_lines(cfg, combos, top=3):
    lines = []
    _, best = track_min(combos, "best")
    _, auh = track_min(combos, "auh")
    if best:
        lines.append(f"<b>Cheapest:</b> {fmt_itin(best['best'])}\n{fmt_dates(best)} · <a href=\"{best['url']}\">open</a>")
    if auh:
        lines.append(f"<b>Via {cfg['watch_via']}:</b> {fmt_itin(auh['auh'])}\n{fmt_dates(auh)} · <a href=\"{auh['auh_url']}\">open</a>")
    ranked = sorted((c for c in combos if c.get("best") and not c.get("stale")), key=lambda c: c["best"]["price"])
    if ranked:
        lines.append("<b>Top dates:</b>\n" + "\n".join(f"• {fmt_dates(c)}: €{c['best']['price']}" for c in ranked[:top]))
    dash = os.environ.get("DASHBOARD_URL")
    if dash:
        lines.append(f"<a href=\"{dash}\">Dashboard</a>")
    return lines


def main():
    cfg = load(CONFIG, None)
    prev_latest = load(LATEST, {})
    history = load(HISTORY, {"runs": []})
    state = load(STATE, {})
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    local = now_utc.astimezone(ZoneInfo(cfg["timezone"]))

    combos, failures = run_searches(cfg, prev_latest)
    fresh = [c for c in combos if not c.get("stale")]

    if not fresh:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        if state["consecutive_failures"] == 3:
            notify.send("⚠️ <b>Flight tracker</b>: the last 3 checks returned no prices. "
                        "Google may be blocking the runner; I'll keep retrying.")
        save(STATE, state)
        # Exit cleanly: Telegram already reports repeated failures, GitHub emails would just add noise.
        print("No fresh results this run.", file=sys.stderr)
        return 0

    if state.get("consecutive_failures", 0) >= 3:
        notify.send("✅ <b>Flight tracker</b> is getting prices again.")
    state["consecutive_failures"] = 0

    best_now, _ = track_min(combos, "best")
    auh_now, _ = track_min(combos, "auh")
    now_run = {
        "t": now_utc.isoformat(),
        "best": best_now,
        "auh": auh_now,
        "p": {f"{c['depart']}|{c['return']}": [
            c["best"]["price"] if c.get("best") else None,
            c["auh"]["price"] if c.get("auh") else None,
        ] for c in fresh},
    }
    runs = history["runs"]

    # Per-combo stats from history, for the dashboard.
    for c in combos:
        key = f"{c['depart']}|{c['return']}"
        seen = [r["p"][key][0] for r in runs if key in r.get("p", {}) and r["p"][key][0] is not None]
        prev_price = seen[-1] if seen else None
        if c.get("best"):
            seen_all = seen + [c["best"]["price"]]
            c["low"], c["high"] = min(seen_all), max(seen_all)
            c["change"] = c["best"]["price"] - prev_price if prev_price is not None else None

    # Alerts
    if not runs:
        notify.send("🛫 <b>Flight tracker started</b> — DUB ⇄ MAA, late Feb 2027\n\n" + "\n\n".join(snapshot_lines(cfg, combos)))
    else:
        alerts = evaluate_alerts(cfg, state, runs, now_run, now_utc)
        if alerts:
            top = max(s for s, _ in alerts)
            body = "\n".join(f"• {escape(line)}" for _, line in sorted(alerts, reverse=True))
            notify.send(f"<b>{HEADINGS[top]}</b> — DUB ⇄ MAA\n{body}\n\n" + "\n\n".join(snapshot_lines(cfg, combos)))

    today = local.date().isoformat()
    if local.hour >= cfg["daily_summary_hour"] and state.get("last_summary") != today and runs:
        day_ago = (now_utc - timedelta(hours=24)).isoformat()
        older = [r for r in runs if r["t"] <= day_ago and r.get("best")]
        change = ""
        if older:
            delta = best_now - older[-1]["best"]
            change = f" ({'+' if delta > 0 else ''}€{delta} vs 24h ago)" if delta else " (no change vs 24h ago)"
        atl = min([r["best"] for r in runs if r.get("best")] + [best_now])
        days_left = (date.fromisoformat(cfg["depart_from"]) - local.date()).days
        notify.send(
            f"☀️ <b>Daily flight summary</b> — cheapest €{best_now}{change}\n"
            f"Lowest seen: €{atl} · target €{cfg['target_price']} · {days_left} days to departure\n\n"
            + "\n\n".join(snapshot_lines(cfg, combos, top=5))
        )
        state["last_summary"] = today

    runs.append(now_run)
    all_best = [r["best"] for r in runs if r.get("best") is not None]
    all_auh = [r["auh"] for r in runs if r.get("auh") is not None]
    latest = {
        "checked_at": now_utc.isoformat(),
        "config": {k: cfg[k] for k in ("origin", "destination", "watch_via", "currency", "target_price",
                                       "strong_target_price", "depart_from", "depart_to")},
        "summary": {
            "best": best_now,
            "auh": auh_now,
            "best_low": min(all_best) if all_best else None,
            "auh_low": min(all_auh) if all_auh else None,
            "runs": len(runs),
            "failed_pairs": failures,
        },
        "combos": combos,
    }
    save(LATEST, latest)
    save(HISTORY, history, compact=True)
    save(STATE, state)
    print(f"Done: {len(fresh)} pairs, {len(failures)} failed, cheapest €{best_now}, via AUH €{auh_now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
