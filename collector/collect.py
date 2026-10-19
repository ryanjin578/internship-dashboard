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
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from common import (BAY, ECE_SALES, FOREIGN, NOT_ECE, NONTECH_SALES, SEMI_NAME, STATES, grad_only, needs_clearance, DEFENSE, INTERN, OFFTRACK, ROOT, SENIOR, CompanyIndex, eligibility, http, is_us,
                    jget, lane_of, norm, strip_html, term_of)

NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date().isoformat()
COMPANIES = json.loads((ROOT / "config/companies.json").read_text())
CFG = json.loads((ROOT / "config/sources.json").read_text())
IDX = CompanyIndex(COMPANIES)
UNREADABLE = CFG.get("unreadable_hosts", {})  # job pages no script can read; open/closed comes from the feeds and repos
QUERIES = ["intern", "co-op"]
DESC_CAP = 8000  # stored text; hashes, deadlines and pay are read from the full text


class Gone(Exception):
    """The job page says the posting no longer exists (404/410, an empty record, or a 'no longer available' page)."""


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
             "url": j.get("absolute_url", ""), "desc": strip_html(j.get("content", "")), "full": True} for j in d.get("jobs", [])]


def a_smartrecruiters(s):
    rows = []
    for q in QUERIES:
        d = jget(f"https://api.smartrecruiters.com/v1/companies/{s['company']}/postings?q={quote(q)}&limit=100")
        for j in d.get("content", []):
            loc = j.get("location") or {}
            if loc.get("country") and loc["country"].lower() != "us":  # "DE" would otherwise pass as Delaware
                continue
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
                countries = {j.get("PrimaryLocationCountry")} | {x.get("CountryCode") for x in j.get("secondaryLocations") or []}
                if j.get("PrimaryLocationCountry") and "US" not in countries:
                    continue
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
             "desc": j.get("descriptionPlain", ""), "full": True} for j in d.get("jobs", [])]


def _lever_text(j):
    lists = " ".join(f"{x.get('text', '')}\n{strip_html(x.get('content', ''))}" for x in j.get("lists") or [])
    return strip_html(f"{j.get('descriptionPlain', '')}\n{lists}\n{j.get('additionalPlain', '')}")


def a_lever(s):
    d = jget(f"https://{s.get('api', 'api.lever.co')}/v0/postings/{s['company']}?mode=json")  # EU boards: "api": "api.eu.lever.co"
    return [{"title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", ""),
             "url": j.get("hostedUrl", ""), "desc": _lever_text(j), "full": True} for j in d]


def a_jibe(s):
    rows = []
    for q in QUERIES:
        d = jget(f"https://{s['host']}/api/jobs?keywords={quote(q)}&page=1&limit=100")
        for j in d.get("jobs", []):
            j = j.get("data", j)
            rows.append({"title": j.get("title", ""),
                         "location": ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x),
                         "url": f"https://{s['host']}/jobs/{j.get('req_id') or j.get('slug')}",
                         "desc": strip_html(j.get("description", "")), "full": True})
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
                if not title or title.lower() in ("apply", "view", "more", "read more") or title.startswith("Share ") or url in seen:
                    continue
                seen.add(url)
                new += 1
                if keep and not keep.search(title):
                    continue
                after = h[h.find(href):][:3000]
                # first thing in the subtitle: city/state/country spans then "• Job ID" (Siemens), or "...<br> Req #" (Lenovo)
                loc = re.search(r'class="[^"]*(?:location|subtitle)[^"]*"[^>]*>(.*?)(?:&#8226;|•|<br|Job ID|Req #|</div>)', after, re.S)
                loc = re.sub(r"\s+,", ",", " ".join(strip_html(loc.group(1)).split())).strip(" ,") if loc else ""
                rows.append({"title": title, "location": loc, "url": url})
            if not new:
                break
            time.sleep(0.3)
    return rows


