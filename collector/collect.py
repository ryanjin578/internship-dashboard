#!/usr/bin/env python3
"""Nightly collector. Pulls internship repos + official career sites, filters to Ryan's lanes,
dedupes, and writes data/listings.json + data/sources.json. No Claude, no API keys.
    python collector/collect.py            # full run
    python collector/collect.py --only Micron,Astera   # test specific companies
"""
import datetime as dt
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from common import (BAY, ECE_SALES, NOT_ECE, SEMI_NAME, DEFENSE, INTERN, OFFTRACK, ROOT, SENIOR, CompanyIndex, eligibility, http, is_us,
                    jget, lane_of, norm, strip_html, term_of)

NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date().isoformat()
COMPANIES = json.loads((ROOT / "config/companies.json").read_text())
CFG = json.loads((ROOT / "config/sources.json").read_text())
IDX = CompanyIndex(COMPANIES)
QUERIES = ["intern", "co-op"]


# ---------- career-site adapters: each returns raw rows ----------

def a_workday(s):
    base = f"https://{s['host']}/wday/cxs/{s['tenant']}/{s['site']}"
    rows, seen = [], set()
    for q in QUERIES:
        off, total = 0, None
        while True:
            d = jget(base + "/jobs", data={"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q})
            total = d.get("total") or total or 0
            posts = d.get("jobPostings", [])
            for p in posts:
                ep = p.get("externalPath")
                if not ep or ep in seen:
                    continue
                seen.add(ep)
                rows.append({"title": p.get("title", ""), "location": p.get("locationsText", ""),
                             "url": f"https://{s['host']}/{s['site']}{ep}", "detail": ["workday", base + ep]})
            off += 20
            if not posts or off >= min(total, 300):
                break
            time.sleep(0.3)
    return rows


def a_greenhouse(s):
    d = jget(f"https://boards-api.greenhouse.io/v1/boards/{s['board']}/jobs?content=true")
    return [{"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", ""), "desc": strip_html(j.get("content", ""))} for j in d.get("jobs", [])]


def a_smartrecruiters(s):
    rows = []
    for q in QUERIES:
        d = jget(f"https://api.smartrecruiters.com/v1/companies/{s['company']}/postings?q={quote(q)}&limit=100")
        for j in d.get("content", []):
            loc = j.get("location") or {}
            rows.append({"title": j.get("name", ""), "location": ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country", "").upper()] if x),
                         "url": f"https://jobs.smartrecruiters.com/{s['company']}/{j['id']}",
                         "detail": ["smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{s['company']}/postings/{j['id']}"]})
    return rows


def a_oracle(s):
    rows = []
    for q in QUERIES:
        url = (f"https://{s['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={s['site']},keyword={quote(q)},limit=200,offset=0")
        d = jget(url)
        for item in d.get("items", []):
            for j in item.get("requisitionList", []):
                rows.append({"title": j.get("Title", ""), "location": j.get("PrimaryLocation", ""),
                             "url": f"https://{s['host']}/hcmUI/CandidateExperience/en/sites/{s['site']}/job/{j['Id']}",
                             "desc": strip_html(j.get("ShortDescriptionStr", "")),
                             "detail": ["oracle", f"https://{s['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22{j['Id']}%22,siteNumber={s['site']}"]})
    return rows


def a_eightfold(s):
    rows = []
    for q in QUERIES:
        d = jget(f"https://{s['host']}/api/apply/v2/jobs?domain={s['domain']}&query={quote(q)}&start=0&num=100")
        for j in d.get("positions", []):
            rows.append({"title": j.get("name", ""), "location": j.get("location", ""),
                         "url": j.get("canonicalPositionUrl") or f"https://{s['host']}/careers/job/{j.get('id')}",
                         "desc": strip_html(j.get("job_description", ""))})
    return rows


def a_ashby(s):
    d = jget(f"https://api.ashbyhq.com/posting-api/job-board/{s['org']}")
    return [{"title": j.get("title", ""), "location": j.get("location", ""), "url": j.get("jobUrl", ""),
             "desc": j.get("descriptionPlain", "")} for j in d.get("jobs", [])]


def a_lever(s):
    d = jget(f"https://api.lever.co/v0/postings/{s['company']}?mode=json")
    return [{"title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", ""),
             "url": j.get("hostedUrl", ""), "desc": j.get("descriptionPlain", "")} for j in d]


