"""
Motor Claim Decision System using Ollama
Processes accident claim information (XML/JSON) and returns decisions based on rules
"""

import json
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List
import requests
from datetime import datetime
from config_manager import config_manager


class ClaimProcessor:
    """Processes motor claims using Ollama model"""
    
    def __init__(self, ollama_base_url: str = "http://localhost:11434", model_name: str = "qwen2.5:14b", 
                 translation_model: str = "llama3.2:latest", check_ollama_health: bool = True, 
                 prewarm_model: bool = True):
        """
        Initialize the claim processor
        
        Args:
            ollama_base_url: Base URL for Ollama API (default: http://localhost:11434)
            model_name: Name of the Ollama model for DECISION making (default: qwen2.5:14b)
                          Recommended models for Arabic claim processing (التعاونية للتأمين):
                          - qwen2.5:14b (9.0 GB) - ⭐ BEST for Arabic - Excellent multilingual support
                          - gpt-oss:latest (13 GB) - Very capable, larger model
                          - llama3.1:latest (4.9 GB) - Good balance, moderate Arabic support
                          - llama3:8b (4.7 GB) - Faster but less accurate for Arabic
            translation_model: Name of the Ollama model for TRANSLATION (default: llama3.2:latest)
                          Fast translation models (if translation needed):
                          - llama3.2:latest (2.0 GB) - ⚡ FASTEST - Smallest, very fast
                          - llama3.1:latest (4.9 GB) - Good balance of speed/accuracy
                          - llama3:8b (4.7 GB) - Fast, good accuracy
                          - qwen2.5:14b (9.0 GB) - Slower but more accurate
            check_ollama_health: If True, verify Ollama is running on initialization (default: True)
            prewarm_model: If True, pre-warm the model on initialization to keep it loaded (default: True)
        """
        self.ollama_base_url = ollama_base_url
        self.model_name = model_name  # For decision making
        self.translation_model = translation_model  # For translation (faster model)
        # Load rules from config manager (dynamically)
        self.rules = self._load_rules()
        
        # Optional health check
        if check_ollama_health:
            try:
                self.check_ollama_health()
            except Exception as e:
                print(f"⚠️ Warning: Ollama health check failed: {str(e)[:100]}")
                print(f"⚠️ Processing may fail if Ollama is not running. Start Ollama with: ollama serve")
        
        # Pre-warm model to keep it loaded in memory for faster responses
        if prewarm_model:
            try:
                self._prewarm_model()
            except Exception as e:
                print(f"⚠️ Warning: Model pre-warming failed: {str(e)[:100]}")
                print(f"⚠️ First request may be slower as model needs to load")
    
    def _prewarm_model(self):
        """Pre-warm the model by sending a small request to keep it loaded in memory"""
        try:
            import threading
            def prewarm():
                try:
                    url = f"{self.ollama_base_url}/api/generate"
                    payload = {
                        "model": self.model_name,
                        "prompt": "test",
                        "stream": False,
                        "options": {
                            "num_predict": 1  # Minimal response
                        }
                    }
                    requests.post(url, json=payload, timeout=30)
                except:
                    pass  # Ignore errors in background pre-warming
            
            # Run pre-warming in background thread to not block initialization
            thread = threading.Thread(target=prewarm, daemon=True)
            thread.start()
        except Exception as e:
            pass  # Ignore pre-warming errors
    
    def check_ollama_health(self) -> bool:
        """
        Check if Ollama is running and accessible
        
        Returns:
            True if Ollama is healthy, raises exception otherwise
        """
        try:
            response = requests.get(f"{self.ollama_base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Ollama is not accessible at {self.ollama_base_url}. Make sure Ollama is running: ollama serve")
    
    def _load_rules(self) -> str:
        """Load rules from config manager - ALWAYS read from claim_config.json"""
        try:
            # Reload config to ensure latest changes are loaded
            config_manager.reload_config()
            prompts = config_manager.get_prompts()
            if prompts and prompts.get("main_prompt"):
                print(f"  ✓ Loaded main_prompt from claim_config.json")
                return prompts["main_prompt"]
            else:
                print(f"  ⚠️ Warning: main_prompt not found in claim_config.json, using default")
        except Exception as e:
            print(f"  ⚠️ Warning: Could not load rules from claim_config.json: {e}")
            print(f"  ⚠️ Using default rules as fallback")
        
        # Fallback to default only if config file doesn't exist or is invalid
        return self._load_default_rules()
    
    def _load_default_rules(self) -> str:
        """Load default rules and conditions for claim processing (English)"""
        return """
Hi Ahmed,

Let's try this:

///////////////////////////////////////////////////

You are a specialized model for analyzing motor vehicle accident reports and claims, determining the final insurance decision with very high accuracy.
You must read the record as is, without adding, without assuming, without interpreting, and without inferring any information not present in the text.
 
⚠️ ⚠️ ⚠️ CRITICAL RULE - Read carefully ⚠️ ⚠️ ⚠️
Each analysis is performed for the specified party only (Party by Party).  
- Liability percentage comes from Parameters (Excel) - do not take it from the accident description
- The accident description explains the accident only - do not use it to determine liability
 
Do not use any information outside the record.  
The output must be in JSON format only.
 
=====================================================================
🔴 FIRST: Claim Rejection Rules (REJECTED)
=====================================================================
⚠️ Basic Rule #1 — Most Important and Highest Priority
1) If the liability percentage of the party you are analyzing now (Liability) = 100%
→ Mandatory Decision: REJECTED  
→ Applies to all companies without any exception, including Tawuniya Cooperative Insurance Company or التعاونية للتأمين.  
→ This rule cannot be overridden.
→ ⚠️ ⚠️ ⚠️ This rule applies only to the party you are analyzing - does not apply to other parties ⚠️ ⚠️ ⚠️
 
------------------------------------------------------
The claim is REJECTED for the party being analyzed if any of the following applies:
------------------------------------------------------
2) The sum of liability for parties insured under Tawuniya Cooperative Insurance Company or التعاونية للتأمين is 0 then reject all parties of the accident.
3) The damaged vehicle is owned by the at-fault party.  
4) The claim concerns property of the insured or property under their management/custody.  
5) The claim is due to death of the insured or driver.  
6) Use of the vehicle in racing or capability testing.  
7) Entering a prohibited area without permission.  
8) Intentional damage to the vehicle.  
9) Collusion or staged accident.  
10) Intentional accident.  
11) Fleeing the scene without acceptable excuse.  
12) Reckless driving (drifting).  
13) Use of drugs/alcohol/medications that prevent driving.  
14) Natural disasters.  
15) Failure to notify authorities immediately.  
16) More than 5 years have passed since the accident.  
17) Fraud exists.

If the 17 rejection reasons above does not apply then the claim is either accepted or accepted with recovery. To identify if the claim is accepted or accepted with recovery follow the below rules:
🟡 Second: Accepted with recovery rules (ACCEPTED_WITH_RECOVERY)
=====================================================================
ACCEPTED_WITH_RECOVERY applies when the at-fault party (i.e., one with Liability > 0) committed any of the following:
 
1) Wrong-way driving (reversing direction)  as per the accident description
2) Crossing a red light as per the accident description
3) Exceeding passenger capacity as per the accident description
4) if the vehicle was stolen as per the accident description
5) If License_Expiry_Date < Accident_Date  
   — Verification is performed only if the value actually exists  
   — If it is "Not Identify", empty, or not present → this violation does not apply  
6) If License_Type_From_Make_Model ≠ "Any License"  
   and does not match or resemble License_Type_From_Request  
   — If license data is "Not Identify" → not considered a violation  
 
If the above 6 rules applies then respond with ACCEPTED_WITH_RECOVERY, if it does not apply then respond with ACCEPTED.
 
=====================================================================
📦 Required Output — JSON Only
=====================================================================
 
Return the result for the specified party only:
 
{
  "decision": "REJECTED | ACCEPTED | ACCEPTED_WITH_RECOVERY",
  "reasoning": "Very brief reason based only on the data (in English)",
  "classification": "Must include the rule/condition in English used to make the decision",
  "applied_conditions": ["List of conditions/rules that were applied"]
}
 
❗ Do not write anything outside JSON  
❗ Do not use examples  
❗ Do not assume any information  
❗ Work only on the specified row
 
/////////////////////////////////////////////////////////
    """
    
    def _translate_text_to_english(self, text: str) -> str:
        """
        Translate Arabic text to English using Ollama.
        If translation fails, returns original text.
        
        Args:
            text: Text containing Arabic and/or English content
            
        Returns:
            Translated text with Arabic parts converted to English
        """
        if not text or not text.strip():
            return text
        
        import re
        import logging
        transaction_logger = logging.getLogger("transaction_tp")
        
        # Check if text contains Arabic characters
        has_arabic = bool(re.search(r'[\u0600-\u06FF]', text))
        if not has_arabic:
            # No Arabic text, return as is
            return text
        
        try:
            # Get translation prompt from claim_config.json
            translation_prompt_template = None
            try:
                config_manager.reload_config()
                prompts = config_manager.get_prompts()
                translation_prompt_template = prompts.get("translation_prompt", None)
                if translation_prompt_template:
                    transaction_logger.info(
                        f"TRANSLATION_PROMPT_LOADED | Source: claim_config.json | "
                        f"Template_Length: {len(translation_prompt_template)}"
                    )
            except Exception as e:
                transaction_logger.warning(
                    f"TRANSLATION_PROMPT_LOAD_ERROR | Error: {str(e)[:200]} | "
                    f"Using_Default: True"
                )
                print(f"  ⚠️ Warning: Could not load translation_prompt from claim_config.json: {e}")
            
            # Use translation prompt from config if available, otherwise use default
            if translation_prompt_template:
                translation_prompt = translation_prompt_template.format(text=text)
                transaction_logger.info(
                    f"TRANSLATION_START | Text_Length: {len(text)} | "
                    f"Has_Arabic: {has_arabic} | Prompt_Length: {len(translation_prompt)} | "
                    f"Source: Config"
                )
                print(f"  ✓ Loaded translation_prompt from claim_config.json")
            else:
                transaction_logger.info(
                    f"TRANSLATION_START | Text_Length: {len(text)} | "
                    f"Has_Arabic: {has_arabic} | Using_Default_Prompt: True"
                )
                # Fallback to default translation prompt
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
4. Preserve ALL structure, formatting, line breaks, and spacing
5. Do NOT add any explanations, notes, or comments
6. Return ONLY the translated text

