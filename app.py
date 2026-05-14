"""
TYE Hotel Reconciliation Tool
Flask web application for matching kiosk check-in data against CLC sleep detail payment records.
"""

import os
import io
import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from flask import Flask, render_template, request, send_file, jsonify, flash, redirect, url_for
import pandas as pd

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# Rate per night for amount owed calculation
NIGHTLY_RATE = 80.93

# ============================================================================
# FIRST NAME VARIATIONS (Nicknames)
# ============================================================================
FIRST_NAME_VARIATIONS = {
    'GERALD': ['JERRY', 'GERRY'],
    'JOSEPH': ['JOE', 'JOEY'],
    'ROBERT': ['BOB', 'BOBBY', 'ROB', 'RJ'],
    'WILLIAM': ['WILL', 'BILL', 'BILLY', 'WILLY'],
    'RICHARD': ['RICK', 'DICK', 'RICH', 'RICKY'],
    'CHARLES': ['CHARLIE', 'CHUCK', 'CHAS'],
    'JAMES': ['JIM', 'JIMMY', 'JAMIE', 'JED'],
    'BRADLEY': ['BRAD'],
    'MICHAEL': ['MIKE', 'MIKEY'],
    'CHRISTOPHER': ['CHRIS', 'CHRISTOPHE', 'CHRISTOPHÉ'],
    'THOMAS': ['TOM', 'TOMMY'],
    'DANIEL': ['DAN', 'DANNY'],
    'MATTHEW': ['MATT', 'MATTY'],
    'ANTHONY': ['TONY'],
    'BENJAMIN': ['BEN', 'BENNY'],
    'TIMOTHY': ['TIM', 'TIMMY'],
    'JEFFREY': ['JEFF'],
    'JONATHAN': ['JON', 'JONNY'],
    'ZACHARY': ['ZACH', 'ZACK'],
    'NICHOLAS': ['NICK', 'NICKY'],
    'ALEXANDER': ['ALEX', 'AL'],
    'STEVEN': ['STEVE'],
    'STEPHEN': ['STEVE'],
    'EDWARD': ['ED', 'EDDIE', 'TED'],
    'THEODORE': ['TED', 'TEDDY'],
    'RAYMOND': ['RAY'],
    'PATRICK': ['PAT', 'PATTY'],
    'DONALD': ['DON', 'DONNY'],
    'RONALD': ['RON', 'RONNY'],
    'KENNETH': ['KEN', 'KENNY'],
    'LAWRENCE': ['LARRY'],
    'PETER': ['PETE'],
    'ANDREW': ['ANDY', 'DREW'],
    'DAVID': ['DAVE', 'DAVEY'],
    'DEMETRIOS': ['DEMETRI'],
    'GREGORY': ['GREG'],
    'SAMUEL': ['SAM', 'SAMMY'],
    'PHILIP': ['PHIL'],
    'NATHANIEL': ['NATE', 'NATHAN'],
    'JOHN': ['JC', 'JOHNNY', 'JACK'],
    'JOSHUA': ['JOSH'],
    'JACOB': ['JAKE'],
    'ELIZABETH': ['LIZ', 'LIZZY', 'BETH', 'BETTY'],
    'JENNIFER': ['JEN', 'JENNY'],
    'KATHERINE': ['KATE', 'KATHY', 'KATIE'],
    'CATHERINE': ['CATHY', 'KATE', 'KATIE'],
    'MARGARET': ['MAGGIE', 'MEG', 'PEGGY'],
    'PATRICIA': ['PAT', 'PATTY', 'TRISH'],
    'REBECCA': ['BECKY', 'BECCA'],
    'VICTORIA': ['VICKY', 'TORI'],
    'DEBORAH': ['DEB', 'DEBBIE'],
    'SUSAN': ['SUE', 'SUZY'],
    'CHAN': ['CR'],
    'CHRISTINA': ['TINA'],   # CHRIS reserved for Christopher/Christophe to avoid wrong matches
    'CHRISTINE': ['TINA'],
}

# Build reverse lookup for variations (first occurrence wins so CHRIS -> CHRISTOPHER, not CHRISTINA)
NICKNAME_TO_OFFICIAL = {}
for official, nicknames in FIRST_NAME_VARIATIONS.items():
    NICKNAME_TO_OFFICIAL[official] = official
    for nick in nicknames:
        if nick not in NICKNAME_TO_OFFICIAL:
            NICKNAME_TO_OFFICIAL[nick] = official

# ============================================================================
# NAME PROCESSING FUNCTIONS
# ============================================================================