def a_jibe(s):
    rows = []
    for q in QUERIES:
        d = jget(f"https://{s['host']}/api/jobs?keywords={quote(q)}&page=1&limit=100")
        for j in d.get("jobs", []):
            j = j.get("data", j)
            rows.append({"title": j.get("title", ""),
                         "location": ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x),
                         "url": f"https://{s['host']}/jobs/{j.get('req_id') or j.get('slug')}",
                         "desc": strip_html(j.get("description", ""))})
    return rows


def a_successfactors(s):
    rows = []
    for q in QUERIES:
        xml = http(f"https://{s['host']}/services/rss/job/?locale=en_US&keywords=({quote(q)})")
        for it in ET.fromstring(xml).iter("item"):
            title = it.findtext("title") or ""
            loc = ""
            m = re.search(r"\(([^()]*,\s*[A-Z]{2}[^()]*)\)\s*$", title)
            if m:
                loc = m.group(1)
            rows.append({"title": title, "location": loc, "url": it.findtext("link") or "",
                         "desc": strip_html(it.findtext("description") or "")})
    return rows


ADAPTERS = {"workday": a_workday, "greenhouse": a_greenhouse, "smartrecruiters": a_smartrecruiters,
            "oracle": a_oracle, "eightfold": a_eightfold, "ashby": a_ashby, "lever": a_lever,
            "jibe": a_jibe, "successfactors": a_successfactors}


def fetch_detail(d):
    kind, url = d
    if kind == "workday":
        return strip_html(jget(url).get("jobPostingInfo", {}).get("jobDescription", ""))
    if kind == "smartrecruiters":
        secs = jget(url).get("jobAd", {}).get("sections", {})
        return strip_html(" ".join((secs.get(k) or {}).get("text", "") for k in ["jobDescription", "qualifications"]))
    if kind == "oracle":
        items = jget(url).get("items", [])
        return strip_html(items[0].get("ExternalDescriptionStr", "") + " " + items[0].get("ExternalQualificationsStr", "")) if items else ""
    return ""


# ---------- repo adapters ----------

def r_simplify(src):
    rows = []
    for x in jget(src["url"]):
        terms = [t for t in x.get("terms", []) if "2027" in t]
        if not terms or not x.get("is_visible", True):
            continue
        rows.append({"company": x.get("company_name", ""), "title": x.get("title", ""),
                     "location": "; ".join(x.get("locations", [])), "url": x.get("url", ""),
                     "term": terms[0], "category": x.get("category", ""), "active": x.get("active", True),
                     "degrees": x.get("degrees", [])})
    return rows


LINK = re.compile(r'href="(https?://[^"]+)"|\]\((https?://[^)\s]+)\)')


def clean_cell(c):
    c = re.sub(r"<[^>]+>", " ", c)
    c = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c)
    c = re.sub(r"[*_`]|[\U0001F000-\U0001FFFF\u2600-\u27BF]", "", c)
    return re.sub(r"\s+", " ", c).strip()


def r_md_table(src):
    rows, cols, prev = [], None, ""
    for line in http(src["url"]).splitlines():
        if not line.startswith("|"):
            cols = None if not line.strip() else cols
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        low = [clean_cell(c).lower() for c in cells]
        if "company" in low:
            cols = low
            continue
        if not cols or set(line) <= set("|-: "):
            continue

        def get(*names):
            for i, h in enumerate(cols):
                if any(n in h for n in names) and i < len(cells):
                    return cells[i]
            return ""
        comp = clean_cell(get("company"))
        if comp in ("↳", ""):
            comp = prev
        prev = comp
        link_cell = get("apply", "posting", "link", "application")
        m = LINK.search(link_cell) or LINK.search(line)
        if not m or "🔒" in line:
            continue
        title = clean_cell(get("role", "position", "title"))
        rows.append({"company": comp, "title": title, "location": clean_cell(get("location")),
                     "url": m.group(1) or m.group(2), "needs_intern": True,
                     "term": term_of(title) if term_of(title) != "Unspecified" else src.get("default_term", "Unspecified")})
    return rows