ADAPTERS = {"workday": a_workday, "greenhouse": a_greenhouse, "smartrecruiters": a_smartrecruiters,
            "oracle": a_oracle, "eightfold": a_eightfold, "ashby": a_ashby, "lever": a_lever,
            "jibe": a_jibe, "successfactors": a_successfactors, "talentbrew": a_talentbrew,
            "icims": a_icims, "jobvite": a_jobvite, "avature": a_avature}


_ashby_boards, _ashby_lock = {}, threading.Lock()


def _html_description(url):
    """Description from a job page: schema.org JobPosting JSON-LD first, then the TEXT_BLOCKS layouts
    (SuccessFactors, TalentBrew, iCIMS, Jobvite, Avature and many custom sites). Returns "" when none is present."""
    h = http(url, headers={"Accept": "text/html"})
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', h, re.S | re.I):
        try:
            data = json.loads(block.strip(), strict=False)
        except ValueError:
            continue
        for d in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
            if isinstance(d, dict) and d.get("@type") == "JobPosting" and d.get("description"):
                return strip_html(d["description"])
    parts = []
    for start, stop in TEXT_BLOCKS:
        for m in re.finditer(start, h):
            body = h[m.end():m.end() + 40000]
            end = re.search(stop, body)
            if end:  # cut before the tag that holds the marker, so no half tag is left
                body = body[:max(0, body.rfind("<", 0, end.start()))]
            txt = strip_html(body)
            if txt and txt not in parts:
                parts.append(txt)
        if parts:
            break
    if not parts and CLOSED_PAGE.search(strip_html(h)[:20000]):
        raise Gone("page says the job is closed")
    return "\n".join(parts)


# Where a page without JSON-LD keeps its text: (block start, where the block ends), first layout that matches wins.
# SuccessFactors splits it over several itemprop="description" blocks (intro, duties, ...); its newer "unify" pages
# (seagatecareers.com) have one joblayouttoken block per field, and Avature (jobs.lenovo.com, jobs.siemens.com) one
# article__content__view__field block per field.
TEXT_BLOCKS = [(r'<(?!meta)[a-z]+[^>]*itemprop="description"[^>]*>', r'joblayouttoken-label|itemprop="description"|class="applylink|</article>|<footer'),
               (r'<div class="joblayouttoken[ "][^>]*>', r'class="joblayouttoken[ "]|class="jobColumnTwo"|<footer'),
               (r'<div class="article__content__view__field[ "][^>]*>', r'class="article__content__view__field[ "]|</article>|<footer')]

# Only checked on pages with no description, so a posting that mentions these words isn't mistaken for a closed one
CLOSED_PAGE = re.compile(r"no longer (available|accepting|open|active|posted)|position has been filled|"
                         r"(job|posting|position|page)( you are looking for)? (was |is |could )?not( be)? found|"
                         r"this (job|position|posting|requisition) (has )?(expired|closed|is closed|been closed)", re.I)


def fetch_detail(d):
    """(full description text, country code or None) for a detail pointer; raises Gone when the site says the posting
    no longer exists. Only Workday says the country: its search shows "2 Locations" or a bare city like "Gratkorn"."""
    try:
        r = _fetch_detail(d)
        return r if isinstance(r, tuple) else (r, None)
    except HTTPError as e:
        if e.code in (404, 410):  # Workday, Greenhouse and Lever answer 404 for a removed posting
            raise Gone(f"HTTP {e.code}")
        if e.code == 403 and d[0] == "workday" and b'"S22"' in e.read():  # Workday's "permission denied" for an unposted job
            raise Gone("HTTP 403 S22")
        raise


