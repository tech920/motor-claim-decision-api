"""
TP Claim Processing API Module
All TP claim processing logic is contained in this module.
Called from unified_api_server.py main router.
"""

import os
import sys
import json
import base64
import re
import time
import inspect
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
from flask import jsonify
from typing import Dict, List, Any

# Get TP directory FIRST - before any imports
TP_DIR = os.path.dirname(os.path.abspath(__file__))

# CRITICAL: Ensure TP directory is first in sys.path to prevent importing wrong modules
# Clear any cached modules that might interfere - be very aggressive
modules_to_clear = []
for k in list(sys.modules.keys()):
    # Clear any module with these names that's NOT from TP directory
    if any(x in k for x in ['claim_processor', 'config_manager', 'unified_processor', 'excel_ocr_license_processor']):
        # Only clear if it's NOT from TP directory
        if 'MotorclaimdecisionlinuxTP' not in k:
            modules_to_clear.append(k)
    # Also clear any CO modules
    if 'MotorclaimdecisionlinuxCO' in k:
        modules_to_clear.append(k)

for mod in modules_to_clear:
    try:
        del sys.modules[mod]
    except:
        pass

# Ensure TP directory is first in path for imports
if TP_DIR not in sys.path:
    sys.path.insert(0, TP_DIR)
elif sys.path[0] != TP_DIR:
    sys.path.remove(TP_DIR)
    sys.path.insert(0, TP_DIR)

# Import TP-specific modules - these MUST come from TP_DIR
# Use importlib to ensure we load from the correct path
import importlib.util
import importlib

# Explicitly load claim_processor from TP directory
claim_processor_path = os.path.abspath(os.path.join(TP_DIR, 'claim_processor.py'))
CLAIM_PROCESSOR_FILE_PATH = claim_processor_path  # Store for verification
if os.path.exists(claim_processor_path):
    # Use unique module name with timestamp to avoid cache conflicts
    import time
    unique_module_name = f'tp_claim_processor_{int(time.time() * 1000000)}'
    spec = importlib.util.spec_from_file_location(unique_module_name, claim_processor_path)
    claim_processor_module = importlib.util.module_from_spec(spec)
    # Store the file path in the module for later verification
    claim_processor_module.__file__ = claim_processor_path
    spec.loader.exec_module(claim_processor_module)
    ClaimProcessor = claim_processor_module.ClaimProcessor
    # Store the file path in the class for verification
    ClaimProcessor.__module_file__ = claim_processor_path
    ClaimProcessor.__file__ = claim_processor_path
else:
    # Fallback to regular import
    from claim_processor import ClaimProcessor
    try:
        CLAIM_PROCESSOR_FILE_PATH = inspect.getfile(ClaimProcessor)
    except:
        CLAIM_PROCESSOR_FILE_PATH = os.path.join(TP_DIR, 'claim_processor.py')

# Load other modules
excel_ocr_path = os.path.join(TP_DIR, 'excel_ocr_license_processor.py')
if os.path.exists(excel_ocr_path):
    spec = importlib.util.spec_from_file_location('tp_excel_ocr_license_processor', excel_ocr_path)
    excel_ocr_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(excel_ocr_module)
    ExcelOCRLicenseProcessor = excel_ocr_module.ExcelOCRLicenseProcessor
else:
    from excel_ocr_license_processor import ExcelOCRLicenseProcessor

unified_processor_path = os.path.join(TP_DIR, 'unified_processor.py')
if os.path.exists(unified_processor_path):
    spec = importlib.util.spec_from_file_location('tp_unified_processor', unified_processor_path)
    unified_processor_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(unified_processor_module)
    UnifiedClaimProcessor = unified_processor_module.UnifiedClaimProcessor
else:
    from unified_processor import UnifiedClaimProcessor

config_manager_path = os.path.join(TP_DIR, 'config_manager.py')
if os.path.exists(config_manager_path):
    spec = importlib.util.spec_from_file_location('tp_config_manager', config_manager_path)
    config_manager_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_manager_module)
    ConfigManager = config_manager_module.ConfigManager
else:
    from config_manager import ConfigManager

# Setup transaction logger for TP
# BASE_DIR should be the parent of TP_DIR (Motorclaimdecision_main)
BASE_DIR = os.path.dirname(TP_DIR)
LOG_DIR = os.path.join(BASE_DIR, "logs")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except PermissionError:
    # Fallback to TP directory if main logs directory not accessible
    LOG_DIR = os.path.join(TP_DIR, "logs")
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception as e:
    # If all else fails, use TP directory
    LOG_DIR = TP_DIR

# Daily transaction log file for TP
def get_transaction_logger():
    """Get or create transaction logger for TP"""
    logger_name = "tp_transaction_logger"
    if logger_name in logging.Logger.manager.loggerDict:
        return logging.getLogger(logger_name)
    
    transaction_logger = logging.getLogger(logger_name)
    transaction_logger.setLevel(logging.INFO)
    transaction_logger.propagate = False
    
    # Daily rotating log file
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_file = os.path.join(LOG_DIR, f"api_transactions_tp_{current_date}.log")
    
    handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        utc=False
    )
    handler.suffix = '%Y-%m-%d'
    
    formatter = logging.Formatter(
        '%(asctime)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    transaction_logger.addHandler(handler)
    
    return transaction_logger

transaction_logger = get_transaction_logger()

# Initialize TP processors
tp_config_file = os.path.join(TP_DIR, "claim_config.json")
tp_config_manager = ConfigManager(config_file=tp_config_file)

# Get Ollama configuration from config file or use defaults
tp_config_manager.reload_config()
tp_config = tp_config_manager.get_config()
ollama_config = tp_config.get("ollama", {})
ollama_url = ollama_config.get("base_url", os.getenv("OLLAMA_URL", "http://localhost:11434"))
ollama_model = ollama_config.get("model_name", os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))
ollama_translation_model = ollama_config.get("translation_model", os.getenv("OLLAMA_TRANSLATION_MODEL", "llama3.2:latest"))

# Log module initialization details
print(f"[TP_MODULE_INIT] TP Module File: {__file__}")
print(f"[TP_MODULE_INIT] TP Directory: {TP_DIR}")
print(f"[TP_MODULE_INIT] TP Config File: {tp_config_file}")
print(f"[TP_MODULE_INIT] Config Manager File: {tp_config_manager.config_file}")
print(f"[TP_MODULE_INIT] ClaimProcessor Module: {ClaimProcessor.__module__}")
print(f"[TP_MODULE_INIT] ClaimProcessor File: {os.path.abspath(ClaimProcessor.__module__.replace('.', '/') + '.py') if hasattr(ClaimProcessor, '__module__') else 'Unknown'}")

# Initialize processors with Ollama configuration
tp_processor = ClaimProcessor(
    ollama_base_url=ollama_url,
    model_name=ollama_model,
    translation_model=ollama_translation_model,
    check_ollama_health=False,  # Don't check on import to avoid blocking
    prewarm_model=False  # Don't prewarm on import
)
tp_ocr_license_processor = ExcelOCRLicenseProcessor()
tp_unified_processor = UnifiedClaimProcessor(
    ollama_base_url=ollama_url,
    model_name=ollama_model,
    translation_model=ollama_translation_model
)

# Log processor initialization with file paths - use stored path
if hasattr(ClaimProcessor, '__module_file__'):
    tp_processor_file = os.path.abspath(ClaimProcessor.__module_file__)
elif hasattr(ClaimProcessor, '__file__'):
    tp_processor_file = os.path.abspath(ClaimProcessor.__file__)
elif 'CLAIM_PROCESSOR_FILE_PATH' in globals():
    tp_processor_file = os.path.abspath(CLAIM_PROCESSOR_FILE_PATH)
else:
    try:
        tp_processor_file = os.path.abspath(inspect.getfile(tp_processor.__class__))
    except:
        tp_processor_file = os.path.abspath(os.path.join(TP_DIR, 'claim_processor.py'))

print(f"[TP_MODULE_INIT] TP Processor Type: {type(tp_processor).__name__}")
print(f"[TP_MODULE_INIT] TP Processor Module: {type(tp_processor).__module__}")
print(f"[TP_MODULE_INIT] TP Processor File: {tp_processor_file}")
print(f"[TP_MODULE_INIT] TP Processor File Exists: {os.path.exists(tp_processor_file) if tp_processor_file != 'Unknown' else False}")
print(f"[TP_MODULE_INIT] Expected TP Processor File: {os.path.join(TP_DIR, 'claim_processor.py')}")
print(f"[TP_MODULE_INIT] Files Match: {tp_processor_file == os.path.join(TP_DIR, 'claim_processor.py')}")
print(f"[TP_MODULE_INIT] TP Processor Ollama URL: {tp_processor.ollama_base_url}")
print(f"[TP_MODULE_INIT] TP Processor Model: {tp_processor.model_name}")
print(f"[TP_MODULE_INIT] TP Unified Processor Type: {type(tp_unified_processor).__name__}")
print(f"[TP_MODULE_INIT] TP Unified Processor Module: {type(tp_unified_processor).__module__}")


