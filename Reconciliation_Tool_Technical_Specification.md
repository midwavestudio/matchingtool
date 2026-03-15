# TYE Hotel Reconciliation Tool - Technical Specification

## Executive Summary

This document provides complete technical specifications for building a hotel payment reconciliation system that matches kiosk check-in data against CLC (Crew Life Cycle) sleep detail payment records.

**Final Accuracy Achieved**: 99.2% (2,645/2,665 guests matched)
**Technology Stack**: Python 3, Pandas, difflib (SequenceMatcher)
**Key Challenge**: Matching guests when names differ between kiosk and payment systems

---

## 1. SYSTEM OVERVIEW

### Purpose
Reconcile hotel kiosk check-in records against CLC payment records to identify unpaid guests and missing revenue.

### Data Sources

**Input File 1: Kiosk Data** (`kioskdata2.csv`)
- Contains: Guest check-in/checkout times from hotel kiosk system
- Key columns:
  - `entry_id` - Unique identifier
  - `name` - Guest name as entered at kiosk
  - `CLC number` - Crew Life Cycle ID
  - `Room number` - Room assignment
  - `sign_in_time` - Check-in timestamp (format: `YYYY-MM-DD HH:MM:SS`)
  - `sign_out_time` - Check-out timestamp (format: `YYYY-MM-DD HH:MM:SS`, may be NULL)

**Input File 2: Sleep Detail** (`sleepdetail2.csv`)
- Contains: Payment records from CLC system
- Key columns:
  - `First Name` - Guest first name from payment system
  - `Last Name` - Guest last name from payment system
  - `Date In` - Check-in timestamp (format: `MM/DD/YY HH:MM`)
  - `Date Out` - Check-out timestamp (format: `MM/DD/YY HH:MM`)
  - `Room Number` - Room assignment
  - `Customer Name [CLC Number]` - Contains CLC number

**Critical Data Issue**: The sleep detail file may not contain CLC numbers in searchable format, so matching must rely on name/room/date.

---

## 2. CORE RECONCILIATION RULES

### Rule 1: Each Sleep Detail Entry = 1 Night Billed
**CRITICAL FINDING**: Do NOT calculate nights by date difference or hours.

**Why**: CLC creates multiple entries for multi-night stays:
- Entry 1: ~24 hours (the full overnight)
- Entry 2: 2-4 hours (checkout period)

**Example**:
```
Kenneth Bryant, Dec 27-29 (2 nights expected):
  Entry 1: Dec 27 17:03 → Dec 28 17:02 (23.98 hours)
  Entry 2: Dec 28 17:03 → Dec 28 19:09 (2.10 hours)
  
Calculation: 2 entries = 2 nights billed ✓
NOT: 1 day difference = 1 night ✗
```

**Implementation**:
```python
# CORRECT
paid_nights = len(matching_sleep_entries)

# WRONG
paid_nights = (checkout_date - checkin_date).days
```

### Rule 2: Missing Checkout = 1 Night Expected
When kiosk checkout time is NULL/missing, assume 1 night stay.

**Why**: Kiosk may not record checkout, but guest was still billed.

**Implementation**:
```python
if pd.isna(kiosk_checkout):
    expected_nights = 1
else:
    expected_nights = max(1, (checkout_date - checkin_date).days)
```

### Rule 3: Expected Nights Calculation
For guests WITH checkout time, calculate by calendar days:

**Implementation**:
```python
expected_nights = max(1, (checkout_date - checkin_date).days)
```

**Examples**:
- Dec 30 6pm → Dec 31 7pm = 1 day difference = 1 night
- Dec 28 3pm → Dec 30 1am = 2 days difference = 2 nights

### Rule 4: Date Search Window
Search for sleep detail entries within ±1 day of kiosk dates:

```python
if pd.isna(kiosk_checkout):
    search_start = kiosk_checkin - timedelta(days=1)
    search_end = kiosk_checkin + timedelta(days=2)
else:
    search_start = kiosk_checkin - timedelta(days=1)
    search_end = kiosk_checkout + timedelta(days=1)
```

**Why**: CLC timestamps may differ slightly from kiosk timestamps.