def _fetch_detail(d):
    kind, url = d[0], d[1]
    if kind == "workday":
        j = jget(url).get("jobPostingInfo", {})
        country = ((j.get("jobRequisitionLocation") or {}).get("country") or {}).get("alpha2Code")
        if country and country != "US" and any(re.match(r"(USA|United States)\b", x) for x in j.get("additionalLocations") or []):
            country = "US"  # posted abroad with a US site as an additional location
        return strip_html(j.get("jobDescription", "")), country
    if kind == "smartrecruiters":
        j = jget(url)
        if j.get("active") is False:
            raise Gone("inactive")
        secs = j.get("jobAd", {}).get("sections", {})
        return strip_html(" ".join((secs.get(k) or {}).get("text", "") for k in ["jobDescription", "qualifications"]))
    if kind == "oracle":
        items = jget(url).get("items", [])
        if not items:
            raise Gone("no record")
        return strip_html(items[0].get("ExternalDescriptionStr", "") + " " + items[0].get("ExternalQualificationsStr", ""))
    if kind == "eightfold":
        data = jget(url).get("data")
        if not data:
            raise Gone("no record")
        return strip_html(data.get("jobDescription", ""))
    if kind == "greenhouse":
        return strip_html(jget(url).get("content", ""))
    if kind == "lever":
        return _lever_text(jget(url))
    if kind == "ashby":  # the public API only lists a whole board, so fetch each board once per run
        org, job_id = url, d[2]
        with _ashby_lock:
            if org not in _ashby_boards:
                _ashby_boards[org] = {j.get("id"): j for j in jget(f"https://api.ashbyhq.com/posting-api/job-board/{org}").get("jobs", [])}
        if job_id not in _ashby_boards[org]:
            raise Gone("not on the board")
        return _ashby_boards[org][job_id].get("descriptionPlain", "")
    if kind == "html":
        return _html_description(url)
    return ""


