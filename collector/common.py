"""Shared helpers: HTTP, text cleanup, company matching, classification."""
import html, json, re
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (personal internship dashboard)",
      "Accept": "application/json, text/plain, */*"}


def http(url, data=None, headers=None, timeout=30):
    h = dict(UA)
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, headers=h), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def jget(url, **kw):
    return json.loads(http(url, **kw))


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def norm(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(inc|corp|corporation|co|ltd|llc|plc|technologies|technology|the|usa|us|america)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class CompanyIndex:
    """Maps a company string from a repo or ATS to an entry in companies.json."""

    def __init__(self, companies):
        self.items = []
        for c in companies:
            for a in [c["name"]] + c.get("aliases", []):
                n = norm(a)
                if n:
                    self.items.append((n, c))
        self.items.sort(key=lambda x: -len(x[0]))

    def find(self, name):
        n = norm(name)
        if not n:
            return None
        for a, c in self.items:
            if n == a or (len(a) >= 4 and n.startswith(a + " ")):
                return c
        return None


DEFENSE = re.compile(r"\b(lockheed|raytheon|rtx|northrop|general dynamics|l3harris|l3 harris|bae systems|anduril|"
                     r"leidos|saic|booz allen|mitre|sandia|los alamos|lawrence livermore|national lab\w*|draper|"
                     r"lincoln lab\w*|nswc|naval|navy|air force|army|darpa|shield ai|mercury systems|huntington ingalls|"
                     r"textron|palantir|parsons|caci|peraton|sierra nevada|kbr|argonne|sri international)\b", re.I)

INTERN = re.compile(r"\b(intern|interns|internship|co-?op|coop|student|apprentice)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|manager|director|head of)\b", re.I)
SALES = re.compile(r"\b(sales|field applications?|fae|applications? engineer\w*|application engineering|customer (success )?engineer\w*|"
                   r"technical marketing|product marketing|business development|solutions? (engineer|architect)\w*|pre-?sales|"
                   r"account (manager|executive)|technical sales|marketing engineer)\b", re.I)
SWE = re.compile(r"\b(software|firmware|embedded|swe|developer|devops|systems software|test automation|automation engineer|"
                 r"validation engineer|tools engineer|bsp|linux|kernel|full ?stack|back ?end|front ?end|programmer)\b", re.I)
HW = re.compile(r"\b(hardware|asic|soc|rtl|fpga|verification|design|silicon|analog|mixed[- ]signal|layout|circuit|electrical|"
                r"signal integrity|power|product engineer\w*|test engineer\w*|process engineer\w*|yield|reliability|failure analysis|"
                r"packaging|device|semiconductor|photonics|rf|validation|characterization|equipment|field service|"
                r"ee|ece|electronics|engineering intern|engineer intern|technical intern|intern - technical)\b", re.I)
OFFTRACK = re.compile(r"\b(mechanical|chemical|chemist|chemistry|materials scien\w*|industrial engineer\w*|civil|biomedical|"
                      r"manufacturing engineer\w*|facilities|ehs|supply chain|logistics|finance|accounting|data scien\w*|"
                      r"machine learning|human resources|recruit\w*|legal|communications|graphic|ux|procurement|"
                      r"tax|audit|optical engineer\w*|process technician)\b", re.I)

BAY = re.compile(r"\b(san jose|santa clara|sunnyvale|milpitas|mountain view|palo alto|san francisco|fremont|cupertino|"
                 r"menlo park|redwood city|livermore|pleasanton|san mateo|hayward|oakland|berkeley|campbell|los gatos|"
                 r"foster city|union city|emeryville|los altos|morgan hill|san ramon|walnut creek|burlingame|"
                 r"san carlos|alameda|bay area|silicon valley)\b", re.I)
STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
FOREIGN = re.compile(r"\b(canada|india|china|taiwan|japan|korea|germany|france|united kingdom|england|israel|singapore|"
                     r"malaysia|philippines|vietnam|mexico|ireland|netherlands|poland|italy|spain|switzerland|austria|"
                     r"czech|bangalore|bengaluru|hyderabad|shanghai|beijing|toronto|ontario|quebec|munich|penang|hsinchu|"
                     r"tokyo|seoul|paris|london|amsterdam|gdansk|kaohsiung|noida)\b", re.I)


def is_us(loc):
    """Unknown or ambiguous locations are kept; clearly foreign ones are dropped."""
    if not loc:
        return True
    for m in re.finditer(r"(?:,|-)\s*([A-Z]{2})\b", loc):
        if m.group(1) in STATES:
            return True
    if re.search(r"united states|\busa\b|\bu\.s\.|remote", loc, re.I) or BAY.search(loc):
        return True
    return not FOREIGN.search(loc)


def lane_of(title):
    """Sales only counts in the role part of the title, so a team name like
    'Data Engineer - Applications Engineering' is not mistaken for an FAE role."""
    main = re.split(r"\s[-\u2013|,(]\s?", title)[0]
    if SALES.search(main) or (main.strip().lower() in ("intern", "internship", "co-op") and SALES.search(title)
                              and not SWE.search(title)):
        return "sales"
    if SWE.search(title):
        return "swe"
    if HW.search(title):
        return "hw"
    return "other"


TERM = re.compile(r"\b(summer|fall|spring|winter|autumn)\s*'?(?:20)?(2[5-9])\b", re.I)


def term_of(text):
    """Returns a 2027 term, 'Unspecified', or None (wrong year, drop it)."""
    text = text or ""
    found = [(m.group(1).title().replace("Autumn", "Fall"), 2000 + int(m.group(2))) for m in TERM.finditer(text)]
    if not found:
        if re.search(r"\b2026\b", text) and not re.search(r"\b2027\b", text):
            return None
        return "Unspecified"
    keep = [f"{s} {y}" for s, y in found if y == 2027]
    return keep[0] if keep else None


def eligibility(title, desc):
    t = f"{title}\n{desc or ''}"
    flags, tags = [], []
    gy = set()
    for m in re.finditer(r"graduat\w*[^.\n]{0,80}", t, re.I):
        gy |= {int(y) for y in re.findall(r"\b(202[6-9]|2030)\b", m.group(0))}
    if gy and 2029 not in gy and max(gy) <= 2028:
        flags.append(f"Grad {'/'.join(str(y)[2:] for y in sorted(gy))} only?")
    if re.search(r"\b(freshm[ae]n|sophomores?|first[- ]year|second[- ]year)\b", t, re.I):
        tags.append("Soph OK")
    elif re.search(r"\b(rising )?(juniors?|seniors?)\b[^.\n]{0,40}(standing|year|or (above|higher))|\b(third|fourth|final)[- ]year\b|\brising seniors?\b", t, re.I):
        flags.append("Junior+?")
    g = re.search(r"\b([2-4]\.\d{1,2})\s*(?:/\s*4\.0\s*)?(?:or (?:higher|above|better)\s*)?(?:cumulative\s*)?(?:gpa|grade point)", t, re.I) or \
        re.search(r"\bgpa\b[^.\n\d]{0,30}([2-4]\.\d{1,2})", t, re.I)
    if g:
        v = float(g.group(1))
        (flags if v > 3.86 else tags).append(f"GPA {v:.2f}")
    if re.search(r"\b(ph\.?d|master'?s|ms student|mba|graduate student)\b", title, re.I):
        flags.append("Grad degree")
    if re.search(r"\b(security clearance|clearance required|active secret|ts/sci)\b", t, re.I):
        flags.append("Clearance")
    return flags, tags


# For companies not on the list: only technical customer-facing roles count as "sales"
ECE_SALES = re.compile(r"\b(field applications?|fae|applications? engineer\w*|sales engineer\w*|technical sales|"
                       r"technical marketing|marketing engineer|customer engineer\w*|product applications)\b", re.I)
NOT_ECE = re.compile(r"\b(analyst|analytics|reporting|data|banking|bank|investments?|insurance|financial|retail|"
                     r"service sales|business analyst|crm|salesforce admin)\b", re.I)
SEMI_NAME = re.compile(r"\b(semiconductor\w*|semi|micro\w*|electronics?|silicon|devices|instruments|photonics|"
                       r"circuits?|chips?|logic|wafer|fab)\b", re.I)