REPO_ADAPTERS = {"simplify_json": r_simplify, "md_table": r_md_table}


# ---------- pipeline ----------

def key_of(company, title, location):
    loc = norm(location.split(";")[0].split(",")[0])
    return hashlib.sha1(f"{norm(company)}|{norm(title)}|{loc}".encode()).hexdigest()[:12]


def accept(row, comp_entry, from_repo):
    """Returns the cleaned listing or None."""
    title = row["title"].strip()
    company = comp_entry["name"] if comp_entry else row.get("company", "").strip()
    if not title or not company or DEFENSE.search(company) or DEFENSE.search(title):
        return None
    if not INTERN.search(title) and (not row.get("term") or row.get("needs_intern")):
        return None
    if SENIOR.search(title) and not INTERN.search(title):
        return None
    if not is_us(row.get("location", "")):
        return None
    term = row.get("term") or term_of(title)
    if term is None or (row.get("term") and "2027" not in term and term != "Unspecified"):
        return None
    lane = lane_of(title)
    if lane == "other" and row.get("category") == "Hardware":
        lane = "hw"
    if lane == "other" or (lane != "sales" and OFFTRACK.search(title)):
        return None
    if not comp_entry:  # unknown company: keep technical sales, hardware, or anything at a chip-sounding company
        semi_name = bool(SEMI_NAME.search(company))
        if lane == "sales" and (not ECE_SALES.search(title) or NOT_ECE.search(title)) and not semi_name:
            return None
        if lane != "sales" and not (row.get("category") == "Hardware" or semi_name):
            return None
    coop = bool(re.search(r"\bco-?op\b", title, re.I))
    return {"company": company, "title": title, "location": row.get("location", ""), "url": row.get("url", ""),
            "term": term, "coop": coop, "lane": lane, "desc": (row.get("desc") or "")[:4000],
            "tier": comp_entry["tier"] if comp_entry else "", "segment": comp_entry["segment"] if comp_entry else "",
            "known": bool(comp_entry), "detail": row.get("detail"), "degrees": row.get("degrees", [])}


def score(l):
    s = {"sales": 40, "swe": 25, "hw": 15}.get(l["lane"], 0)
    s += {"P1": 25, "P2": 15, "P3": 5}.get(l["tier"], 0)
    s += 15 if l["bay"] else 0
    s += 10 if l["known"] else 0
    s += 5 if l["first_seen"] >= (NOW.date() - dt.timedelta(days=3)).isoformat() else 0
    s -= 12 * len(l["flags"])
    s += 5 if "Soph OK" in l["tags"] else 0
    return s