def _validate_recovery_decision_api(current_party_idx: int, current_party_info: Dict[str, Any], 
                                    all_parties: List[Dict], accident_date: str,
                                    transaction_logger, case_number: str) -> Dict[str, Any]:
    """
    Validate ACCEPTED_WITH_RECOVERY decision - SAME LOGIC AS EXCEL unified_processor._validate_recovery_decision
    
    Rules for ACCEPTED_WITH_RECOVERY:
    1. Must apply to the victim party (Liability < 100%)
    2. There must be at least one other party with Liability > 0% (the one causing the accident)
    3. Recovery violations can be found in:
       - Current party's own recovery conditions (Recovery field, Act_Violation, License_Expiry_Date, etc.)
       - Other at-fault parties' recovery conditions
       - Recovery = TRUE, OR
       - model_recovery = TRUE (License_Type_From_Make_Model mismatch), OR
       - One of the specific violations (wrong way, red light, etc.)
    """
    import re
    
    current_liability = current_party_info.get("Liability", 0)
    current_recovery = str(current_party_info.get("Recovery", "")).strip()
    current_recovery_upper = current_recovery.upper()
    
    # Check current party's model_recovery (SAME AS EXCEL)
    current_license_type_make_model = str(current_party_info.get("License_Type_From_Make_Model", "")).strip()
    current_license_type_request = str(current_party_info.get("License_Type_From_Request", "")).strip()
    
    # Normalize values
    if current_license_type_make_model.lower() in ["none", "nan", "null"]:
        current_license_type_make_model = ""
    if current_license_type_request.lower() in ["none", "nan", "null"]:
        current_license_type_request = ""
    
    # Check model_recovery condition (SAME AS EXCEL)
    current_make_model_valid = (current_license_type_make_model and 
                               current_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               current_license_type_make_model.upper() != "ANY LICENSE")
    current_request_is_none_or_empty = (not current_license_type_request or 
                                       current_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
    current_request_mismatch = (current_license_type_request and 
                               current_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               current_license_type_make_model.upper() != current_license_type_request.upper())
    current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
    
    # Initialize recovery analysis
    current_party_recovery_analysis = {
        "recovery_field": current_recovery,
        "has_recovery_field": current_recovery_upper in ["TRUE", "1", "YES", "Y"] or current_recovery in ["True", "true", "TRUE"],
        "model_recovery": current_has_model_recovery,
        "has_model_recovery": current_has_model_recovery,
        "act_violation": str(current_party_info.get("Act_Violation", "")).strip(),
        "license_expiry_date": str(current_party_info.get("License_Expiry_Date", "")).strip(),
        "license_type_from_make_model": current_license_type_make_model,
        "license_type_from_request": current_license_type_request,
        "violations_found": []
    }
    
    # Rule 1: ACCEPTED_WITH_RECOVERY should only apply to parties with liability < 100%
    if current_liability >= 100:
        return {
            "is_valid": False,
            "reason": f"ACCEPTED_WITH_RECOVERY can only apply to parties with liability < 100%, but this party has Liability={current_liability}%",
            "corrected_decision": "REJECTED",
            "recovery_reasons": [],
            "current_party_recovery_analysis": current_party_recovery_analysis
        }
    
    # Rule 2: Check CURRENT PARTY's own recovery conditions first
    recovery_violations_found = False
    recovery_reasons = []
    
    # Check current party's Recovery field
    if current_recovery_upper in ["TRUE", "1", "YES", "Y"] or current_recovery in ["True", "true", "TRUE"]:
        recovery_violations_found = True
        recovery_reasons.append(f"Current Party {current_party_idx + 1} has Recovery=True/TRUE/true")
        current_party_recovery_analysis["violations_found"].append("Recovery field is True/TRUE/true")
    
    # Check current party's model_recovery field (SAME AS Recovery logic)
    if current_has_model_recovery:
        recovery_violations_found = True
        recovery_reasons.append(f"Current Party {current_party_idx + 1} has model_recovery=True")
        current_party_recovery_analysis["violations_found"].append("model_recovery field is True")
    
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
    
    # Check license type mismatch
    license_type_make_model = current_party_recovery_analysis["license_type_from_make_model"]
    license_type_request = current_party_recovery_analysis["license_type_from_request"]
    if (license_type_make_model and 
        license_type_make_model.lower() not in ["not identify", "not identified", ""] and
        license_type_request and 
        license_type_request.lower() not in ["not identify", "not identified", ""] and
        license_type_make_model.upper() != "ANY LICENSE"):
        if license_type_make_model.upper() != license_type_request.upper():
            if license_type_make_model.upper() not in license_type_request.upper() and \
               license_type_request.upper() not in license_type_make_model.upper():
                recovery_violations_found = True
                recovery_reasons.append(f"Current Party {current_party_idx + 1} has license type mismatch: {license_type_make_model} vs {license_type_request}")
                current_party_recovery_analysis["violations_found"].append("License type mismatch")
    
    # Rule 3: Check if there are other parties with Liability > 0% (the ones causing the accident)
    at_fault_parties = []
    for idx, other_party in enumerate(all_parties):
        if idx == current_party_idx:
            continue
        
        other_liability = other_party.get("Liability", 0)
        if other_liability > 0:
            at_fault_parties.append({
                "idx": idx,
                "party": other_party
            })
    
    # Rule 4: Check other at-fault parties for recovery conditions (if current party doesn't have recovery)
    if not recovery_violations_found and at_fault_parties:
        for at_fault_party in at_fault_parties:
            other_party = at_fault_party["party"]
            
            # Check Recovery field
            recovery_field = str(other_party.get("Recovery", "")).strip()
            recovery_field_upper = recovery_field.upper()
            if recovery_field_upper in ["TRUE", "1", "YES", "Y"] or recovery_field in ["True", "true", "TRUE"]:
                recovery_violations_found = True
                recovery_reasons.append(f"At-fault Party {at_fault_party['idx'] + 1} has Recovery=True/TRUE/true")
                continue
            
            # Check model_recovery (SAME AS EXCEL)
            other_license_type_make_model = str(other_party.get("License_Type_From_Make_Model", "")).strip()
            other_license_type_request = str(other_party.get("License_Type_From_Request", "")).strip()
            
            if other_license_type_make_model.lower() in ["none", "nan", "null"]:
                other_license_type_make_model = ""
            if other_license_type_request.lower() in ["none", "nan", "null"]:
                other_license_type_request = ""
            
            other_make_model_valid = (other_license_type_make_model and 
                                     other_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                     other_license_type_make_model.upper() != "ANY LICENSE")
            other_request_is_none_or_empty = (not other_license_type_request or 
                                             other_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
            other_request_mismatch = (other_license_type_request and 
                                     other_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                     other_license_type_make_model.upper() != other_license_type_request.upper())
            other_has_model_recovery = other_make_model_valid and (other_request_is_none_or_empty or other_request_mismatch)
            
            if other_has_model_recovery:
                recovery_violations_found = True
                recovery_reasons.append(f"At-fault Party {at_fault_party['idx'] + 1} has model_recovery=True")
                continue
            
            # Check Act/Violation
            act_violation = str(other_party.get("Act_Violation", "")).strip().upper()
            if act_violation:
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
    
    # If no recovery violations found, decision is invalid
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
        "recovery_reasons": recovery_reasons,
        "current_party_recovery_analysis": current_party_recovery_analysis
    }


def process_tp_claim(data):
    """
    Process TP claim - ALL functionality from TP path (MotorclaimdecisionlinuxTP/)
    
    This is the main entry point for TP claim processing.
    All processing logic is contained within this TP directory.
    
    Args:
        data: Request JSON data containing claim information
        
    Returns:
        Flask response with processed claim results
    """
    # Start timing for entire request
    import time
    request_start_time = time.time()
    request_start_datetime = datetime.now()
    
    try:
        case_number = data.get("Case_Number", "")
        
        transaction_logger.info(
            f"TP_REQUEST_START | Case: {case_number} | "
            f"Start_Time: {request_start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')} | "
            f"Timestamp: {request_start_time}"
        )
        
        # Verify we're using TP processors and config
        tp_config_manager.reload_config()
        current_config_file = tp_config_manager.config_file
        
        # Get detailed information about the processing environment
        import inspect
        tp_module_file = os.path.abspath(__file__)
        
        # Get actual file paths using stored paths or inspect
        # Use stored path first (most reliable for dynamically loaded modules)
        if hasattr(ClaimProcessor, '__module_file__'):
            claim_processor_file = os.path.abspath(ClaimProcessor.__module_file__)
        elif hasattr(ClaimProcessor, '__file__'):
            claim_processor_file = os.path.abspath(ClaimProcessor.__file__)
        elif 'CLAIM_PROCESSOR_FILE_PATH' in globals():
            claim_processor_file = os.path.abspath(CLAIM_PROCESSOR_FILE_PATH)
        else:
            try:
                claim_processor_file = os.path.abspath(inspect.getfile(ClaimProcessor))
            except:
                claim_processor_file = os.path.abspath(os.path.join(TP_DIR, 'claim_processor.py'))
        
        try:
            config_manager_file = os.path.abspath(inspect.getfile(ConfigManager))
        except:
            config_manager_file = os.path.abspath(os.path.join(TP_DIR, 'config_manager.py'))
        
        try:
            unified_processor_file = os.path.abspath(inspect.getfile(UnifiedClaimProcessor))
        except:
            unified_processor_file = os.path.abspath(os.path.join(TP_DIR, 'unified_processor.py'))
        
        # Verify processor is from TP directory
        expected_processor_file = os.path.join(TP_DIR, 'claim_processor.py')
        processor_file_correct = os.path.abspath(claim_processor_file) == os.path.abspath(expected_processor_file) if claim_processor_file != "Unknown" else False
        
        if not processor_file_correct:
            error_msg = f"CRITICAL: TP Processor loaded from wrong file! Expected: {expected_processor_file}, Got: {claim_processor_file}"
            transaction_logger.error(f"TP_PROCESSOR_PATH_ERROR | {error_msg}")
            print(f"[ERROR] {error_msg}")
        
        # Get current working directory
        current_working_dir = os.getcwd()
        
        # Log comprehensive processing start information
        # Get model info for performance tracking
        current_model = tp_processor.model_name
        previous_model = "qwen2.5:3b"  # Previous model for comparison (baseline)
        
        transaction_logger.info(
            f"TP_CLAIM_PROCESSING_START | Case: {case_number} | "
            f"TP_Module_File: {tp_module_file} | "
            f"TP_Directory: {TP_DIR} | "
            f"Current_Working_Dir: {current_working_dir} | "
            f"TP_Config_File: {tp_config_file} | "
            f"Current_Config_File: {current_config_file} | "
            f"Config_Match: {tp_config_file == current_config_file} | "
            f"TP_Processor_Type: {type(tp_processor).__name__} | "
            f"TP_Processor_Module: {type(tp_processor).__module__} | "
            f"TP_Processor_File: {claim_processor_file} | "
            f"Expected_Processor_File: {expected_processor_file} | "
            f"Processor_File_Correct: {processor_file_correct} | "
            f"TP_Config_Manager_File: {config_manager_file} | "
            f"TP_Unified_Processor_File: {unified_processor_file} | "
            f"TP_Processor_Ollama_URL: {tp_processor.ollama_base_url} | "
            f"TP_Processor_Model: {current_model} | "
            f"TP_Processor_Translation_Model: {getattr(tp_processor, 'translation_model', 'N/A')} | "
            f"Model_Changed: {current_model != previous_model} | "
            f"Previous_Model: {previous_model}"
        )
        
        # Verify config file is correct
        if tp_config_file != current_config_file:
            error_msg = f"TP Config file mismatch! Expected: {tp_config_file}, Got: {current_config_file}"
            transaction_logger.error(f"TP_CONFIG_ERROR | {error_msg}")
            return jsonify({"error": error_msg}), 500
        
        # ========== HANDLE HTML/XML/JSON DATA (SAME AS EXCEL) ==========
        # Excel uses clean_data() and detect_and_convert() to handle HTML/XML/JSON strings
        # If data is a string (HTML/XML/JSON), clean and parse it using SAME logic as Excel
        data_cleaning_start = time.time()
        if isinstance(data, str):
            transaction_logger.info(
                f"TP_DATA_CLEANING_START | Case: {case_number} | "
                f"Data_Type: string | Data_Length: {len(data)} | "
                f"Data_Preview: {data[:200]} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
            
            # Use unified processor's clean_data() method (SAME AS EXCEL)
            data_cleaned = tp_unified_processor.clean_data(data)
            data_cleaning_time = time.time() - data_cleaning_start
            
            transaction_logger.info(
                f"TP_DATA_CLEANED | Case: {case_number} | "
                f"Cleaned_Length: {len(data_cleaned)} | "
                f"Cleaned_Preview: {data_cleaned[:200]} | "
                f"Cleaning_Time_Seconds: {data_cleaning_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
            
            # Use unified processor's detect_and_convert() method (SAME AS EXCEL)
            # This handles XML/JSON detection and conversion
            data_conversion_start = time.time()
            try:
                data = tp_unified_processor.detect_and_convert(data_cleaned)
                data_conversion_time = time.time() - data_conversion_start
                transaction_logger.info(
                    f"TP_DATA_CONVERTED | Case: {case_number} | "
                    f"Format_Detected: {'XML' if data_cleaned.strip().startswith('<') else 'JSON'} | "
                    f"Converted_Type: {type(data).__name__} | "
                    f"Conversion_Time_Seconds: {data_conversion_time:.4f} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
            except Exception as e:
                error_msg = f"Failed to parse HTML/XML/JSON data: {str(e)[:200]}"
                transaction_logger.error(
                    f"TP_DATA_PARSE_ERROR | Case: {case_number} | Error: {error_msg} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
                return jsonify({"error": error_msg}), 400
        else:
            data_cleaning_time = time.time() - data_cleaning_start
            transaction_logger.info(
                f"TP_DATA_SKIP_CLEANING | Case: {case_number} | "
                f"Data_Type: {type(data).__name__} (not string, skipping cleaning) | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Also check if data contains a "Request" field with HTML/XML/JSON string (SAME AS EXCEL)
        # Excel processes data from "Request" column which may contain HTML/XML/JSON strings
        request_field_start = time.time()
        if isinstance(data, dict) and "Request" in data:
            request_data = data.get("Request")
            if isinstance(request_data, str) and (request_data.strip().startswith('<') or request_data.strip().startswith('{')):
                transaction_logger.info(
                    f"TP_REQUEST_FIELD_FOUND | Case: {case_number} | "
                    f"Request_Field_Length: {len(request_data)} | "
                    f"Request_Field_Preview: {request_data[:200]} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
                
                # Clean and parse Request field (SAME AS EXCEL)
                request_cleaned = tp_unified_processor.clean_data(request_data)
                request_parsing_start = time.time()
                try:
                    request_parsed = tp_unified_processor.detect_and_convert(request_cleaned)
                    request_parsing_time = time.time() - request_parsing_start
                    # Merge parsed request data into main data dict (SAME AS EXCEL)
                    if isinstance(request_parsed, dict):
                        # Merge request data into main data, with request data taking precedence
                        data = {**data, **request_parsed}
                        transaction_logger.info(
                            f"TP_REQUEST_FIELD_PARSED | Case: {case_number} | "
                            f"Format_Detected: {'XML' if request_cleaned.strip().startswith('<') else 'JSON'} | "
                            f"Merged_Fields: {list(request_parsed.keys())[:10]} | "
                            f"Parsing_Time_Seconds: {request_parsing_time:.4f} | "
                            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                        )
                except Exception as e:
                    request_parsing_time = time.time() - request_parsing_start
                    transaction_logger.warning(
                        f"TP_REQUEST_FIELD_PARSE_WARNING | Case: {case_number} | "
                        f"Failed to parse Request field, using original data | Error: {str(e)[:200]} | "
                        f"Parsing_Time_Seconds: {request_parsing_time:.4f} | "
                        f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                    )
        request_field_time = time.time() - request_field_start
        if request_field_time > 0.001:  # Only log if significant time spent
            transaction_logger.info(
                f"TP_REQUEST_FIELD_CHECK | Case: {case_number} | "
                f"Check_Time_Seconds: {request_field_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Extract request data - USE SAME LOGIC AS EXCEL unified_processor
        data_extraction_start = time.time()
        # Extract accident info (same as Excel extract_accident_info)
        accident_data = data.get("Accident_info", {})
        if not accident_data:
            # Try Case_Info structure (same as Excel)
            case_info = data.get("Case_Info", {})
            if case_info:
                accident_data = case_info.get("Accident_info", {})
        
        transaction_logger.info(
            f"TP_DATA_EXTRACTION_START | Case: {case_number} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Extract accident fields (same as Excel extract_accident_info - lines 4277-4303)
        accident_date = (
            accident_data.get("callDate") or
            accident_data.get("call_date") or
            data.get("Accident_Date", "")
        )
        upload_date = data.get("Upload_Date", "")
        claim_requester_id = data.get("Claim_requester_ID", None)
        accident_description = (
            accident_data.get("AccidentDescription") or
            accident_data.get("accident_description") or
            data.get("accident_description", "")
        )
        ld_rep_base64 = data.get("Name_LD_rep_64bit", "")
        
        # Extract DAA parameters - USE SAME LOGIC AS EXCEL (lines 4355-4426)
        # Excel tries multiple locations and field name variations
        daa_extraction_start = time.time()
        daa_from_request = {
            'isDAA': None,
            'Suspect_as_Fraud': None,
            'DaaReasonEnglish': None
        }
        
        # Try multiple possible locations (same as Excel)
        accident_info_raw = None
        if isinstance(data, dict):
            # Try EICWS structure
            if "EICWS" in data:
                case_info = data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
                accident_info_raw = case_info.get("Accident_info", {})
            # Try cases structure
            elif "cases" in data:
                case_info = data.get("cases", {}).get("Case_Info", {})
                accident_info_raw = case_info.get("Accident_info", {})
            # Try Case_Info structure
            elif "Case_Info" in data:
                accident_info_raw = data.get("Case_Info", {}).get("Accident_info", {})
            # Try direct accident_info
            elif "Accident_info" in data:
                accident_info_raw = data.get("Accident_info", {})
            # Try at root level
            if not accident_info_raw:
                accident_info_raw = data
        
        # Extract DAA values (EXACT Excel logic - lines 4388-4426)
        if accident_info_raw:
            # Try various field name variations (same as Excel)
            isDAA_value = (
                accident_info_raw.get("isDAA") or
                accident_info_raw.get("is_daa") or
                accident_info_raw.get("IsDAA") or
                data.get("isDAA") or  # Also check root level
                None
            )
            if isDAA_value is not None:
                # Convert to string and normalize (EXACT Excel logic)
                isDAA_str = str(isDAA_value).strip().upper()
                # Normalize boolean values (same as Excel)
                if isDAA_str in ['TRUE', '1', 'YES', 'Y', 'T']:
                    daa_from_request['isDAA'] = 'TRUE'
                    isDAA = True
                elif isDAA_str in ['FALSE', '0', 'NO', 'N', 'F']:
                    daa_from_request['isDAA'] = 'FALSE'
                    isDAA = False
                else:
                    daa_from_request['isDAA'] = isDAA_str
                    isDAA = isDAA_value
            else:
                isDAA = data.get("isDAA", None)
            
            suspect_fraud_value = (
                accident_info_raw.get("Suspect_as_Fraud") or
                accident_info_raw.get("suspect_as_fraud") or
                accident_info_raw.get("SuspectAsFraud") or
                data.get("Suspect_as_Fraud") or  # Also check root level
                None
            )
            if suspect_fraud_value is not None:
                suspect_as_fraud = str(suspect_fraud_value).strip()
                daa_from_request['Suspect_as_Fraud'] = suspect_as_fraud
            else:
                suspect_as_fraud = data.get("Suspect_as_Fraud", None)
            
            daa_reason_value = (
                accident_info_raw.get("DaaReasonEnglish") or
                accident_info_raw.get("daa_reason_english") or
                accident_info_raw.get("DaaReason") or
                accident_info_raw.get("daaReasonEnglish") or
                data.get("DaaReasonEnglish") or  # Also check root level
                None
            )
            if daa_reason_value is not None:
                daa_reason_english = str(daa_reason_value).strip()
                daa_from_request['DaaReasonEnglish'] = daa_reason_english
            else:
                daa_reason_english = data.get("DaaReasonEnglish", None)
        else:
            # Fallback to root level (same as Excel)
            isDAA = data.get("isDAA", None)
            suspect_as_fraud = data.get("Suspect_as_Fraud", None)
            daa_reason_english = data.get("DaaReasonEnglish", None)
        
        # Log DAA extraction (same as Excel)
        daa_extraction_time = time.time() - daa_extraction_start
        if any([isDAA, suspect_as_fraud, daa_reason_english]):
            transaction_logger.info(
                f"TP_DAA_EXTRACTED | Case: {case_number} | "
                f"isDAA: {isDAA} | Suspect_as_Fraud: {suspect_as_fraud} | "
                f"DaaReasonEnglish: {daa_reason_english[:50] if daa_reason_english else None} | "
                f"DAA_Extraction_Time_Seconds: {daa_extraction_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        else:
            transaction_logger.info(
                f"TP_DAA_EXTRACTION_COMPLETE | Case: {case_number} | "
                f"No DAA data found | DAA_Extraction_Time_Seconds: {daa_extraction_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Process OCR
        ocr_processing_start = time.time()
        ocr_text = None
        if ld_rep_base64:
            transaction_logger.info(
                f"TP_OCR_PROCESSING_START | Case: {case_number} | "
                f"LD_Rep_Base64_Length: {len(ld_rep_base64)} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
            try:
                if ld_rep_base64.startswith('data:text') or ld_rep_base64.startswith('data:image'):
                    if ',' in ld_rep_base64:
                        base64_part = ld_rep_base64.split(',')[1]
                    else:
                        base64_part = ld_rep_base64
                else:
                    base64_part = ld_rep_base64
                
                try:
                    decoded = base64.b64decode(base64_part).decode('utf-8', errors='ignore')
                    if '<html' in decoded.lower() or 'party' in decoded.lower() or 'رخصة' in decoded:
                        ocr_text = decoded
                        transaction_logger.info(f"TP_OCR_TEXT_EXTRACTED | Case: {case_number}")
                except:
                    pass
            except Exception as e:
                transaction_logger.error(f"TP_BASE64_PROCESSING_ERROR | Case: {case_number} | Error: {str(e)[:100]}")
        
        # Process OCR with TP OCR processor - SAME AS EXCEL
        # Excel translates OCR text to English for better extraction (unified_processor.py lines 4862-4876)
        ocr_text_for_processing = ocr_text
        if ocr_text:
            try:
                # CRITICAL: Translate OCR text to English (SAME AS EXCEL)
                # Excel uses translate_ocr_to_english for better extraction
                has_arabic = bool(re.search(r'[\u0600-\u06FF]', ocr_text) if ocr_text else False)
                transaction_logger.info(
                    f"TP_OCR_TRANSLATION_START | Case: {case_number} | "
                    f"OCR_Text_Length: {len(ocr_text) if ocr_text else 0} | "
                    f"Has_Arabic: {has_arabic} | "
                    f"OCR_Text_Preview: {ocr_text[:500] if ocr_text else 'N/A'}"
                )
                
                # Translate OCR text to English (same as Excel - unified_processor.py lines 4862-4876)
                if ocr_text and has_arabic:
                    try:
                        # Use unified processor's translate_ocr_to_english method (same as Excel)
                        if hasattr(tp_unified_processor, 'translate_ocr_to_english'):
                            transaction_logger.info(
                                f"TP_OCR_TRANSLATION_CALLING | Case: {case_number} | "
                                f"Method: translate_ocr_to_english | "
                                f"Translation_Model: {getattr(tp_unified_processor, 'translation_model', 'llama3.2:latest')}"
                            )
                            ocr_text_translated = tp_unified_processor.translate_ocr_to_english(ocr_text)
                            if ocr_text_translated and ocr_text_translated != ocr_text:
                                ocr_text_for_processing = ocr_text_translated
                                transaction_logger.info(
                                    f"TP_OCR_TRANSLATION_SUCCESS | Case: {case_number} | "
                                    f"Original_Length: {len(ocr_text)} | "
                                    f"Translated_Length: {len(ocr_text_translated)} | "
                                    f"Original_Preview: {ocr_text[:200]} | "
                                    f"Translated_Preview: {ocr_text_translated[:200]}"
                                )
                            else:
                                transaction_logger.info(
                                    f"TP_OCR_TRANSLATION_SKIPPED | Case: {case_number} | "
                                    f"Translation returned same/empty text, using original | "
                                    f"Original: {ocr_text[:200]} | Translated: {ocr_text_translated[:200] if ocr_text_translated else 'N/A'}"
                                )
                        else:
                            # Fallback to _translate_arabic_to_english if translate_ocr_to_english not available
                            transaction_logger.warning(
                                f"TP_OCR_TRANSLATION_METHOD_NOT_FOUND | Case: {case_number} | "
                                f"translate_ocr_to_english not found, using _translate_arabic_to_english"
                            )
                            if hasattr(tp_unified_processor, '_translate_arabic_to_english'):
                                transaction_logger.info(
                                    f"TP_OCR_TRANSLATION_CALLING | Case: {case_number} | "
                                    f"Method: _translate_arabic_to_english | "
                                    f"Translation_Model: {getattr(tp_unified_processor, 'translation_model', 'llama3.2:latest')}"
                                )
                                ocr_text_translated = tp_unified_processor._translate_arabic_to_english(ocr_text)
                                if ocr_text_translated and ocr_text_translated != ocr_text:
                                    ocr_text_for_processing = ocr_text_translated
                                    transaction_logger.info(
                                        f"TP_OCR_TRANSLATION_SUCCESS | Case: {case_number} | "
                                        f"Using _translate_arabic_to_english | "
                                        f"Original_Length: {len(ocr_text)} | "
                                        f"Translated_Length: {len(ocr_text_translated)} | "
                                        f"Original_Preview: {ocr_text[:200]} | "
                                        f"Translated_Preview: {ocr_text_translated[:200]}"
                                    )
                    except Exception as e:
                        transaction_logger.error(
                            f"TP_OCR_TRANSLATION_ERROR | Case: {case_number} | "
                            f"Error: {str(e)[:500]} | Error_Type: {type(e).__name__} | "
                            f"Using original OCR text"
                        )
                        ocr_text_for_processing = ocr_text
                else:
                    transaction_logger.info(
                        f"TP_OCR_TRANSLATION_SKIPPED | Case: {case_number} | "
                        f"No Arabic text detected (Has_Arabic: {has_arabic}), using original OCR text"
                    )
                
                # Process with translated OCR text (same as Excel)
                ocr_validation_start = time.time()
                data = tp_ocr_license_processor.process_claim_data_with_ocr(
                    claim_data=data,
                    ocr_text=ocr_text_for_processing,  # Use translated text
                    base64_image=ld_rep_base64 if not ocr_text_for_processing else None
                )
                ocr_validation_time = time.time() - ocr_validation_start
                ocr_processing_time = time.time() - ocr_processing_start
                transaction_logger.info(
                    f"TP_OCR_VALIDATION_SUCCESS | Case: {case_number} | "
                    f"OCR_Text_Used: Translated={ocr_text_for_processing != ocr_text} | "
                    f"OCR_Text_Length: {len(ocr_text_for_processing) if ocr_text_for_processing else 0} | "
                    f"OCR_Validation_Time_Seconds: {ocr_validation_time:.4f} | "
                    f"OCR_Total_Processing_Time_Seconds: {ocr_processing_time:.4f} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
            except Exception as e:
                ocr_processing_time = time.time() - ocr_processing_start
                transaction_logger.error(
                    f"TP_OCR_VALIDATION_ERROR | Case: {case_number} | Error: {str(e)[:200]} | "
                    f"OCR_Processing_Time_Seconds: {ocr_processing_time:.4f} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
        else:
            transaction_logger.info(
                f"TP_OCR_SKIPPED | Case: {case_number} | "
                f"No LD_rep_base64 provided | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Build accident info - USE SAME LOGIC AS EXCEL extract_accident_info (lines 4277-4303)
        accident_info_start = time.time()
        # Extract all accident fields (same field name variations as Excel)
        case_number_extracted = (
            accident_data.get("caseNumber") or
            accident_data.get("case_number") or
            case_number
        )
        surveyor = accident_data.get("surveyorName", accident_data.get("surveyor_name", ""))
        call_date_extracted = (
            accident_data.get("callDate") or
            accident_data.get("call_date") or
            accident_date
        )
        call_time = accident_data.get("callTime", accident_data.get("call_time", ""))
        city = accident_data.get("city", accident_data.get("City", ""))
        location = accident_data.get("location", accident_data.get("Location", ""))
        coordinates = accident_data.get("LocationCoordinates", accident_data.get("location_coordinates", ""))
        landmark = accident_data.get("landmark", accident_data.get("Landmark", ""))
        description_extracted = (
            accident_data.get("AccidentDescription") or
            accident_data.get("accident_description") or
            accident_description
        )
        
        # Build accident_info (same structure as Excel extract_accident_info)
        if description_extracted:
            accident_desc = description_extracted
        else:
            accident_desc = f"Case: {case_number_extracted}, Date: {call_date_extracted}"
        
        accident_info = {
            # Core fields (same as Excel extract_accident_info)
            "caseNumber": case_number_extracted,
            "case_number": case_number_extracted,
            "Case_Number": case_number_extracted,
            "Surveyor": surveyor,
            "surveyorName": surveyor,
            "surveyor_name": surveyor,
            "Call_Date": call_date_extracted,
            "callDate": call_date_extracted,
            "call_date": call_date_extracted,
            "Call_Time": call_time,
            "callTime": call_time,
            "call_time": call_time,
            "City": city,
            "city": city,
            "Location": location,
            "location": location,
            "Coordinates": coordinates,
            "LocationCoordinates": coordinates,
            "location_coordinates": coordinates,
            "Landmark": landmark,
            "landmark": landmark,
            "AccidentDescription": accident_desc,
            "accident_description": accident_desc,
            "Description": accident_desc,
            # Additional fields for API compatibility
            "Upload_Date": upload_date,
            "Claim_requester_ID": claim_requester_id,
            "Name_LD_rep_64bit": ld_rep_base64,
            "isDAA": isDAA,
            "Suspect_as_Fraud": suspect_as_fraud,
            "DaaReasonEnglish": daa_reason_english
        }
        
        accident_info_time = time.time() - accident_info_start
        data_extraction_time = time.time() - data_extraction_start  # Stop data extraction timer
        transaction_logger.info(
            f"TP_ACCIDENT_INFO_BUILT | Case: {case_number} | "
            f"Accident_Info_Build_Time_Seconds: {accident_info_time:.4f} | "
            f"Data_Extraction_Total_Time_Seconds: {data_extraction_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # TP processes ALL parties (no filtering)
        transaction_logger.info(
            f"TP_PROCESSING_ALL_PARTIES | Case: {case_number} | "
            f"Total_Parties: {len(data.get('Parties', []))} | No_Filtering_Applied | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Convert parties for TP processing - USE SAME LOGIC AS EXCEL unified_processor
        # This ensures 100% accuracy match with Excel processing
        party_conversion_start = time.time()
        converted_parties = []
        
        for idx, party in enumerate(data["Parties"]):
            insurance_type = "TP"
            
            # Use unified_processor.extract_party_info logic for field extraction
            # Handle all field name variations (same as Excel)
            party_id = party.get("ID", party.get("id", party.get("Id", party.get("Party_ID", ""))))
            name = party.get("name", party.get("Name", party.get("Party_Name", "")))
            liability = party.get("Liability", party.get("liability", 0))
            try:
                liability = int(liability) if liability else 0
            except:
                liability = 0
            
            # Extract insurance info (same as Excel - handles multiple structures)
            insurance_info_raw = party.get("Insurance_Info", {})
            if not insurance_info_raw:
                insurance_info_raw = party.get("insurance_info", {})
            if not insurance_info_raw:
                insurance_info_raw = party.get("InsuranceInfo", {})
            
            # Extract insurance name (same as Excel logic)
            insurance_name_arabic = insurance_info_raw.get("ICArabicName", insurance_info_raw.get("ic_arabic_name", ""))
            insurance_name_english = (
                insurance_info_raw.get("ICEnglishName") or
                insurance_info_raw.get("ic_english_name") or
                insurance_info_raw.get("EnglishNam") or
                insurance_info_raw.get("english_nam") or
                insurance_info_raw.get("EnglishName") or
                insurance_info_raw.get("english_name") or
                party.get("ICEnglishName") or  # Check top level (Excel logic)
                party.get("ic_english_name") or
                party.get("EnglishNam") or
                party.get("english_nam") or
                party.get("EnglishName") or
                party.get("english_name") or
                party.get("Insurance_Name", "") or  # Fallback to Insurance_Name
                ""
            )
            insurance_name = insurance_name_arabic if insurance_name_arabic else insurance_name_english
            
            # Build insurance_info (same structure as Excel)
            # Extract Policyholder_ID - check multiple locations (same as Excel)
            policy_number = (
                party.get("Policyholder_ID") or
                party.get("PolicyholderID") or
                party.get("policyholder_id") or
                insurance_info_raw.get("policyNumber") or
                insurance_info_raw.get("policy_number") or
                ""
            )
            
            # Extract Policyholdername from party data (optional parameter)
            # Supports multiple field name variations
            policyholder_name = (
                party.get("Policyholdername") or
                party.get("Policyholder_Name") or
                party.get("PolicyholderName") or
                party.get("policyholder_name") or
                party.get("Policy_Holder_Name") or
                ""
            )
            if not policy_number:
                policy_number = ""
            vehicle_id = insurance_info_raw.get("vehicleID", insurance_info_raw.get("vehicle_id", party.get("Vehicle_Serial", "")))
            
            insurance_info = {
                "ICArabicName": insurance_name_arabic if insurance_name_arabic else insurance_name,
                "ICEnglishName": insurance_name_english if insurance_name_english else insurance_name,
                "policyNumber": policy_number,
                "insuranceCompanyID": insurance_info_raw.get("insuranceCompanyID", ""),
                "vehicleID": vehicle_id,
                "insuranceType": insurance_type
            }
            
            # Extract car make/model (same as Excel processing - handles multiple field names)
            car_make = (
                party.get("carMake") or
                party.get("car_make") or
                party.get("carMake_Najm") or
                party.get("Vehicle_Make") or
                party.get("vehicle_make") or
                ""
            )
            car_model = (
                party.get("carModel") or
                party.get("car_model") or
                party.get("carModel_Najm") or
                party.get("Vehicle_Model") or
                party.get("vehicle_model") or
                ""
            )
            car_year = party.get("carMfgYear", party.get("car_year", party.get("Vehicle_Year", "")))
            
            # Extract other fields (same as Excel extract_party_info)
            chassis_no = party.get("chassisNo", party.get("chassis_no", party.get("Vehicle_Serial", party.get("Chassis_No", ""))))
            vehicle_owner_id = party.get("VehicleOwnerId", party.get("vehicleOwnerId", party.get("vehicle_owner_id", "")))
            license_type_from_request = party.get("licenseType", party.get("license_type", party.get("License_Type_From_Najm", "")))
            recovery = party.get("recovery", party.get("Recovery", False))
            
            # Extract damage info (same as Excel)
            damages = party.get("Damages", {})
            damage_type = ""
            if damages:
                damage_info = damages.get("Damage_Info", {})
                if isinstance(damage_info, list) and len(damage_info) > 0:
                    damage_info = damage_info[0]
                if isinstance(damage_info, dict):
                    damage_type = damage_info.get("damageType", damage_info.get("damage_type", ""))
            
            # Extract Act/Violation (same as Excel)
            acts = party.get("Acts", {})
            act_description = ""
            if acts:
                act_info = acts.get("Act_Info", {})
                if isinstance(act_info, list) and len(act_info) > 0:
                    act_info = act_info[0]
                if isinstance(act_info, dict):
                    act_description = act_info.get("actEnglish", act_info.get("act_english", ""))
                    if not act_description:
                        act_description = act_info.get("actArabic", act_info.get("act_arabic", ""))
            
            # CRITICAL: Add License_Type_From_Make_Model BEFORE processing (same as Excel)
            # This ensures 100% accuracy match with Excel processing
            license_type_from_make_model = ""
            if car_make and car_model:
                try:
                    license_type_from_make_model = tp_unified_processor.lookup_license_type_from_make_model(car_make, car_model)
                    transaction_logger.info(
                        f"TP_LICENSE_TYPE_LOOKUP | Case: {case_number} | Party: {idx + 1} | "
                        f"Make: {car_make} | Model: {car_model} | "
                        f"License_Type: {license_type_from_make_model}"
                    )
                except Exception as e:
                    transaction_logger.warning(
                        f"TP_LICENSE_TYPE_LOOKUP_ERROR | Case: {case_number} | Party: {idx + 1} | "
                        f"Error: {str(e)[:100]}"
                    )
                    license_type_from_make_model = ""
            
            # Build converted_party using SAME structure as Excel extract_party_info
            # This ensures process_party_claim receives data in the same format
            converted_party = {
                # Core fields (same as Excel extract_party_info)
                "ID": party_id,
                "id": party_id,
                "name": name,
                "Name": name,
                "Liability": liability,
                "liability": liability,
                
                # Insurance info (same structure as Excel)
                "Insurance_Info": insurance_info,
                "insurance_info": insurance_info,
                
                # Vehicle info (same field names as Excel)
                "carMake": car_make,
                "carModel": car_model,
                "carMfgYear": car_year,
                "car_year": car_year,
                "carMake_Najm": party.get("carMake_Najm", ""),
                "carModel_Najm": party.get("carModel_Najm", ""),
                "Vehicle_Make": car_make,
                "Vehicle_Model": car_model,
                "Vehicle_Year": car_year,
                "chassisNo": chassis_no,
                "chassis_no": chassis_no,
                "Chassis_No": chassis_no,
                "Vehicle_Serial": chassis_no,
                "Vehicle_ID": vehicle_id,
                "VehicleOwnerId": vehicle_owner_id,
                "vehicleOwnerId": vehicle_owner_id,
                "vehicle_owner_id": vehicle_owner_id,
                
                # License info (same as Excel)
                "licenseType": license_type_from_request,
                "license_type": license_type_from_request,
                "License_Type_From_Najm": license_type_from_request,
                "License_Type_From_Request": license_type_from_request,
                "License_Type_From_Make_Model": license_type_from_make_model,  # Added BEFORE processing (Excel match)
                "License_Expiry_Date": party.get("License_Expiry_Date", ""),
                "License_Expiry_Last_Updated": party.get("License_Expiry_Last_Updated", ""),
                
                # Recovery and other fields (same as Excel)
                "recovery": recovery,
                "Recovery": recovery,
                "Policyholder_ID": policy_number,
                "Policy_Number": policy_number,
                "Policyholdername": policyholder_name,  # NEW: Policyholder name parameter
                "Policyholder_Name": policyholder_name,  # Alternative field name
                "Party": party.get("Party", f"Party {idx + 1}"),
                "insurance_type": insurance_type,
                
                # Damage and Act info (same as Excel)
                "Damages": damages if damages else {},
                "Acts": acts if acts else {},
                "Damage_Type": damage_type,
                "Act_Violation": act_description,
                
                # Additional fields for compatibility
                "Party_ID": party_id,
                "Party_Name": name
            }
            
            converted_parties.append(converted_party)
            transaction_logger.info(
                f"TP_PARTY_ADDED | Case: {case_number} | Party: {idx + 1} | "
                f"Party_ID: {party_id} | Party_Name: {name} | "
                f"Insurance_Name: {insurance_name} | ICEnglishName: {insurance_name_english} | "
                f"License_Type_From_Make_Model: {license_type_from_make_model} | "
                f"Car_Make: {car_make} | Car_Model: {car_model} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        party_conversion_time = time.time() - party_conversion_start
        transaction_logger.info(
            f"TP_PARTY_CONVERSION_COMPLETE | Case: {case_number} | "
            f"Parties_Converted: {len(converted_parties)} | "
            f"Party_Conversion_Time_Seconds: {party_conversion_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Build claim data
        claim_data_build_start = time.time()
        claim_data = {
            "Case_Info": {
                "Accident_info": accident_info,
                "parties": {
                    "Party_Info": converted_parties
                }
            }
        }
        claim_data_build_time = time.time() - claim_data_build_start
        transaction_logger.info(
            f"TP_CLAIM_DATA_BUILT | Case: {case_number} | "
            f"Claim_Data_Build_Time_Seconds: {claim_data_build_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # ========== GLOBAL VALIDATION: TAWUNIYA POLICYHOLDER vs VEHICLE OWNER (BEFORE PROCESSING) ==========
        # CRITICAL RULE: If ANY Tawuniya party has Policyholder_ID != VehicleOwnerId AND Liability >= 50,
        # REJECT ALL PARTIES immediately without processing (no Ollama, no other validations)
        global_validation_start = time.time()
        
        def is_tawuniya_insurance_global(insurance_name, ic_english_name):
            """Check if insurance is Tawuniya (same logic as validation)"""
            if not insurance_name and not ic_english_name:
                return False
            
            insurance_clean = str(insurance_name).strip().lower()
            ic_english_clean = str(ic_english_name).strip().lower() if ic_english_name else ""
            
            # Check ICEnglishName first (most reliable)
            if ic_english_clean:
                if "tawuniya" in ic_english_clean and "cooperative" in ic_english_clean and "insurance" in ic_english_clean:
                    return True
                if re.search(r'tawuniya\s*(?:c\b|co\b|coop|cooperative|insurance)', ic_english_clean):
                    return True
            
            # Check insurance name
            if insurance_clean:
                if "tawuniya" in insurance_clean and ("cooperative" in insurance_clean or "insurance" in insurance_clean):
                    return True
                if "التعاونية" in insurance_name or "التعاونيه" in insurance_name:
                    return True
            
            return False
        
        # Check all parties for Tawuniya Policyholder mismatch
        tawuniya_mismatch_found = False
        tawuniya_mismatch_party = None
        
        for check_idx, check_party in enumerate(converted_parties):
            check_insurance = str(check_party.get("Insurance_Name", "")).strip()
            check_insurance_info = check_party.get("Insurance_Info", {}) or check_party.get("insurance_info", {})
            check_ic_english = str(check_insurance_info.get("ICEnglishName", "")).strip()
            check_is_tawuniya = is_tawuniya_insurance_global(check_insurance, check_ic_english)
            
            if check_is_tawuniya:
                # Get Policyholder_ID and VehicleOwnerId
                check_policyholder_id = (
                    str(check_party.get("Policyholder_ID", "")).strip() or
                    str(check_party.get("PolicyholderID", "")).strip() or
                    str(check_party.get("policyholder_id", "")).strip() or
                    str(check_insurance_info.get("policyNumber", "")).strip() or
                    ""
                )
                
                check_vehicle_owner_id = (
                    str(check_party.get("VehicleOwnerId", "")).strip() or
                    str(check_party.get("vehicleOwnerId", "")).strip() or
                    str(check_party.get("vehicle_owner_id", "")).strip() or
                    ""
                )
                
                check_liability = check_party.get("Liability", 0)
                
                # Check if Policyholder_ID exists and doesn't match VehicleOwnerId AND Liability >= 50
                if (check_policyholder_id and 
                    check_policyholder_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"] and
                    check_vehicle_owner_id and 
                    check_vehicle_owner_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"]):
                    
                    # Normalize IDs for comparison
                    check_policyholder_id_normalized = str(check_policyholder_id).strip().replace(" ", "")
                    check_vehicle_owner_id_normalized = str(check_vehicle_owner_id).strip().replace(" ", "")
                    
                    # Check if they don't match AND Liability >= 50 (includes 50 and above)
                    if check_policyholder_id_normalized != check_vehicle_owner_id_normalized and check_liability >= 50:
                        tawuniya_mismatch_found = True
                        tawuniya_mismatch_party = {
                            "idx": check_idx,
                            "party_id": check_party.get("Party_ID", ""),
                            "policyholder_id": check_policyholder_id,
                            "vehicle_owner_id": check_vehicle_owner_id,
                            "liability": check_liability
                        }
                        break
        
        global_validation_time = time.time() - global_validation_start
        
        # If mismatch found, reject ALL parties immediately
        if tawuniya_mismatch_found:
            transaction_logger.warning(
                f"TP_GLOBAL_TAWUNIYA_POLICYHOLDER_MISMATCH | Case: {case_number} | "
                f"Party: {tawuniya_mismatch_party['idx'] + 1} | Party_ID: {tawuniya_mismatch_party['party_id']} | "
                f"Policyholder_ID: {tawuniya_mismatch_party['policyholder_id']} | "
                f"VehicleOwnerId: {tawuniya_mismatch_party['vehicle_owner_id']} | "
                f"Liability: {tawuniya_mismatch_party['liability']}% | "
                f"Action: REJECTING ALL PARTIES without processing | "
                f"Reason: Tawuniya party - Policyholder_ID does not match VehicleOwnerId and Liability >= 50"
            )
            
            # Create rejected results for ALL parties
            rejected_results = []
            for reject_idx, reject_party in enumerate(converted_parties):
                rejected_results.append({
                    "_index": reject_idx,
                    "Party": reject_party.get("Party", f"Party {reject_idx + 1}"),
                    "Party_ID": reject_party.get("Party_ID", reject_party.get("ID", "")),
                    "Party_Name": reject_party.get("Party_Name", reject_party.get("name", "")),
                    "Liability": reject_party.get("Liability", 0),
                    "Decision": "REJECTED",
                    "Classification": "Policy Holder not same vehicle Owner",
                    "Reasoning": f"Global Rejection: Tawuniya party (Party {tawuniya_mismatch_party['idx'] + 1}) has Policyholder_ID ({tawuniya_mismatch_party['policyholder_id']}) that does not match VehicleOwnerId ({tawuniya_mismatch_party['vehicle_owner_id']}) and Liability >= 50%",
                    "Applied_Conditions": ["Tawuniya Policyholder Mismatch"],
                    "isDAA": isDAA,
                    "Suspect_as_Fraud": suspect_as_fraud,
                    "DaaReasonEnglish": daa_reason_english,
                    "Policyholder_ID": reject_party.get("Policyholder_ID", ""),
                    "insurance_type": "TP"
                })
            
            # Sort by index and remove _index
            rejected_results_sorted = sorted(rejected_results, key=lambda x: x.get("_index", 0))
            final_results = []
            for result in rejected_results_sorted:
                if "_index" in result:
                    del result["_index"]
                final_results.append(result)
            
            # Build response with all rejected parties
            response_build_start = time.time()
            response_data = {
                "Case_Number": case_number,
                "Accident_Date": accident_date,
                "Upload_Date": upload_date,
                "Claim_requester_ID": claim_requester_id,
                "Status": "Success",
                "Parties": final_results,
                "Total_Parties": len(data["Parties"]),
                "Parties_Processed": len(final_results),
                "LD_Rep_64bit_Received": bool(ld_rep_base64),
                "Global_Rejection_Reason": "Tawuniya Policyholder_ID mismatch with VehicleOwnerId and Liability >= 50"
            }
            
            response_build_time = time.time() - response_build_start
            request_total_time = time.time() - request_start_time
            request_end_datetime = datetime.now()
            
            transaction_logger.info(
                f"TP_GLOBAL_REJECTION_APPLIED | Case: {case_number} | "
                f"All_Parties_Rejected: {len(final_results)} | "
                f"Triggering_Party: {tawuniya_mismatch_party['idx'] + 1} | "
                f"Total_Time_Seconds: {request_total_time:.4f} | "
                f"Global_Validation_Time: {global_validation_time:.4f}s"
            )
            
            transaction_logger.info(
                f"TP_REQUEST_COMPLETE | Case: {case_number} | "
                f"Total_Request_Time_Seconds: {request_total_time:.4f} | "
                f"Status: GLOBAL_REJECTION | "
                f"Parties_Count: {len(final_results)} | "
                f"Reason: Tawuniya Policyholder_ID mismatch"
            )
            
            return jsonify(response_data), 200
        
        transaction_logger.info(
            f"TP_GLOBAL_TAWUNIYA_CHECK_PASSED | Case: {case_number} | "
            f"Global_Validation_Time: {global_validation_time:.4f}s | "
            f"No_Tawuniya_Policyholder_Mismatch_Found | Proceeding_with_Normal_Processing"
        )
        
        # Process parties in parallel - ENHANCED: Process ALL parties simultaneously for maximum performance
        # No limit on workers - process all parties at the same time (like Excel batch processing)
        results = []
        max_workers = len(converted_parties)  # Process all parties in parallel
        
        parallel_processing_start = time.time()
        transaction_logger.info(
            f"TP_PARALLEL_PROCESSING_START | Case: {case_number} | "
            f"Parties_Count: {len(converted_parties)} | Max_Workers: {max_workers} | "
            f"Processing_Mode: FULL_PARALLEL (All parties simultaneously) | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        processing_start_time = datetime.now()
        
        def process_single_party(idx, party):
            """Process a single party using TP processor"""
            nonlocal claim_data, ocr_text, ld_rep_base64, isDAA, suspect_as_fraud, daa_reason_english
            nonlocal case_number, accident_date, converted_parties, claim_processor_file, current_model
            # ollama_url and ollama_model are module-level, accessible without nonlocal
            
            # Start timing for this party
            party_start_time = time.time()
            
            # Initialize all timing variables to avoid NameError if exception occurs
            config_reload_time = 0.0
            processor_call_time = 0.0
            validation_time = 0.0
            recovery_validation_time = 0.0
            additional_fields_time = 0.0
            
            try:
                insurance_type = "TP"
                
                transaction_logger.info(
                    f"TP_PARTY_START | Case: {case_number} | Party: {idx + 1} | "
                    f"Start_Time: {party_start_time} | Start_Datetime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}"
                )
                
                # Log party processing start with file and config details
                transaction_logger.info(
                    f"TP_PARTY_PROCESSING_START | Case: {case_number} | Party: {idx + 1} | "
                    f"TP_Module_File: {os.path.abspath(__file__)} | "
                    f"TP_Directory: {TP_DIR} | "
                    f"TP_Config_File: {tp_config_file} | "
                    f"TP_Processor_Type: {type(tp_processor).__name__} | "
                    f"TP_Processor_Module: {type(tp_processor).__module__} | "
                    f"TP_Processor_File: {os.path.abspath(CLAIM_PROCESSOR_FILE_PATH)} | "
                    f"Insurance_Type: {insurance_type} | "
                    f"Current_Working_Dir: {os.getcwd()}"
                )
                
                # Reload rules and log config details
                config_reload_start = time.time()
                tp_config_manager.reload_config()
                current_rules = tp_config_manager.get_rules()
                current_prompts = tp_config_manager.get_prompts()
                transaction_logger.info(
                    f"TP_CONFIG_LOADED | Case: {case_number} | Party: {idx + 1} | "
                    f"Config_File: {tp_config_manager.config_file} | "
                    f"Config_File_Exists: {os.path.exists(tp_config_manager.config_file)} | "
                    f"Rules_Count: {len(current_rules) if isinstance(current_rules, dict) else 'N/A'} | "
                    f"Prompts_Available: {list(current_prompts.keys()) if isinstance(current_prompts, dict) else 'N/A'} | "
                    f"Ollama_URL: {tp_processor.ollama_base_url} | "
                    f"Ollama_Model: {tp_processor.model_name} | "
                    f"Ollama_Translation_Model: {getattr(tp_processor, 'translation_model', 'N/A')}"
                )
                
                tp_processor.reload_rules()
                config_reload_time = time.time() - config_reload_start
                transaction_logger.info(
                    f"TP_TIMING_CONFIG_RELOAD | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {config_reload_time:.4f}"
                )
                
                # Process party claim
                processor_call_start = time.time()
                transaction_logger.info(
                    f"TP_CALLING_PROCESSOR | Case: {case_number} | Party: {idx + 1} | "
                    f"Processor_Method: process_party_claim | "
                    f"Processor_Class: {type(tp_processor).__name__} | "
                    f"Processor_Module: {type(tp_processor).__module__} | "
                    f"Processor_File: {os.path.abspath(type(tp_processor).__module__.replace('.', '/') + '.py') if hasattr(type(tp_processor), '__module__') else 'Unknown'}"
                )
                
                party_result = tp_processor.process_party_claim(
                    claim_data=claim_data,
                    party_info=party,
                    party_index=idx,
                    all_parties=converted_parties
                )
                
                processor_call_time = time.time() - processor_call_start
                transaction_logger.info(
                    f"TP_TIMING_PROCESSOR_CALL | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {processor_call_time:.4f} | "
                    f"Decision: {party_result.get('decision', 'N/A')}"
                )
                
                # Log Ollama response with full details
                transaction_logger.info(
                    f"TP_OLLAMA_RESPONSE_RECEIVED | Case: {case_number} | Party: {idx + 1} | "
                    f"Decision: {party_result.get('decision', 'N/A')} | "
                    f"Classification: {party_result.get('classification', 'N/A')} | "
                    f"Reasoning: {party_result.get('reasoning', '')[:500]} | "
                    f"Applied_Conditions: {party_result.get('applied_conditions', [])}"
                )
                
                # ========== APPLY VALIDATION LOGIC (SAME AS EXCEL) ==========
                # Excel applies validation rules AFTER getting decision from Ollama
                # This ensures Rule #3 and other rules are applied correctly
                
                validation_start = time.time()
                
                # Extract party info for validation
                current_liability = party.get("Liability", 0)
                current_insurance = str(party.get("Insurance_Name", "")).strip()
                insurance_info = party.get("Insurance_Info", {}) or party.get("insurance_info", {})
                current_ic_english = str(insurance_info.get("ICEnglishName", "")).strip()
                
                # Get decision from Ollama
                decision = party_result.get("decision", "ERROR")
                reasoning = party_result.get("reasoning", "")
                classification = party_result.get("classification", "UNKNOWN")
                
                transaction_logger.info(
                    f"TP_VALIDATION_START | Case: {case_number} | Party: {idx + 1} | "
                    f"Ollama_Decision: {decision} | Liability: {current_liability}% | "
                    f"Insurance_Name: {current_insurance} | ICEnglishName: {current_ic_english}"
                )
                
                # Helper function to check if insurance is Tawuniya (same as Excel)
                def is_tawuniya_insurance(insurance_name, ic_english_name):
                    """Check if insurance is Tawuniya (same logic as Excel)"""
                    if not insurance_name and not ic_english_name:
                        return False
                    
                    insurance_clean = str(insurance_name).strip().lower()
                    ic_english_clean = str(ic_english_name).strip().lower() if ic_english_name else ""
                    
                    # Check ICEnglishName first (most reliable)
                    if ic_english_clean:
                        if "tawuniya" in ic_english_clean and "cooperative" in ic_english_clean and "insurance" in ic_english_clean:
                            return True
                        if re.search(r'tawuniya\s*(?:c\b|co\b|coop|cooperative|insurance)', ic_english_clean):
                            return True
                    
                    # Check insurance name
                    if insurance_clean:
                        if "tawuniya" in insurance_clean and ("cooperative" in insurance_clean or "insurance" in insurance_clean):
                            return True
                        if "التعاونية" in insurance_name or "التعاونيه" in insurance_name:
                            return True
                    
                    return False
                
                # ========== VALIDATE TAWUNIYA POLICYHOLDER vs VEHICLE OWNER RULE ==========
                # NEW RULE: For Tawuniya parties, if Policyholder_ID exists and doesn't match VehicleOwnerId AND Liability > 0, then REJECT
                is_tawuniya = is_tawuniya_insurance(current_insurance, current_ic_english)
                if is_tawuniya:
                    # Get Policyholder_ID and VehicleOwnerId from party (converted_party structure)
                    # Check multiple field name variations
                    policyholder_id = (
                        str(party.get("Policyholder_ID", "")).strip() or
                        str(party.get("PolicyholderID", "")).strip() or
                        str(party.get("policyholder_id", "")).strip() or
                        str(insurance_info.get("policyNumber", "")).strip() or
                        ""
                    )
                    
                    vehicle_owner_id = (
                        str(party.get("VehicleOwnerId", "")).strip() or
                        str(party.get("vehicleOwnerId", "")).strip() or
                        str(party.get("vehicle_owner_id", "")).strip() or
                        ""
                    )
                    
                    # Check if Policyholder_ID exists and doesn't match VehicleOwnerId
                    if policyholder_id and policyholder_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"]:
                        if vehicle_owner_id and vehicle_owner_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"]:
                            # Normalize IDs for comparison (remove spaces, convert to string)
                            policyholder_id_normalized = str(policyholder_id).strip().replace(" ", "")
                            vehicle_owner_id_normalized = str(vehicle_owner_id).strip().replace(" ", "")
                            
                            # Check if they don't match AND Liability >= 50 (individual party check - global already handled above)
                            if policyholder_id_normalized != vehicle_owner_id_normalized and current_liability >= 50:
                                transaction_logger.warning(
                                    f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_MISMATCH | Case: {case_number} | Party: {idx + 1} | "
                                    f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                                    f"Policyholder_ID: {policyholder_id} | VehicleOwnerId: {vehicle_owner_id} | "
                                    f"Liability: {current_liability}% | "
                                    f"Reason: Tawuniya party - Policyholder_ID does not match VehicleOwnerId and Liability >= 50"
                                )
                                decision = "REJECTED"
                                reasoning = f"Tawuniya Validation: Policyholder_ID ({policyholder_id}) does not match VehicleOwnerId ({vehicle_owner_id}) and Liability ({current_liability}%) >= 50. {reasoning}" if reasoning else f"Tawuniya Validation: Policyholder_ID ({policyholder_id}) does not match VehicleOwnerId ({vehicle_owner_id}) and Liability ({current_liability}%) >= 50"
                                classification = "Policy Holder not same vehicle Owner"
                            else:
                                transaction_logger.info(
                                    f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                                    f"Policyholder_ID: {policyholder_id} | VehicleOwnerId: {vehicle_owner_id} | "
                                    f"Match: {policyholder_id_normalized == vehicle_owner_id_normalized} | "
                                    f"Liability: {current_liability}% | Rule_Not_Applied: {'IDs match' if policyholder_id_normalized == vehicle_owner_id_normalized else f'Liability is {current_liability}% (must be > 50%)'}"
                                )
                        else:
                            transaction_logger.info(
                                f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                                f"Policyholder_ID: {policyholder_id} | VehicleOwnerId: empty/missing | "
                                f"Rule_Not_Applied: VehicleOwnerId not available"
                            )
                    else:
                        transaction_logger.info(
                            f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                            f"Policyholder_ID: empty/missing | Rule_Not_Applied: Policyholder_ID not available"
                        )
                
                # ========== VALIDATE 0% LIABILITY PARTY ==========
                if current_liability == 0 and decision == "REJECTED":
                    rejection_reason_lower = reasoning.lower() if reasoning else ""
                    classification_lower = classification.lower() if classification else ""
                    
                    # Check if rejection is only due to another party's 100% liability (incorrect)
                    if ("100%" in rejection_reason_lower or "100%" in classification_lower or 
                        "basic rule" in classification_lower or "rule #1" in classification_lower):
                        # Check if there's another party with 100% liability
                        has_other_100_percent = False
                        for other_idx, other_party in enumerate(converted_parties):
                            if other_idx != idx:
                                other_liab = other_party.get("Liability", 0)
                                if other_liab == 100:
                                    has_other_100_percent = True
                                    break
                        
                        if has_other_100_percent:
                            transaction_logger.warning(
                                f"TP_VALIDATION_0_PERCENT_CORRECTION | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: REJECTED | Corrected_Decision: ACCEPTED | "
                                f"Reason: 0% liability party should not be rejected when another party has 100% liability"
                            )
                            decision = "ACCEPTED"
                            reasoning = f"{reasoning} | CORRECTED: 0% liability party should not be rejected when another party has 100% liability" if reasoning else "CORRECTED: 0% liability party should not be rejected when another party has 100% liability"
                            classification = "Correction Rule: Victim party (0% liability) must be accepted"
                
                # ========== VALIDATE 100% LIABILITY RULE (RULE #1) ==========
                if current_liability == 100 and decision != "REJECTED":
                    transaction_logger.warning(
                        f"TP_VALIDATION_RULE_1 | Case: {case_number} | Party: {idx + 1} | "
                        f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                        f"Reason: Rule #1 - 100% liability MUST result in REJECTED for ALL companies"
                    )
                    decision = "REJECTED"
                    reasoning = f"Rule #1: 100% liability requires REJECTED for all companies. {reasoning}" if reasoning else "Rule #1: 100% liability requires REJECTED for all companies"
                    classification = "Basic Rule #1: 100% liability = REJECTED (all companies)"
                
                # ========== VALIDATE NON-COOPERATIVE INSURANCE RULE (RULE #3) ==========
                # CRITICAL: Rule #3 has HIGH PRIORITY - applies even if AI decision is REJECTED
                rule3_applied = False
                if current_liability != 100:  # Rule #3 doesn't apply to 100% liability
                    is_tawuniya = is_tawuniya_insurance(current_insurance, current_ic_english)
                    
                    transaction_logger.info(
                        f"TP_VALIDATION_RULE3_CHECK | Case: {case_number} | Party: {idx + 1} | "
                        f"Is_Tawuniya: {is_tawuniya} | Liability: {current_liability}% | "
                        f"Insurance_Name: {current_insurance} | ICEnglishName: {current_ic_english}"
                    )
                    
                    # Rule #3: Non-Tawuniya parties with 0%/25%/50%/75% liability → ACCEPTED
                    if not is_tawuniya and current_liability in [0, 25, 50, 75]:
                        if decision != "ACCEPTED" and decision != "ACCEPTED_WITH_RECOVERY":
                            transaction_logger.warning(
                                f"TP_VALIDATION_RULE3_APPLIED | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: {decision} | Corrected_Decision: ACCEPTED | "
                                f"Reason: Rule #3 (HIGH PRIORITY) - Non-Tawuniya party with {current_liability}% liability MUST be ACCEPTED"
                            )
                            decision = "ACCEPTED"
                            reasoning = f"Rule #3 (HIGH PRIORITY): Non-Tawuniya insurance party with {current_liability}% liability requires ACCEPTED. Overridden previous decision. {reasoning}" if reasoning else f"Rule #3 (HIGH PRIORITY): Non-Tawuniya insurance party with {current_liability}% liability requires ACCEPTED"
                            classification = f"Rule #3: Other insurance companies (non-Tawuniya) - {current_liability}% liability = ACCEPTED"
                            rule3_applied = True
                        else:
                            transaction_logger.info(
                                f"TP_VALIDATION_RULE3_ALREADY_CORRECT | Case: {case_number} | Party: {idx + 1} | "
                                f"Decision: {decision} | Rule #3 applies and decision is already correct"
                            )
                            rule3_applied = True
                
                # ========== VALIDATE GLOBAL RULE: 100% LIABILITY FROM NON-TAWUNIYA COMPANY ==========
                # If ANY party has 100% liability from non-Tawuniya, ALL parties must be REJECTED
                has_100_percent_non_tawuniya = False
                non_tawuniya_100_party_info = None
                for other_idx, other_party in enumerate(converted_parties):
                    if other_idx == idx:
                        continue
                    
                    other_liability = other_party.get("Liability", 0)
                    if other_liability == 100:
                        other_insurance = str(other_party.get("Insurance_Name", "")).strip()
                        other_ins_info = other_party.get("Insurance_Info", {}) or other_party.get("insurance_info", {})
                        other_ic_english = str(other_ins_info.get("ICEnglishName", "")).strip()
                        is_other_tawuniya = is_tawuniya_insurance(other_insurance, other_ic_english)
                        
                        if not is_other_tawuniya:
                            has_100_percent_non_tawuniya = True
                            non_tawuniya_100_party_info = {
                                "idx": other_idx,
                                "insurance": other_insurance,
                                "liability": other_liability
                            }
                            break
                
                if has_100_percent_non_tawuniya:
                    if decision != "REJECTED":
                        transaction_logger.warning(
                            f"TP_VALIDATION_GLOBAL_TAWUNIYA_RULE | Case: {case_number} | Party: {idx + 1} | "
                            f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                            f"Reason: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}) - ALL parties must be REJECTED"
                        )
                        if rule3_applied:
                            transaction_logger.warning(
                                f"TP_VALIDATION_RULE3_OVERRIDDEN | Case: {case_number} | Party: {idx + 1} | "
                                f"Rule #3 was applied but is OVERRIDDEN by Tawuniya Global Rule"
                            )
                        decision = "REJECTED"
                        reasoning = f"Tawuniya Global Rule OVERRIDES Rule #3: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}). All parties must be REJECTED. {reasoning}" if reasoning else f"Tawuniya Global Rule: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}). All parties must be REJECTED."
                        classification = "Tawuniya Rule: Reject all parties when there is a responsible party (100%) from a non-Tawuniya company"
                
                # ========== VALIDATE COOPERATIVE INSURANCE DECISION ==========
                # Only for Tawuniya parties with liability < 100%
                if is_tawuniya_insurance(current_insurance, current_ic_english) and current_liability < 100 and decision != "REJECTED" and not rule3_applied:
                    # Check if any party with liability > 0% is NOT Tawuniya
                    has_non_tawuniya_with_liability = False
                    for other_idx, other_party in enumerate(converted_parties):
                        if other_idx == idx:
                            continue
                        other_liability = other_party.get("Liability", 0)
                        if other_liability > 0:
                            other_insurance = str(other_party.get("Insurance_Name", "")).strip()
                            other_ins_info = other_party.get("Insurance_Info", {}) or other_party.get("insurance_info", {})
                            other_ic_english = str(other_ins_info.get("ICEnglishName", "")).strip()
                            is_other_tawuniya = is_tawuniya_insurance(other_insurance, other_ic_english)
                            if not is_other_tawuniya:
                                has_non_tawuniya_with_liability = True
                                break
                    
                    if has_non_tawuniya_with_liability:
                        if decision != "REJECTED":
                            transaction_logger.warning(
                                f"TP_VALIDATION_COOPERATIVE_RULE | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                                f"Reason: Tawuniya party with {current_liability}% liability - another party with liability > 0% is NOT Tawuniya"
                            )
                            decision = "REJECTED"
                            reasoning = f"Cooperative Rule: Tawuniya party with {current_liability}% liability - another party with liability > 0% is NOT Tawuniya. {reasoning}" if reasoning else f"Cooperative Rule: Tawuniya party with {current_liability}% liability - another party with liability > 0% is NOT Tawuniya"
                            classification = "Cooperative Rule: Reject Tawuniya party when another party with liability > 0% is not Tawuniya"
                
                # ========== VALIDATE ACCEPTED_WITH_RECOVERY DECISION (SAME AS EXCEL) ==========
                recovery_validation_start = time.time()
                if decision == "ACCEPTED_WITH_RECOVERY":
                    is_tawuniya_party = is_tawuniya_insurance(current_insurance, current_ic_english)
                    
                    if not is_tawuniya_party:
                        transaction_logger.warning(
                            f"TP_VALIDATION_RECOVERY_NON_TAWUNIYA | Case: {case_number} | Party: {idx + 1} | "
                            f"Original_Decision: ACCEPTED_WITH_RECOVERY | Corrected_Decision: ACCEPTED | "
                            f"Reason: ACCEPTED_WITH_RECOVERY only applies to Tawuniya insured parties"
                        )
                        decision = "ACCEPTED"
                        reasoning = f"{reasoning} | VALIDATION: ACCEPTED_WITH_RECOVERY only for Tawuniya parties" if reasoning else "VALIDATION: ACCEPTED_WITH_RECOVERY only for Tawuniya parties"
                    else:
                        # Party is Tawuniya - validate using SAME logic as Excel unified_processor
                        validation_result = _validate_recovery_decision_api(
                            idx, party, converted_parties, accident_date, transaction_logger, case_number
                        )
                        if not validation_result["is_valid"]:
                            transaction_logger.warning(
                                f"TP_VALIDATION_RECOVERY_FAILED | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: ACCEPTED_WITH_RECOVERY | Corrected_Decision: {validation_result['corrected_decision']} | "
                                f"Reason: {validation_result['reason']}"
                            )
                            decision = validation_result["corrected_decision"]
                            reasoning = f"{reasoning} | VALIDATION: {validation_result['reason']}" if reasoning else f"VALIDATION: {validation_result['reason']}"
                        else:
                            transaction_logger.info(
                                f"TP_VALIDATION_RECOVERY_PASSED | Case: {case_number} | Party: {idx + 1} | "
                                f"Recovery_Reasons: {validation_result.get('recovery_reasons', [])}"
                            )
                
                # ========== UPGRADE ACCEPTED TO ACCEPTED_WITH_RECOVERY (SAME AS EXCEL) ==========
                # Excel lines 5507-5802: Check if ACCEPTED decision should be upgraded to ACCEPTED_WITH_RECOVERY
                elif decision == "ACCEPTED":
                    # Only check upgrade if current party has liability < 100% (victim party)
                    if current_liability < 100:
                        is_tawuniya_party = is_tawuniya_insurance(current_insurance, current_ic_english)
                        
                        # Check if recovery conditions are met (SAME AS EXCEL lines 5608-5776)
                        should_validate_recovery = False
                        
                        # Check current party's Recovery field
                        current_recovery_field = str(party.get("Recovery", "")).strip()
                        current_recovery_field_upper = current_recovery_field.upper()
                        current_has_recovery_field = current_recovery_field_upper in ["TRUE", "1", "YES", "Y", "TRUE", "True"] or current_recovery_field in ["True", "true", "TRUE"]
                        
                        # Check current party's model_recovery (calculate on-the-fly)
                        current_license_type_make_model = str(party.get("License_Type_From_Make_Model", "")).strip()
                        current_license_type_request = str(party.get("License_Type_From_Request", "")).strip()
                        
                        # Normalize values
                        if current_license_type_make_model.lower() in ["none", "nan", "null"]:
                            current_license_type_make_model = ""
                        if current_license_type_request.lower() in ["none", "nan", "null"]:
                            current_license_type_request = ""
                        
                        # Check model_recovery condition (SAME AS EXCEL)
                        current_make_model_valid = (current_license_type_make_model and 
                                                   current_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                   current_license_type_make_model.upper() != "ANY LICENSE")
                        current_request_is_none_or_empty = (not current_license_type_request or 
                                                           current_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
                        current_request_mismatch = (current_license_type_request and 
                                                   current_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                   current_license_type_make_model.upper() != current_license_type_request.upper())
                        current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
                        
                        # Check other parties for recovery conditions
                        other_tawuniya_with_recovery = False
                        other_tawuniya_with_model_recovery = False
                        
                        for other_idx, other_party in enumerate(converted_parties):
                            if other_idx == idx:
                                continue
                            
                            other_liability = other_party.get("Liability", 0)
                            other_insurance = str(other_party.get("Insurance_Name", "")).strip()
                            insurance_info_other = other_party.get("Insurance_Info", {}) or other_party.get("insurance_info", {})
                            other_ic_english = str(insurance_info_other.get("ICEnglishName", "")).strip()
                            is_other_tawuniya = is_tawuniya_insurance(other_insurance, other_ic_english)
                            
                            if is_other_tawuniya and other_liability > 0:
                                # Check Recovery field
                                other_recovery = str(other_party.get("Recovery", "")).strip()
                                other_recovery_upper = other_recovery.upper()
                                if other_recovery_upper in ["TRUE", "1", "YES", "Y", "TRUE", "True"] or other_recovery in ["True", "true", "TRUE"]:
                                    other_tawuniya_with_recovery = True
                                
                                # Check model_recovery (SAME AS EXCEL)
                                other_license_type_make_model = str(other_party.get("License_Type_From_Make_Model", "")).strip()
                                other_license_type_request = str(other_party.get("License_Type_From_Request", "")).strip()
                                
                                if other_license_type_make_model.lower() in ["none", "nan", "null"]:
                                    other_license_type_make_model = ""
                                if other_license_type_request.lower() in ["none", "nan", "null"]:
                                    other_license_type_request = ""
                                
                                other_make_model_valid = (other_license_type_make_model and 
                                                         other_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                         other_license_type_make_model.upper() != "ANY LICENSE")
                                other_request_is_none_or_empty = (not other_license_type_request or 
                                                                 other_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
                                other_request_mismatch = (other_license_type_request and 
                                                         other_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                         other_license_type_make_model.upper() != other_license_type_request.upper())
                                other_has_model_recovery = other_make_model_valid and (other_request_is_none_or_empty or other_request_mismatch)
                                
                                if other_has_model_recovery:
                                    other_tawuniya_with_model_recovery = True
                            
                            if other_tawuniya_with_recovery and other_tawuniya_with_model_recovery:
                                break
                        
                        # Determine if recovery validation should proceed (SAME AS EXCEL lines 5740-5776)
                        if is_tawuniya_party:
                            # Current party is Tawuniya - check if liability > 0 AND (Recovery=True OR model_recovery=True)
                            if current_liability > 0 and (current_has_recovery_field or current_has_model_recovery):
                                should_validate_recovery = True
                            # Also check if another Tawuniya party has Recovery=True/TRUE/true with liability > 0
                            elif current_liability > 0 and other_tawuniya_with_recovery:
                                should_validate_recovery = True
                            # Also check if another Tawuniya party has model_recovery=True/TRUE/true with liability > 0
                            elif current_liability > 0 and other_tawuniya_with_model_recovery:
                                should_validate_recovery = True
                        else:
                            # Non-Tawuniya party - Check exception: if another Tawuniya party has Recovery=True/TRUE/true with liability > 0
                            if current_liability < 100 and other_tawuniya_with_recovery:
                                should_validate_recovery = True
                            elif current_liability < 100 and other_tawuniya_with_model_recovery:
                                should_validate_recovery = True
                        
                        # If recovery conditions are met, validate and upgrade
                        if should_validate_recovery:
                            transaction_logger.info(
                                f"TP_RECOVERY_UPGRADE_CHECK | Case: {case_number} | Party: {idx + 1} | "
                                f"Decision: ACCEPTED | Checking recovery conditions for upgrade to ACCEPTED_WITH_RECOVERY | "
                                f"Current_Has_Recovery: {current_has_recovery_field} | Current_Has_Model_Recovery: {current_has_model_recovery} | "
                                f"Other_Tawuniya_With_Recovery: {other_tawuniya_with_recovery} | Other_Tawuniya_With_Model_Recovery: {other_tawuniya_with_model_recovery}"
                            )
                            
                            # Validate recovery conditions (SAME AS EXCEL)
                            validation_result = _validate_recovery_decision_api(
                                idx, party, converted_parties, accident_date, transaction_logger, case_number
                            )
                            
                            if validation_result["is_valid"]:
                                # Upgrade ACCEPTED to ACCEPTED_WITH_RECOVERY
                                transaction_logger.info(
                                    f"TP_RECOVERY_UPGRADE_APPLIED | Case: {case_number} | Party: {idx + 1} | "
                                    f"Original_Decision: ACCEPTED | Upgraded_Decision: ACCEPTED_WITH_RECOVERY | "
                                    f"Recovery_Reasons: {validation_result.get('recovery_reasons', [])}"
                                )
                                decision = "ACCEPTED_WITH_RECOVERY"
                                reasoning = f"{reasoning} | RECOVERY UPGRADE: {validation_result['reason']}" if reasoning else f"RECOVERY UPGRADE: {validation_result['reason']}"
                                classification = "Recovery conditions met - upgraded from ACCEPTED to ACCEPTED_WITH_RECOVERY"
                            else:
                                transaction_logger.info(
                                    f"TP_RECOVERY_UPGRADE_SKIPPED | Case: {case_number} | Party: {idx + 1} | "
                                    f"Decision: ACCEPTED | Recovery conditions not met, keeping ACCEPTED | "
                                    f"Reason: {validation_result.get('reason', 'N/A')}"
                                )
                
                recovery_validation_time = time.time() - recovery_validation_start
                transaction_logger.info(
                    f"TP_TIMING_RECOVERY_VALIDATION | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {recovery_validation_time:.4f}"
                )
                
                # Log final validation result
                validation_time = time.time() - validation_start
                transaction_logger.info(
                    f"TP_VALIDATION_COMPLETE | Case: {case_number} | Party: {idx + 1} | "
                    f"Final_Decision: {decision} | Original_Decision: {party_result.get('decision', 'N/A')} | "
                    f"Rule3_Applied: {rule3_applied} | Liability: {current_liability}% | "
                    f"Insurance: {current_insurance} | Is_Tawuniya: {is_tawuniya_insurance(current_insurance, current_ic_english)} | "
                    f"Validation_Time_Seconds: {validation_time:.4f}"
                )
                
                # Update party_result with validated decision
                party_result["decision"] = decision
                party_result["reasoning"] = reasoning
                party_result["classification"] = classification
                
                transaction_logger.info(
                    f"TP_PROCESSOR_RESPONSE | Case: {case_number} | Party: {idx + 1} | "
                    f"Decision: {party_result.get('decision', 'N/A')} | "
                    f"Classification: {party_result.get('classification', 'N/A')} | "
                    f"Reasoning_Length: {len(str(party_result.get('reasoning', '')))}"
                )
                
                # Calculate additional fields
                additional_fields_start = time.time()
                additional_fields = calculate_additional_fields(party, isDAA, insurance_type)
                additional_fields_time = time.time() - additional_fields_start
                transaction_logger.info(
                    f"TP_TIMING_ADDITIONAL_FIELDS | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {additional_fields_time:.4f}"
                )
                
                # Build response
                base_response = {
                    "_index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Party_Name": party.get("name", ""),
                    "Liability": party.get("Liability", 0),
                    "Policyholder_ID": party.get("Policyholder_ID", ""),
                    "Policyholdername": party.get("Policyholdername", party.get("Policyholder_Name", "")),  # NEW: Policyholder name
                    "Decision": party_result.get("decision", "ERROR"),
                    "Classification": party_result.get("classification", "UNKNOWN"),
                    "Reasoning": party_result.get("reasoning", ""),
                    "Applied_Conditions": party_result.get("applied_conditions", []),
                    "isDAA": isDAA,
                    "Suspect_as_Fraud": suspect_as_fraud,
                    "DaaReasonEnglish": daa_reason_english,
                    "Policyholder_ID": party.get("Policyholder_ID", ""),
                    "Suspected_Fraud": additional_fields.get("Suspected_Fraud"),
                    "model_recovery": additional_fields.get("model_recovery"),
                    "License_Type_From_Make_Model": additional_fields.get("License_Type_From_Make_Model"),
                    "insurance_type": insurance_type
                }
                
                # Filter response fields based on config
                tp_config_manager.reload_config()
                response_fields_config = tp_config_manager.get_config().get("response_fields", {}).get("enabled_fields", {})
                
                transaction_logger.info(
                    f"TP_RESPONSE_FIELDS_CONFIG | Case: {case_number} | Party: {idx + 1} | "
                    f"Config_File: {tp_config_manager.config_file} | "
                    f"Config_File_Path: {os.path.abspath(tp_config_manager.config_file)} | "
                    f"Enabled_Fields: {list(response_fields_config.keys()) if isinstance(response_fields_config, dict) else 'N/A'} | "
                    f"Total_Fields: {len(response_fields_config) if isinstance(response_fields_config, dict) else 0}"
                )
                
                filtered_response = {}
                for field_name, field_value in base_response.items():
                    if field_name == "_index":
                        filtered_response["_index"] = field_value
                        continue
                    if response_fields_config.get(field_name, True):
                        filtered_response[field_name] = field_value
                
                # Log party completion timing
                # Calculate total time (party_start_time is defined before try block, so it's always accessible)
                party_total_time = time.time() - party_start_time
                
                # Calculate Ollama time percentage
                ollama_percentage = (processor_call_time / party_total_time * 100) if party_total_time > 0 else 0.0
                
                transaction_logger.info(
                    f"TP_PARTY_COMPLETE | Case: {case_number} | Party: {idx + 1} | "
                    f"Total_Time_Seconds: {party_total_time:.4f} | "
                    f"End_Time: {time.time()} | End_Datetime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')} | "
                    f"Time_Breakdown: Config_Reload={config_reload_time:.4f}, Processor_Call={processor_call_time:.4f} ({ollama_percentage:.1f}%), "
                    f"Validation={validation_time:.4f}, Recovery_Validation={recovery_validation_time:.4f}, "
                    f"Additional_Fields={additional_fields_time:.4f} | "
                    f"Model: {current_model}"
                )
                
                # Add processing time to response for performance tracking
                filtered_response['_processing_time'] = party_total_time
                
                return filtered_response
                
            except Exception as e:
                error_msg = str(e)
                
                # Log error with timing if party_start_time is available
                try:
                    if 'party_start_time' in locals():
                        error_time = time.time() - party_start_time
                        transaction_logger.error(
                            f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
                            f"Error_Type: {type(e).__name__} | Error_Time_Seconds: {error_time:.4f}"
                        )
                    else:
                        transaction_logger.error(
                            f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
                            f"Error_Type: {type(e).__name__} | Note: party_start_time not available"
                        )
                except:
                    transaction_logger.error(
                        f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
                        f"Error_Type: {type(e).__name__}"
                    )
                
                # Check if it's an Ollama connection error
                if "404" in error_msg or "Not Found" in error_msg or "Failed to connect" in error_msg:
                    reasoning = f"Ollama service error: {error_msg}. Please ensure Ollama is running on {ollama_url} and the model '{ollama_model}' is available."
                else:
                    reasoning = f"Error processing party: {error_msg}"
                
                return {
                    "_index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Reasoning": reasoning,
                    "Applied_Conditions": []
                }
        
        def calculate_additional_fields(party_data, is_daa_value, insurance_type):
            """
            Calculate additional fields using TP unified processor
            EXACTLY matches Excel unified_processor logic for 100% accuracy
            Uses the SAME conditions and logic as unified_processor._validate_recovery_decision
            """
            additional = {}
            
            # License_Type_From_Make_Model already added to party_data before processing (Excel match)
            # Just retrieve it - no need to lookup again
            license_type_from_make_model = str(party_data.get("License_Type_From_Make_Model", "")).strip()
            additional["License_Type_From_Make_Model"] = license_type_from_make_model
            
            # Suspected_Fraud calculation (EXACT Excel unified_processor logic from lines 7517-7546)
            # Excel uses: isDAA_series.isin(['TRUE', '1', 'YES', 'Y', 'T'])
            suspected_fraud = None
            if is_daa_value is not None:
                # Convert to string and normalize (EXACT Excel logic)
                is_daa_str = str(is_daa_value).strip().upper()
                # Excel checks: isDAA_series.isin(['TRUE', '1', 'YES', 'Y', 'T'])
                if is_daa_str in ['TRUE', '1', 'YES', 'Y', 'T']:
                    suspected_fraud = "Suspected Fraud"
            # Excel sets to None if isDAA is NaN/None
            additional["Suspected_Fraud"] = suspected_fraud
            
            # model_recovery calculation (EXACT Excel unified_processor logic from lines 640-660)
            # This matches unified_processor._validate_recovery_decision model_recovery calculation
            license_type_from_request = str(party_data.get("licenseType", "") or party_data.get("License_Type_From_Najm", "")).strip()
            
            # Normalize values (EXACT Excel logic - lines 645-648)
            if license_type_from_make_model.lower() in ["none", "nan", "null"]:
                license_type_from_make_model = ""
            if license_type_from_request.lower() in ["none", "nan", "null"]:
                license_type_from_request = ""
            
            # Check model_recovery condition (EXACT Excel logic - lines 650-659)
            # Excel checks:
            # 1. current_make_model_valid = (current_license_type_make_model and 
            #    current_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
            #    current_license_type_make_model.upper() != "ANY LICENSE")
            make_model_valid = (license_type_from_make_model and 
                               license_type_from_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               license_type_from_make_model.upper() != "ANY LICENSE")
            
            # 2. current_request_is_none_or_empty = (not current_license_type_request or 
            #    current_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
            request_is_none_or_empty = (not license_type_from_request or 
                                       license_type_from_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
            
            # 3. current_request_mismatch = (current_license_type_request and 
            #    current_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
            #    current_license_type_make_model.upper() != current_license_type_request.upper())
            request_mismatch = (license_type_from_request and 
                               license_type_from_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               license_type_from_make_model.upper() != license_type_from_request.upper())
            
            # 4. current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
            # Excel line 659: current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
            has_model_recovery = make_model_valid and (request_is_none_or_empty or request_mismatch)
            model_recovery = has_model_recovery
            
            if model_recovery:
                transaction_logger.info(
                    f"TP_MODEL_RECOVERY_DETECTED | Case: {case_number} | "
                    f"License_Type_From_Make_Model: {license_type_from_make_model} | "
                    f"License_Type_From_Request: {license_type_from_request} | "
                    f"Make_Model_Valid: {make_model_valid} | "
                    f"Request_Is_None_Or_Empty: {request_is_none_or_empty} | "
                    f"Request_Mismatch: {request_mismatch}"
                )
            
            additional["model_recovery"] = model_recovery
            
            return additional
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_party = {
                executor.submit(process_single_party, idx, party): (idx, party)
                for idx, party in enumerate(converted_parties)
            }
            
            completed_results = {}
            for future in as_completed(future_to_party):
                try:
                    result = future.result()
                    result_index = result.get("_index", 0)
                    # Remove _index from result before storing
                    if "_index" in result:
                        del result["_index"]
                    completed_results[result_index] = result
                except Exception as e:
                    idx, party = future_to_party[future]
                    transaction_logger.error(
                        f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {str(e)[:200]}"
                    )
                    # Add error result to maintain order
                    completed_results[idx] = {
                        "Party": party.get("Party", f"Party {idx + 1}"),
                        "Party_ID": party.get("ID", ""),
                        "Decision": "ERROR",
                        "Classification": "ERROR",
                        "Reasoning": f"Error processing party: {str(e)[:200]}",
                        "Applied_Conditions": []
                    }
        
        # Sort results by index (use keys since _index was removed from values)
        filtered_results = [completed_results[i] for i in sorted(completed_results.keys())]
        
        parallel_processing_time = time.time() - parallel_processing_start
        total_processing_time = (datetime.now() - processing_start_time).total_seconds()
        transaction_logger.info(
            f"TP_PARALLEL_PROCESSING_COMPLETE | Case: {case_number} | "
            f"Parties_Processed: {len(filtered_results)} | "
            f"Parallel_Processing_Time_Seconds: {parallel_processing_time:.4f} | "
            f"Total_Processing_Time_Seconds: {total_processing_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s | "
            f"TP_Module_File: {os.path.abspath(__file__)} | "
            f"TP_Directory: {TP_DIR}"
        )
        
        # Build response
        response_build_start = time.time()
        response_data = {
            "Case_Number": case_number,
            "Accident_Date": accident_date,
            "Upload_Date": upload_date,
            "Claim_requester_ID": claim_requester_id,
            "Status": "Success",
            "Parties": filtered_results,
            "Total_Parties": len(data["Parties"]),
            "Parties_Processed": len(filtered_results),
            "LD_Rep_64bit_Received": bool(ld_rep_base64)
        }
        response_build_time = time.time() - response_build_start
        transaction_logger.info(
            f"TP_RESPONSE_BUILT | Case: {case_number} | "
            f"Response_Build_Time_Seconds: {response_build_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Calculate total request time
        request_total_time = time.time() - request_start_time
        request_end_datetime = datetime.now()
        
        # Calculate timing breakdown (use 0.0 if variable not defined)
        data_extraction_time_final = data_extraction_time if 'data_extraction_time' in locals() else 0.0
        ocr_processing_time_final = ocr_processing_time if 'ocr_processing_time' in locals() else 0.0
        
        transaction_logger.info(
            f"TP_CLAIM_PROCESSING_COMPLETE | Case: {case_number} | "
            f"TP_Module_File: {os.path.abspath(__file__)} | "
            f"TP_Config_File: {tp_config_file} | "
            f"TP_Config_File_Path: {os.path.abspath(tp_config_file)} | "
            f"TP_Directory: {TP_DIR} | "
            f"TP_Processor_Type: {type(tp_processor).__name__} | "
            f"TP_Processor_Module: {type(tp_processor).__module__} | "
            f"Total_Parties: {len(data.get('Parties', []))} | "
            f"Parties_Processed: {len(filtered_results)} | "
            f"Processing_Location: TP_PATH | "
            f"Current_Working_Dir: {os.getcwd()}"
        )
        
        # Calculate performance metrics
        avg_party_time = parallel_processing_time / len(filtered_results) if filtered_results else 0.0
        
        # Performance comparison (expected improvement with qwen2.5:1.5b)
        expected_old_time = 110.0 * len(filtered_results)  # Old model: ~110s per party
        speed_improvement = (expected_old_time / request_total_time) if request_total_time > 0 else 0.0
        
        transaction_logger.info(
            f"TP_REQUEST_COMPLETE | Case: {case_number} | "
            f"Total_Request_Time_Seconds: {request_total_time:.4f} | "
            f"Start_Time: {request_start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')} | "
            f"End_Time: {request_end_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')} | "
            f"Time_Breakdown: Data_Cleaning={data_cleaning_time:.4f}s, Data_Extraction={data_extraction_time_final:.4f}s, "
            f"DAA_Extraction={daa_extraction_time:.4f}s, OCR_Processing={ocr_processing_time_final:.4f}s, "
            f"Accident_Info={accident_info_time:.4f}s, Party_Conversion={party_conversion_time:.4f}s, "
            f"Claim_Data_Build={claim_data_build_time:.4f}s, Parallel_Processing={parallel_processing_time:.4f}s, "
            f"Response_Build={response_build_time:.4f}s | "
            f"Parallel_Processing_Time: {total_processing_time:.4f}s | "
            f"Parties_Count: {len(filtered_results)} | "
            f"Average_Party_Time: {avg_party_time:.4f}s | "
            f"Model: {current_model} | "
            f"Performance_Improvement: {speed_improvement:.2f}x faster vs qwen2.5:3b (expected)"
        )
        
        # Performance summary log
        ollama_time_percentage = (parallel_processing_time / request_total_time * 100) if request_total_time > 0 else 0.0
        transaction_logger.info(
            f"TP_PERFORMANCE_SUMMARY | Case: {case_number} | "
            f"Model: {current_model} | "
            f"Total_Time: {request_total_time:.2f}s | "
            f"Parties: {len(filtered_results)} | "
            f"Avg_Party_Time: {avg_party_time:.2f}s | "
            f"Ollama_Time_Percentage: {ollama_time_percentage:.1f}% | "
            f"Speed_vs_Old_Model: {speed_improvement:.2f}x | "
            f"Expected_Old_Time: {expected_old_time:.2f}s | "
            f"Time_Saved: {expected_old_time - request_total_time:.2f}s"
        )
        
        return jsonify(response_data), 200
        
    except Exception as e:
        error_msg = str(e)
        transaction_logger.error(
            f"TP_CLAIM_PROCESSING_ERROR | Error: {error_msg} | "
            f"Traceback: {traceback.format_exc()[:2000]}"
        )
        return jsonify({"error": error_msg}), 500

"""
TP Claim Processing API Module
All TP claim processing logic is contained in this module.
Called from unified_api_server.py main router.
"""

import os
import sys
import json
import base64
import re
import time
import inspect
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
from flask import jsonify
from typing import Dict, List, Any

# Get TP directory FIRST - before any imports
TP_DIR = os.path.dirname(os.path.abspath(__file__))

# CRITICAL: Ensure TP directory is first in sys.path to prevent importing wrong modules
# Clear any cached modules that might interfere - be very aggressive
modules_to_clear = []
for k in list(sys.modules.keys()):
    # Clear any module with these names that's NOT from TP directory
    if any(x in k for x in ['claim_processor', 'config_manager', 'unified_processor', 'excel_ocr_license_processor']):
        # Only clear if it's NOT from TP directory
        if 'MotorclaimdecisionlinuxTP' not in k:
            modules_to_clear.append(k)
    # Also clear any CO modules
    if 'MotorclaimdecisionlinuxCO' in k:
        modules_to_clear.append(k)

for mod in modules_to_clear:
    try:
        del sys.modules[mod]
    except:
        pass

# Ensure TP directory is first in path for imports
if TP_DIR not in sys.path:
    sys.path.insert(0, TP_DIR)
elif sys.path[0] != TP_DIR:
    sys.path.remove(TP_DIR)
    sys.path.insert(0, TP_DIR)

# Import TP-specific modules - these MUST come from TP_DIR
# Use importlib to ensure we load from the correct path
import importlib.util
import importlib

# Explicitly load claim_processor from TP directory
claim_processor_path = os.path.abspath(os.path.join(TP_DIR, 'claim_processor.py'))
CLAIM_PROCESSOR_FILE_PATH = claim_processor_path  # Store for verification
if os.path.exists(claim_processor_path):
    # Use unique module name with timestamp to avoid cache conflicts
    import time
    unique_module_name = f'tp_claim_processor_{int(time.time() * 1000000)}'
    spec = importlib.util.spec_from_file_location(unique_module_name, claim_processor_path)
    claim_processor_module = importlib.util.module_from_spec(spec)
    # Store the file path in the module for later verification
    claim_processor_module.__file__ = claim_processor_path
    spec.loader.exec_module(claim_processor_module)
    ClaimProcessor = claim_processor_module.ClaimProcessor
    # Store the file path in the class for verification
    ClaimProcessor.__module_file__ = claim_processor_path
    ClaimProcessor.__file__ = claim_processor_path
else:
    # Fallback to regular import
    from claim_processor import ClaimProcessor
    try:
        CLAIM_PROCESSOR_FILE_PATH = inspect.getfile(ClaimProcessor)
    except:
        CLAIM_PROCESSOR_FILE_PATH = os.path.join(TP_DIR, 'claim_processor.py')

# Load other modules
excel_ocr_path = os.path.join(TP_DIR, 'excel_ocr_license_processor.py')
if os.path.exists(excel_ocr_path):
    spec = importlib.util.spec_from_file_location('tp_excel_ocr_license_processor', excel_ocr_path)
    excel_ocr_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(excel_ocr_module)
    ExcelOCRLicenseProcessor = excel_ocr_module.ExcelOCRLicenseProcessor
else:
    from excel_ocr_license_processor import ExcelOCRLicenseProcessor

unified_processor_path = os.path.join(TP_DIR, 'unified_processor.py')
if os.path.exists(unified_processor_path):
    spec = importlib.util.spec_from_file_location('tp_unified_processor', unified_processor_path)
    unified_processor_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(unified_processor_module)
    UnifiedClaimProcessor = unified_processor_module.UnifiedClaimProcessor
else:
    from unified_processor import UnifiedClaimProcessor

config_manager_path = os.path.join(TP_DIR, 'config_manager.py')
if os.path.exists(config_manager_path):
    spec = importlib.util.spec_from_file_location('tp_config_manager', config_manager_path)
    config_manager_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_manager_module)
    ConfigManager = config_manager_module.ConfigManager
else:
    from config_manager import ConfigManager

# Setup transaction logger for TP
# BASE_DIR should be the parent of TP_DIR (Motorclaimdecision_main)
BASE_DIR = os.path.dirname(TP_DIR)
LOG_DIR = os.path.join(BASE_DIR, "logs")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except PermissionError:
    # Fallback to TP directory if main logs directory not accessible
    LOG_DIR = os.path.join(TP_DIR, "logs")
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception as e:
    # If all else fails, use TP directory
    LOG_DIR = TP_DIR

# Daily transaction log file for TP
def get_transaction_logger():
    """Get or create transaction logger for TP"""
    logger_name = "tp_transaction_logger"
    if logger_name in logging.Logger.manager.loggerDict:
        return logging.getLogger(logger_name)
    
    transaction_logger = logging.getLogger(logger_name)
    transaction_logger.setLevel(logging.INFO)
    transaction_logger.propagate = False
    
    # Daily rotating log file
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_file = os.path.join(LOG_DIR, f"api_transactions_tp_{current_date}.log")
    
    handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        utc=False
    )
    handler.suffix = '%Y-%m-%d'
    
    formatter = logging.Formatter(
        '%(asctime)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    transaction_logger.addHandler(handler)
    
    return transaction_logger

transaction_logger = get_transaction_logger()

# Initialize TP processors
tp_config_file = os.path.join(TP_DIR, "claim_config.json")
tp_config_manager = ConfigManager(config_file=tp_config_file)

# Get Ollama configuration from config file or use defaults
tp_config_manager.reload_config()
tp_config = tp_config_manager.get_config()
ollama_config = tp_config.get("ollama", {})
ollama_url = ollama_config.get("base_url", os.getenv("OLLAMA_URL", "http://localhost:11434"))
ollama_model = ollama_config.get("model_name", os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))
ollama_translation_model = ollama_config.get("translation_model", os.getenv("OLLAMA_TRANSLATION_MODEL", "llama3.2:latest"))

# Log module initialization details
print(f"[TP_MODULE_INIT] TP Module File: {__file__}")
print(f"[TP_MODULE_INIT] TP Directory: {TP_DIR}")
print(f"[TP_MODULE_INIT] TP Config File: {tp_config_file}")
print(f"[TP_MODULE_INIT] Config Manager File: {tp_config_manager.config_file}")
print(f"[TP_MODULE_INIT] ClaimProcessor Module: {ClaimProcessor.__module__}")
print(f"[TP_MODULE_INIT] ClaimProcessor File: {os.path.abspath(ClaimProcessor.__module__.replace('.', '/') + '.py') if hasattr(ClaimProcessor, '__module__') else 'Unknown'}")

# Initialize processors with Ollama configuration
tp_processor = ClaimProcessor(
    ollama_base_url=ollama_url,
    model_name=ollama_model,
    translation_model=ollama_translation_model,
    check_ollama_health=False,  # Don't check on import to avoid blocking
    prewarm_model=False  # Don't prewarm on import
)
tp_ocr_license_processor = ExcelOCRLicenseProcessor()
tp_unified_processor = UnifiedClaimProcessor(
    ollama_base_url=ollama_url,
    model_name=ollama_model,
    translation_model=ollama_translation_model
)

# Log processor initialization with file paths - use stored path
if hasattr(ClaimProcessor, '__module_file__'):
    tp_processor_file = os.path.abspath(ClaimProcessor.__module_file__)
elif hasattr(ClaimProcessor, '__file__'):
    tp_processor_file = os.path.abspath(ClaimProcessor.__file__)
elif 'CLAIM_PROCESSOR_FILE_PATH' in globals():
    tp_processor_file = os.path.abspath(CLAIM_PROCESSOR_FILE_PATH)
else:
    try:
        tp_processor_file = os.path.abspath(inspect.getfile(tp_processor.__class__))
    except:
        tp_processor_file = os.path.abspath(os.path.join(TP_DIR, 'claim_processor.py'))

print(f"[TP_MODULE_INIT] TP Processor Type: {type(tp_processor).__name__}")
print(f"[TP_MODULE_INIT] TP Processor Module: {type(tp_processor).__module__}")
print(f"[TP_MODULE_INIT] TP Processor File: {tp_processor_file}")
print(f"[TP_MODULE_INIT] TP Processor File Exists: {os.path.exists(tp_processor_file) if tp_processor_file != 'Unknown' else False}")
print(f"[TP_MODULE_INIT] Expected TP Processor File: {os.path.join(TP_DIR, 'claim_processor.py')}")
print(f"[TP_MODULE_INIT] Files Match: {tp_processor_file == os.path.join(TP_DIR, 'claim_processor.py')}")
print(f"[TP_MODULE_INIT] TP Processor Ollama URL: {tp_processor.ollama_base_url}")
print(f"[TP_MODULE_INIT] TP Processor Model: {tp_processor.model_name}")
print(f"[TP_MODULE_INIT] TP Unified Processor Type: {type(tp_unified_processor).__name__}")
print(f"[TP_MODULE_INIT] TP Unified Processor Module: {type(tp_unified_processor).__module__}")


def _validate_recovery_decision_api(current_party_idx: int, current_party_info: Dict[str, Any], 
                                    all_parties: List[Dict], accident_date: str,
                                    transaction_logger, case_number: str) -> Dict[str, Any]:
    """
    Validate ACCEPTED_WITH_RECOVERY decision - SAME LOGIC AS EXCEL unified_processor._validate_recovery_decision
    
    Rules for ACCEPTED_WITH_RECOVERY:
    1. Must apply to the victim party (Liability < 100%)
    2. There must be at least one other party with Liability > 0% (the one causing the accident)
    3. Recovery violations can be found in:
       - Current party's own recovery conditions (Recovery field, Act_Violation, License_Expiry_Date, etc.)
       - Other at-fault parties' recovery conditions
       - Recovery = TRUE, OR
       - model_recovery = TRUE (License_Type_From_Make_Model mismatch), OR
       - One of the specific violations (wrong way, red light, etc.)
    """
    import re
    
    current_liability = current_party_info.get("Liability", 0)
    current_recovery = str(current_party_info.get("Recovery", "")).strip()
    current_recovery_upper = current_recovery.upper()
    
    # Check current party's model_recovery (SAME AS EXCEL)
    current_license_type_make_model = str(current_party_info.get("License_Type_From_Make_Model", "")).strip()
    current_license_type_request = str(current_party_info.get("License_Type_From_Request", "")).strip()
    
    # Normalize values
    if current_license_type_make_model.lower() in ["none", "nan", "null"]:
        current_license_type_make_model = ""
    if current_license_type_request.lower() in ["none", "nan", "null"]:
        current_license_type_request = ""
    
    # Check model_recovery condition (SAME AS EXCEL)
    current_make_model_valid = (current_license_type_make_model and 
                               current_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               current_license_type_make_model.upper() != "ANY LICENSE")
    current_request_is_none_or_empty = (not current_license_type_request or 
                                       current_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
    current_request_mismatch = (current_license_type_request and 
                               current_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               current_license_type_make_model.upper() != current_license_type_request.upper())
    current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
    
    # Initialize recovery analysis
    current_party_recovery_analysis = {
        "recovery_field": current_recovery,
        "has_recovery_field": current_recovery_upper in ["TRUE", "1", "YES", "Y"] or current_recovery in ["True", "true", "TRUE"],
        "model_recovery": current_has_model_recovery,
        "has_model_recovery": current_has_model_recovery,
        "act_violation": str(current_party_info.get("Act_Violation", "")).strip(),
        "license_expiry_date": str(current_party_info.get("License_Expiry_Date", "")).strip(),
        "license_type_from_make_model": current_license_type_make_model,
        "license_type_from_request": current_license_type_request,
        "violations_found": []
    }
    
    # Rule 1: ACCEPTED_WITH_RECOVERY should only apply to parties with liability < 100%
    if current_liability >= 100:
        return {
            "is_valid": False,
            "reason": f"ACCEPTED_WITH_RECOVERY can only apply to parties with liability < 100%, but this party has Liability={current_liability}%",
            "corrected_decision": "REJECTED",
            "recovery_reasons": [],
            "current_party_recovery_analysis": current_party_recovery_analysis
        }
    
    # Rule 2: Check CURRENT PARTY's own recovery conditions first
    recovery_violations_found = False
    recovery_reasons = []
    
    # Check current party's Recovery field
    if current_recovery_upper in ["TRUE", "1", "YES", "Y"] or current_recovery in ["True", "true", "TRUE"]:
        recovery_violations_found = True
        recovery_reasons.append(f"Current Party {current_party_idx + 1} has Recovery=True/TRUE/true")
        current_party_recovery_analysis["violations_found"].append("Recovery field is True/TRUE/true")
    
    # Check current party's model_recovery field (SAME AS Recovery logic)
    if current_has_model_recovery:
        recovery_violations_found = True
        recovery_reasons.append(f"Current Party {current_party_idx + 1} has model_recovery=True")
        current_party_recovery_analysis["violations_found"].append("model_recovery field is True")
    
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
    
    # Check license type mismatch
    license_type_make_model = current_party_recovery_analysis["license_type_from_make_model"]
    license_type_request = current_party_recovery_analysis["license_type_from_request"]
    if (license_type_make_model and 
        license_type_make_model.lower() not in ["not identify", "not identified", ""] and
        license_type_request and 
        license_type_request.lower() not in ["not identify", "not identified", ""] and
        license_type_make_model.upper() != "ANY LICENSE"):
        if license_type_make_model.upper() != license_type_request.upper():
            if license_type_make_model.upper() not in license_type_request.upper() and \
               license_type_request.upper() not in license_type_make_model.upper():
                recovery_violations_found = True
                recovery_reasons.append(f"Current Party {current_party_idx + 1} has license type mismatch: {license_type_make_model} vs {license_type_request}")
                current_party_recovery_analysis["violations_found"].append("License type mismatch")
    
    # Rule 3: Check if there are other parties with Liability > 0% (the ones causing the accident)
    at_fault_parties = []
    for idx, other_party in enumerate(all_parties):
        if idx == current_party_idx:
            continue
        
        other_liability = other_party.get("Liability", 0)
        if other_liability > 0:
            at_fault_parties.append({
                "idx": idx,
                "party": other_party
            })
    
    # Rule 4: Check other at-fault parties for recovery conditions (if current party doesn't have recovery)
    if not recovery_violations_found and at_fault_parties:
        for at_fault_party in at_fault_parties:
            other_party = at_fault_party["party"]
            
            # Check Recovery field
            recovery_field = str(other_party.get("Recovery", "")).strip()
            recovery_field_upper = recovery_field.upper()
            if recovery_field_upper in ["TRUE", "1", "YES", "Y"] or recovery_field in ["True", "true", "TRUE"]:
                recovery_violations_found = True
                recovery_reasons.append(f"At-fault Party {at_fault_party['idx'] + 1} has Recovery=True/TRUE/true")
                continue
            
            # Check model_recovery (SAME AS EXCEL)
            other_license_type_make_model = str(other_party.get("License_Type_From_Make_Model", "")).strip()
            other_license_type_request = str(other_party.get("License_Type_From_Request", "")).strip()
            
            if other_license_type_make_model.lower() in ["none", "nan", "null"]:
                other_license_type_make_model = ""
            if other_license_type_request.lower() in ["none", "nan", "null"]:
                other_license_type_request = ""
            
            other_make_model_valid = (other_license_type_make_model and 
                                     other_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                     other_license_type_make_model.upper() != "ANY LICENSE")
            other_request_is_none_or_empty = (not other_license_type_request or 
                                             other_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
            other_request_mismatch = (other_license_type_request and 
                                     other_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                     other_license_type_make_model.upper() != other_license_type_request.upper())
            other_has_model_recovery = other_make_model_valid and (other_request_is_none_or_empty or other_request_mismatch)
            
            if other_has_model_recovery:
                recovery_violations_found = True
                recovery_reasons.append(f"At-fault Party {at_fault_party['idx'] + 1} has model_recovery=True")
                continue
            
            # Check Act/Violation
            act_violation = str(other_party.get("Act_Violation", "")).strip().upper()
            if act_violation:
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
    
    # If no recovery violations found, decision is invalid
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
        "recovery_reasons": recovery_reasons,
        "current_party_recovery_analysis": current_party_recovery_analysis
    }


def process_tp_claim(data):
    """
    Process TP claim - ALL functionality from TP path (MotorclaimdecisionlinuxTP/)
    
    This is the main entry point for TP claim processing.
    All processing logic is contained within this TP directory.
    
    Args:
        data: Request JSON data containing claim information
        
    Returns:
        Flask response with processed claim results
    """
    # Start timing for entire request
    import time
    request_start_time = time.time()
    request_start_datetime = datetime.now()
    
    try:
        case_number = data.get("Case_Number", "")
        
        transaction_logger.info(
            f"TP_REQUEST_START | Case: {case_number} | "
            f"Start_Time: {request_start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')} | "
            f"Timestamp: {request_start_time}"
        )
        
        # Verify we're using TP processors and config
        tp_config_manager.reload_config()
        current_config_file = tp_config_manager.config_file
        
        # Get detailed information about the processing environment
        import inspect
        tp_module_file = os.path.abspath(__file__)
        
        # Get actual file paths using stored paths or inspect
        # Use stored path first (most reliable for dynamically loaded modules)
        if hasattr(ClaimProcessor, '__module_file__'):
            claim_processor_file = os.path.abspath(ClaimProcessor.__module_file__)
        elif hasattr(ClaimProcessor, '__file__'):
            claim_processor_file = os.path.abspath(ClaimProcessor.__file__)
        elif 'CLAIM_PROCESSOR_FILE_PATH' in globals():
            claim_processor_file = os.path.abspath(CLAIM_PROCESSOR_FILE_PATH)
        else:
            try:
                claim_processor_file = os.path.abspath(inspect.getfile(ClaimProcessor))
            except:
                claim_processor_file = os.path.abspath(os.path.join(TP_DIR, 'claim_processor.py'))
        
        try:
            config_manager_file = os.path.abspath(inspect.getfile(ConfigManager))
        except:
            config_manager_file = os.path.abspath(os.path.join(TP_DIR, 'config_manager.py'))
        
        try:
            unified_processor_file = os.path.abspath(inspect.getfile(UnifiedClaimProcessor))
        except:
            unified_processor_file = os.path.abspath(os.path.join(TP_DIR, 'unified_processor.py'))
        
        # Verify processor is from TP directory
        expected_processor_file = os.path.join(TP_DIR, 'claim_processor.py')
        processor_file_correct = os.path.abspath(claim_processor_file) == os.path.abspath(expected_processor_file) if claim_processor_file != "Unknown" else False
        
        if not processor_file_correct:
            error_msg = f"CRITICAL: TP Processor loaded from wrong file! Expected: {expected_processor_file}, Got: {claim_processor_file}"
            transaction_logger.error(f"TP_PROCESSOR_PATH_ERROR | {error_msg}")
            print(f"[ERROR] {error_msg}")
        
        # Get current working directory
        current_working_dir = os.getcwd()
        
        # Log comprehensive processing start information
        # Get model info for performance tracking
        current_model = tp_processor.model_name
        previous_model = "qwen2.5:3b"  # Previous model for comparison (baseline)
        
        transaction_logger.info(
            f"TP_CLAIM_PROCESSING_START | Case: {case_number} | "
            f"TP_Module_File: {tp_module_file} | "
            f"TP_Directory: {TP_DIR} | "
            f"Current_Working_Dir: {current_working_dir} | "
            f"TP_Config_File: {tp_config_file} | "
            f"Current_Config_File: {current_config_file} | "
            f"Config_Match: {tp_config_file == current_config_file} | "
            f"TP_Processor_Type: {type(tp_processor).__name__} | "
            f"TP_Processor_Module: {type(tp_processor).__module__} | "
            f"TP_Processor_File: {claim_processor_file} | "
            f"Expected_Processor_File: {expected_processor_file} | "
            f"Processor_File_Correct: {processor_file_correct} | "
            f"TP_Config_Manager_File: {config_manager_file} | "
            f"TP_Unified_Processor_File: {unified_processor_file} | "
            f"TP_Processor_Ollama_URL: {tp_processor.ollama_base_url} | "
            f"TP_Processor_Model: {current_model} | "
            f"TP_Processor_Translation_Model: {getattr(tp_processor, 'translation_model', 'N/A')} | "
            f"Model_Changed: {current_model != previous_model} | "
            f"Previous_Model: {previous_model}"
        )
        
        # Verify config file is correct
        if tp_config_file != current_config_file:
            error_msg = f"TP Config file mismatch! Expected: {tp_config_file}, Got: {current_config_file}"
            transaction_logger.error(f"TP_CONFIG_ERROR | {error_msg}")
            return jsonify({"error": error_msg}), 500
        
        # ========== HANDLE HTML/XML/JSON DATA (SAME AS EXCEL) ==========
        # Excel uses clean_data() and detect_and_convert() to handle HTML/XML/JSON strings
        # If data is a string (HTML/XML/JSON), clean and parse it using SAME logic as Excel
        data_cleaning_start = time.time()
        if isinstance(data, str):
            transaction_logger.info(
                f"TP_DATA_CLEANING_START | Case: {case_number} | "
                f"Data_Type: string | Data_Length: {len(data)} | "
                f"Data_Preview: {data[:200]} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
            
            # Use unified processor's clean_data() method (SAME AS EXCEL)
            data_cleaned = tp_unified_processor.clean_data(data)
            data_cleaning_time = time.time() - data_cleaning_start
            
            transaction_logger.info(
                f"TP_DATA_CLEANED | Case: {case_number} | "
                f"Cleaned_Length: {len(data_cleaned)} | "
                f"Cleaned_Preview: {data_cleaned[:200]} | "
                f"Cleaning_Time_Seconds: {data_cleaning_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
            
            # Use unified processor's detect_and_convert() method (SAME AS EXCEL)
            # This handles XML/JSON detection and conversion
            data_conversion_start = time.time()
            try:
                data = tp_unified_processor.detect_and_convert(data_cleaned)
                data_conversion_time = time.time() - data_conversion_start
                transaction_logger.info(
                    f"TP_DATA_CONVERTED | Case: {case_number} | "
                    f"Format_Detected: {'XML' if data_cleaned.strip().startswith('<') else 'JSON'} | "
                    f"Converted_Type: {type(data).__name__} | "
                    f"Conversion_Time_Seconds: {data_conversion_time:.4f} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
            except Exception as e:
                error_msg = f"Failed to parse HTML/XML/JSON data: {str(e)[:200]}"
                transaction_logger.error(
                    f"TP_DATA_PARSE_ERROR | Case: {case_number} | Error: {error_msg} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
                return jsonify({"error": error_msg}), 400
        else:
            data_cleaning_time = time.time() - data_cleaning_start
            transaction_logger.info(
                f"TP_DATA_SKIP_CLEANING | Case: {case_number} | "
                f"Data_Type: {type(data).__name__} (not string, skipping cleaning) | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Also check if data contains a "Request" field with HTML/XML/JSON string (SAME AS EXCEL)
        # Excel processes data from "Request" column which may contain HTML/XML/JSON strings
        request_field_start = time.time()
        if isinstance(data, dict) and "Request" in data:
            request_data = data.get("Request")
            if isinstance(request_data, str) and (request_data.strip().startswith('<') or request_data.strip().startswith('{')):
                transaction_logger.info(
                    f"TP_REQUEST_FIELD_FOUND | Case: {case_number} | "
                    f"Request_Field_Length: {len(request_data)} | "
                    f"Request_Field_Preview: {request_data[:200]} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
                
                # Clean and parse Request field (SAME AS EXCEL)
                request_cleaned = tp_unified_processor.clean_data(request_data)
                request_parsing_start = time.time()
                try:
                    request_parsed = tp_unified_processor.detect_and_convert(request_cleaned)
                    request_parsing_time = time.time() - request_parsing_start
                    # Merge parsed request data into main data dict (SAME AS EXCEL)
                    if isinstance(request_parsed, dict):
                        # Merge request data into main data, with request data taking precedence
                        data = {**data, **request_parsed}
                        transaction_logger.info(
                            f"TP_REQUEST_FIELD_PARSED | Case: {case_number} | "
                            f"Format_Detected: {'XML' if request_cleaned.strip().startswith('<') else 'JSON'} | "
                            f"Merged_Fields: {list(request_parsed.keys())[:10]} | "
                            f"Parsing_Time_Seconds: {request_parsing_time:.4f} | "
                            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                        )
                except Exception as e:
                    request_parsing_time = time.time() - request_parsing_start
                    transaction_logger.warning(
                        f"TP_REQUEST_FIELD_PARSE_WARNING | Case: {case_number} | "
                        f"Failed to parse Request field, using original data | Error: {str(e)[:200]} | "
                        f"Parsing_Time_Seconds: {request_parsing_time:.4f} | "
                        f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                    )
        request_field_time = time.time() - request_field_start
        if request_field_time > 0.001:  # Only log if significant time spent
            transaction_logger.info(
                f"TP_REQUEST_FIELD_CHECK | Case: {case_number} | "
                f"Check_Time_Seconds: {request_field_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Extract request data - USE SAME LOGIC AS EXCEL unified_processor
        data_extraction_start = time.time()
        # Extract accident info (same as Excel extract_accident_info)
        accident_data = data.get("Accident_info", {})
        if not accident_data:
            # Try Case_Info structure (same as Excel)
            case_info = data.get("Case_Info", {})
            if case_info:
                accident_data = case_info.get("Accident_info", {})
        
        transaction_logger.info(
            f"TP_DATA_EXTRACTION_START | Case: {case_number} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Extract accident fields (same as Excel extract_accident_info - lines 4277-4303)
        accident_date = (
            accident_data.get("callDate") or
            accident_data.get("call_date") or
            data.get("Accident_Date", "")
        )
        upload_date = data.get("Upload_Date", "")
        claim_requester_id = data.get("Claim_requester_ID", None)
        accident_description = (
            accident_data.get("AccidentDescription") or
            accident_data.get("accident_description") or
            data.get("accident_description", "")
        )
        ld_rep_base64 = data.get("Name_LD_rep_64bit", "")
        
        # Extract DAA parameters - USE SAME LOGIC AS EXCEL (lines 4355-4426)
        # Excel tries multiple locations and field name variations
        daa_extraction_start = time.time()
        daa_from_request = {
            'isDAA': None,
            'Suspect_as_Fraud': None,
            'DaaReasonEnglish': None
        }
        
        # Try multiple possible locations (same as Excel)
        accident_info_raw = None
        if isinstance(data, dict):
            # Try EICWS structure
            if "EICWS" in data:
                case_info = data.get("EICWS", {}).get("cases", {}).get("Case_Info", {})
                accident_info_raw = case_info.get("Accident_info", {})
            # Try cases structure
            elif "cases" in data:
                case_info = data.get("cases", {}).get("Case_Info", {})
                accident_info_raw = case_info.get("Accident_info", {})
            # Try Case_Info structure
            elif "Case_Info" in data:
                accident_info_raw = data.get("Case_Info", {}).get("Accident_info", {})
            # Try direct accident_info
            elif "Accident_info" in data:
                accident_info_raw = data.get("Accident_info", {})
            # Try at root level
            if not accident_info_raw:
                accident_info_raw = data
        
        # Extract DAA values (EXACT Excel logic - lines 4388-4426)
        if accident_info_raw:
            # Try various field name variations (same as Excel)
            isDAA_value = (
                accident_info_raw.get("isDAA") or
                accident_info_raw.get("is_daa") or
                accident_info_raw.get("IsDAA") or
                data.get("isDAA") or  # Also check root level
                None
            )
            if isDAA_value is not None:
                # Convert to string and normalize (EXACT Excel logic)
                isDAA_str = str(isDAA_value).strip().upper()
                # Normalize boolean values (same as Excel)
                if isDAA_str in ['TRUE', '1', 'YES', 'Y', 'T']:
                    daa_from_request['isDAA'] = 'TRUE'
                    isDAA = True
                elif isDAA_str in ['FALSE', '0', 'NO', 'N', 'F']:
                    daa_from_request['isDAA'] = 'FALSE'
                    isDAA = False
                else:
                    daa_from_request['isDAA'] = isDAA_str
                    isDAA = isDAA_value
            else:
                isDAA = data.get("isDAA", None)
            
            suspect_fraud_value = (
                accident_info_raw.get("Suspect_as_Fraud") or
                accident_info_raw.get("suspect_as_fraud") or
                accident_info_raw.get("SuspectAsFraud") or
                data.get("Suspect_as_Fraud") or  # Also check root level
                None
            )
            if suspect_fraud_value is not None:
                suspect_as_fraud = str(suspect_fraud_value).strip()
                daa_from_request['Suspect_as_Fraud'] = suspect_as_fraud
            else:
                suspect_as_fraud = data.get("Suspect_as_Fraud", None)
            
            daa_reason_value = (
                accident_info_raw.get("DaaReasonEnglish") or
                accident_info_raw.get("daa_reason_english") or
                accident_info_raw.get("DaaReason") or
                accident_info_raw.get("daaReasonEnglish") or
                data.get("DaaReasonEnglish") or  # Also check root level
                None
            )
            if daa_reason_value is not None:
                daa_reason_english = str(daa_reason_value).strip()
                daa_from_request['DaaReasonEnglish'] = daa_reason_english
            else:
                daa_reason_english = data.get("DaaReasonEnglish", None)
        else:
            # Fallback to root level (same as Excel)
            isDAA = data.get("isDAA", None)
            suspect_as_fraud = data.get("Suspect_as_Fraud", None)
            daa_reason_english = data.get("DaaReasonEnglish", None)
        
        # Log DAA extraction (same as Excel)
        daa_extraction_time = time.time() - daa_extraction_start
        if any([isDAA, suspect_as_fraud, daa_reason_english]):
            transaction_logger.info(
                f"TP_DAA_EXTRACTED | Case: {case_number} | "
                f"isDAA: {isDAA} | Suspect_as_Fraud: {suspect_as_fraud} | "
                f"DaaReasonEnglish: {daa_reason_english[:50] if daa_reason_english else None} | "
                f"DAA_Extraction_Time_Seconds: {daa_extraction_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        else:
            transaction_logger.info(
                f"TP_DAA_EXTRACTION_COMPLETE | Case: {case_number} | "
                f"No DAA data found | DAA_Extraction_Time_Seconds: {daa_extraction_time:.4f} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Process OCR
        ocr_processing_start = time.time()
        ocr_text = None
        if ld_rep_base64:
            transaction_logger.info(
                f"TP_OCR_PROCESSING_START | Case: {case_number} | "
                f"LD_Rep_Base64_Length: {len(ld_rep_base64)} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
            try:
                if ld_rep_base64.startswith('data:text') or ld_rep_base64.startswith('data:image'):
                    if ',' in ld_rep_base64:
                        base64_part = ld_rep_base64.split(',')[1]
                    else:
                        base64_part = ld_rep_base64
                else:
                    base64_part = ld_rep_base64
                
                try:
                    decoded = base64.b64decode(base64_part).decode('utf-8', errors='ignore')
                    if '<html' in decoded.lower() or 'party' in decoded.lower() or 'رخصة' in decoded:
                        ocr_text = decoded
                        transaction_logger.info(f"TP_OCR_TEXT_EXTRACTED | Case: {case_number}")
                except:
                    pass
            except Exception as e:
                transaction_logger.error(f"TP_BASE64_PROCESSING_ERROR | Case: {case_number} | Error: {str(e)[:100]}")
        
        # Process OCR with TP OCR processor - SAME AS EXCEL
        # Excel translates OCR text to English for better extraction (unified_processor.py lines 4862-4876)
        ocr_text_for_processing = ocr_text
        if ocr_text:
            try:
                # CRITICAL: Translate OCR text to English (SAME AS EXCEL)
                # Excel uses translate_ocr_to_english for better extraction
                has_arabic = bool(re.search(r'[\u0600-\u06FF]', ocr_text) if ocr_text else False)
                transaction_logger.info(
                    f"TP_OCR_TRANSLATION_START | Case: {case_number} | "
                    f"OCR_Text_Length: {len(ocr_text) if ocr_text else 0} | "
                    f"Has_Arabic: {has_arabic} | "
                    f"OCR_Text_Preview: {ocr_text[:500] if ocr_text else 'N/A'}"
                )
                
                # Translate OCR text to English (same as Excel - unified_processor.py lines 4862-4876)
                if ocr_text and has_arabic:
                    try:
                        # Use unified processor's translate_ocr_to_english method (same as Excel)
                        if hasattr(tp_unified_processor, 'translate_ocr_to_english'):
                            transaction_logger.info(
                                f"TP_OCR_TRANSLATION_CALLING | Case: {case_number} | "
                                f"Method: translate_ocr_to_english | "
                                f"Translation_Model: {getattr(tp_unified_processor, 'translation_model', 'llama3.2:latest')}"
                            )
                            ocr_text_translated = tp_unified_processor.translate_ocr_to_english(ocr_text)
                            if ocr_text_translated and ocr_text_translated != ocr_text:
                                ocr_text_for_processing = ocr_text_translated
                                transaction_logger.info(
                                    f"TP_OCR_TRANSLATION_SUCCESS | Case: {case_number} | "
                                    f"Original_Length: {len(ocr_text)} | "
                                    f"Translated_Length: {len(ocr_text_translated)} | "
                                    f"Original_Preview: {ocr_text[:200]} | "
                                    f"Translated_Preview: {ocr_text_translated[:200]}"
                                )
                            else:
                                transaction_logger.info(
                                    f"TP_OCR_TRANSLATION_SKIPPED | Case: {case_number} | "
                                    f"Translation returned same/empty text, using original | "
                                    f"Original: {ocr_text[:200]} | Translated: {ocr_text_translated[:200] if ocr_text_translated else 'N/A'}"
                                )
                        else:
                            # Fallback to _translate_arabic_to_english if translate_ocr_to_english not available
                            transaction_logger.warning(
                                f"TP_OCR_TRANSLATION_METHOD_NOT_FOUND | Case: {case_number} | "
                                f"translate_ocr_to_english not found, using _translate_arabic_to_english"
                            )
                            if hasattr(tp_unified_processor, '_translate_arabic_to_english'):
                                transaction_logger.info(
                                    f"TP_OCR_TRANSLATION_CALLING | Case: {case_number} | "
                                    f"Method: _translate_arabic_to_english | "
                                    f"Translation_Model: {getattr(tp_unified_processor, 'translation_model', 'llama3.2:latest')}"
                                )
                                ocr_text_translated = tp_unified_processor._translate_arabic_to_english(ocr_text)
                                if ocr_text_translated and ocr_text_translated != ocr_text:
                                    ocr_text_for_processing = ocr_text_translated
                                    transaction_logger.info(
                                        f"TP_OCR_TRANSLATION_SUCCESS | Case: {case_number} | "
                                        f"Using _translate_arabic_to_english | "
                                        f"Original_Length: {len(ocr_text)} | "
                                        f"Translated_Length: {len(ocr_text_translated)} | "
                                        f"Original_Preview: {ocr_text[:200]} | "
                                        f"Translated_Preview: {ocr_text_translated[:200]}"
                                    )
                    except Exception as e:
                        transaction_logger.error(
                            f"TP_OCR_TRANSLATION_ERROR | Case: {case_number} | "
                            f"Error: {str(e)[:500]} | Error_Type: {type(e).__name__} | "
                            f"Using original OCR text"
                        )
                        ocr_text_for_processing = ocr_text
                else:
                    transaction_logger.info(
                        f"TP_OCR_TRANSLATION_SKIPPED | Case: {case_number} | "
                        f"No Arabic text detected (Has_Arabic: {has_arabic}), using original OCR text"
                    )
                
                # Process with translated OCR text (same as Excel)
                ocr_validation_start = time.time()
                data = tp_ocr_license_processor.process_claim_data_with_ocr(
                    claim_data=data,
                    ocr_text=ocr_text_for_processing,  # Use translated text
                    base64_image=ld_rep_base64 if not ocr_text_for_processing else None
                )
                ocr_validation_time = time.time() - ocr_validation_start
                ocr_processing_time = time.time() - ocr_processing_start
                transaction_logger.info(
                    f"TP_OCR_VALIDATION_SUCCESS | Case: {case_number} | "
                    f"OCR_Text_Used: Translated={ocr_text_for_processing != ocr_text} | "
                    f"OCR_Text_Length: {len(ocr_text_for_processing) if ocr_text_for_processing else 0} | "
                    f"OCR_Validation_Time_Seconds: {ocr_validation_time:.4f} | "
                    f"OCR_Total_Processing_Time_Seconds: {ocr_processing_time:.4f} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
            except Exception as e:
                ocr_processing_time = time.time() - ocr_processing_start
                transaction_logger.error(
                    f"TP_OCR_VALIDATION_ERROR | Case: {case_number} | Error: {str(e)[:200]} | "
                    f"OCR_Processing_Time_Seconds: {ocr_processing_time:.4f} | "
                    f"Time_From_Start: {time.time() - request_start_time:.4f}s"
                )
        else:
            transaction_logger.info(
                f"TP_OCR_SKIPPED | Case: {case_number} | "
                f"No LD_rep_base64 provided | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        # Build accident info - USE SAME LOGIC AS EXCEL extract_accident_info (lines 4277-4303)
        accident_info_start = time.time()
        # Extract all accident fields (same field name variations as Excel)
        case_number_extracted = (
            accident_data.get("caseNumber") or
            accident_data.get("case_number") or
            case_number
        )
        surveyor = accident_data.get("surveyorName", accident_data.get("surveyor_name", ""))
        call_date_extracted = (
            accident_data.get("callDate") or
            accident_data.get("call_date") or
            accident_date
        )
        call_time = accident_data.get("callTime", accident_data.get("call_time", ""))
        city = accident_data.get("city", accident_data.get("City", ""))
        location = accident_data.get("location", accident_data.get("Location", ""))
        coordinates = accident_data.get("LocationCoordinates", accident_data.get("location_coordinates", ""))
        landmark = accident_data.get("landmark", accident_data.get("Landmark", ""))
        description_extracted = (
            accident_data.get("AccidentDescription") or
            accident_data.get("accident_description") or
            accident_description
        )
        
        # Build accident_info (same structure as Excel extract_accident_info)
        if description_extracted:
            accident_desc = description_extracted
        else:
            accident_desc = f"Case: {case_number_extracted}, Date: {call_date_extracted}"
        
        accident_info = {
            # Core fields (same as Excel extract_accident_info)
            "caseNumber": case_number_extracted,
            "case_number": case_number_extracted,
            "Case_Number": case_number_extracted,
            "Surveyor": surveyor,
            "surveyorName": surveyor,
            "surveyor_name": surveyor,
            "Call_Date": call_date_extracted,
            "callDate": call_date_extracted,
            "call_date": call_date_extracted,
            "Call_Time": call_time,
            "callTime": call_time,
            "call_time": call_time,
            "City": city,
            "city": city,
            "Location": location,
            "location": location,
            "Coordinates": coordinates,
            "LocationCoordinates": coordinates,
            "location_coordinates": coordinates,
            "Landmark": landmark,
            "landmark": landmark,
            "AccidentDescription": accident_desc,
            "accident_description": accident_desc,
            "Description": accident_desc,
            # Additional fields for API compatibility
            "Upload_Date": upload_date,
            "Claim_requester_ID": claim_requester_id,
            "Name_LD_rep_64bit": ld_rep_base64,
            "isDAA": isDAA,
            "Suspect_as_Fraud": suspect_as_fraud,
            "DaaReasonEnglish": daa_reason_english
        }
        
        accident_info_time = time.time() - accident_info_start
        data_extraction_time = time.time() - data_extraction_start  # Stop data extraction timer
        transaction_logger.info(
            f"TP_ACCIDENT_INFO_BUILT | Case: {case_number} | "
            f"Accident_Info_Build_Time_Seconds: {accident_info_time:.4f} | "
            f"Data_Extraction_Total_Time_Seconds: {data_extraction_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # TP processes ALL parties (no filtering)
        transaction_logger.info(
            f"TP_PROCESSING_ALL_PARTIES | Case: {case_number} | "
            f"Total_Parties: {len(data.get('Parties', []))} | No_Filtering_Applied | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Convert parties for TP processing - USE SAME LOGIC AS EXCEL unified_processor
        # This ensures 100% accuracy match with Excel processing
        party_conversion_start = time.time()
        converted_parties = []
        
        for idx, party in enumerate(data["Parties"]):
            insurance_type = "TP"
            
            # Use unified_processor.extract_party_info logic for field extraction
            # Handle all field name variations (same as Excel)
            party_id = party.get("ID", party.get("id", party.get("Id", party.get("Party_ID", ""))))
            name = party.get("name", party.get("Name", party.get("Party_Name", "")))
            liability = party.get("Liability", party.get("liability", 0))
            try:
                liability = int(liability) if liability else 0
            except:
                liability = 0
            
            # Extract insurance info (same as Excel - handles multiple structures)
            insurance_info_raw = party.get("Insurance_Info", {})
            if not insurance_info_raw:
                insurance_info_raw = party.get("insurance_info", {})
            if not insurance_info_raw:
                insurance_info_raw = party.get("InsuranceInfo", {})
            
            # Extract insurance name (same as Excel logic)
            insurance_name_arabic = insurance_info_raw.get("ICArabicName", insurance_info_raw.get("ic_arabic_name", ""))
            insurance_name_english = (
                insurance_info_raw.get("ICEnglishName") or
                insurance_info_raw.get("ic_english_name") or
                insurance_info_raw.get("EnglishNam") or
                insurance_info_raw.get("english_nam") or
                insurance_info_raw.get("EnglishName") or
                insurance_info_raw.get("english_name") or
                party.get("ICEnglishName") or  # Check top level (Excel logic)
                party.get("ic_english_name") or
                party.get("EnglishNam") or
                party.get("english_nam") or
                party.get("EnglishName") or
                party.get("english_name") or
                party.get("Insurance_Name", "") or  # Fallback to Insurance_Name
                ""
            )
            insurance_name = insurance_name_arabic if insurance_name_arabic else insurance_name_english
            
            # Build insurance_info (same structure as Excel)
            # Extract Policyholder_ID - check multiple locations (same as Excel)
            policy_number = (
                party.get("Policyholder_ID") or
                party.get("PolicyholderID") or
                party.get("policyholder_id") or
                insurance_info_raw.get("policyNumber") or
                insurance_info_raw.get("policy_number") or
                ""
            )
            
            # Extract Policyholdername from party data (optional parameter)
            # Supports multiple field name variations
            policyholder_name = (
                party.get("Policyholdername") or
                party.get("Policyholder_Name") or
                party.get("PolicyholderName") or
                party.get("policyholder_name") or
                party.get("Policy_Holder_Name") or
                ""
            )
            if not policy_number:
                policy_number = ""
            vehicle_id = insurance_info_raw.get("vehicleID", insurance_info_raw.get("vehicle_id", party.get("Vehicle_Serial", "")))
            
            insurance_info = {
                "ICArabicName": insurance_name_arabic if insurance_name_arabic else insurance_name,
                "ICEnglishName": insurance_name_english if insurance_name_english else insurance_name,
                "policyNumber": policy_number,
                "insuranceCompanyID": insurance_info_raw.get("insuranceCompanyID", ""),
                "vehicleID": vehicle_id,
                "insuranceType": insurance_type
            }
            
            # Extract car make/model (same as Excel processing - handles multiple field names)
            car_make = (
                party.get("carMake") or
                party.get("car_make") or
                party.get("carMake_Najm") or
                party.get("Vehicle_Make") or
                party.get("vehicle_make") or
                ""
            )
            car_model = (
                party.get("carModel") or
                party.get("car_model") or
                party.get("carModel_Najm") or
                party.get("Vehicle_Model") or
                party.get("vehicle_model") or
                ""
            )
            car_year = party.get("carMfgYear", party.get("car_year", party.get("Vehicle_Year", "")))
            
            # Extract other fields (same as Excel extract_party_info)
            chassis_no = party.get("chassisNo", party.get("chassis_no", party.get("Vehicle_Serial", party.get("Chassis_No", ""))))
            vehicle_owner_id = party.get("VehicleOwnerId", party.get("vehicleOwnerId", party.get("vehicle_owner_id", "")))
            license_type_from_request = party.get("licenseType", party.get("license_type", party.get("License_Type_From_Najm", "")))
            recovery = party.get("recovery", party.get("Recovery", False))
            
            # Extract damage info (same as Excel)
            damages = party.get("Damages", {})
            damage_type = ""
            if damages:
                damage_info = damages.get("Damage_Info", {})
                if isinstance(damage_info, list) and len(damage_info) > 0:
                    damage_info = damage_info[0]
                if isinstance(damage_info, dict):
                    damage_type = damage_info.get("damageType", damage_info.get("damage_type", ""))
            
            # Extract Act/Violation (same as Excel)
            acts = party.get("Acts", {})
            act_description = ""
            if acts:
                act_info = acts.get("Act_Info", {})
                if isinstance(act_info, list) and len(act_info) > 0:
                    act_info = act_info[0]
                if isinstance(act_info, dict):
                    act_description = act_info.get("actEnglish", act_info.get("act_english", ""))
                    if not act_description:
                        act_description = act_info.get("actArabic", act_info.get("act_arabic", ""))
            
            # CRITICAL: Add License_Type_From_Make_Model BEFORE processing (same as Excel)
            # This ensures 100% accuracy match with Excel processing
            license_type_from_make_model = ""
            if car_make and car_model:
                try:
                    license_type_from_make_model = tp_unified_processor.lookup_license_type_from_make_model(car_make, car_model)
                    transaction_logger.info(
                        f"TP_LICENSE_TYPE_LOOKUP | Case: {case_number} | Party: {idx + 1} | "
                        f"Make: {car_make} | Model: {car_model} | "
                        f"License_Type: {license_type_from_make_model}"
                    )
                except Exception as e:
                    transaction_logger.warning(
                        f"TP_LICENSE_TYPE_LOOKUP_ERROR | Case: {case_number} | Party: {idx + 1} | "
                        f"Error: {str(e)[:100]}"
                    )
                    license_type_from_make_model = ""
            
            # Build converted_party using SAME structure as Excel extract_party_info
            # This ensures process_party_claim receives data in the same format
            converted_party = {
                # Core fields (same as Excel extract_party_info)
                "ID": party_id,
                "id": party_id,
                "name": name,
                "Name": name,
                "Liability": liability,
                "liability": liability,
                
                # Insurance info (same structure as Excel)
                "Insurance_Info": insurance_info,
                "insurance_info": insurance_info,
                
                # Vehicle info (same field names as Excel)
                "carMake": car_make,
                "carModel": car_model,
                "carMfgYear": car_year,
                "car_year": car_year,
                "carMake_Najm": party.get("carMake_Najm", ""),
                "carModel_Najm": party.get("carModel_Najm", ""),
                "Vehicle_Make": car_make,
                "Vehicle_Model": car_model,
                "Vehicle_Year": car_year,
                "chassisNo": chassis_no,
                "chassis_no": chassis_no,
                "Chassis_No": chassis_no,
                "Vehicle_Serial": chassis_no,
                "Vehicle_ID": vehicle_id,
                "VehicleOwnerId": vehicle_owner_id,
                "vehicleOwnerId": vehicle_owner_id,
                "vehicle_owner_id": vehicle_owner_id,
                
                # License info (same as Excel)
                "licenseType": license_type_from_request,
                "license_type": license_type_from_request,
                "License_Type_From_Najm": license_type_from_request,
                "License_Type_From_Request": license_type_from_request,
                "License_Type_From_Make_Model": license_type_from_make_model,  # Added BEFORE processing (Excel match)
                "License_Expiry_Date": party.get("License_Expiry_Date", ""),
                "License_Expiry_Last_Updated": party.get("License_Expiry_Last_Updated", ""),
                
                # Recovery and other fields (same as Excel)
                "recovery": recovery,
                "Recovery": recovery,
                "Policyholder_ID": policy_number,
                "Policy_Number": policy_number,
                "Policyholdername": policyholder_name,  # NEW: Policyholder name parameter
                "Policyholder_Name": policyholder_name,  # Alternative field name
                "Party": party.get("Party", f"Party {idx + 1}"),
                "insurance_type": insurance_type,
                
                # Damage and Act info (same as Excel)
                "Damages": damages if damages else {},
                "Acts": acts if acts else {},
                "Damage_Type": damage_type,
                "Act_Violation": act_description,
                
                # Additional fields for compatibility
                "Party_ID": party_id,
                "Party_Name": name
            }
            
            converted_parties.append(converted_party)
            transaction_logger.info(
                f"TP_PARTY_ADDED | Case: {case_number} | Party: {idx + 1} | "
                f"Party_ID: {party_id} | Party_Name: {name} | "
                f"Insurance_Name: {insurance_name} | ICEnglishName: {insurance_name_english} | "
                f"License_Type_From_Make_Model: {license_type_from_make_model} | "
                f"Car_Make: {car_make} | Car_Model: {car_model} | "
                f"Time_From_Start: {time.time() - request_start_time:.4f}s"
            )
        
        party_conversion_time = time.time() - party_conversion_start
        transaction_logger.info(
            f"TP_PARTY_CONVERSION_COMPLETE | Case: {case_number} | "
            f"Parties_Converted: {len(converted_parties)} | "
            f"Party_Conversion_Time_Seconds: {party_conversion_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Build claim data
        claim_data_build_start = time.time()
        claim_data = {
            "Case_Info": {
                "Accident_info": accident_info,
                "parties": {
                    "Party_Info": converted_parties
                }
            }
        }
        claim_data_build_time = time.time() - claim_data_build_start
        transaction_logger.info(
            f"TP_CLAIM_DATA_BUILT | Case: {case_number} | "
            f"Claim_Data_Build_Time_Seconds: {claim_data_build_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # ========== GLOBAL VALIDATION: TAWUNIYA POLICYHOLDER vs VEHICLE OWNER (BEFORE PROCESSING) ==========
        # CRITICAL RULE: If ANY Tawuniya party has Policyholder_ID != VehicleOwnerId AND Liability >= 50,
        # REJECT ALL PARTIES immediately without processing (no Ollama, no other validations)
        global_validation_start = time.time()
        
        def is_tawuniya_insurance_global(insurance_name, ic_english_name):
            """Check if insurance is Tawuniya (same logic as validation)"""
            if not insurance_name and not ic_english_name:
                return False
            
            insurance_clean = str(insurance_name).strip().lower()
            ic_english_clean = str(ic_english_name).strip().lower() if ic_english_name else ""
            
            # Check ICEnglishName first (most reliable)
            if ic_english_clean:
                if "tawuniya" in ic_english_clean and "cooperative" in ic_english_clean and "insurance" in ic_english_clean:
                    return True
                if re.search(r'tawuniya\s*(?:c\b|co\b|coop|cooperative|insurance)', ic_english_clean):
                    return True
            
            # Check insurance name
            if insurance_clean:
                if "tawuniya" in insurance_clean and ("cooperative" in insurance_clean or "insurance" in insurance_clean):
                    return True
                if "التعاونية" in insurance_name or "التعاونيه" in insurance_name:
                    return True
            
            return False
        
        # Check all parties for Tawuniya Policyholder mismatch
        tawuniya_mismatch_found = False
        tawuniya_mismatch_party = None
        
        for check_idx, check_party in enumerate(converted_parties):
            check_insurance = str(check_party.get("Insurance_Name", "")).strip()
            check_insurance_info = check_party.get("Insurance_Info", {}) or check_party.get("insurance_info", {})
            check_ic_english = str(check_insurance_info.get("ICEnglishName", "")).strip()
            check_is_tawuniya = is_tawuniya_insurance_global(check_insurance, check_ic_english)
            
            if check_is_tawuniya:
                # Get Policyholder_ID and VehicleOwnerId
                check_policyholder_id = (
                    str(check_party.get("Policyholder_ID", "")).strip() or
                    str(check_party.get("PolicyholderID", "")).strip() or
                    str(check_party.get("policyholder_id", "")).strip() or
                    str(check_insurance_info.get("policyNumber", "")).strip() or
                    ""
                )
                
                check_vehicle_owner_id = (
                    str(check_party.get("VehicleOwnerId", "")).strip() or
                    str(check_party.get("vehicleOwnerId", "")).strip() or
                    str(check_party.get("vehicle_owner_id", "")).strip() or
                    ""
                )
                
                check_liability = check_party.get("Liability", 0)
                
                # Check if Policyholder_ID exists and doesn't match VehicleOwnerId AND Liability >= 50
                if (check_policyholder_id and 
                    check_policyholder_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"] and
                    check_vehicle_owner_id and 
                    check_vehicle_owner_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"]):
                    
                    # Normalize IDs for comparison
                    check_policyholder_id_normalized = str(check_policyholder_id).strip().replace(" ", "")
                    check_vehicle_owner_id_normalized = str(check_vehicle_owner_id).strip().replace(" ", "")
                    
                    # Check if they don't match AND Liability >= 50 (includes 50 and above)
                    if check_policyholder_id_normalized != check_vehicle_owner_id_normalized and check_liability >= 50:
                        tawuniya_mismatch_found = True
                        tawuniya_mismatch_party = {
                            "idx": check_idx,
                            "party_id": check_party.get("Party_ID", ""),
                            "policyholder_id": check_policyholder_id,
                            "vehicle_owner_id": check_vehicle_owner_id,
                            "liability": check_liability
                        }
                        break
        
        global_validation_time = time.time() - global_validation_start
        
        # If mismatch found, reject ALL parties immediately
        if tawuniya_mismatch_found:
            transaction_logger.warning(
                f"TP_GLOBAL_TAWUNIYA_POLICYHOLDER_MISMATCH | Case: {case_number} | "
                f"Party: {tawuniya_mismatch_party['idx'] + 1} | Party_ID: {tawuniya_mismatch_party['party_id']} | "
                f"Policyholder_ID: {tawuniya_mismatch_party['policyholder_id']} | "
                f"VehicleOwnerId: {tawuniya_mismatch_party['vehicle_owner_id']} | "
                f"Liability: {tawuniya_mismatch_party['liability']}% | "
                f"Action: REJECTING ALL PARTIES without processing | "
                f"Reason: Tawuniya party - Policyholder_ID does not match VehicleOwnerId and Liability >= 50"
            )
            
            # Create rejected results for ALL parties
            rejected_results = []
            for reject_idx, reject_party in enumerate(converted_parties):
                rejected_results.append({
                    "_index": reject_idx,
                    "Party": reject_party.get("Party", f"Party {reject_idx + 1}"),
                    "Party_ID": reject_party.get("Party_ID", reject_party.get("ID", "")),
                    "Party_Name": reject_party.get("Party_Name", reject_party.get("name", "")),
                    "Liability": reject_party.get("Liability", 0),
                    "Decision": "REJECTED",
                    "Classification": "Policy Holder not same vehicle Owner",
                    "Reasoning": f"Global Rejection: Tawuniya party (Party {tawuniya_mismatch_party['idx'] + 1}) has Policyholder_ID ({tawuniya_mismatch_party['policyholder_id']}) that does not match VehicleOwnerId ({tawuniya_mismatch_party['vehicle_owner_id']}) and Liability >= 50%",
                    "Applied_Conditions": ["Tawuniya Policyholder Mismatch"],
                    "isDAA": isDAA,
                    "Suspect_as_Fraud": suspect_as_fraud,
                    "DaaReasonEnglish": daa_reason_english,
                    "Policyholder_ID": reject_party.get("Policyholder_ID", ""),
                    "insurance_type": "TP"
                })
            
            # Sort by index and remove _index
            rejected_results_sorted = sorted(rejected_results, key=lambda x: x.get("_index", 0))
            final_results = []
            for result in rejected_results_sorted:
                if "_index" in result:
                    del result["_index"]
                final_results.append(result)
            
            # Build response with all rejected parties
            response_build_start = time.time()
            response_data = {
                "Case_Number": case_number,
                "Accident_Date": accident_date,
                "Upload_Date": upload_date,
                "Claim_requester_ID": claim_requester_id,
                "Status": "Success",
                "Parties": final_results,
                "Total_Parties": len(data["Parties"]),
                "Parties_Processed": len(final_results),
                "LD_Rep_64bit_Received": bool(ld_rep_base64),
                "Global_Rejection_Reason": "Tawuniya Policyholder_ID mismatch with VehicleOwnerId and Liability >= 50"
            }
            
            response_build_time = time.time() - response_build_start
            request_total_time = time.time() - request_start_time
            request_end_datetime = datetime.now()
            
            transaction_logger.info(
                f"TP_GLOBAL_REJECTION_APPLIED | Case: {case_number} | "
                f"All_Parties_Rejected: {len(final_results)} | "
                f"Triggering_Party: {tawuniya_mismatch_party['idx'] + 1} | "
                f"Total_Time_Seconds: {request_total_time:.4f} | "
                f"Global_Validation_Time: {global_validation_time:.4f}s"
            )
            
            transaction_logger.info(
                f"TP_REQUEST_COMPLETE | Case: {case_number} | "
                f"Total_Request_Time_Seconds: {request_total_time:.4f} | "
                f"Status: GLOBAL_REJECTION | "
                f"Parties_Count: {len(final_results)} | "
                f"Reason: Tawuniya Policyholder_ID mismatch"
            )
            
            return jsonify(response_data), 200
        
        transaction_logger.info(
            f"TP_GLOBAL_TAWUNIYA_CHECK_PASSED | Case: {case_number} | "
            f"Global_Validation_Time: {global_validation_time:.4f}s | "
            f"No_Tawuniya_Policyholder_Mismatch_Found | Proceeding_with_Normal_Processing"
        )
        
        # Process parties in parallel - ENHANCED: Process ALL parties simultaneously for maximum performance
        # No limit on workers - process all parties at the same time (like Excel batch processing)
        results = []
        max_workers = len(converted_parties)  # Process all parties in parallel
        
        parallel_processing_start = time.time()
        transaction_logger.info(
            f"TP_PARALLEL_PROCESSING_START | Case: {case_number} | "
            f"Parties_Count: {len(converted_parties)} | Max_Workers: {max_workers} | "
            f"Processing_Mode: FULL_PARALLEL (All parties simultaneously) | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        processing_start_time = datetime.now()
        
        def process_single_party(idx, party):
            """Process a single party using TP processor"""
            nonlocal claim_data, ocr_text, ld_rep_base64, isDAA, suspect_as_fraud, daa_reason_english
            nonlocal case_number, accident_date, converted_parties, claim_processor_file, current_model
            # ollama_url and ollama_model are module-level, accessible without nonlocal
            
            # Start timing for this party
            party_start_time = time.time()
            
            # Initialize all timing variables to avoid NameError if exception occurs
            config_reload_time = 0.0
            processor_call_time = 0.0
            validation_time = 0.0
            recovery_validation_time = 0.0
            additional_fields_time = 0.0
            
            try:
                insurance_type = "TP"
                
                transaction_logger.info(
                    f"TP_PARTY_START | Case: {case_number} | Party: {idx + 1} | "
                    f"Start_Time: {party_start_time} | Start_Datetime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}"
                )
                
                # Log party processing start with file and config details
                transaction_logger.info(
                    f"TP_PARTY_PROCESSING_START | Case: {case_number} | Party: {idx + 1} | "
                    f"TP_Module_File: {os.path.abspath(__file__)} | "
                    f"TP_Directory: {TP_DIR} | "
                    f"TP_Config_File: {tp_config_file} | "
                    f"TP_Processor_Type: {type(tp_processor).__name__} | "
                    f"TP_Processor_Module: {type(tp_processor).__module__} | "
                    f"TP_Processor_File: {os.path.abspath(CLAIM_PROCESSOR_FILE_PATH)} | "
                    f"Insurance_Type: {insurance_type} | "
                    f"Current_Working_Dir: {os.getcwd()}"
                )
                
                # Reload rules and log config details
                config_reload_start = time.time()
                tp_config_manager.reload_config()
                current_rules = tp_config_manager.get_rules()
                current_prompts = tp_config_manager.get_prompts()
                transaction_logger.info(
                    f"TP_CONFIG_LOADED | Case: {case_number} | Party: {idx + 1} | "
                    f"Config_File: {tp_config_manager.config_file} | "
                    f"Config_File_Exists: {os.path.exists(tp_config_manager.config_file)} | "
                    f"Rules_Count: {len(current_rules) if isinstance(current_rules, dict) else 'N/A'} | "
                    f"Prompts_Available: {list(current_prompts.keys()) if isinstance(current_prompts, dict) else 'N/A'} | "
                    f"Ollama_URL: {tp_processor.ollama_base_url} | "
                    f"Ollama_Model: {tp_processor.model_name} | "
                    f"Ollama_Translation_Model: {getattr(tp_processor, 'translation_model', 'N/A')}"
                )
                
                tp_processor.reload_rules()
                config_reload_time = time.time() - config_reload_start
                transaction_logger.info(
                    f"TP_TIMING_CONFIG_RELOAD | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {config_reload_time:.4f}"
                )
                
                # Process party claim
                processor_call_start = time.time()
                transaction_logger.info(
                    f"TP_CALLING_PROCESSOR | Case: {case_number} | Party: {idx + 1} | "
                    f"Processor_Method: process_party_claim | "
                    f"Processor_Class: {type(tp_processor).__name__} | "
                    f"Processor_Module: {type(tp_processor).__module__} | "
                    f"Processor_File: {os.path.abspath(type(tp_processor).__module__.replace('.', '/') + '.py') if hasattr(type(tp_processor), '__module__') else 'Unknown'}"
                )
                
                party_result = tp_processor.process_party_claim(
                    claim_data=claim_data,
                    party_info=party,
                    party_index=idx,
                    all_parties=converted_parties
                )
                
                processor_call_time = time.time() - processor_call_start
                transaction_logger.info(
                    f"TP_TIMING_PROCESSOR_CALL | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {processor_call_time:.4f} | "
                    f"Decision: {party_result.get('decision', 'N/A')}"
                )
                
                # Log Ollama response with full details
                transaction_logger.info(
                    f"TP_OLLAMA_RESPONSE_RECEIVED | Case: {case_number} | Party: {idx + 1} | "
                    f"Decision: {party_result.get('decision', 'N/A')} | "
                    f"Classification: {party_result.get('classification', 'N/A')} | "
                    f"Reasoning: {party_result.get('reasoning', '')[:500]} | "
                    f"Applied_Conditions: {party_result.get('applied_conditions', [])}"
                )
                
                # ========== APPLY VALIDATION LOGIC (SAME AS EXCEL) ==========
                # Excel applies validation rules AFTER getting decision from Ollama
                # This ensures Rule #3 and other rules are applied correctly
                
                validation_start = time.time()
                
                # Extract party info for validation
                current_liability = party.get("Liability", 0)
                current_insurance = str(party.get("Insurance_Name", "")).strip()
                insurance_info = party.get("Insurance_Info", {}) or party.get("insurance_info", {})
                current_ic_english = str(insurance_info.get("ICEnglishName", "")).strip()
                
                # Get decision from Ollama
                decision = party_result.get("decision", "ERROR")
                reasoning = party_result.get("reasoning", "")
                classification = party_result.get("classification", "UNKNOWN")
                
                transaction_logger.info(
                    f"TP_VALIDATION_START | Case: {case_number} | Party: {idx + 1} | "
                    f"Ollama_Decision: {decision} | Liability: {current_liability}% | "
                    f"Insurance_Name: {current_insurance} | ICEnglishName: {current_ic_english}"
                )
                
                # Helper function to check if insurance is Tawuniya (same as Excel)
                def is_tawuniya_insurance(insurance_name, ic_english_name):
                    """Check if insurance is Tawuniya (same logic as Excel)"""
                    if not insurance_name and not ic_english_name:
                        return False
                    
                    insurance_clean = str(insurance_name).strip().lower()
                    ic_english_clean = str(ic_english_name).strip().lower() if ic_english_name else ""
                    
                    # Check ICEnglishName first (most reliable)
                    if ic_english_clean:
                        if "tawuniya" in ic_english_clean and "cooperative" in ic_english_clean and "insurance" in ic_english_clean:
                            return True
                        if re.search(r'tawuniya\s*(?:c\b|co\b|coop|cooperative|insurance)', ic_english_clean):
                            return True
                    
                    # Check insurance name
                    if insurance_clean:
                        if "tawuniya" in insurance_clean and ("cooperative" in insurance_clean or "insurance" in insurance_clean):
                            return True
                        if "التعاونية" in insurance_name or "التعاونيه" in insurance_name:
                            return True
                    
                    return False
                
                # ========== VALIDATE TAWUNIYA POLICYHOLDER vs VEHICLE OWNER RULE ==========
                # NEW RULE: For Tawuniya parties, if Policyholder_ID exists and doesn't match VehicleOwnerId AND Liability > 0, then REJECT
                is_tawuniya = is_tawuniya_insurance(current_insurance, current_ic_english)
                if is_tawuniya:
                    # Get Policyholder_ID and VehicleOwnerId from party (converted_party structure)
                    # Check multiple field name variations
                    policyholder_id = (
                        str(party.get("Policyholder_ID", "")).strip() or
                        str(party.get("PolicyholderID", "")).strip() or
                        str(party.get("policyholder_id", "")).strip() or
                        str(insurance_info.get("policyNumber", "")).strip() or
                        ""
                    )
                    
                    vehicle_owner_id = (
                        str(party.get("VehicleOwnerId", "")).strip() or
                        str(party.get("vehicleOwnerId", "")).strip() or
                        str(party.get("vehicle_owner_id", "")).strip() or
                        ""
                    )
                    
                    # Check if Policyholder_ID exists and doesn't match VehicleOwnerId
                    if policyholder_id and policyholder_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"]:
                        if vehicle_owner_id and vehicle_owner_id.lower() not in ["", "none", "null", "nan", "not identify", "not identified"]:
                            # Normalize IDs for comparison (remove spaces, convert to string)
                            policyholder_id_normalized = str(policyholder_id).strip().replace(" ", "")
                            vehicle_owner_id_normalized = str(vehicle_owner_id).strip().replace(" ", "")
                            
                            # Check if they don't match AND Liability >= 50 (individual party check - global already handled above)
                            if policyholder_id_normalized != vehicle_owner_id_normalized and current_liability >= 50:
                                transaction_logger.warning(
                                    f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_MISMATCH | Case: {case_number} | Party: {idx + 1} | "
                                    f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                                    f"Policyholder_ID: {policyholder_id} | VehicleOwnerId: {vehicle_owner_id} | "
                                    f"Liability: {current_liability}% | "
                                    f"Reason: Tawuniya party - Policyholder_ID does not match VehicleOwnerId and Liability >= 50"
                                )
                                decision = "REJECTED"
                                reasoning = f"Tawuniya Validation: Policyholder_ID ({policyholder_id}) does not match VehicleOwnerId ({vehicle_owner_id}) and Liability ({current_liability}%) >= 50. {reasoning}" if reasoning else f"Tawuniya Validation: Policyholder_ID ({policyholder_id}) does not match VehicleOwnerId ({vehicle_owner_id}) and Liability ({current_liability}%) >= 50"
                                classification = "Policy Holder not same vehicle Owner"
                            else:
                                transaction_logger.info(
                                    f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                                    f"Policyholder_ID: {policyholder_id} | VehicleOwnerId: {vehicle_owner_id} | "
                                    f"Match: {policyholder_id_normalized == vehicle_owner_id_normalized} | "
                                    f"Liability: {current_liability}% | Rule_Not_Applied: {'IDs match' if policyholder_id_normalized == vehicle_owner_id_normalized else f'Liability is {current_liability}% (must be > 50%)'}"
                                )
                        else:
                            transaction_logger.info(
                                f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                                f"Policyholder_ID: {policyholder_id} | VehicleOwnerId: empty/missing | "
                                f"Rule_Not_Applied: VehicleOwnerId not available"
                            )
                    else:
                        transaction_logger.info(
                            f"TP_VALIDATION_TAWUNIYA_POLICYHOLDER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                            f"Policyholder_ID: empty/missing | Rule_Not_Applied: Policyholder_ID not available"
                        )
                
                # ========== VALIDATE 0% LIABILITY PARTY ==========
                if current_liability == 0 and decision == "REJECTED":
                    rejection_reason_lower = reasoning.lower() if reasoning else ""
                    classification_lower = classification.lower() if classification else ""
                    
                    # Check if rejection is only due to another party's 100% liability (incorrect)
                    if ("100%" in rejection_reason_lower or "100%" in classification_lower or 
                        "basic rule" in classification_lower or "rule #1" in classification_lower):
                        # Check if there's another party with 100% liability
                        has_other_100_percent = False
                        for other_idx, other_party in enumerate(converted_parties):
                            if other_idx != idx:
                                other_liab = other_party.get("Liability", 0)
                                if other_liab == 100:
                                    has_other_100_percent = True
                                    break
                        
                        if has_other_100_percent:
                            transaction_logger.warning(
                                f"TP_VALIDATION_0_PERCENT_CORRECTION | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: REJECTED | Corrected_Decision: ACCEPTED | "
                                f"Reason: 0% liability party should not be rejected when another party has 100% liability"
                            )
                            decision = "ACCEPTED"
                            reasoning = f"{reasoning} | CORRECTED: 0% liability party should not be rejected when another party has 100% liability" if reasoning else "CORRECTED: 0% liability party should not be rejected when another party has 100% liability"
                            classification = "Correction Rule: Victim party (0% liability) must be accepted"
                
                # ========== VALIDATE 100% LIABILITY RULE (RULE #1) ==========
                if current_liability == 100 and decision != "REJECTED":
                    transaction_logger.warning(
                        f"TP_VALIDATION_RULE_1 | Case: {case_number} | Party: {idx + 1} | "
                        f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                        f"Reason: Rule #1 - 100% liability MUST result in REJECTED for ALL companies"
                    )
                    decision = "REJECTED"
                    reasoning = f"Rule #1: 100% liability requires REJECTED for all companies. {reasoning}" if reasoning else "Rule #1: 100% liability requires REJECTED for all companies"
                    classification = "Basic Rule #1: 100% liability = REJECTED (all companies)"
                
                # ========== VALIDATE NON-COOPERATIVE INSURANCE RULE (RULE #3) ==========
                # CRITICAL: Rule #3 has HIGH PRIORITY - applies even if AI decision is REJECTED
                rule3_applied = False
                if current_liability != 100:  # Rule #3 doesn't apply to 100% liability
                    is_tawuniya = is_tawuniya_insurance(current_insurance, current_ic_english)
                    
                    transaction_logger.info(
                        f"TP_VALIDATION_RULE3_CHECK | Case: {case_number} | Party: {idx + 1} | "
                        f"Is_Tawuniya: {is_tawuniya} | Liability: {current_liability}% | "
                        f"Insurance_Name: {current_insurance} | ICEnglishName: {current_ic_english}"
                    )
                    
                    # Rule #3: Non-Tawuniya parties with 0%/25%/50%/75% liability → ACCEPTED
                    if not is_tawuniya and current_liability in [0, 25, 50, 75]:
                        if decision != "ACCEPTED" and decision != "ACCEPTED_WITH_RECOVERY":
                            transaction_logger.warning(
                                f"TP_VALIDATION_RULE3_APPLIED | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: {decision} | Corrected_Decision: ACCEPTED | "
                                f"Reason: Rule #3 (HIGH PRIORITY) - Non-Tawuniya party with {current_liability}% liability MUST be ACCEPTED"
                            )
                            decision = "ACCEPTED"
                            reasoning = f"Rule #3 (HIGH PRIORITY): Non-Tawuniya insurance party with {current_liability}% liability requires ACCEPTED. Overridden previous decision. {reasoning}" if reasoning else f"Rule #3 (HIGH PRIORITY): Non-Tawuniya insurance party with {current_liability}% liability requires ACCEPTED"
                            classification = f"Rule #3: Other insurance companies (non-Tawuniya) - {current_liability}% liability = ACCEPTED"
                            rule3_applied = True
                        else:
                            transaction_logger.info(
                                f"TP_VALIDATION_RULE3_ALREADY_CORRECT | Case: {case_number} | Party: {idx + 1} | "
                                f"Decision: {decision} | Rule #3 applies and decision is already correct"
                            )
                            rule3_applied = True
                
                # ========== VALIDATE GLOBAL RULE: 100% LIABILITY FROM NON-TAWUNIYA COMPANY ==========
                # If ANY party has 100% liability from non-Tawuniya, ALL parties must be REJECTED
                has_100_percent_non_tawuniya = False
                non_tawuniya_100_party_info = None
                for other_idx, other_party in enumerate(converted_parties):
                    if other_idx == idx:
                        continue
                    
                    other_liability = other_party.get("Liability", 0)
                    if other_liability == 100:
                        other_insurance = str(other_party.get("Insurance_Name", "")).strip()
                        other_ins_info = other_party.get("Insurance_Info", {}) or other_party.get("insurance_info", {})
                        other_ic_english = str(other_ins_info.get("ICEnglishName", "")).strip()
                        is_other_tawuniya = is_tawuniya_insurance(other_insurance, other_ic_english)
                        
                        if not is_other_tawuniya:
                            has_100_percent_non_tawuniya = True
                            non_tawuniya_100_party_info = {
                                "idx": other_idx,
                                "insurance": other_insurance,
                                "liability": other_liability
                            }
                            break
                
                if has_100_percent_non_tawuniya:
                    if decision != "REJECTED":
                        transaction_logger.warning(
                            f"TP_VALIDATION_GLOBAL_TAWUNIYA_RULE | Case: {case_number} | Party: {idx + 1} | "
                            f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                            f"Reason: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}) - ALL parties must be REJECTED"
                        )
                        if rule3_applied:
                            transaction_logger.warning(
                                f"TP_VALIDATION_RULE3_OVERRIDDEN | Case: {case_number} | Party: {idx + 1} | "
                                f"Rule #3 was applied but is OVERRIDDEN by Tawuniya Global Rule"
                            )
                        decision = "REJECTED"
                        reasoning = f"Tawuniya Global Rule OVERRIDES Rule #3: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}). All parties must be REJECTED. {reasoning}" if reasoning else f"Tawuniya Global Rule: Party {non_tawuniya_100_party_info['idx'] + 1} has 100% liability from non-Tawuniya company ({non_tawuniya_100_party_info['insurance']}). All parties must be REJECTED."
                        classification = "Tawuniya Rule: Reject all parties when there is a responsible party (100%) from a non-Tawuniya company"
                
                # ========== VALIDATE COOPERATIVE INSURANCE DECISION ==========
                # Only for Tawuniya parties with liability < 100%
                if is_tawuniya_insurance(current_insurance, current_ic_english) and current_liability < 100 and decision != "REJECTED" and not rule3_applied:
                    # Check if any party with liability > 0% is NOT Tawuniya
                    has_non_tawuniya_with_liability = False
                    for other_idx, other_party in enumerate(converted_parties):
                        if other_idx == idx:
                            continue
                        other_liability = other_party.get("Liability", 0)
                        if other_liability > 0:
                            other_insurance = str(other_party.get("Insurance_Name", "")).strip()
                            other_ins_info = other_party.get("Insurance_Info", {}) or other_party.get("insurance_info", {})
                            other_ic_english = str(other_ins_info.get("ICEnglishName", "")).strip()
                            is_other_tawuniya = is_tawuniya_insurance(other_insurance, other_ic_english)
                            if not is_other_tawuniya:
                                has_non_tawuniya_with_liability = True
                                break
                    
                    if has_non_tawuniya_with_liability:
                        if decision != "REJECTED":
                            transaction_logger.warning(
                                f"TP_VALIDATION_COOPERATIVE_RULE | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: {decision} | Corrected_Decision: REJECTED | "
                                f"Reason: Tawuniya party with {current_liability}% liability - another party with liability > 0% is NOT Tawuniya"
                            )
                            decision = "REJECTED"
                            reasoning = f"Cooperative Rule: Tawuniya party with {current_liability}% liability - another party with liability > 0% is NOT Tawuniya. {reasoning}" if reasoning else f"Cooperative Rule: Tawuniya party with {current_liability}% liability - another party with liability > 0% is NOT Tawuniya"
                            classification = "Cooperative Rule: Reject Tawuniya party when another party with liability > 0% is not Tawuniya"
                
                # ========== VALIDATE ACCEPTED_WITH_RECOVERY DECISION (SAME AS EXCEL) ==========
                recovery_validation_start = time.time()
                if decision == "ACCEPTED_WITH_RECOVERY":
                    is_tawuniya_party = is_tawuniya_insurance(current_insurance, current_ic_english)
                    
                    if not is_tawuniya_party:
                        transaction_logger.warning(
                            f"TP_VALIDATION_RECOVERY_NON_TAWUNIYA | Case: {case_number} | Party: {idx + 1} | "
                            f"Original_Decision: ACCEPTED_WITH_RECOVERY | Corrected_Decision: ACCEPTED | "
                            f"Reason: ACCEPTED_WITH_RECOVERY only applies to Tawuniya insured parties"
                        )
                        decision = "ACCEPTED"
                        reasoning = f"{reasoning} | VALIDATION: ACCEPTED_WITH_RECOVERY only for Tawuniya parties" if reasoning else "VALIDATION: ACCEPTED_WITH_RECOVERY only for Tawuniya parties"
                    else:
                        # Party is Tawuniya - validate using SAME logic as Excel unified_processor
                        validation_result = _validate_recovery_decision_api(
                            idx, party, converted_parties, accident_date, transaction_logger, case_number
                        )
                        if not validation_result["is_valid"]:
                            transaction_logger.warning(
                                f"TP_VALIDATION_RECOVERY_FAILED | Case: {case_number} | Party: {idx + 1} | "
                                f"Original_Decision: ACCEPTED_WITH_RECOVERY | Corrected_Decision: {validation_result['corrected_decision']} | "
                                f"Reason: {validation_result['reason']}"
                            )
                            decision = validation_result["corrected_decision"]
                            reasoning = f"{reasoning} | VALIDATION: {validation_result['reason']}" if reasoning else f"VALIDATION: {validation_result['reason']}"
                        else:
                            transaction_logger.info(
                                f"TP_VALIDATION_RECOVERY_PASSED | Case: {case_number} | Party: {idx + 1} | "
                                f"Recovery_Reasons: {validation_result.get('recovery_reasons', [])}"
                            )
                
                # ========== UPGRADE ACCEPTED TO ACCEPTED_WITH_RECOVERY (SAME AS EXCEL) ==========
                # Excel lines 5507-5802: Check if ACCEPTED decision should be upgraded to ACCEPTED_WITH_RECOVERY
                elif decision == "ACCEPTED":
                    # Only check upgrade if current party has liability < 100% (victim party)
                    if current_liability < 100:
                        is_tawuniya_party = is_tawuniya_insurance(current_insurance, current_ic_english)
                        
                        # Check if recovery conditions are met (SAME AS EXCEL lines 5608-5776)
                        should_validate_recovery = False
                        
                        # Check current party's Recovery field
                        current_recovery_field = str(party.get("Recovery", "")).strip()
                        current_recovery_field_upper = current_recovery_field.upper()
                        current_has_recovery_field = current_recovery_field_upper in ["TRUE", "1", "YES", "Y", "TRUE", "True"] or current_recovery_field in ["True", "true", "TRUE"]
                        
                        # Check current party's model_recovery (calculate on-the-fly)
                        current_license_type_make_model = str(party.get("License_Type_From_Make_Model", "")).strip()
                        current_license_type_request = str(party.get("License_Type_From_Request", "")).strip()
                        
                        # Normalize values
                        if current_license_type_make_model.lower() in ["none", "nan", "null"]:
                            current_license_type_make_model = ""
                        if current_license_type_request.lower() in ["none", "nan", "null"]:
                            current_license_type_request = ""
                        
                        # Check model_recovery condition (SAME AS EXCEL)
                        current_make_model_valid = (current_license_type_make_model and 
                                                   current_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                   current_license_type_make_model.upper() != "ANY LICENSE")
                        current_request_is_none_or_empty = (not current_license_type_request or 
                                                           current_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
                        current_request_mismatch = (current_license_type_request and 
                                                   current_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                   current_license_type_make_model.upper() != current_license_type_request.upper())
                        current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
                        
                        # Check other parties for recovery conditions
                        other_tawuniya_with_recovery = False
                        other_tawuniya_with_model_recovery = False
                        
                        for other_idx, other_party in enumerate(converted_parties):
                            if other_idx == idx:
                                continue
                            
                            other_liability = other_party.get("Liability", 0)
                            other_insurance = str(other_party.get("Insurance_Name", "")).strip()
                            insurance_info_other = other_party.get("Insurance_Info", {}) or other_party.get("insurance_info", {})
                            other_ic_english = str(insurance_info_other.get("ICEnglishName", "")).strip()
                            is_other_tawuniya = is_tawuniya_insurance(other_insurance, other_ic_english)
                            
                            if is_other_tawuniya and other_liability > 0:
                                # Check Recovery field
                                other_recovery = str(other_party.get("Recovery", "")).strip()
                                other_recovery_upper = other_recovery.upper()
                                if other_recovery_upper in ["TRUE", "1", "YES", "Y", "TRUE", "True"] or other_recovery in ["True", "true", "TRUE"]:
                                    other_tawuniya_with_recovery = True
                                
                                # Check model_recovery (SAME AS EXCEL)
                                other_license_type_make_model = str(other_party.get("License_Type_From_Make_Model", "")).strip()
                                other_license_type_request = str(other_party.get("License_Type_From_Request", "")).strip()
                                
                                if other_license_type_make_model.lower() in ["none", "nan", "null"]:
                                    other_license_type_make_model = ""
                                if other_license_type_request.lower() in ["none", "nan", "null"]:
                                    other_license_type_request = ""
                                
                                other_make_model_valid = (other_license_type_make_model and 
                                                         other_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                         other_license_type_make_model.upper() != "ANY LICENSE")
                                other_request_is_none_or_empty = (not other_license_type_request or 
                                                                 other_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
                                other_request_mismatch = (other_license_type_request and 
                                                         other_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                                                         other_license_type_make_model.upper() != other_license_type_request.upper())
                                other_has_model_recovery = other_make_model_valid and (other_request_is_none_or_empty or other_request_mismatch)
                                
                                if other_has_model_recovery:
                                    other_tawuniya_with_model_recovery = True
                            
                            if other_tawuniya_with_recovery and other_tawuniya_with_model_recovery:
                                break
                        
                        # Determine if recovery validation should proceed (SAME AS EXCEL lines 5740-5776)
                        if is_tawuniya_party:
                            # Current party is Tawuniya - check if liability > 0 AND (Recovery=True OR model_recovery=True)
                            if current_liability > 0 and (current_has_recovery_field or current_has_model_recovery):
                                should_validate_recovery = True
                            # Also check if another Tawuniya party has Recovery=True/TRUE/true with liability > 0
                            elif current_liability > 0 and other_tawuniya_with_recovery:
                                should_validate_recovery = True
                            # Also check if another Tawuniya party has model_recovery=True/TRUE/true with liability > 0
                            elif current_liability > 0 and other_tawuniya_with_model_recovery:
                                should_validate_recovery = True
                        else:
                            # Non-Tawuniya party - Check exception: if another Tawuniya party has Recovery=True/TRUE/true with liability > 0
                            if current_liability < 100 and other_tawuniya_with_recovery:
                                should_validate_recovery = True
                            elif current_liability < 100 and other_tawuniya_with_model_recovery:
                                should_validate_recovery = True
                        
                        # If recovery conditions are met, validate and upgrade
                        if should_validate_recovery:
                            transaction_logger.info(
                                f"TP_RECOVERY_UPGRADE_CHECK | Case: {case_number} | Party: {idx + 1} | "
                                f"Decision: ACCEPTED | Checking recovery conditions for upgrade to ACCEPTED_WITH_RECOVERY | "
                                f"Current_Has_Recovery: {current_has_recovery_field} | Current_Has_Model_Recovery: {current_has_model_recovery} | "
                                f"Other_Tawuniya_With_Recovery: {other_tawuniya_with_recovery} | Other_Tawuniya_With_Model_Recovery: {other_tawuniya_with_model_recovery}"
                            )
                            
                            # Validate recovery conditions (SAME AS EXCEL)
                            validation_result = _validate_recovery_decision_api(
                                idx, party, converted_parties, accident_date, transaction_logger, case_number
                            )
                            
                            if validation_result["is_valid"]:
                                # Upgrade ACCEPTED to ACCEPTED_WITH_RECOVERY
                                transaction_logger.info(
                                    f"TP_RECOVERY_UPGRADE_APPLIED | Case: {case_number} | Party: {idx + 1} | "
                                    f"Original_Decision: ACCEPTED | Upgraded_Decision: ACCEPTED_WITH_RECOVERY | "
                                    f"Recovery_Reasons: {validation_result.get('recovery_reasons', [])}"
                                )
                                decision = "ACCEPTED_WITH_RECOVERY"
                                reasoning = f"{reasoning} | RECOVERY UPGRADE: {validation_result['reason']}" if reasoning else f"RECOVERY UPGRADE: {validation_result['reason']}"
                                classification = "Recovery conditions met - upgraded from ACCEPTED to ACCEPTED_WITH_RECOVERY"
                            else:
                                transaction_logger.info(
                                    f"TP_RECOVERY_UPGRADE_SKIPPED | Case: {case_number} | Party: {idx + 1} | "
                                    f"Decision: ACCEPTED | Recovery conditions not met, keeping ACCEPTED | "
                                    f"Reason: {validation_result.get('reason', 'N/A')}"
                                )
                
                recovery_validation_time = time.time() - recovery_validation_start
                transaction_logger.info(
                    f"TP_TIMING_RECOVERY_VALIDATION | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {recovery_validation_time:.4f}"
                )
                
                # Log final validation result
                validation_time = time.time() - validation_start
                transaction_logger.info(
                    f"TP_VALIDATION_COMPLETE | Case: {case_number} | Party: {idx + 1} | "
                    f"Final_Decision: {decision} | Original_Decision: {party_result.get('decision', 'N/A')} | "
                    f"Rule3_Applied: {rule3_applied} | Liability: {current_liability}% | "
                    f"Insurance: {current_insurance} | Is_Tawuniya: {is_tawuniya_insurance(current_insurance, current_ic_english)} | "
                    f"Validation_Time_Seconds: {validation_time:.4f}"
                )
                
                # Update party_result with validated decision
                party_result["decision"] = decision
                party_result["reasoning"] = reasoning
                party_result["classification"] = classification
                
                transaction_logger.info(
                    f"TP_PROCESSOR_RESPONSE | Case: {case_number} | Party: {idx + 1} | "
                    f"Decision: {party_result.get('decision', 'N/A')} | "
                    f"Classification: {party_result.get('classification', 'N/A')} | "
                    f"Reasoning_Length: {len(str(party_result.get('reasoning', '')))}"
                )
                
                # Calculate additional fields
                additional_fields_start = time.time()
                additional_fields = calculate_additional_fields(party, isDAA, insurance_type)
                additional_fields_time = time.time() - additional_fields_start
                transaction_logger.info(
                    f"TP_TIMING_ADDITIONAL_FIELDS | Case: {case_number} | Party: {idx + 1} | "
                    f"Time_Seconds: {additional_fields_time:.4f}"
                )
                
                # Build response
                base_response = {
                    "_index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Party_Name": party.get("name", ""),
                    "Liability": party.get("Liability", 0),
                    "Policyholder_ID": party.get("Policyholder_ID", ""),
                    "Policyholdername": party.get("Policyholdername", party.get("Policyholder_Name", "")),  # NEW: Policyholder name
                    "Decision": party_result.get("decision", "ERROR"),
                    "Classification": party_result.get("classification", "UNKNOWN"),
                    "Reasoning": party_result.get("reasoning", ""),
                    "Applied_Conditions": party_result.get("applied_conditions", []),
                    "isDAA": isDAA,
                    "Suspect_as_Fraud": suspect_as_fraud,
                    "DaaReasonEnglish": daa_reason_english,
                    "Policyholder_ID": party.get("Policyholder_ID", ""),
                    "Suspected_Fraud": additional_fields.get("Suspected_Fraud"),
                    "model_recovery": additional_fields.get("model_recovery"),
                    "License_Type_From_Make_Model": additional_fields.get("License_Type_From_Make_Model"),
                    "insurance_type": insurance_type
                }
                
                # Filter response fields based on config
                tp_config_manager.reload_config()
                response_fields_config = tp_config_manager.get_config().get("response_fields", {}).get("enabled_fields", {})
                
                transaction_logger.info(
                    f"TP_RESPONSE_FIELDS_CONFIG | Case: {case_number} | Party: {idx + 1} | "
                    f"Config_File: {tp_config_manager.config_file} | "
                    f"Config_File_Path: {os.path.abspath(tp_config_manager.config_file)} | "
                    f"Enabled_Fields: {list(response_fields_config.keys()) if isinstance(response_fields_config, dict) else 'N/A'} | "
                    f"Total_Fields: {len(response_fields_config) if isinstance(response_fields_config, dict) else 0}"
                )
                
                filtered_response = {}
                for field_name, field_value in base_response.items():
                    if field_name == "_index":
                        filtered_response["_index"] = field_value
                        continue
                    if response_fields_config.get(field_name, True):
                        filtered_response[field_name] = field_value
                
                # Log party completion timing
                # Calculate total time (party_start_time is defined before try block, so it's always accessible)
                party_total_time = time.time() - party_start_time
                
                # Calculate Ollama time percentage
                ollama_percentage = (processor_call_time / party_total_time * 100) if party_total_time > 0 else 0.0
                
                transaction_logger.info(
                    f"TP_PARTY_COMPLETE | Case: {case_number} | Party: {idx + 1} | "
                    f"Total_Time_Seconds: {party_total_time:.4f} | "
                    f"End_Time: {time.time()} | End_Datetime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')} | "
                    f"Time_Breakdown: Config_Reload={config_reload_time:.4f}, Processor_Call={processor_call_time:.4f} ({ollama_percentage:.1f}%), "
                    f"Validation={validation_time:.4f}, Recovery_Validation={recovery_validation_time:.4f}, "
                    f"Additional_Fields={additional_fields_time:.4f} | "
                    f"Model: {current_model}"
                )
                
                # Add processing time to response for performance tracking
                filtered_response['_processing_time'] = party_total_time
                
                return filtered_response
                
            except Exception as e:
                error_msg = str(e)
                
                # Log error with timing if party_start_time is available
                try:
                    if 'party_start_time' in locals():
                        error_time = time.time() - party_start_time
                        transaction_logger.error(
                            f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
                            f"Error_Type: {type(e).__name__} | Error_Time_Seconds: {error_time:.4f}"
                        )
                    else:
                        transaction_logger.error(
                            f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
                            f"Error_Type: {type(e).__name__} | Note: party_start_time not available"
                        )
                except:
                    transaction_logger.error(
                        f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
                        f"Error_Type: {type(e).__name__}"
                    )
                
                # Check if it's an Ollama connection error
                if "404" in error_msg or "Not Found" in error_msg or "Failed to connect" in error_msg:
                    reasoning = f"Ollama service error: {error_msg}. Please ensure Ollama is running on {ollama_url} and the model '{ollama_model}' is available."
                else:
                    reasoning = f"Error processing party: {error_msg}"
                
                return {
                    "_index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Reasoning": reasoning,
                    "Applied_Conditions": []
                }
        
        def calculate_additional_fields(party_data, is_daa_value, insurance_type):
            """
            Calculate additional fields using TP unified processor
            EXACTLY matches Excel unified_processor logic for 100% accuracy
            Uses the SAME conditions and logic as unified_processor._validate_recovery_decision
            """
            additional = {}
            
            # License_Type_From_Make_Model already added to party_data before processing (Excel match)
            # Just retrieve it - no need to lookup again
            license_type_from_make_model = str(party_data.get("License_Type_From_Make_Model", "")).strip()
            additional["License_Type_From_Make_Model"] = license_type_from_make_model
            
            # Suspected_Fraud calculation (EXACT Excel unified_processor logic from lines 7517-7546)
            # Excel uses: isDAA_series.isin(['TRUE', '1', 'YES', 'Y', 'T'])
            suspected_fraud = None
            if is_daa_value is not None:
                # Convert to string and normalize (EXACT Excel logic)
                is_daa_str = str(is_daa_value).strip().upper()
                # Excel checks: isDAA_series.isin(['TRUE', '1', 'YES', 'Y', 'T'])
                if is_daa_str in ['TRUE', '1', 'YES', 'Y', 'T']:
                    suspected_fraud = "Suspected Fraud"
            # Excel sets to None if isDAA is NaN/None
            additional["Suspected_Fraud"] = suspected_fraud
            
            # model_recovery calculation (EXACT Excel unified_processor logic from lines 640-660)
            # This matches unified_processor._validate_recovery_decision model_recovery calculation
            license_type_from_request = str(party_data.get("licenseType", "") or party_data.get("License_Type_From_Najm", "")).strip()
            
            # Normalize values (EXACT Excel logic - lines 645-648)
            if license_type_from_make_model.lower() in ["none", "nan", "null"]:
                license_type_from_make_model = ""
            if license_type_from_request.lower() in ["none", "nan", "null"]:
                license_type_from_request = ""
            
            # Check model_recovery condition (EXACT Excel logic - lines 650-659)
            # Excel checks:
            # 1. current_make_model_valid = (current_license_type_make_model and 
            #    current_license_type_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
            #    current_license_type_make_model.upper() != "ANY LICENSE")
            make_model_valid = (license_type_from_make_model and 
                               license_type_from_make_model.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               license_type_from_make_model.upper() != "ANY LICENSE")
            
            # 2. current_request_is_none_or_empty = (not current_license_type_request or 
            #    current_license_type_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
            request_is_none_or_empty = (not license_type_from_request or 
                                       license_type_from_request.lower() in ["not identify", "not identified", "", "none", "nan", "null"])
            
            # 3. current_request_mismatch = (current_license_type_request and 
            #    current_license_type_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
            #    current_license_type_make_model.upper() != current_license_type_request.upper())
            request_mismatch = (license_type_from_request and 
                               license_type_from_request.lower() not in ["not identify", "not identified", "", "none", "nan", "null"] and
                               license_type_from_make_model.upper() != license_type_from_request.upper())
            
            # 4. current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
            # Excel line 659: current_has_model_recovery = current_make_model_valid and (current_request_is_none_or_empty or current_request_mismatch)
            has_model_recovery = make_model_valid and (request_is_none_or_empty or request_mismatch)
            model_recovery = has_model_recovery
            
            if model_recovery:
                transaction_logger.info(
                    f"TP_MODEL_RECOVERY_DETECTED | Case: {case_number} | "
                    f"License_Type_From_Make_Model: {license_type_from_make_model} | "
                    f"License_Type_From_Request: {license_type_from_request} | "
                    f"Make_Model_Valid: {make_model_valid} | "
                    f"Request_Is_None_Or_Empty: {request_is_none_or_empty} | "
                    f"Request_Mismatch: {request_mismatch}"
                )
            
            additional["model_recovery"] = model_recovery
            
            return additional
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_party = {
                executor.submit(process_single_party, idx, party): (idx, party)
                for idx, party in enumerate(converted_parties)
            }
            
            completed_results = {}
            for future in as_completed(future_to_party):
                try:
                    result = future.result()
                    result_index = result.get("_index", 0)
                    # Remove _index from result before storing
                    if "_index" in result:
                        del result["_index"]
                    completed_results[result_index] = result
                except Exception as e:
                    idx, party = future_to_party[future]
                    transaction_logger.error(
                        f"TP_PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {str(e)[:200]}"
                    )
                    # Add error result to maintain order
                    completed_results[idx] = {
                        "Party": party.get("Party", f"Party {idx + 1}"),
                        "Party_ID": party.get("ID", ""),
                        "Decision": "ERROR",
                        "Classification": "ERROR",
                        "Reasoning": f"Error processing party: {str(e)[:200]}",
                        "Applied_Conditions": []
                    }
        
        # Sort results by index (use keys since _index was removed from values)
        filtered_results = [completed_results[i] for i in sorted(completed_results.keys())]
        
        parallel_processing_time = time.time() - parallel_processing_start
        total_processing_time = (datetime.now() - processing_start_time).total_seconds()
        transaction_logger.info(
            f"TP_PARALLEL_PROCESSING_COMPLETE | Case: {case_number} | "
            f"Parties_Processed: {len(filtered_results)} | "
            f"Parallel_Processing_Time_Seconds: {parallel_processing_time:.4f} | "
            f"Total_Processing_Time_Seconds: {total_processing_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s | "
            f"TP_Module_File: {os.path.abspath(__file__)} | "
            f"TP_Directory: {TP_DIR}"
        )
        
        # Build response
        response_build_start = time.time()
        response_data = {
            "Case_Number": case_number,
            "Accident_Date": accident_date,
            "Upload_Date": upload_date,
            "Claim_requester_ID": claim_requester_id,
            "Status": "Success",
            "Parties": filtered_results,
            "Total_Parties": len(data["Parties"]),
            "Parties_Processed": len(filtered_results),
            "LD_Rep_64bit_Received": bool(ld_rep_base64)
        }
        response_build_time = time.time() - response_build_start
        transaction_logger.info(
            f"TP_RESPONSE_BUILT | Case: {case_number} | "
            f"Response_Build_Time_Seconds: {response_build_time:.4f} | "
            f"Time_From_Start: {time.time() - request_start_time:.4f}s"
        )
        
        # Calculate total request time
        request_total_time = time.time() - request_start_time
        request_end_datetime = datetime.now()
        
        # Calculate timing breakdown (use 0.0 if variable not defined)
        data_extraction_time_final = data_extraction_time if 'data_extraction_time' in locals() else 0.0
        ocr_processing_time_final = ocr_processing_time if 'ocr_processing_time' in locals() else 0.0
        
        transaction_logger.info(
            f"TP_CLAIM_PROCESSING_COMPLETE | Case: {case_number} | "
            f"TP_Module_File: {os.path.abspath(__file__)} | "
            f"TP_Config_File: {tp_config_file} | "
            f"TP_Config_File_Path: {os.path.abspath(tp_config_file)} | "
            f"TP_Directory: {TP_DIR} | "
            f"TP_Processor_Type: {type(tp_processor).__name__} | "
            f"TP_Processor_Module: {type(tp_processor).__module__} | "
            f"Total_Parties: {len(data.get('Parties', []))} | "
            f"Parties_Processed: {len(filtered_results)} | "
            f"Processing_Location: TP_PATH | "
            f"Current_Working_Dir: {os.getcwd()}"
        )
        
        # Calculate performance metrics
        avg_party_time = parallel_processing_time / len(filtered_results) if filtered_results else 0.0
        
        # Performance comparison (expected improvement with qwen2.5:1.5b)
        expected_old_time = 110.0 * len(filtered_results)  # Old model: ~110s per party
        speed_improvement = (expected_old_time / request_total_time) if request_total_time > 0 else 0.0
        
        transaction_logger.info(
            f"TP_REQUEST_COMPLETE | Case: {case_number} | "
            f"Total_Request_Time_Seconds: {request_total_time:.4f} | "
            f"Start_Time: {request_start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')} | "
            f"End_Time: {request_end_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')} | "
            f"Time_Breakdown: Data_Cleaning={data_cleaning_time:.4f}s, Data_Extraction={data_extraction_time_final:.4f}s, "
            f"DAA_Extraction={daa_extraction_time:.4f}s, OCR_Processing={ocr_processing_time_final:.4f}s, "
            f"Accident_Info={accident_info_time:.4f}s, Party_Conversion={party_conversion_time:.4f}s, "
            f"Claim_Data_Build={claim_data_build_time:.4f}s, Parallel_Processing={parallel_processing_time:.4f}s, "
            f"Response_Build={response_build_time:.4f}s | "
            f"Parallel_Processing_Time: {total_processing_time:.4f}s | "
            f"Parties_Count: {len(filtered_results)} | "
            f"Average_Party_Time: {avg_party_time:.4f}s | "
            f"Model: {current_model} | "
            f"Performance_Improvement: {speed_improvement:.2f}x faster vs qwen2.5:3b (expected)"
        )
        
        # Performance summary log
        ollama_time_percentage = (parallel_processing_time / request_total_time * 100) if request_total_time > 0 else 0.0
        transaction_logger.info(
            f"TP_PERFORMANCE_SUMMARY | Case: {case_number} | "
            f"Model: {current_model} | "
            f"Total_Time: {request_total_time:.2f}s | "
            f"Parties: {len(filtered_results)} | "
            f"Avg_Party_Time: {avg_party_time:.2f}s | "
            f"Ollama_Time_Percentage: {ollama_time_percentage:.1f}% | "
            f"Speed_vs_Old_Model: {speed_improvement:.2f}x | "
            f"Expected_Old_Time: {expected_old_time:.2f}s | "
            f"Time_Saved: {expected_old_time - request_total_time:.2f}s"
        )
        
        return jsonify(response_data), 200
        
    except Exception as e:
        error_msg = str(e)
        transaction_logger.error(
            f"TP_CLAIM_PROCESSING_ERROR | Error: {error_msg} | "
            f"Traceback: {traceback.format_exc()[:2000]}"
        )
        return jsonify({"error": error_msg}), 500