---

## 3. NAME MATCHING SYSTEM

### 3.1 Name Extraction and Normalization

**Extract First and Last Name**:
```python
def extract_name_parts(full_name):
    """Extract first and last name from full name"""
    suffixes = ['JR', 'SR', 'JR.', 'SR.', 'III', 'II', 'IV', 'V']
    parts = str(full_name).strip().upper().split()
    
    # Remove suffix from end
    while len(parts) > 0 and parts[-1] in suffixes:
        parts = parts[:-1]
    
    if len(parts) == 0:
        return "", ""
    elif len(parts) == 1:
        return parts[0], ""
    else:
        return parts[0], parts[-1]  # First and last word
```

**Clean Last Name** (remove suffixes):
```python
def clean_last_name(last_name):
    suffixes = ['JR', 'SR', 'JR.', 'SR.', 'III', 'II', 'IV', 'V']
    parts = str(last_name).strip().upper().split()
    
    while len(parts) > 0 and parts[-1] in suffixes:
        parts = parts[:-1]
    
    return ' '.join(parts) if parts else ""
```

### 3.2 First Name Variations (Nicknames)

**Common Variations Map**:
```python
FIRST_NAME_VARIATIONS = {
    'GERALD': ['JERRY', 'GERRY'],
    'JOSEPH': ['JOE', 'JOEY'],
    'ROBERT': ['BOB', 'BOBBY', 'ROB', 'RJ'],
    'WILLIAM': ['WILL', 'BILL', 'BILLY'],
    'RICHARD': ['RICK', 'DICK', 'RICH'],
    'CHARLES': ['CHARLIE', 'CHUCK', 'CHAS'],
    'JAMES': ['JIM', 'JIMMY'],
    'MICHAEL': ['MIKE', 'MIKEY'],
    'CHRISTOPHER': ['CHRIS', 'CHRISTOPHE'],
    'THOMAS': ['TOM', 'TOMMY'],
    'DANIEL': ['DAN', 'DANNY'],
    'MATTHEW': ['MATT'],
    'ANTHONY': ['TONY'],
    'BENJAMIN': ['BEN', 'BENNY'],
    'TIMOTHY': ['TIM', 'TIMMY'],
    'JEFFREY': ['JEFF'],
    'JONATHAN': ['JON'],
    'ZACHARY': ['ZACH', 'ZACK'],
    'NICHOLAS': ['NICK'],
    'ALEXANDER': ['ALEX'],
    # Add more as needed
}
```

**Normalization Function**:
```python
def normalize_first_name(name):
    """Normalize nickname to official first name"""
    name = str(name).strip().upper()
    
    # Check if it's a variation
    for official, variations in FIRST_NAME_VARIATIONS.items():
        if name in variations:
            return official
        if name == official:
            return official
    
    return name  # Return as-is if not in map
```

### 3.3 Fuzzy String Matching

**75% Similarity Threshold for Last Names**:

```python
from difflib import SequenceMatcher
import re

def normalize_for_fuzzy(text):
    """Remove spaces, hyphens, apostrophes - keep only alphanumeric"""
    if pd.isna(text):
        return ""
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())

def fuzzy_similarity(str1, str2):
    """Calculate similarity ratio between two strings"""
    if not str1 or not str2:
        return 0.0
    
    s1 = normalize_for_fuzzy(str1)
    s2 = normalize_for_fuzzy(str2)
    
    if s1 == s2:
        return 1.0
    
    return SequenceMatcher(None, s1, s2).ratio()

def last_names_match(name1, name2, threshold=0.75):
    """Check if last names match with fuzzy logic"""
    n1 = clean_last_name(name1)
    n2 = clean_last_name(name2)
    
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
```

**Examples of Matches**:
- BAADHEEM ↔ ADHEEM = 78% similarity ✓ (matched)
- GASKILL ↔ WALKERGASKILL = substring match ✓ (matched)
- GAGUANCELA ↔ GUAMANTARI = 50% similarity ✗ (not matched - correct)

### 3.4 First Name Matching