def main():
    only = None
    if "--only" in sys.argv:
        only = {norm(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    out_path = ROOT / "data/listings.json"
    prev = {l["id"]: l for l in json.loads(out_path.read_text()).get("listings", [])} if out_path.exists() else {}
    status, current, failed_sources = [], {}, set()

    def add(row, comp_entry, src_name, from_repo):
        l = accept(row, comp_entry, from_repo)
        if not l:
            return
        lid = key_of(l["company"], l["title"], l["location"])
        cur = current.get(lid)
        if cur:
            cur["sources"] = sorted(set(cur["sources"]) | {src_name})
            if not from_repo and cur.get("from_repo"):  # prefer the official site's link + text
                cur.update({k: l[k] for k in ("url", "detail") if l.get(k)})
                cur["desc"] = l["desc"] or cur["desc"]
                cur["from_repo"] = False
            if row.get("active") is False:
                cur["inactive"] = True
            return
        l.update({"id": lid, "sources": [src_name], "from_repo": from_repo, "inactive": row.get("active") is False})
        current[lid] = l

    # official career sites
    repos_only = "--repos-only" in sys.argv
    for c in COMPANIES:
        if repos_only:
            break
        if only and norm(c["name"]) not in only:
            continue
        for s in c.get("sources", []):
            name = f"{c['name']}: {s['type']}" + (f" ({s.get('site') or s.get('board') or s.get('company') or s.get('org') or s.get('host')})")
            t0 = time.time()
            try:
                rows = ADAPTERS[s["type"]](s)
                for r in rows:
                    add(r, c, name, False)
                status.append({"name": name, "company": c["name"], "ok": True, "rows": len(rows), "secs": round(time.time() - t0, 1)})
            except Exception as e:  # one broken site never stops the run
                failed_sources.add(name)
                status.append({"name": name, "company": c["name"], "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"})
            time.sleep(0.5)

    # repos
    if not only:
        for src in CFG["repos"]:
            try:
                rows = REPO_ADAPTERS[src["type"]](src)
                for r in rows:
                    add(r, IDX.find(r.get("company", "")), src["name"], True)
                status.append({"name": src["name"], "company": "", "ok": True, "rows": len(rows)})
            except Exception as e:
                failed_sources.add(src["name"])
                status.append({"name": src["name"], "company": "", "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"})

    # descriptions for brand-new official listings only (keeps runs fast and polite)
    budget = CFG.get("detail_fetch_cap", 80)
    for lid, l in current.items():
        old = prev.get(lid)
        if old and old.get("desc") and not l["desc"]:
            l["desc"] = old["desc"]
        if not l["desc"] and l.get("detail") and budget > 0:
            budget -= 1
            try:
                l["desc"] = fetch_detail(l["detail"])[:4000]
            except Exception:
                pass

    # merge with history
    listings = []
    for lid, l in current.items():
        if l.get("inactive") and lid not in prev:
            continue  # closed before we ever saw it
        old = prev.get(lid, {})
        l["first_seen"] = old.get("first_seen", TODAY)
        l["last_seen"] = TODAY
        l["missed"] = 0
        l["status"] = "closed" if l.pop("inactive", False) else "open"
        if l["status"] == "closed":
            l["closed_on"] = old.get("closed_on", TODAY)
        listings.append(l)
    for lid, old in prev.items():
        if lid in current:
            continue
        if only and norm(old["company"]) not in only:
            listings.append(old)
            continue
        if any(s in failed_sources for s in old.get("sources", [])):
            listings.append(old)  # its source broke tonight: don't call it closed
            continue
        old["missed"] = old.get("missed", 0) + 1
        if old["missed"] >= CFG.get("close_after_missed_runs", 2) and old["status"] == "open":
            old["status"], old["closed_on"] = "closed", TODAY
        if old["status"] == "closed" and old.get("closed_on", TODAY) < (NOW.date() - dt.timedelta(days=30)).isoformat():
            continue
        listings.append(old)

    for l in listings:
        l.pop("detail", None)
        l["bay"] = bool(BAY.search(l.get("location", "")))
        l["flags"], l["tags"] = eligibility(l["title"], l.get("desc", ""))
        if any(d in ("Master's", "PhD", "MBA") for d in l.get("degrees", [])) and "Bachelor's" not in l.get("degrees", []) \
                and "Grad degree" not in l["flags"]:
            l["flags"].append("Grad degree")
        l["score"] = score(l)
    listings.sort(key=lambda l: (l["status"] != "open", -l["score"], l["company"]))

    (ROOT / "data").mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"generated_at": NOW.isoformat(timespec="seconds"),
                                    "count": sum(l["status"] == "open" for l in listings),
                                    "listings": listings}, separators=(",", ":")))
    (ROOT / "data/sources.json").write_text(json.dumps({"generated_at": NOW.isoformat(timespec="seconds"),
                                                       "sources": status}, indent=1))
    ok = sum(s["ok"] for s in status)
    print(f"{len(listings)} listings ({sum(l['status'] == 'open' for l in listings)} open); sources ok {ok}/{len(status)}")


if __name__ == "__main__":
    main()
