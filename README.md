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
- `config/companies.json`: your company list (tier, segment, notes, feeds). Defense and national labs removed.
- `config/sources.json`: which GitHub repos to read.
- `index.html`: the dashboard.

## Status of the career-site feeds
As of 2026-10-06: 81 of 134 companies have an official feed; all of them except Siemens EDA passed a live test. The dashboard header shows any feed that fails in the nightly run. Companies without a feed still appear through the GitHub repos.

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
- **Turned off on purpose:** Juniper (Simplify linked a different company's board; Juniper is now part of HPE, whose feed would label all HPE jobs as Juniper). Sandisk (its SmartRecruiters board is correct but has 0 postings; restore `{"type": "smartrecruiters", "company": "Sandisk"}` when it posts).
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
- Lanes and filters: regexes in `collector/common.py` (`SALES`, `SWE`, `HW`, `OFFTRACK`, `DEFENSE`).
- Ranking: `score()` in `collector/collect.py` (sales/FAE first, then SWE, then HW; P1 > P2; Bay Area bonus; flags penalized).
- Test one company: `python collector/collect.py --only "Micron,Astera Labs"`.
