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
- `collector/discover_sources.py`: finds a company's career-site feed from Simplify apply links. Run after adding companies.
- `config/companies.json`: your company list (tier, segment, notes, feeds). Defense and national labs removed.
- `config/sources.json`: which GitHub repos to read.
- `index.html`: the dashboard.

## Status of the career-site feeds
The first seed was built from the GitHub repos only (the sandbox it was built in could not reach company sites). After your first Action run, the header shows any failing feeds.
- **Seen in real apply links (should work):** Workday sites (Micron, Intel, Marvell, NVIDIA, ADI, Microchip, KLA, AMAT, ASML, GF, NXP, Cadence, Arrow, Avnet, MPS, SiFive, Samsung, and more), Greenhouse (Astera, SK hynix), SmartRecruiters (WD, Sandisk, Solidigm, Kioxia), Oracle (TI, onsemi), Eightfold (Qualcomm).
- **Endpoint untested:** Teradyne (SuccessFactors), AMD / Keysight / Rivian (iCIMS-backed sites), `asteraearlycareer2027` board.
- **No direct feed yet (repos only):** Lam, Silicon Labs, Synopsys, Siemens EDA, ST, Infineon, Arm, Credo, Power Integrations, FormFactor, SkyWater, TSMC AZ, others.

Hand this to Claude Code: "Run `python collector/collect.py`, fix any failing adapters in data/sources.json, and add career-site feeds for the companies with none."

## Tuning
- Lanes and filters: regexes in `collector/common.py` (`SALES`, `SWE`, `HW`, `OFFTRACK`, `DEFENSE`).
- Ranking: `score()` in `collector/collect.py` (sales/FAE first, then SWE, then HW; P1 > P2; Bay Area bonus; flags penalized).
- Test one company: `python collector/collect.py --only "Micron,Astera Labs"`.
