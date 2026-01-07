"""
CO Claim Processing API Module
All CO claim processing logic is contained in this module.
Called from unified_api_server.py main router.
"""

import os
import json
import base64
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
from flask import jsonify

# Import CO-specific modules
from claim_processor import ClaimProcessor
from excel_ocr_license_processor import ExcelOCRLicenseProcessor
from unified_processor import UnifiedClaimProcessor
from config_manager import ConfigManager

# Get CO directory
CO_DIR = os.path.dirname(os.path.abspath(__file__))

# Setup transaction logger for CO
# BASE_DIR should be the parent of CO_DIR (Motorclaimdecision_main)
BASE_DIR = os.path.dirname(CO_DIR)
LOG_DIR = os.path.join(BASE_DIR, "logs")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except PermissionError:
    # Fallback to CO directory if main logs directory not accessible
    LOG_DIR = os.path.join(CO_DIR, "logs")
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception as e:
    # If all else fails, use CO directory
    LOG_DIR = CO_DIR

# Daily transaction log file for CO
# Use "transaction_co" to match api_server.py and claim_processor.py
def get_transaction_logger():
    """Get or create transaction logger for CO"""
    logger_name = "transaction_co"  # Changed to match api_server.py
    if logger_name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        # Ensure it has handlers
        if logger.handlers:
            return logger
    
    transaction_logger = logging.getLogger(logger_name)
    transaction_logger.setLevel(logging.INFO)
    transaction_logger.propagate = False
    
    # Daily rotating log file
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_file = os.path.join(LOG_DIR, f"api_transactions_co_{current_date}.log")
    
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

# Initialize CO processors
co_config_file = os.path.join(CO_DIR, "claim_config.json")
co_config_file_abs = os.path.abspath(co_config_file)

# Log config file initialization
transaction_logger.info(
    f"CO_CONFIG_INITIALIZATION | "
    f"CO_Directory: {CO_DIR} | "
    f"Config_File_Path: {co_config_file_abs} | "
    f"Config_File_Exists: {os.path.exists(co_config_file)} | "
    f"Config_File_Real_Path: {os.path.realpath(co_config_file) if os.path.exists(co_config_file) else 'N/A'}"
)

co_config_manager = ConfigManager(config_file=co_config_file)

# Get Ollama configuration from config file or use defaults
co_config_manager.reload_config()
co_config = co_config_manager.get_config()

# Log config loaded details
transaction_logger.info(
    f"CO_CONFIG_LOADED | "
    f"Config_File: {co_config_file_abs} | "
    f"Config_Manager_File: {co_config_manager.config_file} | "
    f"Config_Manager_File_Abs: {os.path.abspath(co_config_manager.config_file)} | "
    f"Files_Match: {os.path.abspath(co_config_manager.config_file) == co_config_file_abs}"
)

# Check what path claim_processor.py will use (global config_manager)
try:
    from config_manager import config_manager as global_config_manager
    global_config_file = getattr(global_config_manager, 'config_file', 'UNKNOWN')
    global_config_file_abs = os.path.abspath(global_config_file) if global_config_file != 'UNKNOWN' else 'UNKNOWN'
    transaction_logger.info(
        f"CO_CONFIG_PATH_COMPARISON | "
        f"API_Config_File: {co_config_file_abs} | "
        f"Processor_Global_Config_File: {global_config_file_abs} | "
        f"Paths_Match: {co_config_file_abs == global_config_file_abs} | "
        f"⚠️_WARNING: If paths don't match, claim_processor.py may read from different file!"
    )
except Exception as e:
    transaction_logger.warning(
        f"CO_CONFIG_PATH_CHECK_FAILED | "
        f"Error: {str(e)[:200]}"
    )
ollama_config = co_config.get("ollama", {})
ollama_url = ollama_config.get("base_url", os.getenv("OLLAMA_URL", "http://localhost:11434"))
ollama_model = ollama_config.get("model_name", os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))
ollama_translation_model = ollama_config.get("translation_model", os.getenv("OLLAMA_TRANSLATION_MODEL", "llama3.2:latest"))

# Initialize processors with Ollama configuration
co_processor = ClaimProcessor(
    ollama_base_url=ollama_url,
    model_name=ollama_model,
    translation_model=ollama_translation_model,
    check_ollama_health=False,  # Don't check on import to avoid blocking
    prewarm_model=False  # Don't prewarm on import
)
co_ocr_license_processor = ExcelOCRLicenseProcessor()
co_unified_processor = UnifiedClaimProcessor(
    ollama_base_url=ollama_url,
    model_name=ollama_model,
    translation_model=ollama_translation_model
)


