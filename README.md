# PitchBook Scraper

Extract structured company data from **PitchBook company-profile PDFs**.

Point it at one PDF or a whole folder and it pulls out, for each company:

| Field | Notes |
| --- | --- |
| **Company name** | from the profile header |
| **Website** | company website |
| **Description** | the "Description" overview paragraph |
| **Keywords** | rule-based 1–10 word summary of *what they do* (no AI) |
| **Employees** | current headcount |
| **Last fundraising round** | amount, date and type (e.g. `$1.00M`, `Seed Round`, `31-Dec-2023`) |
| **Last round type** | e.g. `Seed Round`, `Later Stage VC`, `Series B` |
| **Total raised to date** | cumulative capital raised |
| **Current team** | size **and** names, titles, and emails |
| **Primary offices** | HQ first, then alternate offices |
| **Financials** | most recent revenue + its period-end date (and growth / units when shown) |
| **Acquired?** | whether the company has been acquired, and the acquirer if known |
| **Financing rounds** | the full deal history (type / amount / date), best-effort |

Everything runs **offline and deterministically — no AI / LLM / network calls.**
It's pure Python: `pdfplumber` for text + word coordinates, and regex/layout
rules for the fields (including the keyword summary).

## Desktop app (.exe) + Airtable

For a real point-and-click tool, there's a small desktop app: add 20+ PDFs,
then **export an Excel file** or **sync straight into Airtable**.

### Get the Windows .exe (no Python needed)
The app is compiled automatically by GitHub Actions (a Windows `.exe` can't be
built on Linux/Mac). To produce/download it:

1. Push this repo to GitHub.
2. Go to the repo's **Actions** tab → **Build Windows app** → **Run workflow**
   (or push a tag like `v1.0.0`).
3. When it finishes, download **PitchBookScraper.exe** from the run's
   **Artifacts** (tagged builds also attach it to a **Release**).
4. Double-click `PitchBookScraper.exe` — no Python install required.

### Run the app from source
```bash
pip install -r requirements.txt
python app.py            # or: python -m pitchbook_scraper.gui
```

### Airtable setup (one time)
1. Create a base in Airtable with a table (e.g. **Companies**).
2. Add columns matching the app's fields — click **"Show required Airtable
   columns"** in the app for the exact list. Single-line text works for all
   (Team Size can be a Number).
3. Create a **Personal Access Token** at https://airtable.com/create/tokens with
   the scope **data.records:write** and access to your base.
4. In the app, paste the **token**, the **Base ID** (starts with `app…`), and the
   **table name**, then click **Sync to Airtable**. These are saved locally on
   your machine for next time.

The Airtable sync needs internet; everything else runs offline.

## Easiest way to run (no command line)

> Note: double-clicking a `.py` file will **not** work — this is a command-line
> tool. Use the launcher instead.

1. Install **Python 3.10+** from https://www.python.org/downloads/
   (on Windows, tick **"Add Python to PATH"** during install).
2. Unzip this project anywhere.
3. Double-click:
   - **Windows:** `run.bat`
   - **macOS:** `run.command` (first time: right-click → Open to clear the security prompt)
   - **Linux:** `run.command` (or `python3 run.py`)
4. When it asks, **drag a PitchBook PDF (or a folder of PDFs) onto the window and press Enter.**

It installs the one dependency automatically the first time (needs internet
once), then writes `pitchbook_results.csv` next to your PDF and prints a summary.
After that first run it works fully offline.

## Command line

```bash
pip install -r requirements.txt
```

Requires Python 3.10+.

## Usage

```bash
# Pretty, human-readable summary of one profile
python -m pitchbook_scraper samples/PitchBook_Alethea_Medical.pdf --format text

# JSON (default)
python -m pitchbook_scraper profile.pdf

# Scrape an entire folder of PDFs into a spreadsheet-friendly CSV
python -m pitchbook_scraper ./pdfs --format csv -o companies.csv
```

Options:

- `paths` – one or more PDF files and/or directories (directories are searched recursively for `*.pdf`).
- `-f, --format {json,csv,text}` – output format (default `json`).
- `-o, --output FILE` – write to a file instead of stdout.

A single PDF prints a JSON object; multiple PDFs print a JSON array. CSV always
emits one row per company.

## Use as a library

```python
from pitchbook_scraper import parse_company

company = parse_company("profile.pdf")

print(company.name)                 # "Alethea Medical"
print(company.employees)            # "15"
print(company.last_round_type)      # "Seed Round"
print(company.total_raised)         # "$1.29M"
print(company.most_recent_revenue)  # "$2.77M"
print(company.team_size)            # 5
print(company.team_names)           # ["Steven Pilz", "Rob Bevis", ...]
print(company.acquired)             # False

data = company.to_dict()            # plain dict, ready for json.dump
```

## How it works

PitchBook lays its profiles out very consistently, so the scraper combines two
techniques:

1. **Text regex** for the scalar "Highlights" / "General Information" fields.
   The text is extracted with `pdfplumber` using layout mode so columns stay
   aligned.
2. **Word-coordinate clustering** for the "Current Team" roster and the primary
   office. Those tables wrap names/titles across several lines and the columns
   interleave, which defeats naive text parsing and even pdfplumber's table
   detector — so the scraper buckets words into columns using their x-positions.

## Testing

PitchBook profile PDFs are proprietary ("for the exclusive use of subscriber"),
so no sample is committed. Drop one of your own exports at
`samples/PitchBook_Alethea_Medical.pdf` and run:

```bash
python -m pytest          # or: PYTHONPATH=. python tests/test_parser.py
```

The tests skip themselves automatically when that file is absent.

## Limitations

- Designed for PitchBook's standard **company-profile** PDF export. Other
  PitchBook report types (e.g. fund or investor profiles) are not supported.
- If PitchBook changes its layout, the field patterns may need updating.
- Financial figures are reported as shown in the profile (units are surfaced in
  the `financials` map); no currency conversion is performed.