def extract_name_parts(full_name):
    """Extract first and last name from full name, handling suffixes."""
    suffixes = ['JR', 'SR', 'JR.', 'SR.', 'III', 'II', 'IV', 'V']
    
    if pd.isna(full_name) or not str(full_name).strip():
        return "", ""
    
    parts = str(full_name).strip().upper().split()
    
    # Remove suffixes from end
    while len(parts) > 0 and parts[-1] in suffixes:
        parts = parts[:-1]
    
    if len(parts) == 0:
        return "", ""
    elif len(parts) == 1:
        return parts[0], ""
    else:
        return parts[0], parts[-1]  # First and last word


def clean_last_name(last_name):
    """Remove suffixes from last name."""
    suffixes = ['JR', 'SR', 'JR.', 'SR.', 'III', 'II', 'IV', 'V']
    
    if pd.isna(last_name) or not str(last_name).strip():
        return ""
    
    parts = str(last_name).strip().upper().split()
    
    while len(parts) > 0 and parts[-1] in suffixes:
        parts = parts[:-1]
    
    return ' '.join(parts) if parts else ""


def _strip_accents(text):
    """Remove accents so Christophe and Christophé match."""
    if not text:
        return text
    nfd = unicodedata.normalize('NFD', str(text))
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def normalize_first_name(name):
    """Normalize nickname to official first name."""
    if pd.isna(name) or not str(name).strip():
        return ""
    
    name = _strip_accents(str(name).strip().upper())
    return NICKNAME_TO_OFFICIAL.get(name, name)


def normalize_for_fuzzy(text):
    """Remove spaces, hyphens, apostrophes - keep only alphanumeric."""
    if pd.isna(text) or not str(text).strip():
        return ""
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())


def fuzzy_similarity(str1, str2):
    """Calculate similarity ratio between two strings."""
    if not str1 or not str2:
        return 0.0
    
    s1 = normalize_for_fuzzy(str1)
    s2 = normalize_for_fuzzy(str2)
    
    if not s1 or not s2:
        return 0.0
    
    if s1 == s2:
        return 1.0
    
    return SequenceMatcher(None, s1, s2).ratio()


def last_names_match(name1, name2, threshold=0.75):
    """Check if last names match with fuzzy logic."""
    n1 = clean_last_name(name1)
    n2 = clean_last_name(name2)
    
    if not n1 or not n2:
        return False, 0.0
    
    # Exact match
    if n1 == n2:
        return True, 1.0
    
    # Substring match (e.g., GASKILL in WALKERGASKILL)
    n1_norm = normalize_for_fuzzy(n1)
    n2_norm = normalize_for_fuzzy(n2)
    
    if len(n1_norm) >= 4 and len(n2_norm) >= 4:
        if n1_norm in n2_norm or n2_norm in n1_norm:
            return True, 0.9
    
    # Fuzzy similarity
    similarity = fuzzy_similarity(n1, n2)
    if similarity >= threshold:
        return True, similarity
    
    return False, similarity


def first_names_match(name1, name2):
    """Check if first names match (with normalization and initial matching)."""
    if pd.isna(name1) or pd.isna(name2):
        return False
    
    n1 = normalize_first_name(str(name1).strip().upper())
    n2 = normalize_first_name(str(name2).strip().upper())
    
    if not n1 or not n2:
        return False
    
    # Exact match after normalization
    if n1 == n2:
        return True
    
    # First initial match (R matches ROBERT)
    if len(n1) == 1 or len(n2) == 1:
        if n1[0] == n2[0]:
            return True
    
    return False


def is_initials_only(first, last):
    """Check if both names are single letters (initials only)."""
    return len(str(first).strip()) == 1 and len(str(last).strip()) == 1


# ============================================================================
# DATE PARSING FUNCTIONS
# ============================================================================

def parse_datetime(dt_str):
    """Parse datetime from various formats."""
    if pd.isna(dt_str) or not str(dt_str).strip():
        return None
    
    dt_str = str(dt_str).strip()
    
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%m/%d/%y %H:%M',
        '%m/%d/%Y %H:%M',
        '%m/%d/%y %H:%M:%S',
        '%m/%d/%Y %H:%M:%S',
        '%Y/%m/%d %H:%M:%S',
        '%Y/%m/%d %H:%M',
        '%d/%m/%y %H:%M',
        '%d/%m/%Y %H:%M',
        # Arrivals format: "May 13, 2026 10:54 PM"
        '%B %d, %Y %I:%M %p',
        '%B %d, %Y %H:%M',
        # Date only (arrivals date column without time)
        '%B %d, %Y',
        '%m/%d/%Y',
        '%m/%d/%y',
        '%Y-%m-%d',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    
    return None