Text to translate:
{text}

Translation (Arabic parts only, keep English unchanged, use LD report terminology):"""
            
            # Use faster translation_model for translation (not decision model)
            translation_model_to_use = getattr(self, 'translation_model', 'llama3.2:latest')
            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={
                    "model": translation_model_to_use,  # Use faster model for translation
                    "prompt": translation_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "top_p": 0.9,
                        "num_predict": 2000  # Limit response for faster translation
                    }
                },
                timeout=120  # Increased timeout for translation (2 minutes)
            )
            
            if response.status_code == 200:
                result = response.json()
                translated_text = result.get("response", "").strip()
                transaction_logger.info(
                    f"TRANSLATION_RESPONSE | Status: SUCCESS | "
                    f"Original_Length: {len(text)} | Translated_Length: {len(translated_text)} | "
                    f"Model: {translation_model_to_use}"
                )
                if translated_text:
                    # Clean up the response
                    lines = translated_text.split('\n')
                    cleaned_lines = []
                    skip_patterns = [
                        r'^Translation\s*:?',
                        r'^Translated text\s*:?',
                        r'^Here is the translation\s*:?',
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
                    translated_text = re.sub(r'^["\']+|["\']+$', '', translated_text)
                    return translated_text if translated_text else text
            return text
                
        except Exception as e:
            error_msg = str(e)[:200]
            transaction_logger.error(
                f"TRANSLATION_ERROR | Error: {error_msg} | "
                f"Text_Length: {len(text)} | Returning_Original: True"
            )
            print(f"  ⚠️ Translation error: {error_msg}")
            return text
    
    def _translate_claim_data_to_english(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively translate Arabic text in claim data dictionary to English.
        Uses LD report terminology for accurate translation.
        
        Args:
            data: Dictionary containing claim data (may contain Arabic text)
            
        Returns:
            Dictionary with Arabic text translated to English using LD report terminology
        """
        if not isinstance(data, dict):
            if isinstance(data, str):
                return self._translate_text_to_english(data)
            return data
        
        translated = {}
        for key, value in data.items():
            if isinstance(value, dict):
                translated[key] = self._translate_claim_data_to_english(value)
            elif isinstance(value, list):
                translated[key] = [
                    self._translate_claim_data_to_english(item) if isinstance(item, dict) 
                    else (self._translate_text_to_english(item) if isinstance(item, str) else item)
                    for item in value
                ]
            elif isinstance(value, str):
                # Use specialized translation for LD report data
                translated[key] = self._translate_text_to_english(value)
            else:
                translated[key] = value
        
        return translated
    
    def parse_xml(self, xml_string: str) -> Dict[str, Any]:
        """Parse XML claim data into dictionary (handles namespaces)"""
        try:
            # Clean XML string first
            xml_clean = xml_string.strip()
            
            # Fix Excel line break encoding (_x000D_ = carriage return, _x000A_ = line feed)
            xml_clean = xml_clean.replace('_x000D_', '\r')
            xml_clean = xml_clean.replace('_x000A_', '\n')
            xml_clean = xml_clean.replace('_x000d_', '\r')  # lowercase
            xml_clean = xml_clean.replace('_x000a_', '\n')  # lowercase
            
            # Also handle other Excel escape sequences
            import re
            # Replace _x####_ patterns with their unicode equivalents
            def replace_excel_unicode(match):
                code = int(match.group(1), 16)
                return chr(code)
            xml_clean = re.sub(r'_x([0-9A-Fa-f]{4})_', replace_excel_unicode, xml_clean)
            
            # Remove BOM if present
            if xml_clean.startswith('\ufeff'):
                xml_clean = xml_clean[1:]
            
            # Fix namespace issues - if s0: prefix is used but namespace not defined
            import re
            if '<s0:' in xml_clean and 'xmlns:s0' not in xml_clean:
                # Option 1: Add namespace definition
                if xml_clean.startswith('<?xml'):
                    # Insert namespace after XML declaration
                    xml_decl_end = xml_clean.find('?>') + 2
                    namespace_def = '\n<s0:EICWS xmlns:s0="http://www.w3.org/2001/XMLSchema-instance"'
                    # Check if EICWS already has attributes
                    eicws_start = xml_clean.find('<s0:EICWS')
                    if eicws_start != -1:
                        eicws_tag_end = xml_clean.find('>', eicws_start)
                        if xml_clean[eicws_start:eicws_tag_end].strip().endswith('/>'):
                            # Self-closing tag
                            xml_clean = xml_clean[:eicws_start] + '<s0:EICWS xmlns:s0="http://www.w3.org/2001/XMLSchema-instance"' + xml_clean[eicws_tag_end:]
                        elif '>' in xml_clean[eicws_start:eicws_start+50]:
                            # Has opening tag, add namespace before closing >
                            tag_content = xml_clean[eicws_start:eicws_tag_end]
                            if 'xmlns' not in tag_content:
                                xml_clean = xml_clean[:eicws_tag_end] + ' xmlns:s0="http://www.w3.org/2001/XMLSchema-instance"' + xml_clean[eicws_tag_end:]
                else:
                    # No XML declaration, add namespace to first element
                    eicws_start = xml_clean.find('<s0:EICWS')
                    if eicws_start != -1:
                        eicws_tag_end = xml_clean.find('>', eicws_start)
                        tag_content = xml_clean[eicws_start:eicws_tag_end]
                        if 'xmlns' not in tag_content:
                            xml_clean = xml_clean[:eicws_tag_end] + ' xmlns:s0="http://www.w3.org/2001/XMLSchema-instance"' + xml_clean[eicws_tag_end:]
            
            # Alternative: Remove s0: prefix if namespace can't be added
            # This is a fallback if the above doesn't work
            if '<s0:' in xml_clean:
                # Try to register namespace first
                ET.register_namespace('s0', 'http://www.w3.org/2001/XMLSchema-instance')
            
            # Register namespaces to handle them properly
            ET.register_namespace('s0', 'http://www.w3.org/2001/XMLSchema-instance')
            ET.register_namespace('xsi', 'http://www.w3.org/2001/XMLSchema-instance')
            
            # Try parsing
            try:
                root = ET.fromstring(xml_clean)
            except ET.ParseError as e:
                error_msg = str(e)
                # If it's a namespace error, try removing the prefix
                if 'unbound prefix' in error_msg or 'namespace' in error_msg.lower():
                    # Remove s0: prefix from all elements (opening and closing tags)
                    xml_clean_fixed = re.sub(r'<s0:(\w+)', r'<\1', xml_clean)
                    xml_clean_fixed = re.sub(r'</s0:(\w+)', r'</\1', xml_clean_fixed)
                    # Also handle attributes with s0: prefix
                    xml_clean_fixed = re.sub(r'\ss0:(\w+)=', r' \1=', xml_clean_fixed)
                    try:
                        root = ET.fromstring(xml_clean_fixed)
                        xml_clean = xml_clean_fixed
                    except ET.ParseError as e2:
                        # Try to fix common issues
                        # Remove invalid XML characters
                        xml_clean_fixed2 = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', xml_clean_fixed)
                        try:
                            root = ET.fromstring(xml_clean_fixed2)
                            xml_clean = xml_clean_fixed2
                        except ET.ParseError:
                            # Show helpful error message
                            raise ValueError(f"Invalid XML format: {str(e2)}\nFirst 200 chars: {xml_clean[:200]}")
                else:
                    # Try to fix common issues
                    # Remove invalid XML characters
                    xml_clean_fixed = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', xml_clean)
                    # Try again
                    try:
                        root = ET.fromstring(xml_clean_fixed)
                        xml_clean = xml_clean_fixed
                    except ET.ParseError:
                        # Show helpful error message
                        raise ValueError(f"Invalid XML format: {str(e)}\nFirst 200 chars: {xml_clean[:200]}")
            
            # Remove namespace prefixes from tags
            def remove_namespace(tag):
                if '}' in tag:
                    return tag.split('}')[1]
                return tag
            
            def xml_to_dict(element):
                """Recursively convert XML to dictionary"""
                tag = remove_namespace(element.tag)
                result = {}
                
                # Get text content
                text = element.text.strip() if element.text and element.text.strip() else None
                
                # Process children
                children = list(element)
                if children:
                    for child in children:
                        child_tag = remove_namespace(child.tag)
                        child_data = xml_to_dict(child)
                        
                        if child_tag in result:
                            if not isinstance(result[child_tag], list):
                                result[child_tag] = [result[child_tag]]
                            result[child_tag].append(child_data)
                        else:
                            result[child_tag] = child_data
                    
                    # If there's also text, add it
                    if text:
                        result['_text'] = text
                else:
                    # Leaf node - return text or empty dict
                    if text:
                        return text
                    elif element.attrib:
                        result = element.attrib.copy()
                        if text:
                            result['_text'] = text
                        return result if result else None
                    else:
                        return text if text else None
                
                # Add attributes if any
                if element.attrib:
                    result['_attributes'] = element.attrib
                
                return result if result else (text if text else None)
            
            claim_data = xml_to_dict(root)
            return claim_data
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML format: {str(e)}")
    
    def parse_json(self, json_string: str) -> Dict[str, Any]:
        """Parse JSON claim data into dictionary"""
        try:
            return json.loads(json_string)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format: {str(e)}")
    
    def format_claim_for_llm_with_party(self, accident_info: Dict[str, Any], party_info: Dict[str, Any], 
                                       party_index: int, liability: int, is_cooperative: bool,
                                       all_parties: List[Dict[str, Any]] = None) -> str:
        """Format accident info + specific party for LLM - OPTIMIZED COMPACT VERSION"""
        
        # Extract essential data
        insurance_info = party_info.get("Insurance_Info", {}) or party_info.get("insurance_info", {})
        insurance_name = insurance_info.get("ICEnglishName", insurance_info.get("ICArabicName", ""))
        accident_desc = accident_info.get("AccidentDescription", accident_info.get("Accident_description", ""))
        
        # Extract DAA fields from accident_info (same as Excel extraction)
        isDAA = accident_info.get("isDAA", accident_info.get("is_daa", None))
        suspect_as_fraud = accident_info.get("Suspect_as_Fraud", accident_info.get("suspect_as_fraud", None))
        daa_reason_english = accident_info.get("DaaReasonEnglish", accident_info.get("daa_reason_english", None))
        
        # Build compact JSON data structure - MUST INCLUDE ALL FIELDS NEEDED FOR RECOVERY CONDITIONS
        # Recovery conditions require:
        # 1. Wrong-way driving (from accident_description)
        # 2. Red light violation (from accident_description)
        # 3. Exceeding capacity (from accident_description)
        # 4. Stolen vehicle (from accident_description)
        # 5. License_Expiry_Date < Accident_Date (need both dates)
        # 6. License_Type_From_Make_Model ≠ "Any License" and doesn't match License_Type_From_Request (NEED BOTH)
        
        # Extract license type fields (CRITICAL for recovery condition 6)
        license_type_from_make_model = party_info.get("License_Type_From_Make_Model", "")
        license_type_from_request = (
            party_info.get("License_Type_From_Request", "") or
            party_info.get("licenseType", "") or
            party_info.get("License_Type_From_Najm", "")
        )
        
        # Extract recovery field
        recovery = party_info.get("recovery", party_info.get("Recovery", False))
        
        # Extract vehicle make/model (useful for context)
        vehicle_make = party_info.get("carMake", party_info.get("Vehicle_Make", ""))
        vehicle_model = party_info.get("carModel", party_info.get("Vehicle_Model", ""))
        
        data = {
            "party_index": party_index,
            "liability": liability,
            "is_cooperative": is_cooperative,
            "accident_description": accident_desc[:500] if accident_desc else "",  # Limit description length
            "party": {
                "id": party_info.get("ID", ""),
                "name": party_info.get("name", ""),
                "insurance": insurance_name,
                "policyholder_id": party_info.get("Policyholder_ID", ""),
                "policyholder_name": party_info.get("Policyholdername", party_info.get("Policyholder_Name", "")),  # NEW: Policyholder name
                "vehicle_serial": party_info.get("chassisNo", party_info.get("Vehicle_Serial", "")),
                "vehicle_make": vehicle_make,
                "vehicle_model": vehicle_model,
                "license_expiry": party_info.get("License_Expiry_Date", ""),
                "license_type_from_make_model": license_type_from_make_model,  # CRITICAL for recovery condition 6
                "license_type_from_request": license_type_from_request,  # CRITICAL for recovery condition 6
                "recovery": recovery,  # Recovery field from party data
                "accident_date": accident_info.get('callDate', accident_info.get('Accident_Date', ''))
            },
            "other_parties": [],
            # Add DAA fields to Case Information (same as Excel extraction)
            "isDAA": isDAA,
            "Suspect_as_Fraud": suspect_as_fraud,
            "DaaReasonEnglish": daa_reason_english
        }
        
        # Add other parties summary (minimal)
        if all_parties:
            for idx, p in enumerate(all_parties):
                if idx != party_index:
                    p_ins = p.get("Insurance_Info", {}) or p.get("insurance_info", {})
                    p_liab = p.get("Liability", p.get("liability", 0))
                    try:
                        p_liab = int(p_liab) if p_liab else 0
                    except:
                        p_liab = 0
                    data["other_parties"].append({
                        "liability": p_liab,
                        "is_cooperative": "Cooperative" in p_ins.get("ICEnglishName", "") or "التعاونية" in p_ins.get("ICArabicName", "")
                    })
        
        # Build compact prompt - ALWAYS read from claim_config.json
        try:
            # Reload config to ensure latest changes are loaded
            config_manager.reload_config()
            prompts = config_manager.get_prompts()
            compact_template = prompts.get("compact_prompt_template", None)
            if compact_template:
                prompt = compact_template.format(
                    party_index=party_index + 1,
                    data=json.dumps(data, ensure_ascii=False)
                )
                print(f"  ✓ Loaded compact_prompt_template from claim_config.json")
                
                # Log prompt details for comparison
                import logging
                transaction_logger = logging.getLogger("transaction_tp")
                transaction_logger.info(
                    f"PROMPT_BUILT_FROM_CONFIG | Party: {party_index} | "
                    f"Template_Source: claim_config.json | "
                    f"Template_Length: {len(compact_template)} | "
                    f"Final_Prompt_Length: {len(prompt)} | "
                    f"Data_JSON_Length: {len(json.dumps(data, ensure_ascii=False))} | "
                    f"Party_Index: {party_index + 1}"
                )
            else:
                print(f"  ⚠️ Warning: compact_prompt_template not found in claim_config.json, using default")
                # Fallback to default
                prompt = self._get_default_compact_prompt(party_index, data)
                
                # Log that default prompt is used
                import logging
                transaction_logger = logging.getLogger("transaction_tp")
                transaction_logger.warning(
                    f"PROMPT_BUILT_FROM_DEFAULT | Party: {party_index} | "
                    f"Template_Source: DEFAULT (not in config) | "
                    f"Final_Prompt_Length: {len(prompt)}"
                )
        except Exception as e:
            print(f"  ⚠️ Warning: Could not load compact prompt from claim_config.json: {e}")
            print(f"  ⚠️ Using default compact prompt as fallback")
            prompt = self._get_default_compact_prompt(party_index, data)
            
            # Log error
            import logging
            transaction_logger = logging.getLogger("transaction_tp")
            transaction_logger.error(
                f"PROMPT_BUILD_ERROR | Party: {party_index} | "
                f"Error: {str(e)[:200]} | "
                f"Using default prompt | "
                f"Final_Prompt_Length: {len(prompt)}"
            )
        
        return prompt
    
    def _get_default_compact_prompt(self, party_index: int, data: Dict[str, Any]) -> str:
        """Default compact prompt template"""
        return f"""Analyze Party {party_index + 1} for insurance claim decision.

DATA (JSON):
{json.dumps(data, ensure_ascii=False)}

RULES:
1. If liability=100% → REJECTED (always, no exception)
2. If non-cooperative + liability in [0,25,50,75]% → ACCEPTED (unless rejection condition 1-16 applies)
3. If cooperative + liability<100% + responsible party (100%) not cooperative → REJECTED
4. If cooperative + liability<100% + responsible party (100%) cooperative → ACCEPTED
5. If liability=0% + non-cooperative → ACCEPTED
6. If liability=0% + cooperative + responsible party not cooperative → REJECTED
7. If rejection condition 1-16 applies → REJECTED
8. If recovery condition applies → ACCEPTED_WITH_RECOVERY

REJECTION CONDITIONS (1-17):
⚠️ Basic Rule #1 — Most Important and Highest Priority
1) If the liability percentage of the party you are analyzing now (Liability) = 100%
→ Mandatory Decision: REJECTED  
→ Applies to all companies without any exception, including Tawuniya Cooperative Insurance Company or التعاونية للتأمين.  
→ This rule cannot be overridden.
→ ⚠️ ⚠️ ⚠️ This rule applies only to the party you are analyzing - does not apply to other parties ⚠️ ⚠️ ⚠️
 
------------------------------------------------------
The claim is REJECTED for the party being analyzed if any of the following applies:
------------------------------------------------------
2) The sum of liability for parties insured under Tawuniya Cooperative Insurance Company or التعاونية للتأمين is 0 then reject all parties of the accident.
3) The damaged vehicle is owned by the at-fault party.  
4) The claim concerns property of the insured or property under their management/custody.  
5) The claim is due to death of the insured or driver.  
6) Use of the vehicle in racing or capability testing.  
7) Entering a prohibited area without permission.  
8) Intentional damage to the vehicle.  
9) Collusion or staged accident.  
10) Intentional accident.  
11) Fleeing the scene without acceptable excuse.  
12) Reckless driving (drifting).  
13) Use of drugs/alcohol/medications that prevent driving.  
14) Natural disasters.  
15) Failure to notify authorities immediately.  
16) More than 5 years have passed since the accident.  
17) Fraud exists.


RECOVERY CONDITIONS (ACCEPTED_WITH_RECOVERY applies when the at-fault party (i.e., one with Liability > 0) committed any of the following):
1) Wrong-way driving (reversing direction) as per the accident description
2) Crossing a red light as per the accident description
3) Exceeding passenger capacity as per the accident description
4) If the vehicle was stolen as per the accident description
5) If License_Expiry_Date < Accident_Date  
   — Verification is performed only if the value actually exists  
   — If it is "Not Identify", empty, or not present → this violation does not apply  
6) If License_Type_From_Make_Model ≠ "Any License"  
   and does not match or resemble License_Type_From_Request  
   — If license data is "Not Identify" → not considered a violation  
   — Check party.license_type_from_make_model and party.license_type_from_request in DATA

OUTPUT (JSON only):
{{
  "decision": "REJECTED|ACCEPTED|ACCEPTED_WITH_RECOVERY",
  "reasoning": "Brief reason in English",
  "classification": "Rule/condition used (e.g., Basic Rule #1 - 100% liability)",
  "applied_conditions": ["1", "2", ...]
}}"""
        
        return prompt
    
    def format_claim_for_llm(self, claim_data: Dict[str, Any], party_index: int = None, liability: int = None) -> str:
        """Format claim data into a readable prompt for LLM (legacy mode)"""
        # Reload rules from config to get latest changes
        self.reload_rules()
        
        prompt = "=" * 70 + "\n"
        prompt += "معلومات تقرير الحادث - ACCIDENT CLAIM INFORMATION\n"
        prompt += "=" * 70 + "\n\n"
        
        # Extract and display Liability prominently
        if liability is None:
            party_info = claim_data.get("party_info", {})
            liability = party_info.get("Liability", party_info.get("liability", 0))
            try:
                liability = int(liability) if liability else 0
            except:
                liability = 0
        
        prompt += f"⚠️ مهم جداً - نسبة المسؤولية (LIABILITY) للطرف: {liability}%\n"
        if liability == 100:
            prompt += "⚠️ هذا الطرف هو المتسبب بالحادث (100% مسؤولية)\n"
        elif liability == 0:
            prompt += "⚠️ هذا الطرف غير متسبب (0% مسؤولية) - متضرر\n"
        prompt += "\n" + "=" * 70 + "\n\n"
        
        prompt += json.dumps(claim_data, indent=2, ensure_ascii=False)
        prompt += "\n\n" + self.rules
        
        if party_index is not None:
            prompt += f"\n\n{'=' * 70}\n"
            prompt += f"تحليل الطرف رقم {party_index + 1} (Party {party_index + 1})\n"
            prompt += f"نسبة المسؤولية (Liability): {liability}%\n"
            prompt += f"{'=' * 70}\n"
        
        prompt += "\n\nقم بتحليل هذا الحادث وتقديم:\n"
        prompt += "1. Decision (القرار): REJECTED (مرفوضة) أو ACCEPTED (مقبولة) أو ACCEPTED_WITH_RECOVERY (مقبولة مع حق الرجوع)\n"
        prompt += "2. Reasoning (السبب): سبب القرار بشكل مختصر ومباشر بالعربية\n"
        prompt += "3. Applied Conditions (الشروط المطبقة): رقم/أرقام الشروط التي انطبقت (إن وجدت)\n"
        prompt += "4. Classification (التصنيف): يجب أن يتضمن القاعدة والشرط بالعربية التي تم استخدامها لاتخاذ القرار (مثل: القاعدة الأساسية رقم 1 - 100% مسؤولية، أو شرط رفض رقم 2، أو شرط حق الرجوع رقم 1 - عكس السير، إلخ)\n"
        
        prompt += "\n" + "=" * 70 + "\n"
        prompt += "قواعد حاسمة بناءً على نسبة المسؤولية:\n"
        prompt += "=" * 70 + "\n"
        if liability == 100:
            prompt += "⚠️ Liability = 100% → القرار يجب أن يكون: REJECTED (مرفوضة)\n"
            prompt += "السبب: الطرف متسبب بالحادث (100% مسؤولية)\n"
        elif liability == 0:
            prompt += "⚠️ Liability = 0% → القرار يجب أن يكون: ACCEPTED (مقبولة)\n"
            prompt += "السبب: الطرف غير متسبب (0% مسؤولية) - متضرر\n"
            prompt += "ما لم تنطبق شروط رفض أخرى من القائمة (2-17)\n"
        else:
            prompt += f"⚠️ Liability = {liability}% → راجع القواعد بناءً على النسبة\n"
        prompt += "=" * 70 + "\n"
        
        prompt += "\nتذكر: لا تضيف أي معلومات غير موجودة في التقرير. لا تستخدم افتراضات.\n"
        prompt += "\nقم بإرجاع النتيجة بصيغة JSON مع المفاتيح: decision, reasoning, applied_conditions, classification"
        
        return prompt
    
    def process_party_claim(self, claim_data: Dict[str, Any], party_info: Dict[str, Any], party_index: int, all_parties: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process a single party's claim within an accident case
        
        Args:
            claim_data: Full claim data including accident info
            party_info: Information about the specific party
            party_index: Index of the party (0-based)
            all_parties: List of all parties for context (optional)
        
        Returns:
            Dictionary containing decision for this party
        """
        # Reload rules from config to get latest changes (no restart needed)
        self.reload_rules()
        
        # Extract case info
        case_info = None
        if "EICWS" in claim_data:
            case_info = claim_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
        if not case_info and "cases" in claim_data:
            case_info = claim_data.get("cases", {}).get("Case_Info", {})
        if not case_info and "Case_Info" in claim_data:
            case_info = claim_data.get("Case_Info", {})
        
        accident_info = case_info.get("Accident_info", {}) if case_info else {}
        
        # Extract Liability clearly
        liability = party_info.get("Liability", party_info.get("liability", 0))
        try:
            liability = int(liability) if liability else 0
        except:
            liability = 0
        
        # Extract insurance info
        insurance_info = party_info.get("Insurance_Info", {})
        if not insurance_info:
            insurance_info = party_info.get("insurance_info", {})
        
        # Prefer English name (data is already translated)
        insurance_name = insurance_info.get("ICEnglishName", insurance_info.get("ICArabicName", ""))
        is_cooperative = "التعاونية" in insurance_name or "Cooperative" in insurance_info.get("ICEnglishName", "")
        
        # OPTIMIZATION: Skip translation since qwen2.5:14b handles Arabic natively
        # Translation is slow and unnecessary - model can process Arabic directly
        # Uncomment below if translation is needed for other models
        USE_TRANSLATION = False  # Set to False for speed (qwen2.5:14b handles Arabic)
        
        if USE_TRANSLATION:
            print(f"  🔄 Translating claim data to English before sending to Ollama...")
            accident_info_english = self._translate_claim_data_to_english(accident_info)
            party_info_english = self._translate_claim_data_to_english(party_info)
            all_parties_english = None
            if all_parties:
                all_parties_english = [self._translate_claim_data_to_english(p) for p in all_parties]
            print(f"  ✅ Translation completed")
        else:
            # Skip translation - use original data (model handles Arabic)
            accident_info_english = accident_info
            party_info_english = party_info
            all_parties_english = all_parties
        
        # Format for LLM with accident info + this specific party (original data - model handles Arabic)
        prompt = self.format_claim_for_llm_with_party(
            accident_info=accident_info_english,
            party_info=party_info_english,
            party_index=party_index,
            liability=liability,
            is_cooperative=is_cooperative,
            all_parties=all_parties_english
        )
        
        # Extract case number for logging
        case_number = accident_info.get("caseNumber", accident_info.get("case_number", "UNKNOWN"))
        
        # Log prompt building with FULL details for comparison
        import logging
        transaction_logger = logging.getLogger("transaction_tp")
        
        # Extract data structure from prompt for detailed logging
        data_json_from_prompt = None
        try:
            if "DATA (JSON):" in prompt:
                data_start = prompt.find("DATA (JSON):") + len("DATA (JSON):")
                data_end = prompt.find("\n\nRULES:", data_start)
                if data_end == -1:
                    data_end = prompt.find("\n\nOUTPUT", data_start)
                if data_end == -1:
                    data_end = len(prompt)
                data_str = prompt[data_start:data_end].strip()
                try:
                    data_json_from_prompt = json.loads(data_str)
                except:
                    pass
        except:
            pass
        
        transaction_logger.info(
            f"PROMPT_BUILT | Party: {party_index} | Case: {case_number} | "
            f"Prompt_Length: {len(prompt)} | Liability: {liability} | "
            f"Insurance_Name: {insurance_name[:50]} | "
            f"Parties_Count: {len(all_parties) if all_parties else 0} | "
            f"Prompt_Method: format_claim_for_llm_with_party | "
            f"Data_Structure_Keys: {list(data_json_from_prompt.keys()) if data_json_from_prompt else 'N/A'}"
        )
        
        # Log full prompt for comparison (same as Ollama request logging)
        transaction_logger.info(
            f"PROMPT_FULL_CONTENT | Party: {party_index} | Case: {case_number} | "
            f"Full_Prompt: {prompt}"
        )
        
        # Call Ollama with logging parameters
        llm_response = self.call_ollama(prompt, party_index=party_index, case_number=case_number)
        
        # Parse LLM response
        transaction_logger.info(
            f"DECISION_PARSING_START | Party: {party_index} | Case: {case_number} | "
            f"Response_Length: {len(llm_response)} | Has_JSON_Block: {'```json' in llm_response or '```' in llm_response}"
        )
        
        try:
            llm_response_clean = llm_response.strip()
            if "```json" in llm_response_clean:
                start = llm_response_clean.find("```json") + 7
                end = llm_response_clean.find("```", start)
                llm_response_clean = llm_response_clean[start:end].strip()
                transaction_logger.info(
                    f"JSON_EXTRACTION | Party: {party_index} | Case: {case_number} | "
                    f"Format: JSON_Block | Extracted_Length: {len(llm_response_clean)}"
                )
            elif "```" in llm_response_clean:
                start = llm_response_clean.find("```") + 3
                end = llm_response_clean.find("```", start)
                llm_response_clean = llm_response_clean[start:end].strip()
                transaction_logger.info(
                    f"JSON_EXTRACTION | Party: {party_index} | Case: {case_number} | "
                    f"Format: Code_Block | Extracted_Length: {len(llm_response_clean)}"
                )
            else:
                transaction_logger.info(
                    f"JSON_EXTRACTION | Party: {party_index} | Case: {case_number} | "
                    f"Format: Raw_JSON | Length: {len(llm_response_clean)}"
                )
            
            decision_result = json.loads(llm_response_clean)
            
            # Log parsed decision
            transaction_logger.info(
                f"DECISION_PARSED | Party: {party_index} | Case: {case_number} | "
                f"Decision: {decision_result.get('decision', 'UNKNOWN')} | "
                f"Classification: {decision_result.get('classification', 'UNKNOWN')} | "
                f"Conditions_Count: {len(decision_result.get('applied_conditions', []))} | "
                f"Reasoning_Length: {len(decision_result.get('reasoning', ''))}"
            )
            
        except json.JSONDecodeError as e:
            transaction_logger.error(
                f"DECISION_PARSE_ERROR | Party: {party_index} | Case: {case_number} | "
                f"Error: JSON_Decode_Error | Error_Message: {str(e)[:200]} | "
                f"Response_Preview: {llm_response[:500]}"
            )
            decision_result = {
                "decision": "PENDING",
                "reasoning": "Could not parse LLM response as JSON",
                "raw_response": llm_response,
                "applied_conditions": [],
                "classification": "UNKNOWN"
            }
        
        # Add party-specific metadata
        result = {
            "party_index": party_index,
            "party_name": party_info.get("name", "Unknown"),
            "liability": party_info.get("Liability", 0),
            "decision": decision_result.get("decision", "PENDING"),
            "reasoning": decision_result.get("reasoning", ""),
            "applied_conditions": decision_result.get("applied_conditions", []),
            "classification": decision_result.get("classification", "UNKNOWN"),
            "timestamp": datetime.now().isoformat(),
            "model_used": self.model_name
        }
        
        return result
    
    def process_all_parties_together(self, claim_data: Dict[str, Any], party_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process all parties together in one call to get decisions for all parties
        
        Args:
            claim_data: Full claim data
            party_list: List of all parties
        
        Returns:
            List of decisions for all parties
        """
        # Extract accident info
        case_info = None
        if "EICWS" in claim_data:
            case_info = claim_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
        if not case_info and "cases" in claim_data:
            case_info = claim_data.get("cases", {}).get("Case_Info", {})
        if not case_info and "Case_Info" in claim_data:
            case_info = claim_data.get("Case_Info", {})
        
        accident_info = case_info.get("Accident_info", {}) if case_info else {}
        
        # Build comprehensive prompt with all parties
        prompt = "=" * 70 + "\n"
        prompt += "تحليل حادث مروري - جميع الأطراف\n"
        prompt += "MOTOR ACCIDENT ANALYSIS - ALL PARTIES\n"
        prompt += "=" * 70 + "\n\n"
        
        # Accident description
        accident_desc = accident_info.get("AccidentDescription", accident_info.get("Accident_description", ""))
        prompt += f"وصف الحادث (Accident Description):\n{accident_desc}\n\n"
        
        # Case information
        prompt += f"رقم الحادث (Case Number): {accident_info.get('caseNumber', '')}\n"
        prompt += f"المفتش (Surveyor): {accident_info.get('surveyorName', '')}\n"
        prompt += f"التاريخ والوقت: {accident_info.get('callDate', '')} {accident_info.get('callTime', '')}\n"
        prompt += f"الموقع: {accident_info.get('location', '')}, {accident_info.get('city', '')}\n\n"
        
        # All parties information
        prompt += "=" * 70 + "\n"
        prompt += "معلومات جميع الأطراف (ALL PARTIES INFORMATION):\n"
        prompt += "=" * 70 + "\n\n"
        
        for idx, party in enumerate(party_list):
            liability = party.get("Liability", party.get("liability", 0))
            try:
                liability = int(liability) if liability else 0
            except:
                liability = 0
            
            insurance_info = party.get("Insurance_Info", {})
            if not insurance_info:
                insurance_info = party.get("insurance_info", {})
            
            prompt += f"--- الطرف {idx + 1} (Party {idx + 1}) ---\n"
            prompt += f"الاسم (Name): {party.get('name', '')}\n"
            prompt += f"رقم الهوية (ID): {party.get('ID', '')}\n"
            prompt += f"نسبة المسؤولية (Liability): {liability}%\n"
            prompt += f"شركة التأمين: {insurance_info.get('ICArabicName', insurance_info.get('ICEnglishName', ''))}\n"
            prompt += f"رقم البوليصة: {insurance_info.get('policyNumber', '')}\n"
            prompt += f"المركبة: {party.get('carMake', '')} {party.get('carModel', '')} ({party.get('carMfgYear', '')})\n"
            prompt += f"اللوحة: {party.get('plateNo', '')}\n"
            
            if liability == 100:
                prompt += f"⚠️ هذا الطرف متسبب بالحادث (100% مسؤولية)\n"
            elif liability == 0:
                prompt += f"⚠️ هذا الطرف غير متسبب (0% مسؤولية) - متضرر\n"
            
            prompt += "\n"
        
        prompt += "=" * 70 + "\n"
        prompt += self.rules
        prompt += "\n" + "=" * 70 + "\n"
        
        prompt += "\nمطلوب منك:\n"
        prompt += "1. تحليل كل طرف بشكل منفصل\n"
        prompt += "2. تحديد قرار كل طرف:\n"
        prompt += "   - REJECTED (مرفوضة) إذا كان Liability = 100% أو انطبق شرط رفض آخر\n"
        prompt += "   - ACCEPTED (مقبولة) إذا كان Liability = 0% ولم تنطبق شروط رفض\n"
        prompt += "   - ACCEPTED_WITH_RECOVERY (مقبولة مع حق الرجوع) إذا كان Liability = 0% والمتسبب لديه شرط من شروط حق الرجوع\n"
        prompt += "3. ذكر سبب القرار لكل طرف\n\n"
        
        prompt += "قم بإرجاع النتيجة بصيغة JSON مع المفاتيح التالية:\n"
        prompt += "{\n"
        prompt += '  "parties": [\n'
        prompt += '    {\n'
        prompt += '      "party_index": 0,\n'
        prompt += '      "decision": "REJECTED" أو "ACCEPTED" أو "ACCEPTED_WITH_RECOVERY",\n'
        prompt += '      "reasoning": "سبب القرار بالعربية",\n'
        prompt += '      "classification": "يجب أن يتضمن القاعدة والشرط بالعربية التي تم استخدامها لاتخاذ القرار (مثل: القاعدة الأساسية رقم 1 - 100% مسؤولية، أو شرط رفض رقم 2، أو شرط حق الرجوع رقم 1 - عكس السير، إلخ)",\n'
        prompt += '      "applied_conditions": [رقم الشروط التي انطبقت]\n'
        prompt += '    },\n'
        prompt += '    {\n'
        prompt += '      "party_index": 1,\n'
        prompt += '      ...\n'
        prompt += '    }\n'
        prompt += '  ]\n'
        prompt += '}\n'
        
        # Extract case number for logging
        case_number = accident_info.get("caseNumber", accident_info.get("case_number", "UNKNOWN"))
        
        # Call Ollama (processing all parties together, no specific party_index)
        llm_response = self.call_ollama(prompt, party_index=None, case_number=case_number)
        
        # Parse response
        try:
            llm_response_clean = llm_response.strip()
            if "```json" in llm_response_clean:
                start = llm_response_clean.find("```json") + 7
                end = llm_response_clean.find("```", start)
                llm_response_clean = llm_response_clean[start:end].strip()
            elif "```" in llm_response_clean:
                start = llm_response_clean.find("```") + 3
                end = llm_response_clean.find("```", start)
                llm_response_clean = llm_response_clean[start:end].strip()
            
            decision_result = json.loads(llm_response_clean)
            parties_decisions = decision_result.get("parties", [])
            
            # Ensure we have decisions for all parties
            result_list = []
            for idx, party in enumerate(party_list):
                # Find decision for this party
                party_decision_data = None
                for pd in parties_decisions:
                    if pd.get("party_index") == idx:
                        party_decision_data = pd
                        break
                
                if not party_decision_data:
                    # Fallback: determine based on liability
                    liability = party.get("Liability", party.get("liability", 0))
                    try:
                        liability = int(liability) if liability else 0
                    except:
                        liability = 0
                    
                    if liability == 100:
                        decision = "REJECTED"
                        reasoning = "الطرف متسبب بالحادث (100% مسؤولية)"
                    else:
                        decision = "ACCEPTED"
                        reasoning = "الطرف غير متسبب (0% مسؤولية)"
                    
                    party_decision_data = {
                        "decision": decision,
                        "reasoning": reasoning,
                        "classification": "",
                        "applied_conditions": []
                    }
                
                result = {
                    "party_index": idx,
                    "party_name": party.get("name", "Unknown"),
                    "liability": party.get("Liability", party.get("liability", 0)),
                    "decision": party_decision_data.get("decision", "PENDING"),
                    "reasoning": party_decision_data.get("reasoning", ""),
                    "applied_conditions": party_decision_data.get("applied_conditions", []),
                    "classification": party_decision_data.get("classification", ""),
                    "timestamp": datetime.now().isoformat(),
                    "model_used": self.model_name
                }
                result_list.append(result)
            
            return result_list
            
        except json.JSONDecodeError:
            # Fallback: process each party separately
            print("  ⚠ Could not parse all parties response, processing separately...")
            result_list = []
            for idx, party in enumerate(party_list):
                party_decision = self.process_party_claim(claim_data, party, idx, all_parties=party_list)
                result_list.append(party_decision)
            return result_list
    
    def call_ollama(self, prompt: str, max_retries: int = 2, timeout: int = 90,
                     party_index: int = None, case_number: str = None) -> str:
        """
        Call Ollama API to process the claim with retry logic and response validation
        
        Args:
            prompt: The prompt to send to Ollama
            max_retries: Maximum number of retry attempts (default: 2 for faster processing)
            timeout: Request timeout in seconds (default: 90 = 1.5 minutes - optimized for speed)
            party_index: Index of party being processed (for logging)
            case_number: Case number (for logging)
        
        Returns:
            Response text from Ollama (validated JSON response)
        """
        # Import transaction logger
        import logging
        transaction_logger = logging.getLogger("transaction_tp")
        
        url = f"{self.ollama_base_url}/api/generate"
        
        # Log Ollama request - DETAILED LOGGING (same as Excel)
        # Extract data structure from prompt for logging
        data_json = None
        try:
            # Try to extract JSON data from prompt
            if "DATA (JSON):" in prompt:
                data_start = prompt.find("DATA (JSON):") + len("DATA (JSON):")
                data_end = prompt.find("\n\nRULES:", data_start)
                if data_end == -1:
                    data_end = prompt.find("\n\nOUTPUT", data_start)
                if data_end == -1:
                    data_end = len(prompt)
                data_str = prompt[data_start:data_end].strip()
                try:
                    data_json = json.loads(data_str)
                except:
                    pass
        except:
            pass
        
        # Log full prompt and data structure
        prompt_preview = prompt[:500] if len(prompt) > 500 else prompt
        transaction_logger.info(
            f"OLLAMA_REQUEST | Party: {party_index} | Case: {case_number} | "
            f"Model: {self.model_name} | Prompt_Length: {len(prompt)} | "
            f"Prompt_Preview: {prompt_preview[:200]}..."
        )
        
        # Log full prompt (for debugging)
        transaction_logger.info(
            f"OLLAMA_FULL_PROMPT | Party: {party_index} | Case: {case_number} | "
            f"Full_Prompt: {prompt}"
        )
        
        # Log data structure sent to Ollama
        if data_json:
            transaction_logger.info(
                f"OLLAMA_DATA_STRUCTURE | Party: {party_index} | Case: {case_number} | "
                f"Data_JSON: {json.dumps(data_json, ensure_ascii=False)}"
            )
        
        # Optimize for speed: limit response length, use faster inference parameters
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "format": "json",  # Request JSON format response for accuracy
            "options": {
                "temperature": 0.1,  # Lower temperature for faster, more deterministic responses
                "top_p": 0.9,  # Nucleus sampling for faster inference
                "top_k": 40,  # Limit vocabulary for faster processing
                "num_predict": 500,  # Limit response to 500 tokens (JSON should be short)
                "repeat_penalty": 1.1,  # Prevent repetition
                "num_thread": 4,  # Use 4 threads for faster CPU inference
                "numa": False  # Disable NUMA for faster processing
            }
        }
        
        import time
        import json as json_lib
        last_exception = None
        
        # Start timing for Ollama call
        ollama_call_start = time.time()
        ollama_prep_time = 0.0
        ollama_request_time = 0.0
        ollama_parse_time = 0.0
        
        for attempt in range(max_retries + 1):
            try:
                # Time request preparation
                prep_start = time.time()
                
                # Progressive timeout: increase for retries but keep reasonable
                # Optimized for speed: 90s base, 120s on retry
                current_timeout = timeout
                if attempt > 0:
                    # Increase timeout slightly for retry (90s -> 120s)
                    current_timeout = min(timeout + (attempt * 30), 120)  # Cap at 120s (2 min)
                
                if attempt > 0:
                    # Exponential backoff with jitter for better load distribution
                    wait_time = min(2 ** attempt + (attempt * 0.5), 15)  # Max 15 seconds
                    print(f"    ⏳ Retrying Ollama request (attempt {attempt + 1}/{max_retries + 1}) after {wait_time:.1f}s...")
                    time.sleep(wait_time)
                
                ollama_prep_time += time.time() - prep_start
                
                # Make API call with timeout and connection keep-alive
                # Use a session for connection pooling and keep-alive
                request_start = time.time()
                session = requests.Session()
                session.headers.update({
                    'Connection': 'keep-alive',
                    'Keep-Alive': 'timeout=600, max=1000'
                })
                
                try:
                    response = session.post(url, json=payload, timeout=current_timeout)
                    response.raise_for_status()
                    ollama_request_time += time.time() - request_start
                    
                    # Check if response is HTML (error page) instead of JSON
                    content_type = response.headers.get('Content-Type', '').lower()
                    response_text_preview = response.text[:200] if response.text else ""
                    if 'html' in content_type or (response_text_preview and response_text_preview.strip().startswith('<')):
                        raise ValueError(f"Received HTML error page instead of JSON. This usually means the request timed out or the connection was closed. Response preview: {response_text_preview}")
                    
                    # Time response parsing
                    parse_start = time.time()
                    # Try to parse as JSON - might fail if HTML was returned
                    try:
                        result = response.json()
                    except json_lib.JSONDecodeError as je:
                        # Check if it's HTML
                        if response_text_preview and response_text_preview.strip().startswith('<'):
                            raise ValueError(f"Received HTML error page instead of JSON. This usually means the request timed out or the connection was closed. Response preview: {response_text_preview}")
                        else:
                            raise ValueError(f"Failed to parse response as JSON: {str(je)[:200]}")
                    
                    response_text = result.get("response", "").strip()
                    ollama_parse_time += time.time() - parse_start
                finally:
                    session.close()
                
                # Log Ollama response - FULL DETAILS
                response_preview = response_text[:500] if len(response_text) > 500 else response_text
                transaction_logger.info(
                    f"OLLAMA_RESPONSE | Party: {party_index} | Case: {case_number} | "
                    f"Model: {self.model_name} | Status: SUCCESS | "
                    f"Response_Length: {len(response_text)} | "
                    f"Response_Preview: {response_preview[:200]}..."
                )
                
                # Log FULL response for debugging
                transaction_logger.info(
                    f"OLLAMA_FULL_RESPONSE | Party: {party_index} | Case: {case_number} | "
                    f"Full_Response: {response_text}"
                )
                
                # VALIDATION: Ensure response is not empty
                if not response_text:
                    transaction_logger.warning(
                        f"OLLAMA_VALIDATION | Party: {party_index} | Case: {case_number} | "
                        f"Error: Empty response from Ollama"
                    )
                    raise ValueError("Empty response from Ollama")
                
                # VALIDATION: Try to parse as JSON to ensure it's valid (even if format=json, model might return text)
                try:
                    # Try to extract JSON from response if it's wrapped in text
                    json_text = response_text
                    # If response contains JSON block, extract it
                    if "```json" in response_text:
                        json_start = response_text.find("```json") + 7
                        json_end = response_text.find("```", json_start)
                        if json_end > json_start:
                            json_text = response_text[json_start:json_end].strip()
                    elif "```" in response_text:
                        json_start = response_text.find("```") + 3
                        json_end = response_text.find("```", json_start)
                        if json_end > json_start:
                            json_text = response_text[json_start:json_end].strip()
                    elif response_text.strip().startswith("{"):
                        json_text = response_text.strip()
                    
                    # Validate JSON structure
                    parsed = json_lib.loads(json_text)
                    # Ensure required fields exist for accuracy
                    if not isinstance(parsed, dict):
                        raise ValueError("Response is not a JSON object")
                    
                    # If validation passes, return the cleaned JSON text
                    return json_text
                except json_lib.JSONDecodeError as je:
                    # If JSON parsing fails but we have text, return it (might be valid text response)
                    if attempt < max_retries:
                        print(f"    ⚠️ Invalid JSON response, retrying... (error: {str(je)[:100]})")
                        continue
                    else:
                        # On last attempt, return original response even if not valid JSON
                        print(f"    ⚠️ Warning: Response may not be valid JSON, using as-is")
                        # Log timing before return
                        ollama_call_total = time.time() - ollama_call_start
                        transaction_logger.info(
                            f"OLLAMA_TIMING | Party: {party_index} | Case: {case_number} | "
                            f"Total_Time_Seconds: {ollama_call_total:.4f} | "
                            f"Prep_Time: {ollama_prep_time:.4f}s | "
                            f"Request_Time: {ollama_request_time:.4f}s | "
                            f"Parse_Time: {ollama_parse_time:.4f}s | "
                            f"Attempt: {attempt + 1}"
                        )
                        return response_text
                
                # Log timing before return
                ollama_call_total = time.time() - ollama_call_start
                transaction_logger.info(
                    f"OLLAMA_TIMING | Party: {party_index} | Case: {case_number} | "
                    f"Total_Time_Seconds: {ollama_call_total:.4f} | "
                    f"Prep_Time: {ollama_prep_time:.4f}s | "
                    f"Request_Time: {ollama_request_time:.4f}s | "
                    f"Parse_Time: {ollama_parse_time:.4f}s | "
                    f"Attempt: {attempt + 1}"
                )
                return response_text
                
            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < max_retries:
                    print(f"    ⚠️ Ollama request timed out after {current_timeout}s, retrying...")
                    continue
                else:
                    raise ConnectionError(f"Ollama request timed out after {max_retries + 1} attempts (last timeout: {current_timeout}s). The model may be processing a large/complex claim. For qwen2.5:14b on CPU, this can take 10+ minutes. Try: 1) Using a faster model, 2) Reducing claim complexity, 3) Increasing system resources, or 4) Using GPU acceleration.")
                    
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < max_retries:
                    print(f"    ⚠️ Connection error, retrying...")
                    continue
                else:
                    raise ConnectionError(f"Failed to connect to Ollama after {max_retries + 1} attempts. Make sure Ollama is running: ollama serve")
                    
            except requests.exceptions.RequestException as e:
                last_exception = e
                if attempt < max_retries:
                    print(f"    ⚠️ Request error: {str(e)[:100]}, retrying...")
                    continue
                else:
                    raise ConnectionError(f"Failed to connect to Ollama: {str(e)}")
            
            except ValueError as e:
                # Handle validation errors (including HTML responses)
                error_msg = str(e)
                if "HTML" in error_msg or "html" in error_msg or "Unexpected token" in error_msg:
                    if attempt < max_retries:
                        print(f"    ⚠️ Received HTML error page (likely timeout/connection issue), retrying with longer timeout...")
                        continue
                    else:
                        raise ConnectionError(f"Ollama returned an HTML error page instead of JSON after {max_retries + 1} attempts. This usually means the request timed out or the connection was closed. The model (qwen2.5:14b) may need more time on CPU. Check Docker logs: docker logs ollama-ai")
                else:
                    if attempt < max_retries:
                        print(f"    ⚠️ Response validation error: {str(e)[:100]}, retrying...")
                        continue
                    else:
                        raise ValueError(f"Invalid response from Ollama after {max_retries + 1} attempts: {str(e)}")
            
            except json_lib.JSONDecodeError as e:
                # Handle JSON decode errors (might be HTML response)
                last_exception = e
                error_msg = str(e)
                if "Unexpected token" in error_msg and "'<'" in error_msg:
                    if attempt < max_retries:
                        print(f"    ⚠️ Received HTML instead of JSON (likely timeout), retrying with longer timeout...")
                        continue
                    else:
                        raise ConnectionError(f"Ollama returned HTML instead of JSON after {max_retries + 1} attempts. This usually means the request timed out. For qwen2.5:14b on CPU, requests can take 10+ minutes. Check Docker logs: docker logs ollama-ai")
                else:
                    if attempt < max_retries:
                        print(f"    ⚠️ JSON decode error: {str(e)[:100]}, retrying...")
                        continue
                    else:
                        raise ValueError(f"Failed to parse JSON response from Ollama after {max_retries + 1} attempts: {str(e)}")
        
        # Should not reach here, but just in case
        raise ConnectionError(f"Failed to connect to Ollama after {max_retries + 1} attempts: {str(last_exception)}")
    
    def process_claim(self, claim_input: str, input_format: str = "auto", process_parties_separately: bool = True) -> Dict[str, Any]:
        """
        Process a claim from XML or JSON input
        
        Args:
            claim_input: XML or JSON string containing claim information
            input_format: 'xml', 'json', or 'auto' (auto-detect)
            process_parties_separately: If True, process each party separately (default: True)
        
        Returns:
            Dictionary containing decision and analysis for all parties
        """
        # Reload rules from config to get latest changes (no restart needed)
        self.reload_rules()
        
        # Auto-detect format if needed
        if input_format == "auto":
            claim_input_stripped = claim_input.strip()
            if claim_input_stripped.startswith("<"):
                input_format = "xml"
            elif claim_input_stripped.startswith("{"):
                input_format = "json"
            else:
                raise ValueError("Cannot auto-detect input format. Please specify 'xml' or 'json'")
        
        # Parse input
        if input_format.lower() == "xml":
            claim_data = self.parse_xml(claim_input)
        elif input_format.lower() == "json":
            claim_data = self.parse_json(claim_input)
        else:
            raise ValueError(f"Unsupported format: {input_format}. Use 'xml' or 'json'")
        
        # Extract case info and parties (handle different XML structures)
        case_info = None
        
        # Try EICWS structure first (with or without namespace)
        if "EICWS" in claim_data:
            case_info = claim_data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
        
        # Try direct cases structure
        if not case_info and "cases" in claim_data:
            case_info = claim_data.get("cases", {}).get("Case_Info", {})
        
        # Try root level Case_Info
        if not case_info and "Case_Info" in claim_data:
            case_info = claim_data.get("Case_Info", {})
        
        if not case_info:
            raise ValueError("Could not find Case_Info in claim data. Please check XML structure.")
        
        accident_info = case_info.get("Accident_info", {})
        parties = case_info.get("parties", {})
        
        # Handle parties - could be single dict or list
        party_list = []
        if isinstance(parties, dict):
            party_info_list = parties.get("Party_Info", [])
            if isinstance(party_info_list, dict):
                party_list = [party_info_list]
            elif isinstance(party_info_list, list):
                party_list = party_info_list
        elif isinstance(parties, list):
            party_list = parties
        
        # Process all parties together to get decisions for all
        party_decisions = []
        if process_parties_separately and party_list:
            # Process each party separately with accident info + all parties context
            for idx, party in enumerate(party_list):
                party_decision = self.process_party_claim(claim_data, party, idx, all_parties=party_list)
                party_decisions.append(party_decision)
        else:
            # Process as single claim (legacy mode)
            prompt = self.format_claim_for_llm(claim_data)
            # Extract case number for logging
            case_number = "UNKNOWN"
            if isinstance(claim_data, dict):
                case_info = claim_data.get("Case_Info", {})
                accident_info = case_info.get("Accident_info", {})
                case_number = accident_info.get("caseNumber", accident_info.get("case_number", "UNKNOWN"))
            llm_response = self.call_ollama(prompt, party_index=None, case_number=case_number)
            
            try:
                llm_response_clean = llm_response.strip()
                if "```json" in llm_response_clean:
                    start = llm_response_clean.find("```json") + 7
                    end = llm_response_clean.find("```", start)
                    llm_response_clean = llm_response_clean[start:end].strip()
                elif "```" in llm_response_clean:
                    start = llm_response_clean.find("```") + 3
                    end = llm_response_clean.find("```", start)
                    llm_response_clean = llm_response_clean[start:end].strip()
                
                decision_result = json.loads(llm_response_clean)
            except json.JSONDecodeError:
                decision_result = {
                    "decision": "PENDING",
                    "reasoning": "Could not parse LLM response as JSON",
                    "raw_response": llm_response
                }
            
            party_decisions = [{
                "decision": decision_result.get("decision", "PENDING"),
                "reasoning": decision_result.get("reasoning", ""),
                "timestamp": datetime.now().isoformat()
            }]
        
        # Build result
        result = {
            "timestamp": datetime.now().isoformat(),
            "case_number": accident_info.get("caseNumber", "Unknown"),
            "accident_info": accident_info,
            "parties": party_decisions,
            "total_parties": len(party_list),
            "model_used": self.model_name
        }
        
        return result
    
    def update_rules(self, new_rules: str):
        """Update the rules and conditions"""
        self.rules = new_rules
        # Also update in config manager
        try:
            config_manager.update_prompts({"main_prompt": new_rules})
        except Exception as e:
            print(f"Warning: Could not save rules to config: {e}")
    
    def reload_rules(self):
        """Reload rules from config manager"""
        self.rules = self._load_rules()
    
    def process_claim_from_file(self, file_path: str) -> Dict[str, Any]:
        """Process claim from a file (XML or JSON)"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Determine format from file extension
        if file_path.lower().endswith('.xml'):
            return self.process_claim(content, input_format='xml')
        elif file_path.lower().endswith('.json'):
            return self.process_claim(content, input_format='json')
        else:
            return self.process_claim(content, input_format='auto')


def main():
    """Example usage"""
    processor = ClaimProcessor(model_name="llama3.1:latest")
    
    # Example JSON claim
    example_json = """
    {
        "claim_id": "CLM-2024-001",
        "policy_number": "POL-12345",
        "accident_date": "2024-01-15",
        "claim_filed_date": "2024-01-16",
        "accident_type": "Vehicle Collision",
        "driver": {
            "name": "John Doe",
            "license_number": "DL123456",
            "license_valid": true
        },
        "vehicle": {
            "make": "Toyota",
            "model": "Camry",
            "year": 2020,
            "vin": "1HGBH41JXzzzMN109186"
        },
        "damage_description": "Front bumper and headlight damage",
        "estimated_repair_cost": 3500,
        "police_report_available": true,
        "fault_assessment": "Other party at fault"
    }
    """
    
    print("Processing claim...")
    result = processor.process_claim(example_json, input_format='json')
    print("\n" + "="*50)
    print("CLAIM DECISION RESULT")
    print("="*50)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

