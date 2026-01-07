"""
Excel + OCR License Expiry Date Processor
Processes Excel sheets and extracts license expiry dates from OCR/images (Najm reports)
Handles missing/null license expiry dates
"""

import pandas as pd
import base64
import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import os
from PIL import Image
import io
from difflib import SequenceMatcher


class ExcelOCRLicenseProcessor:
    """Processes Excel sheets and extracts license expiry dates from OCR/images"""
    
    def __init__(self):
        """Initialize the processor"""
        self.party_date_matches = {}  # Store Party ID -> License Expiry Date mappings
    
    def extract_license_expiry_from_ocr_text(self, ocr_text: str, party_id: str = None) -> Optional[str]:
        """
        Extract license expiry date from OCR text (Najm report format)
        
        Args:
            ocr_text: OCR text from Najm report
            party_id: Party ID to match (optional)
        
        Returns:
            License expiry date in format DD/MM/YYYY or None if not found
        """
        if not ocr_text:
            return None
        
        # Patterns for license expiry date in Najm reports - improved
        patterns = [
            # Arabic pattern: تاريخ إنتهاء الرخصة followed by date (more flexible)
            r'تاريخ\s*إنتهاء\s*الرخصة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'تاريخ\s*إنتهاء[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'Expiry\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            # Date near "رخصة" (license) - improved
            r'رخصة[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'نوع\s*الرخصة[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            # Date after license type
            r'رخصة\s*خاصة[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'رخصة\s*عمومية[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        ]
        
        # If party_id provided, look for dates near that party's section
        if party_id:
            # Clean party ID for matching
            party_id_clean = re.sub(r'[^\d]', '', str(party_id))
            
            # OPTIMIZED: Fast exact match patterns (combined for speed)
            # Strategy 1: Exact match with Party ID - try most common pattern first
            quick_pattern = rf'{re.escape(party_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
            match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            if match:
                date_str = match.group(1)
                if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                    return date_str
            
            # Try other patterns only if first failed
            party_patterns = [
                rf'رقم\s*الهوية[:\s]*{re.escape(party_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})',
                rf'Party\s*\(\d+\)[^P]*?{re.escape(party_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})',
            ]
            
            for pattern in party_patterns:
                match = re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                if match:
                    date_str = match.group(1)
                    if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                        return date_str
            
            # OPTIMIZED Strategy 2: Fast fuzzy match - try last 8-9 digits (most common case)
            if len(party_id_clean) >= 9:
                last_9 = party_id_clean[-9:]
                quick_pattern = rf'{re.escape(last_9)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
                match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                if match:
                    date_str = match.group(1)
                    if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                        return date_str
                
                if len(party_id_clean) >= 8:
                    last_8 = party_id_clean[-8:]
                    quick_pattern = rf'{re.escape(last_8)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
                    match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                    if match:
                        date_str = match.group(1)
                        if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                            return date_str
            
            # OPTIMIZED Strategy 3: Fast similarity matching - only check if contains/contained (fastest check)
            all_party_ids = re.findall(r'\b\d{9,10}\b', ocr_text)
            
            # Fast check: if Party ID contains or is contained in any OCR ID
            for ocr_party_id in all_party_ids[:10]:  # Limit to first 10 for speed
                ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                if party_id_clean in ocr_id_clean or ocr_id_clean in party_id_clean:
                    # Found similar ID, extract date near it
                    quick_pattern = rf'{re.escape(ocr_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
                    match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                    if match:
                        date_str = match.group(1)
                        if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                            return date_str
            
            # OPTIMIZED Strategy 4: Fast context search - only for first few similar IDs
            for ocr_party_id in all_party_ids[:5]:  # Limit to first 5 for speed
                ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                if party_id_clean in ocr_id_clean or ocr_id_clean in party_id_clean:
                    id_pos = ocr_text.find(ocr_party_id)
                    if id_pos != -1:
                        search_text = ocr_text[id_pos:id_pos + 500]  # Reduced from 1000
                        match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})', search_text)
                        if match:
                            date_str = match.group(1)
                            if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                                return date_str
            
            # OPTIMIZED Strategy 5: Fast fallback - extract first valid date if Party ID not found
            if not all_party_ids:
                # Quick extraction of first valid date
                match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})', ocr_text)
                if match:
                    date_str = match.group(1)
                    if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025', '2025-11-19']:
                        return date_str
        
        # OPTIMIZED: Try patterns - return first valid date (early exit)
        for pattern in patterns:
            match = re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            if match:
                date_str = match.group(1)
                if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025', '2025-11-19']:
                    return date_str
        
        return None
    
    def _validate_date(self, date_str: str) -> bool:
        """
        Validate if date string is a valid license expiry date (not birth date)
        
        Args:
            date_str: Date string in format DD/MM/YYYY or similar
        
        Returns:
            True if valid license expiry date, False otherwise
        """
        try:
            # Parse date
            date_parts = re.split(r'[/-]', date_str.strip())
            if len(date_parts) != 3:
                return False
            
            # Determine year position
            year = None
            for part in date_parts:
                if len(part) == 4:
                    year = int(part)
                    break
            
            if not year:
                return False
            
            # License expiry dates should be:
            # - Gregorian: >= 2010 (current/future licenses)
            # - Hijri: 1400-1600 range
            if 1400 <= year <= 1600:  # Hijri date
                return True
            elif year >= 2010:  # Gregorian date (current/future)
                return True
            elif 1900 <= year < 2010:  # Likely birth date, reject
                return False
            else:
                return False
        except:
            return False
    
    def extract_all_license_expiry_dates(self, ocr_text: str) -> Dict[str, str]:
        """
        Extract all license expiry dates from OCR text, matching with Party IDs
        
        Args:
            ocr_text: OCR text from Najm report
        
        Returns:
            Dictionary mapping Party ID -> License Expiry Date
        """
        party_dates = {}
        
        if not ocr_text:
            return party_dates
        
        # Method 1: Split by Party sections
        # Look for "Party (1)", "Party (2)", etc. or Arabic equivalents
        party_section_patterns = [
            r'Party\s*\((\d+)\)',
            r'الطرف\s*\((\d+)\)',
            r'Party\s*(\d+)',
        ]
        
        party_sections = []
        for pattern in party_section_patterns:
            matches = list(re.finditer(pattern, ocr_text, re.IGNORECASE | re.UNICODE))
            if matches:
                # Split text by party markers
                for i, match in enumerate(matches):
                    start_pos = match.end()
                    end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(ocr_text)
                    party_num = match.group(1)
                    party_text = ocr_text[start_pos:end_pos]
                    party_sections.append((party_num, party_text))
                break
        
        # If no party sections found, try to extract from entire text
        if not party_sections:
            party_sections = [("1", ocr_text)]
        
        for party_num, party_text in party_sections:
            # Extract Party ID from this section - improved patterns
            party_id_patterns = [
                r'رقم\s*الهوية[:\s]*(\d{8,10})',
                r'ID\s*Number[:\s]*(\d{8,10})',
                r'Party\s*ID[:\s]*(\d{8,10})',
                r'(\d{9,10})',  # Fallback: any 9-10 digit number (Saudi ID format)
            ]
            
            party_id = None
            party_id_match = None
            for pattern in party_id_patterns:
                matches = list(re.finditer(pattern, party_text, re.IGNORECASE | re.UNICODE))
                if matches:
                    # Take the first match that looks like an ID (9-10 digits)
                    for match in matches:
                        potential_id = match.group(1)
                        # Saudi ID is usually 9-10 digits, but also accept 8 digits for flexibility
                        if len(potential_id) >= 8:
                            party_id = potential_id
                            party_id_match = match
                            print(f"  🔍 Found Party ID in section {party_num}: {party_id}")
                            break
                    if party_id:
                        break
            
            # Extract license expiry date from this party's section - improved patterns
            expiry_date = None
            
            # Pattern 1: Look for "تاريخ إنتهاء الرخصة" or "Expiry Date" followed by date
            expiry_patterns = [
                r'تاريخ\s*إنتهاء\s*الرخصة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
                r'Expiry\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
                r'تاريخ\s*إنتهاء[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            ]
            
            for pattern in expiry_patterns:
                matches = list(re.finditer(pattern, party_text, re.IGNORECASE | re.UNICODE))
                for match in matches:
                    date_str = match.group(1)
                    if self._validate_date(date_str):
                        expiry_date = date_str
                        break
                if expiry_date:
                    break
            
            # Pattern 2: If party_id found, look for date near the ID
            if not expiry_date and party_id:
                # Look for date within 500 characters after Party ID
                if party_id_match:
                    id_end = party_id_match.end()
                    search_text = party_text[id_end:id_end + 500]
                    date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})'
                    date_matches = list(re.finditer(date_pattern, search_text))
                    for date_match in date_matches:
                        date_str = date_match.group(1)
                        if self._validate_date(date_str):
                            expiry_date = date_str
                            break
            
            # Pattern 3: Look for any valid date in party section (fallback)
            if not expiry_date:
                date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})'
                date_matches = list(re.finditer(date_pattern, party_text))
                for date_match in date_matches:
                    date_str = date_match.group(1)
                    if self._validate_date(date_str):
                        # Check if it's near license-related keywords
                        date_pos = date_match.start()
                        context_before = party_text[max(0, date_pos - 100):date_pos]
                        if any(keyword in context_before for keyword in ['رخصة', 'license', 'إنتهاء', 'expiry']):
                            expiry_date = date_str
                            break
            
            # Store the result
            if party_id and expiry_date:
                party_dates[party_id] = expiry_date
                print(f"  ✅ Extracted: Party ID {party_id} → License Expiry: {expiry_date}")
                # Also store variations (last 8-9 digits) for fuzzy matching
                if len(party_id) >= 9:
                    party_dates[party_id[-9:]] = expiry_date
                if len(party_id) >= 8:
                    party_dates[party_id[-8:]] = expiry_date
            elif expiry_date:
                # If we found a date but no Party ID, store with party number
                party_dates[f"Party_{party_num}"] = expiry_date
                print(f"  ✅ Extracted: Party {party_num} → License Expiry: {expiry_date}")
            elif party_id:
                print(f"  ⚠️ Found Party ID {party_id} but no license expiry date")
        
        return party_dates
    
    def process_base64_image(self, base64_data: str) -> Dict[str, str]:
        """
        Process base64 encoded image and extract license expiry dates
        
        Args:
            base64_data: Base64 encoded image string
        
        Returns:
            Dictionary mapping Party ID -> License Expiry Date
        """
        try:
            # Decode base64 to image
            if base64_data.startswith('data:image'):
                # Remove data URL prefix
                base64_data = base64_data.split(',')[1]
            
            image_data = base64.b64decode(base64_data)
            image = Image.open(io.BytesIO(image_data))
            
            # For now, we'll use OCR text if provided
            # In production, you'd use Tesseract or similar OCR here
            # For this implementation, we assume OCR text is already extracted
            # and passed separately
            
            return {}
        except Exception as e:
            print(f"  ⚠️ Error processing base64 image: {str(e)[:100]}")
            return {}
    
    def process_excel_with_ocr(self, excel_path: str = None, df: pd.DataFrame = None,
                               ocr_text: str = None, base64_image: str = None, 
                               case_number: str = None) -> pd.DataFrame:
        """
        Process Excel sheet and fill in missing license expiry dates from OCR
        
        Args:
            excel_path: Path to Excel file (optional if df provided)
            df: DataFrame to process (optional if excel_path provided)
            ocr_text: OCR text from Najm report (optional)
            base64_image: Base64 encoded image (optional)
            case_number: Case number for matching (optional)
        
        Returns:
            Updated DataFrame with license expiry dates filled in
        """
        # Read Excel file or use provided DataFrame
        if df is not None:
            print(f"✓ Using provided DataFrame: {len(df)} rows")
        elif excel_path:
            try:
                df = pd.read_excel(excel_path)
                print(f"✓ Loaded Excel file: {len(df)} rows")
            except Exception as e:
                print(f"✗ Error reading Excel file: {str(e)}")
                return pd.DataFrame()
        else:
            print("✗ Error: Either excel_path or df must be provided")
            return pd.DataFrame()
        
        # Extract license expiry dates from OCR
        party_dates = {}
        if ocr_text:
            party_dates = self.extract_all_license_expiry_dates(ocr_text)
        elif base64_image:
            # Process image and extract OCR text (would need OCR library)
            # For now, assume OCR text is provided separately
            pass
        
        # Find Party_ID column (handle different column names)
        party_id_col = None
        for col in df.columns:
            if 'party' in col.lower() and 'id' in col.lower():
                party_id_col = col
                break
        
        if not party_id_col:
            print("⚠️ Warning: Party ID column not found in Excel")
            return df
        
        # Find License_Expiry_Date column
        expiry_col = None
        for col in df.columns:
            if 'license' in col.lower() and 'expiry' in col.lower():
                expiry_col = col
                break
        
        if not expiry_col:
            # Create new column if it doesn't exist
            expiry_col = 'License_Expiry_Date'
            df[expiry_col] = ''
        
        # Fill in missing license expiry dates
        updated_count = 0
        for idx, row in df.iterrows():
            party_id = str(row[party_id_col]).strip() if pd.notna(row[party_id_col]) else ''
            current_expiry = str(row[expiry_col]).strip() if pd.notna(row[expiry_col]) else ''
            
            # Clean Party ID for matching
            party_id_clean = re.sub(r'[^\d]', '', party_id)
            
            # Check if current expiry is empty/null/non-existent or "not identify"
            if not current_expiry or current_expiry.lower() in ['nan', 'none', 'null', '', 'not identify', 'notidentify']:
                # Try to find in OCR extracted dates
                if party_id_clean in party_dates:
                    df.at[idx, expiry_col] = party_dates[party_id_clean]
                    updated_count += 1
                    print(f"  ✅ Row {idx + 1}: Filled License_Expiry_Date from OCR: {party_dates[party_id_clean]}")
                else:
                    # Set to "no expiry license" if party has no license
                    # Check if party has license type indicating no license
                    license_type_col = None
                    for col in df.columns:
                        if 'license' in col.lower() and 'type' in col.lower():
                            license_type_col = col
                            break
                    
                    if license_type_col:
                        license_type = str(row[license_type_col]).strip() if pd.notna(row[license_type_col]) else ''
                        # Check for "no license" indicators
                        no_license_indicators = [
                            'لا يوجد رخصة',
                            'لا يحمل',
                            'no license',
                            'none',
                            'null'
                        ]
                        if any(indicator.lower() in license_type.lower() for indicator in no_license_indicators):
                            df.at[idx, expiry_col] = "no expiry license"
                            print(f"  ℹ️ Row {idx + 1}: Set to 'no expiry license' (party has no license)")
                    else:
                        # If we can't determine, leave empty or set to "no expiry license"
                        df.at[idx, expiry_col] = "no expiry license"
                        print(f"  ℹ️ Row {idx + 1}: Set to 'no expiry license' (no license type info)")
            else:
                print(f"  ✓ Row {idx + 1}: License_Expiry_Date already exists: {current_expiry}")
        
        print(f"\n✓ Updated {updated_count} rows with license expiry dates from OCR")
        return df
    
    def process_claim_data_with_ocr(self, claim_data: Dict, ocr_text: str = None, 
                                    base64_image: str = None) -> Dict:
        """
        Process claim data and fill in missing license expiry dates from OCR
        
        Args:
            claim_data: Claim data dictionary with Parties array
            ocr_text: OCR text from Najm report (optional)
            base64_image: Base64 encoded image (optional)
        
        Returns:
            Updated claim data with license expiry dates filled in
        """
        if "Parties" not in claim_data:
            return claim_data
        
        # Extract license expiry dates from OCR
        party_dates = {}
        if ocr_text:
            party_dates = self.extract_all_license_expiry_dates(ocr_text)
        elif base64_image:
            # Would process image here
            pass
        
        # Update parties with missing license expiry dates
        print(f"\n  🔍 Processing {len(claim_data['Parties'])} parties with OCR data...")
        print(f"  🔍 Extracted {len(party_dates)} license expiry dates from OCR: {party_dates}")
        
        for party in claim_data["Parties"]:
            party_id = str(party.get("Party_ID", "")).strip()
            current_expiry = str(party.get("License_Expiry_Date", "")).strip()
            
            # Clean Party ID for matching - multiple strategies
            party_id_clean = re.sub(r'[^\d]', '', party_id)
            party_id_original = party_id
            
            print(f"\n  🔍 Processing Party: {party.get('Party', 'Unknown')}")
            print(f"     Party_ID (original): {party_id_original}")
            print(f"     Party_ID (cleaned): {party_id_clean}")
            print(f"     Current License_Expiry_Date: {current_expiry}")
            
            # Check if current expiry is empty/null/non-existent
            if not current_expiry or current_expiry.lower() in ['nan', 'none', 'null', '', 'not identify']:
                # Try multiple matching strategies
                matched_date = None
                
                # Strategy 1: Exact match with cleaned ID
                if party_id_clean in party_dates:
                    matched_date = party_dates[party_id_clean]
                    print(f"     ✅ Found exact match: {matched_date}")
                
                # Strategy 2: Try partial match (last 8-9 digits) - common when IDs are truncated
                if not matched_date and len(party_id_clean) >= 8:
                    for ocr_party_id, date_value in party_dates.items():
                        ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                        # Try last 8-9 digits match
                        if len(ocr_id_clean) >= 8 and len(party_id_clean) >= 8:
                            if party_id_clean[-8:] == ocr_id_clean[-8:] or party_id_clean[-9:] == ocr_id_clean[-9:]:
                                matched_date = date_value
                                print(f"     ✅ Found partial match (last digits): {ocr_party_id} → {matched_date}")
                                break
                
                # Strategy 2.5: Try fuzzy match (handle typos like 1083668838 vs 108366838)
                if not matched_date and len(party_id_clean) >= 8:
                    for ocr_party_id, date_value in party_dates.items():
                        ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                        # Check if IDs are very similar (differ by 1-2 digits)
                        if len(ocr_id_clean) >= 8 and len(party_id_clean) >= 8:
                            # Check if one contains the other (handle extra digits)
                            if party_id_clean in ocr_id_clean or ocr_id_clean in party_id_clean:
                                matched_date = date_value
                                print(f"     ✅ Found fuzzy match (contains): {ocr_party_id} → {matched_date}")
                                break
                            # Check similarity using SequenceMatcher
                            similarity = SequenceMatcher(None, party_id_clean, ocr_id_clean).ratio()
                            if similarity >= 0.85:  # 85% similarity threshold
                                matched_date = date_value
                                print(f"     ✅ Found fuzzy match (similarity {similarity:.2f}): {ocr_party_id} → {matched_date}")
                                break
                            # Check Levenshtein-like similarity (simple version) for same-length IDs
                            if len(party_id_clean) == len(ocr_id_clean):
                                diff_count = sum(1 for a, b in zip(party_id_clean, ocr_id_clean) if a != b)
                                if diff_count <= 2:  # Allow up to 2 digit differences
                                    matched_date = date_value
                                    print(f"     ✅ Found fuzzy match (similar, {diff_count} diffs): {ocr_party_id} → {matched_date}")
                                    break
                
                # Strategy 3: Try string matching (handle type differences)
                if not matched_date:
                    for ocr_party_id, date_value in party_dates.items():
                        ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                        if ocr_id_clean == party_id_clean or str(ocr_party_id).strip() == party_id_original.strip():
                            matched_date = date_value
                            print(f"     ✅ Found string match: {ocr_party_id} → {matched_date}")
                            break
                
                # Strategy 4: Try order-based assignment (if we have dates but no ID match)
                if not matched_date and party_dates:
                    # Get all used dates
                    used_dates = set()
                    for p in claim_data["Parties"]:
                        exp = str(p.get("License_Expiry_Date", "")).strip()
                        if exp and exp.lower() not in ['nan', 'none', 'null', '', 'not identify']:
                            used_dates.add(exp)
                    
                    # Find first unused date
                    for ocr_party_id, date_value in party_dates.items():
                        if date_value not in used_dates:
                            matched_date = date_value
                            print(f"     ✅ Using order-based assignment: {date_value}")
                            break
                
                if matched_date:
                    party["License_Expiry_Date"] = matched_date
                    party["License_Expiry_Last_Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"  ✅ Party {party.get('Party', 'Unknown')}: Filled License_Expiry_Date from OCR: {matched_date}")
                else:
                    # Check license type to determine if "no expiry license"
                    license_type = str(party.get("License_Type_From_Najm", "")).strip()
                    no_license_indicators = [
                        'لا يوجد رخصة',
                        'لا يحمل',
                        'no license',
                        'none',
                        'null'
                    ]
                    if any(indicator.lower() in license_type.lower() for indicator in no_license_indicators):
                        party["License_Expiry_Date"] = "no expiry license"
                        print(f"  ℹ️ Party {party.get('Party', 'Unknown')}: Set to 'no expiry license' (no license)")
                    else:
                        # If we can't determine, try to extract directly from OCR text for this party
                        if ocr_text and party_id_clean:
                            direct_date = self.extract_license_expiry_from_ocr_text(ocr_text, party_id_clean)
                            if direct_date:
                                party["License_Expiry_Date"] = direct_date
                                party["License_Expiry_Last_Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                print(f"  ✅ Party {party.get('Party', 'Unknown')}: Extracted directly from OCR: {direct_date}")
                            else:
                                party["License_Expiry_Date"] = "no expiry license"
                                print(f"  ⚠️ Party {party.get('Party', 'Unknown')}: Not found in OCR, set to 'no expiry license'")
                        else:
                            party["License_Expiry_Date"] = "no expiry license"
                            print(f"  ⚠️ Party {party.get('Party', 'Unknown')}: Set to 'no expiry license' (not found in OCR)")
            else:
                print(f"  ✓ Party {party.get('Party', 'Unknown')}: License_Expiry_Date already exists: {current_expiry}")
        
        return claim_data


def test_extraction():
    """Test extraction with sample Najm report format"""
    processor = ExcelOCRLicenseProcessor()
    
    # Sample OCR text matching Najm report format
    ocr_text = """
    Party (1)
    Driver Info / معلومات السائق:
    ID Number / رقم الهوية: 
    License Type / نوع الرخصة: لا يوجد رخصة / لا يحمل
    Expiry Date / تاريخ إنتهاء الرخصة: 
    
    Party (2)
    Driver Info / معلومات السائق:
    Name / الاسم: احمد محمد دحلان ال شاعث
    ID Number / رقم الهوية: 108366838
    License Type / نوع الرخصة: رخصة خاصة
    Expiry Date / تاريخ إنتهاء الرخصة: 08/07/2028
    """
    
    print("Testing OCR extraction...")
    print("=" * 60)
    party_dates = processor.extract_all_license_expiry_dates(ocr_text)
    print(f"\nExtracted dates: {party_dates}")
    
    # Test with Party ID 108366838
    print("\n" + "=" * 60)
    print("Testing specific Party ID: 108366838")
    test_party_id = "108366838"
    test_party_id_typo = "1083668838"  # With typo
    
    for test_id in [test_party_id, test_party_id_typo]:
        print(f"\nTesting Party ID: {test_id}")
        if test_id in party_dates:
            print(f"  ✅ Found: {party_dates[test_id]}")
        else:
            # Try cleaned version
            cleaned = re.sub(r'[^\d]', '', test_id)
            if cleaned in party_dates:
                print(f"  ✅ Found (cleaned): {party_dates[cleaned]}")
            else:
                print(f"  ⚠️ Not found directly, trying extraction...")
                direct = processor.extract_license_expiry_from_ocr_text(ocr_text, cleaned)
                if direct:
                    print(f"  ✅ Extracted directly: {direct}")
                else:
                    print(f"  ❌ Not found")


def main():
    """Example usage"""
    processor = ExcelOCRLicenseProcessor()
    
    # Run test first
    test_extraction()
    
    # Example: Process Excel file with OCR text
    excel_path = "example_claims.xlsx"
    ocr_text = """
    Party (1)
    رقم الهوية: 1234567890
    نوع الرخصة: لا يوجد رخصة / لا يحمل
    تاريخ إنتهاء الرخصة: 
    
    Party (2)
    رقم الهوية: 108366838
    نوع الرخصة: رخصة خاصة
    تاريخ إنتهاء الرخصة: 08/07/2028
    """
    
    if os.path.exists(excel_path):
        df = processor.process_excel_with_ocr(excel_path, ocr_text=ocr_text)
        # Save updated Excel
        output_path = excel_path.replace('.xlsx', '_updated.xlsx')
        df.to_excel(output_path, index=False)
        print(f"\n✓ Saved updated Excel to: {output_path}")
    else:
        print(f"⚠️ Excel file not found: {excel_path}")
        print("Example OCR text processing:")
        party_dates = processor.extract_all_license_expiry_dates(ocr_text)
        print(f"Extracted dates: {party_dates}")


if __name__ == "__main__":
    main()

"""
Excel + OCR License Expiry Date Processor
Processes Excel sheets and extracts license expiry dates from OCR/images (Najm reports)
Handles missing/null license expiry dates
"""

import pandas as pd
import base64
import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import os
from PIL import Image
import io
from difflib import SequenceMatcher


class ExcelOCRLicenseProcessor:
    """Processes Excel sheets and extracts license expiry dates from OCR/images"""
    
    def __init__(self):
        """Initialize the processor"""
        self.party_date_matches = {}  # Store Party ID -> License Expiry Date mappings
    
    def extract_license_expiry_from_ocr_text(self, ocr_text: str, party_id: str = None) -> Optional[str]:
        """
        Extract license expiry date from OCR text (Najm report format)
        
        Args:
            ocr_text: OCR text from Najm report
            party_id: Party ID to match (optional)
        
        Returns:
            License expiry date in format DD/MM/YYYY or None if not found
        """
        if not ocr_text:
            return None
        
        # Patterns for license expiry date in Najm reports - improved
        patterns = [
            # Arabic pattern: تاريخ إنتهاء الرخصة followed by date (more flexible)
            r'تاريخ\s*إنتهاء\s*الرخصة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'تاريخ\s*إنتهاء[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'Expiry\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            # Date near "رخصة" (license) - improved
            r'رخصة[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'نوع\s*الرخصة[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            # Date after license type
            r'رخصة\s*خاصة[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'رخصة\s*عمومية[^/]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        ]
        
        # If party_id provided, look for dates near that party's section
        if party_id:
            # Clean party ID for matching
            party_id_clean = re.sub(r'[^\d]', '', str(party_id))
            
            # OPTIMIZED: Fast exact match patterns (combined for speed)
            # Strategy 1: Exact match with Party ID - try most common pattern first
            quick_pattern = rf'{re.escape(party_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
            match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            if match:
                date_str = match.group(1)
                if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                    return date_str
            
            # Try other patterns only if first failed
            party_patterns = [
                rf'رقم\s*الهوية[:\s]*{re.escape(party_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})',
                rf'Party\s*\(\d+\)[^P]*?{re.escape(party_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})',
            ]
            
            for pattern in party_patterns:
                match = re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                if match:
                    date_str = match.group(1)
                    if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                        return date_str
            
            # OPTIMIZED Strategy 2: Fast fuzzy match - try last 8-9 digits (most common case)
            if len(party_id_clean) >= 9:
                last_9 = party_id_clean[-9:]
                quick_pattern = rf'{re.escape(last_9)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
                match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                if match:
                    date_str = match.group(1)
                    if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                        return date_str
                
                if len(party_id_clean) >= 8:
                    last_8 = party_id_clean[-8:]
                    quick_pattern = rf'{re.escape(last_8)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
                    match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                    if match:
                        date_str = match.group(1)
                        if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                            return date_str
            
            # OPTIMIZED Strategy 3: Fast similarity matching - only check if contains/contained (fastest check)
            all_party_ids = re.findall(r'\b\d{9,10}\b', ocr_text)
            
            # Fast check: if Party ID contains or is contained in any OCR ID
            for ocr_party_id in all_party_ids[:10]:  # Limit to first 10 for speed
                ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                if party_id_clean in ocr_id_clean or ocr_id_clean in party_id_clean:
                    # Found similar ID, extract date near it
                    quick_pattern = rf'{re.escape(ocr_id_clean)}[^0-9]*?(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})'
                    match = re.search(quick_pattern, ocr_text, re.IGNORECASE | re.UNICODE)
                    if match:
                        date_str = match.group(1)
                        if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                            return date_str
            
            # OPTIMIZED Strategy 4: Fast context search - only for first few similar IDs
            for ocr_party_id in all_party_ids[:5]:  # Limit to first 5 for speed
                ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                if party_id_clean in ocr_id_clean or ocr_id_clean in party_id_clean:
                    id_pos = ocr_text.find(ocr_party_id)
                    if id_pos != -1:
                        search_text = ocr_text[id_pos:id_pos + 500]  # Reduced from 1000
                        match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})', search_text)
                        if match:
                            date_str = match.group(1)
                            if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025']:
                                return date_str
            
            # OPTIMIZED Strategy 5: Fast fallback - extract first valid date if Party ID not found
            if not all_party_ids:
                # Quick extraction of first valid date
                match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})', ocr_text)
                if match:
                    date_str = match.group(1)
                    if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025', '2025-11-19']:
                        return date_str
        
        # OPTIMIZED: Try patterns - return first valid date (early exit)
        for pattern in patterns:
            match = re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            if match:
                date_str = match.group(1)
                if self._validate_date(date_str) and date_str not in ['19/11/2025', '19-11-2025', '2025-11-19']:
                    return date_str
        
        return None
    
    def _validate_date(self, date_str: str) -> bool:
        """
        Validate if date string is a valid license expiry date (not birth date)
        
        Args:
            date_str: Date string in format DD/MM/YYYY or similar
        
        Returns:
            True if valid license expiry date, False otherwise
        """
        try:
            # Parse date
            date_parts = re.split(r'[/-]', date_str.strip())
            if len(date_parts) != 3:
                return False
            
            # Determine year position
            year = None
            for part in date_parts:
                if len(part) == 4:
                    year = int(part)
                    break
            
            if not year:
                return False
            
            # License expiry dates should be:
            # - Gregorian: >= 2010 (current/future licenses)
            # - Hijri: 1400-1600 range
            if 1400 <= year <= 1600:  # Hijri date
                return True
            elif year >= 2010:  # Gregorian date (current/future)
                return True
            elif 1900 <= year < 2010:  # Likely birth date, reject
                return False
            else:
                return False
        except:
            return False
    
    def extract_all_license_expiry_dates(self, ocr_text: str) -> Dict[str, str]:
        """
        Extract all license expiry dates from OCR text, matching with Party IDs
        
        Args:
            ocr_text: OCR text from Najm report
        
        Returns:
            Dictionary mapping Party ID -> License Expiry Date
        """
        party_dates = {}
        
        if not ocr_text:
            return party_dates
        
        # Method 1: Split by Party sections
        # Look for "Party (1)", "Party (2)", etc. or Arabic equivalents
        party_section_patterns = [
            r'Party\s*\((\d+)\)',
            r'الطرف\s*\((\d+)\)',
            r'Party\s*(\d+)',
        ]
        
        party_sections = []
        for pattern in party_section_patterns:
            matches = list(re.finditer(pattern, ocr_text, re.IGNORECASE | re.UNICODE))
            if matches:
                # Split text by party markers
                for i, match in enumerate(matches):
                    start_pos = match.end()
                    end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(ocr_text)
                    party_num = match.group(1)
                    party_text = ocr_text[start_pos:end_pos]
                    party_sections.append((party_num, party_text))
                break
        
        # If no party sections found, try to extract from entire text
        if not party_sections:
            party_sections = [("1", ocr_text)]
        
        for party_num, party_text in party_sections:
            # Extract Party ID from this section - improved patterns
            party_id_patterns = [
                r'رقم\s*الهوية[:\s]*(\d{8,10})',
                r'ID\s*Number[:\s]*(\d{8,10})',
                r'Party\s*ID[:\s]*(\d{8,10})',
                r'(\d{9,10})',  # Fallback: any 9-10 digit number (Saudi ID format)
            ]
            
            party_id = None
            party_id_match = None
            for pattern in party_id_patterns:
                matches = list(re.finditer(pattern, party_text, re.IGNORECASE | re.UNICODE))
                if matches:
                    # Take the first match that looks like an ID (9-10 digits)
                    for match in matches:
                        potential_id = match.group(1)
                        # Saudi ID is usually 9-10 digits, but also accept 8 digits for flexibility
                        if len(potential_id) >= 8:
                            party_id = potential_id
                            party_id_match = match
                            print(f"  🔍 Found Party ID in section {party_num}: {party_id}")
                            break
                    if party_id:
                        break
            
            # Extract license expiry date from this party's section - improved patterns
            expiry_date = None
            
            # Pattern 1: Look for "تاريخ إنتهاء الرخصة" or "Expiry Date" followed by date
            expiry_patterns = [
                r'تاريخ\s*إنتهاء\s*الرخصة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
                r'Expiry\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
                r'تاريخ\s*إنتهاء[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            ]
            
            for pattern in expiry_patterns:
                matches = list(re.finditer(pattern, party_text, re.IGNORECASE | re.UNICODE))
                for match in matches:
                    date_str = match.group(1)
                    if self._validate_date(date_str):
                        expiry_date = date_str
                        break
                if expiry_date:
                    break
            
            # Pattern 2: If party_id found, look for date near the ID
            if not expiry_date and party_id:
                # Look for date within 500 characters after Party ID
                if party_id_match:
                    id_end = party_id_match.end()
                    search_text = party_text[id_end:id_end + 500]
                    date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})'
                    date_matches = list(re.finditer(date_pattern, search_text))
                    for date_match in date_matches:
                        date_str = date_match.group(1)
                        if self._validate_date(date_str):
                            expiry_date = date_str
                            break
            
            # Pattern 3: Look for any valid date in party section (fallback)
            if not expiry_date:
                date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})'
                date_matches = list(re.finditer(date_pattern, party_text))
                for date_match in date_matches:
                    date_str = date_match.group(1)
                    if self._validate_date(date_str):
                        # Check if it's near license-related keywords
                        date_pos = date_match.start()
                        context_before = party_text[max(0, date_pos - 100):date_pos]
                        if any(keyword in context_before for keyword in ['رخصة', 'license', 'إنتهاء', 'expiry']):
                            expiry_date = date_str
                            break
            
            # Store the result
            if party_id and expiry_date:
                party_dates[party_id] = expiry_date
                print(f"  ✅ Extracted: Party ID {party_id} → License Expiry: {expiry_date}")
                # Also store variations (last 8-9 digits) for fuzzy matching
                if len(party_id) >= 9:
                    party_dates[party_id[-9:]] = expiry_date
                if len(party_id) >= 8:
                    party_dates[party_id[-8:]] = expiry_date
            elif expiry_date:
                # If we found a date but no Party ID, store with party number
                party_dates[f"Party_{party_num}"] = expiry_date
                print(f"  ✅ Extracted: Party {party_num} → License Expiry: {expiry_date}")
            elif party_id:
                print(f"  ⚠️ Found Party ID {party_id} but no license expiry date")
        
        return party_dates
    
    def process_base64_image(self, base64_data: str) -> Dict[str, str]:
        """
        Process base64 encoded image and extract license expiry dates
        
        Args:
            base64_data: Base64 encoded image string
        
        Returns:
            Dictionary mapping Party ID -> License Expiry Date
        """
        try:
            # Decode base64 to image
            if base64_data.startswith('data:image'):
                # Remove data URL prefix
                base64_data = base64_data.split(',')[1]
            
            image_data = base64.b64decode(base64_data)
            image = Image.open(io.BytesIO(image_data))
            
            # For now, we'll use OCR text if provided
            # In production, you'd use Tesseract or similar OCR here
            # For this implementation, we assume OCR text is already extracted
            # and passed separately
            
            return {}
        except Exception as e:
            print(f"  ⚠️ Error processing base64 image: {str(e)[:100]}")
            return {}
    
    def process_excel_with_ocr(self, excel_path: str = None, df: pd.DataFrame = None,
                               ocr_text: str = None, base64_image: str = None, 
                               case_number: str = None) -> pd.DataFrame:
        """
        Process Excel sheet and fill in missing license expiry dates from OCR
        
        Args:
            excel_path: Path to Excel file (optional if df provided)
            df: DataFrame to process (optional if excel_path provided)
            ocr_text: OCR text from Najm report (optional)
            base64_image: Base64 encoded image (optional)
            case_number: Case number for matching (optional)
        
        Returns:
            Updated DataFrame with license expiry dates filled in
        """
        # Read Excel file or use provided DataFrame
        if df is not None:
            print(f"✓ Using provided DataFrame: {len(df)} rows")
        elif excel_path:
            try:
                df = pd.read_excel(excel_path)
                print(f"✓ Loaded Excel file: {len(df)} rows")
            except Exception as e:
                print(f"✗ Error reading Excel file: {str(e)}")
                return pd.DataFrame()
        else:
            print("✗ Error: Either excel_path or df must be provided")
            return pd.DataFrame()
        
        # Extract license expiry dates from OCR
        party_dates = {}
        if ocr_text:
            party_dates = self.extract_all_license_expiry_dates(ocr_text)
        elif base64_image:
            # Process image and extract OCR text (would need OCR library)
            # For now, assume OCR text is provided separately
            pass
        
        # Find Party_ID column (handle different column names)
        party_id_col = None
        for col in df.columns:
            if 'party' in col.lower() and 'id' in col.lower():
                party_id_col = col
                break
        
        if not party_id_col:
            print("⚠️ Warning: Party ID column not found in Excel")
            return df
        
        # Find License_Expiry_Date column
        expiry_col = None
        for col in df.columns:
            if 'license' in col.lower() and 'expiry' in col.lower():
                expiry_col = col
                break
        
        if not expiry_col:
            # Create new column if it doesn't exist
            expiry_col = 'License_Expiry_Date'
            df[expiry_col] = ''
        
        # Fill in missing license expiry dates
        updated_count = 0
        for idx, row in df.iterrows():
            party_id = str(row[party_id_col]).strip() if pd.notna(row[party_id_col]) else ''
            current_expiry = str(row[expiry_col]).strip() if pd.notna(row[expiry_col]) else ''
            
            # Clean Party ID for matching
            party_id_clean = re.sub(r'[^\d]', '', party_id)
            
            # Check if current expiry is empty/null/non-existent or "not identify"
            if not current_expiry or current_expiry.lower() in ['nan', 'none', 'null', '', 'not identify', 'notidentify']:
                # Try to find in OCR extracted dates
                if party_id_clean in party_dates:
                    df.at[idx, expiry_col] = party_dates[party_id_clean]
                    updated_count += 1
                    print(f"  ✅ Row {idx + 1}: Filled License_Expiry_Date from OCR: {party_dates[party_id_clean]}")
                else:
                    # Set to "no expiry license" if party has no license
                    # Check if party has license type indicating no license
                    license_type_col = None
                    for col in df.columns:
                        if 'license' in col.lower() and 'type' in col.lower():
                            license_type_col = col
                            break
                    
                    if license_type_col:
                        license_type = str(row[license_type_col]).strip() if pd.notna(row[license_type_col]) else ''
                        # Check for "no license" indicators
                        no_license_indicators = [
                            'لا يوجد رخصة',
                            'لا يحمل',
                            'no license',
                            'none',
                            'null'
                        ]
                        if any(indicator.lower() in license_type.lower() for indicator in no_license_indicators):
                            df.at[idx, expiry_col] = "no expiry license"
                            print(f"  ℹ️ Row {idx + 1}: Set to 'no expiry license' (party has no license)")
                    else:
                        # If we can't determine, leave empty or set to "no expiry license"
                        df.at[idx, expiry_col] = "no expiry license"
                        print(f"  ℹ️ Row {idx + 1}: Set to 'no expiry license' (no license type info)")
            else:
                print(f"  ✓ Row {idx + 1}: License_Expiry_Date already exists: {current_expiry}")
        
        print(f"\n✓ Updated {updated_count} rows with license expiry dates from OCR")
        return df
    
    def process_claim_data_with_ocr(self, claim_data: Dict, ocr_text: str = None, 
                                    base64_image: str = None) -> Dict:
        """
        Process claim data and fill in missing license expiry dates from OCR
        
        Args:
            claim_data: Claim data dictionary with Parties array
            ocr_text: OCR text from Najm report (optional)
            base64_image: Base64 encoded image (optional)
        
        Returns:
            Updated claim data with license expiry dates filled in
        """
        if "Parties" not in claim_data:
            return claim_data
        
        # Extract license expiry dates from OCR
        party_dates = {}
        if ocr_text:
            party_dates = self.extract_all_license_expiry_dates(ocr_text)
        elif base64_image:
            # Would process image here
            pass
        
        # Update parties with missing license expiry dates
        print(f"\n  🔍 Processing {len(claim_data['Parties'])} parties with OCR data...")
        print(f"  🔍 Extracted {len(party_dates)} license expiry dates from OCR: {party_dates}")
        
        for party in claim_data["Parties"]:
            party_id = str(party.get("Party_ID", "")).strip()
            current_expiry = str(party.get("License_Expiry_Date", "")).strip()
            
            # Clean Party ID for matching - multiple strategies
            party_id_clean = re.sub(r'[^\d]', '', party_id)
            party_id_original = party_id
            
            print(f"\n  🔍 Processing Party: {party.get('Party', 'Unknown')}")
            print(f"     Party_ID (original): {party_id_original}")
            print(f"     Party_ID (cleaned): {party_id_clean}")
            print(f"     Current License_Expiry_Date: {current_expiry}")
            
            # Check if current expiry is empty/null/non-existent
            if not current_expiry or current_expiry.lower() in ['nan', 'none', 'null', '', 'not identify']:
                # Try multiple matching strategies
                matched_date = None
                
                # Strategy 1: Exact match with cleaned ID
                if party_id_clean in party_dates:
                    matched_date = party_dates[party_id_clean]
                    print(f"     ✅ Found exact match: {matched_date}")
                
                # Strategy 2: Try partial match (last 8-9 digits) - common when IDs are truncated
                if not matched_date and len(party_id_clean) >= 8:
                    for ocr_party_id, date_value in party_dates.items():
                        ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                        # Try last 8-9 digits match
                        if len(ocr_id_clean) >= 8 and len(party_id_clean) >= 8:
                            if party_id_clean[-8:] == ocr_id_clean[-8:] or party_id_clean[-9:] == ocr_id_clean[-9:]:
                                matched_date = date_value
                                print(f"     ✅ Found partial match (last digits): {ocr_party_id} → {matched_date}")
                                break
                
                # Strategy 2.5: Try fuzzy match (handle typos like 1083668838 vs 108366838)
                if not matched_date and len(party_id_clean) >= 8:
                    for ocr_party_id, date_value in party_dates.items():
                        ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                        # Check if IDs are very similar (differ by 1-2 digits)
                        if len(ocr_id_clean) >= 8 and len(party_id_clean) >= 8:
                            # Check if one contains the other (handle extra digits)
                            if party_id_clean in ocr_id_clean or ocr_id_clean in party_id_clean:
                                matched_date = date_value
                                print(f"     ✅ Found fuzzy match (contains): {ocr_party_id} → {matched_date}")
                                break
                            # Check similarity using SequenceMatcher
                            similarity = SequenceMatcher(None, party_id_clean, ocr_id_clean).ratio()
                            if similarity >= 0.85:  # 85% similarity threshold
                                matched_date = date_value
                                print(f"     ✅ Found fuzzy match (similarity {similarity:.2f}): {ocr_party_id} → {matched_date}")
                                break
                            # Check Levenshtein-like similarity (simple version) for same-length IDs
                            if len(party_id_clean) == len(ocr_id_clean):
                                diff_count = sum(1 for a, b in zip(party_id_clean, ocr_id_clean) if a != b)
                                if diff_count <= 2:  # Allow up to 2 digit differences
                                    matched_date = date_value
                                    print(f"     ✅ Found fuzzy match (similar, {diff_count} diffs): {ocr_party_id} → {matched_date}")
                                    break
                
                # Strategy 3: Try string matching (handle type differences)
                if not matched_date:
                    for ocr_party_id, date_value in party_dates.items():
                        ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                        if ocr_id_clean == party_id_clean or str(ocr_party_id).strip() == party_id_original.strip():
                            matched_date = date_value
                            print(f"     ✅ Found string match: {ocr_party_id} → {matched_date}")
                            break
                
                # Strategy 4: Try order-based assignment (if we have dates but no ID match)
                if not matched_date and party_dates:
                    # Get all used dates
                    used_dates = set()
                    for p in claim_data["Parties"]:
                        exp = str(p.get("License_Expiry_Date", "")).strip()
                        if exp and exp.lower() not in ['nan', 'none', 'null', '', 'not identify']:
                            used_dates.add(exp)
                    
                    # Find first unused date
                    for ocr_party_id, date_value in party_dates.items():
                        if date_value not in used_dates:
                            matched_date = date_value
                            print(f"     ✅ Using order-based assignment: {date_value}")
                            break
                
                if matched_date:
                    party["License_Expiry_Date"] = matched_date
                    party["License_Expiry_Last_Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"  ✅ Party {party.get('Party', 'Unknown')}: Filled License_Expiry_Date from OCR: {matched_date}")
                else:
                    # Check license type to determine if "no expiry license"
                    license_type = str(party.get("License_Type_From_Najm", "")).strip()
                    no_license_indicators = [
                        'لا يوجد رخصة',
                        'لا يحمل',
                        'no license',
                        'none',
                        'null'
                    ]
                    if any(indicator.lower() in license_type.lower() for indicator in no_license_indicators):
                        party["License_Expiry_Date"] = "no expiry license"
                        print(f"  ℹ️ Party {party.get('Party', 'Unknown')}: Set to 'no expiry license' (no license)")
                    else:
                        # If we can't determine, try to extract directly from OCR text for this party
                        if ocr_text and party_id_clean:
                            direct_date = self.extract_license_expiry_from_ocr_text(ocr_text, party_id_clean)
                            if direct_date:
                                party["License_Expiry_Date"] = direct_date
                                party["License_Expiry_Last_Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                print(f"  ✅ Party {party.get('Party', 'Unknown')}: Extracted directly from OCR: {direct_date}")
                            else:
                                party["License_Expiry_Date"] = "no expiry license"
                                print(f"  ⚠️ Party {party.get('Party', 'Unknown')}: Not found in OCR, set to 'no expiry license'")
                        else:
                            party["License_Expiry_Date"] = "no expiry license"
                            print(f"  ⚠️ Party {party.get('Party', 'Unknown')}: Set to 'no expiry license' (not found in OCR)")
            else:
                print(f"  ✓ Party {party.get('Party', 'Unknown')}: License_Expiry_Date already exists: {current_expiry}")
        
        return claim_data


def test_extraction():
    """Test extraction with sample Najm report format"""
    processor = ExcelOCRLicenseProcessor()
    
    # Sample OCR text matching Najm report format
    ocr_text = """
    Party (1)
    Driver Info / معلومات السائق:
    ID Number / رقم الهوية: 
    License Type / نوع الرخصة: لا يوجد رخصة / لا يحمل
    Expiry Date / تاريخ إنتهاء الرخصة: 
    
    Party (2)
    Driver Info / معلومات السائق:
    Name / الاسم: احمد محمد دحلان ال شاعث
    ID Number / رقم الهوية: 108366838
    License Type / نوع الرخصة: رخصة خاصة
    Expiry Date / تاريخ إنتهاء الرخصة: 08/07/2028
    """
    
    print("Testing OCR extraction...")
    print("=" * 60)
    party_dates = processor.extract_all_license_expiry_dates(ocr_text)
    print(f"\nExtracted dates: {party_dates}")
    
    # Test with Party ID 108366838
    print("\n" + "=" * 60)
    print("Testing specific Party ID: 108366838")
    test_party_id = "108366838"
    test_party_id_typo = "1083668838"  # With typo
    
    for test_id in [test_party_id, test_party_id_typo]:
        print(f"\nTesting Party ID: {test_id}")
        if test_id in party_dates:
            print(f"  ✅ Found: {party_dates[test_id]}")
        else:
            # Try cleaned version
            cleaned = re.sub(r'[^\d]', '', test_id)
            if cleaned in party_dates:
                print(f"  ✅ Found (cleaned): {party_dates[cleaned]}")
            else:
                print(f"  ⚠️ Not found directly, trying extraction...")
                direct = processor.extract_license_expiry_from_ocr_text(ocr_text, cleaned)
                if direct:
                    print(f"  ✅ Extracted directly: {direct}")
                else:
                    print(f"  ❌ Not found")


def main():
    """Example usage"""
    processor = ExcelOCRLicenseProcessor()
    
    # Run test first
    test_extraction()
    
    # Example: Process Excel file with OCR text
    excel_path = "example_claims.xlsx"
    ocr_text = """
    Party (1)
    رقم الهوية: 1234567890
    نوع الرخصة: لا يوجد رخصة / لا يحمل
    تاريخ إنتهاء الرخصة: 
    
    Party (2)
    رقم الهوية: 108366838
    نوع الرخصة: رخصة خاصة
    تاريخ إنتهاء الرخصة: 08/07/2028
    """
    
    if os.path.exists(excel_path):
        df = processor.process_excel_with_ocr(excel_path, ocr_text=ocr_text)
        # Save updated Excel
        output_path = excel_path.replace('.xlsx', '_updated.xlsx')
        df.to_excel(output_path, index=False)
        print(f"\n✓ Saved updated Excel to: {output_path}")
    else:
        print(f"⚠️ Excel file not found: {excel_path}")
        print("Example OCR text processing:")
        party_dates = processor.extract_all_license_expiry_dates(ocr_text)
        print(f"Extracted dates: {party_dates}")


if __name__ == "__main__":
    main()