def normalize_room(room):
    """Normalize room number for comparison."""
    if pd.isna(room):
        return ""
    return str(room).strip().upper().replace(' ', '')


def format_datetime_display(dt):
    """Format datetime for easy reading: 'Dec 27, 5:00 PM'"""
    if pd.isna(dt) or dt is None:
        return 'N/A'
    
    if isinstance(dt, str):
        dt = parse_datetime(dt)
    
    if dt is None:
        return 'N/A'
    
    # Format as "Dec 27, 5:00 PM"
    return dt.strftime('%b %d, %I:%M %p').replace(' 0', ' ')


# ============================================================================
# MATCHING LOGIC
# ============================================================================

def find_matches(kiosk_entry, sleep_df):
    """
    Find all matching sleep detail entries for a kiosk entry.
    Returns list of matches and match type.
    """
    matches = []
    match_type = None
    
    k_first = kiosk_entry['first_raw']
    k_last = kiosk_entry['last_raw']
    k_room = kiosk_entry['room_norm']
    k_cin = kiosk_entry['cin']
    k_cout = kiosk_entry['cout']
    
    if pd.isna(k_cin):
        return [], None
    
    # Calculate search window
    if pd.isna(k_cout):
        search_start = k_cin - timedelta(days=1)
        search_end = k_cin + timedelta(days=2)
    else:
        search_start = k_cin - timedelta(days=1)
        search_end = k_cout + timedelta(days=1)
    
    # Filter candidates by date range
    candidates = sleep_df[
        (sleep_df['cin'].notna()) &
        (sleep_df['cin'] >= search_start) &
        (sleep_df['cin'] <= search_end)
    ]
    
    for _, s in candidates.iterrows():
        s_first = s['first_raw']
        s_last = s['last_raw']
        s_room = s['room_norm']
        
        matched = False
        current_match_type = None
        
        # Priority 1: Exact name match
        if first_names_match(k_first, s_first):
            match_result, similarity = last_names_match(k_last, s_last)
            if match_result:
                if similarity == 1.0:
                    matched = True
                    current_match_type = 'exact'
                else:
                    matched = True
                    current_match_type = 'fuzzy'
        
        # Priority 3: Middle name case (first name + room + date match)
        if not matched and first_names_match(k_first, s_first):
            if k_room and s_room and k_room == s_room:
                matched = True
                current_match_type = 'middle_name'
        
        # Priority 4: Initials only
        if not matched and is_initials_only(k_first, k_last):
            if k_room and s_room and k_room == s_room:
                if s_first and s_last:
                    if k_first.upper() == s_first[0].upper() and k_last.upper() == s_last[0].upper():
                        matched = True
                        current_match_type = 'initials'
        
        if matched:
            matches.append(s)
            if match_type is None:
                match_type = current_match_type
    
    return matches, match_type


# ============================================================================
# MAIN RECONCILIATION FUNCTION
# ============================================================================

def normalize_kiosk_df(df):
    """
    Normalize a kiosk/arrivals DataFrame into standard internal columns:
      name, CLC number, Room number, sign_in_time, sign_out_time
    Supports both the old kiosk format and the new arrivals format.
    """
    df = df.copy()
    cols = [c.strip() for c in df.columns]
    df.columns = cols

    # --- New arrivals format: Name / Check-In Date / Check-In Time ---
    if 'Check-In Date' in cols and 'Check-In Time' in cols:
        # Combine date + time columns into a single datetime string
        def combine_dt(row, date_col, time_col):
            d = str(row[date_col]).strip() if pd.notna(row[date_col]) else ''
            t = str(row[time_col]).strip() if pd.notna(row[time_col]) else ''
            if not d or d.lower() in ('nat', 'nan', ''):
                return None
            return f"{d} {t}".strip() if t and t.lower() not in ('nat', 'nan') else d

        df['sign_in_time']  = df.apply(lambda r: combine_dt(r, 'Check-In Date',  'Check-In Time'),  axis=1)
        df['sign_out_time'] = df.apply(lambda r: combine_dt(r, 'Check-Out Date', 'Check-Out Time'), axis=1) \
            if 'Check-Out Date' in cols else None

        # Map column names to standard names
        if 'Name' in cols and 'name' not in cols:
            df['name'] = df['Name']
        if 'CLC Number' in cols and 'CLC number' not in cols:
            df['CLC number'] = df['CLC Number']
        if 'Room' in cols and 'Room number' not in cols:
            df['Room number'] = df['Room']

        # entry_id from Reservation ID if present, else row index
        if 'entry_id' not in cols:
            df['entry_id'] = df.get('Reservation ID', pd.Series(range(len(df))))

    # --- Old kiosk format: sign_in_time already present ---
    else:
        if 'entry_id' not in cols:
            df['entry_id'] = range(len(df))

    return df


