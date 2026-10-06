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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from common import (BAY, ECE_SALES, NOT_ECE, SEMI_NAME, STATES, grad_only, needs_clearance, DEFENSE, INTERN, OFFTRACK, ROOT, SENIOR, CompanyIndex, eligibility, http, is_us,
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
    """Eightfold's newer pcsx search API (the old /api/apply/v2/jobs now returns 403). Pages are 10 rows."""
    base = f"https://{s['host']}/api/pcsx"
    rows, seen = [], set()
    for q in QUERIES:
        start = 0
        while True:
            d = jget(f"{base}/search?domain={s['domain']}&query={quote(q)}&location=&start={start}&sort_by=relevance").get("data") or {}
            posts = d.get("positions", [])
            for j in posts:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                std = j.get("standardizedLocations") or []  # "Tualatin, OR, US": ends in a country code
                us = [l for l in std if l.split(",")[-1].strip() == "US"]
                if std and not us:
                    continue
                rows.append({"title": j.get("name", ""), "location": "; ".join(us or j.get("locations") or []),
                             "url": f"https://{s['host']}{j.get('positionUrl') or '/careers/job/' + str(j['id'])}",
                             "detail": ["eightfold", f"{base}/position_details?position_id={j['id']}&domain={s['domain']}&hl=en"]})
            start += len(posts)
            if not posts or start >= min(d.get("count") or 0, 300):
                break
            time.sleep(0.3)
    return rows


def a_ashby(s):
    d = jget(f"https://api.ashbyhq.com/posting-api/job-board/{s['org']}")
    return [{"title": j.get("title", ""), "location": j.get("location", ""), "url": j.get("jobUrl", ""),
             "desc": j.get("descriptionPlain", "")} for j in d.get("jobs", [])]


def a_lever(s):
    d = jget(f"https://{s.get('api', 'api.lever.co')}/v0/postings/{s['company']}?mode=json")  # EU boards: "api": "api.eu.lever.co"
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


def _foreign_code(loc):
    """SuccessFactors 'City, ST, US, zip' style: 'Munich, DE, 81829' or 'Penang, MY, MYS' is foreign
    (DE would otherwise pass as Delaware). 'Fremont, CA' with no country is kept."""
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if any(p in ("US", "USA") for p in parts):
        return False
    codes = [p for p in parts[1:] if re.fullmatch(r"[A-Z]{2,3}", p)]
    return bool(codes) and (len(parts) >= 3 or any(c not in STATES for c in codes))


def _sf_rss(s):
    """Older SuccessFactors sites: RSS feed, capped at ~20 items per query."""
    rows, seen = [], set()
    for q in QUERIES:
        xml = http(f"https://{s['host']}/services/rss/job/?locale=en_US&keywords=({quote(q)})")
        for it in ET.fromstring(xml).iter("item"):
            title, link = it.findtext("title") or "", it.findtext("link") or ""
            if link in seen:
                continue
            seen.add(link)
            loc = ""
            m = re.search(r"\(([^()]*,\s*[A-Z]{2}[^()]*)\)\s*$", title)  # "(Greensboro, NC, US, 27409)"
            d = re.search(r"\(([A-Za-z .]+(?: - [^()]+)+)\)\s*$", title)  # "(US - MA - Waltham)", "(Malaysia - Penang)"
            if m:
                loc, title = m.group(1), title[:m.start()].strip()
            elif d:
                parts = [p.strip() for p in d.group(1).split(" - ")]
                if parts[0] not in ("US", "USA", "United States"):
                    continue
                loc, title = ", ".join(reversed(parts[1:])), title[:d.start()].strip()
            if _foreign_code(loc):
                continue
            rows.append({"title": title, "location": loc, "url": link,
                         "desc": strip_html(it.findtext("description") or "")})
    return rows


def a_successfactors(s):
    """SuccessFactors career sites: the site's search API (10 per page); falls back to RSS where it's locked."""
    rows, seen = [], set()
    try:
        for q in QUERIES:
            page = 0
            while True:
                d = jget(f"https://{s['host']}/services/recruiting/v1/jobs",
                         data={"locale": "en_US", "pageNumber": page, "sortBy": "", "keywords": q, "location": "",
                               "facetFilters": {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""})
                res = d.get("jobSearchResult") or []
                for x in res:
                    j = x.get("response") or {}
                    if j.get("id") in seen:
                        continue
                    seen.add(j.get("id"))
                    locs = [l.strip() for l in j.get("jobLocationShort") or [] if l.strip()] or [j.get("primLocation") or ""]
                    if all(_foreign_code(l) for l in locs):
                        continue
                    rows.append({"title": j.get("unifiedStandardTitle", ""), "location": "; ".join(locs),
                                 "url": f"https://{s['host']}/job/{j.get('urlTitle')}/{j['id']}-en_US"})
                page += 1
                if not res or page * 10 >= min(d.get("totalJobs") or 0, 300):
                    break
                time.sleep(0.3)
    except HTTPError as e:
        if e.code not in (401, 403, 404):
            raise
        return _sf_rss(s)
    return rows


def a_talentbrew(s):
    """Radancy / TalentBrew career sites (careers.synopsys.com, careers.arm.com): results come back as HTML in JSON."""
    rows, seen = [], set()
    for q in QUERIES:
        for page in range(1, 6):
            h = jget(f"https://{s['host']}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage=100"
                     f"&Distance=50&RadiusUnitType=0&Keywords={quote(q)}&Location=&ShowRadius=False&IsPagination=False"
                     f"&FacetType=0&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters"
                     f"&SortCriteria=0&SortDirection=0&SearchType=5&ResultsType=0",
                     headers={"X-Requested-With": "XMLHttpRequest"}).get("results", "")
            new = 0
            for li in re.split(r"<li\b", h)[1:]:
                m = re.search(r'<a[^>]+href="(/job/[^"]+)"[^>]*>(.*?)</a>', li, re.S)
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                new += 1
                t = re.search(r"<h2[^>]*>(.*?)</h2>", m.group(2), re.S)
                loc = re.search(r'<span class="[^"]*location[^"]*">(.*?)</span>', li, re.S)
                rows.append({"title": strip_html(t.group(1) if t else m.group(2)), "location": strip_html(loc.group(1)) if loc else "",
                             "url": f"https://{s['host']}{m.group(1)}"})
            pages = re.search(r'data-total-pages="(\d+)"', h)
            if not new or page >= int(pages.group(1) if pages else 1):
                break
            time.sleep(0.3)
    return rows


def a_icims(s):
    """Classic iCIMS portals (careers-<co>.icims.com). Keyword search is unreliable there, so read every
    page (20 rows each) and let the filters pick interns. Locations look like 'US-CA-San Jose | IN-KA-Bangalore'."""
    rows, seen = [], set()
    for page in range(15):
        h = http(f"https://{s['host']}/jobs/search?pr={page}&in_iframe=1", headers={"Accept": "text/html"})
        new = 0
        for li in h.split('class="iCIMS_JobCardItem"')[1:]:
            m = re.search(r'href="([^"]+/jobs/\d+/[^"?]+/job)[^"]*"', li)
            t = re.search(r"<h3[^>]*>(.*?)</h3>", li, re.S)
            if not m or not t or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new += 1
            loc = re.search(r"Job Locations</span>\s*<span[^>]*>(.*?)</span>", li, re.S)
            locs = [x.strip() for x in strip_html(loc.group(1) if loc else "").split("|") if x.strip()]
            us = [", ".join(reversed(x.split("-", 2)[1:])) for x in locs if x.startswith("US-")]
            if locs and not us:
                continue
            d = re.search(r'class="col-xs-12 description">(.*?)</div>', li, re.S)
            rows.append({"title": strip_html(t.group(1)), "location": "; ".join(us), "url": m.group(1),
                         "desc": strip_html(d.group(1)) if d else ""})
        if not new:
            break
        time.sleep(0.3)
    return rows


def a_jobvite(s):
    """jobs.jobvite.com/<company>/jobs lists every opening on one page."""
    h = http(f"https://jobs.jobvite.com/{s['company']}/jobs", headers={"Accept": "text/html"})
    if "jv-job-list" not in h:  # unknown company slugs still return 200
        raise ValueError(f"no Jobvite job list for '{s['company']}'")
    rows = []
    for m in re.finditer(r'<td class="jv-job-list-name">\s*<a href="([^"]+)">(.*?)</a>\s*</td>\s*'
                         r'<td class="jv-job-list-location">(.*?)</td>', h, re.S):
        rows.append({"title": strip_html(m.group(2)), "location": " ".join(strip_html(m.group(3)).split()),
                     "url": f"https://jobs.jobvite.com{m.group(1)}"})
    return rows


def a_avature(s):
    """Avature portals (jobs.siemens.com/en_US/externaljobs). Optional "title_filter" regex keeps one business
    out of a shared portal (Siemens EDA titles carry 'EDA'). Raises if the page has no job links, so a markup
    change shows up as a failing feed instead of 0 rows."""
    base = f"https://{s['host']}/{s['portal'].strip('/')}"
    keep = re.compile(s["title_filter"]) if s.get("title_filter") else None
    rows, seen = [], set()
    for q in QUERIES:
        for page in range(10):
            h = http(f"{base}/SearchJobs/?search={quote(q)}&jobRecordsPerPage=50&jobOffset={page * 50}",
                     headers={"Accept": "text/html"})
            links = re.findall(r'<a[^>]+href="([^"]*/JobDetail/[^"]*)"[^>]*>(.*?)</a>', h, re.S)
            if page == 0 and q == QUERIES[0] and "/JobDetail/" not in h:
                raise ValueError("no Avature JobDetail links on the search page")
            new = 0
            for href, text in links:
                title = strip_html(text)
                url = href if href.startswith("http") else f"https://{s['host']}{href}"
                if not title or title.lower() in ("apply", "view", "more", "read more") or url in seen:
                    continue
                seen.add(url)
                new += 1
                if keep and not keep.search(title):
                    continue
                after = h[h.find(href):][:1500]
                loc = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</', after, re.S)
                rows.append({"title": title, "location": strip_html(loc.group(1)) if loc else "", "url": url})
            if not new:
                break
            time.sleep(0.3)
    return rows


ADAPTERS = {"workday": a_workday, "greenhouse": a_greenhouse, "smartrecruiters": a_smartrecruiters,
            "oracle": a_oracle, "eightfold": a_eightfold, "ashby": a_ashby, "lever": a_lever,
            "jibe": a_jibe, "successfactors": a_successfactors, "talentbrew": a_talentbrew,
            "icims": a_icims, "jobvite": a_jobvite, "avature": a_avature}


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
    if kind == "eightfold":
        return strip_html((jget(url).get("data") or {}).get("jobDescription", ""))
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

def feed_name(c, s):
    label = s.get("site") or s.get("board") or s.get("company") or s.get("org") or s.get("host")
    return f"{c['name']}: {s['type']} ({label})"


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
    if not comp_entry:  # companies off your list: technical sales / FAE roles only
        if lane != "sales" or not ECE_SALES.search(title) or NOT_ECE.search(title):
            return None
    if grad_only(title, row.get("desc", ""), row.get("degrees", [])) or needs_clearance(row.get("desc", "")):
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

    # official career sites, 8 at a time, with a progress line per feed
    repos_only = "--repos-only" in sys.argv
    jobs = []
    for c in COMPANIES:
        if repos_only or (only and norm(c["name"]) not in only):
            continue
        for s in c.get("sources", []):
            jobs.append((c, s, feed_name(c, s)))

    def run(job):
        c, s, name = job
        t0 = time.time()
        try:
            return job, ADAPTERS[s["type"]](s), None, round(time.time() - t0, 1)
        except Exception as e:  # one broken site never stops the run
            return job, [], f"{type(e).__name__}: {str(e)[:160]}", round(time.time() - t0, 1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for (c, s, name), rows, err, secs in pool.map(run, jobs):
            print(f"{'ok ' if not err else 'ERR'} {secs:>5}s {len(rows):>4} rows  {name}" + (f"  {err}" if err else ""), flush=True)
            if err:
                failed_sources.add(name)
                status.append({"name": name, "company": c["name"], "ok": False, "error": err})
                continue
            for r in rows:
                add(r, c, name, False)
            status.append({"name": name, "company": c["name"], "ok": True, "rows": len(rows), "secs": secs})

    # repos
    if not only:
        for src in CFG["repos"]:
            try:
                rows = REPO_ADAPTERS[src["type"]](src)
                for r in rows:
                    add(r, IDX.find(r.get("company", "")), src["name"], True)
                status.append({"name": src["name"], "company": "", "ok": True, "rows": len(rows)})
                print(f"ok  {len(rows):>4} rows  {src['name']}", flush=True)
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
    configured = {feed_name(c, s) for c in COMPANIES for s in c.get("sources", [])} | {r["name"] for r in CFG["repos"]}
    for lid, old in prev.items():
        if lid in current:
            continue
        if old.get("sources") and not any(s in configured for s in old["sources"]):
            continue  # every source behind it was removed from config (e.g. a feed that was the wrong company)
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

    listings = [l for l in listings if (l.get("known") or (l["lane"] == "sales" and ECE_SALES.search(l["title"])))
                and not grad_only(l["title"], l.get("desc", ""), l.get("degrees", []))
                and not needs_clearance(l.get("desc", ""))]
    for l in listings:
        l.pop("detail", None)
        l["bay"] = bool(BAY.search(l.get("location", "")))
        l["flags"], l["tags"] = eligibility(l["title"], l.get("desc", ""))
        if "Master's" in l.get("degrees", []) or re.search(r"\b(ms|m\.s\.|master'?s)\b", l["title"], re.I):
            l["tags"].append("BS/MS")
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