def detail_from_url(u):
    """Maps an apply link (mostly from the GitHub repos) to the ATS endpoint that has its description."""
    p = urlparse(u or "")
    host, parts = p.netloc.lower(), [x for x in p.path.split("/") if x]
    if p.scheme not in ("http", "https") or not host or host in UNREADABLE:
        return None
    q = parse_qs(p.query)
    if re.match(r"[a-z]{2}-[A-Z]{2}$", parts[0] if parts else ""):
        parts = parts[1:]
    m = re.match(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", host)
    if m and len(parts) >= 3 and "job" in parts[1:]:
        k = parts.index("job", 1)
        return ["workday", f"https://{host}/wday/cxs/{m.group(1)}/{parts[0]}/{'/'.join(parts[k:])}"]
    if re.match(r"wd\d+\.myworkdaysite\.com$", host) and len(parts) >= 4 and parts[0] == "recruiting" and "job" in parts:
        k = parts.index("job")
        return ["workday", f"https://{host}/wday/cxs/{parts[1]}/{parts[2]}/{'/'.join(parts[k:])}"]
    if "greenhouse.io" in host:
        if parts[:1] == ["embed"] and q.get("for") and q.get("token"):
            return ["greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{q['for'][0]}/jobs/{q['token'][0]}"]
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            return ["greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs/{parts[2]}"]
    if q.get("gh_jid") and q["gh_jid"][0].isdigit():
        return ["html", u]  # company page wrapping a Greenhouse board; the board name isn't in the link
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(parts) >= 2:
        api = "api.eu.lever.co" if ".eu." in host else "api.lever.co"
        return ["lever", f"https://{api}/v0/postings/{parts[0]}/{parts[1]}"]
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return ["ashby", parts[0], parts[1]]
    if host == "jobs.smartrecruiters.com" and len(parts) >= 2 and re.match(r"\d+", parts[1]):
        return ["smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{re.match(r'\d+', parts[1]).group(0)}"]
    if host.endswith("oraclecloud.com") and "sites" in parts and "job" in parts:
        site, job_id = parts[parts.index("sites") + 1], parts[parts.index("job") + 1] if len(parts) > parts.index("job") + 1 else ""
        if job_id.isdigit():
            return ["oracle", f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22{job_id}%22,siteNumber={site}"]
    pid = (parts[2] if len(parts) >= 3 and parts[:2] == ["careers", "job"] and parts[2].isdigit() else (q.get("pid") or [""])[0])
    if pid and (host.endswith(".eightfold.ai") or parts[:1] == ["careers"]):
        domain = f"{host.split('.')[0]}.com" if host.endswith(".eightfold.ai") else ".".join(host.split(".")[-2:])
        return ["eightfold", f"https://{host}/api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en"]
    if host.endswith(".icims.com") and "/jobs/" in p.path:
        return ["html", f"{p.scheme}://{host}{p.path}?in_iframe=1"]  # the bare page is a wrapper frame
    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        return None  # Workday search/landing pages: nothing to read
    return ["html", u]


FILL, CONFIRM, RECHECK = 0, 1, 2  # task priorities: never read in full, missing from tonight's search, routine re-read


def fetch_details(tasks, fill_cap, per_host, budget_s):
    """Reads job pages: one worker per host (each site sees one request at a time, with a pause), up to 8 hosts in
    parallel. tasks: (priority, sort key, listing, detail). New postings (FILL) share a global cap; re-reads are capped
    per host so no site gets more than `per_host` extra requests a night. A host is dropped for the night on a 429 or
    after 3 failures in a row. Sets l["_full"]/l["_kind"] on success or l["_gone"] when the site says it's gone.
    Returns per-host counts."""
    todo = {}
    for t in sorted(tasks, key=lambda t: t[:2]):
        d = t[3]
        todo.setdefault(urlparse(d[1]).netloc if d[0] != "ashby" else "api.ashbyhq.com", []).append(t)
    deadline, lock, left = time.time() + budget_s, threading.Lock(), [fill_cap]
    stats = {}

    def work(host, items):
        st = stats[host] = Counter()
        streak, rechecks = 0, 0
        for n, (prio, _, l, d) in enumerate(items):
            if streak >= 3:
                st["blocked"] += len(items) - n
                return
            with lock:
                if time.time() > deadline or (prio == FILL and left[0] <= 0) or (prio != FILL and rechecks >= per_host):
                    st["skipped"] += 1
                    continue
                if prio == FILL:
                    left[0] -= 1
                else:
                    rechecks += 1
            try:
                txt, country = fetch_detail(d)
                txt, streak = txt.strip(), 0
                if country:
                    l["_country"] = country
                if len(txt) >= 80:
                    l["_full"], l["_kind"] = txt, d[0]
                    st["read"] += 1
                else:
                    st["empty"] += 1
            except Gone as e:
                streak = 0
                l["_gone"] = str(e)
                st["gone"] += 1
            except HTTPError as e:
                st["failed"] += 1
                if e.code == 429:  # rate limited: leave this site alone until tomorrow
                    st["blocked"] += len(items) - n - 1
                    return
                streak += 1
            except Exception:
                st["failed"] += 1
                streak += 1
            time.sleep(0.3)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda kv: work(*kv), todo.items()))
    return stats


# ---------- reading a full description: change hash, stated deadline, pay ----------

# Text that changes on its own ("Posted 3 days ago") is left out of the hash, as are whitespace and punctuation
DYNAMIC = re.compile(r"\b(date posted|posted|updated)\s*:?\s*(on\s+)?(today|yesterday|\d+\+?\s+\w+\s+ago|[a-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|"
                     r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d\d-\d\d)|\b\d+\+?\s+(days?|hours?|weeks?|months?)\s+ago\b|\b\d+\+?\s+applicants\b", re.I)


def text_hash(t):
    return hashlib.sha1(re.sub(r"[^a-z0-9$%]+", "", DYNAMIC.sub(" ", t.lower())).encode()).hexdigest()[:12]


_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_RE = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?\b"
                     rf"|\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}(?:,?\s+(20\d\d))?\b"
                     r"|\b(\d{1,2})/(\d{1,2})/((?:20)?\d\d)\b|\b(20\d\d)-(\d\d)-(\d\d)\b", re.I)
