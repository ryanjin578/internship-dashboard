#!/usr/bin/env python3
"""Fill in career-site feeds for companies in config/companies.json by reading the apply
links in the Simplify listings. Run by hand when you add companies:
    python collector/discover_sources.py
Only companies marked "auto": true are touched; give a new company "auto": true to let this fill it in.
Hand-checked entries (no "auto", including ones with "sources": [] on purpose) are never changed."""
import json, re, sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, CompanyIndex, jget

SIMPLIFY = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"


def source_from_url(u):
    p = urlparse(u)
    host, parts = p.netloc.lower(), [x for x in p.path.split("/") if x]
    if re.match(r"en-?us", parts[0] if parts else "", re.I):
        parts = parts[1:]
    m = re.match(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if m and parts:
        return {"type": "workday", "host": host, "tenant": m.group(1), "site": parts[0]}
    if re.match(r"wd\d+\.myworkdaysite\.com$", host) and len(parts) >= 3 and parts[0] == "recruiting":
        return {"type": "workday", "host": host, "tenant": parts[1], "site": parts[2]}
    if "greenhouse.io" in host and parts:
        tok = parts[0] if parts[0] != "embed" else None
        return {"type": "greenhouse", "board": tok} if tok else None
    if host == "jobs.smartrecruiters.com" and parts:
        return {"type": "smartrecruiters", "company": parts[0]}
    if host.endswith("oraclecloud.com") and "sites" in parts:
        return {"type": "oracle", "host": host, "site": parts[parts.index("sites") + 1]}
    if host.endswith(".eightfold.ai"):
        t = host.split(".")[0]
        return {"type": "eightfold", "host": host, "domain": f"{t}.com"}
    if host == "jobs.ashbyhq.com" and parts:
        return {"type": "ashby", "org": parts[0]}
    if host == "jobs.lever.co" and parts:
        return {"type": "lever", "company": parts[0]}
    if host == "jobs.eu.lever.co" and parts:
        return {"type": "lever", "company": parts[0], "api": "api.eu.lever.co"}
    if host == "jobs.jobvite.com" and parts:
        return {"type": "jobvite", "company": parts[0]}
    if "icims=1" in (p.query or "") and parts and parts[0] == "jobs":
        return {"type": "jibe", "host": host}
    if host.endswith(".icims.com") and len(parts) >= 2 and parts[0] == "jobs" and parts[1].isdigit():
        return {"type": "icims", "host": host}
    if len(parts) == 3 and parts[0] == "careers" and parts[1] == "job" and parts[2].isdigit():
        # Eightfold on a company domain: careers.qualcomm.com/careers/job/446718947455
        return {"type": "eightfold", "host": host, "domain": ".".join(host.split(".")[-2:])}
    if len(parts) == 5 and parts[0] == "job" and parts[3].isdigit() and parts[4].isdigit():
        # TalentBrew: careers.synopsys.com/job/<city>/<title>/<org id>/<job id>
        return {"type": "talentbrew", "host": host}
    if len(parts) == 3 and parts[0] == "job" and re.fullmatch(r"\d+(-[a-z]{2}_[A-Z]{2})?", parts[2]):
        # SuccessFactors: seagatecareers.com/job/<city-title-ST-zip>/1234/ or /job/<title>/78488-en_US
        return {"type": "successfactors", "host": host}
    return None


def dedupe_key(s):
    """Workday site names are case-insensitive and some tenants answer on two hosts (KLA search/Search,
    Avnet wd1 vs myworkdaysite): treat those as the same feed."""
    if s["type"] == "workday":
        return ("workday", s["tenant"].lower(), s["site"].lower())
    return tuple(sorted(s.items()))


def main():
    cfg_path = ROOT / "config/companies.json"
    companies = json.loads(cfg_path.read_text())
    idx = CompanyIndex(companies)
    found = {}
    for x in jget(SIMPLIFY):
        c = idx.find(x.get("company_name", ""))
        if not c:
            continue
        s = source_from_url(x.get("url", ""))
        if s:
            found.setdefault(c["name"], Counter())[json.dumps(s, sort_keys=True)] += 1
    changed = 0
    for c in companies:
        if not c.get("auto") or c["name"] not in found:
            continue
        picked, keys = [], set()
        for k, _ in found[c["name"]].most_common():
            s = json.loads(k)
            if dedupe_key(s) not in keys and len(picked) < 3:
                keys.add(dedupe_key(s))
                picked.append(dict(s, verified="seen in Simplify apply links"))
        c["sources"] = picked
        changed += 1
    cfg_path.write_text(json.dumps(companies, indent=1))
    print(f"updated {changed} companies")


if __name__ == "__main__":
    main()