def process_co_claim(data):
    """
    Process CO claim - ALL functionality from CO path (MotorclaimdecisionlinuxCO/)
    
    CONFIG FILE TRACING:
    - This function uses: co_config_manager (from claim_processor_api.py)
    - Config file path: co_config_file = os.path.join(CO_DIR, "claim_config.json")
    - claim_processor.py uses: global config_manager (from config_manager.py)
    - Both should use the same file path for consistency
    
    This is the main entry point for CO claim processing.
    All processing logic is contained within this CO directory.
    
    Args:
        data: Request JSON data containing claim information
        
    Returns:
        Flask response with processed claim results
    """
    try:
        case_number = data.get("Case_Number", "")
        
        # Verify we're using CO processors and config
        co_config_manager.reload_config()
        current_config_file = co_config_manager.config_file
        transaction_logger.info(
            f"CO_CLAIM_PROCESSING_START | Case: {case_number} | "
            f"CO_Directory: {CO_DIR} | "
            f"CO_Config_File: {co_config_file} | "
            f"Current_Config_File: {current_config_file} | "
            f"Config_Match: {co_config_file == current_config_file} | "
            f"CO_Processor_Type: {type(co_processor).__name__} | "
            f"CO_Processor_Module: {type(co_processor).__module__}"
        )
        
        # Verify config file is correct
        if co_config_file != current_config_file:
            error_msg = f"CO Config file mismatch! Expected: {co_config_file}, Got: {current_config_file}"
            transaction_logger.error(f"CO_CONFIG_ERROR | {error_msg}")
            return jsonify({"error": error_msg}), 500
        
        # Extract request data
        accident_date = data.get("Accident_Date", "")
        upload_date = data.get("Upload_Date", "")
        claim_requester_id = data.get("Claim_requester_ID", None)
        accident_description = data.get("accident_description", "")
        ld_rep_base64 = data.get("Name_LD_rep_64bit", "")
        
        # Extract DAA parameters
        isDAA = data.get("isDAA", None)
        suspect_as_fraud = data.get("Suspect_as_Fraud", None)
        daa_reason_english = data.get("DaaReasonEnglish", None)
        
        # Process OCR
        ocr_text = None
        ocr_processing_result = {"status": "no_image", "text_length": 0, "error": None}
        
        if ld_rep_base64:
            try:
                transaction_logger.info(
                    f"BASE64_PROCESSING_START | Case: {case_number} | Base64_Length: {len(ld_rep_base64)}"
                )
                
                # Determine base64 format
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
                        ocr_processing_result = {"status": "success", "text_length": len(ocr_text), "error": None}
                        transaction_logger.info(
                            f"OCR_TEXT_EXTRACTED | Case: {case_number} | Text_Length: {len(ocr_text)}"
                        )
                    else:
                        ocr_processing_result = {"status": "image_detected", "text_length": 0, "error": "Image detected"}
                except Exception as decode_error:
                    ocr_processing_result = {"status": "image_detected", "text_length": 0, "error": "Image detected"}
            except Exception as e:
                error_msg = f"Error processing base64: {str(e)[:100]}"
                ocr_processing_result = {"status": "error", "text_length": 0, "error": error_msg}
                transaction_logger.error(f"BASE64_PROCESSING_ERROR | Case: {case_number} | Error: {error_msg}")
        else:
            transaction_logger.info(f"BASE64_NOT_PROVIDED | Case: {case_number}")
        
        # Translate OCR text to English (SAME AS TP)
        ocr_text_translated = None
        if ocr_text:
            try:
                import re
                has_arabic = bool(re.search(r'[\u0600-\u06FF]', ocr_text) if ocr_text else False)
                transaction_logger.info(
                    f"CO_OCR_TRANSLATION_START | Case: {case_number} | "
                    f"OCR_Text_Length: {len(ocr_text) if ocr_text else 0} | "
                    f"Has_Arabic: {has_arabic}"
                )
                
                if has_arabic:
                    ocr_text_translated = co_unified_processor.translate_ocr_to_english(ocr_text)
                    transaction_logger.info(
                        f"CO_OCR_TRANSLATION_SUCCESS | Case: {case_number} | "
                        f"Translated_Length: {len(ocr_text_translated) if ocr_text_translated else 0}"
                    )
                else:
                    ocr_text_translated = ocr_text
                    transaction_logger.info(
                        f"CO_OCR_TRANSLATION_SKIPPED | Case: {case_number} | "
                        f"Reason: No_Arabic_Text_Detected"
                    )
            except Exception as e:
                transaction_logger.error(
                    f"CO_OCR_TRANSLATION_ERROR | Case: {case_number} | Error: {str(e)[:200]} | "
                    f"Falling_Back_To_Original"
                )
                ocr_text_translated = ocr_text  # Fallback to original
        
        # Process OCR with CO OCR processor (use translated text if available)
        if ocr_text_translated or ocr_text:
            try:
                data = co_ocr_license_processor.process_claim_data_with_ocr(
                    claim_data=data,
                    ocr_text=ocr_text_translated if ocr_text_translated else ocr_text,
                    base64_image=ld_rep_base64 if not ocr_text else None
                )
                transaction_logger.info(f"CO_OCR_VALIDATION_SUCCESS | Case: {case_number}")
            except Exception as e:
                transaction_logger.error(f"CO_OCR_VALIDATION_ERROR | Case: {case_number} | Error: {str(e)[:200]}")
        
        # Build accident info
        if accident_description:
            accident_desc = accident_description
        else:
            accident_desc = f"Case: {case_number}, Date: {accident_date}"
        
        accident_info = {
            "caseNumber": case_number,
            "AccidentDescription": accident_desc,
            "callDate": accident_date,
            "Accident_description": accident_desc,
            "Upload_Date": upload_date,
            "Claim_requester_ID": claim_requester_id,
            "Name_LD_rep_64bit": ld_rep_base64,
            "isDAA": isDAA,
            "Suspect_as_Fraud": suspect_as_fraud,
            "DaaReasonEnglish": daa_reason_english
        }
        
        # Check if CO should only process Tawuniya parties
        only_process_tawuniya = False
        tawuniya_insurance_names = ["Tawuniya Cooperative Insurance Company", "التعاونية للتأمين"]
        
        try:
            co_config_manager.reload_config()
            co_config = co_config_manager.get_config()
            processing_filters = co_config.get("processing_filters", {})
            only_tawuniya_filter = processing_filters.get("only_process_tawuniya", {})
            only_process_tawuniya = only_tawuniya_filter.get("enabled", False)
            
            if only_process_tawuniya:
                configured_name = only_tawuniya_filter.get("insurance_name_match", "Tawuniya Cooperative Insurance Company")
                configured_name_arabic = only_tawuniya_filter.get("insurance_name_match_arabic", "التعاونية للتأمين")
                tawuniya_insurance_names = [configured_name, configured_name_arabic]
                
                transaction_logger.info(
                    f"CO_TAWUNIYA_FILTER_ENABLED | Case: {case_number} | "
                    f"Only_Processing_Tawuniya: True | Match_Names: {', '.join(tawuniya_insurance_names)}"
                )
        except Exception as e:
            transaction_logger.error(f"CO_FILTER_CONFIG_ERROR | Case: {case_number} | Error: {str(e)[:200]}")
        
        # Convert parties and apply Tawuniya filter
        converted_parties = []
        skipped_parties = []
        
        for idx, party in enumerate(data["Parties"]):
            # Use claim_type "CO" as insurance_type for internal processing (response building, etc.)
            # BUT: insurance_type sent to Ollama should be empty (assume comprehensive) - handled in claim_processor.py
            claim_type = "CO"  # This is the claim type (CO = Comprehensive)
            insurance_type = claim_type  # Use internally for response building
            
            # Apply Tawuniya filter - STRICT FILTERING (ENHANCED - SAME AS TP)
            if only_process_tawuniya:
                insurance_name = party.get("Insurance_Name", "").strip()
                is_tawuniya = False
                insurance_name_lower = insurance_name.lower() if insurance_name else ""
                
                transaction_logger.info(
                    f"TAWUNIYA_FILTER_CHECK | Case: {case_number} | Party: {idx + 1} | "
                    f"Party_ID: {party.get('Party_ID', 'Unknown')} | Insurance_Name: '{insurance_name}' | "
                    f"Insurance_Name_Lower: '{insurance_name_lower}'"
                )
                
                # Enhanced Tawuniya detection (SAME AS TP) - More robust matching
                # Check 1: Direct lowercase match for "tawuniya"
                if insurance_name_lower and "tawuniya" in insurance_name_lower:
                    is_tawuniya = True
                    transaction_logger.info(
                        f"TAWUNIYA_MATCH | Case: {case_number} | Party: {idx + 1} | "
                        f"Match_Type: Contains_'tawuniya' | Insurance_Name: '{insurance_name}'"
                    )
                # Check 2: Check for "cooperative insurance company" (might be Tawuniya)
                elif insurance_name_lower and "cooperative insurance company" in insurance_name_lower:
                    is_tawuniya = True
                    transaction_logger.info(
                        f"TAWUNIYA_MATCH | Case: {case_number} | Party: {idx + 1} | "
                        f"Match_Type: Contains_'cooperative_insurance_company' | Insurance_Name: '{insurance_name}'"
                    )
                # Check 3: Arabic name check
                elif insurance_name and ("التعاونية" in insurance_name or "التعاونية للتأمين" in insurance_name):
                    is_tawuniya = True
                    transaction_logger.info(
                        f"TAWUNIYA_MATCH | Case: {case_number} | Party: {idx + 1} | "
                        f"Match_Type: Contains_Arabic_Cooperative | Insurance_Name: '{insurance_name}'"
                    )
                # Check 4: Match against configured names (exact or partial match)
                elif insurance_name_lower:
                    for tawuniya_name in tawuniya_insurance_names:
                        if not tawuniya_name:
                            continue
                        tawuniya_lower = tawuniya_name.lower().strip()
                        # More flexible matching: check if either string contains the other
                        if (tawuniya_lower in insurance_name_lower or 
                            insurance_name_lower in tawuniya_lower or
                            insurance_name_lower == tawuniya_lower):
                            is_tawuniya = True
                            transaction_logger.info(
                                f"TAWUNIYA_MATCH | Case: {case_number} | Party: {idx + 1} | "
                                f"Match_Type: Configured_Name_Match | "
                                f"Insurance_Name: '{insurance_name}' | Configured_Name: '{tawuniya_name}'"
                            )
                            break
                
                if not is_tawuniya:
                    skipped_parties.append({
                        "index": idx,
                        "party_id": party.get("Party_ID", ""),
                        "insurance_name": insurance_name,
                        "reason": "Not insured with Tawuniya Cooperative Insurance Company - Filtered out"
                    })
                    transaction_logger.info(
                        f"PARTY_FILTERED_OUT | Case: {case_number} | Party: {idx + 1} | "
                        f"Party_ID: {party.get('Party_ID', 'Unknown')} | Insurance_Name: '{insurance_name}' | "
                        f"Reason: Not_Tawuniya | Action: Removed_From_Processing"
                    )
                    continue  # Skip this party - DO NOT ADD TO converted_parties
                else:
                    transaction_logger.info(
                        f"PARTY_ACCEPTED | Case: {case_number} | Party: {idx + 1} | "
                        f"Party_ID: {party.get('Party_ID', 'Unknown')} | Insurance_Name: '{insurance_name}' | "
                        f"Reason: Tawuniya_Verified | Action: Added_To_Processing"
                    )
            
            # Convert party data
            liability = party.get("Liability", "0")
            try:
                liability = int(liability) if liability else 0
            except:
                liability = 0
            
            insurance_name = party.get("Insurance_Name", "")
            
            # Extract insurance_type from party data (optional parameter)
            # If provided, use it (e.g., "CO", "comprehensive", "TP", etc.)
            # If not provided, will default to empty (assume comprehensive) in claim_processor.py
            party_insurance_type = party.get("insurance_type", party.get("Insurance_Type", ""))
            
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
            
            insurance_info = {
                "ICArabicName": insurance_name,
                "ICEnglishName": insurance_name,
                "policyNumber": party.get("Policyholder_ID", ""),
                "insuranceCompanyID": "",
                "vehicleID": party.get("Vehicle_Serial", ""),
                "insuranceType": party_insurance_type,  # Add insurance_type from party data (optional)
                "InsuranceType": party_insurance_type,  # Alternative field name
                "insurance_type": party_insurance_type  # Alternative field name
            }
            
            converted_party = {
                "name": party.get("Party_Name", ""),
                "ID": party.get("Party_ID", ""),
                "Liability": liability,
                "liability": liability,
                "Insurance_Info": insurance_info,
                "insurance_info": insurance_info,
                "carMake": party.get("carMake", ""),
                "carModel": party.get("carModel", ""),
                "carMake_Najm": party.get("carMake_Najm", ""),
                "carModel_Najm": party.get("carModel_Najm", ""),
                "chassisNo": party.get("Vehicle_Serial", ""),
                "licenseType": party.get("License_Type_From_Najm", ""),
                "License_Type_From_Najm": party.get("License_Type_From_Najm", ""),
                "VehicleOwnerId": party.get("VehicleOwnerId", ""),
                "recovery": party.get("Recovery", False),
                "License_Expiry_Date": party.get("License_Expiry_Date", ""),
                "License_Expiry_Last_Updated": party.get("License_Expiry_Last_Updated", ""),
                "Policyholder_ID": party.get("Policyholder_ID", ""),
                "Policyholdername": policyholder_name,  # NEW: Policyholder name parameter
                "Policyholder_Name": policyholder_name,  # Alternative field name
                "Party": party.get("Party", f"Party {idx + 1}"),
                "insurance_type": insurance_type
            }
            
            converted_parties.append(converted_party)
            transaction_logger.info(
                f"PARTY_ADDED_TO_PROCESSING | Case: {case_number} | Party: {idx + 1} | "
                f"Party_ID: {party.get('Party_ID', 'Unknown')}"
            )
        
        # If no parties to process after filtering, return early
        if not converted_parties:
            transaction_logger.warning(
                f"NO_PARTIES_TO_PROCESS | Case: {case_number} | "
                f"Total_Parties: {len(data['Parties'])} | Filtered_Out: {len(skipped_parties)}"
            )
            response_data = {
                "Status": "Success",
                "Case_Number": case_number,
                "Accident_Date": accident_date,
                "Upload_Date": upload_date,
                "Claim_requester_ID": claim_requester_id,
                "Total_Parties": len(data["Parties"]),
                "Parties_Processed": 0,
                "Parties_Skipped": len(skipped_parties),
                "Parties": [],
                "LD_Rep_64bit_Received": bool(ld_rep_base64)
            }
            if only_process_tawuniya and skipped_parties:
                response_data["Parties_Skipped"] = len(skipped_parties)
                response_data["Filter_Applied"] = "Only processing Tawuniya Cooperative Insurance Company parties"
            return jsonify(response_data), 200
        
        # Build claim data
        claim_data = {
            "Case_Info": {
                "Accident_info": accident_info,
                "parties": {
                    "Party_Info": converted_parties
                }
            }
        }
        
        # Process parties in parallel
        results = []
        max_workers = min(len(converted_parties), 4)
        
        transaction_logger.info(
            f"PARALLEL_PROCESSING_START | Case: {case_number} | "
            f"Parties_Count: {len(converted_parties)} | Max_Workers: {max_workers}"
        )
        
        processing_start_time = datetime.now()
        
        def process_single_party(idx, party):
            """Process a single party using CO processor"""
            nonlocal claim_data, ocr_text, ld_rep_base64, isDAA, suspect_as_fraud, daa_reason_english
            nonlocal case_number, accident_date, converted_parties, only_process_tawuniya, tawuniya_insurance_names
            # ollama_url and ollama_model are module-level, accessible without nonlocal
            
            try:
                # Use claim_type "CO" as insurance_type for internal processing (response building, etc.)
                # BUT: Do NOT send this to Ollama - insurance_type in data sent to Ollama should be empty (assume comprehensive)
                claim_type = "CO"  # This is the claim type (CO = Comprehensive)
                insurance_type = claim_type  # Use internally for response building
                
                # Safety check: verify Tawuniya if filter enabled (FIXED - Use Insurance_Info from converted_party)
                if only_process_tawuniya:
                    # Get insurance name from converted_party structure (Insurance_Info, not Insurance_Name)
                    insurance_info = party.get("Insurance_Info", {}) or party.get("insurance_info", {})
                    insurance_name = (
                        insurance_info.get("ICEnglishName", "") or 
                        insurance_info.get("ICArabicName", "") or
                        party.get("Insurance_Name", "")  # Fallback to Insurance_Name if exists
                    ).strip()
                    insurance_name_lower = insurance_name.lower() if insurance_name else ""
                    is_tawuniya_verified = False
                    
                    # Enhanced Tawuniya check (same as pre-filter)
                    if insurance_name_lower and "tawuniya" in insurance_name_lower:
                        is_tawuniya_verified = True
                    elif insurance_name_lower and "cooperative insurance company" in insurance_name_lower:
                        is_tawuniya_verified = True
                    elif insurance_name and ("التعاونية" in insurance_name or "التعاونية للتأمين" in insurance_name):
                        is_tawuniya_verified = True
                    elif insurance_name_lower:
                        for tawuniya_name in tawuniya_insurance_names:
                            if not tawuniya_name:
                                continue
                            tawuniya_lower = tawuniya_name.lower().strip()
                            if (tawuniya_lower in insurance_name_lower or 
                                insurance_name_lower in tawuniya_lower or
                                insurance_name_lower == tawuniya_lower):
                                is_tawuniya_verified = True
                                break
                    
                    if not is_tawuniya_verified:
                        transaction_logger.warning(
                            f"SAFETY_CHECK_FAILED | Case: {case_number} | Party: {idx + 1} | "
                            f"Party_ID: {party.get('ID', 'Unknown')} | Insurance_Name: '{insurance_name}' | "
                            f"Action: Skipping_Immediately"
                        )
                        return {
                            "_index": idx,
                            "Party": party.get("Party", f"Party {idx + 1}"),
                            "Party_ID": party.get("ID", ""),
                            "Decision": "SKIPPED",
                            "Classification": "FILTERED_OUT",
                            "Reasoning": "Party not insured with Tawuniya Cooperative Insurance Company - Removed by filter",
                            "Applied_Conditions": []
                        }
                    else:
                        transaction_logger.info(
                            f"SAFETY_CHECK_PASSED | Case: {case_number} | Party: {idx + 1} | "
                            f"Party_ID: {party.get('ID', 'Unknown')} | Insurance_Name: '{insurance_name}' | "
                            f"Action: Proceeding_With_Processing"
                        )
                
                # Reload rules
                co_processor.reload_rules()
                
                # Process party claim
                party_result = co_processor.process_party_claim(
                    claim_data=claim_data,
                    party_info=party,
                    party_index=idx,
                    all_parties=converted_parties
                )
                
                # Calculate additional fields
                # Use claim_type "CO" as insurance_type for internal processing (response building)
                # BUT: insurance_type sent to Ollama is empty (assume comprehensive) - handled in claim_processor.py
                additional_fields = calculate_additional_fields(party, isDAA, insurance_type)
                
                # Log decision received from processor
                transaction_logger.info(
                    f"PARTY_RESULT_RECEIVED | Case: {case_number} | Party: {idx + 1} | "
                    f"Party_ID: {party.get('ID', 'Unknown')} | "
                    f"Decision: {party_result.get('decision', 'ERROR')} | "
                    f"Classification: {party_result.get('classification', 'UNKNOWN')} | "
                    f"Reasoning: {party_result.get('reasoning', '')} | "
                    f"Applied_Conditions: {party_result.get('applied_conditions', [])}"
                )
                
                # Build response
                base_response = {
                    "_index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Party_Name": party.get("name", ""),
                    "Liability": party.get("Liability", 0),
                    "Decision": party_result.get("decision", "ERROR"),
                    "Classification": party_result.get("classification", "UNKNOWN"),
                    "Reasoning": party_result.get("reasoning", ""),
                    "Applied_Conditions": party_result.get("applied_conditions", []),
                    "isDAA": isDAA,
                    "Suspect_as_Fraud": suspect_as_fraud,
                    "DaaReasonEnglish": daa_reason_english,
                    "Policyholder_ID": party.get("Policyholder_ID", ""),
                    "Policyholdername": party.get("Policyholdername", party.get("Policyholder_Name", "")),  # NEW: Policyholder name
                    "Suspected_Fraud": additional_fields.get("Suspected_Fraud"),
                    "model_recovery": additional_fields.get("model_recovery"),
                    "License_Type_From_Make_Model": additional_fields.get("License_Type_From_Make_Model")
                    # NOTE: Do NOT include insurance_type in response - it should be empty for CO claims (assume comprehensive)
                    # insurance_type is only set if explicitly provided in party data, otherwise empty (Rule #1)
                }
                
                # Log response being built
                transaction_logger.info(
                    f"RESPONSE_BUILDING | Case: {case_number} | Party: {idx + 1} | "
                    f"Party_ID: {base_response.get('Party_ID', 'Unknown')} | "
                    f"Response_Decision: {base_response.get('Decision', 'ERROR')} | "
                    f"Response_Classification: {base_response.get('Classification', 'UNKNOWN')} | "
                    f"Response_Reasoning: {base_response.get('Reasoning', '')}"
                )
                
                # Filter response fields based on config
                co_config_manager.reload_config()
                response_fields_config = co_config_manager.get_config().get("response_fields", {}).get("enabled_fields", {})
                
                filtered_response = {}
                for field_name, field_value in base_response.items():
                    if field_name == "_index":
                        filtered_response["_index"] = field_value
                        continue
                    if response_fields_config.get(field_name, True):
                        filtered_response[field_name] = field_value
                
                return filtered_response
                
            except Exception as e:
                error_msg = str(e)
                transaction_logger.error(
                    f"PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {error_msg[:200]} | "
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
            """Calculate additional fields using CO unified processor"""
            additional = {}
            
            car_make = party_data.get("carMake", "") or party_data.get("carMake_Najm", "")
            car_model = party_data.get("carModel", "") or party_data.get("carModel_Najm", "")
            license_type_from_make_model = ""
            if car_make and car_model:
                try:
                    license_type_from_make_model = co_unified_processor.lookup_license_type_from_make_model(car_make, car_model)
                except Exception as e:
                    license_type_from_make_model = ""
            additional["License_Type_From_Make_Model"] = license_type_from_make_model
            
            suspected_fraud = None
            if is_daa_value:
                is_daa_str = str(is_daa_value).strip().upper()
                if is_daa_str in ['TRUE', '1', 'YES', 'Y', 'T']:
                    suspected_fraud = "Suspected Fraud"
            additional["Suspected_Fraud"] = suspected_fraud
            
            model_recovery = False
            license_type_from_request = party_data.get("licenseType", "") or party_data.get("License_Type_From_Najm", "")
            if license_type_from_make_model and license_type_from_make_model.strip() and license_type_from_make_model.strip() != "Any License":
                if license_type_from_request and license_type_from_request.strip():
                    if license_type_from_make_model.strip().upper() != license_type_from_request.strip().upper():
                        model_recovery = True
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
                    if "_index" in result:
                        del result["_index"]
                    completed_results[result_index] = result
                except Exception as e:
                    idx, party = future_to_party[future]
                    transaction_logger.error(
                        f"PARTY_PROCESSING_ERROR | Case: {case_number} | Party: {idx + 1} | Error: {str(e)[:200]}"
                    )
        
        # Filter out SKIPPED or FILTERED_OUT parties from results
        # CRITICAL: For CO claims, ONLY return Tawuniya parties
        filtered_results = []
        for result in completed_results.values():
            # Skip filtered out parties
            if result.get("Decision") == "SKIPPED" or result.get("Classification") == "FILTERED_OUT":
                transaction_logger.info(
                    f"RESULT_FILTERED_OUT | Case: {case_number} | "
                    f"Party_ID: {result.get('Party_ID', 'Unknown')} | "
                    f"Reason: {result.get('Reasoning', 'Filtered out')}"
                )
                continue
            
            # For CO with Tawuniya filter, verify party is Tawuniya - STRICT CHECK
            if only_process_tawuniya:
                party_id = result.get("Party_ID", "")
                is_tawuniya_result = False
                
                # Find original party to verify insurance name
                for orig_party in data.get("Parties", []):
                    if str(orig_party.get("Party_ID", "")) == str(party_id):
                        insurance_name = orig_party.get("Insurance_Name", "").strip()
                        insurance_name_lower = insurance_name.lower() if insurance_name else ""
                        
                        # Enhanced Tawuniya check - same logic as pre-filter
                        if insurance_name_lower and "tawuniya" in insurance_name_lower:
                            is_tawuniya_result = True
                        elif insurance_name_lower and "cooperative insurance company" in insurance_name_lower:
                            is_tawuniya_result = True
                        elif insurance_name and ("التعاونية" in insurance_name or "التعاونية للتأمين" in insurance_name):
                            is_tawuniya_result = True
                        elif insurance_name_lower:
                            for tawuniya_name in tawuniya_insurance_names:
                                if not tawuniya_name:
                                    continue
                                tawuniya_lower = tawuniya_name.lower()
                                if (tawuniya_lower in insurance_name_lower or insurance_name_lower in tawuniya_lower):
                                    is_tawuniya_result = True
                                    break
                        break
                
                # If not Tawuniya, skip this result - DO NOT ADD TO RESPONSE
                if not is_tawuniya_result:
                    transaction_logger.info(
                        f"RESULT_FILTERED_OUT | Case: {case_number} | Party_ID: {party_id} | "
                        f"Reason: Non-Tawuniya party in results - Removing from response | "
                        f"Action: Bypassed_Completely"
                    )
                    continue
            
            # Only add verified Tawuniya parties to filtered results
            filtered_results.append(result)
        
        # Sort results by Party number (Party 1, Party 2, etc.)
        def get_party_number(party_result):
            party_str = party_result.get("Party", "")
            try:
                if "Party" in party_str:
                    return int(party_str.replace("Party", "").strip())
            except:
                pass
            return 999  # Put unknown parties at the end
        filtered_results = sorted(filtered_results, key=get_party_number)
        
        total_processing_time = (datetime.now() - processing_start_time).total_seconds()
        
        # Log filtering summary
        if only_process_tawuniya:
            transaction_logger.info(
                f"CO_FILTERING_SUMMARY | Case: {case_number} | "
                f"Total_Parties_Requested: {len(data['Parties'])} | "
                f"Tawuniya_Parties_Processed: {len(filtered_results)} | "
                f"Non_Tawuniya_Parties_Skipped: {len(skipped_parties)} | "
                f"Filter_Status: Active | "
                f"Response_Contains_Only_Tawuniya: True"
            )
        else:
            transaction_logger.info(
                f"PARALLEL_PROCESSING_COMPLETE | Case: {case_number} | "
                f"Parties_Processed: {len(filtered_results)} | Total_Time: {total_processing_time:.2f}s"
            )
        
        # Log final decisions for all parties
        for result in filtered_results:
            transaction_logger.info(
                f"FINAL_DECISION_SUMMARY | Case: {case_number} | "
                f"Party_ID: {result.get('Party_ID', 'Unknown')} | "
                f"Party_Name: {result.get('Party_Name', 'Unknown')} | "
                f"Liability: {result.get('Liability', 0)} | "
                f"Final_Decision: {result.get('Decision', 'UNKNOWN')} | "
                f"Final_Classification: {result.get('Classification', 'UNKNOWN')} | "
                f"Final_Reasoning: {result.get('Reasoning', '')} | "
                f"Applied_Conditions: {result.get('Applied_Conditions', [])}"
            )
        
        # Build response - ONLY Tawuniya parties will be in filtered_results
        response_data = {
            "Case_Number": case_number,
            "Accident_Date": accident_date,
            "Upload_Date": upload_date,
            "Claim_requester_ID": claim_requester_id,
            "Status": "Success",
            "Parties": filtered_results,  # Only Tawuniya parties - all others bypassed
            "Total_Parties": len(data["Parties"]),
            "Parties_Processed": len(filtered_results),  # Only Tawuniya parties processed
            "LD_Rep_64bit_Received": bool(ld_rep_base64)
        }
        
        # Add filter information if Tawuniya filter is enabled
        if only_process_tawuniya:
            response_data["Parties_Skipped"] = len(skipped_parties)
            response_data["Filter_Applied"] = "Only processing Tawuniya Cooperative Insurance Company parties"
            response_data["Filter_Status"] = "Active - Non-Tawuniya parties bypassed"
            
            # Log which parties are in response vs skipped
            tawuniya_party_ids = [p.get("Party_ID", "") for p in filtered_results]
            skipped_party_ids = [p.get("party_id", "") for p in skipped_parties]
            transaction_logger.info(
                f"CO_RESPONSE_PARTIES | Case: {case_number} | "
                f"Tawuniya_Parties_In_Response: {tawuniya_party_ids} | "
                f"Skipped_Party_IDs: {skipped_party_ids} | "
                f"Total_In_Response: {len(tawuniya_party_ids)} | "
                f"Total_Skipped: {len(skipped_party_ids)}"
            )
        
        # Log complete response being returned
        transaction_logger.info(
            f"CO_RESPONSE_COMPLETE | Case: {case_number} | "
            f"Status: {response_data.get('Status', 'UNKNOWN')} | "
            f"Total_Parties: {response_data.get('Total_Parties', 0)} | "
            f"Parties_Processed: {response_data.get('Parties_Processed', 0)} | "
            f"Parties_Skipped: {response_data.get('Parties_Skipped', 0)} | "
            f"Response_JSON: {json.dumps(response_data, ensure_ascii=False)}"
        )
        
        return jsonify(response_data), 200
        
    except Exception as e:
        error_msg = str(e)
        transaction_logger.error(
            f"CO_CLAIM_PROCESSING_ERROR | Error: {error_msg} | "
            f"Traceback: {traceback.format_exc()[:2000]}"
        )
        return jsonify({"error": error_msg}), 500