# Only wording about applying counts; "meet deadlines", start dates and graduation dates never match
DEADLINE_CUE = re.compile(
    r"\b(application deadline|deadline (to|for) (apply|applications?|submit\w*)|deadline\s*[:\-–]|apply (by|before|no later than)|"
    r"applications? (are |will be )?(accepted|due|reviewed|received|considered|open) (until|through|thru|by|before|no later than)|"
    r"(applications?|posting|window) (will )?(close|closes|closing|end|ends|expires?)( on)?|"
    r"(position|job|requisition|role) (posting )?(will )?(close|closes|closing|expires?)( on)?|"
    r"(posted|open|accepting applications) (until|through|thru)|closing date|posting end date|last day to apply)(?![a-z])", re.I)
PAY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?\s*[kK]?\s*(?:-|–|—|to)\s*\$?\s?\d[\d,]*(?:\.\d{1,2})?\s*[kK]?"
                    r"(?:\s*(?:/|per|an|a)\s*(?:hour|hr|year|yr|annum|month|week)\b)?"
                    r"|\$\s?\d[\d,]*(?:\.\d{1,2})?\s*(?:/|per|an|a)\s*(?:hour|hr)\b", re.I)
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def _date_of(m, first_seen):
    g = m.groups()
    try:
        if g[0]:
            mon, day, year = MONTHS[g[0][:3].lower()], int(g[1]), g[2]
        elif g[3]:
            day, mon, year = int(g[3]), MONTHS[g[4][:3].lower()], g[5]
        elif g[6]:
            a, b, year = int(g[6]), int(g[7]), g[8]
            mon, day = (b, a) if a > 12 else (a, b)  # US sites write month first
            year = year if len(year) == 4 else "20" + year
        else:
            year, mon, day = g[9], int(g[10]), int(g[11])
        if year:
            return dt.date(int(year), mon, day)
        d = dt.date(first_seen.year, mon, day)  # no year: the next such date after the posting appeared
        return d if d >= first_seen - dt.timedelta(days=30) else dt.date(first_seen.year + 1, mon, day)
    except (ValueError, KeyError):
        return None


def find_deadline(text, first_seen):
    """(date, sentence) for an application deadline the posting states, or None. The date must sit right after the
    cue in the same sentence, and fall between a month before the posting appeared and about a year out."""
    fs = dt.date.fromisoformat(first_seen)
    for m in DEADLINE_CUE.finditer(text):
        seg = text[m.end():m.end() + 70]
        dm = DATE_RE.search(seg)
        if not dm or dm.start() > 45 or re.search(r"[.!?]\s|\n", seg[:dm.start()]):
            continue
        d = _date_of(dm, fs)
        if not d or not (fs - dt.timedelta(days=45) <= d <= NOW.date() + dt.timedelta(days=400)):
            continue
        dot = text.rfind(". ", 0, m.start())
        a = max(dot + 2 if dot >= 0 else 0, text.rfind("\n", 0, m.start()) + 1, m.start() - 160)
        tail = re.search(r"[.!?](\s|$)|\n", text[m.end() + dm.end():])
        b = m.end() + dm.end() + (tail.end() if tail else 0)
        return d.isoformat(), " ".join(text[a:min(b, a + 260)].split())
    return None


def find_pay(text):
    for m in PAY_RE.finditer(text):
        if not re.match(r"\s*(million|billion|[mb]n?\b)", text[m.end():m.end() + 10], re.I) and re.search(r"[1-9]", m.group(0)):
            return " ".join(m.group(0).split())[:60]
    return None


TEXT_FIELDS = ("desc_hash", "hash_kind", "checked_on", "desc_changed_on", "deadline", "deadline_quote", "pay", "page_gone", "country")


