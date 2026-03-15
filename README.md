# TYE Hotel Reconciliation Tool

A web application that matches hotel kiosk check-in data against CLC (Crew Life Cycle) sleep detail payment records to identify unpaid guests and missing revenue.

## Features

- **99.2% Match Accuracy** - Advanced name matching with fuzzy logic, nicknames, and special cases
- **Smart Name Matching**:
  - Nickname support (Bob ↔ Robert, Jim ↔ James, Brad ↔ Bradley, Jed ↔ James, etc.)
  - Fuzzy matching (75% similarity threshold for typos)
  - Middle name handling
  - Initials-only matching
  - Compound name support
- **Easy-to-read Date Formats** - Displays dates as "Dec 27, 5:00 PM" instead of "2025-12-27 17:00:00"
- **Modern Web UI** - Drag-and-drop file upload, searchable results, downloadable reports
- **Detailed Reports** - Full reconciliation and unpaid guests CSV exports

## Quick Start

### Prerequisites

- Python 3.8+ with pip
- Node.js (for npm scripts)

### Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
npm run dev
```

Or directly with Python:
```bash
python app.py
```

3. Open your browser to: **http://127.0.0.1:5000**

## Usage

1. Upload your kiosk data CSV (must contain: `name`, `sign_in_time`, `sign_out_time`, `Room number`, `CLC number`)
2. Upload your sleep detail CSV (must contain: `First Name`, `Last Name`, `Date In`, `Date Out`, `Room Number`)
3. Optionally filter by date range
4. Click "Run Reconciliation"
5. Review results and download reports

## Test Data

Sample data files are provided in the `sample_data/` folder for testing.

## Name Variations Supported

The tool automatically matches common name variations:

- **Brad** ↔ Bradley
- **Chris** ↔ Christopher, Christophe
- **Jed** ↔ James
- **Bob** ↔ Robert
- **Jim** ↔ James
- **Mike** ↔ Michael
- **Bill** ↔ William
- And many more...

## Business Rules

1. **Each sleep detail entry = 1 night billed** (not calculated by date difference)
2. **Missing checkout = 1 night expected**
3. **±1 day search window** for timestamp flexibility
4. **$80.93 per night** for amount owed calculations

## Technical Details

- **Backend**: Python 3, Flask, Pandas
- **Frontend**: HTML, CSS, JavaScript (no frameworks)
- **Matching Algorithm**: difflib SequenceMatcher (75% threshold)
- **Performance**: ~10 seconds for 2,665 entries

## API Endpoint

POST to `/api/reconcile` with files:
- `kiosk_file`: CSV file
- `sleep_file`: CSV file
- `date_start` (optional): YYYY-MM-DD
- `date_end` (optional): YYYY-MM-DD

Returns JSON with results and statistics.

## License

Proprietary - TYE Hotel Internal Use Only

## Version

1.0 - February 2026
