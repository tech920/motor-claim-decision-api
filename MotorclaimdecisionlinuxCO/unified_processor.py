"""
Unified Claim Processor
Converts XML/JSON to standardized format, then processes with Ollama
Handles different column names and formats automatically
"""

import pandas as pd
import json
import xml.etree.ElementTree as ET
from claim_processor import ClaimProcessor
from typing import Dict, List, Any, Optional
import os
from datetime import datetime
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import base64
from io import BytesIO
from PIL import Image
import pytesseract
import requests
try:
    from pdf2image import convert_from_bytes
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("Warning: pdf2image not installed. PDF support disabled.")
try:
    from hijri_converter import Hijri, Gregorian
    HIJRI_SUPPORT = True
except ImportError:
    try:
        # Try alternative import name
        from hijri_converter.convert import Hijri, Gregorian
        HIJRI_SUPPORT = True
    except ImportError:
        HIJRI_SUPPORT = False
        print("Warning: hijri-converter not installed. Hijri date conversion disabled.")
        print("Install it with: pip install hijri-converter")

# Configure Tesseract OCR path
# Note: Windows .exe files won't work on Linux, so we prioritize system installation
# Try system Tesseract first (recommended for Linux), then custom paths, then Windows path
TESSERACT_PATHS = [
    "/usr/bin/tesseract",  # Common Linux path (system installation - recommended)
    "/usr/local/bin/tesseract",  # Alternative system path
    "/opt/Library/Tesseract-OCR/tesseract",  # Custom Linux path (if Linux binary exists)
    "/opt/Library/Tesseract-OCR/bin/tesseract",  # Alternative structure
    "/AI/applications/Library/Tesseract-OCR/tesseract",  # Legacy path (fallback)
    "/AI/applications/Library/Tesseract-OCR/bin/tesseract",  # Legacy alternative
    r"D:\Library\Tesseract-OCR\tesseract.exe",  # Windows path (for Windows development)
]

tesseract_found = False
for tesseract_path in TESSERACT_PATHS:
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        print(f"✓ Tesseract OCR configured: {tesseract_path}")
        tesseract_found = True
        break

if not tesseract_found:
    # Try to find tesseract in PATH first (system installation - recommended for Linux)
    import shutil
    tesseract_cmd = shutil.which("tesseract")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        print(f"✓ Tesseract OCR found in PATH: {tesseract_cmd}")
        tesseract_found = True
    
    if not tesseract_found:
        # Search recursively in custom library directory (skip .exe files on Linux)
        import glob
        import platform
        
        # On Linux, skip .exe files (Windows executables won't work)
        if platform.system() != "Windows":
            search_paths = [
                "/opt/Library/Tesseract-OCR/**/tesseract",  # Primary path
                "/AI/applications/Library/Tesseract-OCR/**/tesseract",  # Legacy path
            ]
        else:
            search_paths = [
                "/opt/Library/Tesseract-OCR/**/tesseract",  # Primary path
                "/opt/Library/Tesseract-OCR/**/tesseract.exe",  # Windows executable
                "/AI/applications/Library/Tesseract-OCR/**/tesseract",  # Legacy path
                "/AI/applications/Library/Tesseract-OCR/**/tesseract.exe",  # Legacy Windows
            ]
        
        for pattern in search_paths:
            custom_tesseract = glob.glob(pattern, recursive=True)
            if custom_tesseract:
                # Filter to only actual files (not directories) and skip .exe on Linux
                for path in custom_tesseract:
                    if os.path.isfile(path) and (platform.system() == "Windows" or not path.endswith('.exe')):
                        pytesseract.pytesseract.tesseract_cmd = path
                        print(f"✓ Tesseract OCR found: {path}")
                        tesseract_found = True
                        break
                if tesseract_found:
                    break
    
    if not tesseract_found:
        print(f"⚠ Warning: Tesseract not found")
        print(f"   Note: Windows .exe files in /opt/Library/ or /AI/applications/Library/ won't work on Linux")
        print(f"   Please install Tesseract via: sudo yum install -y tesseract")
        print(f"   Or run: sudo ./install_tesseract_linux.sh")
        print(f"   Using system default (may not work)")

# Configure Poppler path for PDF conversion
# Try custom Linux paths first, then Windows path, then Linux default
POPPLER_PATHS = [
    "/opt/Library/poppler-25.07.0/Library/bin",  # Custom Linux path (Windows structure) - Primary
    "/opt/Library/poppler-25.07.0/bin",  # Alternative Linux structure - Primary
    "/opt/Library/poppler-25.07.0/usr/bin",  # Another alternative - Primary
    "/AI/applications/Library/poppler-25.07.0/Library/bin",  # Legacy path
    "/AI/applications/Library/poppler-25.07.0/bin",  # Legacy alternative
    "/AI/applications/Library/poppler-25.07.0/usr/bin",  # Legacy alternative
    r"D:\Library\poppler-25.07.0\Library\bin",  # Windows path
    "/usr/bin",  # Common Linux path
]

POPPLER_PATH = None  # Will be set based on what's available

if PDF_SUPPORT:
    poppler_found = False
    for poppler_path in POPPLER_PATHS:
        if os.path.exists(poppler_path):
            # Check if pdftoppm exists in this path
            pdftoppm_path = os.path.join(poppler_path, "pdftoppm")
            if os.path.exists(pdftoppm_path) or os.path.exists(poppler_path):
                POPPLER_PATH = poppler_path
                os.environ['PATH'] = poppler_path + os.pathsep + os.environ.get('PATH', '')
                print(f"✓ Poppler configured: {poppler_path}")
                poppler_found = True
                break
    
    if not poppler_found:
        # Search for pdftoppm in custom library directory
        import glob
        custom_pdftoppm = glob.glob("/AI/applications/Library/poppler-25.07.0/**/pdftoppm", recursive=True)
        if custom_pdftoppm:
            poppler_dir = os.path.dirname(custom_pdftoppm[0])
            POPPLER_PATH = poppler_dir
            os.environ['PATH'] = poppler_dir + os.pathsep + os.environ.get('PATH', '')
            print(f"✓ Poppler found: {poppler_dir}")
        else:
            print(f"⚠ Warning: Poppler not found, PDF conversion may not work")


