#!/usr/bin/env python3
"""Fill in career-site feeds for companies in config/companies.json by reading the apply
links in the Simplify listings. Run by hand when you add companies:
    python collector/discover_sources.py
Only companies with no "sources" yet (or "auto": true) are touched."""
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
    if "icims=1" in (p.query or "") and parts and parts[0] == "jobs":
        return {"type": "jibe", "host": host}
    return None


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
        if c.get("sources") and not c.get("auto"):
            continue
        if c["name"] in found:
            c["sources"] = [dict(json.loads(k), verified="seen in Simplify apply links")
                            for k, _ in found[c["name"]].most_common(3)]
            c["auto"] = True
            changed += 1
    cfg_path.write_text(json.dumps(companies, indent=1))
    print(f"updated {changed} companies")


if __name__ == "__main__":
    main()