def reconcile(kiosk_df, sleep_df, date_start=None, date_end=None):
    """
    Main reconciliation function.
    Returns DataFrame with reconciliation results.
    """
    results = []

    # Normalize kiosk input (handles both old and new arrivals format)
    kiosk_df = normalize_kiosk_df(kiosk_df)

    # Parse datetime columns for kiosk
    kiosk_df = kiosk_df.copy()
    kiosk_df['cin'] = kiosk_df['sign_in_time'].apply(parse_datetime)
    kiosk_df['cout'] = kiosk_df['sign_out_time'].apply(parse_datetime) \
        if 'sign_out_time' in kiosk_df.columns else pd.NaT
    
    # Parse datetime columns for sleep detail
    sleep_df = sleep_df.copy()
    sleep_df['cin'] = sleep_df['Date In'].apply(parse_datetime)
    sleep_df['cout'] = sleep_df['Date Out'].apply(parse_datetime)
    
    # Filter by date range if specified
    if date_start:
        kiosk_df = kiosk_df[kiosk_df['cin'] >= parse_datetime(date_start)]
    if date_end:
        kiosk_df = kiosk_df[kiosk_df['cin'] <= parse_datetime(date_end)]
    
    # Extract and normalize names for kiosk
    kiosk_df[['first_raw', 'last_raw']] = kiosk_df['name'].apply(
        lambda x: pd.Series(extract_name_parts(x))
    )
    kiosk_df['room_norm'] = kiosk_df['Room number'].apply(normalize_room)
    
    # Extract and normalize names for sleep detail
    sleep_df['first_raw'] = sleep_df['First Name'].apply(
        lambda x: str(x).strip().upper() if pd.notna(x) else ""
    )
    sleep_df['last_raw'] = sleep_df['Last Name'].apply(clean_last_name)
    sleep_df['room_norm'] = sleep_df['Room Number'].apply(normalize_room)
    
    # Process each kiosk entry
    for idx, k in kiosk_df.iterrows():
        k_cin = k['cin']
        k_cout = k['cout']
        
        if pd.isna(k_cin):
            continue
        
        # Calculate expected nights (Rule 2 & 3)
        if pd.isna(k_cout):
            expected_nights = 1
        else:
            expected_nights = max(1, (k_cout.date() - k_cin.date()).days)
        
        # Find all matching sleep entries
        matching_entries, match_type = find_matches(k, sleep_df)
        
        # Count paid nights (Rule 1: each entry = 1 night)
        paid_nights = len(matching_entries)
        
        # Calculate discrepancy
        missing = max(0, expected_nights - paid_nights)
        status = 'MATCHED' if missing == 0 else 'DISCREPANCY'
        
        results.append({
            'entry_id': k.get('entry_id', idx),
            'name': k.get('name', ''),
            'clc': k.get('CLC number', ''),
            'room': k.get('Room number', ''),
            'checkin': format_datetime_display(k_cin),
            'checkout': format_datetime_display(k_cout),
            'expected_nights': expected_nights,
            'paid_nights': paid_nights,
            'status': status,
            'missing': missing,
            'match_type': match_type if match_type else 'no_match',
            'amount_owed': missing * NIGHTLY_RATE
        })
    
    return pd.DataFrame(results)


# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def index():
    """Main page with file upload form."""
    return render_template('index.html')


