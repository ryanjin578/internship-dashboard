# Summer 2027 internship dashboard

Personal dashboard. A free GitHub Action collects internship listings every night; the page shows them next to your apply-later list. Your statuses, contacts, resume, and API key live only in your browser.

## Setup (about 10 minutes)
1. Create a **public** repo (e.g. `internship-dashboard`) and upload everything in this folder, keeping the folders (`.github/`, `collector/`, `config/`, `data/`).
   The `.github` folder is hidden on Mac: press Cmd+Shift+. in Finder to see it.
2. **Settings > Actions > General > Workflow permissions:** choose *Read and write permissions*, Save.
3. **Actions tab:** enable workflows, open *Nightly collect*, click **Run workflow**. Wait ~5 min.
4. **Settings > Pages:** Source *Deploy from a branch*, branch `main`, folder `/ (root)`, Save. Your site appears at `https://<username>.github.io/internship-dashboard/` in a minute or two.
5. Open the site > **Settings**: paste your resume as text and your Anthropic API key (console.anthropic.com > API keys; set a monthly spend limit under Billing).

After that it refreshes nightly at 5am Eastern. To refresh now: Actions > Nightly collect > Run workflow.

## Files
- `collector/collect.py`: nightly run. Repos + official career sites, filters, dedupes, writes `data/listings.json` and `data/sources.json` (feed health).
- `collector/discover_sources.py`: finds career-site feeds from Simplify apply links for companies marked `"auto": true`.
- `collector/history.py`: run by hand every month or two. Fills each company's `last_cycle` (when last summer's postings opened and roughly when half had closed) and `this_cycle` from Simplify's posting history. The Companies tab's Timing and Deadline columns use these.
- `config/companies.json`: your company list (tier, segment, notes, feeds). Defense and national labs removed. `"sales_only": true` keeps only sales / FAE roles from that company (used for companies outside semiconductors); `"deadline"` is an application deadline the company stated.
- `config/sources.json`: which GitHub repos to read.
- `index.html`: the dashboard.

## Closed roles
The nightly run marks a listing closed once it's missing from 2 runs in a row (or 1, if its page is also gone) and drops it 30 days later; Openings only shows open ones. A role you saved remembers when its posting closed, so it keeps the "Posting closed" tag after the listing is gone. **Clean up** on the board lists saved roles you haven't applied to whose posting closed or whose deadline passed, and removes the ones you tick. Roles you applied to stay. On each load the page also drops browser data (hidden marks, Claude matches, pasted descriptions) for postings that are gone and not saved.

## How Apply later stays accurate
- **Job pages are re-read nightly.** Boards that list full descriptions (Greenhouse, Lever, Ashby, Jibe) are read in full by the search itself. For every other posting the run reads its job page: new ones first, then ones missing from that night's search, then the rest, oldest read first. Each site gets at most 40 re-reads a night (`recheck_per_host` in `config/sources.json`), one request at a time. A site is left alone until the next night after a 429 or 3 failures in a row. Sites that block scripts (e.g. Tesla) never get read; their roles say "Text not read".
- **What a read updates:** the description (stored up to 8,000 characters; the hash, deadline and pay come from the full text), `checked_on`, `desc_changed_on` (only when the text really changed, ignoring whitespace, punctuation and "Posted 3 days ago"), `deadline` + `deadline_quote` (only from application wording like "applications accepted until", with the date in the same sentence; the quote is shown so you can check it), and `pay`.
- **Closings:** a posting missing from 2 nightly searches closes. If it's missing once and its page also says it's gone (404, empty record, "no longer available"), it closes that night. A page that's gone while the search still lists it shows "Page gone, may be closed".
- **Renamed postings:** a title or location edit gives a listing a new id. The run matches it to the old one by job link and records `prev_ids` and `was`; the dashboard moves your saved role to the new id instead of calling it closed. A closed role with an open posting of the same title at the same company shows "Reposted?" and offers to switch.
- **Eligibility edits:** a posting you could see before that now reads grad-only or clearance-only stays in the data marked `excluded`, so your saved role says why instead of vanishing.
- **Changes since you saved:** the page keeps a snapshot of each saved role's posting (in this browser; backups include it) and flags differences in title, location, term, stated deadline, pay, eligibility and text, with a line diff. **Mark reviewed** makes the current version the new baseline. Roles saved before this existed start tracking from the first load. A Claude match made on an older version of the text says "match again".
- **Dates say where they came from:** "You set it", "Stated in posting" (hover for the sentence), or the Companies tab's stated / last-year date. If your date and the posting's differ, both show.
- **Not tracked:** roles you added by hand that aren't linked to a listing say so; "I checked the posting today" records when you last looked.
- **Stale data:** if the last nightly run is more than 36 hours old, a banner says so. GitHub turns off scheduled workflows in a repo with no commits for 60 days; the nightly commits normally prevent that, but if the banner appears, check the Actions tab.
- The workflow allows one run at a time and rebases onto anything you pushed during the run, so a night's data isn't lost to a rejected push.