def apply_text(l, old):
    """Tonight's full text (inline from the board or read from the job page) updates the stored description, its hash,
    the stated deadline and pay. Without it, last night's values carry over. A different hash from the same kind of
    source is a real edit (desc_changed_on); a new source kind just resets the baseline."""
    text, kind, gone, country = l.pop("_full", ""), l.pop("_kind", None), l.pop("_gone", None), l.pop("_country", None)
    for k in TEXT_FIELDS:
        if old.get(k) and not l.get(k):
            l[k] = old[k]
    if country:
        l["country"] = country
    if gone:
        l["page_gone"] = old.get("page_gone") or TODAY
        if old.get("desc") and len(old["desc"]) > len(l.get("desc") or ""):
            l["desc"] = old["desc"]
        return
    if len(text) < 80:
        if old.get("desc") and (old.get("desc_hash") or not l.get("desc")):
            l["desc"] = old["desc"]  # keep the full text over a short teaser from the search results
        return
    h = text_hash(text)
    if old.get("desc_hash") and old.get("hash_kind") == kind and old["desc_hash"] != h:
        l["desc_changed_on"] = TODAY
    l.update(desc=text[:DESC_CAP], desc_hash=h, hash_kind=kind, checked_on=TODAY)
    l.pop("page_gone", None)
    for k, v in zip(("deadline", "deadline_quote"), find_deadline(text, l.get("first_seen") or TODAY) or (None, None)):
        l[k] = v
    l["pay"] = find_pay(text)
    for k in ("deadline", "deadline_quote", "pay"):
        if not l[k]:
            del l[k]