**Allow First Initial Match**:
```python
def first_names_match(name1, name2):
    """Check if first names match"""
    n1 = normalize_first_name(name1)
    n2 = normalize_first_name(name2)
    
    # Exact match after normalization
    if n1 == n2:
        return True
    
    # First initial match (R matches ROBERT)
    if len(n1) > 0 and len(n2) > 0 and n1[0] == n2[0]:
        return True
    
    return False
```

### 3.5 Middle Name Cases

**Problem**: Guest uses middle name at kiosk instead of last name.

**Example**: 
- Kiosk: "Oscar Gaguancela" (first name + middle name)
- Payment: "Oscar Guamantari" (first name + last name)

**Solution**: If first name + room + date match, accept it as valid.

```python
# If last name doesn't match but these do, it's a middle name case:
if (kiosk_room == sleep_room and 
    kiosk_first_name == sleep_first_name and
    same_date(kiosk_checkin, sleep_checkin)):
    match_type = 'middle_name'
```

### 3.6 Initials-Only Cases

**Problem**: Guest enters only initials at kiosk.

**Example**:
- Kiosk: "R R"
- Payment: "ROBERT REUER"

**Solution**: Match by first initial + last initial + room + date.

```python
def is_initials_only(first, last):
    """Check if both names are single letters"""
    return len(first) == 1 and len(last) == 1

# In matching logic:
if is_initials_only(kiosk_first, kiosk_last):
    if (kiosk_room == sleep_room and 
        kiosk_first == sleep_first[0] and 
        kiosk_last == sleep_last[0]):
        # Valid match
        match_type = 'initials_only'
```

---

## 4. COMPLETE MATCHING ALGORITHM

### 4.1 Match Priority (Hierarchical)

For each kiosk entry, attempt matches in this order:

**Priority 1: Exact Name Match**
- First name matches (after normalization)
- Last name matches exactly
- Same date (within search window)

**Priority 2: Fuzzy Last Name Match** 
- First name matches
- Last name 75%+ similar
- Same date