@app.route('/reconcile', methods=['POST'])
def run_reconciliation():
    """Process uploaded files and run reconciliation."""
    try:
        # Check if files were uploaded
        if 'kiosk_file' not in request.files or 'sleep_file' not in request.files:
            flash('Please upload both kiosk data and sleep detail files.', 'error')
            return redirect(url_for('index'))
        
        kiosk_file = request.files['kiosk_file']
        sleep_file = request.files['sleep_file']
        
        if kiosk_file.filename == '' or sleep_file.filename == '':
            flash('Please select both files.', 'error')
            return redirect(url_for('index'))
        
        # Read CSV files
        try:
            kiosk_df = pd.read_csv(kiosk_file)
            sleep_df = pd.read_csv(sleep_file)
        except Exception as e:
            flash(f'Error reading CSV files: {str(e)}', 'error')
            return redirect(url_for('index'))
        
        # Validate required columns — support both kiosk formats
        kiosk_cols = kiosk_df.columns.tolist()
        is_arrivals_format = 'Check-In Date' in kiosk_cols and 'Check-In Time' in kiosk_cols

        if is_arrivals_format:
            kiosk_required = ['Name', 'Check-In Date', 'Check-In Time', 'Room']
        else:
            kiosk_required = ['name', 'sign_in_time', 'Room number']

        sleep_required = ['First Name', 'Last Name', 'Date In', 'Room Number']
        
        missing_kiosk = [col for col in kiosk_required if col not in kiosk_cols]
        missing_sleep = [col for col in sleep_required if col not in sleep_df.columns]
        
        if missing_kiosk:
            flash(f'Kiosk/Arrivals file missing columns: {", ".join(missing_kiosk)}', 'error')
            return redirect(url_for('index'))
        
        if missing_sleep:
            flash(f'Sleep detail file missing columns: {", ".join(missing_sleep)}', 'error')
            return redirect(url_for('index'))
        
        # Get optional date range
        date_start = request.form.get('date_start', None)
        date_end = request.form.get('date_end', None)
        
        if date_start == '':
            date_start = None
        if date_end == '':
            date_end = None
        
        # Run reconciliation
        results_df = reconcile(kiosk_df, sleep_df, date_start, date_end)
        
        # Calculate statistics
        total_entries = len(results_df)
        matched = len(results_df[results_df['status'] == 'MATCHED'])
        discrepancies = len(results_df[results_df['status'] == 'DISCREPANCY'])
        match_rate = (matched / total_entries * 100) if total_entries > 0 else 0
        total_missing_nights = results_df['missing'].sum()
        total_amount_owed = results_df['amount_owed'].sum()
        
        # Get discrepancy details for display
        discrepancy_df = results_df[results_df['status'] == 'DISCREPANCY'].copy()
        discrepancy_df['Priority'] = discrepancy_df['missing'].apply(
            lambda x: 'HIGH' if x >= 2 else 'MEDIUM'
        )
        discrepancy_df = discrepancy_df.sort_values(
            by=['missing', 'name'], 
            ascending=[False, True]
        )
        
        # Store results in session for download
        app.config['RESULTS_DF'] = results_df
        app.config['DISCREPANCY_DF'] = discrepancy_df
        
        stats = {
            'total_entries': total_entries,
            'matched': matched,
            'discrepancies': discrepancies,
            'match_rate': round(match_rate, 1),
            'total_missing_nights': int(total_missing_nights),
            'total_amount_owed': round(total_amount_owed, 2)
        }
        
        return render_template(
            'results.html',
            stats=stats,
            discrepancies=discrepancy_df.to_dict('records'),
            all_results=results_df.to_dict('records')
        )
        
    except Exception as e:
        flash(f'Error during reconciliation: {str(e)}', 'error')
        return redirect(url_for('index'))


@app.route('/download/<report_type>')
def download_report(report_type):
    """Download reconciliation report as CSV."""
    try:
        if report_type == 'full':
            df = app.config.get('RESULTS_DF')
            filename = 'Full_Reconciliation.csv'
        elif report_type == 'unpaid':
            df = app.config.get('DISCREPANCY_DF')
            filename = 'Unpaid_Guests.csv'
        else:
            flash('Invalid report type.', 'error')
            return redirect(url_for('index'))
        
        if df is None:
            flash('No results available. Please run reconciliation first.', 'error')
            return redirect(url_for('index'))
        
        # Create CSV in memory
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        flash(f'Error generating report: {str(e)}', 'error')
        return redirect(url_for('index'))


@app.route('/api/reconcile', methods=['POST'])
def api_reconcile():
    """API endpoint for programmatic access."""
    try:
        if 'kiosk_file' not in request.files or 'sleep_file' not in request.files:
            return jsonify({'error': 'Both kiosk_file and sleep_file are required'}), 400
        
        kiosk_df = pd.read_csv(request.files['kiosk_file'])
        sleep_df = pd.read_csv(request.files['sleep_file'])
        
        date_start = request.form.get('date_start')
        date_end = request.form.get('date_end')
        
        results_df = reconcile(kiosk_df, sleep_df, date_start, date_end)
        
        return jsonify({
            'success': True,
            'total_entries': len(results_df),
            'matched': len(results_df[results_df['status'] == 'MATCHED']),
            'discrepancies': len(results_df[results_df['status'] == 'DISCREPANCY']),
            'results': results_df.to_dict('records')
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    app.run(debug=True, port=5000)
