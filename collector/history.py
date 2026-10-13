#!/usr/bin/env python3
"""Fill each company's posting timing in config/companies.json from Simplify's listing history:
  last_cycle: when its US intern postings for the previous summer opened, and roughly when they closed
  this_cycle: when postings for the coming summer started showing up
Run by hand every month or two during recruiting season (or after adding companies):
    python collector/history.py
Simplify trims old postings from listings.json each spring, so this also reads the file as it was in mid-February
(from the repo's git history) to see last fall's postings. Simplify doesn't list every company (no Lam, Synopsys or
Infineon, for example); those, and anything with fewer than 3 postings, get no timing and keep the hand-written window."""
import datetime as dt
import json
import sys
from collections import Counter

sys.path.insert(0, __import__("os").path.dirname(__file__))
from common import ROOT, CompanyIndex, INTERN, DEFENSE, is_us, jget

REPO = "SimplifyJobs/Summer2027-Internships"  # GitHub redirects the yearly renames, so old commits stay reachable
PATH = ".github/scripts/listings.json"
SWEEP = 150  # Simplify closes stale postings in bulk; a day with this many closures says nothing about one company


def snapshot(until):
    """listings.json as of the last commit before `until` (YYYY-MM-DD)."""
    # no path= filter: GitHub times out (500) on path-filtered history for this repo, and any commit has the file
    commits = jget(f"https://api.github.com/repos/{REPO}/commits?until={until}T00:00:00Z&per_page=1")
    return jget(f"https://raw.githubusercontent.com/{REPO}/{commits[0]['sha']}/{PATH}") if commits else []


def day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def timing(ps, closed_ok):
    opened = sorted(day(p["date_posted"]) for p in ps)
    closed = sorted(day(p["date_updated"]) for p in ps if p.get("active") is False and closed_ok(day(p["date_updated"])))
    return {"postings": len(ps), "opened": opened[min(1, len(opened) - 1)],  # 2nd earliest: skips one stray early post
            "closed": closed[len(closed) // 2] if len(closed) >= 3 else None}


def main():
    today = dt.date.today()
    cur = today.year + (1 if today.month >= 7 else 0)  # recruiting for Summer <cur> runs from the previous summer
    prev_term, cur_term = f"Summer {cur - 1}", f"Summer {cur}"
    rows = {}
    for x in snapshot(f"{cur - 1}-02-14") + jget(f"https://raw.githubusercontent.com/{REPO}/dev/{PATH}"):
        old = rows.get(x["id"])
        if old:  # Simplify re-dated carried-over postings when it reset the file; keep the first date seen
            x = dict(x, date_posted=min(x["date_posted"], old["date_posted"]))
        rows[x["id"]] = x
    sweeps = {d for d, n in Counter(day(x["date_updated"]) for x in rows.values() if x.get("active") is False).items() if n >= SWEEP}

    cfg = ROOT / "config/companies.json"
    companies = json.loads(cfg.read_text(encoding="utf-8"))
    idx = CompanyIndex(companies)
    posts = {}
    for x in rows.values():
        if not INTERN.search(x.get("title", "")) or DEFENSE.search(x.get("company_name", "")):
            continue
        if not any(is_us(l) for l in (x.get("locations") or [""])):
            continue
        c = idx.find(x.get("company_name", ""))
        for term in (prev_term, cur_term):
            if c and term in x.get("terms", []):
                posts.setdefault((c["name"], term), []).append(x)
    counts = Counter()
    for c in companies:
        for key, term, floor in (("last_cycle", prev_term, f"{cur - 2}-06-01"), ("this_cycle", cur_term, f"{cur - 1}-06-01")):
            ps = [p for p in posts.get((c["name"], term), []) if day(p["date_posted"]) >= floor]  # floor drops evergreen posts
            if len(ps) < 3:
                c.pop(key, None)
                continue
            c[key] = {"term": term, **timing(ps, lambda d: d not in sweeps)}
            if key == "this_cycle":
                c[key].pop("closed")
            counts[key] += 1
    cfg.write_text(json.dumps(companies, indent=1), encoding="utf-8", newline="")
    print(f"{prev_term} timing for {counts['last_cycle']}, {cur_term} for {counts['this_cycle']} of {len(companies)} companies "
          f"(ignored {len(sweeps)} bulk-closure days)")


if __name__ == "__main__":
    main()