class UnifiedClaimProcessor:
    """Unified processor that handles XML/JSON and different column names"""
    
    def __init__(self, ollama_base_url: str = "http://localhost:11434", model_name: str = "qwen2.5:14b",
                 translation_model: str = "llama3.2:latest",
                 make_model_mapping_file: str = None):
        """
        Initialize the unified processor
        
        Args:
            ollama_base_url: Base URL for Ollama API
            model_name: Name of the Ollama model for DECISION making (default: qwen2.5:14b)
            translation_model: Name of the Ollama model for TRANSLATION (default: llama3.2:latest - fastest)
            make_model_mapping_file: Path to Excel file with Make/Model to License type mapping
        """
        # Get base directory - auto-detects Windows dev or Linux production
        def _get_base_dir():
            """Get base directory - auto-detects Windows dev or Linux production"""
            # 1. Check environment variable first
            env_dir = os.getenv("MOTORCLAIM_BASE_DIR")
            if env_dir and os.path.exists(env_dir):
                return env_dir
            
            # 2. Use script directory (works in both environments)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            
            # 3. Check if we're in Windows dev environment
            if os.name == 'nt':  # Windows
                # Check common Windows dev paths
                windows_paths = [
                    r"D:\Motorclaimdecisionlinux",
                    r"D:\Motorclaimdecision",
                    script_dir
                ]
                for path in windows_paths:
                    if os.path.exists(path) and os.path.isdir(path):
                        return path
            
            # 4. Check if we're in Linux production
            linux_paths = [
                "/opt/motorclaimdecision",
                script_dir
            ]
            for path in linux_paths:
                if os.path.exists(path) and os.path.isdir(path):
                    return path
            
            # 5. Fallback to script directory
            return script_dir
        
        base_dir = _get_base_dir()
        
        # Set default make_model_mapping_file if not provided
        if make_model_mapping_file is None:
            # Try multiple possible locations (Windows and Linux)
            possible_paths = [
                # Relative to base_dir
                os.path.join(base_dir, "makemodelmapped", "makemodelmapped.xlsx"),
                os.path.join(base_dir, "makemodelmapped.xlsx"),
                # Windows dev paths
                r"D:\Motorclaimdecision\makemodelmapped\makemodelmapped.xlsx",
                r"D:\Motorclaimdecisionlinux\makemodelmapped\makemodelmapped.xlsx",
                # Linux production paths
                "/opt/motorclaimdecision/makemodelmapped/makemodelmapped.xlsx",
                "/opt/motorclaimdecision/makemodelmapped.xlsx"
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    make_model_mapping_file = path
                    break
            else:
                # Use first path as default (will show warning if not found)
                make_model_mapping_file = possible_paths[0]
        
        self.processor = ClaimProcessor(ollama_base_url=ollama_base_url, model_name=model_name, 
                                       translation_model=translation_model)
        self.ollama_base_url = ollama_base_url
        self.model_name = model_name  # For decision making
        self.translation_model = translation_model  # For translation (faster model)
        self.make_model_mapping_file = make_model_mapping_file
        self.base_dir = base_dir
        self._mapping_df = None
        # Load make/model mapping for License_Type_From_Make_Model extraction
        self._load_make_model_mapping()
    
    def _load_make_model_mapping(self):
        """Load the Make/Model to License type mapping from Excel file"""
        try:
            if os.path.exists(self.make_model_mapping_file):
                self._mapping_df = pd.read_excel(self.make_model_mapping_file)
                # Clean column names
                self._mapping_df.columns = self._mapping_df.columns.str.strip()
                print(f"✓ Loaded Make/Model mapping: {len(self._mapping_df)} rows")

                # PERFORMANCE OPTIMIZATION: Create lookup dictionary for fast access
                # This avoids iterrows() on every lookup
                self._mapping_cache = {}
                self._mapping_partial = {}
                self._mapping_cols = {}

                # Find column names once
                for col in self._mapping_df.columns:
                    col_clean = str(col).strip()
                    if 'najm' in col_clean.lower() and 'make' in col_clean.lower():
                        self._mapping_cols['make'] = col
                    elif 'najm' in col_clean.lower() and 'model' in col_clean.lower():
                        self._mapping_cols['model'] = col
                    elif 'match' in col_clean.lower() and 'license' in col_clean.lower() and 'type' in col_clean.lower():
                        self._mapping_cols['license'] = col

                # Fallback if exact names not found
                if 'make' not in self._mapping_cols:
                    self._mapping_cols['make'] = 'Najm Make' if 'Najm Make' in self._mapping_df.columns else None
                if 'model' not in self._mapping_cols:
                    self._mapping_cols['model'] = ' Najm  Model' if ' Najm  Model' in self._mapping_df.columns else None
                if 'license' not in self._mapping_cols:
                    self._mapping_cols['license'] = 'Match License type' if 'Match License type' in self._mapping_df.columns else None

                # Build lookup caches (key: (make_upper, model_upper) -> license_type)
                if all(self._mapping_cols.values()):
                    make_col = self._mapping_cols['make']
                    model_col = self._mapping_cols['model']
                    license_col = self._mapping_cols['license']

                    make_vals = (
                        self._mapping_df[make_col]
                        .fillna("")
                        .astype(str)
                        .str.strip()
                        .str.upper()
                    )
                    model_vals = (
                        self._mapping_df[model_col]
                        .fillna("")
                        .astype(str)
                        .str.strip()
                        .str.upper()
                    )
                    license_vals = self._mapping_df[license_col]

                    for make_val, model_val, license_val in zip(make_vals, model_vals, license_vals):
                        if not make_val or not model_val:
                            continue

                        license_str = str(license_val).strip() if pd.notna(license_val) else ""

                        key = (make_val, model_val)
                        if key not in self._mapping_cache:
                            self._mapping_cache[key] = license_str

                        if make_val not in self._mapping_partial:
                            self._mapping_partial[make_val] = []
                        self._mapping_partial[make_val].append((model_val, license_str))

                    print(f"✓ Built fast lookup cache: {len(self._mapping_cache)} exact matches")
                else:
                    self._mapping_cache = {}
                    self._mapping_partial = {}
                    self._mapping_cols = {}
            else:
                print(f"⚠ Warning: Make/Model mapping file not found: {self.make_model_mapping_file}")
                self._mapping_df = None
                self._mapping_cache = {}
                self._mapping_partial = {}
                self._mapping_cols = {}
        except Exception as e:
            print(f"⚠ Warning: Could not load Make/Model mapping: {str(e)[:100]}")
            self._mapping_df = None
            self._mapping_cache = {}
            self._mapping_partial = {}
            self._mapping_cols = {}
    
    def lookup_license_type_from_make_model(self, car_make: str, car_model: str) -> str:
        """
        Lookup License type from Make/Model mapping sheet
        PERFORMANCE OPTIMIZED: Uses cached dictionary lookup instead of iterrows()
        
        Args:
            car_make: Car make value from request (to match with "Najm Make")
            car_model: Car model value from request (to match with " Najm  Model")
        
        Returns:
            License type string from "Match License type" column, or empty string if not found
        """
        # Use cached lookup if available (much faster)
        if hasattr(self, '_mapping_cache') and self._mapping_cache:
            if not car_make or not car_model:
                return ""

            car_make_clean = str(car_make).strip().upper()
            car_model_clean = str(car_model).strip().upper()

            if not car_make_clean or not car_model_clean:
                return ""

            # Try exact match from cache (O(1) lookup)
            key = (car_make_clean, car_model_clean)
            if key in self._mapping_cache:
                return self._mapping_cache[key]

            # Try partial/fuzzy matching from cache
            if hasattr(self, '_mapping_partial') and self._mapping_partial:
                if car_make_clean in self._mapping_partial:
                    for model_val, license_val in self._mapping_partial[car_make_clean]:
                        if (
                            model_val == car_model_clean
                            or car_model_clean in model_val
                            or model_val in car_model_clean
                        ):
                            return license_val

        # Fallback to original method if cache not available
        if self._mapping_df is None or self._mapping_df.empty:
            return ""

        if not car_make or not car_model:
            return ""

        car_make_clean = str(car_make).strip().upper()
        car_model_clean = str(car_model).strip().upper()

        if not car_make_clean or not car_model_clean:
            return ""

        try:
            # Use cached column names if available
            if hasattr(self, '_mapping_cols') and self._mapping_cols:
                najm_make_col = self._mapping_cols.get('make')
                najm_model_col = self._mapping_cols.get('model')
                license_type_col = self._mapping_cols.get('license')
            else:
                # Find columns (fallback)
                najm_make_col = None
                najm_model_col = None
                license_type_col = None

                for col in self._mapping_df.columns:
                    col_clean = str(col).strip()
                    if 'najm' in col_clean.lower() and 'make' in col_clean.lower():
                        najm_make_col = col
                    elif 'najm' in col_clean.lower() and 'model' in col_clean.lower():
                        najm_model_col = col
                    elif 'match' in col_clean.lower() and 'license' in col_clean.lower() and 'type' in col_clean.lower():
                        license_type_col = col

                if not najm_make_col:
                    najm_make_col = 'Najm Make' if 'Najm Make' in self._mapping_df.columns else None
                if not najm_model_col:
                    najm_model_col = ' Najm  Model' if ' Najm  Model' in self._mapping_df.columns else None
                if not license_type_col:
                    license_type_col = 'Match License type' if 'Match License type' in self._mapping_df.columns else None

            if not najm_make_col or not najm_model_col or not license_type_col:
                return ""

            mask = (
                (self._mapping_df[najm_make_col].astype(str).str.strip().str.upper() == car_make_clean) &
                (self._mapping_df[najm_model_col].astype(str).str.strip().str.upper() == car_model_clean)
            )
            matches = self._mapping_df[mask]

            if len(matches) > 0:
                license_type = matches.iloc[0][license_type_col]
                return str(license_type).strip() if pd.notna(license_type) else ""

            # Try partial/fuzzy matching if exact match fails
            mask_make = self._mapping_df[najm_make_col].astype(str).str.strip().str.upper() == car_make_clean
            if mask_make.any():
                make_matches = self._mapping_df[mask_make]
                for _, row in make_matches.iterrows():
                    model_val = str(row[najm_model_col]).strip().upper()
                    if (
                        model_val == car_model_clean
                        or car_model_clean in model_val
                        or model_val in car_model_clean
                    ):
                        license_type = row[license_type_col]
                        if pd.notna(license_type):
                            return str(license_type).strip()

        except Exception as e:
            print(f"  Warning: Error in license type lookup: {str(e)[:100]}")

        return ""
    
    def _translate_arabic_to_english(self, text: str) -> str:
        """
        Translate Arabic text to English using Ollama.
        If translation fails, returns original text with Arabic parts marked.
        
        Args:
            text: Text containing Arabic and/or English content
            
        Returns:
            Translated text with Arabic parts converted to English
        """
        if not text or not text.strip():
            return text
        
        # Check if text contains Arabic characters
        has_arabic = bool(re.search(r'[\u0600-\u06FF]', text))
        if not has_arabic:
            # No Arabic text, return as is
            return text
        
        try:
            # Use Ollama to translate Arabic to English with LD report terminology
            # Improved prompt for accurate motor accident report translation
            translation_prompt = f"""You are a professional translator specializing in motor vehicle accident reports and insurance claims (LD reports).
Translate the following text from Arabic to English using accurate insurance and motor accident terminology.

CRITICAL INSTRUCTIONS FOR LD REPORT TRANSLATION:
1. Translate ONLY Arabic text to English using standard motor accident report terminology
2. Keep ALL English text EXACTLY as is (do not modify English words, numbers, dates, IDs, or formatting)
3. Use standard LD report terminology:
   - "حادث مروري" → "Motor Vehicle Accident" or "Traffic Accident"
   - "مسؤولية" → "Liability"
   - "متضرر" → "Victim" or "Injured Party"
   - "متسبب" → "At-Fault Party" or "Responsible Party"
   - "رخصة قيادة" → "Driving License" or "Driver's License"
   - "مركبة" → "Vehicle"
   - "تأمين" → "Insurance"
   - "بوليصة" → "Policy"
   - "مطالبة" → "Claim"
   - "أضرار" → "Damages"
   - "انتهاك" → "Violation" or "Traffic Violation"
   - "عكس السير" → "Wrong-Way Driving" or "Reversing Direction"
   - "تجاوز الإشارة الحمراء" → "Running Red Light" or "Red Light Violation"
   - "التعاونية للتأمين" → "Tawuniya Cooperative Insurance Company"
   - "قاعدة" → "Rule"
   - "قرار" → "Decision"
   - "مرفوض" → "REJECTED"
   - "مقبول" → "ACCEPTED"
   - "مقبول مع حق الرجوع" → "ACCEPTED_WITH_RECOVERY"
4. Preserve ALL structure, formatting, line breaks, and spacing
5. Do NOT add any explanations, notes, or comments
6. Return ONLY the translated text

Text to translate:
{text}

Translation (Arabic parts only, keep English unchanged, use LD report terminology):"""
            
            # Use faster translation_model for translation (accident descriptions, reasoning, etc.)
            translation_model_to_use = getattr(self, 'translation_model', 'llama3.2:latest')
            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={
                    "model": translation_model_to_use,  # Use faster model for translation (not decision model)
                    "prompt": translation_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "top_p": 0.9,
                        "num_predict": 2000  # OPTIMIZATION: Limit response length for faster translation
                    }
                },
                timeout=20  # OPTIMIZATION: Increased timeout to prevent 503 errors (was 15, increased to 20)
            )
            
            if response.status_code == 200:
                result = response.json()
                translated_text = result.get("response", "").strip()
                # Clean up the response (remove any extra text or explanations)
                if translated_text:
                    # Remove common prefixes/suffixes that models might add
                    lines = translated_text.split('\n')
                    # Remove lines that are clearly instructions or explanations (not part of translation)
                    cleaned_lines = []
                    skip_patterns = [
                        r'^Translation\s*:?',
                        r'^Translated text\s*:?',
                        r'^Here is the translation\s*:?',
                        r'^The translation is\s*:?',
                        r'^Arabic parts translated\s*:?',
                        r'^Translation \(Arabic parts only\)\s*:?'
                    ]
                    for line in lines:
                        should_skip = False
                        for pattern in skip_patterns:
                            if re.match(pattern, line.strip(), re.IGNORECASE):
                                should_skip = True
                                break
                        if not should_skip:
                            cleaned_lines.append(line)
                    
                    translated_text = '\n'.join(cleaned_lines).strip()
                    # Remove leading/trailing quotes if present
                    translated_text = re.sub(r'^["\']+|["\']+$', '', translated_text)
                    return translated_text if translated_text else text
            else:
                print(f"  ⚠️ Translation API error: {response.status_code}")
                if response.status_code == 503:
                    print(f"  ⚠️ Translation service unavailable (503) - using original text")
                return text
                
        except requests.exceptions.Timeout:
            print(f"  ⚠️ Translation timeout - using original text")
            return text
        except requests.exceptions.ConnectionError:
            print(f"  ⚠️ Translation connection error - using original text")
            return text
        except Exception as e:
            error_msg = str(e)[:200] if e else "Unknown error"
            print(f"  ⚠️ Translation error: {error_msg}")
            # Return original text if translation fails - ensure we always have text to save
            return text
    
    def _is_tawuniya_insurance(self, insurance_name: str, ic_english_name: str = None) -> bool:
        """
        Check if insurance company is specifically Tawuniya (التعاونية للتأمين).
        This is more specific than checking for "cooperative" - only matches Tawuniya specifically.
        Uses ICEnglishName for accurate identification since many companies may have "التعاونيه" in their name.
        
        Args:
            insurance_name: Insurance company name (Arabic or English)
            ic_english_name: ICEnglishName from party info (e.g., "Tawuniya Cooperative Insurance Company")
            
        Returns:
            True if it's Tawuniya, False otherwise
        """
        # First check ICEnglishName if provided - most accurate identifier
        if ic_english_name:
            ic_english_clean = str(ic_english_name).strip().lower()
            # Check for Tawuniya - can be full name or abbreviated (e.g., "Tawuniya C")
            # Method 1: If it starts with "tawuniya" (case-insensitive) - most reliable for any format
            if ic_english_clean.startswith("tawuniya"):
                return True
            
            # Method 2: Full name match (contains all three keywords)
            if "tawuniya" in ic_english_clean and "cooperative" in ic_english_clean and "insurance" in ic_english_clean:
                return True
            
            # Method 3: Abbreviated name (e.g., "Tawuniya C", "Tawuniya Co")
            # Match "tawuniya" followed by optional whitespace and abbreviation
            import re
            if re.search(r'tawuniya\s*(?:c\b|co\b|coop|cooperative|insurance)', ic_english_clean):
                return True
        
        # Fallback to checking insurance_name
        if not insurance_name:
            return False
        
        insurance_clean = str(insurance_name).strip().lower()
        
        # Specific Tawuniya identifiers (more precise)
        tawuniya_identifiers = [
            "التعاونية للتأمين",  # Tawuniya Arabic full name
            "التعاونيه للتأمين",  # Alternative spelling
            "tawuniya",  # English name (case-insensitive) - but only if combined with insurance context
        ]
        
        # Check for exact Tawuniya matches in insurance_name
        # Only match "tawuniya" if it appears with "insurance" or "cooperative insurance" context
        # to avoid false positives
        if "tawuniya" in insurance_clean:
            if "insurance" in insurance_clean or "cooperative" in insurance_clean:
                return True
        
        # Check for Arabic full name
        for identifier in ["التعاونية للتأمين", "التعاونيه للتأمين"]:
            if identifier in insurance_clean:
                return True
        
        return False
    
    def _validate_recovery_decision(self, current_party_idx: int, current_party_info: Dict[str, Any], 
                                     all_parties: List[Dict], parties_data: Dict[int, Dict], 
                                     accident_date: str = "") -> Dict[str, Any]:
        """
        Validate ACCEPTED_WITH_RECOVERY decision before acceptance.
        
        Rules for ACCEPTED_WITH_RECOVERY:
        1. Must apply to the victim party (Liability = 0%)
        2. There must be at least one other party with Liability > 0% (the one causing the accident)
        3. Recovery violations can be found in:
           - Current party's own recovery conditions (Recovery field, Act_Violation, License_Expiry_Date, etc.)
           - Other at-fault parties' recovery conditions
           - Recovery = TRUE, OR
           - One of the specific violations (wrong way, red light, etc.)
        
        Args:
            current_party_idx: Index of the current party being validated
            current_party_info: Information dictionary for the current party
            all_parties: List of all party decisions
            parties_data: Dictionary mapping party index to raw party data
            accident_date: Accident date for license expiry validation
        
        Returns:
            Dictionary with:
            - is_valid: bool - Whether the decision is valid
            - reason: str - Explanation of validation result
            - corrected_decision: str - Corrected decision if invalid (ACCEPTED or REJECTED)
            - recovery_reasons: List[str] - Detailed list of recovery violations found
            - current_party_recovery_analysis: Dict - Analysis of current party's recovery conditions
        """
        current_liability = current_party_info.get("Liability", 0)
        current_recovery = str(current_party_info.get("Recovery", "")).strip().upper()
        
        # Initialize recovery analysis for current party
        current_party_recovery_analysis = {
            "recovery_field": current_recovery,
            "has_recovery_field": current_recovery in ["TRUE", "1", "YES", "Y"],
            "act_violation": str(current_party_info.get("Act_Violation", "")).strip(),
            "license_expiry_date": str(current_party_info.get("License_Expiry_Date", "")).strip(),
            "license_type_from_make_model": str(current_party_info.get("License_Type_From_Make_Model", "")).strip(),
            "license_type_from_request": str(current_party_info.get("License_Type_From_Request", "")).strip(),
            "violations_found": []
        }
        
        # Rule 1: ACCEPTED_WITH_RECOVERY should only apply to victims (Liability = 0%)
        if current_liability != 0:
            return {
                "is_valid": False,
                "reason": f"ACCEPTED_WITH_RECOVERY can only apply to victims (Liability=0%), but this party has Liability={current_liability}%",
                "corrected_decision": "ACCEPTED" if current_liability < 100 else "REJECTED",
                "recovery_reasons": [],
                "current_party_recovery_analysis": current_party_recovery_analysis
            }
        
        # Rule 2: Check CURRENT PARTY's own recovery conditions first
        recovery_violations_found = False
        recovery_reasons = []
        
        # Check current party's Recovery field
        if current_recovery in ["TRUE", "1", "YES", "Y"]:
            recovery_violations_found = True
            recovery_reasons.append(f"Current Party {current_party_idx + 1} has Recovery=TRUE")
            current_party_recovery_analysis["violations_found"].append("Recovery field is TRUE")
        
        # Check current party's Act/Violation for specific recovery violations
        current_act_violation = current_party_recovery_analysis["act_violation"].upper()
        if current_act_violation:
            recovery_keywords = [
                ("WRONG WAY", "عكس السير", "WRONG DIRECTION"),
                ("RED LIGHT", "إشارة حمراء", "TRAFFIC LIGHT"),
                ("OVERLOAD", "زيادة الركاب", "EXCEED CAPACITY"),
                ("NO LICENSE", "بدون رخصة", "LICENSE EXPIRED", "رخصة منتهية"),
                ("STOLEN", "مسروقة", "THEFT")
            ]
            for keyword_group in recovery_keywords:
                for keyword in keyword_group:
                    if keyword in current_act_violation:
                        recovery_violations_found = True
                        violation_desc = f"Current Party {current_party_idx + 1} has violation: {current_act_violation[:50]}"
                        recovery_reasons.append(violation_desc)
                        current_party_recovery_analysis["violations_found"].append(f"Act_Violation contains: {keyword}")
                        break
                if any(kw in current_act_violation for kw in keyword_group):
                    break
        
        # Check current party's license expiry date if available
        current_license_expiry = current_party_recovery_analysis["license_expiry_date"]
        if current_license_expiry and current_license_expiry.lower() not in ["not identify", "not identified", ""]:
            try:
                if accident_date and accident_date.lower() not in ["not identify", "not identified", ""]:
                    # Basic date comparison - if license expiry is before accident date, it's a violation
                    if "/" in current_license_expiry or "-" in current_license_expiry:
                        # Enhanced date parsing could be added here
                        # For now, if the field exists and is not "not identify", consider it for recovery
                        # The AI model should have already considered this in its decision
                        pass
            except:
                pass
        
        # Check current party's license type mismatch
        license_type_make_model = current_party_recovery_analysis["license_type_from_make_model"]
        license_type_request = current_party_recovery_analysis["license_type_from_request"]
        if (license_type_make_model and 
            license_type_make_model.lower() not in ["not identify", "not identified", ""] and
            license_type_request and 
            license_type_request.lower() not in ["not identify", "not identified", ""] and
            license_type_make_model.upper() != "ANY LICENSE"):
            # Check if they match or resemble
            if license_type_make_model.upper() != license_type_request.upper():
                # Check for similarity (basic check)
                if license_type_make_model.upper() not in license_type_request.upper() and \
                   license_type_request.upper() not in license_type_make_model.upper():
                    recovery_violations_found = True
                    recovery_reasons.append(f"Current Party {current_party_idx + 1} has license type mismatch: {license_type_make_model} vs {license_type_request}")
                    current_party_recovery_analysis["violations_found"].append("License type mismatch")
        
        # Rule 3: Check if there are other parties with Liability > 0% (the ones causing the accident)
        at_fault_parties = []
        for idx, party_decision in enumerate(all_parties):
            if idx == current_party_idx:
                continue
            
            # Get party info for this other party
            party_raw_data = parties_data.get(idx, {})
            if not party_raw_data and "party_info" in party_decision:
                party_raw_data = party_decision.get("party_info", {})
            
            other_party_info = self.extract_party_info(party_raw_data)
            
            # Fallback from decision
            if not other_party_info.get("Party_ID") and "party_id" in party_decision:
                other_party_info["Party_ID"] = str(party_decision.get("party_id", ""))
            if other_party_info.get("Liability") == 0 and "liability" in party_decision:
                other_party_info["Liability"] = int(party_decision.get("liability", 0))
            
            other_liability = other_party_info.get("Liability", 0)
            
            if other_liability > 0:
                at_fault_parties.append({
                    "idx": idx,
                    "info": other_party_info,
                    "raw_data": party_raw_data
                })
        
        # Rule 4: Check other at-fault parties for recovery conditions (if current party doesn't have recovery)
        # This is for cases where the victim party's recovery depends on at-fault party violations
        if not recovery_violations_found and at_fault_parties:
            for at_fault_party in at_fault_parties:
                party_info = at_fault_party["info"]
                party_raw_data = at_fault_party["raw_data"]
                
                # Check Recovery field
                recovery_field = str(party_info.get("Recovery", "")).strip().upper()
                if recovery_field in ["TRUE", "1", "YES", "Y"]:
                    recovery_violations_found = True
                    recovery_reasons.append(f"At-fault Party {at_fault_party['idx'] + 1} has Recovery=TRUE")
                    continue
                
                # Check Act/Violation for specific recovery violations
                act_violation = str(party_info.get("Act_Violation", "")).strip().upper()
                if act_violation:
                    # Check for specific violations that trigger recovery
                    recovery_keywords = [
                        "WRONG WAY", "عكس السير", "WRONG DIRECTION",
                        "RED LIGHT", "إشارة حمراء", "TRAFFIC LIGHT",
                        "OVERLOAD", "زيادة الركاب", "EXCEED CAPACITY",
                        "NO LICENSE", "بدون رخصة", "LICENSE EXPIRED", "رخصة منتهية",
                        "STOLEN", "مسروقة", "THEFT"
                    ]
                    for keyword in recovery_keywords:
                        if keyword in act_violation:
                            recovery_violations_found = True
                            recovery_reasons.append(f"At-fault Party {at_fault_party['idx'] + 1} has violation: {act_violation[:50]}")
                            break
                
                # Check license expiry date if available
                license_expiry = str(party_info.get("License_Expiry_Date", "")).strip()
                if license_expiry and license_expiry.lower() not in ["not identify", "not identified", ""]:
                    # Try to parse and compare with accident date
                    try:
                        # Normalize dates for comparison
                        if accident_date and accident_date.lower() not in ["not identify", "not identified", ""]:
                            # Simple date comparison (you may need more robust date parsing)
                            # This is a basic check - you might want to enhance date parsing
                            if "/" in license_expiry or "-" in license_expiry:
                                # If we can determine license is expired, it's a recovery condition
                                # For now, we'll rely on the AI model's decision if it included this
                                pass
                    except:
                        pass
        
        # If no recovery violations found in current party or at-fault parties, decision is invalid
        if not recovery_violations_found:
            reason_msg = "ACCEPTED_WITH_RECOVERY requires recovery violations, but none found."
            if at_fault_parties:
                reason_msg += f" Checked current party and at-fault parties: {[p['idx']+1 for p in at_fault_parties]}"
            else:
                reason_msg += " No at-fault parties found."
            
            return {
                "is_valid": False,
                "reason": reason_msg,
                "corrected_decision": "ACCEPTED",
                "recovery_reasons": [],
                "current_party_recovery_analysis": current_party_recovery_analysis
            }
        
        # Validation passed
        return {
            "is_valid": True,
            "reason": f"Valid recovery decision. Found recovery violations: {', '.join(recovery_reasons)}",
            "corrected_decision": "ACCEPTED_WITH_RECOVERY",
            "recovery_reasons": recovery_reasons,  # Add recovery reasons list for detailed identification
            "current_party_recovery_analysis": current_party_recovery_analysis  # Include current party analysis
        }
    
    def _validate_cooperative_insurance_decision(self, current_party_idx: int, current_party_info: Dict[str, Any],
                                                  all_parties: List[Dict], parties_data: Dict[int, Dict]) -> Dict[str, Any]:
        """
        Validate decision based on التعاونيه للتامين (Cooperative Insurance) multi-party rules.
        
        Rules for التعاونيه للتامين:
        1. If current party is insured with Cooperative AND liability < 100%
        2. Then REJECT if ANY party with liability > 0% is NOT insured with Cooperative
        
        Exception:
        - If ALL parties with liability > 0% are insured with Cooperative
          AND their percentages are 25%, 50%, or 75%
        - Then ACCEPT all parties (unless another rejection condition applies)
        
        Args:
            current_party_idx: Index of the current party being validated
            current_party_info: Information dictionary for the current party
            all_parties: List of all party decisions
            parties_data: Dictionary mapping party index to raw party data
        
        Returns:
            Dictionary with:
            - is_valid: bool - Whether the decision should be corrected
            - reason: str - Explanation of validation result
            - corrected_decision: str - Corrected decision if invalid
        """
        current_liability = current_party_info.get("Liability", 0)
        current_insurance = str(current_party_info.get("Insurance_Name", "")).strip()
        
        # Check if current party is insured with التعاونيه للتامين (Tawuniya)
        # Use precise Tawuniya detection with ICEnglishName for accuracy
        current_ic_english = str(current_party_info.get("ICEnglishName", "")).strip()
        is_cooperative_party = self._is_tawuniya_insurance(current_insurance, current_ic_english)
        
        # Only validate if this is a cooperative party
        # Note: Cooperative rules apply even to 0% liability parties if there's a 100% liability party from another company
        if not is_cooperative_party:
            return {
                "is_valid": True,  # No correction needed
                "reason": "Not a cooperative party",
                "corrected_decision": None
            }
        
        # SPECIAL RULE: If there's ANY party with 100% liability from a non-cooperative company,
        # ALL cooperative parties (including 0% liability) should be REJECTED
        has_100_percent_non_cooperative = False
        for idx, party_decision in enumerate(all_parties):
            if idx == current_party_idx:
                continue
            
            # Get party info for this other party
            party_raw_data = parties_data.get(idx, {})
            if not party_raw_data and "party_info" in party_decision:
                party_raw_data = party_decision.get("party_info", {})
            
            other_party_info = self.extract_party_info(party_raw_data)
            
            # Fallback from decision
            if other_party_info.get("Liability") == 0 and "liability" in party_decision:
                other_party_info["Liability"] = int(party_decision.get("liability", 0))
            
            other_liability = other_party_info.get("Liability", 0)
            other_insurance = str(other_party_info.get("Insurance_Name", "")).strip()
            
            # Check if this party has 100% liability and is NOT Tawuniya
            if other_liability == 100:
                other_ic_english = str(other_party_info.get("ICEnglishName", "")).strip()
                is_other_cooperative = self._is_tawuniya_insurance(other_insurance, other_ic_english)
                
                if not is_other_cooperative:
                    has_100_percent_non_cooperative = True
                    break
        
        # If there's a 100% liability party from a non-cooperative company, REJECT all cooperative parties
        if has_100_percent_non_cooperative:
            current_decision = None
            for idx, party_decision in enumerate(all_parties):
                if idx == current_party_idx:
                    current_decision = party_decision.get("decision", "")
                    break
            
            if current_decision != "REJECTED":
                return {
                    "is_valid": False,
                    "reason": f"Cooperative rule: There is a party with 100% liability from a non-cooperative company. All Tawuniya parties must be REJECTED, but got {current_decision}",
                    "corrected_decision": "REJECTED"
                }
            else:
                return {
                    "is_valid": True,
                    "reason": "Correctly rejected: 100% liability party from non-cooperative company exists",
                    "corrected_decision": None
                }
        
        # If current party has 100% liability, cooperative rules don't apply (100% rule applies instead)
        if current_liability >= 100:
            return {
                "is_valid": True,
                "reason": "Liability is 100% - 100% liability rule applies (not cooperative rules)",
                "corrected_decision": None
            }
        
        # Get all other parties with liability > 0%
        at_fault_parties = []
        for idx, party_decision in enumerate(all_parties):
            if idx == current_party_idx:
                continue
            
            # Get party info for this other party
            party_raw_data = parties_data.get(idx, {})
            if not party_raw_data and "party_info" in party_decision:
                party_raw_data = party_decision.get("party_info", {})
            
            other_party_info = self.extract_party_info(party_raw_data)
            
            # Fallback from decision
            if not other_party_info.get("Party_ID") and "party_id" in party_decision:
                other_party_info["Party_ID"] = str(party_decision.get("party_id", ""))
            if other_party_info.get("Liability") == 0 and "liability" in party_decision:
                other_party_info["Liability"] = int(party_decision.get("liability", 0))
            
            other_liability = other_party_info.get("Liability", 0)
            other_insurance = str(other_party_info.get("Insurance_Name", "")).strip()
            
            if other_liability > 0:
                # Check if this party is insured with Tawuniya
                other_ic_english = str(other_party_info.get("ICEnglishName", "")).strip()
                is_other_cooperative = self._is_tawuniya_insurance(other_insurance, other_ic_english)
                
                at_fault_parties.append({
                    "idx": idx,
                    "liability": other_liability,
                    "insurance": other_insurance,
                    "is_cooperative": is_other_cooperative
                })
        
        # If no at-fault parties, no validation needed
        if not at_fault_parties:
            return {
                "is_valid": True,
                "reason": "No other parties with liability > 0%",
                "corrected_decision": None
            }
        
        # Check if all at-fault parties are insured with cooperative
        all_at_fault_are_cooperative = all(p["is_cooperative"] for p in at_fault_parties)
        
        # Check if all at-fault parties have 25%, 50%, or 75% liability (exception case)
        all_at_fault_have_valid_percentages = all(
            p["liability"] in [25, 50, 75] for p in at_fault_parties
        )
        
        # Apply cooperative insurance rule
        current_decision = None
        for idx, party_decision in enumerate(all_parties):
            if idx == current_party_idx:
                current_decision = party_decision.get("decision", "")
                break
        
        # Rule: If any at-fault party is NOT insured with cooperative, current party should be REJECTED
        if not all_at_fault_are_cooperative:
            # Exception: If all at-fault parties are cooperative AND have 25%/50%/75%, then ACCEPT
            if all_at_fault_are_cooperative and all_at_fault_have_valid_percentages:
                if current_decision == "REJECTED":
                    return {
                        "is_valid": False,
                        "reason": f"Cooperative exception: All at-fault parties are cooperative with 25%/50%/75% liability. Should be ACCEPTED, but got REJECTED",
                        "corrected_decision": "ACCEPTED"
                    }
                else:
                    return {
                        "is_valid": True,
                        "reason": "Cooperative exception applies: All at-fault parties are cooperative with valid percentages",
                        "corrected_decision": None
                    }
            else:
                # Normal rule: At least one at-fault party is NOT cooperative → REJECT current party
                non_cooperative_parties = [p for p in at_fault_parties if not p["is_cooperative"]]
                if current_decision != "REJECTED":
                    return {
                        "is_valid": False,
                        "reason": f"Cooperative rule: Party(ies) {[p['idx']+1 for p in non_cooperative_parties]} with liability > 0% are NOT insured with Cooperative. Current party should be REJECTED, but got {current_decision}",
                        "corrected_decision": "REJECTED"
                    }
                else:
                    return {
                        "is_valid": True,
                        "reason": f"Correctly rejected: Non-cooperative at-fault parties found",
                        "corrected_decision": None
                    }
        
        # All at-fault parties are cooperative
        # Check exception: If all have 25%/50%/75%, should be ACCEPTED
        if all_at_fault_have_valid_percentages:
            if current_decision == "REJECTED":
                return {
                    "is_valid": False,
                    "reason": f"Cooperative exception: All at-fault parties are cooperative with 25%/50%/75% liability. Should be ACCEPTED, but got REJECTED",
                    "corrected_decision": "ACCEPTED"
                }
            else:
                return {
                    "is_valid": True,
                    "reason": "Cooperative exception applies: All at-fault parties are cooperative with valid percentages",
                    "corrected_decision": None
                }
        
        # All at-fault parties are cooperative but don't all have 25%/50%/75%
        # This case is ambiguous - let the AI decision stand, but log it
        return {
            "is_valid": True,
            "reason": "All at-fault parties are cooperative, but exception conditions not fully met",
            "corrected_decision": None
        }
    
    def translate_ocr_to_english(self, ocr_text: str) -> str:
        """
        Translate ONLY Arabic parts of OCR text to English, preserving English and structure.
        This helps with date extraction when text is mixed Arabic/English without damaging the report.
        
        Strategy: Extract Arabic phrases, translate them, then replace in original text.
        
        Args:
            ocr_text: OCR text (may contain Arabic and English)
            
        Returns:
            Text with Arabic parts translated to English, or original text if translation fails
        """
        if not ocr_text or len(ocr_text.strip()) < 10:
            return ocr_text
        
        try:
            # Check if text contains Arabic characters
            has_arabic = bool(re.search(r'[\u0600-\u06FF]', ocr_text))
            if not has_arabic:
                # No Arabic text, return as is
                return ocr_text
            
            # Extract Arabic phrases (words/phrases containing Arabic characters)
            # Keep English, numbers, dates, and structure intact
            arabic_pattern = r'[\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+)*'
            arabic_matches = list(re.finditer(arabic_pattern, ocr_text))
            
            if not arabic_matches:
                return ocr_text
            
            # Extract unique Arabic phrases to translate
            arabic_phrases = {}
            for match in arabic_matches:
                phrase = match.group(0).strip()
                if len(phrase) > 2:  # Only translate meaningful phrases
                    arabic_phrases[phrase] = phrase  # Store original for replacement
            
            if not arabic_phrases:
                return ocr_text
            
            # Translate Arabic phrases only (not the whole text)
            # This preserves the report structure
            phrases_to_translate = list(arabic_phrases.keys())[:20]  # Limit to 20 phrases to avoid timeout
            translate_prompt = f"""Translate ONLY the Arabic phrases below to English. 
Keep the translation concise and preserve the meaning.
Do NOT translate numbers, dates, or English text.

Arabic phrases to translate:
{chr(10).join(f"- {phrase}" for phrase in phrases_to_translate)}

Translation (one line per phrase, format: original|translation):"""

            import requests
            # Use faster translation_model for OCR translation
            translation_model_to_use = getattr(self, 'translation_model', 'llama3.2:latest')
            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={
                    "model": translation_model_to_use,  # Use faster model for OCR translation
                    "prompt": translate_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 500  # Shorter for phrase translation
                    }
                },
                timeout=10  # OPTIMIZATION: Shorter timeout for faster translation model (was 20)
            )
            
            if response.status_code == 200:
                result = response.json()
                translated_text = result.get("response", "").strip()
                
                # Parse translations (format: original|translation)
                translation_map = {}
                for line in translated_text.split('\n'):
                    if '|' in line:
                        parts = line.split('|', 1)
                        if len(parts) == 2:
                            original = parts[0].strip().lstrip('-').strip()
                            translation = parts[1].strip()
                            if original in arabic_phrases:
                                translation_map[original] = translation
                
                # Replace Arabic phrases with translations in original text
                if translation_map:
                    result_text = ocr_text
                    for arabic_phrase, english_translation in translation_map.items():
                        # Replace Arabic phrase with English translation
                        result_text = result_text.replace(arabic_phrase, english_translation)
                    print(f"  ✅ Translated {len(translation_map)} Arabic phrase(s), preserved report structure")
                    return result_text
                else:
                    print(f"  ⚠️ Could not parse translations, using original text")
                    return ocr_text
            else:
                print(f"  ⚠️ Translation failed (status {response.status_code}), using original text")
                return ocr_text
        except Exception as e:
            print(f"  ⚠️ Translation error: {str(e)[:100]}, using original text")
            return ocr_text
    
    def extract_party_id_from_ocr(self, ocr_text: str) -> List[str]:
        """
        Extract Party ID(s) from OCR text
        Returns list of found Party IDs
        """
        party_ids = []
        
        # Patterns to find Party ID / رقم الهوية / ID Number
        id_patterns = [
            r'رقم\s*الهوية[:\s]*(\d{8,10})',  # 8-10 digits (handle truncation)
            r'ID\s*Number[:\s]*(\d{8,10})',
            r'Party\s*ID[:\s]*(\d{8,10})',
            r'Party\s*\((\d+)\)',  # Party (1), Party (2)
            r'الطرف\s*\((\d+)\)',  # الطرف (1), الطرف (2)
        ]
        
        # Also look for long numbers (8-10 digits) that might be ID numbers
        # But only if they appear near ID-related keywords
        id_keywords = ['رقم الهوية', 'ID Number', 'Party ID', 'الهوية', 'Identity', 'رقم الهوية الوطنية']
        
        for pattern in id_patterns:
            matches = re.findall(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            party_ids.extend(matches)
        
        # Look for numbers near ID keywords
        for keyword in id_keywords:
            keyword_pos = ocr_text.find(keyword)
            if keyword_pos != -1:
                # Extract context around keyword
                start = max(0, keyword_pos - 20)
                end = min(len(ocr_text), keyword_pos + len(keyword) + 50)
                context = ocr_text[start:end]
                
                # Look for 8-10 digit numbers in context
                id_matches = re.findall(r'\b(\d{8,10})\b', context)
                party_ids.extend(id_matches)
        
        # Also try to find any 8-10 digit numbers that might be Party IDs
        # (as a fallback, but be careful not to match dates)
        all_long_numbers = re.findall(r'\b(\d{8,10})\b', ocr_text)
        for num in all_long_numbers:
            # Exclude if it looks like a date (contains / or -)
            if '/' not in num and '-' not in num:
                party_ids.append(num)
        
        # Remove duplicates and return
        return list(set(party_ids))
    
    def extract_party_ids_with_positions(self, ocr_text: str) -> List[tuple]:
        """
        Extract all Party IDs with their positions in OCR text
        Returns list of tuples: (party_id, start_position, end_position)
        
        Strategy:
        1. First, extract IDs near known keywords (most reliable)
        2. Then, extract all 8-10 digit numbers that appear in table-like contexts
        3. Filter out dates, case numbers, and other non-ID numbers
        """
        party_ids_with_pos = []
        
        # Step 1: Patterns to find Party ID with position tracking (near keywords)
        id_patterns = [
            (r'رقم\s*الهوية[:\s]*(\d{8,10})', 'رقم الهوية'),
            (r'ID\s*Number[:\s]*(\d{8,10})', 'ID Number'),
            (r'Party\s*ID[:\s]*(\d{8,10})', 'Party ID'),
            (r'رقم\s*الهويه[:\s]*(\d{8,10})', 'رقم الهويه'),  # Alternative spelling
            (r'الهوية[:\s]*(\d{8,10})', 'الهوية'),
            (r'الهويه[:\s]*(\d{8,10})', 'الهويه'),
        ]
        
        for pattern, keyword in id_patterns:
            for match in re.finditer(pattern, ocr_text, re.IGNORECASE | re.UNICODE):
                party_id = match.group(1)
                start_pos = match.start(1)  # Position of the ID number
                end_pos = match.end(1)
                # Clean party ID (remove any non-digit characters)
                party_id_clean = re.sub(r'[^\d]', '', party_id)
                if len(party_id_clean) >= 8:  # Ensure it's at least 8 digits
                    party_ids_with_pos.append((party_id_clean, start_pos, end_pos))
        
        # Step 2: Also search for Party IDs near ID keywords (expanded context)
        id_keywords = ['رقم الهوية', 'رقم الهويه', 'ID Number', 'Party ID', 'الهوية', 'الهويه']
        for keyword in id_keywords:
            for match in re.finditer(re.escape(keyword), ocr_text, re.IGNORECASE | re.UNICODE):
                keyword_end = match.end()
                # Look for 8-10 digit number within 100 characters after keyword (expanded)
                context_start = keyword_end
                context_end = min(len(ocr_text), keyword_end + 100)
                context = ocr_text[context_start:context_end]
                
                id_match = re.search(r'\b(\d{8,10})\b', context)
                if id_match:
                    party_id = id_match.group(1)
                    party_id_clean = re.sub(r'[^\d]', '', party_id)
                    if len(party_id_clean) >= 8:
                        start_pos = context_start + id_match.start(1)
                        end_pos = context_start + id_match.end(1)
                        # Avoid duplicates
                        if not any(pid == party_id_clean and abs(pos - start_pos) < 10 for pid, pos, _ in party_ids_with_pos):
                            party_ids_with_pos.append((party_id_clean, start_pos, end_pos))
        
        # Step 3: Extract all 8-10 digit numbers that might be Party IDs (in table contexts)
        # Look for numbers that appear in lines containing party-related keywords
        party_related_keywords = ['طرف', 'Party', 'مسؤولية', 'Liability', 'رخصة', 'License', 'تأمين', 'Insurance']
        
        # Find all 8-10 digit numbers
        all_number_matches = list(re.finditer(r'\b(\d{8,10})\b', ocr_text))
        
        for num_match in all_number_matches:
            num_value = num_match.group(1)
            num_start = num_match.start(1)
            num_end = num_match.end(1)
            
            # Skip if already found near keywords
            if any(pid == num_value and abs(pos - num_start) < 10 for pid, pos, _ in party_ids_with_pos):
                continue
            
            # Get context around this number (200 chars before and after)
            context_start = max(0, num_start - 200)
            context_end = min(len(ocr_text), num_end + 200)
            context = ocr_text[context_start:context_end]
            
            # Check if this number is in a line with party-related keywords
            # Get the line containing this number
            line_start = ocr_text.rfind('\n', 0, num_start)
            if line_start == -1:
                line_start = 0
            else:
                line_start += 1
            line_end = ocr_text.find('\n', num_end)
            if line_end == -1:
                line_end = len(ocr_text)
            line_text = ocr_text[line_start:line_end]
            
            # Check if line contains party-related keywords
            has_party_keyword = any(kw in line_text for kw in party_related_keywords)
            
            # Also check if it's NOT a date (dates usually have / or - separators nearby)
            # Check 20 chars before and after for date separators
            nearby_text = ocr_text[max(0, num_start - 20):min(len(ocr_text), num_end + 20)]
            is_likely_date = bool(re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', nearby_text))
            
            # Check if it's NOT a case number (case numbers usually have letters like DM, AK)
            is_likely_case_number = bool(re.search(r'[A-Z]{2}\d+', nearby_text))
            
            # Check if it's NOT a phone number (phone numbers often have + or spaces)
            is_likely_phone = bool(re.search(r'[+\s]\d{8,10}', nearby_text))
            
            # If it's in a party-related context and not a date/case/phone, it's likely a Party ID
            if has_party_keyword and not is_likely_date and not is_likely_case_number and not is_likely_phone:
                party_id_clean = re.sub(r'[^\d]', '', num_value)
                if len(party_id_clean) >= 8:
                    # Avoid duplicates
                    if not any(pid == party_id_clean and abs(pos - num_start) < 10 for pid, pos, _ in party_ids_with_pos):
                        party_ids_with_pos.append((party_id_clean, num_start, num_end))
        
        # Sort by position
        party_ids_with_pos.sort(key=lambda x: x[1])
        return party_ids_with_pos
    
    def extract_all_expiry_dates_with_positions(self, ocr_text: str, exclude_keywords: List[str] = None) -> List[tuple]:
        """
        Extract all License Expiry Dates with their positions in OCR text
        CRITICAL: Only extracts dates near "تاريخ إنتهاء الرخصة" (License Expiry Date)
        EXCLUDES dates near "تاريخ إضافة الرخصة" (Upload Date)
        EXCLUDES dates of birth (old dates, typically before 2000)
        
        Returns list of tuples: (date, start_position, end_position)
        
        Args:
            ocr_text: OCR text to search
            exclude_keywords: Keywords that indicate dates to exclude (e.g., Upload Date)
        """
        if exclude_keywords is None:
            exclude_keywords = [
                'إصدار', 'اصدار', 'تاريخ الإصدار', 'Version Date', 'Upload Date', 
                'تاريخ إضافة', 'تاريخ الرفع', 'تاريخ إضافة الرخصة', 'تاريخ إضافةالرخصة',  # No space variant
                'إضافة الرخصة', 'إضافةالرخصة',  # No space variant
                'رفع الرخصة', 'رفعالرخصة',  # No space variant
                'تاريخ اضافة', 'تاريخ اضافة الرخصة', 'تاريخ اضافةالرخصة',  # No space variant
                'اضافة الرخصة', 'اضافةالرخصة',  # No space variant
                # Date of Birth keywords
                'تاريخ الميلاد', 'تاريخ ميلاد', 'Date of Birth', 'Birth Date', 'DOB',
                'ميلاد', 'ولادة', 'تاريخ الولادة'
            ]
        
        dates_with_pos = []
        
        # Priority patterns for expiry dates
        # Arabic patterns - MUST contain "تاريخ إنتهاء الرخصة"
        arabic_patterns = [
            r'تاريخ\s*إنتهاء\s*الرخصة\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*إنتهاء\s*الرخصه\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*إنتهاء\s*الرخصة\s*[:\s|]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*إنتهاء\s*الرخصه\s*[:\s|]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*انتهاء\s*الرخصة\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*انتهاء\s*الرخصه\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*انتهاء\s*الرخصة\s*[:\s|]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*انتهاء\s*الرخصه\s*[:\s|]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        ]
        
        # English patterns - for translated OCR text
        # CRITICAL: These patterns work with English-only text (after translation)
        english_patterns = [
            r'License\s*Expiry\s*Date\s*[:\s|]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'License\s*Expiry\s*Date\s*[:\s|]*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',  # YYYY-MM-DD format
            r'Expiry\s*Date\s*[:\s|]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'Expiry\s*Date\s*[:\s|]*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',  # YYYY-MM-DD format
            # Pattern for table format with pipe separators
            r'Expiry\s*Date\s*\|\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'License\s*Expiry\s*Date\s*\|\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        ]
        
        # Combine all patterns
        priority_patterns = arabic_patterns + english_patterns
        
        # Upload Date patterns - to identify and exclude
        # Note: Handle both with space (تاريخ إضافة الرخصة) and without space (تاريخ إضافةالرخصة)
        upload_patterns = [
            r'تاريخ\s*إضافة\s*الرخصة',
            r'تاريخ\s*إضافةالرخصة',  # No space between إضافة and الرخصة
            r'تاريخ\s*إضافه\s*الرخصة',
            r'تاريخ\s*إضافهالرخصة',  # No space variant
            r'تاريخ\s*اضافة\s*الرخصة',
            r'تاريخ\s*اضافةالرخصة',  # No space variant
            r'تاريخ\s*الرفع',
            r'Upload\s*Date',
            r'إضافة\s*الرخصة',
            r'إضافةالرخصة',  # No space variant
            r'رفع\s*الرخصة',
            r'رفعالرخصة'  # No space variant
        ]
        
        # Clean OCR text
        ocr_text_clean = ocr_text
        invisible_chars = ['\u200E', '\u200F', '\u200B', '\u200C', '\u200D', '\uFEFF', '\u2060']
        for char in invisible_chars:
            ocr_text_clean = ocr_text_clean.replace(char, '')
        
        # Find all Upload Date positions first (to exclude dates near them)
        upload_date_positions = []
        for pattern in upload_patterns:
            for match in re.finditer(pattern, ocr_text_clean, re.IGNORECASE | re.UNICODE):
                upload_date_positions.append((match.start(), match.end()))
        
        # Find all Date of Birth positions (to exclude dates near them)
        birth_date_patterns = [
            r'تاريخ\s*الميلاد',
            r'تاريخ\s*ميلاد',
            r'تاريخ\s*الولادة',
            r'Date\s*of\s*Birth',
            r'Birth\s*Date',
            r'DOB',
            r'ميلاد',
            r'ولادة'
        ]
        birth_date_positions = []
        for pattern in birth_date_patterns:
            for match in re.finditer(pattern, ocr_text_clean, re.IGNORECASE | re.UNICODE):
                birth_date_positions.append((match.start(), match.end()))
        
        # Find all matches for expiry date patterns
        # CRITICAL: Extract ALL dates from each matched line, not just the first one
        matched_patterns = []
        for pattern_idx, pattern in enumerate(priority_patterns):
            matches_found = list(re.finditer(pattern, ocr_text_clean, re.IGNORECASE | re.UNICODE))
            if matches_found:
                matched_patterns.append((pattern_idx, pattern, len(matches_found)))
                print(f"    🔍 Pattern {pattern_idx + 1} ({'Arabic' if pattern_idx < len(arabic_patterns) else 'English'}): matched {len(matches_found)} time(s)")
            for match in matches_found:
                date_found = match.group(1).strip()
                if date_found:
                    date_start = match.start(1)
                    date_end = match.end(1)
                    match_start = match.start(0)  # Start of full match including "تاريخ إنتهاء الرخصة"
                    match_end = match.end(0)
                    
                    # CRITICAL: Extract ALL dates from the line containing this match
                    # For table layouts: "تاريخ إنتهاء الرخصة / Expiry Date 21/06/1451 06/02/1451"
                    # We need to extract BOTH dates, not just the first one
                    line_start = ocr_text_clean.rfind('\n', 0, match_start)
                    if line_start == -1:
                        line_start = 0
                    else:
                        line_start += 1
                    
                    line_end = ocr_text_clean.find('\n', match_end)
                    if line_end == -1:
                        line_end = len(ocr_text_clean)
                    
                    full_line = ocr_text_clean[line_start:line_end]
                    
                    # CRITICAL: If this line contains expiry keywords (Arabic or English), ALL dates in this line are expiry dates
                    # (except Upload Dates like 19/11/2025 which are in a different column)
                    line_contains_expiry_keyword = (
                        'تاريخ إنتهاء' in full_line or 
                        'تاريخ انتهاء' in full_line or 
                        'Expiry Date' in full_line or
                        'License Expiry' in full_line or
                        'License Expiry Date' in full_line
                    )
                    
                    print(f"    🔍 Line contains expiry keyword: {line_contains_expiry_keyword}")
                    print(f"    🔍 Full line: '{full_line[:200]}'")
                    
                    # Extract ALL dates from this line
                    date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
                    all_dates_in_line = re.findall(date_pattern, full_line)
                    
                    # Filter out Upload Dates (19/11/2025 is common) and Dates of Birth
                    valid_dates_in_line = []
                    for date_str in all_dates_in_line:
                        # Skip common Upload Date format
                        if date_str in ['19/11/2025', '19-11-2025', '2025-11-19']:
                            print(f"    ⚠️ Skipping Upload Date: {date_str}")
                            continue
                        
                        # CRITICAL: Check if date is likely a Date of Birth (old dates before 2000)
                        # This must be done BEFORE checking expiry keyword, as birth dates should NEVER be used
                        date_parts = date_str.replace('/', '-').split('-')
                        is_birth_date = False
                        year = None
                        if len(date_parts) == 3:
                            try:
                                # Determine year position
                                if len(date_parts[0]) == 4:  # YYYY-MM-DD
                                    year = int(date_parts[0])
                                elif len(date_parts[2]) == 4:  # DD-MM-YYYY
                                    year = int(date_parts[2])
                                elif len(date_parts[1]) == 4:  # DD-YYYY-MM (uncommon)
                                    year = int(date_parts[1])
                                else:
                                    # Try 2-digit year
                                    if len(date_parts[2]) == 2:  # DD-MM-YY
                                        yy = int(date_parts[2])
                                        year = 1900 + yy if yy > 50 else 2000 + yy
                                    elif len(date_parts[0]) == 2:  # YY-MM-DD
                                        yy = int(date_parts[0])
                                        year = 1900 + yy if yy > 50 else 2000 + yy
                                
                                # License expiry dates are typically 2000-2030+ (or Hijri 1400+)
                                # Dates before 2000 (Gregorian) are likely birth dates
                                # EXCEPTION: Hijri dates can be 1400-1600 range, which is valid
                                if year:
                                    if year < 2000 and year >= 1400:
                                        # Could be Hijri date - check if it's in valid Hijri range
                                        # Hijri years 1400-1600 are valid for license expiry
                                        # But years < 1400 are likely errors or birth dates
                                        if year < 1400:
                                            is_birth_date = True
                                            print(f"    🚫 Skipping Date of Birth: {date_str} (year {year} < 1400 - invalid)")
                                    elif year < 2000:
                                        # Gregorian date before 2000 - definitely birth date
                                        is_birth_date = True
                                        print(f"    🚫 Skipping Date of Birth: {date_str} (year {year} < 2000)")
                            except (ValueError, IndexError):
                                pass
                        
                        if is_birth_date:
                            print(f"    🚫 Date {date_str} EXCLUDED - identified as Date of Birth (year: {year})")
                            continue
                        
                        # If line contains expiry keyword, include dates (but we've already filtered birth dates)
                        if line_contains_expiry_keyword:
                            valid_dates_in_line.append(date_str)
                            print(f"    ✅ Including date {date_str} (line contains expiry keyword)")
                        else:
                            # Line doesn't contain expiry keyword - check if it's a recent Gregorian date that might be Upload Date
                            date_parts = date_str.replace('/', '-').split('-')
                            if len(date_parts) == 3:
                                try:
                                    if len(date_parts[0]) == 4:
                                        year = int(date_parts[0])
                                    elif len(date_parts[2]) == 4:
                                        year = int(date_parts[2])
                                    else:
                                        year = None
                                    
                                    # Skip if it's 2024-2026 (likely Upload Date, not Expiry)
                                    if year and 2024 <= year <= 2026:
                                        # Check if it's in the Upload Date column area
                                        date_pos_in_line = full_line.find(date_str)
                                        # Check for both with space and without space variants
                                        # Check for Upload Date keywords (Arabic or English)
                                        upload_date_pos_in_line = full_line.find('تاريخ إضافة')
                                        if upload_date_pos_in_line == -1:
                                            upload_date_pos_in_line = full_line.find('تاريخ إضافةالرخصة')  # No space variant
                                        if upload_date_pos_in_line == -1:
                                            upload_date_pos_in_line = full_line.find('Upload Date')  # English
                                        if upload_date_pos_in_line == -1:
                                            upload_date_pos_in_line = full_line.find('License Upload Date')  # English
                                        if upload_date_pos_in_line != -1 and abs(date_pos_in_line - upload_date_pos_in_line) < 50:
                                            print(f"    ⚠️ Skipping Upload Date in line: {date_str}")
                                            continue
                                except (ValueError, IndexError):
                                    pass
                            
                            valid_dates_in_line.append(date_str)
                    
                    print(f"    🔍 Line contains {len(all_dates_in_line)} date(s), {len(valid_dates_in_line)} valid: {valid_dates_in_line}")
                    
                    # Process each valid date from the line
                    # CRITICAL: Track which dates we've already processed to avoid duplicates
                    processed_date_positions = []
                    for date_to_process in valid_dates_in_line:
                        # Find position of this date in the LINE (not full text)
                        # This ensures we get the correct position even if date appears multiple times
                        date_pos_in_line = full_line.find(date_to_process)
                        if date_pos_in_line == -1:
                            # Try to find it in full text as fallback
                            date_pos_in_full = ocr_text_clean.find(date_to_process, line_start)
                            if date_pos_in_full == -1:
                                date_pos_in_full = match_start  # Last resort fallback
                            date_start = date_pos_in_full
                        else:
                            # Convert line position to full text position
                            date_start = line_start + date_pos_in_line
                        
                        date_end = date_start + len(date_to_process)
                        
                        # Skip if we've already processed a date at this exact position (duplicate)
                        if (date_start, date_end) in processed_date_positions:
                            print(f"    ⚠️ Skipping duplicate date {date_to_process} at position {date_start}-{date_end}")
                            continue
                        processed_date_positions.append((date_start, date_end))
                        
                        # Get context around the match
                        context_start = max(0, match_start - 200)  # Larger context to check for Upload Date
                        context_end = min(len(ocr_text_clean), match_end + 200)
                        context = ocr_text_clean[context_start:context_end]
                        
                        # CRITICAL: Check if this date is a Date of Birth (old dates, typically before 2000)
                        # License expiry dates are usually in the future or recent past (2000-2030+)
                        # Dates of birth are typically old (before 2000, like 1980s, 1990s)
                        # THIS CHECK MUST HAPPEN FIRST - even if in expiry line, birth dates should be excluded
                        is_likely_birth_date = False
                        date_parts = date_to_process.replace('/', '-').split('-')
                        year = None
                        if len(date_parts) == 3:
                            try:
                                # Determine year position
                                if len(date_parts[0]) == 4:  # YYYY-MM-DD
                                    year = int(date_parts[0])
                                elif len(date_parts[2]) == 4:  # DD-MM-YYYY
                                    year = int(date_parts[2])
                                elif len(date_parts[1]) == 4:  # DD-YYYY-MM (uncommon but possible)
                                    year = int(date_parts[1])
                                else:
                                    # Try to parse 2-digit year (assume 19xx or 20xx)
                                    if len(date_parts[2]) == 2:  # DD-MM-YY
                                        yy = int(date_parts[2])
                                        year = 1900 + yy if yy > 50 else 2000 + yy
                                    elif len(date_parts[0]) == 2:  # YY-MM-DD
                                        yy = int(date_parts[0])
                                        year = 1900 + yy if yy > 50 else 2000 + yy
                                
                                # CRITICAL: Exclude dates before 2010 (more strict)
                                # License expiry dates are typically 2010-2030+ (or Hijri 1400+)
                                # Dates before 2010 are likely birth dates or very old licenses
                                if year:
                                    # For Gregorian dates: exclude if < 2010
                                    if 1900 <= year < 2010:
                                        is_likely_birth_date = True
                                        print(f"    🚫 Date {date_to_process} has year {year} (before 2010) - EXCLUDING as Date of Birth/Old License")
                                        print(f"    🚫 This date will NOT be used as License Expiry Date")
                                    # For Hijri dates: valid range is 1400-1600
                                    elif 1400 <= year <= 1600:
                                        # Valid Hijri date - keep it
                                        pass
                                    elif year < 1400:
                                        # Invalid Hijri date - exclude
                                        is_likely_birth_date = True
                                        print(f"    🚫 Date {date_to_process} has year {year} (invalid Hijri < 1400) - EXCLUDING")
                                    elif year > 2100:
                                        # Invalid future date - likely OCR error
                                        is_likely_birth_date = True
                                        print(f"    🚫 Date {date_to_process} has year {year} (invalid > 2100) - EXCLUDING as OCR error")
                            except (ValueError, IndexError):
                                pass
                        
                        # Check if date is near "Date of Birth" keywords
                        is_near_birth_keyword = False
                        for birth_start, birth_end in birth_date_positions:
                            date_center = (date_start + date_end) // 2
                            birth_center = (birth_start + birth_end) // 2
                            distance = abs(date_center - birth_center)
                            if distance < 300:  # Within 300 characters
                                is_near_birth_keyword = True
                                print(f"    🚫 Date {date_to_process} is near Date of Birth keyword (distance: {distance}) - EXCLUDING")
                                break
                        
                        # CRITICAL: If this is a birth date, EXCLUDE IT IMMEDIATELY - don't process further
                        if is_likely_birth_date or is_near_birth_keyword:
                            print(f"    🚫 SKIPPING Date {date_to_process} - identified as Date of Birth")
                            continue  # Skip this date entirely
                        
                        # CRITICAL: Check if this date is near an Upload Date
                        # BUT: If the line contains "تاريخ إنتهاء الرخصة", ALL dates in that line are expiry dates
                        # (they're in the same table row, just different columns)
                        is_near_upload_date = False
                        
                        # CRITICAL: If line contains expiry keyword, dates are likely expiry dates
                        # BUT: We've already filtered out birth dates above
                        if line_contains_expiry_keyword:
                            # Date is in line with expiry keyword and is NOT a birth date - likely expiry date
                            print(f"    ✅ Date {date_to_process} is in line containing 'تاريخ إنتهاء الرخصة' - KEEPING (table row, year: {year})")
                            is_near_upload_date = False  # Explicitly set to False - skip upload date exclusion checks
                        else:
                            # Line doesn't contain expiry keyword - check if it's near Upload Date
                            print(f"    ⚠️ Date {date_to_process} is NOT in line with expiry keyword - checking Upload Date proximity...")
                            for upload_start, upload_end in upload_date_positions:
                                # Calculate distance between expiry date and upload date
                                date_center = (date_start + date_end) // 2
                                upload_center = (upload_start + upload_end) // 2
                                distance = abs(date_center - upload_center)
                                
                                # If Upload Date is within 300 characters and closer than Expiry keyword, exclude
                                # BUT: If both are in the same line (table row), they're both valid - don't exclude
                                upload_in_same_line = (line_start <= upload_start <= line_end)
                                if distance < 300 and not upload_in_same_line:
                                    # Check which keyword is closer to the date
                                    expiry_keyword_pos = match_start  # Position of "تاريخ إنتهاء الرخصة"
                                    if abs(upload_center - date_center) < abs(expiry_keyword_pos - date_center):
                                        is_near_upload_date = True
                                        print(f"    ⚠️ Date {date_to_process} is closer to Upload Date (distance: {distance}) than Expiry keyword - EXCLUDING")
                                        break
                            
                            # ADDITIONAL SAFEGUARD: Only check if line does NOT contain expiry keyword
                            # (If line contains expiry keyword, we already decided to keep it)
                            # Upload Dates are typically recent Gregorian dates (e.g., "19/11/2025")
                            # Expiry Dates are typically Hijri dates (e.g., "21/04/1451")
                            # If date is in Gregorian format (year 2020-2030) and appears in context with Upload keyword, exclude
                            date_parts = date_to_process.replace('/', '-').split('-')
                            if len(date_parts) == 3:
                                try:
                                    # Try to parse as DD-MM-YYYY or YYYY-MM-DD
                                    if len(date_parts[0]) == 4:  # YYYY-MM-DD
                                        year = int(date_parts[0])
                                    elif len(date_parts[2]) == 4:  # DD-MM-YYYY
                                        year = int(date_parts[2])
                                    else:
                                        year = None
                                    
                                    # If year is 2020-2030 (recent Gregorian), check context more carefully
                                    if year and 2020 <= year <= 2030:
                                        # Check if Upload Date keyword is in nearby context
                                        nearby_context = ocr_text_clean[max(0, date_start - 100):min(len(ocr_text_clean), date_end + 100)]
                                        has_upload_keyword_nearby = any(
                                            kw in nearby_context for kw in ['إضافة', 'اضافة', 'رفع', 'Upload Date', 'تاريخ إضافة', 'تاريخ إضافةالرخصة', 'تاريخ الرفع', 'إضافةالرخصة', 'رفعالرخصة']
                                        )
                                        has_expiry_keyword_nearby = any(
                                            kw in nearby_context for kw in ['إنتهاء', 'انتهاء', 'Expiry', 'Expires']
                                        )
                                        
                                        # If Upload keyword is present and Expiry keyword is NOT, exclude
                                        if has_upload_keyword_nearby and not has_expiry_keyword_nearby:
                                            is_near_upload_date = True
                                            print(f"    ⚠️ Date {date_to_process} appears to be Upload Date (recent Gregorian year {year} with Upload keyword) - EXCLUDING")
                                except (ValueError, IndexError):
                                    pass  # Ignore parsing errors
                        
                        # Check for exclude keywords in context
                        has_exclude_keyword = any(kw in context for kw in exclude_keywords)
                        has_expiry_keyword = any(kw in context for kw in ['إنتهاء', 'انتهاء', 'Expiry', 'Expires'])
                        
                        # CRITICAL FIX: Even if line contains expiry keyword, exclude dates that are specifically 
                        # near "Version Date", "Accident Time", "Case Number", "Final Report" etc. (report header dates)
                        # These should NEVER be treated as license expiry dates, regardless of expiry keyword presence
                        report_header_keywords = [
                            'Version Date', 'تاريخ الإصدار', 'تاريخ الاصدار', 'Version',
                            'Accident Time', 'وقت الحادث', 'Accident Date', 'تاريخ الحادث',
                            'Case Number', 'رقم الحالة', 'Case',
                            'Final Report', 'التقرير', 'Report',
                            'Liability Determination Report'
                        ]
                        is_near_report_header = False
                        date_center = (date_start + date_end) // 2
                        
                        for header_kw in report_header_keywords:
                            # Find all occurrences of header keyword in the context
                            header_positions = []
                            search_start = 0
                            while True:
                                pos = full_line.find(header_kw, search_start)
                                if pos == -1:
                                    break
                                header_positions.append(line_start + pos)
                                search_start = pos + len(header_kw)
                            
                            # Check if date is near any header keyword (within 200 chars)
                            for header_pos in header_positions:
                                header_center = header_pos + len(header_kw) // 2
                                distance_to_header = abs(header_center - date_center)
                                if distance_to_header < 200:
                                    # Also check that expiry keyword is NOT closer than header keyword
                                    expiry_keyword_pos = match_start if line_contains_expiry_keyword else -1
                                    if expiry_keyword_pos == -1 or distance_to_header < abs(expiry_keyword_pos - date_center):
                                        is_near_report_header = True
                                        print(f"    🚫 Date {date_to_process} is near report header keyword '{header_kw}' (distance: {distance_to_header}) - EXCLUDING as report/accident date")
                                        break
                            
                            if is_near_report_header:
                                break
                        
                        # CRITICAL FIX: Exclude dates near report headers FIRST (highest priority exclusion)
                        if is_near_report_header:
                            # Date is near report header (Version Date, Accident Time, etc.) - skip it entirely
                            # This prevents matching report dates or accident dates as license expiry dates
                            continue
                        
                        # CRITICAL: If line contains expiry keyword and date passed birth date check, include it
                        # (we've already filtered out birth dates above with the continue statement)
                        if line_contains_expiry_keyword:
                            # Date is in line with expiry keyword and is NOT a birth date and NOT near report header - include it
                            is_duplicate = False
                            for existing_date, existing_pos, _ in dates_with_pos:
                                if existing_date == date_to_process and abs(existing_pos - date_start) < 10:
                                    is_duplicate = True
                                    print(f"    ⚠️ Skipping duplicate date {date_to_process} at position {date_start} (already found at {existing_pos})")
                                    break
                            if not is_duplicate:
                                dates_with_pos.append((date_to_process, date_start, date_end))
                                print(f"    ✅ Extracted expiry date: {date_to_process} (from line containing expiry keyword, year: {year})")
                            continue  # Skip further processing for this date if it was handled above
                        # Only include if:
                        # 1. Has expiry keyword (from the pattern match itself)
                        # 2. NOT near Upload Date
                        # 3. NOT a Date of Birth (old dates or near birth keywords)
                        # 4. NOT near other exclude keywords (unless expiry keyword is also present)
                        elif has_expiry_keyword and not is_near_upload_date and not is_likely_birth_date and not is_near_birth_keyword:
                                # Additional check: if exclude keyword is present, make sure expiry keyword is closer
                                if has_exclude_keyword:
                                    # Find positions of expiry and exclude keywords relative to date
                                    expiry_keyword_pos = match_start
                                    exclude_keyword_pos = -1
                                    for kw in exclude_keywords:
                                        pos = context.find(kw)
                                        if pos != -1:
                                            exclude_keyword_pos = context_start + pos
                                            break
                                    
                                    if exclude_keyword_pos != -1:
                                        date_center = (date_start + date_end) // 2
                                        expiry_dist = abs(expiry_keyword_pos - date_center)
                                        exclude_dist = abs(exclude_keyword_pos - date_center)
                                        
                                        # Only include if expiry keyword is closer than exclude keyword
                                        if expiry_dist < exclude_dist:
                                            # Expiry keyword is closer - include this date
                                            # CRITICAL: Only deduplicate if it's the EXACT same date at similar position
                                            # Allow different dates even if they're close together (different parties might have dates near each other)
                                            is_duplicate = False
                                            for existing_date, existing_pos, _ in dates_with_pos:
                                                # Check if it's the same date value AND very close position (likely duplicate)
                                                if existing_date == date_to_process and abs(existing_pos - date_start) < 10:
                                                    is_duplicate = True
                                                    print(f"    ⚠️ Skipping duplicate date {date_to_process} at position {date_start} (already found at {existing_pos})")
                                                    break
                                            if not is_duplicate:
                                                dates_with_pos.append((date_to_process, date_start, date_end))
                                                print(f"    ✅ Extracted expiry date: {date_to_process} (expiry keyword closer than exclude keyword)")
                                        else:
                                            print(f"    ⚠️ Date {date_to_process} excluded: exclude keyword closer than expiry keyword")
                                    else:
                                        # No exclude keyword found in context - include
                                        # CRITICAL: Only deduplicate if it's the EXACT same date at similar position
                                        is_duplicate = False
                                        for existing_date, existing_pos, _ in dates_with_pos:
                                            if existing_date == date_to_process and abs(existing_pos - date_start) < 10:
                                                is_duplicate = True
                                                print(f"    ⚠️ Skipping duplicate date {date_to_process} at position {date_start} (already found at {existing_pos})")
                                                break
                                        if not is_duplicate:
                                            dates_with_pos.append((date_to_process, date_start, date_end))
                                else:
                                    # No exclude keywords - include
                                    # CRITICAL: Only deduplicate if it's the EXACT same date at similar position
                                    is_duplicate = False
                                    for existing_date, existing_pos, _ in dates_with_pos:
                                        if existing_date == date_to_process and abs(existing_pos - date_start) < 10:
                                            is_duplicate = True
                                            print(f"    ⚠️ Skipping duplicate date {date_to_process} at position {date_start} (already found at {existing_pos})")
                                            break
                                    if not is_duplicate:
                                        dates_with_pos.append((date_to_process, date_start, date_end))
        
        # Sort by position
        dates_with_pos.sort(key=lambda x: x[1])
        print(f"    📅 Total expiry dates extracted: {len(dates_with_pos)}")
        if matched_patterns:
            print(f"    🔍 Matched patterns: {len(matched_patterns)} pattern(s) found matches")
        else:
            print(f"    ⚠️ No patterns matched - checking why...")
            # Check if expiry keywords exist in text
            expiry_keywords_check = [
                'تاريخ إنتهاء', 'تاريخ انتهاء', 'Expiry Date', 'License Expiry', 'License Expiry Date'
            ]
            found_keywords = [kw for kw in expiry_keywords_check if kw in ocr_text_clean]
            if found_keywords:
                print(f"    🔍 DEBUG: Found expiry keywords in text: {found_keywords}")
                # Show sample context around keywords
                for kw in found_keywords[:2]:  # Show first 2
                    idx = ocr_text_clean.find(kw)
                    if idx != -1:
                        context_start = max(0, idx - 100)
                        context_end = min(len(ocr_text_clean), idx + len(kw) + 200)
                        context = ocr_text_clean[context_start:context_end]
                        print(f"    🔍 DEBUG: Context around '{kw}' (pos {idx}): '{context}'")
            else:
                print(f"    ⚠️ DEBUG: NO expiry keywords found in text at all!")
        if dates_with_pos:
            for date, pos, _ in dates_with_pos:
                # Show context around each date for debugging
                context_start = max(0, pos - 150)
                context_end = min(len(ocr_text), pos + len(date) + 150)
                context = ocr_text[context_start:context_end]
                print(f"       - {date} at position {pos}")
                print(f"         Context: '{context}'")
        else:
            print(f"    ⚠️ DEBUG: No dates extracted - checking why...")
            # Check if expiry keywords exist
            expiry_keywords = ['تاريخ إنتهاء', 'تاريخ انتهاء', 'Expiry Date', 'License Expiry']
            found_keywords = [kw for kw in expiry_keywords if kw in ocr_text]
            if found_keywords:
                print(f"    🔍 DEBUG: Found expiry keywords: {found_keywords}")
            else:
                print(f"    🔍 DEBUG: NO expiry keywords found in OCR text")
            # Check for any date patterns
            any_date_pattern = r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
            any_dates = re.findall(any_date_pattern, ocr_text)
            if any_dates:
                print(f"    🔍 DEBUG: Found {len(any_dates)} date-like patterns (may be excluded): {any_dates[:5]}...")
            else:
                print(f"    🔍 DEBUG: NO date patterns found in OCR at all")
        return dates_with_pos
    
    def extract_license_type(self, ocr_text: str, party_id: str = None) -> str:
        """
        Extract License Type from OCR text.
        Looks for patterns like: "License Type", "نوع الرخصة", etc.
        
        Args:
            ocr_text: OCR text to search
            party_id: Optional Party ID to find license type near
            
        Returns:
            License type string, or "not identify" if not found
        """
        license_type_patterns = [
            # English patterns (more flexible)
            r'License\s*Type[:\s|]*([A-Za-z0-9\s\-]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف)',
            r'License\s*Type[:\s|]*([A-Za-z]+)',
            r'Type[:\s|]*([A-Za-z]+)',
            # Arabic patterns (with and without spaces, more flexible)
            r'نوع\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف|رقم|Upload|إضافة)',
            r'نوع\s*الرخصه[:\s|/]*([^\n|/]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف|رقم|Upload|إضافة)',
            r'نوع الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف|رقم|Upload|إضافة)',
            r'نوع الرخصه[:\s|/]*([^\n|/]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف|رقم|Upload|إضافة)',
            r'نوع\s*/\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف|رقم|Upload|إضافة)',
            r'نوع\s*:\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|License|Expiry|تاريخ|Party|طرف|رقم|Upload|إضافة)',
            # Table format patterns (common in OCR tables)
            r'License\s*Type\s*[|:/]\s*([A-Za-z0-9\s\-]+)',
            r'نوع\s*الرخصة\s*[|:/]\s*([^\n|/]+)',
            r'نوع\s*/\s*الرخصة\s*[|:/]\s*([^\n|/]+)',
            # Pattern for table rows: look for license type in same row as party ID
            r'([A-Za-z]+)\s*(?:License|Type|نوع)',
        ]
        
        # Common license types to look for (expanded list)
        # These are the standard license types that should be extracted
        # CRITICAL: Do NOT include "Insurance" - that's not a license type
        common_license_types = [
            'Private', 'Commercial', 'Motorcycle', 'Motor', 'Car', 'Vehicle',
            'خاصة', 'تجارية', 'دراجة', 'خاص', 'تجاري', 'نقل', 'نقل عام', 'نقل خاص',
            # Additional variations
            'Private License', 'Commercial License', 'Motorcycle License',
            'رخصة خاصة', 'رخصة تجارية', 'رخصة دراجة'
        ]
        
        # Map individual Arabic words to full license types
        # This helps when OCR splits "رخصة خاصة" into separate words
        arabic_license_word_map = {
            'خاصة': 'خاصة',  # Private
            'خاص': 'خاصة',   # Private (short form)
            'تجارية': 'تجارية',  # Commercial
            'تجاري': 'تجارية',   # Commercial (short form)
            'نقل': 'نقل',    # Transport/Commercial
            'دراجة': 'دراجة'  # Motorcycle
        }
        
        # Words to exclude (not license types)
        exclude_words = ['Insurance', 'تأمين', 'Insurance Company', 'شركة التأمين', 'Company', 'Insurance Name']
        
        print(f"    🔍 DEBUG License Type: Searching for Party ID: {party_id}")
        print(f"    🔍 DEBUG License Type: Using expanded list: {common_license_types[:5]}... (showing first 5)")
        print(f"    🔍 DEBUG License Type: Excluding words: {exclude_words}")
        
        # If party_id provided, search near it (in same table row)
        if party_id:
            party_id_str = str(party_id).strip()
            party_id_clean = re.sub(r'[^\d]', '', party_id_str)
            # Try both cleaned and original
            party_positions = []
            if party_id_clean:
                pos = ocr_text.find(party_id_clean)
                if pos != -1:
                    party_positions.append(pos)
            pos = ocr_text.find(party_id_str)
            if pos != -1 and pos not in party_positions:
                party_positions.append(pos)
            
            if not party_positions:
                print(f"    ⚠️ DEBUG License Type: Party ID '{party_id}' NOT found in OCR text - will search entire text")
            else:
                print(f"    🔍 DEBUG License Type: Found Party ID '{party_id}' at position(s): {party_positions}")
            
            for party_pos in party_positions:
                # Get the line/row containing the party ID (for table layouts)
                line_start = ocr_text.rfind('\n', 0, party_pos)
                if line_start == -1:
                    line_start = 0
                else:
                    line_start += 1
                line_end = ocr_text.find('\n', party_pos)
                if line_end == -1:
                    line_end = len(ocr_text)
                line_text = ocr_text[line_start:line_end]
                
                # IMPROVED: Also search in a wider context (500 chars before and after) to catch table structures
                # where license type might be in a different column but same row
                wider_context_start = max(0, line_start - 500)
                wider_context_end = min(len(ocr_text), line_end + 500)
                wider_context = ocr_text[wider_context_start:wider_context_end]
                
                # Also search in expanded context around party ID (1000 chars)
                context_start = max(0, party_pos - 1000)
                context_end = min(len(ocr_text), party_pos + len(party_id_str) + 1000)
                context = ocr_text[context_start:context_end]
                
                # Priority 1: Search in the same line/row (most accurate for tables)
                print(f"    🔍 DEBUG License Type: Searching in same line as Party ID")
                print(f"    🔍 DEBUG License Type: Line text (first 200 chars): '{line_text[:200]}'")
                for pattern in license_type_patterns:
                    match = re.search(pattern, line_text, re.IGNORECASE | re.UNICODE)
                    if match:
                        license_type = match.group(1).strip()
                        print(f"    🔍 DEBUG License Type: Pattern matched, extracted: '{license_type}'")
                        # Clean up common OCR errors
                        license_type = re.sub(r'\s+', ' ', license_type)
                        # Remove common separators and special chars
                        license_type = re.sub(r'[|:\-]+', ' ', license_type).strip()
                        # Remove trailing numbers or dates
                        license_type = re.sub(r'\s+\d+[/-]\d+[/-]\d+.*$', '', license_type).strip()
                        print(f"    🔍 DEBUG License Type: After cleaning: '{license_type}'")
                        if len(license_type) > 1 and len(license_type) < 50:  # Reasonable length
                            # CRITICAL: Exclude words that are NOT license types
                            if any(exclude_word.lower() in license_type.lower() for exclude_word in exclude_words):
                                print(f"    🚫 DEBUG License Type: EXCLUDING '{license_type}' - contains excluded word (not a license type)")
                                continue  # Skip this match
                            
                            # Check if it's a valid license type (not just random text)
                            if any(lt.lower() in license_type.lower() or license_type.lower() in lt.lower() 
                                   for lt in common_license_types):
                                print(f"    ✅ DEBUG License Type: Found valid license type: '{license_type}'")
                                return license_type
                            # Also return if it's short and looks like a license type (but not excluded)
                            if len(license_type.split()) <= 3:
                                print(f"    ✅ DEBUG License Type: Returning short license type: '{license_type}'")
                                return license_type
                            else:
                                print(f"    ⚠️ DEBUG License Type: '{license_type}' is too long or not in common list, skipping")
                
                # Priority 2: Check for common license types in the same line
                print(f"    🔍 DEBUG License Type: Checking common license types in line...")
                for license_type in common_license_types:
                    # Use word boundary or exact match to avoid partial matches
                    pattern = r'\b' + re.escape(license_type) + r'\b'
                    if re.search(pattern, line_text, re.IGNORECASE | re.UNICODE):
                        print(f"    ✅ DEBUG License Type: Found '{license_type}' in same line as Party ID (word boundary match)")
                        return license_type
                    # Also try without word boundary for Arabic text
                    if license_type in line_text:
                        print(f"    ✅ DEBUG License Type: Found '{license_type}' in same line as Party ID (exact match)")
                        return license_type
                    else:
                        print(f"    🔍 DEBUG License Type: '{license_type}' NOT found in line")
                
                # Priority 2.1: Check for "رخصة" followed by license type word in the same line
                # This handles cases where OCR splits "رخصة خاصة" into separate words
                if 'رخصة' in line_text:
                    print(f"    🔍 DEBUG License Type: Found 'رخصة' in line, checking for license type word nearby...")
                    for arabic_word, license_type in arabic_license_word_map.items():
                        # Look for the license type word within 100 chars after "رخصة" in the line
                        رخصة_pos_in_line = line_text.find('رخصة')
                        if رخصة_pos_in_line != -1:
                            after_رخصة_in_line = line_text[رخصة_pos_in_line:min(len(line_text), رخصة_pos_in_line + 100)]
                            if arabic_word in after_رخصة_in_line:
                                distance = after_رخصة_in_line.find(arabic_word)
                                if distance < 100:
                                    print(f"    ✅ DEBUG License Type: Found '{arabic_word}' after 'رخصة' in same line (distance: {distance} chars) - returning '{license_type}'")
                                    return license_type
                
                # Priority 2.25: Check in wider context (for table structures where license type is in different column)
                print(f"    🔍 DEBUG License Type: Checking common license types in wider context (table structure)...")
                for license_type in common_license_types:
                    # Search in wider context but prioritize proximity to party ID
                    pattern = r'\b' + re.escape(license_type) + r'\b'
                    matches = list(re.finditer(pattern, wider_context, re.IGNORECASE | re.UNICODE))
                    if matches:
                        # Find the closest match to party ID position (relative to wider_context)
                        party_pos_in_context = party_pos - wider_context_start
                        closest_match = min(matches, key=lambda m: abs(m.start() - party_pos_in_context))
                        distance = abs(closest_match.start() - party_pos_in_context)
                        # Only use if within reasonable distance (1000 chars)
                        if distance < 1000:
                            print(f"    ✅ DEBUG License Type: Found '{license_type}' in wider context near Party ID (distance: {distance} chars)")
                            return license_type
                    # Also try without word boundary for Arabic text
                    if license_type in wider_context:
                        # Find position
                        pos_in_context = wider_context.find(license_type)
                        party_pos_in_context = party_pos - wider_context_start
                        distance = abs(pos_in_context - party_pos_in_context)
                        if distance < 1000:
                            print(f"    ✅ DEBUG License Type: Found '{license_type}' in wider context near Party ID (exact match, distance: {distance} chars)")
                            return license_type
                
                # Priority 2.26: Check for "رخصة" followed by license type word in wider context
                if 'رخصة' in wider_context:
                    print(f"    🔍 DEBUG License Type: Found 'رخصة' in wider context, checking for license type word nearby...")
                    party_pos_in_context = party_pos - wider_context_start
                    رخصة_positions = [m.start() for m in re.finditer(r'رخصة', wider_context)]
                    for رخصة_pos in رخصة_positions:
                        # Check distance from party ID
                        distance_from_party = abs(رخصة_pos - party_pos_in_context)
                        if distance_from_party < 1000:  # Within reasonable distance
                            # Look for license type word within 200 chars after "رخصة"
                            after_رخصة = wider_context[رخصة_pos:min(len(wider_context), رخصة_pos + 200)]
                            for arabic_word, license_type in arabic_license_word_map.items():
                                if arabic_word in after_رخصة:
                                    lt_pos_in_after = after_رخصة.find(arabic_word)
                                    if lt_pos_in_after < 200:
                                        print(f"    ✅ DEBUG License Type: Found '{arabic_word}' after 'رخصة' in wider context (distance from party: {distance_from_party} chars) - returning '{license_type}'")
                                        return license_type
                
                # Priority 2.5: Make sure we don't return "Insurance" or other excluded words
                print(f"    🔍 DEBUG License Type: Checking for excluded words in line...")
                for exclude_word in exclude_words:
                    if exclude_word in line_text:
                        print(f"    🚫 DEBUG License Type: Found excluded word '{exclude_word}' in line - will NOT use as license type")
                
                # Priority 3: Search in expanded context
                print(f"    🔍 DEBUG License Type: Searching in expanded context (1000 chars around Party ID)...")
                print(f"    🔍 DEBUG License Type: Context text (first 300 chars): '{context[:300]}'")
                for pattern in license_type_patterns:
                    match = re.search(pattern, context, re.IGNORECASE | re.UNICODE)
                    if match:
                        license_type = match.group(1).strip()
                        print(f"    🔍 DEBUG License Type: Pattern matched in context, extracted: '{license_type}'")
                        license_type = re.sub(r'\s+', ' ', license_type)
                        license_type = re.sub(r'[|:\-]+', ' ', license_type).strip()
                        license_type = re.sub(r'\s+\d+[/-]\d+[/-]\d+.*$', '', license_type).strip()
                        print(f"    🔍 DEBUG License Type: After cleaning: '{license_type}'")
                        if len(license_type) > 1 and len(license_type) < 50:
                            # CRITICAL: Exclude words that are NOT license types
                            if any(exclude_word.lower() in license_type.lower() for exclude_word in exclude_words):
                                print(f"    ⚠️ Excluding '{license_type}' - contains excluded word (not a license type)")
                                continue  # Skip this match
                            
                            if any(lt.lower() in license_type.lower() or license_type.lower() in lt.lower() 
                                   for lt in common_license_types):
                                print(f"    ✅ DEBUG License Type: Found valid license type in context: '{license_type}'")
                                return license_type
                            if len(license_type.split()) <= 3:
                                print(f"    ✅ DEBUG License Type: Returning short license type from context: '{license_type}'")
                                return license_type
                
                # Priority 4: Check for common license types in context
                print(f"    🔍 DEBUG License Type: Checking common license types in expanded context...")
                for license_type in common_license_types:
                    pattern = r'\b' + re.escape(license_type) + r'\b'
                    if re.search(pattern, context, re.IGNORECASE | re.UNICODE):
                        print(f"    ✅ DEBUG License Type: Found '{license_type}' in context near Party ID (word boundary)")
                        return license_type
                    if license_type in context:
                        print(f"    ✅ DEBUG License Type: Found '{license_type}' in context near Party ID (exact match)")
                        return license_type
                    else:
                        print(f"    🔍 DEBUG License Type: '{license_type}' NOT found in context")
                
                # Priority 4.1: Check for "رخصة" followed by license type word in expanded context
                if 'رخصة' in context:
                    print(f"    🔍 DEBUG License Type: Found 'رخصة' in expanded context, checking for license type word nearby...")
                    party_pos_in_context = party_pos - context_start
                    رخصة_positions = [m.start() for m in re.finditer(r'رخصة', context)]
                    for رخصة_pos in رخصة_positions:
                        # Check distance from party ID
                        distance_from_party = abs(رخصة_pos - party_pos_in_context)
                        if distance_from_party < 1000:  # Within reasonable distance
                            # Look for license type word within 200 chars after "رخصة"
                            after_رخصة = context[رخصة_pos:min(len(context), رخصة_pos + 200)]
                            for arabic_word, license_type in arabic_license_word_map.items():
                                if arabic_word in after_رخصة:
                                    lt_pos_in_after = after_رخصة.find(arabic_word)
                                    if lt_pos_in_after < 200:
                                        print(f"    ✅ DEBUG License Type: Found '{arabic_word}' after 'رخصة' in expanded context (distance from party: {distance_from_party} chars) - returning '{license_type}'")
                                        return license_type
        
        # Search entire text (if party_id not provided or not found near party_id)
        print(f"    🔍 DEBUG License Type: Party ID not found or not in OCR, searching entire text...")
        print(f"    🔍 DEBUG License Type: OCR text length: {len(ocr_text)} characters")
        for pattern in license_type_patterns:
            match = re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            if match:
                license_type = match.group(1).strip()
                print(f"    🔍 DEBUG License Type: Pattern matched in entire text, extracted: '{license_type}'")
                license_type = re.sub(r'\s+', ' ', license_type)
                # Remove common separators
                license_type = re.sub(r'[|:\-]+', ' ', license_type).strip()
                print(f"    🔍 DEBUG License Type: After cleaning: '{license_type}'")
                # CRITICAL: Exclude words that are NOT license types
                if any(exclude_word.lower() in license_type.lower() for exclude_word in exclude_words):
                    print(f"    ⚠️ Excluding '{license_type}' - contains excluded word (not a license type)")
                    continue  # Skip this match
                if len(license_type) > 1:
                    print(f"    ✅ DEBUG License Type: Returning license type from entire text: '{license_type}'")
                    return license_type
        
        # Last resort: check for common license types anywhere in text
        print(f"    🔍 DEBUG License Type: Last resort - checking common license types anywhere in entire text...")
        for license_type in common_license_types:
            pattern = r'\b' + re.escape(license_type) + r'\b'
            if re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE):
                print(f"    ✅ DEBUG License Type: Found '{license_type}' anywhere in OCR text (word boundary)")
                return license_type
            if license_type in ocr_text:
                print(f"    ✅ DEBUG License Type: Found '{license_type}' anywhere in OCR text (exact match)")
                return license_type
            else:
                print(f"    🔍 DEBUG License Type: '{license_type}' NOT found in entire text")
        
        # Last resort 2: Check for "رخصة" followed by license type word anywhere in text
        print(f"    🔍 DEBUG License Type: Last resort 2 - checking for 'رخصة' + license type word pattern in entire text...")
        رخصة_positions_all = [m.start() for m in re.finditer(r'رخصة', ocr_text)]
        if رخصة_positions_all:
            for رخصة_pos in رخصة_positions_all:
                # Look for license type words within 300 chars after "رخصة" (increased from 200)
                after_رخصة = ocr_text[رخصة_pos:min(len(ocr_text), رخصة_pos + 300)]
                print(f"    🔍 DEBUG License Type: Checking text after 'رخصة' at position {رخصة_pos} (first 150 chars): '{after_رخصة[:150]}'")
                for arabic_word, license_type in arabic_license_word_map.items():
                    if arabic_word in after_رخصة:
                        lt_pos_in_after = after_رخصة.find(arabic_word)
                        if lt_pos_in_after < 300:  # Within reasonable distance (increased from 200)
                            print(f"    ✅ DEBUG License Type: Found '{arabic_word}' after 'رخصة' anywhere in OCR (distance: {lt_pos_in_after} chars) - returning '{license_type}'")
                            return license_type
        
        # Last resort 3: If party_id provided, check if any license type word appears in OCR at all
        # (even if not near "رخصة" - sometimes OCR misses the connection)
        if party_id:
            print(f"    🔍 DEBUG License Type: Last resort 3 - checking if any license type word appears anywhere in OCR (fallback)...")
            for arabic_word, license_type in arabic_license_word_map.items():
                if arabic_word in ocr_text:
                    # Found the license type word somewhere in OCR
                    positions = [i for i in range(len(ocr_text)) if ocr_text.startswith(arabic_word, i)]
                    if positions:
                        print(f"    ✅ DEBUG License Type: Found '{arabic_word}' in OCR at position(s) {positions[:3]}... (fallback match) - returning '{license_type}'")
                        return license_type
        
        # CRITICAL: Do NOT return "Insurance" or other excluded words as license type
        for exclude_word in exclude_words:
            if exclude_word in ocr_text:
                print(f"    ⚠️ Found excluded word '{exclude_word}' in OCR - will NOT use as license type")
        
        # DEBUG: Show OCR text snippet to help identify why license type wasn't found
        print(f"    ⚠️ License Type not found - returning 'not identify'")
        print(f"    🔍 DEBUG: OCR text snippet (first 500 chars) for analysis:")
        print(f"    {'='*70}")
        print(f"    {ocr_text[:500]}")
        print(f"    {'='*70}")
        print(f"    🔍 DEBUG: Searching for any Arabic license-related keywords in OCR...")
        # Try to find any license-related keywords that might indicate where license type is
        license_keywords = ['نوع', 'رخصة', 'License', 'Type', 'نقل', 'خاصة', 'تجارية', 'دراجة']
        found_keywords = []
        for keyword in license_keywords:
            if keyword in ocr_text:
                # Find all positions
                positions = [i for i in range(len(ocr_text)) if ocr_text.startswith(keyword, i)]
                found_keywords.append((keyword, len(positions)))
                if positions:
                    # Show context around first occurrence
                    pos = positions[0]
                    context_start = max(0, pos - 50)
                    context_end = min(len(ocr_text), pos + 100)
                    context = ocr_text[context_start:context_end]
                    print(f"    🔍 Found '{keyword}' at position {pos}, context: '{context}'")
        if found_keywords:
            print(f"    📋 Found license-related keywords: {found_keywords}")
        else:
            print(f"    ⚠️ No license-related keywords found in OCR text!")
        
        # SPECIAL: Search specifically for "نوع الرخصة" pattern with more flexible matching
        print(f"    🔍 DEBUG: Specifically searching for 'نوع الرخصة' pattern in OCR...")
        # Try multiple variations of "نوع الرخصة" pattern
        نوع_الرخصة_variations = [
            r'نوع\s*الرخصة',
            r'نوع\s*الرخصه',
            r'نوع الرخصة',
            r'نوع الرخصه',
            r'نوع\s+الرخصة',
            r'نوع\s+الرخصه',
            r'نوع\s*/\s*الرخصة',
            r'نوع\s*:\s*الرخصة',
            r'نوع\s*\|\s*الرخصة',
        ]
        for pattern in نوع_الرخصة_variations:
            matches = list(re.finditer(pattern, ocr_text, re.IGNORECASE | re.UNICODE))
            if matches:
                for match in matches:
                    pos = match.start()
                    context_start = max(0, pos - 30)
                    context_end = min(len(ocr_text), pos + match.end() - match.start() + 100)
                    context = ocr_text[context_start:context_end]
                    print(f"    ✅ Found 'نوع الرخصة' pattern at position {pos}, context: '{context}'")
                    # Try to extract the value after "نوع الرخصة"
                    after_match = ocr_text[match.end():min(len(ocr_text), match.end() + 200)]
                    print(f"    🔍 DEBUG: Text after 'نوع الرخصة' (first 150 chars): '{after_match[:150]}'")
                    # Look for license type words in the text after "نوع الرخصة"
                    for arabic_word, license_type in arabic_license_word_map.items():
                        if arabic_word in after_match:
                            lt_pos = after_match.find(arabic_word)
                            if lt_pos < 200:  # Within reasonable distance
                                print(f"    ✅ Found '{arabic_word}' after 'نوع الرخصة' at distance {lt_pos} - returning '{license_type}'")
                                return license_type
                    # Also check for common license types
                    for lt in common_license_types:
                        if lt in after_match:
                            lt_pos = after_match.find(lt)
                            if lt_pos < 200:
                                if not any(exclude_word.lower() in lt.lower() for exclude_word in exclude_words):
                                    print(f"    ✅ Found '{lt}' after 'نوع الرخصة' at distance {lt_pos} - returning '{lt}'")
                                    return lt
        
        # SPECIAL 2: Look for "نوع" and "رخصة" separately but within reasonable distance (OCR might split them)
        print(f"    🔍 DEBUG: Searching for 'نوع' and 'رخصة' separately (in case OCR split them)...")
        نوع_positions = [m.start() for m in re.finditer(r'نوع', ocr_text)]
        رخصة_positions = [m.start() for m in re.finditer(r'رخصة', ocr_text)]
        if نوع_positions and رخصة_positions:
            for نوع_pos in نوع_positions:
                for رخصة_pos in رخصة_positions:
                    # Check if "رخصة" comes after "نوع" within 50 characters
                    if رخصة_pos > نوع_pos and (رخصة_pos - نوع_pos) < 50:
                        # Found "نوع" followed by "رخصة" - this is likely "نوع الرخصة"
                        print(f"    ✅ Found 'نوع' at {نوع_pos} followed by 'رخصة' at {رخصة_pos} (distance: {رخصة_pos - نوع_pos})")
                        # Extract text after "رخصة"
                        after_رخصة = ocr_text[رخصة_pos + len('رخصة'):min(len(ocr_text), رخصة_pos + len('رخصة') + 200)]
                        print(f"    🔍 DEBUG: Text after 'رخصة' (first 150 chars): '{after_رخصة[:150]}'")
                        # Look for license type words
                        for arabic_word, license_type in arabic_license_word_map.items():
                            if arabic_word in after_رخصة:
                                lt_pos = after_رخصة.find(arabic_word)
                                if lt_pos < 200:
                                    print(f"    ✅ Found '{arabic_word}' after 'نوع الرخصة' at distance {lt_pos} - returning '{license_type}'")
                                    return license_type
                        # Also check for common license types
                        for lt in common_license_types:
                            if lt in after_رخصة:
                                lt_pos = after_رخصة.find(lt)
                                if lt_pos < 200:
                                    if not any(exclude_word.lower() in lt.lower() for exclude_word in exclude_words):
                                        print(f"    ✅ Found '{lt}' after 'نوع الرخصة' at distance {lt_pos} - returning '{lt}'")
                                        return lt
        
        # FINAL ATTEMPT: If party_id was provided but license type not found, try to extract from table structure
        # by looking for license type in the same "section" as the party (e.g., between party ID and expiry date)
        if party_id:
            party_id_str = str(party_id).strip()
            party_id_clean = re.sub(r'[^\d]', '', party_id_str)
            party_positions = []
            if party_id_clean:
                pos = ocr_text.find(party_id_clean)
                if pos != -1:
                    party_positions.append(pos)
            
            for party_pos in party_positions:
                # Look for license type between party ID and expiry date (common table structure)
                # Search in a window: from party ID to 2000 chars after
                search_window = ocr_text[party_pos:min(len(ocr_text), party_pos + 2000)]
                
                print(f"    🔍 DEBUG License Type: Final attempt - searching in window around Party ID (position {party_pos}, window length: {len(search_window)})...")
                print(f"    🔍 DEBUG License Type: Search window (first 500 chars): '{search_window[:500]}'")
                
                # Try to find "نوع الرخصة" or "License Type" header, then extract value after it
                # More flexible patterns to handle OCR variations
                header_patterns = [
                    r'نوع\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'نوع\s*الرخصه[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'نوع\s*/\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'نوع\s*:\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'License\s*Type[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'License\s*/\s*Type[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                ]
                
                for header_pattern in header_patterns:
                    matches = list(re.finditer(header_pattern, search_window, re.IGNORECASE | re.UNICODE))
                    if matches:
                        # Get the value after the header (might be in same line or next)
                        for match in matches:
                            value = match.group(1).strip()
                            value = re.sub(r'[|:\-]+', ' ', value).strip()
                            print(f"    🔍 DEBUG License Type: Found header pattern, extracted value: '{value}'")
                            # Check if it's a valid license type
                            for lt in common_license_types:
                                if lt.lower() in value.lower() or value.lower() in lt.lower():
                                    if not any(exclude_word.lower() in value.lower() for exclude_word in exclude_words):
                                        print(f"    ✅ DEBUG License Type: Found '{value}' after header pattern in table structure")
                                        return lt  # Return the standard license type name
                
                # FINAL FINAL ATTEMPT: Look for license type values directly in the search window
                # (in case the header is missing but the value is there)
                print(f"    🔍 DEBUG License Type: Searching for license type values directly in window around Party ID...")
                for lt in common_license_types:
                    # Search for the license type in the window
                    if lt in search_window:
                        # Find position of license type relative to party ID
                        lt_pos = search_window.find(lt)
                        distance = abs(lt_pos - 0)  # Distance from party ID
                        print(f"    🔍 DEBUG License Type: Found '{lt}' in search window at position {lt_pos} (distance: {distance} chars from Party ID)")
                        # Only use if within reasonable distance (1500 chars)
                        if distance < 1500:
                            if not any(exclude_word.lower() in lt.lower() for exclude_word in exclude_words):
                                print(f"    ✅ DEBUG License Type: Found '{lt}' directly in search window (distance: {distance} chars from Party ID)")
                                return lt
                    else:
                        print(f"    🔍 DEBUG License Type: '{lt}' NOT found in search window")
                
                # SPECIAL CASE: Look for "رخصة" followed by license type word within reasonable distance
                # This handles cases where OCR splits "رخصة خاصة" into separate words
                print(f"    🔍 DEBUG License Type: Checking for 'رخصة' followed by license type word...")
                رخصة_positions = [m.start() for m in re.finditer(r'رخصة', search_window)]
                if رخصة_positions:
                    for رخصة_pos in رخصة_positions:
                        # Look for license type words within 300 chars after "رخصة" (increased from 200)
                        after_رخصة = search_window[رخصة_pos:min(len(search_window), رخصة_pos + 300)]
                        print(f"    🔍 DEBUG License Type: Checking text after 'رخصة' (first 100 chars): '{after_رخصة[:100]}'")
                        for arabic_word, license_type in arabic_license_word_map.items():
                            if arabic_word in after_رخصة:
                                # Found license type word near "رخصة"
                                lt_pos_in_after = after_رخصة.find(arabic_word)
                                distance_from_رخصة = lt_pos_in_after
                                if distance_from_رخصة < 300:  # Within reasonable distance (increased from 200)
                                    print(f"    ✅ DEBUG License Type: Found '{arabic_word}' near 'رخصة' (distance: {distance_from_رخصة} chars) - returning '{license_type}'")
                                    return license_type
                            else:
                                print(f"    🔍 DEBUG License Type: '{arabic_word}' NOT found in text after 'رخصة'")
                
                # SPECIAL CASE 2: Look for "نوع الرخصة" header pattern and extract value after it
                # This is more reliable when the header is present
                print(f"    🔍 DEBUG License Type: Looking for 'نوع الرخصة' header pattern in search window...")
                نوع_الرخصة_patterns = [
                    r'نوع\s*الرخصة[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'نوع\s*الرخصه[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                    r'License\s*Type[:\s|/]*([^\n|/]+?)(?:\n|$|تاريخ|Expiry|Party|طرف|Upload|إضافة)',
                ]
                for pattern in نوع_الرخصة_patterns:
                    matches = list(re.finditer(pattern, search_window, re.IGNORECASE | re.UNICODE))
                    if matches:
                        for match in matches:
                            value = match.group(1).strip()
                            value = re.sub(r'[|:\-/]+', ' ', value).strip()
                            print(f"    🔍 DEBUG License Type: Found 'نوع الرخصة' pattern, extracted value: '{value}'")
                            # Check if extracted value contains a license type word
                            for arabic_word, license_type in arabic_license_word_map.items():
                                if arabic_word in value:
                                    print(f"    ✅ DEBUG License Type: Found '{arabic_word}' in extracted value - returning '{license_type}'")
                                    return license_type
                            # Also check if value itself is a license type
                            for lt in common_license_types:
                                if lt.lower() in value.lower() or value.lower() in lt.lower():
                                    if not any(exclude_word.lower() in value.lower() for exclude_word in exclude_words):
                                        print(f"    ✅ DEBUG License Type: Extracted value '{value}' matches license type '{lt}'")
                                        return lt
        
        # FINAL FINAL FINAL ATTEMPT: Search entire OCR for "رخصة" followed by license type word
        # This is a last resort when party_id-based search fails
        print(f"    🔍 DEBUG License Type: Final attempt - searching entire OCR for 'رخصة' + license type pattern...")
        رخصة_positions_all = [m.start() for m in re.finditer(r'رخصة', ocr_text)]
        if رخصة_positions_all:
            for رخصة_pos in رخصة_positions_all:
                # Look for license type words within 300 chars after "رخصة" (increased from 200)
                after_رخصة = ocr_text[رخصة_pos:min(len(ocr_text), رخصة_pos + 300)]
                for arabic_word, license_type in arabic_license_word_map.items():
                    if arabic_word in after_رخصة:
                        # Found license type word near "رخصة"
                        lt_pos_in_after = after_رخصة.find(arabic_word)
                        distance_from_رخصة = lt_pos_in_after
                        if distance_from_رخصة < 300:  # Within reasonable distance (increased from 200)
                            print(f"    ✅ DEBUG License Type: Found '{arabic_word}' near 'رخصة' in entire OCR (distance: {distance_from_رخصة} chars) - returning '{license_type}'")
                            return license_type
        
        # Last resort 4: If "رخصة" is found but no license type word is found, and we're clearly in a license section
        # (not insurance), default to "خاصة" (Private) as it's the most common license type
        # Check if we're in a license-related section (has "تاريخ إنتهاء الرخصة" or "Expiry Date")
        رخصة_found = 'رخصة' in ocr_text
        license_section_indicators = ['تاريخ إنتهاء الرخصة', 'Expiry Date', 'تاريخ إضافة الرخصة', 'Upload Date']
        has_license_section = any(indicator in ocr_text for indicator in license_section_indicators)
        
        # If we found "رخصة" and we're in a license section, but no license type word was found,
        # default to "خاصة" (Private) - but only if we're NOT in an insurance-only section
        if رخصة_found and has_license_section:
            # Check if there's a "نوع الرخصة" header that we might have missed
            نوع_الرخصة_found = 'نوع الرخصة' in ocr_text or 'License Type' in ocr_text
            # If we have license section indicators but no license type was found, and no "نوع الرخصة" header,
            # it's likely the OCR missed the license type word - default to "خاصة"
            if not نوع_الرخصة_found:
                print(f"    🔍 DEBUG License Type: Last resort 4 - 'رخصة' found in license section but no license type word detected...")
                print(f"    ⚠️ DEBUG License Type: 'رخصة' found in license section (has 'تاريخ إنتهاء الرخصة') but no license type word detected.")
                print(f"    ⚠️ DEBUG License Type: Defaulting to 'خاصة' (Private) as most common license type.")
                return 'خاصة'
        
        return "not identify"
    
    def extract_upload_date(self, ocr_text: str, party_id: str = None) -> str:
        """
        Extract Upload Date from OCR text.
        Looks for "تاريخ إضافة الرخصة" or "Upload Date" patterns.
        
        Args:
            ocr_text: OCR text to search
            party_id: Optional Party ID to find upload date near
            
        Returns:
            Upload date string, or "not identify" if not found
        """
        upload_date_patterns = [
            r'تاريخ\s*إضافة\s*الرخصة[:\s/]*\s*Expiry\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*إضافةالرخصة[:\s/]*\s*Expiry\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'Upload\s*Date[:\s/]*\s*تاريخ\s*إضافة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'Upload\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*إضافة\s*الرخصة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'تاريخ\s*إضافةالرخصة[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        ]
        
        # If party_id provided, search near it
        if party_id:
            party_id_str = str(party_id).strip()
            party_pos = ocr_text.find(party_id_str)
            if party_pos != -1:
                # Search in context around party ID
                context_start = max(0, party_pos - 500)
                context_end = min(len(ocr_text), party_pos + len(party_id_str) + 500)
                context = ocr_text[context_start:context_end]
                
                for pattern in upload_date_patterns:
                    match = re.search(pattern, context, re.IGNORECASE | re.UNICODE)
                    if match:
                        upload_date = match.group(1).strip()
                        # Normalize date format
                        upload_date = self.normalize_date_format(upload_date)
                        return upload_date
        
        # Search entire text
        for pattern in upload_date_patterns:
            match = re.search(pattern, ocr_text, re.IGNORECASE | re.UNICODE)
            if match:
                upload_date = match.group(1).strip()
                upload_date = self.normalize_date_format(upload_date)
                return upload_date
        
        return "not identify"
    
    def match_date_to_party_id(self, target_party_id: str, party_positions: List[tuple], date_positions: List[tuple], used_dates: set = None) -> str:
        """
        Find the License Expiry Date closest to the target Party ID
        Optionally exclude already-used dates to ensure each party gets a unique date
        
        Args:
            target_party_id: The Party ID to find date for
            party_positions: List of (party_id, start_pos, end_pos)
            date_positions: List of (date, start_pos, end_pos)
            used_dates: Set of dates that have already been assigned to other parties
            
        Returns:
            Date string closest to the target Party ID, or "not identify" if not found
        """
        if used_dates is None:
            used_dates = set()
        
        # Find target Party ID position
        target_party_pos = None
        for party_id, start_pos, end_pos in party_positions:
            # Try to match Party ID (exact or partial)
            target_str = str(target_party_id).strip()
            party_str = str(party_id).strip()
            
            # Exact match
            if party_str == target_str:
                target_party_pos = (start_pos + end_pos) // 2  # Use middle position
                break
            # Partial match (last 8-9 digits)
            elif len(target_str) >= 8 and len(party_str) >= 8:
                if target_str[-8:] == party_str[-8:] or target_str[-9:] == party_str[-9:]:
                    target_party_pos = (start_pos + end_pos) // 2
                    break
        
        if target_party_pos is None:
            print(f"    ⚠️ Target Party ID {target_party_id} not found in extracted Party IDs")
            return "not identify"
        
        # Find closest date (prefer unused dates, but allow reuse if necessary)
        min_distance = float('inf')
        closest_date = None
        closest_date_info = None
        
        # First pass: Try to find closest unused date
        for date, date_start, date_end in date_positions:
            if date in used_dates:
                continue  # Skip already used dates
                
            date_pos = (date_start + date_end) // 2
            distance = abs(target_party_pos - date_pos)
            
            if distance < min_distance:
                min_distance = distance
                closest_date = date
                closest_date_info = (date, date_start, date_end, distance)
        
        # If no unused date found, use closest date anyway (better than "not identify")
        if closest_date is None:
            print(f"    ⚠️ All dates already used, using closest date for Party ID {target_party_id}")
            min_distance = float('inf')
            for date, date_start, date_end in date_positions:
                date_pos = (date_start + date_end) // 2
                distance = abs(target_party_pos - date_pos)
                
                if distance < min_distance:
                    min_distance = distance
                    closest_date = date
                    closest_date_info = (date, date_start, date_end, distance)
        
        if closest_date:
            status = "✅ UNIQUE" if closest_date not in used_dates else "⚠️ REUSED"
            print(f"    {status} Matched date {closest_date} to Party ID {target_party_id} (distance: {min_distance} chars)")
            return closest_date
        else:
            print(f"    ⚠️ No date found near Party ID {target_party_id}")
            return "not identify"
    
    def extract_table_rows(self, ocr_text: str) -> List[tuple]:
        """
        Extract table rows from OCR text.
        Each row contains text and its start/end positions.
        
        Returns:
            List of (row_text, row_start_pos, row_end_pos)
        """
        rows = []
        lines = ocr_text.split('\n')
        
        current_row_start = 0
        current_row_text = ""
        
        for line in lines:
            line = line.strip()
            if not line:
                # Empty line - end current row
                if current_row_text:
                    row_end = current_row_start + len(current_row_text)
                    rows.append((current_row_text, current_row_start, row_end))
                    current_row_text = ""
                current_row_start += len(line) + 1  # +1 for newline
                continue
            
            # Check if this line starts a new logical row
            # (contains Party ID pattern, or expiry date header, etc.)
            is_new_row = False
            party_id_pattern = r'\b\d{8,10}\b'  # 8-10 digit Party ID
            if re.search(party_id_pattern, line):
                # Line contains Party ID - start new row
                if current_row_text:
                    row_end = current_row_start + len(current_row_text)
                    rows.append((current_row_text, current_row_start, row_end))
                current_row_text = line
                current_row_start = ocr_text.find(line, current_row_start)
                is_new_row = True
            elif 'تاريخ إنتهاء' in line or 'Expiry Date' in line:
                # Header row - start new row
                if current_row_text:
                    row_end = current_row_start + len(current_row_text)
                    rows.append((current_row_text, current_row_start, row_end))
                current_row_text = line
                current_row_start = ocr_text.find(line, current_row_start)
                is_new_row = True
            
            if not is_new_row:
                # Continue current row
                if current_row_text:
                    current_row_text += " " + line
                else:
                    current_row_text = line
                    current_row_start = ocr_text.find(line, current_row_start)
        
        # Add last row
        if current_row_text:
            row_end = current_row_start + len(current_row_text)
            rows.append((current_row_text, current_row_start, row_end))
        
        return rows
    
    def match_all_parties_to_dates(self, party_ids: List[str], party_positions: List[tuple], date_positions: List[tuple], ocr_text: str = None) -> Dict[str, str]:
        """
        Match all Party IDs to dates using ROW-BASED matching (most accurate) with ORDER-BASED fallback.
        
        Strategy:
        1. ROW-BASED: If OCR text provided, detect table rows and match Party ID to date in same row
        2. ORDER-BASED: Match by position order (First Party → First Date)
        3. PROXIMITY: Fallback to closest date if above methods fail
        
        Args:
            party_ids: List of Party IDs to match
            party_positions: List of (party_id, start_pos, end_pos)
            date_positions: List of (date, start_pos, end_pos) - already sorted by position
            ocr_text: Optional OCR text for row-based matching
            
        Returns:
            Dictionary mapping Party ID -> Date
        """
        matches = {}
        used_dates = set()
        
        print(f"    🔍 Matching {len(party_ids)} parties to {len(date_positions)} dates...")
        
        # STRATEGY 1: ROW-BASED MATCHING (most accurate for table layouts)
        if ocr_text and len(party_ids) > 0 and len(date_positions) > 0:
            print(f"    📋 Attempting ROW-BASED matching (most accurate)...")
            rows = self.extract_table_rows(ocr_text)
            print(f"    📊 Detected {len(rows)} table row(s)")
            
            # For each party ID, find which row it's in, then find date in same row
            # CRITICAL: Track which dates are used to prevent duplicates
            row_matches = {}
            row_used_dates = set()  # Track dates used in row-based matching
            for party_id in party_ids:
                party_id_str = str(party_id).strip()
                
                # Find which row contains this party ID
                party_row_idx = None
                for row_idx, (row_text, row_start, row_end) in enumerate(rows):
                    # Check if party ID is in this row
                    if party_id_str in row_text:
                        # Verify it's actually the party ID (not part of another number)
                        party_id_pattern = r'\b' + re.escape(party_id_str) + r'\b'
                        if re.search(party_id_pattern, row_text):
                            party_row_idx = row_idx
                            print(f"    📍 Party ID {party_id} found in row {row_idx + 1}: '{row_text[:100]}...'")
                            break
                
                if party_row_idx is not None:
                    # Find expiry date in the same row
                    row_text, row_start, row_end = rows[party_row_idx]
                    
                    print(f"    🔍 DEBUG ROW-BASED: Row {party_row_idx + 1} boundaries: {row_start}-{row_end}")
                    print(f"    🔍 DEBUG ROW-BASED: Row text (first 200 chars): '{row_text[:200]}'")
                    print(f"    🔍 DEBUG ROW-BASED: Available pre-filtered dates: {[d for d, _, _ in date_positions]}")
                    
                    # CRITICAL: Use PRE-FILTERED dates from date_positions (already filtered for birth dates)
                    # Instead of extracting dates fresh from row text, match the pre-filtered dates to this row
                    # This ensures we only use dates that passed the birth date filter
                    print(f"    🔍 DEBUG ROW-BASED: Checking {len(date_positions)} pre-filtered dates against row {party_row_idx + 1}")
                    valid_dates_in_row = []
                    for date, date_start_pos, date_end_pos in date_positions:
                        # Check if this date is within the row boundaries
                        date_center = (date_start_pos + date_end_pos) // 2
                        in_row = row_start <= date_center <= row_end
                        is_used = date in used_dates
                        
                        print(f"    🔍 DEBUG ROW-BASED: Date '{date}' at pos {date_start_pos}-{date_end_pos} (center: {date_center})")
                        print(f"       - Row boundaries: {row_start}-{row_end}")
                        print(f"       - In row: {in_row}")
                        print(f"       - Already used: {is_used}")
                        
                        if in_row:
                            if not is_used:
                                valid_dates_in_row.append(date)
                                print(f"    ✅ DEBUG ROW-BASED: ✓ Date {date} ADDED to valid dates (in row, not used)")
                            else:
                                print(f"    ⚠️ DEBUG ROW-BASED: ✗ Date {date} SKIPPED (already used)")
                        else:
                            print(f"    ⚠️ DEBUG ROW-BASED: ✗ Date {date} SKIPPED (not in row)")
                    
                    print(f"    🔍 DEBUG ROW-BASED: Found {len(valid_dates_in_row)} valid pre-filtered date(s) in row: {valid_dates_in_row}")
                    valid_dates = valid_dates_in_row
                    
                    # If no pre-filtered dates found in row, fall back to extracting from row (but still filter)
                    if not valid_dates:
                        print(f"    ⚠️ No pre-filtered dates found in row, extracting from row text (with filtering)...")
                        # Extract dates from this row
                        date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
                        dates_in_row = re.findall(date_pattern, row_text)
                        
                        # Filter out Upload Dates (19/11/2025)
                        valid_dates = [d for d in dates_in_row if d not in ['19/11/2025', '19-11-2025', '2025-11-19']]
                        
                        # CRITICAL: Filter out Dates of Birth (dates before 2010) and invalid dates
                        filtered_dates = []
                        for date_str in valid_dates:
                            date_parts = date_str.replace('/', '-').split('-')
                            is_birth_date = False
                            year = None
                            day = None
                            month = None
                            if len(date_parts) == 3:
                                try:
                                    # Determine year position and extract day/month
                                    if len(date_parts[0]) == 4:  # YYYY-MM-DD
                                        year = int(date_parts[0])
                                        month = int(date_parts[1])
                                        day = int(date_parts[2])
                                    elif len(date_parts[2]) == 4:  # DD-MM-YYYY
                                        day = int(date_parts[0])
                                        month = int(date_parts[1])
                                        year = int(date_parts[2])
                                    elif len(date_parts[1]) == 4:  # DD-YYYY-MM
                                        day = int(date_parts[0])
                                        year = int(date_parts[1])
                                        month = int(date_parts[2])
                                    else:
                                        # Try 2-digit year
                                        if len(date_parts[2]) == 2:  # DD-MM-YY
                                            day = int(date_parts[0])
                                            month = int(date_parts[1])
                                            yy = int(date_parts[2])
                                            year = 1900 + yy if yy > 50 else 2000 + yy
                                        elif len(date_parts[0]) == 2:  # YY-MM-DD
                                            yy = int(date_parts[0])
                                            month = int(date_parts[1])
                                            day = int(date_parts[2])
                                            year = 1900 + yy if yy > 50 else 2000 + yy
                                    
                                    # CRITICAL: Validate day and month are in valid ranges
                                    if day and month:
                                        if not (1 <= day <= 31) or not (1 <= month <= 12):
                                            is_birth_date = True
                                            print(f"    🚫 ROW-BASED: Excluding Invalid Date {date_str} (day={day}, month={month} - invalid)")
                                    
                                    # CRITICAL: Exclude dates before 2010 (more strict - license expiry dates should be recent)
                                    # License expiry dates are typically 2010-2030+ (or Hijri 1400+)
                                    # Dates before 2010 are likely birth dates or very old licenses
                                    if year:
                                        # For Gregorian dates: exclude if < 2010
                                        if 1900 <= year < 2010:
                                            is_birth_date = True
                                            print(f"    🚫 ROW-BASED: Excluding Date of Birth/Old License {date_str} (year {year} < 2010)")
                                        # For Hijri dates: valid range is 1400-1600
                                        elif 1400 <= year <= 1600:
                                            # Valid Hijri date - keep it (but still validate day/month above)
                                            pass
                                        elif year < 1400:
                                            # Invalid Hijri date - exclude
                                            is_birth_date = True
                                            print(f"    🚫 ROW-BASED: Excluding Invalid Date {date_str} (year {year} < 1400)")
                                        elif year > 2100:
                                            # Invalid future date - likely OCR error
                                            is_birth_date = True
                                            print(f"    🚫 ROW-BASED: Excluding Invalid Date {date_str} (year {year} > 2100 - OCR error)")
                                except (ValueError, IndexError) as e:
                                    print(f"    ⚠️ ROW-BASED: Error parsing date {date_str}: {e}")
                                    is_birth_date = True  # Reject if we can't parse it
                            
                            if not is_birth_date:
                                filtered_dates.append(date_str)
                            else:
                                print(f"    🚫 ROW-BASED: Excluding Date {date_str} (year: {year}, day: {day}, month: {month})")
                        
                        valid_dates = filtered_dates
                    
                    if valid_dates:
                        # CRITICAL: If multiple parties are in the same row, assign dates in order
                        # Count how many parties are in this row
                        parties_in_this_row = []
                        for pid_check in party_ids:
                            pid_check_str = str(pid_check).strip()
                            pid_check_clean = re.sub(r'[^\d]', '', pid_check_str)
                            if pid_check_clean in row_text or pid_check_str in row_text:
                                parties_in_this_row.append(pid_check_clean if pid_check_clean else pid_check_str)
                        
                        # Find this party's index in the row
                        party_id_clean = re.sub(r'[^\d]', '', str(party_id).strip())
                        if not party_id_clean:
                            party_id_clean = str(party_id).strip()
                        
                        party_index_in_row = -1
                        for idx, pid_in_row in enumerate(parties_in_this_row):
                            if pid_in_row == party_id_clean or str(party_id).strip() == pid_in_row:
                                party_index_in_row = idx
                                break
                        
                        # Assign date based on party's position in row
                        print(f"    🔍 DEBUG ROW-BASED: Party {party_id} index in row: {party_index_in_row}, Valid dates count: {len(valid_dates)}")
                        print(f"    🔍 DEBUG ROW-BASED: Valid dates: {valid_dates}")
                        print(f"    🔍 DEBUG ROW-BASED: Used dates so far: {used_dates}")
                        
                        if party_index_in_row >= 0 and party_index_in_row < len(valid_dates):
                            matched_date = valid_dates[party_index_in_row]
                            print(f"    ✅ DEBUG ROW-BASED: Using date at index {party_index_in_row}: {matched_date}")
                        else:
                            # Fallback: use first available unused date
                            matched_date = None
                            for date in valid_dates:
                                if date not in used_dates:
                                    matched_date = date
                                    print(f"    ✅ DEBUG ROW-BASED: Using first unused date: {matched_date}")
                                    break
                            if not matched_date and valid_dates:
                                matched_date = valid_dates[0]  # Last resort
                                print(f"    ⚠️ DEBUG ROW-BASED: Using first date as last resort: {matched_date}")
                        
                        # CRITICAL: Validate that matched_date is NOT a birth date (double-check)
                        if matched_date:
                            print(f"    🔍 DEBUG ROW-BASED: Validating matched date '{matched_date}'...")
                            date_parts = matched_date.replace('/', '-').split('-')
                            is_birth_date = False
                            year = None
                            if len(date_parts) == 3:
                                try:
                                    if len(date_parts[0]) == 4:
                                        year = int(date_parts[0])
                                        print(f"    🔍 DEBUG ROW-BASED: Parsed as YYYY-MM-DD, year = {year}")
                                    elif len(date_parts[2]) == 4:
                                        year = int(date_parts[2])
                                        print(f"    🔍 DEBUG ROW-BASED: Parsed as DD-MM-YYYY, year = {year}")
                                    elif len(date_parts[1]) == 4:
                                        year = int(date_parts[1])
                                        print(f"    🔍 DEBUG ROW-BASED: Parsed as DD-YYYY-MM, year = {year}")
                                    
                                    if year:
                                        # For Gregorian dates: exclude if < 2010
                                        if 1900 <= year < 2010:
                                            is_birth_date = True
                                            print(f"    🚫 DEBUG ROW-BASED: REJECTING '{matched_date}' - year {year} < 2010 (BIRTH DATE/OLD LICENSE)")
                                        # For Hijri dates: valid range is 1400-1600
                                        elif 1400 <= year <= 1600:
                                            print(f"    ✅ DEBUG ROW-BASED: Date '{matched_date}' is VALID (Hijri year {year})")
                                        elif year > 2100:
                                            is_birth_date = True
                                            print(f"    🚫 DEBUG ROW-BASED: REJECTING '{matched_date}' - year {year} > 2100 (OCR ERROR)")
                                        else:
                                            print(f"    ✅ DEBUG ROW-BASED: Date '{matched_date}' is VALID (year {year} >= 2010)")
                                except (ValueError, IndexError) as e:
                                    print(f"    ⚠️ DEBUG ROW-BASED: Error parsing date '{matched_date}': {e}")
                            
                            if is_birth_date:
                                print(f"    🚫 DEBUG ROW-BASED: Skipping birth date {matched_date}, will try ORDER-BASED matching")
                                continue  # Skip this match, try ORDER-BASED instead
                        
                        if matched_date and matched_date not in row_used_dates:
                            row_matches[party_id] = matched_date
                            row_used_dates.add(matched_date)
                            used_dates.add(matched_date)  # Also add to main used_dates
                            print(f"    ✅ ROW-BASED: Party ID {party_id} → Date {matched_date} (from same row, position {party_index_in_row + 1})")
                        else:
                            if matched_date:
                                print(f"    ⚠️ Date {matched_date} already used in row-based matching, will try ORDER-BASED for Party ID {party_id}")
                            else:
                                print(f"    ⚠️ No valid date found for Party ID {party_id} in row, will try ORDER-BASED")
                    else:
                        print(f"    ⚠️ No valid expiry date found in row for Party ID {party_id}")
            
            # If row-based matching found matches for all parties, use it
            if len(row_matches) == len(party_ids):
                print(f"    ✅ ROW-BASED matching successful for all {len(party_ids)} parties!")
                return row_matches
            elif len(row_matches) > 0:
                print(f"    ⚠️ ROW-BASED matched {len(row_matches)}/{len(party_ids)} parties, using ORDER-BASED for rest")
                matches.update(row_matches)
        
        # STRATEGY 2: ORDER-BASED MATCHING (fallback or supplement)
        print(f"    📋 Using ORDER-BASED matching (table layout): First Party → First Date, Second Party → Second Date, etc.")
        
        # CRITICAL: Process ALL parties from input list in order, regardless of whether they're found in OCR
        # This ensures parties not found in OCR still get dates assigned in order
        # Skip parties already matched in row-based matching
        parties_to_match = [pid for pid in party_ids if pid not in matches]
        
        if len(parties_to_match) > 0:
            print(f"    📋 Processing {len(parties_to_match)} unmatched party(ies) with ORDER-BASED matching")
            
            # Sort parties by their position in text (if found in OCR), otherwise keep input order
            party_ids_with_pos = []
            for party_id in parties_to_match:
                party_id_str = str(party_id).strip()
                party_id_clean = re.sub(r'[^\d]', '', party_id_str)
                found = False
                for pid, start_pos, end_pos in party_positions:
                    pid_str = str(pid).strip()
                    pid_clean = re.sub(r'[^\d]', '', pid_str)
                    # Try exact match first
                    if pid_clean == party_id_clean or pid_str == party_id_str:
                        party_ids_with_pos.append((party_id, (start_pos + end_pos) // 2, start_pos, end_pos))
                        print(f"    📍 Party ID {party_id} found at position {start_pos}-{end_pos}")
                        found = True
                        break
                    # Try partial match (last 8-9 digits)
                    elif len(party_id_clean) >= 8 and len(pid_clean) >= 8:
                        if party_id_clean[-8:] == pid_clean[-8:] or party_id_clean[-9:] == pid_clean[-9:]:
                            party_ids_with_pos.append((party_id, (start_pos + end_pos) // 2, start_pos, end_pos))
                            print(f"    📍 Party ID {party_id} matched to {pid} at position {start_pos}-{end_pos} (partial match)")
                            found = True
                            break
                
                if not found:
                    # Party not found in OCR - assign a high position number to process it last
                    # But still include it in the list to ensure it gets a date
                    party_ids_with_pos.append((party_id, 999999, -1, -1))
                    print(f"    ⚠️ Party ID {party_id} not found in extracted Party IDs - will assign date in order")
            
            # Sort by position (ascending - first party first, second party second, etc.)
            # Parties not found in OCR (position 999999) will be processed after those found
            party_ids_with_pos.sort(key=lambda x: x[1])
            print(f"    📊 Party order (by position): {[pid for pid, _, _, _ in party_ids_with_pos]}")
            
            # Dates are already sorted by position (from extract_all_expiry_dates_with_positions)
            print(f"    📊 Date order (by position): {[date for date, _, _ in date_positions]}")
            
            # CRITICAL: ORDER-BASED MATCHING - assign dates in order, ensuring uniqueness
            date_idx = 0
            for party_id, _, _, _ in party_ids_with_pos:
                # Find next unused date
                matched = False
                # Start from date_idx and look for unused dates
                while date_idx < len(date_positions):
                    date, date_start, date_end = date_positions[date_idx]
                    if date not in used_dates:
                        matches[party_id] = date
                        used_dates.add(date)
                        print(f"    ✅ ORDER-BASED: Party ID {party_id} → Date {date_idx + 1} ({date})")
                        date_idx += 1
                        matched = True
                        break
                    date_idx += 1
                
                if not matched:
                    # Try to find any unused date (scan from beginning)
                    for alt_idx, (alt_date, _, _) in enumerate(date_positions):
                        if alt_date not in used_dates:
                            matches[party_id] = alt_date
                            used_dates.add(alt_date)
                            print(f"    ✅ ORDER-BASED: Party ID {party_id} → Date {alt_idx + 1} ({alt_date}) [found unused]")
                            date_idx = alt_idx + 1
                            matched = True
                            break
                
                if not matched:
                    # Last resort: if all dates are used, reuse the first date (shouldn't happen, but handle gracefully)
                    if len(date_positions) > 0:
                        first_date = date_positions[0][0]
                        matches[party_id] = first_date
                        print(f"    ⚠️ ORDER-BASED: Party ID {party_id} → Date {first_date} [REUSED - no unused dates available]")
                    else:
                        print(f"    ⚠️ No dates available for Party ID {party_id}")
        
        print(f"    ✅ Final matches: {matches}")
        return matches
    
    def extract_license_expiry_from_image(self, image_data: Any, target_party_id: str = None) -> str:
        """
        Extract license expiry date from image or PDF using OCR
        Process: Base64 → Image → OCR Text → Extract Date
        
        IMPORTANT: This function specifically extracts "تاريخ إنتهاء الرخصة" (License Expiry Date)
        and EXCLUDES "تاريخ إضافة الرخصة" (Upload Date) which is often 19/11/2025.
        
        Extraction Strategy:
        1. Uses priority patterns that match "تاريخ إنتهاء الرخصة" (Expiry Date) specifically
        2. Excludes dates near "تاريخ إضافة الرخصة" (Upload Date) keywords
        3. Validates that matched text contains expiry keywords, not upload keywords
        4. Uses distance-based validation to ensure date is closer to expiry than upload
        
        Args:
            image_data: Can be base64 string, image path, PDF bytes, or PIL Image
            target_party_id: Optional Party ID to match - only extract if Party ID matches
            
        Returns:
            Expiry date string or "not identify" if not found
        """
        try:
            image = None
            
            # Step 1: Convert base64 to image
            if isinstance(image_data, str):
                # Check if it's base64
                is_base64 = False
                image_data_clean = image_data.strip()
                
                # Check for base64 indicators
                if (len(image_data_clean) > 100 or 
                    image_data_clean.startswith('data:image') or 
                    image_data_clean.startswith('iVBORw0KGgo') or
                    image_data_clean.startswith('/9j/') or
                    image_data_clean.startswith('R0lGODlh') or
                    image_data_clean.startswith('UklGR')):
                    is_base64 = True
                
                if is_base64:
                    try:
                        # Remove data URL prefix if present
                        if ',' in image_data_clean:
                            image_data_clean = image_data_clean.split(',')[1]
                        
                        # Decode base64 string to bytes
                        img_bytes = base64.b64decode(image_data_clean)
                        
                        # Convert bytes to PIL Image
                        image = Image.open(BytesIO(img_bytes))
                        
                        # Convert to RGB if necessary
                        if image.mode != 'RGB':
                            image = image.convert('RGB')
                    except Exception as e:
                        # Try as file path
                        if os.path.exists(image_data):
                            image = Image.open(image_data)
                            if image.mode != 'RGB':
                                image = image.convert('RGB')
                        else:
                            return "not identify"
                else:
                    # Try as file path
                    if os.path.exists(image_data):
                        image = Image.open(image_data)
                        if image.mode != 'RGB':
                            image = image.convert('RGB')
                    else:
                        return "not identify"
            elif isinstance(image_data, bytes):
                # Check if it's PDF
                if image_data.startswith(b'%PDF'):
                    if not PDF_SUPPORT:
                        return "not identify"
                    try:
                        if POPPLER_PATH and os.path.exists(POPPLER_PATH):
                            images = convert_from_bytes(image_data, poppler_path=POPPLER_PATH)
                        else:
                            images = convert_from_bytes(image_data)
                        if not images:
                            return "not identify"
                        image = images[0]  # Use first page
                        if image.mode != 'RGB':
                            image = image.convert('RGB')
                    except Exception as e:
                        return "not identify"
                else:
                    # Try as image bytes
                    image = Image.open(BytesIO(image_data))
                    if image.mode != 'RGB':
                        image = image.convert('RGB')
            elif isinstance(image_data, Image.Image):
                image = image_data
                if image.mode != 'RGB':
                    image = image.convert('RGB')
            else:
                return "not identify"
            
            if image is None:
                return "not identify"
            
            # Perform OCR with Arabic and English support - OPTIMIZED FOR SPEED
            # Use best PSM mode first (PSM 6 for tables) - only try others if needed
            # PSM 6 = uniform block of text (best for tables, fastest)
            ocr_text = ""
            
            # OPTIMIZATION: Try best mode first, only fallback if needed
            try:
                config = '--psm 6 --oem 3'  # Best for tables, fastest
                ocr_text = pytesseract.image_to_string(image, lang='ara+eng', config=config)
                if len(ocr_text.strip()) < 20:
                    # Quick fallback to PSM 4 if PSM 6 didn't work well
                    try:
                        config = '--psm 4 --oem 3'
                        ocr_text = pytesseract.image_to_string(image, lang='ara+eng', config=config)
                    except:
                        pass
            except Exception as e:
                # Only try Arabic if ara+eng fails
                try:
                    config = '--psm 6 --oem 3'
                    ocr_text = pytesseract.image_to_string(image, lang='ara', config=config)
                except:
                    pass
            
            if len(ocr_text.strip()) < 10:
                print(f"    ⚠️ OCR text too short ({len(ocr_text)} chars) - cannot extract date")
                return "not identify"
            
            # DEBUG: Print OCR text sample for investigation (if extraction fails)
            # Store OCR text for later debug output if needed
            ocr_text_sample = ocr_text[:500] if len(ocr_text) > 500 else ocr_text
            
            # Step 3: OPTIMIZED Party ID matching - fast early exit
            if target_party_id:
                target_id_str = str(target_party_id).strip()
                target_id_clean = re.sub(r'[^\d]', '', target_id_str)
                
                # OPTIMIZATION: Quick check if Party ID exists in OCR text (fast string search)
                if target_id_clean in ocr_text or target_id_clean[-8:] in ocr_text or target_id_clean[-9:] in ocr_text:
                    # Party ID likely exists, continue with extraction
                    pass
                else:
                    # OPTIMIZATION: If Party ID not found, still try extraction (might be in different format)
                    pass
            
            # Step 4: Extract expiry date from OCR text
            # Clean OCR text: remove invisible Unicode characters that break regex matching
            # Remove left-to-right mark (LRM: \u200E), right-to-left mark (RLM: \u200F), and other formatting marks
            ocr_text_clean = ocr_text
            # Remove invisible Unicode formatting characters
            invisible_chars = [
                '\u200E',  # Left-to-right mark
                '\u200F',  # Right-to-left mark
                '\u200B',  # Zero-width space
                '\u200C',  # Zero-width non-joiner
                '\u200D',  # Zero-width joiner
                '\uFEFF',  # Zero-width no-break space (BOM)
                '\u2060',  # Word joiner
            ]
            for char in invisible_chars:
                ocr_text_clean = ocr_text_clean.replace(char, '')
            
            # Normalize whitespace
            ocr_text_normalized = ' '.join(ocr_text_clean.split())
            
            # Keywords to EXCLUDE (إصدار الرخصة - Issue Date, Upload Date, Version Date)
            # IMPORTANT: Exclude Upload Date (تاريخ إضافة الرخصة) which is often 19/11/2025
            # IMPORTANT: Exclude Version Date (تاريخ الإصدار / Version Date) which is 19/11/2025
            exclude_keywords = [
                # Issue/Version Date keywords
                'إصدار', 'اصدار', 'تاريخ الإصدار', 'تاريخ الاصدار', 
                'Issue Date', 'Issue', 'Date of Issue', 'Issued',
                'تاريخ الصدور', 'صدر', 'صدرت',
                'Version Date', 'Version',
                # Upload Date keywords - CRITICAL: These must be excluded
                'Upload Date', 'تاريخ إضافة', 'تاريخ الرفع', 'تاريخ إضافة الرخصة', 'تاريخ إضافةالرخصة',  # No space variant
                'تاريخ الرفع الرخصة', 'إضافة الرخصة', 'إضافةالرخصة',  # No space variant
                'رفع الرخصة', 'رفعالرخصة',  # No space variant
                'تاريخ اضافة', 'تاريخ اضافة الرخصة', 'تاريخ اضافةالرخصة',  # No space variant
                'اضافة الرخصة', 'اضافةالرخصة',  # No space variant
                # Common OCR variations
                'تاريخ إضافه', 'تاريخ اضافه', 'إضافه', 'اضافه', 'تاريخ إضافهالرخصة', 'إضافهالرخصة'
            ]
            
            # Helper function to check if date is near exclude keywords
            def is_near_exclude_keyword(text, date_pos, date_length):
                """Check if date is near any exclude keyword (إصدار, Version Date, Upload Date, etc.)"""
                # Use larger context window to catch dates near exclude keywords
                context_start = max(0, date_pos - 150)  # Increased to catch more context
                context_end = min(len(text), date_pos + date_length + 150)  # Increased to catch more context
                context = text[context_start:context_end]
                context_lower = context.lower()
                
                # CRITICAL: Check for report header keywords FIRST (highest priority exclusion)
                # These should ALWAYS exclude dates, even if expiry keywords are present
                report_header_keywords = [
                    'Version Date', 'تاريخ الإصدار', 'تاريخ الاصدار', 'Version',
                    'Accident Time', 'وقت الحادث', 'Accident Date', 'تاريخ الحادث',
                    'Case Number', 'رقم الحالة', 'Case',
                    'Final Report', 'التقرير', 'Report',
                    'Liability Determination Report'
                ]
                for header_kw in report_header_keywords:
                    if header_kw in context or header_kw.lower() in context_lower:
                        # Find position of header keyword relative to date
                        header_pos_in_context = context.find(header_kw)
                        if header_pos_in_context == -1:
                            header_pos_in_context = context_lower.find(header_kw.lower())
                        if header_pos_in_context != -1:
                            header_pos_abs = context_start + header_pos_in_context
                            date_center = date_pos + date_length // 2
                            header_center = header_pos_abs + len(header_kw) // 2
                            distance = abs(header_center - date_center)
                            # If date is within 200 chars of header keyword, exclude it
                            if distance < 200:
                                print(f"    🚫 Date is near report header keyword '{header_kw}' (distance: {distance}) - EXCLUDING")
                                return True
                
                # Check for exclude keywords in context
                for exclude_kw in exclude_keywords:
                    # Check both lowercase and original case (for Arabic)
                    if exclude_kw.lower() in context_lower or exclude_kw in context:
                        # Additional check: make sure it's not also near expiry keywords (which take priority)
                        expiry_kw_in_context = any(kw in context for kw in ['إنتهاء', 'انتهاء', 'Expiry', 'Expires'])
                        if not expiry_kw_in_context:
                            print(f"    ⚠️ Date is near exclude keyword '{exclude_kw}' - will skip this date")
                            return True
                        else:
                            # If expiry keyword is also present, check distances to determine priority
                            # Find positions to compare distances
                            exclude_pos_in_context = context.find(exclude_kw)
                            if exclude_pos_in_context == -1:
                                exclude_pos_in_context = context_lower.find(exclude_kw.lower())
                            expiry_pos_in_context = context.find('إنتهاء')
                            if expiry_pos_in_context == -1:
                                expiry_pos_in_context = context.find('انتهاء')
                            if expiry_pos_in_context == -1:
                                expiry_pos_in_context = context_lower.find('expiry')
                            
                            if exclude_pos_in_context != -1 and expiry_pos_in_context != -1:
                                date_center_rel = (date_pos - context_start) + date_length // 2
                                exclude_dist = abs(exclude_pos_in_context - date_center_rel)
                                expiry_dist = abs(expiry_pos_in_context - date_center_rel)
                                # If exclude keyword is closer, exclude the date
                                if exclude_dist < expiry_dist:
                                    print(f"    ⚠️ Date is closer to exclude keyword '{exclude_kw}' than expiry keyword - EXCLUDING")
                                    return True
                            
                            # If expiry keyword is closer or no expiry keyword found, keep it
                            print(f"    ✓ Date is near exclude keyword '{exclude_kw}' BUT expiry keyword takes priority - KEEPING")
                            return False
                
                # Also check if date appears to be an Upload Date by looking for "إضافة" or "رفع" patterns
                # These should be excluded even if expiry keywords are not nearby
                upload_patterns = [
                    r'تاريخ\s*إضافة\s*الرخصة',
                    r'تاريخ\s*إضافه\s*الرخصة',
                    r'تاريخ\s*اضافة\s*الرخصة',
                    r'تاريخ\s*الرفع',
                    r'Upload\s*Date',
                    r'إضافة\s*الرخصة',
                    r'رفع\s*الرخصة'
                ]
                for pattern in upload_patterns:
                    if re.search(pattern, context, re.IGNORECASE | re.UNICODE):
                        # Check if expiry keyword is NOT nearby (if expiry is nearby, it takes priority)
                        expiry_nearby = any(kw in context for kw in ['إنتهاء', 'انتهاء', 'Expiry', 'Expires'])
                        if not expiry_nearby:
                            print(f"    ⚠️ Date matches Upload Date pattern '{pattern}' and no expiry keyword nearby - will skip")
                            return True
                
                return False
            
            # Priority patterns - ONLY "تاريخ إنتهاء الرخصة" (License Expiry Date)
            # CRITICAL: These patterns MUST contain the full phrase "تاريخ إنتهاء الرخصة"
            # We do NOT extract dates from "تاريخ إضافة الرخصة" (Upload Date) or any other field
            # Note: "إنتهاء" (with kasra and hamza) vs "انتهاء" (with fatha) - both are valid
            # Patterns handle invisible Unicode characters and flexible spacing
            # IMPORTANT: OCR may show "Expiry Date" or "License Expiry" - both are handled
            priority_patterns = [
                # HIGHEST PRIORITY: Full phrase "تاريخ إنتهاء الرخصة" with "Expiry Date" (most common in OCR)
                r'تاريخ\s*إنتهاء\s*الرخصة\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*إنتهاء\s*الرخصه\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*إنتهاء\s*الرخصة\s*[/\s]*\s*Expiry\s*Date\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*إنتهاء\s*الرخصه\s*[/\s]*\s*Expiry\s*Date\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                # Full phrase with "License Expiry"
                r'تاريخ\s*إنتهاء\s*الرخصة\s*[/\s]*\s*License\s*Expiry\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*إنتهاء\s*الرخصه\s*[/\s]*\s*License\s*Expiry\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                # Full phrase "تاريخ إنتهاء الرخصة" (Arabic only, with kasra+hamza - most common)
                r'تاريخ\s*إنتهاء\s*الرخصة\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*إنتهاء\s*الرخصه\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                # Full phrase with alternative spelling (fatha instead of kasra) and "Expiry Date"
                r'تاريخ\s*انتهاء\s*الرخصة\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصه\s*[/\s]*\s*Expiry\s*Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصة\s*[/\s]*\s*Expiry\s*Date\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصه\s*[/\s]*\s*Expiry\s*Date\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصة\s*[/\s]*\s*License\s*Expiry\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصه\s*[/\s]*\s*License\s*Expiry\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصة\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاء\s*الرخصه\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                # Table format: "تاريخ" followed by "إنتهاء الرخصة" (flexible spacing for table columns)
                r'تاريخ[:\s]*إنتهاء\s*الرخصة\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ[:\s]*انتهاء\s*الرخصة\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                # OCR variation: انتهاءء الرخصة (with two hamzas)
                r'تاريخ\s*انتهاءء\s*الرخصة\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'تاريخ\s*انتهاءء\s*الرخصه\s*[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            ]
            
            # REMOVED: All patterns without "تاريخ" prefix (too flexible, might match wrong dates)
            # REMOVED: English-only patterns (not specific enough)
            # REMOVED: Very flexible patterns (just "إنتهاء" - too risky)
            
            # REMOVED: Secondary patterns - too flexible, might match wrong dates
            # We ONLY use priority patterns that contain "تاريخ إنتهاء الرخصة"
            arabic_patterns = []  # Empty - we don't use flexible patterns anymore
            
            # Date patterns
            date_patterns = [
                r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
                r'(\d{1,2}\.\d{1,2}\.\d{2,4})',
                r'(\d{4}\.\d{1,2}\.\d{1,2})',
            ]
            
            # OPTIMIZATION: Skip verbose debugging - only check keywords if needed
            has_expiry_keyword = any(kw in ocr_text_normalized for kw in ['إنتهاء', 'انتهاء', 'تاريخ إنتهاء', 'Expiry Date'])
            
            # OPTIMIZED: If target_party_id is provided, use fast proximity-based matching
            if target_party_id:
                # Extract all Party IDs and dates with positions (optimized)
                party_positions = self.extract_party_ids_with_positions(ocr_text_normalized)
                date_positions = self.extract_all_expiry_dates_with_positions(ocr_text_normalized, exclude_keywords)
                
                # Match date to Party ID based on proximity
                if party_positions and date_positions:
                    matched_date = self.match_date_to_party_id(target_party_id, party_positions, date_positions)
                    if matched_date and matched_date != "not identify":
                        return matched_date
                elif date_positions and not party_positions:
                    # If we found dates but no Party IDs, return first date (fallback)
                    return date_positions[0][0]
            
            # FALLBACK: Original pattern matching (for backward compatibility or when no target_party_id)
            # CRITICAL: Extract ALL dates from the matched line, not just the first one
            # Try priority patterns first (these are already specific to expiry)
            # These patterns are ordered from most specific to least specific
            for pattern_idx, pattern in enumerate(priority_patterns):
                match = re.search(pattern, ocr_text_normalized, re.IGNORECASE | re.UNICODE)
                if match:
                    date_found = match.group(1).strip()
                    if date_found:
                        match_pos = match.start(1)
                        match_start = match.start(0)  # Start of entire match (including keyword)
                        match_end = match.end(0)  # End of entire match
                        
                        # Extract the matched text to see what keyword was matched
                        matched_text = ocr_text_normalized[match_start:match_end]
                        print(f"    🔍 Pattern {pattern_idx + 1} matched: '{matched_text[:100]}'")
                        print(f"    🔍 Extracted date: {date_found}")
                        
                        # CRITICAL: Extract ALL dates from the matched line, not just the first one
                        # For table layouts, there might be multiple dates on the same line
                        # Example: "تاريخ إنتهاء الرخصة / Expiry Date 21/06/1451 06/02/1451"
                        # We need to extract BOTH dates: 21/06/1451 AND 06/02/1451
                        
                        # Get the full line containing the match (for table layouts)
                        line_start = ocr_text_normalized.rfind('\n', 0, match_start)
                        if line_start == -1:
                            line_start = 0
                        else:
                            line_start += 1  # Start after newline
                        
                        line_end = ocr_text_normalized.find('\n', match_end)
                        if line_end == -1:
                            line_end = len(ocr_text_normalized)
                        
                        full_line = ocr_text_normalized[line_start:line_end]
                        print(f"    🔍 Full line containing match: '{full_line[:200]}'")
                        
                        # Extract ALL dates from this line (not just the first one)
                        date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
                        all_dates_in_line = re.findall(date_pattern, full_line)
                        print(f"    🔍 Found {len(all_dates_in_line)} date(s) in line: {all_dates_in_line}")
                        
                        # Filter dates: only keep those that are NOT Upload Date (19/11/2025 is common Upload Date)
                        valid_dates = []
                        for date_str in all_dates_in_line:
                            # Check if it's likely an Upload Date (recent Gregorian date like 19/11/2025)
                            date_parts = date_str.replace('/', '-').split('-')
                            if len(date_parts) == 3:
                                try:
                                    if len(date_parts[0]) == 4:  # YYYY-MM-DD
                                        year = int(date_parts[0])
                                    elif len(date_parts[2]) == 4:  # DD-MM-YYYY
                                        year = int(date_parts[2])
                                    else:
                                        year = None
                                    
                                    # Skip if it's a recent Upload Date (2024-2026)
                                    if year and 2024 <= year <= 2026:
                                        # Check if it's the common Upload Date format (19/11/2025)
                                        if date_str in ['19/11/2025', '19-11-2025', '2025-11-19']:
                                            print(f"    ⚠️ Skipping Upload Date: {date_str}")
                                            continue
                                    
                                    valid_dates.append(date_str)
                                except (ValueError, IndexError):
                                    valid_dates.append(date_str)
                            else:
                                valid_dates.append(date_str)
                        
                        print(f"    🔍 Valid dates (excluding Upload Dates): {valid_dates}")
                        
                        # CRITICAL: Try each valid date until we find one that's not already used
                        # This ensures each party gets a different date when multiple dates exist
                        date_found = None
                        for date_candidate in valid_dates:
                            # Check if this date is already in used_dates_for_case (if available)
                            # Note: used_dates_for_case is passed from the calling function
                            # For now, we'll use the first valid date, but the caller should check for reuse
                            date_found = date_candidate
                            print(f"    🔍 Trying date: {date_found}")
                            break  # Use first valid date for now - caller will check for reuse
                        
                        if not date_found and valid_dates:
                            date_found = valid_dates[0]  # Fallback to first if none selected
                        
                        if date_found:
                            print(f"    🔍 Using date: {date_found}")
                            if len(valid_dates) > 1:
                                print(f"    ⚠️ NOTE: Found {len(valid_dates)} dates in line: {valid_dates}")
                                print(f"    ⚠️ Using first date {date_found} for this party")
                                print(f"    ⚠️ Other dates in line: {valid_dates[1:]}")
                                print(f"    ⚠️ These should be available for other parties via pre-extraction")
                        else:
                            # No valid dates found, use originally matched date
                            print(f"    ⚠️ No valid dates after filtering, using originally matched date: {date_found}")
                        
                        # OPTIMIZED: Fast validation - check exclusion and return immediately
                        if not is_near_exclude_keyword(ocr_text_normalized, match_pos, len(date_found)):
                            # Quick check for expiry keywords in matched text
                            has_expiry = any(kw in matched_text for kw in ['إنتهاء', 'انتهاء', 'Expiry', 'Expires'])
                            has_upload = any(kw in matched_text for kw in ['إضافة', 'اضافة', 'رفع', 'Upload'])
                            
                            if has_expiry and not has_upload:
                                return date_found
                            elif not has_upload:  # If no upload keyword, accept it
                                return date_found
            
            # REMOVED: All fallback logic that searches for dates near keywords
            # We ONLY use priority patterns that contain the full "تاريخ إنتهاء الرخصة" phrase
            # This ensures we never extract dates from "تاريخ إضافة الرخصة" (Upload Date) or other fields
            print(f"    ℹ️ Only using patterns that contain 'تاريخ إنتهاء الرخصة' - no fallback searches")
            
            # Check if Arabic expiry keywords exist (for debugging only)
            expiry_keywords = [
                'تاريخ إنتهاء', 'تاريخ انتهاء',  # Full phrase with "تاريخ"
            ]
            has_expiry_keyword = any(keyword in ocr_text_normalized for keyword in expiry_keywords)
            
            # OPTIMIZED FALLBACK: Fast improved extraction with early exit
            if target_party_id:
                try:
                    from excel_ocr_license_processor import ExcelOCRLicenseProcessor
                    fallback_processor = ExcelOCRLicenseProcessor()
                    
                    # Try direct extraction first (fastest)
                    fallback_date = fallback_processor.extract_license_expiry_from_ocr_text(ocr_text_normalized, str(target_party_id))
                    if fallback_date:
                        return fallback_date
                    
                    # If direct extraction failed, try extracting all dates (one-time operation)
                    all_party_dates = fallback_processor.extract_all_license_expiry_dates(ocr_text_normalized)
                    if all_party_dates:
                        target_id_clean = re.sub(r'[^\d]', '', str(target_party_id).strip())
                        
                        # Fast exact match
                        if target_id_clean in all_party_dates:
                            return all_party_dates[target_id_clean]
                        
                        # Fast partial match (last 8-9 digits)
                        for ocr_party_id, date in all_party_dates.items():
                            ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                            if len(target_id_clean) >= 8 and len(ocr_id_clean) >= 8:
                                if target_id_clean[-8:] == ocr_id_clean[-8:] or target_id_clean[-9:] == ocr_id_clean[-9:]:
                                    return date
                        
                        # Fast fuzzy match (only if needed)
                        from difflib import SequenceMatcher
                        for ocr_party_id, date in all_party_dates.items():
                            ocr_id_clean = re.sub(r'[^\d]', '', str(ocr_party_id))
                            if target_id_clean in ocr_id_clean or ocr_id_clean in target_id_clean:
                                return date
                            ratio = SequenceMatcher(None, target_id_clean, ocr_id_clean).ratio()
                            if ratio >= 0.85:  # Higher threshold for speed
                                return date
                        
                        # Last resort: first available date
                        if all_party_dates:
                            return list(all_party_dates.values())[0]
                except Exception:
                    pass
            
            # REMOVED: All the fallback code below - we don't search for dates near keywords anymore
            # This was removed to ensure we ONLY extract from "تاريخ إنتهاء الرخصة" patterns
            if False:  # Disabled - we don't use this fallback anymore
                print(f"    🔍 Found expiry keywords, searching for dates nearby...")
                # Find dates near expiry keywords only
                # For tables, search for all occurrences and use larger context window
                for keyword in expiry_keywords:
                    # Search for all occurrences of keyword (tables might have multiple rows)
                    keyword_positions = []
                    start_pos = 0
                    while True:
                        pos = ocr_text_normalized.find(keyword, start_pos)
                        if pos == -1:
                            break
                        keyword_positions.append(pos)
                        start_pos = pos + 1
                    
                    if keyword_positions:
                        print(f"    🔍 Found '{keyword}' {len(keyword_positions)} time(s) in text")
                    
                    for keyword_pos in keyword_positions:
                        # For tables, use larger context window (dates might be in next column)
                        start = max(0, keyword_pos - 30)
                        end = min(len(ocr_text_normalized), keyword_pos + len(keyword) + 150)  # Increased for tables
                        context = ocr_text_normalized[start:end]
                        
                        print(f"    🔍 Keyword '{keyword}' at position {keyword_pos}, context: {context[:200]}")
                        
                        # Make sure context doesn't contain issue keywords
                        if not any(ikw in context for ikw in issue_keywords):
                            # Also check that context doesn't contain Upload Date keywords (which should be excluded)
                            upload_keywords_in_context = ['إضافة', 'اضافة', 'رفع', 'Upload Date']
                            has_upload_keyword = any(ukw in context for ukw in upload_keywords_in_context)
                            
                            # If Upload Date keyword is present, make sure Expiry keyword is closer to the date
                            if has_upload_keyword:
                                print(f"    ⚠️ Context contains Upload Date keyword - will verify expiry keyword is closer")
                            
                            for pattern in date_patterns:
                                matches = re.finditer(pattern, context)
                                for match in matches:
                                    date_found = match.group(1).strip()
                                    if date_found:
                                        # Check position relative to original text
                                        date_pos_in_context = match.start(1)
                                        date_pos_in_full = start + date_pos_in_context
                                        
                                        # If Upload Date keyword exists, check distances
                                        if has_upload_keyword:
                                            # Find positions of expiry and upload keywords relative to date
                                            expiry_dist = abs(keyword_pos - date_pos_in_full)
                                            upload_keyword_pos = -1
                                            for ukw in upload_keywords_in_context:
                                                pos = context.find(ukw)
                                                if pos != -1:
                                                    upload_keyword_pos = start + pos
                                                    break
                                            
                                            if upload_keyword_pos != -1:
                                                upload_dist = abs(upload_keyword_pos - date_pos_in_full)
                                                if upload_dist < expiry_dist:
                                                    print(f"    ⚠️ Date {date_found} is closer to Upload Date keyword than Expiry keyword - SKIPPING")
                                                    continue
                                        
                                        if not is_near_exclude_keyword(ocr_text_normalized, date_pos_in_full, len(date_found)):
                                            print(f"    ✅✅✅ Found expiry date near '{keyword}': {date_found}")
                                            return date_found
                        else:
                            print(f"    ⚠️ Context contains issue keywords, skipping...")
            
            # No date found - return immediately (optimized)
            print(f"    ❌ No expiry date found in OCR text")
            ocr_sample_for_debug = ocr_text_sample[:1000] if 'ocr_text_sample' in locals() else (ocr_text[:1000] if len(ocr_text) > 1000 else ocr_text)
            print(f"    🔍 DEBUG: OCR text sample (first 1000 chars): '{ocr_sample_for_debug}'")
            # Check if expiry keywords exist
            expiry_keywords = ['تاريخ إنتهاء', 'تاريخ انتهاء', 'Expiry Date', 'License Expiry']
            found_keywords = [kw for kw in expiry_keywords if kw in ocr_text_normalized]
            if found_keywords:
                print(f"    🔍 DEBUG: Found expiry keywords but no matching dates: {found_keywords}")
                # Try to show context around keywords
                for kw in found_keywords[:2]:  # Show first 2 keywords
                    idx = ocr_text_normalized.find(kw)
                    if idx != -1:
                        context_start = max(0, idx - 200)
                        context_end = min(len(ocr_text_normalized), idx + len(kw) + 200)
                        context = ocr_text_normalized[context_start:context_end]
                        print(f"    🔍 DEBUG: Context around '{kw}' (position {idx}): '{context}'")
            else:
                print(f"    🔍 DEBUG: NO expiry keywords found in OCR - might be wrong OCR or image")
            return "not identify"
            
        except Exception as e:
            print(f"    ⚠️ ERROR in extract_license_expiry_from_image: {str(e)[:200]}")
            import traceback
            print(f"    ⚠️ Traceback: {traceback.format_exc()[:300]}")
            return "not identify"
    
    def clean_data(self, data: str) -> str:
        """Clean data from Excel (remove quotes, fix encoding, etc.)"""
        data_str = str(data).strip()
        
        # Remove extra quotes
        if data_str.startswith('"') and data_str.endswith('"'):
            data_str = data_str[1:-1]
        if data_str.startswith("'") and data_str.endswith("'"):
            data_str = data_str[1:-1]
        
        # Fix Excel line break encoding
        data_str = data_str.replace('_x000D_', '\r')
        data_str = data_str.replace('_x000A_', '\n')
        data_str = data_str.replace('_x000d_', '\r')
        data_str = data_str.replace('_x000a_', '\n')
        
        # Replace Excel unicode escapes
        def replace_excel_unicode(match):
            code = int(match.group(1), 16)
            return chr(code)
        data_str = re.sub(r'_x([0-9A-Fa-f]{4})_', replace_excel_unicode, data_str)
        
        # Fix HTML entities
        data_str = data_str.replace('&quot;', '"')
        data_str = data_str.replace('&amp;', '&')
        data_str = data_str.replace('&lt;', '<')
        data_str = data_str.replace('&gt;', '>')
        data_str = data_str.replace('&apos;', "'")
        
        # Remove BOM
        if data_str.startswith('\ufeff'):
            data_str = data_str[1:]
        
        return data_str
    
    def normalize_date_format(self, date_str: str) -> str:
        """
        Normalize date format to YYYY-MM-DD
        Handles various formats including:
        - YYYYMMDD (e.g., "20251119" -> "2025-11-19")
        - DD/MM/YYYY (e.g., "19/11/2025" -> "2025-11-19")
        - YYYY-MM-DD (already normalized)
        - DD-MM-YYYY (e.g., "19-11-2025" -> "2025-11-19")
        
        Args:
            date_str: Date string in various formats
            
        Returns:
            Normalized date string in format "YYYY-MM-DD" or original string if parsing fails
        """
        if not date_str or date_str.strip() == "" or date_str == "not identify":
            return date_str
        
        try:
            date_str = str(date_str).strip()
            
            # Handle YYYYMMDD format (8 digits, no separators)
            # Example: "20251119" -> "2025-11-19" or "14451206" (Hijri) -> keep for conversion
            if date_str.isdigit() and len(date_str) == 8:
                year = int(date_str[0:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                
                # CRITICAL: Validate date parts - reject invalid dates
                # Day must be 1-31, month must be 1-12
                # Year: Gregorian (1900-2100) or Hijri (1400-1600)
                if not (1 <= day <= 31 and 1 <= month <= 12):
                    print(f"    🚫 Invalid date {date_str}: day={day}, month={month} (out of range)")
                    return date_str  # Return original if invalid
                
                if (1900 <= year <= 2100 or 1400 <= year <= 1600):
                    normalized = f"{year:04d}-{month:02d}-{day:02d}"
                    print(f"    ✓ Normalized date format {date_str} to {normalized}")
                    return normalized
                else:
                    print(f"    🚫 Invalid date {date_str}: year={year} (out of valid range)")
                    return date_str
            
            # Handle formats with separators
            if '/' in date_str or '-' in date_str:
                separator = '/' if '/' in date_str else '-'
                parts = date_str.split(separator)
                if len(parts) == 3:
                    try:
                        part1 = int(parts[0])
                        part2 = int(parts[1])
                        part3 = int(parts[2])
                        
                        # Determine format: YYYY-MM-DD or DD-MM-YYYY
                        if part1 > 31:
                            # YYYY-MM-DD format
                            year, month, day = part1, part2, part3
                        else:
                            # DD-MM-YYYY or DD/MM/YYYY format
                            day, month, year = part1, part2, part3
                        
                        # CRITICAL: Validate date parts - reject invalid dates
                        if not (1 <= day <= 31 and 1 <= month <= 12):
                            print(f"    🚫 Invalid date {date_str}: day={day}, month={month} (out of range)")
                            return date_str  # Return original if invalid
                        
                        # Validate year range - handle both Gregorian (1900-2100) and Hijri (1400-1600)
                        if (1900 <= year <= 2100 or 1400 <= year <= 1600):
                            normalized = f"{year:04d}-{month:02d}-{day:02d}"
                            if normalized != date_str:
                                print(f"    ✓ Normalized date format {date_str} to {normalized}")
                            return normalized
                        else:
                            print(f"    🚫 Invalid date {date_str}: year={year} (out of valid range)")
                            return date_str
                    except ValueError:
                        pass
            
            # If already in YYYY-MM-DD format, validate it
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                parts = date_str.split('-')
                try:
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                    if not (1 <= day <= 31 and 1 <= month <= 12):
                        print(f"    🚫 Invalid date {date_str}: day={day}, month={month} (out of range)")
                        return date_str
                    if not (1900 <= year <= 2100 or 1400 <= year <= 1600):
                        print(f"    🚫 Invalid date {date_str}: year={year} (out of valid range)")
                        return date_str
                except ValueError:
                    pass
                return date_str
            
            # Return original if we can't parse it
            return date_str
                
        except Exception as e:
            print(f"    ⚠️ Error normalizing date {date_str}: {str(e)}")
            return date_str
    
    def convert_hijri_to_gregorian(self, date_str: str) -> str:
        """
        Convert Hijri date to Gregorian date if the date is in Hijri format.
        Detects Hijri dates by checking if year is > 1400 (Hijri years are typically 1400-1500+)
        
        Args:
            date_str: Date string that might be in Hijri format (e.g., "19/07/1440" or "1440-07-19")
            
        Returns:
            Gregorian date string in format "YYYY-MM-DD" or original string if not Hijri or conversion fails
        """
        if not date_str or date_str.strip() == "" or date_str == "not identify":
            return date_str
        
        if not HIJRI_SUPPORT:
            return date_str
        
        try:
            date_str = str(date_str).strip()
            
            # Try to parse different date formats
            # Common formats: DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, YYYYMMDD, etc.
            date_parts = None
            
            # Handle YYYYMMDD format (8 digits, no separators)
            # Example: "14451206" (Hijri) or "20251119" (Gregorian)
            if date_str.isdigit() and len(date_str) == 8:
                try:
                    year = int(date_str[0:4])
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                    date_parts = (year, month, day)
                except ValueError:
                    pass
            
            # Try DD/MM/YYYY or DD-MM-YYYY format
            if date_parts is None and ('/' in date_str or '-' in date_str):
                separator = '/' if '/' in date_str else '-'
                parts = date_str.split(separator)
                if len(parts) == 3:
                    # Check if it's DD/MM/YYYY or YYYY/MM/DD
                    try:
                        part1 = int(parts[0])
                        part2 = int(parts[1])
                        part3 = int(parts[2])
                        
                        # If first part is > 31, it's likely YYYY/MM/DD
                        if part1 > 31:
                            year, month, day = part1, part2, part3
                        else:
                            # Assume DD/MM/YYYY
                            day, month, year = part1, part2, part3
                        
                        date_parts = (year, month, day)
                    except ValueError:
                        pass
            
            if date_parts is None:
                return date_str
            
            year, month, day = date_parts
            
            # Check if year is in Hijri range (typically 1400-1500+)
            # Hijri years are usually between 1400-1500, while Gregorian are 1900-2100+
            if 1400 <= year <= 1600:
                # This looks like a Hijri date, convert it
                try:
                    # Validate month and day ranges for Hijri calendar
                    # Hijri months have 29-30 days, validate accordingly
                    if not (1 <= month <= 12):
                        print(f"    ⚠️ Invalid Hijri month: {month} (must be 1-12)")
                        return date_str
                    if not (1 <= day <= 30):
                        print(f"    ⚠️ Invalid Hijri day: {day} (must be 1-30)")
                        return date_str
                    
                    try:
                        hijri = Hijri(year, month, day)
                        gregorian = hijri.to_gregorian()
                        gregorian_date = f"{gregorian.year:04d}-{gregorian.month:02d}-{gregorian.day:02d}"
                        print(f"    ✅ Converted Hijri date {year:04d}-{month:02d}-{day:02d} (Hijri) to Gregorian: {gregorian_date}")
                        
                        # CRITICAL: Validate that Hijri date is in the future (for license expiry)
                        # Check if the Hijri date is in the future relative to current Hijri date
                        from datetime import datetime as dt
                        if HIJRI_SUPPORT:
                            try:
                                current_gregorian = dt.now()
                                current_hijri = Gregorian(current_gregorian.year, current_gregorian.month, current_gregorian.day).to_hijri()
                                print(f"    🔍 DEBUG: Current date: Gregorian {current_gregorian.strftime('%Y-%m-%d')}, Hijri {current_hijri.year:04d}-{current_hijri.month:02d}-{current_hijri.day:02d}")
                                
                                # Compare Hijri dates - allow dates within past year to pass through for final validation
                                # Convert to Gregorian first to calculate days difference more accurately
                                try:
                                    hijri_date_obj = Hijri(year, month, day)
                                    gregorian_date_obj = hijri_date_obj.to_gregorian()
                                    hijri_as_gregorian = dt(gregorian_date_obj.year, gregorian_date_obj.month, gregorian_date_obj.day)
                                    days_diff_gregorian = (hijri_as_gregorian - current_gregorian).days
                                    
                                    # Only reject dates that are clearly invalid (> 1 year in the past)
                                    if days_diff_gregorian < -365:  # More than 1 year in the past
                                        print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} (Gregorian: {gregorian_date}) is more than 1 year in the past ({abs(days_diff_gregorian)} days ago)")
                                        print(f"    ⚠️ Converted Gregorian date {gregorian_date} is likely invalid for license expiry - setting to 'not identify'")
                                        return "not identify"
                                    elif days_diff_gregorian < 0:  # Within past year
                                        print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} (Gregorian: {gregorian_date}) is {abs(days_diff_gregorian)} days in the past - allowing for final validation")
                                    elif days_diff_gregorian >= 0:
                                        print(f"    ✓ Hijri date {year:04d}-{month:02d}-{day:02d} (Gregorian: {gregorian_date}) is in the future")
                                except:
                                    # Fallback to simple year comparison if conversion fails
                                    if year < current_hijri.year - 1:  # More than 1 year in the past
                                        print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} is more than 1 year in the past (current Hijri year: {current_hijri.year}, difference: {current_hijri.year - year} years)")
                                        print(f"    ⚠️ Converted Gregorian date {gregorian_date} is likely invalid for license expiry - setting to 'not identify'")
                                        return "not identify"
                                    elif year < current_hijri.year:
                                        print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} is in the past (current Hijri year: {current_hijri.year}) - allowing for final validation")
                                    elif year == current_hijri.year and month < current_hijri.month:
                                        print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} is in the past (current Hijri: {current_hijri.year}-{current_hijri.month:02d}) - allowing for final validation")
                                    elif year == current_hijri.year and month == current_hijri.month and day < current_hijri.day:
                                        days_diff = current_hijri.day - day
                                        print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} is {days_diff} day(s) in the past - allowing for final validation")
                                
                                # Check if Hijri date is unreasonably far in future (> 20 Hijri years = ~19 Gregorian years)
                                if year > current_hijri.year + 20:
                                    years_diff = year - current_hijri.year
                                    print(f"    ⚠️ Hijri date {year:04d}-{month:02d}-{day:02d} is {years_diff} Hijri years in future (current: {current_hijri.year})")
                                    print(f"    ⚠️ REASON: License expiry dates should not be more than ~20 Hijri years in the future")
                                    print(f"    ⚠️ Converted Gregorian date {gregorian_date} is too far in future - likely OCR error")
                                    return "not identify"
                            except Exception as hijri_validation_error:
                                print(f"    ⚠️ Error validating Hijri date: {str(hijri_validation_error)[:100]}")
                                # Continue with conversion if validation fails
                        
                        print(f"    ✓ Converted Hijri date {date_str} ({year:04d}-{month:02d}-{day:02d} H) to Gregorian: {gregorian_date}")
                        return gregorian_date
                    except ValueError as ve:
                        # Date might be invalid (e.g., day 30 in a 29-day month)
                        print(f"    ⚠️ Invalid Hijri date {date_str}: {str(ve)}")
                        # Try with day 29 if day was 30 and month might only have 29 days
                        if day == 30:
                            try:
                                hijri = Hijri(year, month, 29)
                                gregorian = hijri.to_gregorian()
                                gregorian_date = f"{gregorian.year:04d}-{gregorian.month:02d}-{gregorian.day:02d}"
                                
                                # Validate adjusted date - allow dates within past year for final validation
                                if HIJRI_SUPPORT:
                                    try:
                                        current_gregorian = dt.now()
                                        current_hijri = Gregorian(current_gregorian.year, current_gregorian.month, current_gregorian.day).to_hijri()
                                        # Convert to Gregorian to calculate days difference
                                        try:
                                            adjusted_hijri = Hijri(year, month, 29)
                                            adjusted_gregorian = adjusted_hijri.to_gregorian()
                                            adjusted_gregorian_dt = dt(adjusted_gregorian.year, adjusted_gregorian.month, adjusted_gregorian.day)
                                            days_diff = (adjusted_gregorian_dt - current_gregorian).days
                                            if days_diff < -365:  # More than 1 year in the past
                                                return "not identify"
                                        except:
                                            # Fallback to simple year comparison
                                            if year < current_hijri.year - 1:  # More than 1 year in the past
                                                return "not identify"
                                    except:
                                        pass
                                
                                print(f"    ✓ Converted Hijri date {date_str} (adjusted day to 29) to Gregorian: {gregorian_date}")
                                return gregorian_date
                            except:
                                pass
                        return "not identify"
                except Exception as e:
                    print(f"    ⚠️ Error converting Hijri date {date_str} (year={year}, month={month}, day={day}): {str(e)}")
                    # Try to return normalized format even if conversion fails
                    try:
                        normalized = self.normalize_date_format(date_str)
                        return normalized
                    except:
                        return date_str
            elif 1900 <= year <= 2100:
                # Year is in Gregorian range, normalize format but don't convert
                try:
                    normalized = self.normalize_date_format(date_str)
                    
                    # CRITICAL: Validate that Gregorian date is in the future (for license expiry)
                    # Check if the normalized date is in the future
                    from datetime import datetime as dt
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', normalized):
                        date_parts = normalized.split('-')
                        norm_year = int(date_parts[0])
                        norm_month = int(date_parts[1])
                        norm_day = int(date_parts[2])
                        
                        try:
                            # Create datetime object for the normalized date
                            normalized_date = dt(norm_year, norm_month, norm_day)
                            current_date = dt.now()
                            days_difference = (normalized_date - current_date).days
                            
                            # Only reject dates that are clearly invalid:
                            # 1. Dates more than 1 year in the past (likely OCR errors or old licenses)
                            # 2. Dates more than 50 years in the future (likely OCR errors)
                            # Allow dates within the past year to pass through for final validation
                            if days_difference < -365:  # More than 1 year in the past
                                print(f"    ⚠️ Gregorian date {normalized} is more than 1 year in the past (current: {current_date.strftime('%Y-%m-%d')}, {abs(days_difference)} days ago) - likely invalid")
                                return "not identify"
                            elif days_difference < 0:  # Within past year
                                print(f"    ⚠️ Gregorian date {normalized} is {abs(days_difference)} days in the past - allowing for final validation")
                            elif norm_year > current_date.year + 50:  # More than 50 years in future
                                print(f"    ⚠️ Gregorian date {normalized} is too far in future (current year: {current_date.year}) - likely OCR error")
                                return "not identify"
                            
                            if days_difference >= 0:
                                print(f"    ✓ Validated Gregorian date {normalized} is in the future")
                        except ValueError as ve:
                            print(f"    ⚠️ Invalid Gregorian date {normalized}: {str(ve)}")
                            return "not identify"
                    
                    return normalized
                except Exception as e:
                    print(f"    ⚠️ Error normalizing Gregorian date: {str(e)[:100]}")
                    return "not identify"
            else:
                # Year is not in expected range, return as is
                return date_str
                
        except Exception as e:
            print(f"    ⚠️ Error processing date {date_str}: {str(e)}")
            return date_str
    
    def xml_to_json(self, xml_string: str) -> Dict[str, Any]:
        """
        Convert XML to JSON format
        Handles namespace issues by removing prefixes
        """
        xml_clean = self.clean_data(xml_string)
        
        # Try parsing first
        try:
            root = ET.fromstring(xml_clean)
        except ET.ParseError as e:
            error_msg = str(e)
            # If namespace error, fix it immediately
            if 'unbound prefix' in error_msg or 'namespace' in error_msg.lower():
                # Remove all namespace prefixes
                xml_clean = re.sub(r'<s0:(\w+)', r'<\1', xml_clean)
                xml_clean = re.sub(r'</s0:(\w+)', r'</\1', xml_clean)
                xml_clean = re.sub(r'<xsi:(\w+)', r'<\1', xml_clean)
                xml_clean = re.sub(r'</xsi:(\w+)', r'</\1', xml_clean)
                xml_clean = re.sub(r'\ss0:(\w+)=', r' \1=', xml_clean)
                xml_clean = re.sub(r'\sxsi:(\w+)=', r' \1=', xml_clean)
                xml_clean = re.sub(r'\sxsi:nil="[^"]*"', '', xml_clean)
                
                # Try again
                try:
                    root = ET.fromstring(xml_clean)
                except ET.ParseError as e2:
                    # Remove invalid characters
                    xml_clean = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', xml_clean)
                    root = ET.fromstring(xml_clean)
            else:
                # Other parse errors - try removing invalid characters
                xml_clean = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', xml_clean)
                root = ET.fromstring(xml_clean)
        
        # Now parse (root is already set from above)
            
            def xml_to_dict(element):
                """Recursively convert XML to dictionary"""
                tag = element.tag
                # Remove namespace if present
                if '}' in tag:
                    tag = tag.split('}')[1]
                
                result = {}
                
                # Get text content
                text = element.text.strip() if element.text and element.text.strip() else None
                
                # Process children
                children = list(element)
                if children:
                    for child in children:
                        child_tag = child.tag
                        if '}' in child_tag:
                            child_tag = child_tag.split('}')[1]
                        child_data = xml_to_dict(child)
                        
                        if child_tag in result:
                            if not isinstance(result[child_tag], list):
                                result[child_tag] = [result[child_tag]]
                            result[child_tag].append(child_data)
                        else:
                            result[child_tag] = child_data
                    
                    if text:
                        result['_text'] = text
                else:
                    if text:
                        return text
                    elif element.attrib:
                        result = element.attrib.copy()
                        if text:
                            result['_text'] = text
                        return result if result else None
                    else:
                        return text if text else None
                
                # Add attributes
                if element.attrib:
                    result['_attributes'] = element.attrib
                
                return result if result else (text if text else None)
            
        json_data = xml_to_dict(root)
        return json_data
    
    def detect_and_convert(self, data: str) -> Dict[str, Any]:
        """
        Detect format (XML/JSON) and convert to JSON
        Returns standardized JSON format
        """
        data_clean = self.clean_data(data)
        
        # Detect format
        if data_clean.strip().startswith('<'):
            # XML format
            json_data = self.xml_to_json(data_clean)
            return json_data
        elif data_clean.strip().startswith('{'):
            # JSON format
            try:
                json_data = json.loads(data_clean)
                return json_data
            except json.JSONDecodeError as e:
                raise ValueError(f"Failed to parse JSON: {str(e)}")
        else:
            raise ValueError(f"Unknown format. Data should start with '<' (XML) or '{{' (JSON)")
    
    def extract_party_info(self, party_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract comprehensive party information from JSON data"""
        # Handle different possible structures
        party_id = party_data.get("ID", party_data.get("id", party_data.get("Id", "")))
        name = party_data.get("name", party_data.get("Name", ""))
        liability = party_data.get("Liability", party_data.get("liability", 0))
        gender_id = party_data.get("GenderID", party_data.get("genderID", 1))
        age = party_data.get("age", party_data.get("Age", ""))
        nationality = party_data.get("nationality", party_data.get("Nationality", ""))
        license_no = party_data.get("licenseNo", party_data.get("license_no", ""))
        phone = party_data.get("phoneNo", party_data.get("phone_no", ""))
        license_type_from_request = party_data.get("licenseType", party_data.get("license_type", ""))
        recovery = party_data.get("recovery", "")
        
        # Vehicle info
        car_make = party_data.get("carMake", party_data.get("car_make", ""))
        car_model = party_data.get("carModel", party_data.get("car_model", ""))
        car_year = party_data.get("carMfgYear", party_data.get("car_year", ""))
        plate_no = party_data.get("plateNo", party_data.get("plate_no", ""))
        chassis_no = party_data.get("chassisNo", party_data.get("chassis_no", ""))
        vehicle_owner_id = party_data.get("VehicleOwnerId", party_data.get("vehicleOwnerId", party_data.get("vehicle_owner_id", "")))
        
        # Extract insurance info (handle different structures)
        insurance_info = party_data.get("Insurance_Info", {})
        if not insurance_info:
            insurance_info = party_data.get("insurance_info", {})
        if not insurance_info:
            insurance_info = party_data.get("InsuranceInfo", {})
        
        policy_number = insurance_info.get("policyNumber", insurance_info.get("policy_number", ""))
        insurance_name_arabic = insurance_info.get("ICArabicName", insurance_info.get("ic_arabic_name", ""))
        
        # Try multiple possible field names for English name (ICEnglishName, EnglishNam, etc.)
        # First check in insurance_info, then check in party_data top level (for Excel/JSON structures)
        insurance_name_english = (
            insurance_info.get("ICEnglishName") or
            insurance_info.get("ic_english_name") or
            insurance_info.get("EnglishNam") or
            insurance_info.get("english_nam") or
            insurance_info.get("EnglishName") or
            insurance_info.get("english_name") or
            party_data.get("ICEnglishName") or  # Check top level
            party_data.get("ic_english_name") or
            party_data.get("EnglishNam") or
            party_data.get("english_nam") or
            party_data.get("EnglishName") or
            party_data.get("english_name") or
            ""
        )
        insurance_name = insurance_name_arabic if insurance_name_arabic else insurance_name_english
        policy_expiry = insurance_info.get("policyExpiryDate", insurance_info.get("policy_expiry", ""))
        vehicle_id = insurance_info.get("vehicleID", insurance_info.get("vehicle_id", ""))
        
        # Damage info
        damages = party_data.get("Damages", {})
        damage_type = ""
        if damages:
            damage_info = damages.get("Damage_Info", {})
            if isinstance(damage_info, list) and len(damage_info) > 0:
                damage_info = damage_info[0]
            if isinstance(damage_info, dict):
                damage_type = damage_info.get("damageType", damage_info.get("damage_type", ""))
        
        # Act/Violation
        acts = party_data.get("Acts", {})
        act_description = ""
        if acts:
            act_info = acts.get("Act_Info", {})
            if isinstance(act_info, list) and len(act_info) > 0:
                act_info = act_info[0]
            if isinstance(act_info, dict):
                act_description = act_info.get("actEnglish", act_info.get("act_english", ""))
                if not act_description:
                    act_description = act_info.get("actArabic", act_info.get("act_arabic", ""))
        
        return {
            "Party_ID": str(party_id),
            "Name": str(name),
            "Gender": "Female" if gender_id == 2 else "Male",
            "Age": str(age) if age else "",
            "Nationality": str(nationality),
            "License_No": str(license_no),
            "Phone": str(phone),
            "Liability": int(liability) if liability else 0,
            "Policy_Number": str(policy_number),
            "Insurance_Name": str(insurance_name),
            "ICEnglishName": str(insurance_name_english) if insurance_name_english else "",
            "Policy_Expiry": str(policy_expiry),
            "Vehicle_Make": str(car_make),
            "Vehicle_Model": str(car_model),
            "Vehicle_Year": str(car_year),
            "Plate_No": str(plate_no),
            "Chassis_No": str(chassis_no),
            "Vehicle_ID": str(vehicle_id),
            "VehicleOwnerId": str(vehicle_owner_id),
            "Damage_Type": str(damage_type),
            "Act_Violation": str(act_description),
            "License_Type_From_Request": str(license_type_from_request),
            "Recovery": str(recovery)
        }
    
    def extract_accident_info(self, accident_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract accident information"""
        # Ensure accident_data is a dict
        if not isinstance(accident_data, dict):
            accident_data = {}
        
        case_number = accident_data.get("caseNumber", accident_data.get("case_number", ""))
        surveyor = accident_data.get("surveyorName", accident_data.get("surveyor_name", ""))
        call_date = accident_data.get("callDate", accident_data.get("call_date", ""))
        call_time = accident_data.get("callTime", accident_data.get("call_time", ""))
        city = accident_data.get("city", accident_data.get("City", ""))
        location = accident_data.get("location", accident_data.get("Location", ""))
        coordinates = accident_data.get("LocationCoordinates", accident_data.get("location_coordinates", ""))
        landmark = accident_data.get("landmark", accident_data.get("Landmark", ""))
        description = accident_data.get("AccidentDescription", accident_data.get("accident_description", ""))
        
        return {
            "Case_Number": str(case_number),
            "Surveyor": str(surveyor),
            "Call_Date": str(call_date),
            "Call_Time": str(call_time),
            "City": str(city),
            "Location": str(location),
            "Coordinates": str(coordinates),
            "Landmark": str(landmark),
            "Description": str(description)
        }
    
    def find_request_column(self, df: pd.DataFrame, possible_names: List[str] = None) -> Optional[str]:
        """
        Find the request column automatically
        Tries common names and patterns
        """
        if possible_names is None:
            possible_names = ['request', 'Request', 'REQUEST', 'data', 'Data', 'xml', 'XML', 'json', 'JSON', 'claim', 'Claim']
        
        # Clean column names
        df.columns = df.columns.str.strip()
        
        # Try exact matches first
        for name in possible_names:
            if name in df.columns:
                return name
        
        # Try case-insensitive
        for col in df.columns:
            if col.strip().lower() in [n.lower() for n in possible_names]:
                return col
        
        # Try pattern matching (contains 'request', 'data', 'xml', 'json')
        for col in df.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in ['request', 'data', 'xml', 'json', 'claim']):
                return col
        
        return None
    
    def _process_single_row(self, row_num: int, claim_data: Any, request_column: str, 
                           total_rows: int, idx: int, base64_files_path: str = None) -> List[Dict[str, Any]]:
        """
        Process a single row from Excel
        Returns a list of results (one per party)
        """
        results = []
        print(f"[{idx + 1}/{total_rows}] Processing row {row_num + 1}...")
        
        try:
            if pd.isna(claim_data) or str(claim_data).strip() == "":
                print(f"  ⚠ Skipped - Empty row")
                return results
            
            # Convert to JSON (handles both XML and JSON)
            # Also extract DAA values from request data
            daa_from_request = {
                'isDAA': None,
                'Suspect_as_Fraud': None,
                'DaaReasonEnglish': None
            }
            
            try:
                json_data = self.detect_and_convert(str(claim_data))
                print(f"  ✓ Converted to JSON successfully (Row {row_num + 1})")
                
                # Extract DAA values from JSON/XML data in Request column
                # Try multiple possible locations in the JSON structure
                accident_info_raw = None
                if isinstance(json_data, dict):
                    # Try EICWS structure
                    if "EICWS" in json_data:
                        case_info = json_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
                        accident_info_raw = case_info.get("Accident_info", {})
                    # Try cases structure
                    elif "cases" in json_data:
                        case_info = json_data.get("cases", {}).get("Case_Info", {})
                        accident_info_raw = case_info.get("Accident_info", {})
                    # Try Case_Info structure
                    elif "Case_Info" in json_data:
                        accident_info_raw = json_data.get("Case_Info", {}).get("Accident_info", {})
                    # Try direct accident_info
                    elif "Accident_info" in json_data:
                        accident_info_raw = json_data.get("Accident_info", {})
                    # Try at root level
                    if not accident_info_raw:
                        accident_info_raw = json_data
                
                # Extract DAA values from accident_info
                if accident_info_raw:
                    # Try various field name variations
                    isDAA_value = (
                        accident_info_raw.get("isDAA") or
                        accident_info_raw.get("is_daa") or
                        accident_info_raw.get("IsDAA") or
                        None
                    )
                    if isDAA_value is not None:
                        daa_from_request['isDAA'] = str(isDAA_value).strip() if pd.notna(isDAA_value) else None
                    
                    suspect_fraud_value = (
                        accident_info_raw.get("Suspect_as_Fraud") or
                        accident_info_raw.get("suspect_as_fraud") or
                        accident_info_raw.get("SuspectAsFraud") or
                        None
                    )
                    if suspect_fraud_value is not None:
                        daa_from_request['Suspect_as_Fraud'] = str(suspect_fraud_value).strip() if pd.notna(suspect_fraud_value) else None
                    
                    daa_reason_value = (
                        accident_info_raw.get("DaaReasonEnglish") or
                        accident_info_raw.get("daa_reason_english") or
                        accident_info_raw.get("DaaReason") or
                        accident_info_raw.get("daaReasonEnglish") or
                        None
                    )
                    if daa_reason_value is not None:
                        daa_from_request['DaaReasonEnglish'] = str(daa_reason_value).strip() if pd.notna(daa_reason_value) else None
                    
                    if any(daa_from_request.values()):
                        print(f"  ✓ Extracted DAA from Request: isDAA={daa_from_request['isDAA']}, Suspect_as_Fraud={daa_from_request['Suspect_as_Fraud']}, DaaReasonEnglish={daa_from_request['DaaReasonEnglish']}")
            except Exception as e:
                error_msg = str(e)
                print(f"  ✗ Conversion error (Row {row_num + 1}): {error_msg[:200]}")
                results.append({
                    "Case_Number": f"ERROR_ROW_{row_num + 1}",
                    "Party": 0,
                    "Party_ID": "",
                    "Party_Name": "",
                    "Insurance_Name": "",
                    "ICEnglishName": "",
                    "Liability": 0,
                    "Vehicle_Serial": "",
                    "VehicleOwnerId": "",
                    "License_Type_From_Request": "",
                    "Recovery": "",
                    "License_Expiry_Date": "not identify",
                    "Upload_Date": "not identify",
                    "License_Expiry_Last_Updated": "",
                    "Accident_Date": "",
                    "carMake": "",
                    "carModel": "",
                    "License_Type_From_Make_Model": "",
                    "Full_Analysis": "",
                    "Full_Analysis_English": "",
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Description": f"Conversion error: {error_msg[:200]}"
                })
                return results
            
            # Add License_Type_From_Make_Model to ALL parties before sending to Ollama
            # Extract parties from JSON and add License_Type_From_Make_Model to each
            if isinstance(json_data, dict):
                # Find parties in different possible locations
                parties_to_update = []
                
                # Check EICWS structure
                if "EICWS" in json_data:
                    case_info = json_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
                    if case_info:
                        parties_raw = case_info.get("parties", {})
                        if isinstance(parties_raw, dict):
                            party_info_list = parties_raw.get("Party_Info", [])
                            if isinstance(party_info_list, list):
                                parties_to_update = party_info_list
                            elif isinstance(party_info_list, dict):
                                parties_to_update = [party_info_list]
                        elif isinstance(parties_raw, list):
                            parties_to_update = parties_raw
                
                # Check cases structure
                if not parties_to_update and "cases" in json_data:
                    case_info = json_data.get("cases", {}).get("Case_Info", {})
                    if case_info:
                        parties_raw = case_info.get("parties", {})
                        if isinstance(parties_raw, dict):
                            party_info_list = parties_raw.get("Party_Info", [])
                            if isinstance(party_info_list, list):
                                parties_to_update = party_info_list
                            elif isinstance(party_info_list, dict):
                                parties_to_update = [party_info_list]
                        elif isinstance(parties_raw, list):
                            parties_to_update = parties_raw
                
                # Check Case_Info structure
                if not parties_to_update and "Case_Info" in json_data:
                    case_info = json_data.get("Case_Info", {})
                    parties_raw = case_info.get("parties", {})
                    if isinstance(parties_raw, dict):
                        party_info_list = parties_raw.get("Party_Info", [])
                        if isinstance(party_info_list, list):
                            parties_to_update = party_info_list
                        elif isinstance(party_info_list, dict):
                            parties_to_update = [party_info_list]
                    elif isinstance(parties_raw, list):
                        parties_to_update = parties_raw
                
                # Check direct Parties array
                if not parties_to_update and "Parties" in json_data:
                    parties_to_update = json_data.get("Parties", [])
                
                # Add License_Type_From_Make_Model to ALL parties before sending to Ollama
                # Extract parties from JSON and add License_Type_From_Make_Model to each
                if isinstance(json_data, dict):
                    # Find parties in different possible locations
                    parties_to_update = []
                    
                    # Check EICWS structure
                    if "EICWS" in json_data:
                        case_info = json_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
                        if case_info:
                            parties_raw = case_info.get("parties", {})
                            if isinstance(parties_raw, dict):
                                party_info_list = parties_raw.get("Party_Info", [])
                                if isinstance(party_info_list, list):
                                    parties_to_update = party_info_list
                                elif isinstance(party_info_list, dict):
                                    parties_to_update = [party_info_list]
                            elif isinstance(parties_raw, list):
                                parties_to_update = parties_raw
                    
                    # Check cases structure
                    if not parties_to_update and "cases" in json_data:
                        case_info = json_data.get("cases", {}).get("Case_Info", {})
                        if case_info:
                            parties_raw = case_info.get("parties", {})
                            if isinstance(parties_raw, dict):
                                party_info_list = parties_raw.get("Party_Info", [])
                                if isinstance(party_info_list, list):
                                    parties_to_update = party_info_list
                                elif isinstance(party_info_list, dict):
                                    parties_to_update = [party_info_list]
                            elif isinstance(parties_raw, list):
                                parties_to_update = parties_raw
                    
                    # Check Case_Info structure
                    if not parties_to_update and "Case_Info" in json_data:
                        case_info = json_data.get("Case_Info", {})
                        parties_raw = case_info.get("parties", {})
                        if isinstance(parties_raw, dict):
                            party_info_list = parties_raw.get("Party_Info", [])
                            if isinstance(party_info_list, list):
                                parties_to_update = party_info_list
                            elif isinstance(party_info_list, dict):
                                parties_to_update = [party_info_list]
                        elif isinstance(parties_raw, list):
                            parties_to_update = parties_raw
                    
                    # Check direct Parties array
                    if not parties_to_update and "Parties" in json_data:
                        parties_to_update = json_data.get("Parties", [])
                    
                    # Update each party with License_Type_From_Make_Model
                    if parties_to_update:
                        print(f"  🔍 Adding License_Type_From_Make_Model to {len(parties_to_update)} party(ies)...")
                        for party in parties_to_update:
                            if isinstance(party, dict):
                                # Extract carMake and carModel
                                car_make = party.get("carMake", party.get("car_make", party.get("Vehicle_Make", "")))
                                car_model = party.get("carModel", party.get("car_model", party.get("Vehicle_Model", "")))
                                
                                # Lookup License type from Make/Model mapping
                                if car_make and car_model:
                                    license_type_from_mapping = self.lookup_license_type_from_make_model(car_make, car_model)
                                    if license_type_from_mapping:
                                        party["License_Type_From_Make_Model"] = license_type_from_mapping
                                        print(f"    ✅ Added License_Type_From_Make_Model = {license_type_from_mapping} (Make: {car_make}, Model: {car_model})")
                                    else:
                                        party["License_Type_From_Make_Model"] = ""
                                        print(f"    ⚠️ No License_Type_From_Make_Model found (Make: {car_make}, Model: {car_model})")
                                else:
                                    party["License_Type_From_Make_Model"] = ""
            
            # VALIDATION: Ensure JSON data is valid before processing
            if not isinstance(json_data, dict):
                raise ValueError(f"Invalid JSON structure: expected dict, got {type(json_data).__name__}")
            
            # VALIDATION: Check for required fields to ensure data accuracy
            has_valid_structure = False
            if "EICWS" in json_data or "cases" in json_data or "Case_Info" in json_data or "Parties" in json_data:
                has_valid_structure = True
            
            if not has_valid_structure:
                print(f"  ⚠️ Warning: JSON structure may be incomplete (Row {row_num + 1})")
            
            # Convert JSON back to string for processing
            claim_json_str = json.dumps(json_data, ensure_ascii=False)
            
            # VALIDATION: Ensure claim data is not empty
            if not claim_json_str or len(claim_json_str.strip()) < 50:
                raise ValueError("Claim data is too short or empty")
            
            # Process claim with better error handling and validation
            try:
                result = self.processor.process_claim(claim_json_str, input_format="json", process_parties_separately=True)
                
                # VALIDATION: Ensure result is valid
                if not isinstance(result, dict):
                    raise ValueError(f"Invalid result type: expected dict, got {type(result).__name__}")
                
                # VALIDATION: Ensure parties exist in result
                if "parties" not in result or not isinstance(result.get("parties"), list):
                    print(f"  ⚠️ Warning: No parties found in result (Row {row_num + 1})")
                    result["parties"] = []
                
                print(f"  ✓ Processed by Ollama model (Row {row_num + 1}) - {len(result.get('parties', []))} party(ies)")
            except ConnectionError as e:
                error_msg = str(e)
                print(f"  ✗ Connection error (Row {row_num + 1}): {error_msg[:300]}")
                # Provide helpful suggestions for timeout errors
                if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                    print(f"  💡 Tip: Ollama is taking too long. Try:")
                    print(f"     - Using a faster model (llama3.1:latest instead of qwen2.5:14b)")
                    print(f"     - Reducing claim complexity")
                    print(f"     - Increasing system RAM/CPU resources")
                    print(f"     - Processing fewer parties at once")
                elif "connect" in error_msg.lower() or "connection" in error_msg.lower():
                    print(f"  💡 Tip: Make sure Ollama is running: ollama serve")
                
                results.append({
                    "Case_Number": f"ERROR_ROW_{row_num + 1}",
                    "Party": 0,
                    "Party_ID": "",
                    "Party_Name": "",
                    "Insurance_Name": "",
                    "ICEnglishName": "",
                    "Liability": 0,
                    "Vehicle_Serial": "",
                    "VehicleOwnerId": "",
                    "License_Type_From_Request": "",
                    "Recovery": "",
                    "License_Expiry_Date": "not identify",
                    "Upload_Date": "not identify",
                    "License_Expiry_Last_Updated": "",
                    "Accident_Date": "",
                    "carMake": "",
                    "carModel": "",
                    "License_Type_From_Make_Model": "",
                    "Full_Analysis": "",
                    "Full_Analysis_English": "",
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Description": f"Connection error: {error_msg[:200]}"
                })
                return results
            except Exception as e:
                error_msg = str(e)
                print(f"  ✗ Processing error (Row {row_num + 1}): {error_msg[:200]}")
                results.append({
                    "Case_Number": f"ERROR_ROW_{row_num + 1}",
                    "Party": 0,
                    "Party_ID": "",
                    "Party_Name": "",
                    "Insurance_Name": "",
                    "ICEnglishName": "",
                    "Liability": 0,
                    "Vehicle_Serial": "",
                    "VehicleOwnerId": "",
                    "License_Type_From_Request": "",
                    "Recovery": "",
                    "License_Expiry_Date": "not identify",
                    "Upload_Date": "not identify",
                    "License_Expiry_Last_Updated": "",
                    "Accident_Date": "",
                    "carMake": "",
                    "carModel": "",
                    "License_Type_From_Make_Model": "",
                    "Full_Analysis": "",
                    "Full_Analysis_English": "",
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Description": f"Processing error: {error_msg[:200]}"
                })
                return results
            
            case_number = result.get("case_number", f"Case_{row_num + 1}")
            parties = result.get("parties", [])
            
            # Also try to extract case number from JSON data if not found in result
            if not case_number or case_number.startswith("Case_"):
                # Try to extract from JSON data
                if isinstance(json_data, dict):
                    case_number_fields = ['Case_Number', 'case_number', 'CaseNumber', 'caseNumber', 
                                        'CaseNo', 'caseNo', 'Case_No', 'case_no', 'ClaimNumber', 'claimNumber']
                    for field in case_number_fields:
                        if field in json_data:
                            case_number = str(json_data[field]).strip()
                            break
                    
                    # Also check in accident details
                    if (not case_number or case_number.startswith("Case_")) and 'accident_details' in json_data:
                        accident_details = json_data.get('accident_details', {})
                        for field in case_number_fields:
                            if field in accident_details:
                                case_number = str(accident_details[field]).strip()
                                break
            
            print(f"  ✓ Case: {case_number} - {len(parties)} parties (Row {row_num + 1})")
            
            # Extract case info from JSON
            case_info = None
            accident_info = {}
            if "EICWS" in json_data:
                case_info = json_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
                accident_info = case_info.get("Accident_info", {}) if case_info else {}
            if not case_info and "cases" in json_data:
                case_info = json_data.get("cases", {}).get("Case_Info", {})
                accident_info = case_info.get("Accident_info", {}) if case_info else {}
            if not case_info and "Case_Info" in json_data:
                case_info = json_data.get("Case_Info", {})
                accident_info = case_info.get("Accident_info", {}) if case_info else {}
            
            # Extract accident information
            accident_details = self.extract_accident_info(accident_info)
            # Ensure accident_details is a dict (not a string)
            if not isinstance(accident_details, dict):
                accident_details = {}
            accident_description = accident_details.get("Description", "") if isinstance(accident_details, dict) else ""
            
            # Get parties data
            parties_data = {}
            if case_info:
                parties_raw = case_info.get("parties", {})
                if isinstance(parties_raw, dict):
                    party_info_list = parties_raw.get("Party_Info", [])
                    if isinstance(party_info_list, dict):
                        parties_data = {0: party_info_list}
                    elif isinstance(party_info_list, list):
                        parties_data = {i: p for i, p in enumerate(party_info_list)}
                elif isinstance(parties_raw, list):
                    parties_data = {i: p for i, p in enumerate(parties_raw)}
            
            # Extract all Party IDs first (before processing individual parties)
            # This allows us to match all parties to dates at once
            all_party_ids = []
            for party_idx, party_decision in enumerate(parties):
                party_raw_data = parties_data.get(party_idx, {})
                if not party_raw_data and "party_info" in party_decision:
                    party_raw_data = party_decision.get("party_info", {})
                party_info_temp = self.extract_party_info(party_raw_data)
                party_id_temp = party_info_temp.get("Party_ID", "")
                if not party_id_temp and "party_id" in party_decision:
                    party_id_temp = str(party_decision.get("party_id", ""))
                # Clean Party_ID - remove Arabic characters, keep only digits
                if party_id_temp:
                    party_id_clean = re.sub(r'[^\d]', '', str(party_id_temp))
                    if party_id_clean:
                        all_party_ids.append(party_id_clean)
                    else:
                        all_party_ids.append(str(party_id_temp).strip())
            
            # Pre-extract dates for all parties if base64 file exists
            # This allows us to match all parties to dates at once, ensuring each gets a unique date
            party_date_matches = {}  # Cache: party_id -> date (raw dates from OCR, before conversion)
            used_dates_for_case = set()  # Track dates already assigned in this case to prevent reuse
            case_ocr_text = None  # Store OCR text (translated) for later use in license type/upload date extraction
            case_date_positions = []  # Store date positions for later use in order-based assignment
            # Store case number for base64 file access in parallel processing - always initialize
            case_number_for_base64 = str(case_number).strip() if case_number else None
            if base64_files_path and case_number:
                case_clean = str(case_number).strip()
                case_number_for_base64 = case_clean  # Store for use in parallel processing
                base64_file_path = os.path.join(base64_files_path, f"{case_clean}.txt")
                
                # Try to find base64 file (with alternative paths)
                alternative_paths = [base64_file_path]
                if not os.path.exists(base64_file_path):
                    # Try alternative formats (same logic as before)
                    if len(case_clean) > 2 and case_clean[:2].isalpha():
                        alternative_paths.append(os.path.join(base64_files_path, f"{case_clean[2:]}.txt"))
                    if len(case_clean) > 10:
                        alternative_paths.append(os.path.join(base64_files_path, f"{case_clean[-10:]}.txt"))
                    for variant in [case_clean.replace('-', ''), case_clean.replace('_', ''), case_clean.replace(' ', '')]:
                        if variant != case_clean:
                            alternative_paths.append(os.path.join(base64_files_path, f"{variant}.txt"))
                
                base64_file_path = None
                for path in alternative_paths:
                    if os.path.exists(path):
                        base64_file_path = path
                        break
                
                if base64_file_path and os.path.exists(base64_file_path):
                    try:
                        print(f"  📁 Pre-extracting dates for all parties from: {base64_file_path}")
                        with open(base64_file_path, 'r', encoding='utf-8') as f:
                            file_content = f.read().strip()
                        
                        if file_content:
                            # Parse base64 images
                            base64_images = []
                            if '\n\n' in file_content:
                                base64_images = [img.strip() for img in file_content.split('\n\n') if img.strip()]
                            elif '\n' in file_content:
                                lines = file_content.split('\n')
                                if len(lines) > 1 and all(len(line) > 100 for line in lines):
                                    base64_images = [line.strip() for line in lines if line.strip()]
                                else:
                                    base64_images = [file_content]
                            else:
                                base64_images = [file_content]
                            
                            # Try to extract all Party IDs and dates from ALL images
                            # (typically all parties are in the same image/document, but check all to be safe)
                            if base64_images and len(base64_images) > 0:
                                try:
                                    from PIL import Image
                                    from io import BytesIO
                                    import base64
                                    
                                    # Combine OCR text from all images to ensure we get all parties and dates
                                    all_ocr_text = ""
                                    for img_idx, base64_img in enumerate(base64_images):
                                        try:
                                            img_bytes = base64.b64decode(base64_img.split(',')[-1] if ',' in base64_img else base64_img)
                                            image = Image.open(BytesIO(img_bytes))
                                            if image.mode != 'RGB':
                                                image = image.convert('RGB')
                                            
                                            # Perform OCR
                                            ocr_text = ""
                                            for psm_mode in ['6', '4', '11', '12', '3']:
                                                try:
                                                    config = f'--psm {psm_mode} --oem 3'
                                                    ocr_text = pytesseract.image_to_string(image, lang='ara+eng', config=config)
                                                    if len(ocr_text.strip()) > 20:
                                                        break
                                                except:
                                                    continue
                                            
                                            if len(ocr_text.strip()) > 20:
                                                all_ocr_text += "\n\n" + ocr_text  # Combine with separator
                                                print(f"  📄 Processed image {img_idx + 1}/{len(base64_images)} ({len(ocr_text)} chars)")
                                        except Exception as e:
                                            print(f"  ⚠️ Error processing image {img_idx + 1}: {str(e)[:100]}")
                                            continue
                                    
                                    if len(all_ocr_text.strip()) > 20:
                                        # Clean OCR text
                                        ocr_text_clean = all_ocr_text
                                        invisible_chars = ['\u200E', '\u200F', '\u200B', '\u200C', '\u200D', '\uFEFF', '\u2060']
                                        for char in invisible_chars:
                                            ocr_text_clean = ocr_text_clean.replace(char, '')
                                        ocr_text_normalized = ' '.join(ocr_text_clean.split())
                                        
                                        # Translate to English for better extraction (optional but recommended)
                                        # OPTIMIZATION: Skip translation during high-performance mode to avoid blocking
                                        enable_translation_flag = getattr(self, '_enable_translation', False)
                                        if enable_translation_flag:
                                            print(f"  🌐 Translating OCR text to English for better extraction...")
                                            try:
                                                ocr_text_translated = self.translate_ocr_to_english(ocr_text_normalized)
                                                # Use translated text if available, otherwise use original
                                                ocr_text_for_extraction = ocr_text_translated if ocr_text_translated != ocr_text_normalized else ocr_text_normalized
                                            except Exception as e:
                                                print(f"  ⚠️ OCR translation skipped (error/timeout): {str(e)[:100]}, using original text")
                                                ocr_text_for_extraction = ocr_text_normalized
                                        else:
                                            print(f"  ⚡ Skipping OCR translation (disabled for speed) - using original text")
                                            ocr_text_for_extraction = ocr_text_normalized
                                        # Store for later use in license type/upload date extraction
                                        case_ocr_text = ocr_text_for_extraction
                                        
                                        print(f"  📄 Combined OCR text length: {len(ocr_text_normalized)} characters")
                                        
                                        # Clean Party IDs - remove Arabic characters before matching
                                        all_party_ids_clean = []
                                        for pid in all_party_ids:
                                            pid_clean = re.sub(r'[^\d]', '', str(pid))
                                            if pid_clean:
                                                all_party_ids_clean.append(pid_clean)
                                            else:
                                                all_party_ids_clean.append(str(pid).strip())
                                        
                                        # Extract all Party IDs and dates from combined text (use translated if available)
                                        party_positions = self.extract_party_ids_with_positions(ocr_text_for_extraction)
                                        exclude_keywords = ['إصدار', 'اصدار', 'تاريخ الإصدار', 'Version Date', 'Upload Date', 'تاريخ إضافة']
                                        date_positions = self.extract_all_expiry_dates_with_positions(ocr_text_for_extraction, exclude_keywords)
                                        case_date_positions = date_positions  # Store for later use
                                        
                                        print(f"  📊 Pre-extraction results:")
                                        print(f"     Looking for Party IDs (cleaned): {all_party_ids_clean}")
                                        print(f"     Found {len(party_positions)} Party ID(s) in OCR: {[pid for pid, _, _ in party_positions]}")
                                        print(f"     Found {len(date_positions)} expiry date(s) in OCR: {[date for date, _, _ in date_positions]}")
                                        
                                        # DEBUG: Show OCR text sample for investigation
                                        ocr_sample_length = min(1000, len(ocr_text_for_extraction))
                                        ocr_sample = ocr_text_for_extraction[:ocr_sample_length]
                                        print(f"  🔍 DEBUG: OCR text sample (first {ocr_sample_length} chars):")
                                        print(f"     {ocr_sample}")
                                        if len(ocr_text_for_extraction) > ocr_sample_length:
                                            print(f"     ... (truncated, total length: {len(ocr_text_for_extraction)} chars)")
                                        
                                        # DEBUG: Show detailed positions
                                        if party_positions:
                                            print(f"     Party ID positions:")
                                            for pid, start, end in party_positions:
                                                # Show context around Party ID
                                                context_start = max(0, start - 100)
                                                context_end = min(len(ocr_text_for_extraction), end + 100)
                                                context = ocr_text_for_extraction[context_start:context_end]
                                                print(f"       - {pid} at position {start}-{end}")
                                                print(f"         Context: '{context}'")
                                        if date_positions:
                                            print(f"     Date positions:")
                                            for date, start, end in date_positions:
                                                # Show context around date
                                                context_start = max(0, start - 150)
                                                context_end = min(len(ocr_text_for_extraction), end + 150)
                                                context = ocr_text_for_extraction[context_start:context_end]
                                                print(f"       - {date} at position {start}-{end}")
                                                print(f"         Context: '{context}'")
                                        
                                        # CRITICAL: Verify we have enough dates for all parties
                                        if len(date_positions) < len(all_party_ids):
                                            print(f"  ⚠️ WARNING: Only {len(date_positions)} date(s) found for {len(all_party_ids)} party(ies)!")
                                            print(f"  ⚠️ This may cause multiple parties to get the same date.")
                                            print(f"  ⚠️ Please check OCR extraction - all expiry dates should be extracted.")
                                            print(f"  ⚠️ DEBUG: This is the ROOT CAUSE of duplicate dates!")
                                            # Show what dates we DID find vs what we need
                                            print(f"  🔍 DEBUG: Need {len(all_party_ids)} dates, found {len(date_positions)}:")
                                            for idx, (date, start, end) in enumerate(date_positions, 1):
                                                print(f"     {idx}. {date} (position {start}-{end})")
                                            # Search for expiry keywords in OCR to see if patterns might be wrong
                                            expiry_keywords = ['تاريخ إنتهاء', 'تاريخ انتهاء', 'Expiry Date', 'License Expiry']
                                            found_keywords = []
                                            for kw in expiry_keywords:
                                                if kw in ocr_text_for_extraction:
                                                    count = ocr_text_for_extraction.count(kw)
                                                    found_keywords.append(f"{kw} (x{count})")
                                            if found_keywords:
                                                print(f"  🔍 DEBUG: Found expiry keywords in OCR: {', '.join(found_keywords)}")
                                            else:
                                                print(f"  🔍 DEBUG: NO expiry keywords found in OCR! This might be why no dates were extracted.")
                                                print(f"  🔍 DEBUG: Searching for any date-like patterns...")
                                                # Try to find ANY dates in OCR
                                                all_date_pattern = r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
                                                all_dates_found = re.findall(all_date_pattern, ocr_text_for_extraction)
                                                if all_dates_found:
                                                    print(f"  🔍 DEBUG: Found {len(all_dates_found)} date-like patterns in OCR (may include wrong dates): {all_dates_found[:10]}...")
                                                else:
                                                    print(f"  🔍 DEBUG: NO date-like patterns found in OCR at all!")
                                        
                                        # Match all parties to dates at once (use translated text for row-based matching)
                                        # Use cleaned Party IDs for matching
                                        if party_positions and date_positions and all_party_ids_clean:
                                            party_date_matches = self.match_all_parties_to_dates(all_party_ids_clean, party_positions, date_positions, ocr_text_for_extraction)
                                            print(f"  ✅ Pre-matched {len(party_date_matches)} party(ies) to dates")
                                            print(f"  📋 Pre-matched results: {party_date_matches}")
                                            
                                            # CRITICAL: Verify each party got a unique date
                                            unique_dates = set(party_date_matches.values())
                                            if len(unique_dates) < len(party_date_matches):
                                                print(f"  ⚠️ WARNING: {len(party_date_matches)} parties matched but only {len(unique_dates)} unique dates!")
                                                print(f"  ⚠️ Some parties are sharing the same date:")
                                                date_to_parties = {}
                                                for pid, date in party_date_matches.items():
                                                    if date not in date_to_parties:
                                                        date_to_parties[date] = []
                                                    date_to_parties[date].append(pid)
                                                for date, parties in date_to_parties.items():
                                                    if len(parties) > 1:
                                                        print(f"     ⚠️ Date {date} assigned to parties: {parties}")
                                                        print(f"     ⚠️ THIS IS THE PROBLEM - Multiple parties getting same date!")
                                            else:
                                                print(f"  ✅ All parties have unique dates in pre-matching!")
                                        elif not date_positions:
                                            print(f"  ⚠️ WARNING: No expiry dates extracted from image!")
                                            print(f"  ⚠️ This will cause all parties to get 'not identify'")
                                            print(f"  ⚠️ DEBUG: Check OCR extraction patterns - dates might not be found")
                                            # Show OCR sample around expiry keywords
                                            expiry_keywords = ['تاريخ إنتهاء', 'تاريخ انتهاء', 'Expiry Date', 'License Expiry']
                                            for kw in expiry_keywords:
                                                if kw in ocr_text_for_extraction:
                                                    idx = ocr_text_for_extraction.find(kw)
                                                    context_start = max(0, idx - 200)
                                                    context_end = min(len(ocr_text_for_extraction), idx + len(kw) + 200)
                                                    context = ocr_text_for_extraction[context_start:context_end]
                                                    print(f"  🔍 DEBUG: Found '{kw}' at position {idx}, context:")
                                                    print(f"     '{context}'")
                                                    # Try to find dates near this keyword
                                                    near_text = ocr_text_for_extraction[max(0, idx - 300):min(len(ocr_text_for_extraction), idx + len(kw) + 300)]
                                                    date_pattern = r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
                                                    nearby_dates = re.findall(date_pattern, near_text)
                                                    if nearby_dates:
                                                        print(f"  🔍 DEBUG: Found {len(nearby_dates)} date(s) near '{kw}': {nearby_dates}")
                                                    else:
                                                        print(f"  🔍 DEBUG: NO dates found near '{kw}' - might be OCR issue or wrong format")
                                        elif not party_positions:
                                            print(f"  ⚠️ WARNING: No Party IDs extracted from image!")
                                            print(f"  ⚠️ Cannot match dates to parties")
                                            print(f"  ⚠️ DEBUG: Party IDs from Excel/JSON: {all_party_ids}")
                                            print(f"  ⚠️ DEBUG: Check if Party IDs in OCR match these IDs")
                                except Exception as e:
                                    print(f"  ⚠️ Error in pre-extraction: {str(e)[:100]}")
                                    import traceback
                                    print(f"  ⚠️ Traceback: {traceback.format_exc()[:200]}")
                    except Exception as e:
                        print(f"  ⚠️ Error reading base64 file for pre-extraction: {str(e)[:100]}")
            
            # Pre-extract License_Type_From_Make_Model for ALL parties before processing
            # This ensures all parties have this field when sent to Ollama
            print(f"  🔍 Pre-extracting License_Type_From_Make_Model for all parties...")
            for party_idx, party_decision in enumerate(parties):
                party_raw_data = parties_data.get(party_idx, {})
                if not party_raw_data and "party_info" in party_decision:
                    party_raw_data = party_decision.get("party_info", {})
                
                # Extract carMake and carModel for this party
                car_make = party_raw_data.get("carMake", party_raw_data.get("car_make", "")) if party_raw_data else ""
                car_model = party_raw_data.get("carModel", party_raw_data.get("car_model", "")) if party_raw_data else ""
                
                # Fallback to party_info if not found in raw data
                if not car_make or not car_model:
                    party_info_temp = self.extract_party_info(party_raw_data)
                    if not car_make:
                        car_make = party_info_temp.get("Vehicle_Make", "")
                    if not car_model:
                        car_model = party_info_temp.get("Vehicle_Model", "")
                
                # Lookup License type from Make/Model mapping
                if car_make and car_model:
                    license_type_from_mapping = self.lookup_license_type_from_make_model(car_make, car_model)
                    if license_type_from_mapping:
                        # Add to party_raw_data so it's available when processing
                        if not party_raw_data:
                            party_raw_data = {}
                        party_raw_data["License_Type_From_Make_Model"] = license_type_from_mapping
                        parties_data[party_idx] = party_raw_data
                        print(f"  ✅ Party {party_idx + 1}: License_Type_From_Make_Model = {license_type_from_mapping} (Make: {car_make}, Model: {car_model})")
                    else:
                        print(f"  ⚠️ Party {party_idx + 1}: No License_Type_From_Make_Model found (Make: {car_make}, Model: {car_model})")
            
            # OPTIMIZATION: Process parties in parallel for same accident
            # Extract party processing logic to enable parallelization
            def process_single_party_optimized(party_idx, party_decision, party_raw_data, parties, parties_data, 
                                               accident_details, accident_description, case_number, party_date_matches,
                                               used_dates_for_case, case_date_positions, case_ocr_text, case_number_for_base64,
                                               base64_files_path):
                """Process a single party - extracted for parallel processing"""
                try:
                    if not party_raw_data and "party_info" in party_decision:
                        party_raw_data = party_decision.get("party_info", {})
                    
                    party_info = self.extract_party_info(party_raw_data)
                    
                    # Ensure License_Type_From_Make_Model is in party_info
                    if "License_Type_From_Make_Model" not in party_info and "License_Type_From_Make_Model" in party_raw_data:
                        party_info["License_Type_From_Make_Model"] = party_raw_data["License_Type_From_Make_Model"]
                    
                    # Fallback from decision
                    if not party_info.get("Party_ID") and "party_id" in party_decision:
                        party_info["Party_ID"] = str(party_decision.get("party_id", ""))
                    if not party_info.get("Name") and "party_name" in party_decision:
                        party_info["Name"] = str(party_decision.get("party_name", ""))
                    if party_info.get("Liability") == 0 and "liability" in party_decision:
                        party_info["Liability"] = int(party_decision.get("liability", 0))
                    
                    # Build full analysis text for model (same as before)
                    full_analysis = f"""
Accident Description (Arabic):
{accident_description}

Case Information:
- Case Number: {accident_details.get("Case_Number", "")}
- Surveyor: {accident_details.get("Surveyor", "")}
- Date: {accident_details.get("Call_Date", "")} Time: {accident_details.get("Call_Time", "")}
- Location: {accident_details.get("Location", "")}, {accident_details.get("City", "")}
- Coordinates: {accident_details.get("Coordinates", "")}

Party {party_idx + 1} Information:
- Name: {party_info.get("Name", "")}
- ID: {party_info.get("Party_ID", "")}
- Gender: {party_info.get("Gender", "")}
- Age: {party_info.get("Age", "")}
- Nationality: {party_info.get("Nationality", "")}
- License No: {party_info.get("License_No", "")}
- Phone: {party_info.get("Phone", "")}
- Liability: {party_info.get("Liability", 0)}%
- Vehicle: {party_info.get("Vehicle_Make", "")} {party_info.get("Vehicle_Model", "")} ({party_info.get("Vehicle_Year", "")})
- Plate No: {party_info.get("Plate_No", "")}
- Chassis: {party_info.get("Chassis_No", "")}
- Insurance Company: {party_info.get("Insurance_Name", "")}
- Policy Number: {party_info.get("Policy_Number", "")}
- Policy Expiry: {party_info.get("Policy_Expiry", "")}
- Damage: {party_info.get("Damage_Type", "")}
- Act/Violation: {party_info.get("Act_Violation", "")}
"""
                    
                    decision = party_decision.get("decision", "PENDING")
                    reasoning = party_decision.get("reasoning", "")
                    classification = party_decision.get("classification", "UNKNOWN")
                    applied_conditions = party_decision.get("applied_conditions", [])
                    
                    # CRITICAL: Translate reasoning and classification from Arabic to English if needed
                    # OPTIMIZED: Use async/non-blocking translation or batch
                    if reasoning:
                        reasoning_english = self._translate_arabic_to_english(reasoning)
                        reasoning = reasoning_english
                    if classification:
                        classification_english = self._translate_arabic_to_english(classification)
                        classification = classification_english
                    
                    # Return early result for parallel processing - validation will be done after all parties processed
                    return {
                        "party_idx": party_idx,
                        "party_info": party_info,
                        "party_raw_data": party_raw_data,
                        "decision": decision,
                        "reasoning": reasoning,
                        "classification": classification,
                        "applied_conditions": applied_conditions,
                        "full_analysis": full_analysis
                    }
                except Exception as e:
                    print(f"  ✗ Error in parallel party processing for party {party_idx + 1}: {str(e)[:200]}")
                    return None
            
            # Process parties in parallel within this accident
            party_tasks = []
            for party_idx, party_decision in enumerate(parties):
                party_raw_data = parties_data.get(party_idx, {})
                if not party_raw_data and "party_info" in party_decision:
                    party_raw_data = party_decision.get("party_info", {})
                
                party_tasks.append((party_idx, party_decision, party_raw_data))
            
            # Process parties in parallel (up to number of parties in this accident)
            max_party_workers = min(len(parties), 10)  # Max 10 workers per accident to avoid overwhelming
            
            party_results_prelim = {}
            if len(parties) > 1:
                print(f"  ⚡ Processing {len(parties)} parties in parallel (max {max_party_workers} workers)...")
                with ThreadPoolExecutor(max_workers=max_party_workers) as party_executor:
                    party_futures = {
                        party_executor.submit(
                            process_single_party_optimized,
                            party_idx, party_decision, party_raw_data, parties, parties_data,
                            accident_details, accident_description, case_number, party_date_matches,
                            used_dates_for_case, case_date_positions, case_ocr_text, case_number_for_base64,
                            base64_files_path
                        ): party_idx
                        for party_idx, party_decision, party_raw_data in party_tasks
                    }
                    
                    for future in as_completed(party_futures):
                        party_idx = party_futures[future]
                        try:
                            result = future.result()
                            if result:
                                party_results_prelim[party_idx] = result
                        except Exception as e:
                            print(f"  ✗ Error processing party {party_idx + 1} in parallel: {str(e)[:200]}")
            else:
                # Single party - process directly
                party_idx, party_decision, party_raw_data = party_tasks[0]
                result = process_single_party_optimized(
                    party_idx, party_decision, party_raw_data, parties, parties_data,
                    accident_details, accident_description, case_number, party_date_matches,
                    used_dates_for_case, case_date_positions, case_ocr_text, case_number_for_base64,
                    base64_files_path
                )
                if result:
                    party_results_prelim[party_idx] = result
            
            # Now process each party result sequentially for validation (validation depends on other parties)
            for party_idx, party_decision in enumerate(parties):
                if party_idx not in party_results_prelim:
                    continue
                    
                prelim_result = party_results_prelim[party_idx]
                party_info = prelim_result["party_info"]
                party_raw_data = prelim_result["party_raw_data"]
                decision = prelim_result["decision"]
                reasoning = prelim_result["reasoning"]
                classification = prelim_result["classification"]
                applied_conditions = prelim_result["applied_conditions"]
                full_analysis = prelim_result["full_analysis"]
                party_raw_data = parties_data.get(party_idx, {})
                
                if not party_raw_data and "party_info" in party_decision:
                    party_raw_data = party_decision.get("party_info", {})
                
                party_info = self.extract_party_info(party_raw_data)
                
                # Ensure License_Type_From_Make_Model is in party_info
                if "License_Type_From_Make_Model" not in party_info and "License_Type_From_Make_Model" in party_raw_data:
                    party_info["License_Type_From_Make_Model"] = party_raw_data["License_Type_From_Make_Model"]
                
                # Fallback from decision
                if not party_info.get("Party_ID") and "party_id" in party_decision:
                    party_info["Party_ID"] = str(party_decision.get("party_id", ""))
                if not party_info.get("Name") and "party_name" in party_decision:
                    party_info["Name"] = str(party_decision.get("party_name", ""))
                if party_info.get("Liability") == 0 and "liability" in party_decision:
                    party_info["Liability"] = int(party_decision.get("liability", 0))
                
                # Build full analysis text for model
                full_analysis = f"""
Accident Description (Arabic):
{accident_description}

Case Information:
- Case Number: {accident_details.get("Case_Number", "")}
- Surveyor: {accident_details.get("Surveyor", "")}
- Date: {accident_details.get("Call_Date", "")} Time: {accident_details.get("Call_Time", "")}
- Location: {accident_details.get("Location", "")}, {accident_details.get("City", "")}
- Coordinates: {accident_details.get("Coordinates", "")}

Party {party_idx + 1} Information:
- Name: {party_info.get("Name", "")}
- ID: {party_info.get("Party_ID", "")}
- Gender: {party_info.get("Gender", "")}
- Age: {party_info.get("Age", "")}
- Nationality: {party_info.get("Nationality", "")}
- License No: {party_info.get("License_No", "")}
- Phone: {party_info.get("Phone", "")}
- Liability: {party_info.get("Liability", 0)}%
- Vehicle: {party_info.get("Vehicle_Make", "")} {party_info.get("Vehicle_Model", "")} ({party_info.get("Vehicle_Year", "")})
- Plate No: {party_info.get("Plate_No", "")}
- Chassis: {party_info.get("Chassis_No", "")}
- Insurance Company: {party_info.get("Insurance_Name", "")}
- Policy Number: {party_info.get("Policy_Number", "")}
- Policy Expiry: {party_info.get("Policy_Expiry", "")}
- Damage: {party_info.get("Damage_Type", "")}
- Act/Violation: {party_info.get("Act_Violation", "")}
"""
                
                decision = party_decision.get("decision", "PENDING")
                reasoning = party_decision.get("reasoning", "")
                classification = party_decision.get("classification", "UNKNOWN")
                applied_conditions = party_decision.get("applied_conditions", [])
                
                # CRITICAL: Translate reasoning and classification from Arabic to English if needed
                if reasoning:
                    reasoning_english = self._translate_arabic_to_english(reasoning)
                    reasoning = reasoning_english  # Use English version
                if classification:
                    classification_english = self._translate_arabic_to_english(classification)
                    classification = classification_english  # Use English version
                
                # ========== VALIDATE DECISION BASED ON LIABILITY ==========
                # Critical validation: 0% liability party should NOT be rejected just because another party has 100% liability
                current_liability = party_info.get("Liability", 0)
                if current_liability == 0 and decision == "REJECTED":
                    # Check if rejection is only due to another party's 100% liability (incorrect)
                    rejection_reason_lower = reasoning.lower() if reasoning else ""
                    classification_lower = classification.lower() if classification else ""
                    
                    # Check if the rejection reason mentions 100% liability rule incorrectly
                    # Note: classification is already translated to English above
                    if ("100%" in rejection_reason_lower or "100%" in classification_lower or 
                        "basic rule" in classification_lower or "rule #1" in classification_lower):
                        # Check if there's another party with 100% liability
                        has_other_100_percent = False
                        for other_idx, other_party_decision in enumerate(parties):
                            if other_idx != party_idx:
                                other_party_raw = parties_data.get(other_idx, {})
                                if not other_party_raw and "party_info" in other_party_decision:
                                    other_party_raw = other_party_decision.get("party_info", {})
                                other_party_info = self.extract_party_info(other_party_raw)
                                if other_party_info.get("Liability", 0) == 100:
                                    has_other_100_percent = True
                                    break
                        
                        # If rejection is only because another party has 100% liability, this is WRONG
                        if has_other_100_percent:
                            print(f"  ⚠️ VALIDATION: Party {party_idx + 1} has 0% liability but was REJECTED")
                            print(f"  ⚠️ This appears to be incorrect - 0% liability party should not be rejected")
                            print(f"  ⚠️ Correcting decision from REJECTED to ACCEPTED")
                            decision = "ACCEPTED"
                            reasoning = f"{reasoning} | CORRECTED: 0% liability party should not be rejected when another party has 100% liability" if reasoning else "CORRECTED: 0% liability party should not be rejected when another party has 100% liability"
                            classification = "Correction Rule: Victim party (0% liability) must be accepted"
                
                # ========== VALIDATE 100% LIABILITY RULE (APPLIES TO ALL COMPANIES) ==========
                # CRITICAL RULE #1: If liability = 100%, MUST be REJECTED for ALL insurance companies
                # This rule applies to ALL companies, including التعاونيه للتامين (Cooperative) and all others
                if current_liability == 100 and decision != "REJECTED":
                    print(f"  ⚠️ VALIDATION: Party {party_idx + 1} has 100% liability but decision is {decision}")
                    print(f"  ⚠️ Rule #1: 100% liability MUST result in REJECTED for ALL companies")
                    print(f"  ⚠️ Insurance: {party_info.get('Insurance_Name', 'N/A')}")
                    print(f"  ⚠️ Correcting decision from {decision} to REJECTED")
                    decision = "REJECTED"
                    reasoning = f"Rule #1: 100% liability requires REJECTED for all companies. {reasoning}" if reasoning else "Rule #1: 100% liability requires REJECTED for all companies"
                    classification = "Basic Rule #1: 100% liability = REJECTED (all companies)"
                
                # ========== VALIDATE NON-COOPERATIVE INSURANCE RULE (RULE #3) - APPLIES BEFORE OTHER RULES ==========
                # Rule #3: If party is insured with NON-cooperative (non-Tawuniya) company:
                # - And liability = 0% or 25% or 50% or 75%
                # → Mandatory decision: ACCEPTED (unless rejection conditions 1-16 are met)
                # This rule applies BEFORE all other rules EXCEPT the 100% liability rule
                # Must be checked BEFORE the global Tawuniya rule and cooperative rules
                # IMPORTANT: Rule #3 has HIGH PRIORITY - it applies even if AI decision is REJECTED
                rule3_applied = False
                if current_liability != 100:  # Rule #3 doesn't apply to 100% liability (Rule #1 takes precedence)
                    current_insurance_check_rule3 = str(party_info.get("Insurance_Name", "")).strip()
                    current_ic_english_rule3 = str(party_info.get("ICEnglishName", "")).strip()
                    # Check if it's specifically Tawuniya using precise matching with ICEnglishName
                    is_tawuniya_check_rule3 = self._is_tawuniya_insurance(current_insurance_check_rule3, current_ic_english_rule3)
                    
                    # Check if party is NON-Tawuniya (not insured with Tawuniya) and has valid liability percentage
                    # Rule #3 applies to all companies that are NOT Tawuniya
                    if not is_tawuniya_check_rule3 and current_liability in [0, 25, 50, 75]:
                        # Rule #3 applies: Non-cooperative party with 0%/25%/50%/75% liability → ACCEPTED
                        # This rule OVERRIDES any REJECTED decision from AI or other rules (except Rule #1)
                        if decision != "ACCEPTED" and decision != "ACCEPTED_WITH_RECOVERY":
                            print(f"  ⚠️ VALIDATION: Rule #3 (Non-Cooperative Insurance) applies - HIGH PRIORITY")
                            print(f"  ⚠️ Party is insured with NON-Tawuniya company: {current_insurance_check_rule3} ({current_ic_english_rule3})")
                            print(f"  ⚠️ Liability: {current_liability}%")
                            print(f"  ⚠️ Rule #3: Non-Tawuniya parties with 0%/25%/50%/75% liability MUST be ACCEPTED")
                            print(f"  ⚠️ Overriding AI decision from {decision} to ACCEPTED (Rule #3 has priority)")
                            decision = "ACCEPTED"
                            reasoning = f"Rule #3 (HIGH PRIORITY): Non-Tawuniya insurance party with {current_liability}% liability requires ACCEPTED. Overridden previous decision. {reasoning}" if reasoning else f"Rule #3 (HIGH PRIORITY): Non-Tawuniya insurance party with {current_liability}% liability requires ACCEPTED"
                            classification = f"Rule #3: Other insurance companies (non-Tawuniya) - {current_liability}% liability = ACCEPTED"
                            rule3_applied = True
                        else:
                            print(f"  ✅ VALIDATION: Rule #3 applies and decision is already correct (ACCEPTED or ACCEPTED_WITH_RECOVERY)")
                            rule3_applied = True
                
                # ========== GLOBAL RULE: 100% LIABILITY FROM NON-TAWUNIYA COMPANY ==========
                # SPECIAL RULE FOR TAWUNIYA: If ANY party has 100% liability from a NON-Tawuniya company,
                # ALL parties (regardless of insurance company) must be REJECTED
                # This is because Tawuniya does not accept claims when responsibility party is not Tawuniya
                # NOTE: This rule OVERRIDES Rule #3 - it applies AFTER Rule #3
                # Check if any party has 100% liability from a non-Tawuniya company
                has_100_percent_non_tawuniya = False
                non_tawuniya_100_party_info = None
                for idx_check, other_party_decision_check in enumerate(parties):
                    if idx_check == party_idx:
                        continue
                    
                    # Get other party info
                    other_party_raw_check = parties_data.get(idx_check, {})
                    if not other_party_raw_check and "party_info" in other_party_decision_check:
                        other_party_raw_check = other_party_decision_check.get("party_info", {})
                    
                    other_party_info_check = self.extract_party_info(other_party_raw_check)
                    
                    # Fallback from decision
                    if other_party_info_check.get("Liability") == 0 and "liability" in other_party_decision_check:
                        other_party_info_check["Liability"] = int(other_party_decision_check.get("liability", 0))
                    
                    other_liability_check = other_party_info_check.get("Liability", 0)
                    other_insurance_check = str(other_party_info_check.get("Insurance_Name", "")).strip()
                    
                    # Check if this party has 100% liability and is NOT Tawuniya (using precise detection)
                    if other_liability_check == 100:
                        other_ic_english_check = str(other_party_info_check.get("ICEnglishName", "")).strip()
                        is_other_tawuniya = self._is_tawuniya_insurance(other_insurance_check, other_ic_english_check)
                        
                        if not is_other_tawuniya:
                            has_100_percent_non_tawuniya = True
                            non_tawuniya_100_party_info = {
                                "idx": idx_check,
                                "insurance": other_insurance_check,
                                "liability": other_liability_check
                            }
                            break
                
                # If there's a 100% liability party from non-Tawuniya, REJECT ALL parties
                # This OVERRIDES Rule #3 - the global Tawuniya rule takes precedence
                if has_100_percent_non_tawuniya:
                    if decision != "REJECTED":
                        print(f"  ⚠️ VALIDATION: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company: {non_tawuniya_100_party_info['insurance']}")
                        print(f"  ⚠️ Tawuniya Rule: ALL parties must be REJECTED when responsibility party (100%) is NOT Tawuniya")
                        if rule3_applied:
                            print(f"  ⚠️ NOTE: Rule #3 was applied but is OVERRIDDEN by Tawuniya Global Rule")
                        print(f"  ⚠️ Current party: {party_info.get('Insurance_Name', 'N/A')}, Liability: {current_liability}%")
                        print(f"  ⚠️ Correcting decision from {decision} to REJECTED")
                        decision = "REJECTED"
                        reasoning = f"Tawuniya Global Rule OVERRIDES Rule #3: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}). All parties must be REJECTED. {reasoning}" if reasoning else f"Tawuniya Global Rule: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}). All parties must be REJECTED."
                        classification = "Tawuniya Rule: Reject all parties when there is a responsible party (100%) from a non-Tawuniya company"
                
                # ========== VALIDATE COOPERATIVE INSURANCE DECISION (ONLY FOR التعاونيه للتامين) ==========
                # This validation applies to ALL التعاونيه للتامين parties (including 0% liability)
                # Special rule: If there's a 100% liability party from a non-cooperative company,
                # ALL Tawuniya parties (including 0%) must be REJECTED
                # It should NOT apply to 100% liability cooperative parties (100% rule applies instead)
                # Skip if already rejected by global rule (100% from non-Tawuniya)
                validation_result_cooperative = None
                current_insurance_check = str(party_info.get("Insurance_Name", "")).strip()
                current_ic_english_check = str(party_info.get("ICEnglishName", "")).strip()
                # Use precise Tawuniya detection with ICEnglishName
                is_cooperative_check = self._is_tawuniya_insurance(current_insurance_check, current_ic_english_check)
                
                # Check cooperative rules for ALL cooperative parties (including 0%), except those with 100% liability
                # (100% liability cooperative parties are handled by the 100% rule above)
                # Also skip if already rejected by global rule (all parties rejected when 100% from non-Tawuniya)
                # NOTE: Cooperative rules only apply to Tawuniya parties, NOT other cooperative companies
                # Rule #3 already handled non-Tawuniya companies above
                if is_cooperative_check and current_liability < 100 and decision != "REJECTED" and not rule3_applied:
                    validation_result_cooperative = self._validate_cooperative_insurance_decision(
                        party_idx, party_info, parties, parties_data
                    )
                    if not validation_result_cooperative["is_valid"] and validation_result_cooperative["corrected_decision"]:
                        print(f"  ⚠️ VALIDATION: Cooperative insurance rule validation failed")
                        print(f"  ⚠️ Reason: {validation_result_cooperative['reason']}")
                        print(f"  ⚠️ Correcting decision from {decision} to {validation_result_cooperative['corrected_decision']}")
                        decision = validation_result_cooperative["corrected_decision"]
                        reasoning = f"{reasoning} | COOPERATIVE VALIDATION: {validation_result_cooperative['reason']}" if reasoning else f"COOPERATIVE VALIDATION: {validation_result_cooperative['reason']}"
                    elif validation_result_cooperative["is_valid"] and "Cooperative" in validation_result_cooperative.get("reason", ""):
                        print(f"  ✅ VALIDATION: Cooperative insurance rule validation passed")
                        print(f"  ✅ Details: {validation_result_cooperative['reason']}")
                
                # ========== VALIDATE ACCEPTED_WITH_RECOVERY DECISION ==========
                # Get accident date from accident_details (available earlier)
                accident_date_for_validation = accident_details.get("Call_Date", "") if isinstance(accident_details, dict) else ""
                
                # Store recovery reasons and analysis for later use in description
                recovery_reasons_list = []
                current_party_recovery_analysis = None
                
                if decision == "ACCEPTED_WITH_RECOVERY":
                    # Validate existing ACCEPTED_WITH_RECOVERY decision
                    validation_result = self._validate_recovery_decision(
                        party_idx, party_info, parties, parties_data, accident_date_for_validation
                    )
                    if not validation_result["is_valid"]:
                        print(f"  ⚠️ VALIDATION FAILED: ACCEPTED_WITH_RECOVERY decision is invalid")
                        print(f"  ⚠️ Reason: {validation_result['reason']}")
                        print(f"  ⚠️ Correcting decision from ACCEPTED_WITH_RECOVERY to {validation_result['corrected_decision']}")
                        decision = validation_result["corrected_decision"]
                        # Update reasoning to include validation failure
                        reasoning = f"{reasoning} | VALIDATION: {validation_result['reason']}" if reasoning else f"VALIDATION: {validation_result['reason']}"
                    else:
                        print(f"  ✅ VALIDATION PASSED: ACCEPTED_WITH_RECOVERY decision is valid")
                        print(f"  ✅ Validation details: {validation_result['reason']}")
                        # Store recovery reasons and current party analysis for description
                        recovery_reasons_list = validation_result.get("recovery_reasons", [])
                        current_party_recovery_analysis = validation_result.get("current_party_recovery_analysis")
                        if current_party_recovery_analysis:
                            print(f"  ✅ Current Party Recovery Analysis: Recovery Field={current_party_recovery_analysis.get('recovery_field')}, "
                                  f"Has Recovery={current_party_recovery_analysis.get('has_recovery_field')}, "
                                  f"Violations Found={len(current_party_recovery_analysis.get('violations_found', []))}")
                
                elif decision == "ACCEPTED":
                    # Check if ACCEPTED decision should be upgraded to ACCEPTED_WITH_RECOVERY
                    # Only check if current party has 0% liability (victim) - recovery only applies to victims
                    if current_liability == 0:
                        validation_result = self._validate_recovery_decision(
                            party_idx, party_info, parties, parties_data, accident_date_for_validation
                        )
                        # If validation returns is_valid=True with corrected_decision="ACCEPTED_WITH_RECOVERY",
                        # it means recovery conditions are valid and should be applied
                        if validation_result.get("is_valid") and validation_result.get("corrected_decision") == "ACCEPTED_WITH_RECOVERY":
                            print(f"  ✅ VALIDATION: ACCEPTED decision qualifies for ACCEPTED_WITH_RECOVERY")
                            print(f"  ✅ Reason: {validation_result['reason']}")
                            print(f"  ✅ Upgrading decision from ACCEPTED to ACCEPTED_WITH_RECOVERY")
                            decision = "ACCEPTED_WITH_RECOVERY"
                            reasoning = f"{reasoning} | UPGRADED: {validation_result['reason']}" if reasoning else f"UPGRADED: {validation_result['reason']}"
                            classification = f"{classification} | Upgraded to ACCEPTED_WITH_RECOVERY due to recovery conditions" if classification else "Upgraded to ACCEPTED_WITH_RECOVERY due to recovery conditions"
                            # Store recovery reasons and current party analysis for description
                            recovery_reasons_list = validation_result.get("recovery_reasons", [])
                            current_party_recovery_analysis = validation_result.get("current_party_recovery_analysis")
                            if current_party_recovery_analysis:
                                print(f"  ✅ Current Party Recovery Analysis: Recovery Field={current_party_recovery_analysis.get('recovery_field')}, "
                                      f"Has Recovery={current_party_recovery_analysis.get('has_recovery_field')}, "
                                      f"Violations Found={len(current_party_recovery_analysis.get('violations_found', []))}")
                
                # ========== ADD VALIDATION ANALYSIS TO FULL_ANALYSIS ==========
                # Append validation details to full_analysis for better traceability
                validation_analysis = []
                
                # Check for global rule: 100% liability from non-Tawuniya company
                has_100_non_tawuniya_analysis = False
                non_tawuniya_100_info_analysis = None
                for idx_analysis, other_party_decision_analysis in enumerate(parties):
                    if idx_analysis == party_idx:
                        continue
                    other_party_raw_analysis = parties_data.get(idx_analysis, {})
                    if not other_party_raw_analysis and "party_info" in other_party_decision_analysis:
                        other_party_raw_analysis = other_party_decision_analysis.get("party_info", {})
                    other_party_info_analysis = self.extract_party_info(other_party_raw_analysis)
                    other_liability_analysis = other_party_info_analysis.get("Liability", 0)
                    if other_liability_analysis == 100:
                        other_insurance_analysis = str(other_party_info_analysis.get("Insurance_Name", "")).strip()
                        other_ic_english_analysis = str(other_party_info_analysis.get("ICEnglishName", "")).strip()
                        is_other_tawuniya_analysis = self._is_tawuniya_insurance(other_insurance_analysis, other_ic_english_analysis)
                        if not is_other_tawuniya_analysis:
                            has_100_non_tawuniya_analysis = True
                            non_tawuniya_100_info_analysis = {
                                "idx": idx_analysis,
                                "insurance": other_insurance_analysis,
                                "liability": other_liability_analysis
                            }
                            break
                
                # Add global Tawuniya rule validation (applies to ALL parties)
                if has_100_non_tawuniya_analysis:
                    validation_analysis.append("\n=== VALIDATION: Tawuniya Global Rule (100% from Non-Tawuniya) ===")
                    validation_analysis.append("⚠️ CRITICAL: If ANY party has 100% liability from a NON-Tawuniya company:")
                    validation_analysis.append("  → ALL parties (regardless of insurance company) must be REJECTED")
                    validation_analysis.append(f"\nTriggered by:")
                    validation_analysis.append(f"  - Party {non_tawuniya_100_info_analysis['idx'] + 1}: {non_tawuniya_100_info_analysis['insurance']} with 100% liability")
                    validation_analysis.append(f"\nCurrent Party:")
                    validation_analysis.append(f"  - Insurance: {party_info.get('Insurance_Name', 'N/A')}")
                    validation_analysis.append(f"  - Liability: {current_liability}%")
                    validation_analysis.append(f"  - Decision: REJECTED (applied globally)")
                    validation_analysis.append(f"\nReason: Tawuniya does not accept claims when the responsibility party (100%) is NOT Tawuniya")
                
                # Add 100% liability rule validation (for the party itself)
                if current_liability == 100:
                    validation_analysis.append("\n=== VALIDATION: 100% Liability Rule ===")
                    validation_analysis.append("Rule #1: 100% liability = REJECTED (applies to ALL companies)")
                    validation_analysis.append(f"Insurance Company: {party_info.get('Insurance_Name', 'N/A')}")
                    validation_analysis.append(f"Validation: Applied - Decision must be REJECTED")
                
                # Add Rule #3 (Non-Tawuniya Insurance) validation details
                current_insurance_name_for_rule3 = str(party_info.get("Insurance_Name", "")).strip()
                current_ic_english_for_rule3 = str(party_info.get("ICEnglishName", "")).strip()
                is_tawuniya_rule3 = self._is_tawuniya_insurance(current_insurance_name_for_rule3, current_ic_english_for_rule3)
                
                validation_analysis.append("\n=== VALIDATION: Rule #3 (Non-Tawuniya Insurance) ===")
                validation_analysis.append("Rule #3: Non-Tawuniya insurance parties with 0%/25%/50%/75% liability")
                validation_analysis.append("  → Mandatory decision: ACCEPTED (unless rejection conditions 1-16)")
                validation_analysis.append(f"Insurance Company: {current_insurance_name_for_rule3}")
                validation_analysis.append(f"Is Tawuniya: {is_tawuniya_rule3}")
                validation_analysis.append(f"Liability: {current_liability}%")
                
                if current_liability == 100:
                    validation_analysis.append(f"Rule Applies: NO - Liability is 100% (Rule #1 takes precedence)")
                elif is_tawuniya_rule3:
                    validation_analysis.append(f"Rule Applies: NO - This is a Tawuniya party (Rule #3 only applies to non-Tawuniya companies)")
                elif current_liability not in [0, 25, 50, 75]:
                    validation_analysis.append(f"Rule Applies: NO - Liability ({current_liability}%) is not 0%, 25%, 50%, or 75%")
                else:
                    validation_analysis.append(f"Rule Applies: YES - Non-Tawuniya party with {current_liability}% liability → Decision should be ACCEPTED")
                    validation_analysis.append(f"Final Decision: {decision}")
                    if decision == "REJECTED":
                        validation_analysis.append(f"⚠️ NOTE: Decision is REJECTED but Rule #3 requires ACCEPTED. This may be overridden by rejection conditions 1-16 or other rules.")
                    elif decision == "ACCEPTED" or decision == "ACCEPTED_WITH_RECOVERY":
                        validation_analysis.append(f"✅ Decision is correctly ACCEPTED/ACCEPTED_WITH_RECOVERY as per Rule #3")
                
                # Add cooperative insurance validation details
                # Check ALL parties (including 0% liability) for Tawuniya insurance
                current_insurance_name = str(party_info.get("Insurance_Name", "")).strip()
                current_ic_english = str(party_info.get("ICEnglishName", "")).strip()
                is_cooperative = self._is_tawuniya_insurance(current_insurance_name, current_ic_english)
                
                if is_cooperative:
                        validation_analysis.append("\n=== VALIDATION: التعاونيه للتامين (Cooperative Insurance) Rules ===")
                        validation_analysis.append(f"Insurance: {current_insurance_name}")
                        validation_analysis.append(f"Liability: {current_liability}%")
                        validation_analysis.append("\nSPECIAL RULE: If ANY party has 100% liability from a NON-cooperative company:")
                        validation_analysis.append("  → ALL Tawuniya parties (including 0% liability) must be REJECTED")
                        validation_analysis.append("\nRule: If insured with Cooperative AND liability < 100%:")
                        validation_analysis.append("  → REJECT if ANY party with liability > 0% is NOT insured with Cooperative")
                        validation_analysis.append("Exception: ACCEPT if ALL at-fault parties are Cooperative with 25%/50%/75%")
                        
                        # Check for 100% liability from non-cooperative company
                        has_100_percent_non_coop = False
                        for idx_check, other_party_decision_check in enumerate(parties):
                            if idx_check == party_idx:
                                continue
                            other_party_raw_check = parties_data.get(idx_check, {})
                            if not other_party_raw_check and "party_info" in other_party_decision_check:
                                other_party_raw_check = other_party_decision_check.get("party_info", {})
                            other_party_info_check = self.extract_party_info(other_party_raw_check)
                            other_liability_check = other_party_info_check.get("Liability", 0)
                            if other_liability_check == 100:
                                other_insurance_check = str(other_party_info_check.get("Insurance_Name", "")).strip()
                                other_ic_english_check = str(other_party_info_check.get("ICEnglishName", "")).strip()
                                other_is_coop_check = self._is_tawuniya_insurance(other_insurance_check, other_ic_english_check)
                                if not other_is_coop_check:
                                    has_100_percent_non_coop = True
                                    validation_analysis.append(f"\n⚠️ SPECIAL RULE TRIGGERED:")
                                    validation_analysis.append(f"  - Party {idx_check + 1} has 100% liability from non-cooperative company: {other_insurance_check}")
                                    validation_analysis.append(f"  - Current party (Tawuniya) must be REJECTED regardless of liability percentage")
                                    break
                        
                        # Add validation result details
                        if validation_result_cooperative:
                            validation_analysis.append(f"\nValidation Result: {validation_result_cooperative.get('reason', 'N/A')}")
                            if validation_result_cooperative.get("corrected_decision"):
                                validation_analysis.append(f"Corrected Decision: {validation_result_cooperative['corrected_decision']}")
                        
                        # Get other parties info for analysis
                        other_parties_info = []
                        for idx, other_party_decision in enumerate(parties):
                            if idx == party_idx:
                                continue
                            other_party_raw = parties_data.get(idx, {})
                            if not other_party_raw and "party_info" in other_party_decision:
                                other_party_raw = other_party_decision.get("party_info", {})
                            other_party_info_temp = self.extract_party_info(other_party_raw)
                            other_liability = other_party_info_temp.get("Liability", 0)
                            if other_liability > 0:
                                other_insurance = other_party_info_temp.get("Insurance_Name", "")
                                other_ic_english = str(other_party_info_temp.get("ICEnglishName", "")).strip()
                                other_is_coop = self._is_tawuniya_insurance(str(other_insurance), other_ic_english)
                                other_parties_info.append(f"  - Party {idx + 1}: Liability={other_liability}%, Insurance={other_insurance}, Tawuniya={other_is_coop}")
                        
                        if other_parties_info:
                            validation_analysis.append("\nOther Parties with Liability > 0%:")
                            validation_analysis.extend(other_parties_info)
                
                # Append validation analysis to full_analysis
                if validation_analysis:
                    full_analysis += "\n\n" + "="*60 + "\n"
                    full_analysis += "DECISION VALIDATION ANALYSIS\n"
                    full_analysis += "="*60 + "\n"
                    full_analysis += "\n".join(validation_analysis)
                    full_analysis += "\n\nFinal Decision: " + decision
                    full_analysis += "\nFinal Classification: " + classification
                    if reasoning:
                        full_analysis += "\nFinal Reasoning: " + reasoning
                
                # Create Full_Analysis_English by translating Arabic text to English
                # This contains the same full details as Full_Analysis but with Arabic parts translated to English
                # Always translate if Arabic text is detected, or if translation is explicitly enabled
                # Initialize with original text as fallback
                full_analysis_english = full_analysis if full_analysis else ""
                
                # Check if translation should be performed
                # Always translate if there's Arabic text, or if explicitly enabled
                enable_translation_flag = getattr(self, '_enable_translation', False)
                has_arabic = False
                if full_analysis and full_analysis.strip():
                    # Check if text contains Arabic characters
                    has_arabic = bool(re.search(r'[\u0600-\u06FF]', full_analysis))
                
                # OPTIMIZATION: Batch translations for all parties or skip if not critical
                # Translate if Arabic is detected OR if translation is explicitly enabled
                if (has_arabic or enable_translation_flag) and full_analysis and full_analysis.strip():
                    print(f"  🔄 Translating Full_Analysis to English...")
                    if has_arabic:
                        print(f"  📝 Arabic text detected - translation will be performed")
                    
                    # OPTIMIZATION: For speed, skip translation during parallel processing
                    # Can be enabled later if needed, or do batch translation
                    # Set timeout to prevent blocking
                    try:
                        import signal
                        # Use a shorter timeout for translation (30 seconds max)
                        translated = self._translate_arabic_to_english(full_analysis)
                        if translated and translated.strip() and translated.strip() != full_analysis.strip():
                            full_analysis_english = translated
                            print(f"  ✅ Translation completed")
                        else:
                            print(f"  ⚠️ Translation returned same/empty text, using original")
                            # Even if translation fails, save the original text in English field
                            full_analysis_english = full_analysis if full_analysis else ""
                    except Exception as e:
                        error_msg = str(e)[:200] if e else "Unknown error"
                        print(f"  ⚠️ Translation skipped (timeout/error): {error_msg}, using original text")
                        # Always save something - use original text if translation fails
                        full_analysis_english = full_analysis if full_analysis else ""
                else:
                    if not has_arabic and full_analysis:
                        print(f"  ℹ️ No Arabic text detected in Full_Analysis - skipping translation")
                    # If no Arabic and translation not enabled, keep original text (which may be English)
                    # Ensure full_analysis_english is always set
                    if not full_analysis_english:
                        full_analysis_english = full_analysis if full_analysis else ""
                
                # ========== DETAILED DEBUG PRINTING ==========
                print(f"\n{'='*80}")
                print(f"🔍 PROCESSING PARTY {party_idx + 1} OF {len(parties)}")
                print(f"{'='*80}")
                print(f"📋 Party Information:")
                print(f"   - Party Index: {party_idx}")
                print(f"   - Party ID (raw): {party_info.get('Party_ID', 'N/A')}")
                party_id_cleaned = re.sub(r'[^\d]', '', str(party_info.get('Party_ID', '')))
                print(f"   - Party ID (cleaned): {party_id_cleaned}")
                print(f"   - Name: {party_info.get('Name', 'N/A')}")
                print(f"   - Liability: {party_info.get('Liability', 0)}%")
                print(f"   - Insurance: {party_info.get('Insurance_Name', 'N/A')}")
                print(f"   - Decision: {decision}")
                print(f"   - Classification: {classification}")
                print(f"{'='*80}\n")
                
                # Build description
                description_parts = []
                if reasoning:
                    description_parts.append(f"Reasoning: {reasoning}")
                if applied_conditions:
                    description_parts.append(f"Applied Conditions: {', '.join(map(str, applied_conditions))}")
                
                # Add recovery reason identification for ACCEPTED_WITH_RECOVERY decisions
                if decision == "ACCEPTED_WITH_RECOVERY" and recovery_reasons_list:
                    recovery_reason_text = " | ".join(recovery_reasons_list)
                    description_parts.append(f"Recovery Reason: {recovery_reason_text}")
                    
                    # Add detailed current party recovery analysis if available
                    if current_party_recovery_analysis:
                        recovery_analysis_parts = []
                        if current_party_recovery_analysis.get("recovery_field"):
                            recovery_analysis_parts.append(f"Recovery Field: {current_party_recovery_analysis.get('recovery_field')}")
                        if current_party_recovery_analysis.get("act_violation"):
                            recovery_analysis_parts.append(f"Act Violation: {current_party_recovery_analysis.get('act_violation')[:100]}")
                        if current_party_recovery_analysis.get("license_expiry_date") and \
                           current_party_recovery_analysis.get("license_expiry_date").lower() not in ["not identify", "not identified", ""]:
                            recovery_analysis_parts.append(f"License Expiry: {current_party_recovery_analysis.get('license_expiry_date')}")
                        if current_party_recovery_analysis.get("violations_found"):
                            violations_text = ", ".join(current_party_recovery_analysis.get("violations_found", []))
                            recovery_analysis_parts.append(f"Current Party Violations: {violations_text}")
                        
                        if recovery_analysis_parts:
                            description_parts.append(f"Current Party Recovery Analysis: {' | '.join(recovery_analysis_parts)}")
                
                description = " | ".join(description_parts) if description_parts else "No description available"
                
                # Extract license expiry date from base64 file if available
                license_expiry_date = "not identify"
                license_expiry_last_updated = ""  # Timestamp when license expiry was extracted from OCR
                party_id = party_info.get("Party_ID", "")
                
                # Clean Party ID for matching - remove Arabic characters
                party_id_clean_for_matching = re.sub(r'[^\d]', '', str(party_id)) if party_id else ""
                if not party_id_clean_for_matching and party_id:
                    party_id_clean_for_matching = str(party_id).strip()
                
                # Check if we have a pre-matched date for this party
                # CRITICAL: Try multiple matching strategies for Party ID
                matched_date = None
                if party_id_clean_for_matching and party_date_matches:
                    # Strategy 1: Exact match with cleaned ID
                    if party_id_clean_for_matching in party_date_matches:
                        matched_date = party_date_matches[party_id_clean_for_matching]
                        print(f"  ✅ Using pre-matched date (exact match) for Party ID {party_id_clean_for_matching}: {matched_date}")
                    else:
                        # Strategy 2: Try string matching (handle type differences)
                        party_id_str = str(party_id_clean_for_matching).strip()
                        for pid_key, date_value in party_date_matches.items():
                            pid_key_str = str(pid_key).strip()
                            # Clean the key too
                            pid_key_clean = re.sub(r'[^\d]', '', pid_key_str)
                            if pid_key_clean == party_id_str or pid_key_str == party_id_str:
                                matched_date = date_value
                                print(f"  ✅ Using pre-matched date (string match) for Party ID {party_id_clean_for_matching}: {matched_date}")
                                break
                        
                        # Strategy 3: Try partial match (last 8-9 digits) - common when IDs are truncated
                        if not matched_date:
                            for pid_key, date_value in party_date_matches.items():
                                pid_key_str = str(pid_key).strip()
                                pid_key_clean = re.sub(r'[^\d]', '', pid_key_str)
                                if len(party_id_str) >= 8 and len(pid_key_clean) >= 8:
                                    if party_id_str[-8:] == pid_key_clean[-8:] or party_id_str[-9:] == pid_key_clean[-9:]:
                                        matched_date = date_value
                                        print(f"  ✅ Using pre-matched date (partial match) for Party ID {party_id_clean_for_matching}: {matched_date}")
                                        break
                        
                        # Strategy 4: If still no match and we have dates available, use order-based assignment
                        # Assign first unused date to first unmatched party, second unused date to second unmatched party, etc.
                        if not matched_date and case_date_positions:
                            # Get all used dates
                            used_dates_set = set(party_date_matches.values())
                            # Find first unused date
                            for date, _, _ in case_date_positions:
                                if date not in used_dates_set:
                                    matched_date = date
                                    # Add to matches for this party
                                    party_date_matches[party_id_clean_for_matching] = date
                                    print(f"  ✅ Using order-based assignment: Party ID {party_id_clean_for_matching} → Date {date} (first unused date)")
                                    break
                
                if matched_date:
                    print(f"  🔍 DEBUG FINAL VALIDATION: Checking matched date '{matched_date}' before assignment...")
                    # CRITICAL: Final validation - ensure matched_date is NOT a birth date
                    date_parts = matched_date.replace('/', '-').split('-')
                    is_birth_date = False
                    year = None
                    if len(date_parts) == 3:
                        try:
                            if len(date_parts[0]) == 4:
                                year = int(date_parts[0])
                                print(f"  🔍 DEBUG FINAL VALIDATION: Parsed '{matched_date}' as YYYY-MM-DD, year = {year}")
                            elif len(date_parts[2]) == 4:
                                year = int(date_parts[2])
                                print(f"  🔍 DEBUG FINAL VALIDATION: Parsed '{matched_date}' as DD-MM-YYYY, year = {year}")
                            elif len(date_parts[1]) == 4:
                                year = int(date_parts[1])
                                print(f"  🔍 DEBUG FINAL VALIDATION: Parsed '{matched_date}' as DD-YYYY-MM, year = {year}")
                            
                            if year:
                                # For Gregorian dates: exclude if < 2010
                                if 1900 <= year < 2010:
                                    is_birth_date = True
                                    print(f"  🚫 CRITICAL FINAL VALIDATION: REJECTING '{matched_date}' - year {year} < 2010 (BIRTH DATE/OLD LICENSE)")
                                    print(f"  🚫 Will NOT use this date - it's a birth date or very old license, not a current license expiry date")
                                    matched_date = None  # Reject this date
                                # For Hijri dates: valid range is 1400-1600
                                elif 1400 <= year <= 1600:
                                    print(f"  ✅ DEBUG FINAL VALIDATION: Date '{matched_date}' is VALID (Hijri year {year})")
                                elif year > 2100:
                                    is_birth_date = True
                                    print(f"  🚫 CRITICAL FINAL VALIDATION: REJECTING '{matched_date}' - year {year} > 2100 (OCR ERROR)")
                                    matched_date = None  # Reject this date
                                else:
                                    print(f"  ✅ DEBUG FINAL VALIDATION: Date '{matched_date}' is VALID (year {year} >= 2010)")
                        except (ValueError, IndexError) as e:
                            print(f"  ⚠️ DEBUG FINAL VALIDATION: Error parsing date '{matched_date}': {e}")
                    
                    if matched_date and not is_birth_date:
                        license_expiry_date = matched_date
                        license_expiry_last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        used_dates_for_case.add(matched_date)  # Mark as used
                        print(f"  ✅ DEBUG FINAL VALIDATION: ASSIGNED date '{matched_date}' to Party {party_idx + 1}")
                        print(f"  ✅ This ensures Party {party_idx + 1} gets a unique date (different from other parties)")
                        # DEBUG: Show all matched dates to verify uniqueness
                        print(f"  🔍 DEBUG: All pre-matched dates: {party_date_matches}")
                        print(f"  🔍 DEBUG: Current party date: {license_expiry_date} (year: {year})")
                        print(f"  🔍 DEBUG: Other parties' dates: {[d for pid, d in party_date_matches.items() if str(pid).strip() != str(party_id).strip()]}")
                        print(f"  🔍 DEBUG: Used dates in this case so far: {used_dates_for_case}")
                    elif is_birth_date:
                        print(f"  ⚠️ WARNING: Pre-matched date was a birth date - will try individual extraction")
                        license_expiry_date = "not identify"  # Reset to try fallback
                    else:
                        print(f"  ⚠️ DEBUG FINAL VALIDATION: No matched date available")
                else:
                    if party_id and party_date_matches:
                        print(f"  ⚠️ WARNING: Party ID {party_id} not found in pre-matched dates!")
                        print(f"  ⚠️ Available Party IDs in pre-matches: {list(party_date_matches.keys())}")
                        print(f"  ⚠️ Will try individual extraction as fallback")
                        print(f"  ⚠️ CRITICAL: Will avoid dates already used: {used_dates_for_case}")
                
                # If no pre-matched date, try individual extraction (fallback)
                # CRITICAL: This fallback should NOT be used if pre-matching worked for other parties
                # because it will extract the same date for all parties
                if license_expiry_date == "not identify" and base64_files_path and case_number:
                    # Try to load base64 from file: {base64_files_path}/{Case_Number}.txt
                    case_clean = str(case_number).strip()
                    base64_file_path = os.path.join(base64_files_path, f"{case_clean}.txt")
                    print(f"  🔍 Looking for base64 file: {base64_file_path}")
                    print(f"  🔍 Case number: {case_clean}")
                    print(f"  🔍 Base64 files path: {base64_files_path}")
                    
                    # List available files in directory for debugging
                    if os.path.exists(base64_files_path):
                        try:
                            available_files = [f for f in os.listdir(base64_files_path) if f.endswith('.txt')]
                            print(f"  📂 Available .txt files in directory ({len(available_files)} files):")
                            for f in available_files[:10]:  # Show first 10
                                print(f"     - {f}")
                            if len(available_files) > 10:
                                print(f"     ... and {len(available_files) - 10} more files")
                        except Exception as e:
                            print(f"  ⚠️ Could not list directory: {str(e)[:100]}")
                    
                    # Also try alternative case number formats if file not found
                    alternative_paths = [base64_file_path]
                    if not os.path.exists(base64_file_path):
                        print(f"  ⚠️ File not found: {base64_file_path}")
                        print(f"  🔍 Trying alternative case number formats...")
                        
                        # Try removing any prefixes (like "RD", "HA", etc.)
                        if len(case_clean) > 2 and case_clean[:2].isalpha():
                            case_without_prefix = case_clean[2:]
                            alt_path = os.path.join(base64_files_path, f"{case_without_prefix}.txt")
                            if alt_path not in alternative_paths:
                                alternative_paths.append(alt_path)
                                print(f"     Trying: {alt_path}")
                        
                        # Try last 10 characters (common format: RD1911253329)
                        if len(case_clean) > 10:
                            case_short = case_clean[-10:]
                            alt_path = os.path.join(base64_files_path, f"{case_short}.txt")
                            if alt_path not in alternative_paths:
                                alternative_paths.append(alt_path)
                                print(f"     Trying: {alt_path}")
                        
                        # Try with different separators
                        case_variants = [
                            case_clean.replace('-', ''),
                            case_clean.replace('_', ''),
                            case_clean.replace(' ', ''),
                        ]
                        for variant in case_variants:
                            if variant != case_clean:
                                alt_path = os.path.join(base64_files_path, f"{variant}.txt")
                                if alt_path not in alternative_paths:
                                    alternative_paths.append(alt_path)
                                    print(f"     Trying: {alt_path}")
                        
                        # Try partial match - search for files that start with case number
                        if os.path.exists(base64_files_path):
                            try:
                                for f in os.listdir(base64_files_path):
                                    if f.endswith('.txt'):
                                        # Check if filename starts with case number or contains it
                                        if case_clean in f or f.startswith(case_clean[:8]):  # First 8 chars
                                            alt_path = os.path.join(base64_files_path, f)
                                            if alt_path not in alternative_paths:
                                                alternative_paths.append(alt_path)
                                                print(f"     Found similar file: {f}")
                            except Exception:
                                pass
                    
                    # Try all alternative paths
                    base64_file_path = None
                    for path in alternative_paths:
                        if os.path.exists(path):
                            base64_file_path = path
                            print(f"  ✅✅✅ Found base64 file: {base64_file_path}")
                            break
                    
                    if base64_file_path and os.path.exists(base64_file_path):
                        try:
                            print(f"  📁 Loading base64 from file: {base64_file_path} for Party ID: {party_id}")
                            with open(base64_file_path, 'r', encoding='utf-8') as f:
                                file_content = f.read().strip()
                            
                            if file_content:
                                print(f"  ✓ Loaded base64 from file (length: {len(file_content)} chars)")
                                
                                # Handle multiple base64 images in the file (separated by newlines or other delimiters)
                                # Split by common delimiters
                                base64_images = []
                                
                                # Try splitting by double newlines first (common separator)
                                if '\n\n' in file_content:
                                    base64_images = [img.strip() for img in file_content.split('\n\n') if img.strip()]
                                # Try splitting by single newline if no double newlines
                                elif '\n' in file_content:
                                    # Check if it's actually multiple images or just one with line breaks
                                    lines = file_content.split('\n')
                                    # If we have multiple lines and they're all long (likely base64), treat as separate images
                                    if len(lines) > 1 and all(len(line) > 100 for line in lines):
                                        base64_images = [line.strip() for line in lines if line.strip()]
                                    else:
                                        # Single image with line breaks
                                        base64_images = [file_content]
                                else:
                                    # Single image, no line breaks
                                    base64_images = [file_content]
                                
                                print(f"  📷 Found {len(base64_images)} image(s) in file")
                                
                                # Try each image until we find a valid expiry date
                                # IMPORTANT: Try with Party ID matching first, then without matching
                                license_expiry_date = "not identify"
                                for img_idx, base64_content in enumerate(base64_images):
                                    if not base64_content or len(base64_content) < 100:
                                        continue
                                    
                                    try:
                                        print(f"  🔍 Processing image {img_idx + 1}/{len(base64_images)} for Party {party_idx + 1} (ID: {party_id})...")
                                        
                                        # Attempt 1: Extract expiry date WITH Party ID matching
                                        print(f"    🔍 Attempt 1: Extracting with Party ID matching...")
                                        print(f"    🔍 Party ID to match: {party_id}")
                                        print(f"    🔍 Will avoid dates already used in this case: {used_dates_for_case}")
                                        extracted_date = self.extract_license_expiry_from_image(
                                            base64_content, 
                                            target_party_id=party_id if party_id else None
                                        )
                                        
                                        # CRITICAL: Check if extracted date is already used by another party in this case
                                        if extracted_date and extracted_date.strip() != "" and extracted_date != "not identify":
                                            if extracted_date in used_dates_for_case:
                                                print(f"  ⚠️ WARNING: Extracted date {extracted_date} is already used by another party in this case!")
                                                print(f"  ⚠️ Will skip this date and try to find a different one")
                                                # Try to extract all dates and find an unused one
                                                # This is a fallback - ideally pre-matching should handle this
                                                continue  # Try next image or next attempt
                                            else:
                                                license_expiry_date = extracted_date
                                                license_expiry_last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                used_dates_for_case.add(extracted_date)  # Mark as used
                                                print(f"  ✅✅✅ SUCCESS! Found license expiry for Party ID {party_id}: {license_expiry_date}")
                                                print(f"  ✅ Last Updated: {license_expiry_last_updated}")
                                                print(f"  ✅ This will be saved to Excel for Case {case_number}, Party {party_idx + 1}")
                                                break  # Found valid date, stop trying other images
                                        
                                        # Attempt 2: Extract expiry date WITHOUT Party ID matching (extract any expiry date)
                                        if license_expiry_date == "not identify":
                                            print(f"    🔍 Attempt 2: Extracting WITHOUT Party ID matching (extract any expiry date)...")
                                            print(f"    🔍 Will avoid dates already used in this case: {used_dates_for_case}")
                                            extracted_date_no_match = self.extract_license_expiry_from_image(
                                                base64_content, 
                                                target_party_id=None  # Don't match Party ID - extract any expiry date
                                            )
                                            
                                            # CRITICAL: Check if extracted date is already used by another party in this case
                                            if extracted_date_no_match and extracted_date_no_match.strip() != "" and extracted_date_no_match != "not identify":
                                                if extracted_date_no_match in used_dates_for_case:
                                                    print(f"  ⚠️ WARNING: Extracted date {extracted_date_no_match} is already used by another party in this case!")
                                                    print(f"  ⚠️ Will skip this date - need to find a different one")
                                                    continue  # Try next image
                                                else:
                                                    license_expiry_date = extracted_date_no_match
                                                    license_expiry_last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                    used_dates_for_case.add(extracted_date_no_match)  # Mark as used
                                                    print(f"  ✅✅✅ SUCCESS! Found license expiry (without Party ID match): {license_expiry_date}")
                                                    print(f"  ✅ Last Updated: {license_expiry_last_updated}")
                                                    print(f"  ⚠️ NOTE: Party ID didn't match, but expiry date found. Will use this date.")
                                                    print(f"  ✅ This will be saved to Excel for Case {case_number}, Party {party_idx + 1}")
                                                    break  # Found valid date, stop trying other images
                                        
                                        if license_expiry_date == "not identify":
                                            if party_id:
                                                print(f"  ⚠️ Image {img_idx + 1}: No expiry date found (Party ID: {party_id})")
                                            else:
                                                print(f"  ⚠️ Image {img_idx + 1}: No expiry date found")
                                    except Exception as e:
                                        print(f"  ⚠️ Error processing image {img_idx + 1}: {str(e)[:100]}")
                                        import traceback
                                        print(f"  ⚠️ Traceback: {traceback.format_exc()[:300]}")
                                        continue
                                
                                if license_expiry_date == "not identify":
                                    print(f"  ❌ Could not extract expiry date for Party ID {party_id} from any image")
                                    print(f"  ❌ All {len(base64_images)} image(s) processed, no expiry date found")
                            else:
                                print(f"  ⚠️ Base64 file is empty for Party ID {party_id}")
                        except Exception as e:
                            print(f"  ⚠️ Error reading base64 file: {str(e)[:100]}")
                            license_expiry_date = "not identify"
                    else:
                        print(f"  ⚠️ Base64 file not found: {base64_file_path}")
                        license_expiry_date = "not identify"
                else:
                    if not base64_files_path:
                        print(f"  ⚠️ Base64 files path not provided")
                    if not case_number:
                        print(f"  ⚠️ Case number not available")
                
                # Validate and normalize License_Expiry_Date
                # CRITICAL: If date is null, empty, or not exists, set to "not identify"
                if not license_expiry_date or license_expiry_date.strip() == "" or license_expiry_date.lower() in ["null", "none", "n/a", "na"]:
                    print(f"  ⚠️ License_Expiry_Date is null/empty - setting to 'not identify'")
                    license_expiry_gregorian = "not identify"
                    license_expiry_normalized = "not identify"
                else:
                    # Normalize License_Expiry_Date format first (handle YYYYMMDD format)
                    print(f"  🔄 STEP 1: Normalizing License Expiry Date...")
                    print(f"     Input: '{license_expiry_date}'")
                    license_expiry_normalized = self.normalize_date_format(license_expiry_date)
                    print(f"     Output (normalized): '{license_expiry_normalized}'")
                    
                    # Convert License_Expiry_Date from Hijri to Gregorian if needed
                    print(f"  🔄 STEP 2: Converting License Expiry Date (Hijri to Gregorian if needed)...")
                    print(f"     Input: '{license_expiry_normalized}'")
                    license_expiry_gregorian = self.convert_hijri_to_gregorian(license_expiry_normalized)
                    print(f"     Output (Gregorian): '{license_expiry_gregorian}'")
                    
                    # STEP 3: Validate the final Gregorian date
                    print(f"  🔄 STEP 3: Validating License Expiry Date...")
                    if license_expiry_gregorian and license_expiry_gregorian.strip() and license_expiry_gregorian != "not identify":
                        # Check if date is valid and reasonable
                        try:
                            # Parse the date to validate it
                            from datetime import datetime as dt
                            if re.match(r'^\d{4}-\d{2}-\d{2}$', license_expiry_gregorian):
                                date_parts = license_expiry_gregorian.split('-')
                                year = int(date_parts[0])
                                month = int(date_parts[1])
                                day = int(date_parts[2])
                                
                                # Validate date components
                                if not (1 <= month <= 12 and 1 <= day <= 31):
                                    print(f"  ⚠️ Invalid date format: month={month}, day={day} - setting to 'not identify'")
                                    license_expiry_gregorian = "not identify"
                                elif year < 1900 or year > 2100:
                                    # Year is out of reasonable range
                                    if year > 2100:
                                        print(f"  ⚠️ Date is too far in future (year={year}) - likely OCR error, setting to 'not identify'")
                                        license_expiry_gregorian = "not identify"
                                elif year < 1900:
                                    print(f"  ⚠️ Date is too old (year={year}, current={current_year})")
                                    print(f"  ⚠️ REASON: License expiry dates should not be before 1900")
                                    print(f"  ⚠️ This is likely an OCR error (possibly misread Hijri as Gregorian) - setting to 'not identify'")
                                    license_expiry_gregorian = "not identify"
                                else:
                                    # Try to create a datetime object to validate the date
                                    try:
                                        parsed_date = dt(year, month, day)
                                        current_date = dt.now()
                                        current_year = current_date.year
                                        
                                        # CRITICAL: Check if date is in the past (invalid for license expiry)
                                        # Allow small tolerance (e.g., 7 days) in case of processing delays or timezone issues
                                        days_difference = (parsed_date - current_date).days
                                        if days_difference < -7:  # More than 7 days in the past
                                            print(f"  ⚠️ Date {license_expiry_gregorian} is {abs(days_difference)} days in the past (current: {current_date.strftime('%Y-%m-%d')}) - invalid for license expiry")
                                            print(f"  ⚠️ REASON: License expiry date must be in the future (or very recent past < 7 days)")
                                            print(f"  ⚠️ Setting to 'not identify'")
                                            license_expiry_gregorian = "not identify"
                                        elif days_difference < 0:  # Past but within 7 days tolerance
                                            print(f"  ⚠️ Date {license_expiry_gregorian} is {abs(days_difference)} days in the past (within 7-day tolerance)")
                                            print(f"  ⚠️ ACCEPTING: License may have expired recently or timezone issue")
                                            print(f"  ✅ Valid date (within tolerance): {license_expiry_gregorian}")
                                        elif year > current_year + 50:  # Check if date is unreasonably far in the future (> 50 years)
                                            print(f"  ⚠️ Date is too far in future (year={year}, current={current_year}, difference={year - current_year} years)")
                                            print(f"  ⚠️ REASON: License expiry dates should not be more than 50 years in the future")
                                            print(f"  ⚠️ This is likely an OCR error - setting to 'not identify'")
                                            license_expiry_gregorian = "not identify"
                                        else:
                                            print(f"  ✅ Valid date in future: {license_expiry_gregorian} (year={year}, month={month}, day={day})")
                                    except ValueError as ve:
                                        print(f"  ⚠️ Invalid date (ValueError: {str(ve)}) - setting to 'not identify'")
                                        license_expiry_gregorian = "not identify"
                            else:
                                # Date format is not YYYY-MM-DD after conversion
                                print(f"  ⚠️ Invalid date format (not YYYY-MM-DD): '{license_expiry_gregorian}' - setting to 'not identify'")
                                license_expiry_gregorian = "not identify"
                        except Exception as e:
                            print(f"  ⚠️ Error validating date '{license_expiry_gregorian}': {str(e)[:100]} - setting to 'not identify'")
                            license_expiry_gregorian = "not identify"
                    else:
                        # Date is empty or "not identify" - keep as is
                        if not license_expiry_gregorian or license_expiry_gregorian.strip() == "":
                            print(f"  ⚠️ License_Expiry_Date is empty after conversion - setting to 'not identify'")
                            license_expiry_gregorian = "not identify"
                        else:
                            print(f"  ℹ️ License_Expiry_Date is already 'not identify'")
                    
                    print(f"  📝 Final validated License_Expiry_Date: '{license_expiry_gregorian}'")
                
                # Extract accident date from accident details
                accident_date = accident_details.get("Call_Date", "")
                # Also try alternative field names for accident date
                if not accident_date:
                    accident_date = accident_info.get("accidentDate", accident_info.get("accident_date", ""))
                if not accident_date:
                    # Try to get from JSON data directly
                    if isinstance(json_data, dict):
                        accident_date = json_data.get("accident_date", json_data.get("accidentDate", ""))
                
                # Normalize Accident_Date format (handle YYYYMMDD format like "20251119" -> "2025-11-19")
                print(f"  🔄 STEP 3: Normalizing Accident Date...")
                print(f"     Input: '{accident_date}'")
                accident_date_normalized = self.normalize_date_format(str(accident_date) if accident_date else "")
                print(f"     Output (normalized): '{accident_date_normalized}'")
                
                # Convert Accident_Date from Hijri to Gregorian if needed
                print(f"  🔄 STEP 4: Converting Accident Date (Hijri to Gregorian if needed)...")
                print(f"     Input: '{accident_date_normalized}'")
                accident_date_final = self.convert_hijri_to_gregorian(accident_date_normalized)
                print(f"     Output (Gregorian): '{accident_date_final}'")
                
                # License Type extraction DISABLED - always returns "not identify"
                license_type = "not identify"
                upload_date = "not identify"
                
                # Upload date extraction (License Type extraction completely disabled)
                ocr_text_for_extraction_local = case_ocr_text
                if not ocr_text_for_extraction_local and base64_files_path and case_number:
                    # Try to get OCR text from pre-extraction for upload date only
                    try:
                        case_clean = str(case_number).strip()
                        base64_file_path = os.path.join(base64_files_path, f"{case_clean}.txt")
                        if os.path.exists(base64_file_path):
                            with open(base64_file_path, 'r', encoding='utf-8') as f:
                                file_content = f.read().strip()
                            if file_content:
                                # Quick OCR extraction for upload date only (License Type extraction disabled)
                                from PIL import Image
                                from io import BytesIO
                                import base64
                                base64_images = [file_content] if '\n\n' not in file_content else file_content.split('\n\n')
                                if base64_images:
                                    img_bytes = base64.b64decode(base64_images[0].split(',')[-1] if ',' in base64_images[0] else base64_images[0])
                                    image = Image.open(BytesIO(img_bytes))
                                    if image.mode != 'RGB':
                                        image = image.convert('RGB')
                                    ocr_text_temp = pytesseract.image_to_string(image, lang='ara+eng', config='--psm 6 --oem 3')
                                    ocr_text_for_extraction_local = self.translate_ocr_to_english(ocr_text_temp)
                    except Exception as e:
                        print(f"  ⚠️ Could not extract OCR for Upload Date: {str(e)[:100]}")
                
                if ocr_text_for_extraction_local and party_id:
                    # Extract upload date only (License Type extraction completely disabled)
                    upload_date_raw = self.extract_upload_date(ocr_text_for_extraction_local, party_id)
                    if upload_date_raw != "not identify":
                        upload_date_normalized = self.normalize_date_format(upload_date_raw)
                        upload_date = self.convert_hijri_to_gregorian(upload_date_normalized)
                    print(f"  📋 License Type extraction DISABLED - always returns 'not identify'")
                    print(f"  📋 Extracted Upload Date: {upload_date}")
                
                # Debug: Print what will be saved to Excel
                print(f"  📝 Saving to Excel:")
                print(f"     Case_Number: {case_number}")
                print(f"     Party: {party_idx + 1}")
                print(f"     Party_ID: {party_info.get('Party_ID', '')}")
                # License_Type removed - not generated
                print(f"     License_Expiry_Date (original): {license_expiry_date}")
                print(f"     License_Expiry_Date (normalized): {license_expiry_normalized}")
                print(f"     License_Expiry_Date (Gregorian): {license_expiry_gregorian}")
                print(f"     Upload_Date: {upload_date}")
                print(f"     Accident_Date (original): {accident_date}")
                print(f"     Accident_Date (normalized): {accident_date_normalized}")
                print(f"     Accident_Date (final): {accident_date_final}")
                print(f"     License_Expiry_Last_Updated: {license_expiry_last_updated}")
                
                # Clean Party_ID - remove any Arabic characters that might have been appended
                party_id_clean = str(party_info.get("Party_ID", "")).strip()
                # Remove Arabic characters, keep only digits
                party_id_clean = re.sub(r'[^\d]', '', party_id_clean)
                if not party_id_clean:
                    party_id_clean = str(party_info.get("Party_ID", "")).strip()
                
                # Extract carMake and carModel from party_raw_data (original request data)
                car_make = party_raw_data.get("carMake", party_raw_data.get("car_make", "")) if party_raw_data else ""
                car_model = party_raw_data.get("carModel", party_raw_data.get("car_model", "")) if party_raw_data else ""
                # Fallback to party_info if not found in raw data
                if not car_make:
                    car_make = party_info.get("Vehicle_Make", "")
                if not car_model:
                    car_model = party_info.get("Vehicle_Model", "")
                
                # Lookup License_Type_From_Make_Model from Excel mapping file
                license_type_from_mapping = ""
                if car_make and car_model:
                    license_type_from_mapping = self.lookup_license_type_from_make_model(car_make, car_model)
                    if license_type_from_mapping:
                        print(f"  ✅ Party {party_idx + 1}: License_Type_From_Make_Model = {license_type_from_mapping} (Make: {car_make}, Model: {car_model})")
                    else:
                        print(f"  ⚠️ Party {party_idx + 1}: No License_Type_From_Make_Model found (Make: {car_make}, Model: {car_model})")
                
                # Add DAA values from Request data (extracted earlier)
                result_dict = {
                    "Case_Number": str(case_number),
                    "Party": party_idx + 1,
                    "Party_ID": party_id_clean,
                    "Party_Name": str(party_info.get("Name", "")).strip(),
                    "Insurance_Name": str(party_info.get("Insurance_Name", "")).strip(),
                    "ICEnglishName": str(party_info.get("ICEnglishName", "")).strip(),
                    "Liability": int(party_info.get("Liability", 0)),
                    "Vehicle_Serial": str(party_info.get("Chassis_No", "")).strip(),
                    "VehicleOwnerId": str(party_info.get("VehicleOwnerId", "")).strip(),
                    "License_Type_From_Request": str(party_info.get("License_Type_From_Request", "")).strip(),
                    "Recovery": str(party_info.get("Recovery", "")).strip(),
                    "License_Expiry_Date": str(license_expiry_gregorian).strip() if license_expiry_gregorian != "not identify" else "not identify",
                    "Upload_Date": str(upload_date).strip() if upload_date != "not identify" else "not identify",
                    "License_Expiry_Last_Updated": str(license_expiry_last_updated).strip(),
                    "Accident_Date": str(accident_date_final).strip() if accident_date_final else "",
                    "carMake": str(car_make).strip() if car_make else "",
                    "carModel": str(car_model).strip() if car_model else "",
                    "License_Type_From_Make_Model": str(license_type_from_mapping).strip() if license_type_from_mapping else "",
                    "Full_Analysis": str(full_analysis.strip())[:10000] if full_analysis else "",
                    "Full_Analysis_English": str(full_analysis_english.strip())[:10000] if full_analysis_english else (str(full_analysis.strip())[:10000] if full_analysis else ""),
                    "Decision": str(decision).strip(),
                    "Classification": str(classification).strip(),
                    "Description": str(description)[:5000].strip() if isinstance(description, str) else str(description)[:5000].strip(),
                    # Add DAA values from Request data (will be merged with Excel columns later)
                    "isDAA": daa_from_request.get('isDAA'),
                    "Suspect_as_Fraud": daa_from_request.get('Suspect_as_Fraud'),
                    "DaaReasonEnglish": daa_from_request.get('DaaReasonEnglish')
                }
                results.append(result_dict)
            
        except Exception as e:
            error_msg = str(e)
            print(f"  ✗ Unexpected error (Row {row_num + 1}): {error_msg[:200]}")
            results.append({
                "Case_Number": f"ERROR_ROW_{row_num + 1}",
                "Party": 0,
                "Party_ID": "",
                "Party_Name": "",
                "Insurance_Name": "",
                "Liability": 0,
                "Vehicle_Serial": "",
                "VehicleOwnerId": "",
                "License_Type_From_Request": "",
                "Recovery": "",
                "License_Expiry_Date": "not identify",
                "License_Expiry_Last_Updated": "",
                "Accident_Date": "",
                    "carMake": "",
                    "carModel": "",
                    "License_Type_From_Make_Model": "",
                    "Full_Analysis": "",
                    "Full_Analysis_English": "",
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Description": f"Unexpected error: {error_msg[:200]}"
            })
        
        return results
    
    def process_excel_to_results(self, file_path: str,
                                 request_column: str = None,
                                 base64_files_path: str = None,
                                 output_file: str = None,
                                 start_row: int = 0,
                                 end_row: int = None,
                                 max_workers: int = None,
                                 enable_translation: bool = False) -> pd.DataFrame:
        """
        Process Excel file - automatically detects column and format
        Converts to JSON first, then processes
        """
        # Read Excel
        try:
            df = pd.read_excel(file_path, sheet_name=0)
        except Exception as e:
            raise ValueError(f"Error reading Excel file: {str(e)}")
        
        # Clean column names (remove leading/trailing spaces)
        df.columns = df.columns.str.strip()
        
        # Extract DAA-related columns from original Excel if they exist
        daa_columns = {}
        isDAA_col = None
        suspect_fraud_col = None
        daa_reason_col = None
        
        # Find DAA columns (case-insensitive)
        for col in df.columns:
            col_lower = col.strip().lower()
            if col_lower == 'isdaa' or col_lower == 'is_daa':
                isDAA_col = col
            elif col_lower == 'suspect_as_fraud' or col_lower == 'suspectasfraud':
                suspect_fraud_col = col
            elif col_lower == 'daareasonenglish' or col_lower == 'daa_reason_english':
                daa_reason_col = col
        
        # Store DAA data for later merging (by row index, will match by Case_Number later)
        # Also try to extract Case_Number from original Excel for better matching
        case_number_col = None
        for col in df.columns:
            col_lower = col.strip().lower()
            if col_lower in ['case_number', 'casenumber', 'case number']:
                case_number_col = col
                break
        
        if isDAA_col or suspect_fraud_col or daa_reason_col:
            print(f"✓ Found DAA columns in Excel:")
            if isDAA_col:
                print(f"  - isDAA: {isDAA_col}")
            if suspect_fraud_col:
                print(f"  - Suspect_as_Fraud: {suspect_fraud_col}")
            if daa_reason_col:
                print(f"  - DaaReasonEnglish: {daa_reason_col}")
            if case_number_col:
                print(f"  - Case_Number (for matching): {case_number_col}")
            
            # Store DAA data indexed by row number and Case_Number (for matching)
            for idx in df.index:
                daa_data = {}
                if isDAA_col and idx < len(df):
                    isDAA_value = df.at[idx, isDAA_col] if pd.notna(df.at[idx, isDAA_col]) else None
                    daa_data['isDAA'] = str(isDAA_value).strip() if isDAA_value is not None else None
                if suspect_fraud_col and idx < len(df):
                    daa_data['Suspect_as_Fraud'] = df.at[idx, suspect_fraud_col] if pd.notna(df.at[idx, suspect_fraud_col]) else None
                if daa_reason_col and idx < len(df):
                    daa_data['DaaReasonEnglish'] = df.at[idx, daa_reason_col] if pd.notna(df.at[idx, daa_reason_col]) else None
                if case_number_col and idx < len(df):
                    case_num = df.at[idx, case_number_col] if pd.notna(df.at[idx, case_number_col]) else None
                    daa_data['Case_Number_Match'] = str(case_num).strip() if case_num is not None else None
                daa_columns[idx] = daa_data
        else:
            print("⚠ No DAA columns found in Excel (isDAA, Suspect_as_Fraud, DaaReasonEnglish)")
        
        # Find request column if not specified
        if request_column is None:
            request_column = self.find_request_column(df)
            if request_column is None:
                raise ValueError(f"Could not find request column. Available columns: {list(df.columns)}")
            print(f"Auto-detected column: '{request_column}'")
        else:
            # Clean and find column
            df.columns = df.columns.str.strip()
            request_column_clean = request_column.strip()
            if request_column_clean not in df.columns:
                # Try case-insensitive
                matching_cols = [col for col in df.columns if col.strip().lower() == request_column_clean.lower()]
                if matching_cols:
                    request_column = matching_cols[0]
                else:
                    # Try auto-detect
                    request_column = self.find_request_column(df)
                    if request_column is None:
                        raise ValueError(f"Column '{request_column_clean}' not found. Available: {list(df.columns)}")
        
        # Determine rows to process
        if end_row is None:
            end_row = len(df)
        else:
            end_row = min(end_row, len(df))
        
        rows_to_process = range(start_row, end_row)
        total_rows = len(rows_to_process)
        
        # Auto-calculate optimal max_workers if not specified
        # CRITICAL: Drastically reduced workers to prevent Ollama overload and timeouts
        # Large models like qwen2.5:14b need more time and can't handle many parallel requests
        if max_workers is None:
            import multiprocessing
            cpu_count = multiprocessing.cpu_count()
            # Dynamic scaling based on number of rows
            # For large models (qwen2.5:14b), use EXTREMELY conservative worker counts
            # Model size matters: larger models = fewer parallel workers
            # CRITICAL: qwen2.5:14b can only handle 2-3 concurrent requests reliably
            # Even 6 workers causes complete timeout cascade - must use 3-4 max
            if total_rows >= 100:
                # For 100+ accidents: use only 4 workers (was 6 - still too many!)
                max_workers = 4  # Fixed at 4 for large batches
            elif total_rows >= 50:
                # For 50-99 accidents: use 3 workers (was 5)
                max_workers = 3  # Fixed at 3 for medium batches
            elif total_rows >= 20:
                # For 20-49 accidents: use 3 workers (was 4)
                max_workers = 3  # Fixed at 3
            else:
                # For smaller batches: use 2-3 workers max
                max_workers = min(3, total_rows)
        
        # CRITICAL: Cap at 4 workers maximum for large models to prevent timeouts
        # For qwen2.5:14b (9GB model), 4 workers is the absolute maximum
        # If still timing out, manually set --max-workers 2 or 3
        max_workers = min(max_workers, 4)
        
        # Detect if using large model and adjust warning
        model_name = getattr(self.processor, 'model_name', 'qwen2.5:14b')
        is_large_model = '14b' in str(model_name) or '13b' in str(model_name) or 'gpt-oss' in str(model_name)
        
        print(f"⚡ Performance Mode: {total_rows} rows → {max_workers} parallel workers")
        if is_large_model:
            print(f"⚠️  CRITICAL: Using large model ({model_name}) - workers set to {max_workers} to prevent timeouts")
            print(f"💡 If still timing out, manually set --max-workers 2 or 3")
            print(f"💡 Large models like {model_name} can only handle 2-3 concurrent requests reliably")
            print(f"💡 For faster processing, consider: llama3.1:latest (4.9GB) instead of {model_name}")
        
        print(f"\n{'=' * 60}")
        print(f"⚡ HIGH-PERFORMANCE PARALLEL PROCESSING")
        print(f"{'=' * 60}")
        print(f"Processing {total_rows} claims...")
        print(f"Using parallel processing with {max_workers} workers")
        print(f"Translation: {'ENABLED' if enable_translation else 'DISABLED (faster processing)'}")
        if total_rows >= 100:
            print(f"🚀 Large batch mode: Optimized for maximum speed")
        print("=" * 60)
        
        all_results = []
        results_lock = threading.Lock()
        
        # Set default base64 files path if not provided
        if base64_files_path is None:
            # Get base directory
            base_dir = getattr(self, 'base_dir', None)
            if base_dir is None:
                # Auto-detect base directory
                env_dir = os.getenv("MOTORCLAIM_BASE_DIR")
                if env_dir and os.path.exists(env_dir):
                    base_dir = env_dir
                elif os.name == 'nt':  # Windows
                    base_dir = r"D:\Motorclaimdecisionlinux" if os.path.exists(r"D:\Motorclaimdecisionlinux") else os.path.dirname(os.path.abspath(__file__))
                else:  # Linux
                    base_dir = "/opt/motorclaimdecision" if os.path.exists("/opt/motorclaimdecision") else os.path.dirname(os.path.abspath(__file__))
            
            # Try multiple possible locations (Windows and Linux)
            possible_paths = [
                # Relative to base_dir
                os.path.join(base_dir, "base64_files", "base64_files"),
                os.path.join(base_dir, "base64_files"),
                # Windows dev paths
                r"D:\Motorclaimdecision\base64_files\base64_files",
                r"D:\Motorclaimdecisionlinux\base64_files\base64_files",
                # Linux production paths
                "/opt/motorclaimdecision/base64_files/base64_files",
                "/opt/motorclaimdecision/base64_files"
            ]
            for default_path in possible_paths:
                if os.path.exists(default_path):
                    base64_files_path = default_path
                    print(f"\n✓ Using base64 files path: {base64_files_path}")
                    break
            else:
                print(f"\n⚠️ Base64 files path not found. Tried: {possible_paths}")
                base64_files_path = None
        else:
            if os.path.exists(base64_files_path):
                print(f"\n✓ Using provided base64 files path: {base64_files_path}")
            else:
                print(f"\n⚠️ Provided base64 files path not found: {base64_files_path}")
                base64_files_path = None
        
        # Prepare tasks
        tasks = []
        for idx, row_num in enumerate(rows_to_process):
            claim_data = df.at[row_num, request_column]
            tasks.append((idx, row_num, claim_data))
        
        # Process in parallel
        start_time = datetime.now()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            # Store enable_translation flag for use in _process_single_row
            self._enable_translation = enable_translation
            
            future_to_task = {
                executor.submit(self._process_single_row, row_num, claim_data, request_column, total_rows, idx, base64_files_path): (idx, row_num)
                for idx, row_num, claim_data in tasks
            }
            
            # Collect results as they complete (optimized for large batches)
            completed = 0
            last_progress_time = start_time
            
            for future in as_completed(future_to_task):
                idx, row_num = future_to_task[future]
                try:
                    row_results = future.result()
                    with results_lock:
                        all_results.extend(row_results)
                    completed += 1
                    
                    # Progress reporting - more frequent for large batches
                    current_time = datetime.now()
                    elapsed = (current_time - start_time).total_seconds()
                    
                    # For large batches, show progress every 10% or every 10 seconds
                    if total_rows >= 50:
                        if completed % max(1, total_rows // 10) == 0 or (current_time - last_progress_time).total_seconds() >= 10:
                            rate = completed / elapsed if elapsed > 0 else 0
                            remaining = total_rows - completed
                            eta_seconds = remaining / rate if rate > 0 else 0
                            eta_minutes = eta_seconds / 60
                            print(f"  ⚡ Progress: {completed}/{total_rows} ({completed*100//total_rows}%) | "
                                  f"Speed: {rate:.1f} rows/sec | ETA: {eta_minutes:.1f} min")
                            last_progress_time = current_time
                    else:
                        # For smaller batches, show every completion
                        print(f"  ✓ Completed {completed}/{total_rows} rows")
                        
                except Exception as e:
                    error_msg = str(e)
                    print(f"  ✗ Error processing row {row_num + 1}: {error_msg[:200]}")
                    with results_lock:
                        all_results.append({
                            "Case_Number": f"ERROR_ROW_{row_num + 1}",
                            "Party": 0,
                            "Party_ID": "",
                            "Party_Name": "",
                            "Insurance_Name": "",
                            "ICEnglishName": "",
                            "Liability": 0,
                            "Vehicle_Serial": "",
                            "VehicleOwnerId": "",
                            "License_Type_From_Request": "",
                            "Recovery": "",
                            "License_Expiry_Date": "not identify",
                            "License_Expiry_Last_Updated": "",
                            "Accident_Date": "",
                            "carMake": "",
                            "carModel": "",
                            "Full_Analysis": "",
                            "Full_Analysis_English": "",
                            "Decision": "ERROR",
                            "Classification": "ERROR",
                            "Description": f"Thread error: {error_msg[:200]}"
                        })
        
        
        # Create DataFrame
        total_time = (datetime.now() - start_time).total_seconds()
        results_df = pd.DataFrame(all_results)
        
        if len(results_df) == 0:
            print("\n⚠ No results to save!")
            return results_df
        
        # Merge DAA columns from original Excel if available
        # Priority: Request data (from JSON/XML) > Excel columns
        if daa_columns and len(daa_columns) > 0:
            print(f"\n{'=' * 60}")
            print(f"📋 Merging DAA columns from original Excel...")
            print(f"  Note: DAA values from Request data take priority over Excel columns")
            print(f"{'=' * 60}")
            
            # Check if DAA columns already exist (from Request data extraction)
            if 'isDAA' not in results_df.columns:
                results_df['isDAA'] = None
            if 'Suspect_as_Fraud' not in results_df.columns:
                results_df['Suspect_as_Fraud'] = None
            if 'DaaReasonEnglish' not in results_df.columns:
                results_df['DaaReasonEnglish'] = None
            
            # Try to match by Case_Number first, then by row index
            matched_count = 0
            import re
            for idx, row in results_df.iterrows():
                case_number = str(row.get('Case_Number', '')).strip()
                matched = False
                
                # Get current values (may already be set from Request data)
                current_isDAA = results_df.at[idx, 'isDAA']
                current_suspect = results_df.at[idx, 'Suspect_as_Fraud']
                current_reason = results_df.at[idx, 'DaaReasonEnglish']
                
                # Try matching by Case_Number first (if Case_Number exists in original Excel)
                if case_number:
                    for excel_row_idx, daa_data in daa_columns.items():
                        case_match = daa_data.get('Case_Number_Match', '')
                        if case_match and str(case_match).strip() == case_number:
                            # Only use Excel data if Request data is None/empty
                            if current_isDAA is None or str(current_isDAA).strip() == "" or str(current_isDAA).strip().lower() == "none":
                                results_df.at[idx, 'isDAA'] = daa_data.get('isDAA')
                            if current_suspect is None or str(current_suspect).strip() == "" or str(current_suspect).strip().lower() == "none":
                                results_df.at[idx, 'Suspect_as_Fraud'] = daa_data.get('Suspect_as_Fraud')
                            if current_reason is None or str(current_reason).strip() == "" or str(current_reason).strip().lower() == "none":
                                results_df.at[idx, 'DaaReasonEnglish'] = daa_data.get('DaaReasonEnglish')
                            matched = True
                            matched_count += 1
                            break
                
                # If not matched by Case_Number, try matching by row index
                # Extract row number from Case_Number if it's in format ERROR_ROW_X or Case_X
                if not matched:
                    row_match = re.search(r'ROW_(\d+)|Case_(\d+)', case_number)
                    if row_match:
                        excel_row_num = int(row_match.group(1) or row_match.group(2))
                        # Adjust for 0-based vs 1-based indexing (Excel rows are 1-based, Python is 0-based)
                        if excel_row_num > 0:
                            excel_row_idx = excel_row_num - 1
                            if excel_row_idx in daa_columns:
                                daa_data = daa_columns[excel_row_idx]
                                # Only use Excel data if Request data is None/empty
                                if current_isDAA is None or str(current_isDAA).strip() == "" or str(current_isDAA).strip().lower() == "none":
                                    results_df.at[idx, 'isDAA'] = daa_data.get('isDAA')
                                if current_suspect is None or str(current_suspect).strip() == "" or str(current_suspect).strip().lower() == "none":
                                    results_df.at[idx, 'Suspect_as_Fraud'] = daa_data.get('Suspect_as_Fraud')
                                if current_reason is None or str(current_reason).strip() == "" or str(current_reason).strip().lower() == "none":
                                    results_df.at[idx, 'DaaReasonEnglish'] = daa_data.get('DaaReasonEnglish')
                                matched = True
                                matched_count += 1
            
            print(f"✓ Matched DAA data for {matched_count} out of {len(results_df)} result rows")
            
            # Create new "Suspected Fraud" column based on isDAA
            def create_suspected_fraud(row):
                isDAA_value = str(row.get('isDAA', '')).strip().upper() if pd.notna(row.get('isDAA')) else ''
                if isDAA_value == 'TRUE':
                    return 'Suspected Fraud'
                else:
                    return None
            
            results_df['Suspected_Fraud'] = results_df.apply(create_suspected_fraud, axis=1)
            print(f"✓ Created 'Suspected_Fraud' column based on isDAA values")
            print(f"{'=' * 60}\n")
        else:
            # Initialize DAA columns as None even if not found in Excel
            results_df['isDAA'] = None
            results_df['Suspect_as_Fraud'] = None
            results_df['DaaReasonEnglish'] = None
            results_df['Suspected_Fraud'] = None
        
        # Performance summary
        print(f"\n{'=' * 60}")
        print(f"⚡ PERFORMANCE SUMMARY")
        print(f"{'=' * 60}")
        print(f"Total rows processed: {total_rows}")
        print(f"Total parties processed: {len(results_df)}")
        print(f"Total time: {total_time:.1f} seconds ({total_time/60:.1f} minutes)")
        print(f"Average speed: {total_rows/total_time:.2f} rows/second")
        if total_rows >= 20:
            print(f"Parallel efficiency: {max_workers} workers")
        print(f"{'=' * 60}")
        
        # Reorder columns as requested
        # Column order - keep names short to avoid Excel truncation
        column_order = [
            "Case_Number", 
            "Party", 
            "Party_ID", 
            "Party_Name",
            "Insurance_Name",
            "ICEnglishName",
            "Liability", 
            "Vehicle_Serial", 
            "VehicleOwnerId",
            "License_Type_From_Request",
            "Recovery",
            "License_Expiry_Date", 
            "Upload_Date", 
            "License_Expiry_Last_Updated", 
            "Accident_Date", 
            "carMake",
            "carModel",
            "License_Type_From_Make_Model",
            "Decision", 
            "Classification", 
            "Description",
            "isDAA",  # DAA columns from original Excel
            "Suspect_as_Fraud",
            "DaaReasonEnglish",
            "Suspected_Fraud",  # New column: "Suspected Fraud" if isDAA is TRUE, else null
            "Full_Analysis",  # Move to end to avoid truncation
            "Full_Analysis_English"  # English translation of Full_Analysis
        ]
        # Only include columns that exist
        available_columns = [col for col in column_order if col in results_df.columns]
        results_df = results_df[available_columns]
        
        # Save to Excel
        if output_file is None:
            base_name = os.path.splitext(file_path)[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"{base_name}_results_{timestamp}.xlsx"
        
        try:
            results_df.to_excel(output_file, index=False, engine='openpyxl')
            print(f"\n{'=' * 60}")
            print(f"✓ Results saved to: {output_file}")
            print(f"✓ Total parties processed: {len(results_df)}")
            print(f"{'=' * 60}")
        except Exception as e:
            print(f"\n✗ Error saving Excel: {str(e)}")
            csv_file = output_file.replace('.xlsx', '.csv')
            results_df.to_csv(csv_file, index=False, encoding='utf-8-sig')
            print(f"  Saved as CSV: {csv_file}")
        
        return results_df


def main():
    """Example usage"""
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='Unified Claim Processor - Handles XML/JSON automatically')
    parser.add_argument('excel_file', help='Path to Excel file with claims')
    parser.add_argument('output_file', nargs='?', default=None, help='Output Excel file path (optional)')
    parser.add_argument('--request-column', default=None, help='Column name (auto-detected if not specified)')
    parser.add_argument('--model', default='qwen2.5:14b', help='Ollama model for DECISION making (default: qwen2.5:14b)')
    parser.add_argument('--translation-model', default='llama3.2:latest', dest='translation_model', help='Ollama model for TRANSLATION - fastest model (default: llama3.2:latest - 2.0 GB, very fast). Options: llama3.2:latest (fastest), llama3.1:latest, llama3:8b')
    parser.add_argument('--start-row', type=int, default=0, help='Starting row index')
    parser.add_argument('--end-row', type=int, default=None, help='Ending row index')
    parser.add_argument('--max-workers', type=int, default=None, help='Number of parallel workers (default: auto-calculated dynamically: 100+ rows → up to 100 workers, 50-99 → up to 60, 20-49 → up to 40, smaller → CPU*2, max 100)')
    parser.add_argument('--enable-translation', action='store_true', help='Enable Full_Analysis translation to English (slower, disabled by default)')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Unified Claim Processor")
    print("معالج موحد للمطالبات")
    print("=" * 60)
    print(f"\nInput File: {args.excel_file}")
    print(f"Decision Model: {args.model}")
    print(f"Translation Model: {args.translation_model}")
    if args.request_column:
        print(f"Request Column: {args.request_column}")
    else:
        print("Request Column: Auto-detect")
    print("=" * 60)
    print()
    
    try:
        processor = UnifiedClaimProcessor(model_name=args.model, translation_model=args.translation_model)
        
        # Get base64 files path - auto-detects Windows dev or Linux production
        base64_path = os.getenv("BASE64_FILES_PATH", None)
        if base64_path is None:
            # Auto-detect base directory
            env_dir = os.getenv("MOTORCLAIM_BASE_DIR")
            if env_dir and os.path.exists(env_dir):
                base_dir = env_dir
            elif os.name == 'nt':  # Windows
                base_dir = r"D:\Motorclaimdecisionlinux" if os.path.exists(r"D:\Motorclaimdecisionlinux") else os.path.dirname(os.path.abspath(__file__))
            else:  # Linux
                base_dir = "/opt/motorclaimdecision" if os.path.exists("/opt/motorclaimdecision") else os.path.dirname(os.path.abspath(__file__))
            
            # Try common locations (Windows and Linux)
            possible_paths = [
                # Relative to base_dir
                os.path.join(base_dir, "base64_files", "base64_files"),
                os.path.join(base_dir, "base64_files"),
                # Windows dev paths
                r"D:\Motorclaimdecision\base64_files\base64_files",
                r"D:\Motorclaimdecisionlinux\base64_files\base64_files",
                # Linux production paths
                "/opt/motorclaimdecision/base64_files/base64_files",
                "/opt/motorclaimdecision/base64_files"
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    base64_path = path
                    break
        
        df = processor.process_excel_to_results(
            file_path=args.excel_file,
            request_column=args.request_column,
            base64_files_path=base64_path,
            output_file=args.output_file,
            start_row=args.start_row,
            end_row=args.end_row,
            max_workers=args.max_workers,
            enable_translation=args.enable_translation
        )
        
        if len(df) > 0:
            print(f"\nSummary:")
            print(f"  Total parties: {len(df)}")
            print(f"  Unique cases: {df['Case_Number'].nunique()}")
            print(f"  Decisions: {df['Decision'].value_counts().to_dict()}")
        
    except Exception as e:
        print(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