## Status of the career-site feeds
As of 2026-10-12: 125 of 186 companies have an official feed. The 52 companies added on 2026-10-12 each had a US sales/FAE internship (or, for chip companies, any CompE internship) posted in the last two years; the evidence is in each entry's `notes`. Their feeds passed a live test except Siemens and Endress+Hauser (certificate errors on the setup PC only). As of 2026-10-06 the original 134 all passed except Siemens EDA. The dashboard header shows any feed that fails in the nightly run. Companies without a feed still appear through the GitHub repos.

**Working feeds, by site type**
- **Workday:** Micron, Intel, AMAT, KLA, ASML, ADI, Microchip, GlobalFoundries, Cadence, Arrow, Avnet, Marvell, NVIDIA, NXP, MPS, Allegro, Semtech, Samsung, Cisco, Lumentum, SiFive, Onto, Axcelis, Azenta, MKS, Entegris, Ambarella, Polar, Edwards, Air Liquide, Aptiv, Tokyo Electron, Broadcom, Silicon Labs, FormFactor, Digi-Key
- **Eightfold:** Qualcomm, Lam Research, Infineon, Tektronix (Ralliant)
- **SuccessFactors:** Teradyne, Skyworks, Qorvo, Seagate, Advanced Energy, Vicor, Veeco, ZF
- **Oracle:** TI, onsemi, Coherent, Kulicke & Soffa, Cohu, NI
- **Greenhouse / Lever / Ashby:** Astera Labs, SK hynix, Integra, Lucid, Tenstorrent, Waymo, Nuro, SambaNova, indie, Zoox, Aeva, Cirrus Logic (Lever EU), Rivian, Cerebras
- **SmartRecruiters:** Renesas, Western Digital, Solidigm, Kioxia, Arista, Bosch Roseville
- **Other:** AMD, Keysight, Rivian (iCIMS-backed Jibe), Synopsys and Arm (TalentBrew), Rambus (iCIMS), Power Integrations (Jobvite)
- **Untested:** Siemens EDA (Avature, keeps titles containing "EDA"). It failed a certificate check on the PC used to set it up; check the dashboard after a nightly run.

**No feed, and why**
- **Turned off on purpose:** Juniper (Simplify linked a different company's board; Juniper is now part of HPE, which has its own sales-only entry).
- **Added 2026-10-12, check by hand:** Schneider Electric, Trane, Amphenol, Mitsubishi Electric Automation, Lincoln Electric, AWS, Google Cloud, SiMa.ai, Atomic Semi (custom career sites, or a job board that disappeared).
- **Blocks scripts or login-only:** Apple, Tesla, IBM (bot challenge), Mobileye, Luminar, Aurora
- **Wrong company under the same name:** Nova, Nordic Semiconductor (`nordic` on Workday is Nordic Consulting), DuPont Electronics (now Qnity; `dupont` is DuPont proper), Tower Semiconductor
- **Absorbed:** Ansys (Synopsys), Alphawave (Qualcomm), Untether AI (AMD), Richardson RFPD (Arrow)
- **Workday account found, site name unknown:** Wolfspeed (`cree` tenant on wd108), TTI, Lattice (its iCIMS site is gone), Silvaco
- **No machine-readable job site found:** SkyWater, TSMC Arizona, ST, Diodes, Navitas, Credo, Mouser, Amkor, ASE, MACOM, Ampere, Groq, Ambiq, Advantest, UCT, Ichor, Rohde & Schwarz, MediaTek, Ouster, Imagination, X-FAB, EV Group, Camtek, SUSS MicroTec, Linde, Shin-Etsu, SUMCO, Element Solutions, Chroma ATE, Deca, Promex, Future Electronics
- **Duplicate:** Bosch (semiconductor/auto) would reuse Bosch Roseville's `BoschGroup` board and list every job twice

**Adding companies:** give a new entry `"auto": true` and run `python collector/discover_sources.py`; it fills feeds from Simplify apply links. Entries without `"auto"` (including `"sources": []`) are never changed.

## Secret scanning
A pre-commit hook runs [gitleaks](https://github.com/gitleaks/gitleaks) on staged changes and blocks the commit if it finds a secret (API keys, tokens, private keys). It also blocks commits when gitleaks isn't installed, so it can't be skipped by accident. Set it up once per clone:
1. Install gitleaks: `winget install Gitleaks.Gitleaks` (Windows) or `brew install gitleaks` (Mac). Restart the terminal, then check `gitleaks version`.
2. From the repo folder, turn on the repo's hooks: `git config core.hooksPath .githooks`. This applies to this repo only.
3. Mac/Linux only: `chmod +x .githooks/pre-commit`.

To scan the whole history by hand: `gitleaks git . --redact -v`. The nightly Action's commits don't go through the hook; they only write `data/`.

`.gitignore` keeps dashboard backups (`internship-backup-*.json`), calendar exports (`*.ics`), `.env*` and `*.key` files out of the repo. Backups never include your API key, but they do include your resume and contacts.

## Tuning
- Lanes and filters: regexes in `collector/common.py` (`SALES`, `SWE`, `HW`, `OFFTRACK`, `DEFENSE`; `NONTECH_SALES` is what sales-only companies drop: inside sales, SDR, sales ops).
- Ranking: `score()` in `collector/collect.py` (sales/FAE first, then SWE, then HW; P1 > P2; Bay Area bonus; flags penalized).
- Test one company: `python collector/collect.py --only "Micron,Astera Labs"`.
