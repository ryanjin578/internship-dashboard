"""Shared helpers: HTTP, text cleanup, company matching, classification."""
import html, json, re, ssl, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (personal internship dashboard)",
      "Accept": "application/json, text/plain, */*"}


RETRY_CODES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}  # rate limits, gateway and Cloudflare hiccups


def http(url, data=None, headers=None, timeout=25, retries=2):
    """GET (or POST JSON). Rate limits, 5xx and dropped connections are retried after 3s, then 10s (or the site's
    Retry-After, up to 60s); `retries=0` fails fast."""
    h = dict(UA)
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    for n in range(retries + 1):
        try:
            with urlopen(Request(url, data=body, headers=h), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except HTTPError as e:
            if e.code not in RETRY_CODES or n == retries:
                raise
            wait = e.headers.get("Retry-After", "")
            time.sleep(min(int(wait), 60) if wait.isdigit() else (3, 10)[min(n, 1)])
        except (URLError, ConnectionError, TimeoutError) as e:
            if n == retries or isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise
            time.sleep((3, 10)[min(n, 1)])


def jget(url, **kw):
    return json.loads(http(url, **kw))


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", s, flags=re.I | re.S)
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
FOREIGN = re.compile(r"\b(canada|india|china|taiwan|japan|korea|germany|france|united kingdom|uk|england|israel|singapore|"
                     r"malaysia|philippines|vietnam|(?<!new )mexico|brazil|australia|ireland|netherlands|poland|italy|spain|switzerland|austria|"
                     r"czech|bangalore|bengaluru|hyderabad|shanghai|beijing|toronto|ontario|quebec|munich|penang|hsinchu|"
                     r"tokyo|seoul|paris|london|amsterdam|gdansk|kaohsiung|noida|norway|sweden|denmark|finland|belgium|scotland|"
                     r"hungary|romania|portugal|greece|turkey|t[uü]rkiye|thailand|indonesia|new zealand|chile|argentina|costa rica|"
                     r"hong kong|macau|armenia|ukraine|serbia|slovakia|estonia|united arab emirates|dubai|yerevan|trondheim|budapest|saudi arabia|riyadh|qatar|egypt|south africa|"
                     r"british columbia|b\.c\.|kanata|ottawa|montreal|petah[- ]tikva|tel aviv|haifa|belo horizonte|gratkorn|graz|villach|"
                     r"nijmegen|eindhoven|da nang|ho chi minh|tianjin|suzhou|shenzhen|bucharest|sibiu|brno|glasgow|edinburgh|madrid|barcelona|"
                     r"bangkok|istanbul|aveiro|selangor|petaling jaya|pasir gudang|kuala lumpur|dresden|regensburg|grenoble)\b", re.I)


def _is_us_part(loc):
    if re.search(r"united states|\busa\b|\bu\.s\.", loc, re.I):
        return True
    iso3 = re.match(r"([A-Z]{3})\s+-\s", loc)  # Workday sites that lead with a country code: "SGP - Woodlands"
    if iso3:
        return iso3.group(1) == "USA"
    if FOREIGN.search(loc):  # before state codes: "Munich, DE" is not Delaware
        return False
    code = re.search(r",\s*([A-Za-z]{2})\s*$", loc)  # "Pasir Gudang, MY", "Dublin, County Dublin, IE", "Seongnam-si, kr"
    if code and code.group(1).upper() not in STATES | {"US"}:
        return False
    return True


def is_us(loc):
    """Unknown or ambiguous locations are kept; clearly foreign ones are dropped. A list of locations
    ("Santa Clara, CA; Toronto, ON" or "San Jose, CA or Toronto") is kept when any of them is in the US."""
    if not loc:
        return True
    return any(_is_us_part(p) for p in re.split(r";|\sor\s", loc) if p.strip())


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
# For "sales_only" companies on the list (outside semiconductors): any sales/FAE role except non-technical desk sales
NONTECH_SALES = re.compile(r"\b(inside sales|sales development|business development rep\w*|bdr|sdr|sales operations|sales ops|"
                           r"sales enablement|sales analyst|revenue|retail|clinical)\b", re.I)
SEMI_NAME = re.compile(r"\b(semiconductor\w*|semi|micro\w*|electronics?|silicon|devices|instruments|photonics|"
                       r"circuits?|chips?|logic|wafer|fab)\b", re.I)


GRAD = re.compile(r"\b(ph\.?\s?d|doctoral|master'?s|m\.s\.|ms|mba|graduate students?|meng)\b", re.I)
UNDERGRAD = re.compile(r"\b(bachelor'?s|b\.s\.|bs|bsee|bsce|undergrad\w*|b\.e\.)\b", re.I)
# A grad degree stated as what the student is or must be: "Currently enrolled in an EE (or related) MS or PhD program",
# "Master's student in EE", "Master or PhD graduate candidates", "MSEE", "requires a Masters or PhD"
_GDEG = r"(ph\.?\s?d|doctoral|masters?|master's|m\.s\.|(?-i:MS)|msc|m\.sc\.?|msee|mscs|graduate degree|graduate program)"
GRAD_REQ = re.compile(
    rf"\b(pursuing|enrolled|enrollment|working towards?|candidate for|completing|completion of|studying|requires?|required|must be)\b"
    rf"[^.\n]{{0,60}}\b{_GDEG}(?![\w-])"
    rf"|\b{_GDEG}( or (ph\.?\s?d|doctoral|master'?s?))?( graduate)? (students?|candidates?|thesis|programs?|level)\b"
    rf"|\bgraduate (students?|candidates?)\b|\b(masters?|master's|ph\.?\s?d)('s)? (degree|in|of science)\b|\bmsee\b", re.I)
# Anything a BS student fits: "Bachelor's or Master's", "B.S./M.S.", "undergraduate", class years. A finished BS
# ("Completed a B.S. degree ... Graduate student") doesn't count; "pursuing or recently completed a BS" does.
UNDER_OK = re.compile(r"(?<!(?<!recently )completed a )(?<!(?<!recently )completed an )\b(bachelor'?s?|undergrad\w*|b\.s\.|(?-i:BS)(EE|CS|CE)?|bsc|b\.e\.|b\.tech|"
                      r"freshm[ae]n|sophomores?|juniors?)(?!\w)", re.I)
NOT_REQUIRED = re.compile(r"prefer|\bplus\b|bonus|compensation|\bpay\b|nice to have|desired|ideal", re.I)


def grad_only(title, desc="", degrees=()):
    """True when an undergrad (BS) student can't apply."""
    if degrees and "Bachelor's" not in degrees and any(d in degrees for d in ("Master's", "PhD", "MBA")):
        return True
    if UNDERGRAD.search(title):
        return False
    if GRAD.search(title):
        return True
    if not desc or UNDER_OK.search(desc):
        return False
    for m in GRAD_REQ.finditer(desc):  # "A masters or doctoral candidate is preferred" leaves the door open
        a = max(desc.rfind(c, 0, m.start()) for c in ".!?\n") + 1
        b = min((i for i in (desc.find(c, m.end()) for c in ".!?\n") if i >= 0), default=len(desc))
        if not NOT_REQUIRED.search(desc[a:b]):
            return True
    return False


def needs_clearance(text):
    return bool(re.search(r"\b(security clearance|clearance required|active secret|ts/sci|top secret)\b", text or "", re.I))