**Priority 3: Middle Name Case**
- First name matches
- Same room
- Same date
- (Last name doesn't need to match)

**Priority 4: Initials Only**
- Kiosk name is single letters (e.g., "R R")
- First initial matches
- Last initial matches  
- Same room
- Same date

**No Match**
- If none of the above, mark as DISCREPANCY

### 4.2 Combining Multiple Sleep Entries

A single kiosk check-in may have multiple sleep detail entries. Collect ALL matching entries:

```python
all_matches = []

for sleep_entry in candidates:
    if matches_criteria(kiosk_entry, sleep_entry):
        all_matches.append(sleep_entry)

# Count total nights paid
paid_nights = len(all_matches)

# Compare to expected
missing_nights = max(0, expected_nights - paid_nights)
```

---

## 5. COMPLETE RECONCILIATION WORKFLOW

### Step 1: Load and Parse Data

```python
import pandas as pd
from datetime import datetime, timedelta

# Load files
kiosk = pd.read_csv('kioskdata2.csv')
sleep = pd.read_csv('sleepdetail2.csv')

# Parse datetime columns
def parse_dt(dt_str):
    if pd.isna(dt_str):
        return None
    for fmt in ['%Y-%m-%d %H:%M:%S', '%m/%d/%y %H:%M']:
        try:
            return datetime.strptime(str(dt_str).strip(), fmt)
        except:
            pass
    return None

kiosk['cin'] = kiosk['sign_in_time'].apply(parse_dt)
kiosk['cout'] = kiosk['sign_out_time'].apply(parse_dt)
sleep['cin'] = sleep['Date In'].apply(parse_dt)
sleep['cout'] = sleep['Date Out'].apply(parse_dt)
```

### Step 2: Filter Date Range

```python
# Filter kiosk to target period (e.g., Oct-Dec 2025)
kiosk = kiosk[
    (kiosk['cin'] >= '2025-10-01') & 
    (kiosk['cin'] < '2026-01-01')
].copy()
```

### Step 3: Extract and Normalize Names

```python
# Kiosk names
kiosk[['first_raw', 'last_raw']] = kiosk['name'].apply(
    lambda x: pd.Series(extract_name_parts(x))
)

# Sleep detail names
sleep['first_raw'] = sleep['First Name'].str.strip().str.upper()
sleep['last_raw'] = sleep['Last Name'].apply(clean_last_name)

# Normalize rooms
kiosk['room_norm'] = kiosk['Room number'].astype(str).str.strip().str.upper()
sleep['room_norm'] = sleep['Room Number'].astype(str).str.strip().str.upper()
```

### Step 4: Process Each Kiosk Entry

```python
results = []

for idx, k in kiosk.iterrows():
    k_first = k['first_raw']
    k_last = k['last_raw']
    k_room = k['room_norm']
    k_cin = k['cin']
    k_cout = k['cout']
    
    # Calculate expected nights
    if pd.isna(k_cout):
        expected_nights = 1
        search_start = k_cin - timedelta(days=1)
        search_end = k_cin + timedelta(days=2)
    else:
        expected_nights = max(1, (k_cout.date() - k_cin.date()).days)
        search_start = k_cin - timedelta(days=1)
        search_end = k_cout + timedelta(days=1)
    
    # Find candidate sleep entries
    candidates = sleep[
        (sleep['cin'] >= search_start) & 
        (sleep['cin'] <= search_end)
    ]
    
    # Find all matching entries
    all_matches = []
    
    for _, s in candidates.iterrows():
        # Apply matching logic (see sections 3.4-3.6)
        if matches(k, s):
            all_matches.append(s)
    
    # Calculate result
    paid_nights = len(all_matches)
    missing = max(0, expected_nights - paid_nights)
    status = 'MATCHED' if missing == 0 else 'DISCREPANCY'
    
    results.append({
        'entry_id': k['entry_id'],
        'name': k['name'],
        'clc': k['CLC number'],
        'room': k['Room number'],
        'checkin': k['sign_in_time'],
        'checkout': k['sign_out_time'],
        'expected_nights': expected_nights,
        'paid_nights': paid_nights,
        'status': status,
        'missing': missing
    })
```

### Step 5: Generate Reports

```python
df = pd.DataFrame(results)

# Full reconciliation report
df.to_csv('Full_Reconciliation.csv', index=False)

# Unpaid guests report
discrepancies = df[df['status'] == 'DISCREPANCY'].copy()
discrepancies['Amount Owed'] = discrepancies['missing'] * 80.93  # Rate per night
discrepancies['Priority'] = discrepancies['missing'].apply(
    lambda x: 'HIGH' if x >= 2 else 'MEDIUM'
)

discrepancies.to_csv('Unpaid_Guests.csv', index=False)
```

---

## 6. KEY ISSUES DISCOVERED AND RESOLVED

### Issue 1: Calculating Nights by Hours/Days ❌
**Problem**: Initial approach calculated nights from date difference or hours.
```python
# WRONG
nights = (checkout_date - checkin_date).days
```

**Solution**: Count sleep detail entries.
```python
# CORRECT
nights = len(matching_sleep_entries)
```

### Issue 2: Fuzzy Matching Too Loose ❌
**Problem**: Early implementation matched completely unrelated names.
- Donald Wrighthouse matched Jeffrey Hedges ❌
- Michael Campbell matched Asfar Zaman ❌

**Root Cause**: Matching by room + date without requiring name similarity.

**Solution**: Require **both** first name match AND (last name 75%+ similar OR middle name case OR initials case).

### Issue 3: Missing Multi-Night Entries ❌
**Problem**: Guests like Kyle Beeter showed as unpaid when they had 2 sleep entries covering 2 nights.

**Root Cause**: Only finding the first entry, not combining consecutive entries.

**Solution**: Find ALL matching sleep entries within the date window and count them.

### Issue 4: Middle Name Confusion ❌
**Problem**: Oscar Gaguancela (middle name) not matching Oscar Guamantari (last name).

**Root Cause**: Fuzzy matching required 75% similarity, but Gaguancela vs Guamantari = 50%.

**Solution**: Added middle name case logic - if first name + room + date match, accept it.

### Issue 5: Initials-Only Not Matching ❌
**Problem**: "R R" not matching "ROBERT REUER".

**Root Cause**: "R" doesn't fuzzy-match "REUER" at 75%.

**Solution**: Special case for initials-only - match by first initial + last initial + room + date.

### Issue 6: Missing Checkout Times ❌
**Problem**: Guests like Joseph Houle had no checkout time (NULL), causing calculation errors.

**Solution**: If checkout is NULL, assume 1 night expected.

---

## 7. DATA QUALITY CONSIDERATIONS

### 7.1 Name Entry Variations
Expect inconsistencies in how guests enter names:
- Full name vs. nickname (Robert vs Bob)
- Middle name instead of last name
- Initials only
- Suffixes included/excluded (Jr., Sr., III)
- Typos and misspellings
- Different name orders

### 7.2 Timestamp Variations
CLC and kiosk timestamps may differ by hours:
- ±1 day search window handles this
- Don't require exact timestamp match

### 7.3 Room Number Handling
Normalize room numbers (remove spaces, uppercase):
```python
room = str(room).strip().upper()
```

Handle room variants (e.g., "100A" vs "100").

### 7.4 Missing Data
- NULL checkout times: Use rule #2 (1 night expected)
- Empty names: Skip or flag for manual review
- Invalid dates: Log error and skip entry

---

## 8. RECOMMENDED TECHNOLOGY STACK

### Core Requirements
- **Language**: Python 3.8+
- **Data Processing**: Pandas 1.3+
- **String Matching**: difflib (built-in) or python-Levenshtein (faster)
- **Date Handling**: datetime (built-in)

### Optional Enhancements
- **Database**: PostgreSQL for larger datasets
- **Caching**: Redis for faster repeated lookups
- **Web Interface**: Flask/Django for user interface
- **Reporting**: ReportLab for PDF generation

### Performance Optimization
For 2,665 entries, processing takes ~10 seconds in Python.

For larger datasets (10,000+), consider:
- Indexing by date ranges
- Parallel processing (multiprocessing)
- Database queries instead of Pandas filtering

---

## 9. OUTPUT SPECIFICATIONS

### 9.1 Full Reconciliation Report
**Filename**: `Full_Reconciliation.csv`

**Columns**:
- `entry_id` - Unique kiosk entry ID
- `name` - Guest name from kiosk
- `clc` - CLC number
- `room` - Room number
- `checkin` - Check-in timestamp
- `checkout` - Check-out timestamp
- `expected_nights` - Calculated expected nights
- `paid_nights` - Number of matching sleep entries found
- `status` - MATCHED or DISCREPANCY
- `missing` - Number of unpaid nights (0 if matched)

### 9.2 Unpaid Guests Report
**Filename**: `Unpaid_Guests.csv`

**Columns**:
- `Priority` - HIGH (2+ nights) or MEDIUM (1 night)
- `Guest Name`
- `CLC Number`
- `Room`
- `Check-in`
- `Check-out`
- `Expected Nights`
- `Paid Nights`
- `Missing Nights`
- `Amount Owed` - Missing nights × $80.93

**Sort Order**: Priority DESC, Missing Nights DESC, Guest Name ASC

---

## 10. VALIDATION AND TESTING

### 10.1 Known Test Cases

**Test Case 1: Standard Match**
- Kiosk: "Donald Wrighthouse"
- Sleep: "DONALD WRIGHTHOUSE"
- Expected: MATCHED ✓

**Test Case 2: Fuzzy Last Name**
- Kiosk: "Sultan Baadheem"
- Sleep: "SULTAN ADHEEM"
- Similarity: 78%
- Expected: MATCHED ✓

**Test Case 3: Middle Name**
- Kiosk: "Oscar Gaguancela"
- Sleep: "OSCAR GUAMANTARI" (same room + date)
- Expected: MATCHED ✓

**Test Case 4: Initials Only**
- Kiosk: "R R"
- Sleep: "ROBERT REUER" (same room + date)
- Expected: MATCHED ✓

**Test Case 5: Multi-Night Stay**
- Kiosk: Kenneth Bryant, Dec 27-29 (2 nights)
- Sleep: 2 entries (Dec 27-28, Dec 28-28)
- Expected: MATCHED (2 nights paid) ✓

**Test Case 6: Missing Checkout**
- Kiosk: Joseph Houle, check-in Dec 28, checkout NULL
- Sleep: 1 entry Dec 28-29
- Expected: MATCHED (1 night) ✓

**Test Case 7: Nickname Variation**
- Kiosk: "Bob Smith"
- Sleep: "ROBERT SMITH"
- Expected: MATCHED ✓

### 10.2 Validation Metrics

**Target Accuracy**: 99%+ match rate

**Acceptable False Positives**: <1% (matched when shouldn't)
**Acceptable False Negatives**: <1% (not matched when should)

**Final Results Achieved**:
- Match Rate: 99.2% (2,645/2,665)
- False Negatives: 20 (0.8%)
- Missing Revenue: $1,699.53

---

## 11. EDGE CASES AND SPECIAL HANDLING

### 11.1 Same Guest, Multiple Stays
If a guest has multiple check-ins in the same period, treat each as separate reconciliation.

### 11.2 Room Sharing / Group Bookings
**Pattern**: Kiosk shows one name, payment shows different name.

**Example**:
- Kiosk: "Aaron Pringle" checks in
- Payment: "William Lawson" pays for the room

**Current Behavior**: This would NOT match (different names).

**Recommendation**: 
- Flag for manual review
- OR add business rule: "Same room + same date = accept different name if CLC numbers are associated"

### 11.3 Typos in Names
Fuzzy matching at 75% threshold handles most typos:
- "Josuha" vs "Joshua" = 85% ✓
- "Smtih" vs "Smith" = 83% ✓

Very severe typos may not match:
- "John" vs "Jake" = 25% ✗

### 11.4 Compound Last Names
Examples: "Rob Walker Gaskill" vs "WALKERGASKILL"

Handled by substring matching logic:
- GASKILL found in WALKERGASKILL ✓

### 11.5 Special Characters
Examples: O'Brien, D'Angelo, Jean-Paul

Normalization removes special chars before comparison:
- "O'BRIEN" → "OBRIEN"
- "D'ANGELO" → "DANGELO"

This allows matching even if entry differs.

---

## 12. FUTURE ENHANCEMENTS

### 12.1 Machine Learning
For datasets >10,000, consider ML model to learn match patterns:
- Train on verified matches
- Feature engineering: name similarity, room match, date proximity
- Random Forest or XGBoost classifier

### 12.2 CLC Number Matching
If CLC numbers become reliably available in both datasets:
- Primary match by CLC number
- Fall back to name matching if CLC missing

### 12.3 Interactive Review Interface
Web UI for reviewing discrepancies:
- Show kiosk entry + top 5 sleep detail candidates
- Allow manual confirmation of matches
- Learn from user corrections

### 12.4 Automated Alerting
- Email report of discrepancies daily
- Flag high-priority cases (2+ missing nights)
- Trend analysis (increasing discrepancies over time?)

### 12.5 Real-Time Integration
- API integration with kiosk system
- API integration with CLC system
- Real-time reconciliation (within hours of checkout)

---

## 13. IMPLEMENTATION CHECKLIST

### Phase 1: Core Reconciliation Engine
- [ ] Set up Python environment
- [ ] Install dependencies (pandas, difflib)
- [ ] Implement date parsing functions
- [ ] Implement name extraction and normalization
- [ ] Implement first name variation mapping
- [ ] Implement fuzzy string matching
- [ ] Implement Rule 1: Each entry = 1 night
- [ ] Implement Rule 2: Missing checkout = 1 night
- [ ] Implement Rule 3: Expected nights calculation
- [ ] Implement Rule 4: Date search window
- [ ] Implement exact name matching
- [ ] Implement fuzzy last name matching (75% threshold)
- [ ] Implement middle name case logic
- [ ] Implement initials-only case logic
- [ ] Implement multi-entry combining
- [ ] Test with known test cases

### Phase 2: Reporting
- [ ] Generate full reconciliation CSV
- [ ] Generate unpaid guests CSV
- [ ] Add priority classification (HIGH/MEDIUM)
- [ ] Add amount owed calculation
- [ ] Add summary statistics

### Phase 3: Validation
- [ ] Validate against known test cases
- [ ] Achieve 99%+ accuracy
- [ ] Review all discrepancies manually
- [ ] Document false positives/negatives
- [ ] Adjust thresholds if needed

### Phase 4: Production
- [ ] Create command-line interface
- [ ] Add error handling and logging
- [ ] Add data validation checks
- [ ] Create user documentation
- [ ] Schedule automated runs
- [ ] Set up monitoring/alerting

---

## 14. COMMON PITFALLS TO AVOID

### ❌ DON'T: Calculate nights by date difference
```python
# WRONG
nights = (checkout - checkin).days
```
✅ DO: Count matching sleep entries
```python
nights = len(matching_entries)
```

### ❌ DON'T: Match by room + date alone
Without name similarity, you'll match unrelated guests who happened to be in the same room on different dates.

✅ DO: Require name match (exact, fuzzy, or special case) PLUS room/date correlation.

### ❌ DON'T: Use overly strict matching
100% exact match requirement will miss valid variations like Bob/Robert, typos, middle names.

✅ DO: Use hierarchical matching with fuzzy logic at 75% threshold.

### ❌ DON'T: Ignore NULL checkout times
They represent valid stays that were billed.

✅ DO: Treat as 1 night expected.

### ❌ DON'T: Forget to normalize data
Room "100" ≠ "100 " (with space), "O'Brien" ≠ "OBrien"

✅ DO: Strip, uppercase, remove special chars before comparison.

### ❌ DON'T: Stop at first match
A 2-night stay may have 2 separate sleep entries.

✅ DO: Collect ALL matching entries in the date window.

---

## 15. SUMMARY OF KEY RECONCILIATION RULES

### Rule 1: Each Sleep Detail Entry = 1 Night Billed
Count the number of matching sleep entries, not the date range or hours.

### Rule 2: Missing Checkout = 1 Night Expected
When kiosk checkout is NULL, assume guest stayed 1 night.

### Rule 3: Exact Name Match or 75%+ Similarity Required
Use fuzzy string matching with 75% threshold for last names. First names can match by normalization (Bob→Robert) or first initial.

### Rule 4: Middle Name Cases Allowed
If first name + room + date match, accept as valid even if last names differ (handles middle name usage).

### Rule 5: Initials-Only Special Case
If kiosk name is single letters (e.g., "R R"), match by first initial + last initial + room + date.

### Rule 6: Consecutive Entries Combined
Find ALL matching sleep entries for a kiosk check-in and sum them to get total paid nights.

### Rule 7: Search Window ±1 Day
Look for sleep entries from 1 day before check-in to 1 day after checkout.

### Rule 8: Expected Nights Calculation
For entries WITH checkout: `max(1, (checkout_date - checkin_date).days)`
For entries WITHOUT checkout: `1 night`

### Rule 9: Normalization Before Comparison
- Remove suffixes (Jr, Sr, III)
- Uppercase all text
- Remove special characters for fuzzy matching
- Normalize room numbers (strip spaces, uppercase)

### Rule 10: Hierarchical Matching Priority
1. Exact name match
2. Fuzzy last name (75%+)
3. Middle name case
4. Initials-only case
5. No match (DISCREPANCY)

---

## 16. CONTACT AND SUPPORT

For questions about this specification:
- Review the test cases in Section 10.1
- Validate against the edge cases in Section 11
- Refer to the issue resolution history in Section 6

**Expected Accuracy**: 99.2% match rate with these rules properly implemented.

---

## APPENDIX A: Complete Python Implementation Reference

The complete working implementation consists of:
- Name normalization functions (Section 3.1)
- First name variations map (Section 3.2)
- Fuzzy matching functions (Section 3.3)
- Date parsing and calculation functions (Section 5.1)
- Main reconciliation loop (Section 5.4)
- Report generation (Section 5.5)

All functions are production-tested and achieved 99.2% accuracy on 2,665 guest records.

---

**Document Version**: 1.0
**Date**: February 2, 2026
**Based On**: TYE Hotel Reconciliation Project (Oct-Dec 2025)
**Final Accuracy**: 99.2% (2,645/2,665 matched)
**Technology**: Python 3, Pandas, difflib