def job_key(l):
    """Company + site + job number from the link, so a posting whose title or location was edited (a new listing id)
    is recognized as the same job."""
    p = urlparse(l.get("url") or "")
    if not p.netloc:
        return None
    segs = [x for x in p.path.split("/") if x]
    ids = re.findall(r"\d{5,}", segs[-1]) if segs else []
    ids = ids or [v for vs in parse_qs(p.query).values() for v in vs if re.fullmatch(r"\d{5,}", v)]
    return (norm(l.get("company", "")), p.netloc.lower(), ids[-1] if ids else (p.path.lower().rstrip("/") + "?" + p.query))


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
    if not is_us(row.get("location", "")) or FOREIGN.search(title):  # "Sales Intern (Riyadh, Saudi Arabia)"
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
    elif comp_entry.get("sales_only"):  # on your list for sales/FAE only (non-semiconductor companies)
        if lane != "sales" or NOT_ECE.search(title) or NONTECH_SALES.search(title):
            return None
    if grad_only(title, "", row.get("degrees", [])):  # the description is checked after merging (see main)
        return None
    coop = bool(re.search(r"\bco-?op\b", title, re.I))
    return {"company": company, "title": title, "location": row.get("location", ""), "url": row.get("url", ""),
            "term": term, "coop": coop, "lane": lane, "desc": (row.get("desc") or "")[:DESC_CAP],
            "tier": comp_entry["tier"] if comp_entry else "", "segment": comp_entry["segment"] if comp_entry else "",
            "known": bool(comp_entry), "detail": row.get("detail"), "degrees": row.get("degrees", []),
            "_full": (row.get("desc") or "") if row.get("full") else "", "_kind": row.get("kind")}


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
                cur.update({k: l[k] for k in ("url", "detail", "_full", "_kind") if l.get(k)})
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
        results = list(pool.map(run, jobs))
    retry = [r[0] for r in results if r[2]]
    if retry:  # a site that rate-limited or dropped us mid-run usually answers a minute later, one feed at a time
        print(f"retrying {len(retry)} failed feeds in 60s", flush=True)
        time.sleep(60)
        again = {r[0][2]: r for r in map(run, retry)}
        results = [again.get(r[0][2], r) for r in results]
    for (c, s, name), rows, err, secs in results:
        print(f"{'ok ' if not err else 'ERR'} {secs:>5}s {len(rows):>4} rows  {name}" + (f"  {err}" if err else ""), flush=True)
        if err:
            failed_sources.add(name)
            status.append({"name": name, "company": c["name"], "ok": False, "error": err})
            continue
        for r in rows:
            r.setdefault("kind", s["type"])
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

    # same job, new id: a title or location edit changes the id, so match tonight's new ids to last night's
    # vanished ones by job link and carry the history over (the old id goes in prev_ids for the dashboard)
    vanished = {}
    for lid, old in prev.items():
        k = lid not in current and job_key(old)
        if k:
            vanished[k] = None if k in vanished else lid  # two candidates: ambiguous, carry neither
    carried = {}
    for lid, l in current.items():
        old_id = lid not in prev and vanished.pop(job_key(l), None)  # pop: one old listing carries to one new id
        if old_id:
            carried[lid] = prev[old_id]
    carried_old = {o["id"] for o in carried.values()}
    history = lambda lid: prev.get(lid) or carried.get(lid) or {}

    # job pages: read new postings in full, confirm ones missing from tonight's search, and re-read the rest
    # (oldest read first, sales and P1 first) so edits, deadlines and closings show up. Boards that list full
    # descriptions (Greenhouse, Lever, Ashby, Jibe) were read in full by the search itself.
    rank = lambda l: -({"sales": 30, "swe": 20, "hw": 10}.get(l["lane"], 0) + {"P1": 3, "P2": 2, "P3": 1}.get(l.get("tier"), 0))
    tasks = []
    for lid, l in current.items():
        if l.get("inactive") or len(l["_full"]) >= 80:
            continue
        d = l.get("detail") or detail_from_url(l.get("url"))
        old = history(lid)
        if d:  # Workday postings read before the country was kept go first, so foreign ones drop out in one night
            new = not old.get("desc_hash") or (d[0] == "workday" and not old.get("country"))
            tasks.append((FILL, rank(l), l, d) if new else (RECHECK, (old.get("checked_on") or "", rank(l)), l, d))
    for lid, old in prev.items():
        if lid in current or lid in carried_old or old["status"] != "open" or (only and norm(old["company"]) not in only) \
                or any(s in failed_sources for s in old.get("sources", [])):
            continue
        d = detail_from_url(old.get("url"))
        if d:
            tasks.append((CONFIRM, rank(old), old, d))
    t0 = time.time()
    st = fetch_details(tasks, CFG.get("detail_fetch_cap", 600), CFG.get("recheck_per_host", 40), CFG.get("detail_time_budget_s", 480))
    tot = sum(st.values(), Counter())
    print(f"job pages: {len(tasks)} queued, {tot['read']} read, {tot['gone']} gone, {tot['empty']} empty, {tot['failed']} failed, "
          f"{tot['blocked']} blocked, {tot['skipped']} left for next run ({time.time() - t0:.0f}s)", flush=True)
    for host, c in sorted(st.items(), key=lambda kv: -(kv[1]["failed"] + kv[1]["blocked"])):
        if c["failed"] or c["blocked"]:
            print(f"  {host}: {dict(c)}", flush=True)

    # merge with history
    listings = []
    for lid, l in current.items():
        if l.get("inactive") and lid not in prev and lid not in carried:
            continue  # closed before we ever saw it
        old = history(lid)
        l["first_seen"] = old.get("first_seen", TODAY)
        l["last_seen"] = TODAY
        l["missed"] = 0
        l["status"] = "closed" if l.pop("inactive", False) else "open"
        if l["status"] == "closed":
            l["closed_on"] = old.get("closed_on", TODAY)
        if lid in carried:
            l["prev_ids"] = (old.get("prev_ids") or [])[-4:] + [old["id"]]
            if (old["title"], old["location"]) != (l["title"], l["location"]):
                l["was"] = {"title": old["title"], "location": old["location"], "on": TODAY}
        for k in ("prev_ids", "was"):
            if old.get(k) and not l.get(k):
                l[k] = old[k]
        apply_text(l, old)
        listings.append(l)
    configured = {feed_name(c, s) for c in COMPANIES for s in c.get("sources", [])} | {r["name"] for r in CFG["repos"]}
    for lid, old in prev.items():
        if lid in current or lid in carried_old:
            continue
        if old.get("sources") and not any(s in configured for s in old["sources"]):
            continue  # every source behind it was removed from config (e.g. a feed that was the wrong company)
        if not is_us(old.get("location", "")):
            continue  # outside the US by tonight's location rules (e.g. "SGP - Woodlands" from before they caught it)
        if only and norm(old["company"]) not in only:
            listings.append(old)
            continue
        if any(s in failed_sources for s in old.get("sources", [])):
            listings.append(old)  # its source broke tonight: don't call it closed
            continue
        gone = old.get("_gone")
        apply_text(old, dict(old))
        old["missed"] = old.get("missed", 0) + 1
        if old["status"] == "open" and (old["missed"] >= CFG.get("close_after_missed_runs", 2) or gone):
            old["status"], old["closed_on"] = "closed", TODAY  # missing from search and its page is gone: closed now
        if old["status"] == "closed" and old.get("closed_on", TODAY) < (NOW.date() - dt.timedelta(days=30)).isoformat():
            continue
        listings.append(old)

    # Off-list companies keep only technical sales roles. Foreign, grad-only and clearance postings are dropped, except
    # ones already shown before: those stay, marked "excluded", so a saved role whose posting changed says why.
    kept = []
    for l in listings:
        if not (l.get("known") or (l["lane"] == "sales" and ECE_SALES.search(l["title"]))):
            continue
        why = ("Outside the US" if l.get("country", "US") != "US"
               else "Grad students only" if grad_only(l["title"], l.get("desc", ""), l.get("degrees", []))
               else "Needs a security clearance" if needs_clearance(l.get("desc", "")) else None)
        if why and l["id"] not in prev and not l.get("prev_ids"):
            continue
        if why:
            l["excluded"] = why
        else:
            l.pop("excluded", None)
        kept.append(l)
    listings = kept
    for l in listings:
        for k in ("detail", "_full", "_kind", "_gone", "_country"):
            l.pop(k, None)
        l["bay"] = bool(BAY.search(l.get("location", "")))
        l["flags"], l["tags"] = eligibility(l["title"], l.get("desc", ""))
        if "Master's" in l.get("degrees", []) or re.search(r"\b(ms|m\.s\.|master'?s)\b", l["title"], re.I):
            l["tags"].append("BS/MS")
        l["score"] = score(l)
    listings.sort(key=lambda l: (l["status"] != "open", -l["score"], l["company"]))

    (ROOT / "data").mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"generated_at": NOW.isoformat(timespec="seconds"),
                                    "count": sum(l["status"] == "open" and not l.get("excluded") for l in listings),
                                    "listings": listings}, separators=(",", ":")))
    # job-page health, so a site that starts blocking (and its postings going stale) is visible on the dashboard
    open_ = [l for l in listings if l["status"] == "open"]
    unreadable = Counter(h for l in open_ if (h := urlparse(l["url"]).netloc) in UNREADABLE)
    stale_by = Counter(h for l in open_ if (h := urlparse(l["url"]).netloc) not in UNREADABLE
                       if (l.get("checked_on") or "") < (NOW.date() - dt.timedelta(days=3)).isoformat())
    pages = {**{k: tot[k] for k in ("read", "gone", "empty", "failed", "blocked", "skipped")}, "queued": len(tasks),
             "changed": sum(l.get("desc_changed_on") == TODAY for l in open_), "unread_3d": sum(stale_by.values()),
             "unread_3d_by_site": dict(stale_by.most_common(12)),
             "unreadable": {h: {"open": n, "why": UNREADABLE[h]} for h, n in unreadable.items()},
             "problems": {h: {k: c[k] for k in ("failed", "blocked") if c[k]} for h, c in st.items() if c["failed"] or c["blocked"]}}
    (ROOT / "data/sources.json").write_text(json.dumps({"generated_at": NOW.isoformat(timespec="seconds"),
                                                       "sources": status, "pages": pages}, indent=1))
    ok = sum(s["ok"] for s in status)
    print(f"{len(listings)} listings ({sum(l['status'] == 'open' for l in listings)} open); sources ok {ok}/{len(status)}")


if __name__ == "__main__":
    main()
