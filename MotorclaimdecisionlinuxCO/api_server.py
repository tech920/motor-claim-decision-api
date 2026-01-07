"""
REST API Server for Motor Claim Decision System
Provides HTTP endpoints to process claims via Ollama
"""

from flask import Flask, request, jsonify, Response
from claim_processor import ClaimProcessor
from excel_ocr_license_processor import ExcelOCRLicenseProcessor
from unified_processor import UnifiedClaimProcessor
from auth_manager import auth_manager
from config_manager import config_manager
import os
import json
import base64
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial, wraps
import traceback

app = Flask(__name__)

# Setup logging configuration
BASE_DIR = os.getenv("MOTORCLAIM_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Error log file - logs all errors from HTML pages and API
ERROR_LOG_FILE = os.path.join(LOG_DIR, "error.log")

# Configure logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=10*1024*1024,  # 10MB per file
            backupCount=5,  # Keep 5 backup files
            encoding='utf-8'
        )
    ]
)

# Create logger for this module
logger = logging.getLogger(__name__)

# Daily transaction log file - logs all API requests for CO
# Use parent directory to create shared log location
PARENT_DIR = os.path.dirname(BASE_DIR)
UNIFIED_LOG_DIR = os.path.join(PARENT_DIR, "logs")
os.makedirs(UNIFIED_LOG_DIR, exist_ok=True)

# Get current date for daily log file
current_date = datetime.now().strftime('%Y-%m-%d')
TRANSACTION_LOG_FILE = os.path.join(UNIFIED_LOG_DIR, f"api_transactions_co_{current_date}.log")

# Transaction logger - logs all requests (HTML pages and API endpoints)
transaction_logger = logging.getLogger("transaction_co")
transaction_logger.setLevel(logging.INFO)

# Use TimedRotatingFileHandler for daily rotation
transaction_handler = TimedRotatingFileHandler(
    TRANSACTION_LOG_FILE,
    when='midnight',  # Rotate at midnight
    interval=1,  # Every day
    backupCount=30,  # Keep 30 days of logs
    encoding='utf-8',
    utc=False  # Use local time
)
transaction_formatter = logging.Formatter(
    '%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
transaction_handler.setFormatter(transaction_formatter)
transaction_handler.suffix = '%Y-%m-%d'  # Date suffix for rotated files
transaction_logger.addHandler(transaction_handler)
transaction_logger.propagate = False  # Don't propagate to root logger

# Store current date to check if we need to update log file
_last_log_date = current_date

# Log startup information
startup_log_file = os.path.join(LOG_DIR, "startup.log")
startup_logger = logging.getLogger("startup")
startup_logger.setLevel(logging.INFO)
startup_handler = RotatingFileHandler(
    startup_log_file,
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8'
)
startup_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
startup_logger.addHandler(startup_handler)

# Initialize processor
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")  # Fast, accurate for Arabic/English decision making
OLLAMA_TRANSLATION_MODEL = os.getenv("OLLAMA_TRANSLATION_MODEL", "llama3.2:latest")  # Fast translation model

# Log Ollama model configuration at startup
startup_logger.info("=" * 60)
startup_logger.info("Motor Claim Decision System - Starting Up")
startup_logger.info("=" * 60)
startup_logger.info(f"Startup Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
startup_logger.info(f"Ollama URL: {OLLAMA_URL}")
startup_logger.info(f"Decision Model: {OLLAMA_MODEL}")
startup_logger.info(f"Translation Model: {OLLAMA_TRANSLATION_MODEL}")
startup_logger.info(f"Base Directory: {BASE_DIR}")
startup_logger.info(f"Error Log File: {ERROR_LOG_FILE}")
startup_logger.info(f"Startup Log File: {startup_log_file}")
startup_logger.info("=" * 60)

processor = ClaimProcessor(
    ollama_base_url=OLLAMA_URL,
    model_name=OLLAMA_MODEL,
    translation_model=OLLAMA_TRANSLATION_MODEL
)

# Initialize OCR license processor
ocr_license_processor = ExcelOCRLicenseProcessor()

# Initialize unified processor for license type lookup
unified_processor = UnifiedClaimProcessor()

# Log successful initialization
startup_logger.info("✅ ClaimProcessor initialized successfully")
startup_logger.info("✅ OCR License Processor initialized successfully")
startup_logger.info("✅ Unified Processor initialized successfully")
startup_logger.info(f"✅ Transaction log file: {TRANSACTION_LOG_FILE}")


# Request logging middleware - logs all API requests
@app.before_request
def log_request_info():
    """Log all incoming requests"""
    global _last_log_date
    
    # Check if date changed and update log file if needed
    current_date = datetime.now().strftime('%Y-%m-%d')
    if current_date != _last_log_date:
        # Date changed, create new log file handler
        new_log_file = os.path.join(UNIFIED_LOG_DIR, f"api_transactions_co_{current_date}.log")
        transaction_logger.handlers.clear()  # Remove old handler
        new_handler = TimedRotatingFileHandler(
            new_log_file,
            when='midnight',
            interval=1,
            backupCount=30,
            encoding='utf-8',
            utc=False
        )
        new_handler.setFormatter(transaction_formatter)
        new_handler.suffix = '%Y-%m-%d'
        transaction_logger.addHandler(new_handler)
        _last_log_date = current_date
    
    # Get client IP
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    if ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    
    # Get request details
    method = request.method
    path = request.path
    user_agent = request.headers.get('User-Agent', 'unknown')
    
    # Log request
    transaction_logger.info(
        f"CO | REQUEST | {method} | {path} | IP: {client_ip} | User-Agent: {user_agent[:100]}"
    )


@app.after_request
def log_response_info(response):
    """Log all outgoing responses"""
    # Get request details
    method = request.method
    path = request.path
    status_code = response.status_code
    
    # Get response size if available
    try:
        response_size = len(response.get_data())
    except:
        response_size = 0
    
    # Log response
    transaction_logger.info(
        f"CO | RESPONSE | {method} | {path} | Status: {status_code} | Size: {response_size} bytes"
    )
    
    return response


# Global error handler for all unhandled exceptions
@app.errorhandler(Exception)
def handle_global_error(e):
    """Global error handler - logs all unhandled exceptions"""
    error_msg = f"Unhandled exception: {str(e)}"
    error_traceback = traceback.format_exc()
    logger.error(f"{error_msg}\n{error_traceback}")
    return jsonify({"error": "Internal server error", "details": str(e)}), 500


def check_auth(username, password):
    """Check if username and password are valid"""
    if not username or not password:
        return False
    return auth_manager.verify_user(username, password)


def authenticate():
    """Sends a 401 response that enables basic auth - browser will show native login dialog"""
    return Response(
        'Authentication required', 401,
        {
            'WWW-Authenticate': 'Basic realm="Motor Claim Decision API - Login Required"',
            'Content-Type': 'text/plain'
        }
    )


def requires_auth(f):
    """Decorator to require Basic Authentication - blocks access until authenticated"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth:
            # No auth header - return 401 to trigger browser login dialog
            return authenticate()
        
        # Verify credentials
        if not check_auth(auth.username, auth.password):
            # Invalid credentials - return 401 to trigger browser login dialog again
            return authenticate()
        
        # Authentication successful - proceed with request
        return f(*args, **kwargs)
    return decorated


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "ollama_url": OLLAMA_URL,
        "decision_model": OLLAMA_MODEL,
        "translation_model": OLLAMA_TRANSLATION_MODEL
    })


@app.route("/process-claim", methods=["POST"])
@requires_auth
def process_claim():
    """
    Process a claim from JSON or XML input
    
    Request body can be:
    - JSON object with claim data
    - JSON with 'claim_data' field (string XML or JSON)
    - JSON with 'format' field to specify 'xml' or 'json'
    """
    try:
        # Reload rules from config before processing (to get latest changes)
        processor.reload_rules()
        
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Check if claim_data is provided as string
        if "claim_data" in data:
            claim_input = data["claim_data"]
            input_format = data.get("format", "auto")
            result = processor.process_claim(claim_input, input_format=input_format)
        else:
            # Treat entire body as claim data
            claim_json = json.dumps(data)
            result = processor.process_claim(claim_json, input_format="json")
        
        return jsonify(result), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/process-claim-xml", methods=["POST"])
@requires_auth
def process_claim_xml():
    """Process a claim from XML input"""
    try:
        # Reload rules from config before processing (to get latest changes)
        processor.reload_rules()
        
        xml_data = request.data.decode('utf-8')
        result = processor.process_claim(xml_data, input_format="xml")
        return jsonify(result), 200
    
    except ValueError as e:
        logger.error(f"ValueError in /process-claim-xml: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 400
    except ConnectionError as e:
        logger.error(f"ConnectionError in /process-claim-xml: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"Exception in /process-claim-xml: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/process-claim-json", methods=["POST"])
@requires_auth
def process_claim_json():
    """Process a claim from JSON input"""
    try:
        # Reload rules from config before processing (to get latest changes)
        processor.reload_rules()
        
        json_data = request.get_json()
        if not json_data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        claim_json = json.dumps(json_data)
        result = processor.process_claim(claim_json, input_format="json")
        return jsonify(result), 200
    
    except ValueError as e:
        logger.error(f"ValueError in /process-claim-json: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 400
    except ConnectionError as e:
        logger.error(f"ConnectionError in /process-claim-json: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"Exception in /process-claim-json: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/process-claim-simplified", methods=["POST"])
@requires_auth
def process_claim_simplified():
    """
    Process a claim from simplified JSON structure
    
    Expected structure:
    {
        "Case_Number": "",
        "Accident_Date": "",
        "Upload_Date": "",
        "accident_description": "",  # Optional: Full accident description
        "isDAA": "",  # Optional: DAA flag (true/false)
        "Suspect_as_Fraud": "",  # Optional: Suspect as fraud flag
        "DaaReasonEnglish": "",  # Optional: DAA reason in English
        "Parties": [
            {
                "Party": "",
                "Party_ID": "",
                "Party_Name": "",
                "Insurance_Name": "",
                "Policyholder_ID": "",
                "Liability": "",
                "Vehicle_Serial": "",
                "VehicleOwnerId": "",
                "License_Type_From_Najm": "",
                "Recovery": False,
                "License_Expiry_Date": "",
                "License_Expiry_Last_Updated": "",
                "carMake": "",
                "carMake_Najm": "",
                "carModel": "",
                "carModel_Najm": ""
            }
        ],
        "Name_LD_rep_64bit": ""
    }
    
    Returns decision and classification for each party, including DAA parameters:
    - isDAA: DAA flag from request
    - Suspect_as_Fraud: Suspect as fraud flag from request
    - DaaReasonEnglish: DAA reason in English from request
    
    Parties are processed in parallel for faster response times.
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        if "Parties" not in data or not isinstance(data["Parties"], list):
            return jsonify({"error": "Invalid structure: 'Parties' array is required"}), 400
        
        # Convert simplified structure to format expected by processor
        case_number = data.get("Case_Number", "")
        accident_date = data.get("Accident_Date", "")
        accident_description = data.get("accident_description", "")  # Get accident description from request
        ld_rep_base64 = data.get("Name_LD_rep_64bit", "")
        
        # Extract DAA parameters from request
        isDAA = data.get("isDAA", None)
        suspect_as_fraud = data.get("Suspect_as_Fraud", None)
        daa_reason_english = data.get("DaaReasonEnglish", None)
        
        # Process OCR to extract license expiry dates if base64 provided
        ocr_text = None
        ocr_processing_result = {
            "status": "no_image",
            "text_length": 0,
            "error": None
        }
        
        if ld_rep_base64:
            try:
                # Log image processing start
                transaction_logger.info(
                    f"IMAGE_PROCESSING_START | Case: {case_number} | "
                    f"Base64_Length: {len(ld_rep_base64)} | "
                    f"Has_Data_Prefix: {ld_rep_base64.startswith('data:')}"
                )
                
                # If base64 is HTML/text content, decode it
                if ld_rep_base64.startswith('data:text') or ld_rep_base64.startswith('data:image'):
                    # Extract base64 part
                    if ',' in ld_rep_base64:
                        base64_part = ld_rep_base64.split(',')[1]
                    else:
                        base64_part = ld_rep_base64
                else:
                    base64_part = ld_rep_base64
                
                # Try to decode as text (HTML/OCR text)
                try:
                    decoded = base64.b64decode(base64_part).decode('utf-8', errors='ignore')
                    # Check if it looks like text/HTML
                    if '<html' in decoded.lower() or 'party' in decoded.lower() or 'رخصة' in decoded:
                        ocr_text = decoded
                        ocr_processing_result = {
                            "status": "success",
                            "text_length": len(ocr_text),
                            "error": None
                        }
                        print(f"  ✓ Extracted OCR text from base64 ({len(ocr_text)} chars)")
                        
                        # Log successful OCR extraction
                        transaction_logger.info(
                            f"IMAGE_PROCESSING_SUCCESS | Case: {case_number} | "
                            f"Type: HTML/Text | Text_Length: {len(ocr_text)} | "
                            f"Preview: {ocr_text[:200]}..."
                        )
                except Exception as decode_error:
                    # If decoding fails, might be image - would need OCR library
                    ocr_processing_result = {
                        "status": "image_detected",
                        "text_length": 0,
                        "error": "Image detected, OCR library required"
                    }
                    print(f"  ℹ️ Base64 appears to be image, OCR text extraction would require OCR library")
                    
                    # Log image detection
                    transaction_logger.info(
                        f"IMAGE_PROCESSING_DETECTED | Case: {case_number} | "
                        f"Type: Image | Status: OCR library required"
                    )
            except Exception as e:
                error_msg = f"Error processing base64: {str(e)[:100]}"
                ocr_processing_result = {
                    "status": "error",
                    "text_length": 0,
                    "error": error_msg
                }
                logger.error(f"{error_msg}\n{traceback.format_exc()}")
                print(f"  ⚠️ {error_msg}")
                
                # Log OCR processing error
                transaction_logger.error(
                    f"IMAGE_PROCESSING_ERROR | Case: {case_number} | "
                    f"Error: {error_msg}"
                )
        
        # Process claim data to fill in missing license expiry dates from OCR
        validation_results = {}
        if ocr_text:
            print(f"\n  🔍 Processing OCR text to extract license expiry dates...")
            print(f"  🔍 OCR text length: {len(ocr_text)} characters")
            print(f"  🔍 OCR text preview (first 500 chars): {ocr_text[:500]}")
            
            # Log OCR validation start
            transaction_logger.info(
                f"OCR_VALIDATION_START | Case: {case_number} | "
                f"OCR_Text_Length: {len(ocr_text)} | "
                f"Parties_Count: {len(data.get('Parties', []))}"
            )
            
            try:
                data = ocr_license_processor.process_claim_data_with_ocr(
                    claim_data=data,
                    ocr_text=ocr_text,
                    base64_image=ld_rep_base64 if not ocr_text else None
                )
                
                # Extract validation results
                for idx, party in enumerate(data.get("Parties", [])):
                    license_expiry = party.get("License_Expiry_Date", "")
                    license_updated = party.get("License_Expiry_Last_Updated", "")
                    validation_results[f"party_{idx}"] = {
                        "license_expiry_extracted": bool(license_expiry),
                        "license_expiry_date": license_expiry,
                        "license_updated": license_updated
                    }
                
                print(f"  ✅ Finished processing OCR for license expiry dates")
                
                # Log OCR validation success
                transaction_logger.info(
                    f"OCR_VALIDATION_SUCCESS | Case: {case_number} | "
                    f"Parties_Processed: {len(validation_results)} | "
                    f"Results: {json.dumps(validation_results)}"
                )
            except Exception as validation_error:
                error_msg = f"OCR validation error: {str(validation_error)[:200]}"
                logger.error(f"{error_msg}\n{traceback.format_exc()}")
                
                # Log OCR validation error
                transaction_logger.error(
                    f"OCR_VALIDATION_ERROR | Case: {case_number} | "
                    f"Error: {error_msg}"
                )
        
        # Build accident info - use provided accident_description if available
        if accident_description:
            accident_desc = accident_description
        else:
            accident_desc = f"Case: {case_number}, Date: {accident_date}"
        
        accident_info = {
            "caseNumber": case_number,
            "AccidentDescription": accident_desc,
            "callDate": accident_date,
            "Accident_description": accident_desc,
            "Name_LD_rep_64bit": ld_rep_base64,  # Store base64 data
            # Add DAA fields to accident_info so they're included in Case Information for Ollama
            "isDAA": isDAA,
            "Suspect_as_Fraud": suspect_as_fraud,
            "DaaReasonEnglish": daa_reason_english
        }
        
        # Convert parties to expected format
        converted_parties = []
        for idx, party in enumerate(data["Parties"]):
            # Convert liability to int
            liability = party.get("Liability", "0")
            try:
                liability = int(liability) if liability else 0
            except:
                liability = 0
            
            # Build insurance info
            insurance_name = party.get("Insurance_Name", "")
            # Extract insurance type if provided (for comprehensive insurance validation - Rule #1)
            insurance_type = (
                party.get("Insurance_Type") or
                party.get("InsuranceType") or
                party.get("insurance_type") or
                party.get("CoverageType") or
                party.get("coverage_type") or
                ""
            )
            insurance_info = {
                "ICArabicName": insurance_name,
                "ICEnglishName": insurance_name,
                "policyNumber": party.get("Policyholder_ID", ""),
                "insuranceCompanyID": "",
                "vehicleID": party.get("Vehicle_Serial", ""),
                "insuranceType": insurance_type  # Add insurance type for Rule #1 validation
            }
            
            # Build party info in expected format
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
                "carMfgYear": "",
                "plateNo": "",
                "chassisNo": party.get("Vehicle_Serial", ""),
                "licenseType": party.get("License_Type_From_Najm", ""),
                "License_Type_From_Najm": party.get("License_Type_From_Najm", ""),  # Keep for model_recovery calculation
                "licenseNo": "",
                "VehicleOwnerId": party.get("VehicleOwnerId", ""),
                "ownerName": "",
                "recovery": party.get("Recovery", False),
                "License_Expiry_Date": party.get("License_Expiry_Date", ""),
                "License_Expiry_Last_Updated": party.get("License_Expiry_Last_Updated", ""),
                "Policyholder_ID": party.get("Policyholder_ID", ""),  # Include Policyholder_ID
                "Party": party.get("Party", f"Party {idx + 1}")
            }
            
            converted_parties.append(converted_party)
        
        # Build claim data structure
        claim_data = {
            "Case_Info": {
                "Accident_info": accident_info,
                "parties": {
                    "Party_Info": converted_parties
                }
            }
        }
        
        # Reload rules from config before processing (to get latest changes)
        try:
            processor.reload_rules()
        except Exception as e:
            error_msg = f"Warning: Could not reload rules: {e}"
            logger.warning(f"{error_msg}\n{traceback.format_exc()}")
            print(error_msg)
        
        # Get response fields configuration (reload to get latest changes)
        config_manager.reload_config()
        config = config_manager.get_config()
        response_fields_config = config.get("response_fields", {}).get("enabled_fields", {})
        
        # Helper function to calculate additional fields (same logic as Excel)
        def calculate_additional_fields(party_data, is_daa_value):
            """Calculate Suspected_Fraud, model_recovery, License_Type_From_Make_Model"""
            additional = {}
            
            # Calculate License_Type_From_Make_Model
            car_make = party_data.get("carMake", "") or party_data.get("carMake_Najm", "")
            car_model = party_data.get("carModel", "") or party_data.get("carModel_Najm", "")
            license_type_from_make_model = ""
            if car_make and car_model:
                try:
                    license_type_from_make_model = unified_processor.lookup_license_type_from_make_model(car_make, car_model)
                except Exception as e:
                    print(f"  ⚠️ Error looking up license type: {e}")
                    license_type_from_make_model = ""
            additional["License_Type_From_Make_Model"] = license_type_from_make_model
            
            # Calculate Suspected_Fraud (if isDAA is TRUE, set to "Suspected Fraud", else null)
            suspected_fraud = None
            if is_daa_value:
                is_daa_str = str(is_daa_value).strip().upper()
                if is_daa_str in ['TRUE', '1', 'YES', 'Y', 'T']:
                    suspected_fraud = "Suspected Fraud"
            additional["Suspected_Fraud"] = suspected_fraud
            
            # Calculate model_recovery (if License_Type_From_Make_Model exists and is not "Any License" 
            # and doesn't match License_Type_From_Request, then True)
            model_recovery = False
            license_type_from_request = party_data.get("licenseType", "") or party_data.get("License_Type_From_Najm", "")
            if license_type_from_make_model and license_type_from_make_model.strip() and license_type_from_make_model.strip() != "Any License":
                if license_type_from_request and license_type_from_request.strip():
                    # Check if they don't match (case-insensitive)
                    if license_type_from_make_model.strip().upper() != license_type_from_request.strip().upper():
                        model_recovery = True
            additional["model_recovery"] = model_recovery
            
            return additional
        
        # Process each party in PARALLEL for faster response
        def process_single_party(idx, party):
            """Process a single party - used for parallel execution"""
            try:
                party_result = processor.process_party_claim(
                    claim_data=claim_data,
                    party_info=party,
                    party_index=idx,
                    all_parties=converted_parties
                )
                
                # Calculate additional fields (same logic as Excel)
                additional_fields = calculate_additional_fields(party, isDAA)
                
                # Build base response with all possible fields
                base_response = {
                    "index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Party_Name": party.get("name", ""),
                    "Liability": party.get("Liability", 0),
                    "Decision": party_result.get("decision", "PENDING"),
                    "Classification": party_result.get("classification", "UNKNOWN"),
                    "Reasoning": party_result.get("reasoning", ""),
                    "Applied_Conditions": party_result.get("applied_conditions", []),
                    "isDAA": isDAA,  # DAA parameter from request
                    "Suspect_as_Fraud": suspect_as_fraud,  # DAA parameter from request
                    "DaaReasonEnglish": daa_reason_english,  # DAA parameter from request
                    "Policyholder_ID": party.get("Policyholder_ID", ""),  # Policyholder ID from request
                    "Suspected_Fraud": additional_fields.get("Suspected_Fraud"),  # Calculated
                    "model_recovery": additional_fields.get("model_recovery"),  # Calculated
                    "License_Type_From_Make_Model": additional_fields.get("License_Type_From_Make_Model"),  # Calculated
                    "error": None
                }
                
                # Filter response based on configuration (only include enabled fields)
                # Keep "index" for result tracking, but don't include it in final response
                filtered_response = {}
                for field_name, field_value in base_response.items():
                    if field_name == "error":
                        continue  # Always exclude error field from response
                    if field_name == "index":
                        # Keep index for tracking but mark it for removal later
                        filtered_response["_index"] = field_value
                        continue
                    if response_fields_config.get(field_name, True):  # Default to True if not in config
                        filtered_response[field_name] = field_value
                
                return filtered_response
            except Exception as e:
                # Log error for party processing
                logger.error(f"Error processing party {idx + 1}: {str(e)}\n{traceback.format_exc()}")
                
                # Calculate additional fields even on error
                additional_fields = calculate_additional_fields(party, isDAA)
                
                # Build base error response
                base_error_response = {
                    "index": idx,
                    "Party": party.get("Party", f"Party {idx + 1}"),
                    "Party_ID": party.get("ID", ""),
                    "Party_Name": party.get("name", ""),
                    "Liability": party.get("Liability", 0),
                    "Decision": "ERROR",
                    "Classification": "ERROR",
                    "Reasoning": f"Error processing party: {str(e)}",
                    "Applied_Conditions": [],
                    "isDAA": isDAA,
                    "Suspect_as_Fraud": suspect_as_fraud,
                    "DaaReasonEnglish": daa_reason_english,
                    "Policyholder_ID": party.get("Policyholder_ID", ""),
                    "Suspected_Fraud": additional_fields.get("Suspected_Fraud"),
                    "model_recovery": additional_fields.get("model_recovery"),
                    "License_Type_From_Make_Model": additional_fields.get("License_Type_From_Make_Model"),
                    "error": str(e)
                }
                
                # Filter based on configuration
                filtered_error_response = {}
                for field_name, field_value in base_error_response.items():
                    if field_name in ["index", "error"]:
                        continue
                    if response_fields_config.get(field_name, True):
                        filtered_error_response[field_name] = field_value
                
                return filtered_error_response
        
        # Process parties in parallel using ThreadPoolExecutor
        results = []
        max_workers = min(len(converted_parties), 4)  # Limit to 4 parallel requests to avoid overwhelming Ollama
        
        print(f"🚀 Processing {len(converted_parties)} parties in parallel (max {max_workers} workers)...")
        
        # Log parallel processing start
        transaction_logger.info(
            f"PARALLEL_PROCESSING_START | Case: {case_number} | "
            f"Parties_Count: {len(converted_parties)} | "
            f"Max_Workers: {max_workers} | "
            f"OCR_Status: {ocr_processing_result.get('status')} | "
            f"OCR_Text_Length: {ocr_processing_result.get('text_length')}"
        )
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all party processing tasks
            future_to_party = {
                executor.submit(process_single_party, idx, party): (idx, party) 
                for idx, party in enumerate(converted_parties)
            }
            
            # Collect results as they complete
            completed_results = {}
            processing_start_time = datetime.now()
            
            for future in as_completed(future_to_party):
                try:
                    party_idx, party = future_to_party[future]
                    result = future.result()
                    
                    # Get index from result (it's stored as _index temporarily)
                    result_index = result.get("_index", party_idx)
                    # Remove _index from final response
                    if "_index" in result:
                        del result["_index"]
                    
                    completed_results[result_index] = result
                    
                    # Calculate processing time after result is ready
                    processing_time = (datetime.now() - processing_start_time).total_seconds()
                    
                    # Log party processing completion
                    transaction_logger.info(
                        f"PARTY_PROCESSING_COMPLETE | Case: {case_number} | "
                        f"Party: {party_idx + 1} | "
                        f"Decision: {result.get('Decision', 'UNKNOWN')} | "
                        f"Processing_Time: {processing_time:.2f}s"
                    )
                    print(f"  ✅ Party {party_idx + 1} completed: {result.get('Decision', 'PENDING')}")
                except Exception as e:
                    idx, party = future_to_party[future]
                    error_msg = f"Error processing party {idx + 1}: {str(e)}"
                    logger.error(f"{error_msg}\n{traceback.format_exc()}")
                    
                    # Log party processing error
                    transaction_logger.error(
                        f"PARTY_PROCESSING_ERROR | Case: {case_number} | "
                        f"Party: {idx + 1} | "
                        f"Error: {str(e)[:200]}"
                    )
                    
                    # Calculate additional fields even on error
                    additional_fields = calculate_additional_fields(party, isDAA)
                    
                    # Build base error response
                    base_error_response = {
                        "index": idx,
                        "Party": party.get("Party", f"Party {idx + 1}"),
                        "Party_ID": party.get("ID", ""),
                        "Party_Name": party.get("name", ""),
                        "Liability": party.get("Liability", 0),
                        "Decision": "ERROR",
                        "Classification": "ERROR",
                        "Reasoning": f"Error processing party: {str(e)}",
                        "Applied_Conditions": [],
                        "isDAA": isDAA,
                        "Suspect_as_Fraud": suspect_as_fraud,
                        "DaaReasonEnglish": daa_reason_english,
                        "Policyholder_ID": party.get("Policyholder_ID", ""),
                        "Suspected_Fraud": additional_fields.get("Suspected_Fraud"),
                        "model_recovery": additional_fields.get("model_recovery"),
                        "License_Type_From_Make_Model": additional_fields.get("License_Type_From_Make_Model"),
                        "error": str(e)
                    }
                    
                    # Filter based on configuration
                    filtered_error_response = {}
                    for field_name, field_value in base_error_response.items():
                        if field_name == "error":
                            continue  # Always exclude error field
                        if field_name == "index":
                            # Keep index for tracking but mark it for removal later
                            filtered_error_response["_index"] = field_value
                            continue
                        if response_fields_config.get(field_name, True):
                            filtered_error_response[field_name] = field_value
                    
                    # Remove _index from final response
                    result_index = filtered_error_response.get("_index", idx)
                    if "_index" in filtered_error_response:
                        del filtered_error_response["_index"]
                    completed_results[result_index] = filtered_error_response
                    print(f"  ❌ Party {idx + 1} failed: {str(e)[:100]}")
        
        # Sort results by index to maintain order
        results = [completed_results[i] for i in sorted(completed_results.keys())]
        
        total_processing_time = (datetime.now() - processing_start_time).total_seconds()
        
        # Log parallel processing completion
        transaction_logger.info(
            f"PARALLEL_PROCESSING_COMPLETE | Case: {case_number} | "
            f"Parties_Count: {len(results)} | "
            f"Total_Time: {total_processing_time:.2f}s | "
            f"Average_Time_Per_Party: {total_processing_time / len(results) if results else 0:.2f}s"
        )
        
        # Results already filtered by configuration, no need to remove fields
        
        print(f"✅ All {len(results)} parties processed")
        
        # Return response
        response = {
            "Case_Number": case_number,
            "Accident_Date": accident_date,
            "Status": "Success",
            "Parties": results,
            "Total_Parties": len(results),
            "LD_Rep_64bit_Received": bool(ld_rep_base64),
            "LD_Rep_64bit_Length": len(ld_rep_base64) if ld_rep_base64 else 0
        }
        
        return jsonify(response), 200
    
    except ValueError as e:
        logger.error(f"ValueError in /process-claim-simplified: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 400
    except ConnectionError as e:
        logger.error(f"ConnectionError in /process-claim-simplified: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"Exception in /process-claim-simplified: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/", methods=["GET"])
@requires_auth
def web_interface():
    """Serve the web interface"""
    try:
        with open("web_interface.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Web interface file not found"}), 404
    except Exception as e:
        logger.error(f"Error loading web interface: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error loading web interface: {str(e)}"}), 500


@app.route("/manage-prompts.html", methods=["GET"])
@requires_auth
def manage_prompts_page():
    """Serve the manage prompts page"""
    try:
        with open("manage_prompts.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Manage prompts page not found"}), 404
    except Exception as e:
        logger.error(f"Error loading manage-prompts.html: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error loading page: {str(e)}"}), 500


@app.route("/manage-rules.html", methods=["GET"])
@requires_auth
def manage_rules_page():
    """Serve the manage rules page"""
    try:
        with open("manage_rules.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Manage rules page not found"}), 404
    except Exception as e:
        logger.error(f"Error loading manage-rules.html: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error loading page: {str(e)}"}), 500


@app.route("/view-all-conditions.html", methods=["GET"])
@requires_auth
def view_all_conditions_page():
    """Serve the view all conditions page (read-only)"""
    try:
        with open("view_all_conditions.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "View all conditions page not found"}), 404
    except Exception as e:
        logger.error(f"Error loading view-all-conditions.html: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error loading page: {str(e)}"}), 500


@app.route("/manage-response-fields.html", methods=["GET"])
@requires_auth
def manage_response_fields_page():
    """Serve the manage response fields page"""
    try:
        with open("manage_response_fields.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Manage response fields page not found"}), 404
    except Exception as e:
        logger.error(f"Error loading manage-response-fields.html: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error loading page: {str(e)}"}), 500


@app.route("/process-excel-with-ocr", methods=["POST"])
@requires_auth
def process_excel_with_ocr():
    """
    Process Excel file and extract license expiry dates from OCR
    
    Request body:
    {
        "excel_file_base64": "base64_encoded_excel_file",
        "ocr_text": "OCR text from Najm report",
        "ocr_image_base64": "base64_encoded_image"
    }
    
    Returns updated Excel data with license expiry dates filled in
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Get OCR data
        ocr_text = data.get("ocr_text", "")
        ocr_image_base64 = data.get("ocr_image_base64", "")
        excel_file_base64 = data.get("excel_file_base64", "")
        
        if not excel_file_base64:
            return jsonify({"error": "excel_file_base64 is required"}), 400
        
        # Decode Excel file
        try:
            excel_data = base64.b64decode(excel_file_base64)
            import io
            import pandas as pd
            # Try reading as Excel
            try:
                df = pd.read_excel(io.BytesIO(excel_data))
            except:
                # If that fails, try reading as CSV
                df = pd.read_csv(io.BytesIO(excel_data))
        except Exception as e:
            logger.error(f"Error reading Excel file in /process-excel-with-ocr: {str(e)}\n{traceback.format_exc()}")
            return jsonify({"error": f"Error reading Excel file: {str(e)}"}), 400
        
        # Process with OCR
        if ocr_text:
            df = ocr_license_processor.process_excel_with_ocr(
                df=df,  # Pass DataFrame directly
                ocr_text=ocr_text,
                base64_image=ocr_image_base64
            )
        
        # Convert DataFrame to JSON
        result = df.to_dict(orient='records')
        
        return jsonify({
            "status": "Success",
            "rows_processed": len(result),
            "data": result
        }), 200
    
    except Exception as e:
        logger.error(f"Exception in /process-excel-with-ocr: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/config/prompts", methods=["GET"])
@requires_auth
def get_prompts():
    """Get all prompts"""
    try:
        prompts = config_manager.get_prompts()
        return jsonify({
            "status": "success",
            "prompts": prompts,
            "last_updated": config_manager.get_config().get("last_updated", "")
        }), 200
    except Exception as e:
        logger.error(f"Exception in /api/config/prompts GET: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/config/prompts", methods=["POST"])
@requires_auth
def update_prompts():
    """Update prompts"""
    try:
        data = request.get_json()
        if "prompts" not in data:
            return jsonify({"error": "No 'prompts' field provided"}), 400
        
        if config_manager.update_prompts(data["prompts"]):
            # Reload processor rules immediately
            processor.reload_rules()
            return jsonify({
                "status": "success",
                "message": "Prompts updated successfully and applied immediately",
                "last_updated": config_manager.get_config().get("last_updated", "")
            }), 200
        else:
            return jsonify({"error": "Failed to update prompts"}), 500
    
    except Exception as e:
        logger.error(f"Exception in /api/config/prompts POST: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/config/rules", methods=["GET"])
@requires_auth
def get_rules():
    """Get all rules and conditions"""
    try:
        rules = config_manager.get_rules()
        return jsonify({
            "status": "success",
            "rules": rules,
            "last_updated": config_manager.get_config().get("last_updated", "")
        }), 200
    except Exception as e:
        logger.error(f"Exception in /api/config/rules GET: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/config/rules", methods=["POST"])
@requires_auth
def update_rules():
    """Update rules and conditions"""
    try:
        data = request.get_json()
        if "rules" not in data:
            return jsonify({"error": "No 'rules' field provided"}), 400
        
        if config_manager.update_rules(data["rules"]):
            return jsonify({
                "status": "success",
                "message": "Rules updated successfully",
                "last_updated": config_manager.get_config().get("last_updated", "")
            }), 200
        else:
            return jsonify({"error": "Failed to update rules"}), 500
    
    except Exception as e:
        logger.error(f"Exception in /api/config/rules POST: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/config/reload", methods=["POST"])
@requires_auth
def reload_config():
    """Reload configuration from file"""
    try:
        config_manager.reload_config()
        # Reload processor rules
        processor.rules = config_manager.get_prompts().get("main_prompt", processor.rules)
        return jsonify({
            "status": "success",
            "message": "Configuration reloaded successfully"
        }), 200
    except Exception as e:
        logger.error(f"Exception in /api/config/reload: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/config/response-fields", methods=["GET"])
@requires_auth
def get_response_fields():
    """Get response fields configuration"""
    try:
        config_manager.reload_config()
        config = config_manager.get_config()
        response_fields = config.get("response_fields", {})
        return jsonify(response_fields), 200
    except Exception as e:
        logger.error(f"Error getting response fields: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error getting response fields: {str(e)}"}), 500


@app.route("/api/config/response-fields", methods=["POST"])
@requires_auth
def update_response_fields():
    """Update response fields configuration"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        config_manager.reload_config()
        config = config_manager.get_config()
        
        # Update response_fields section
        if "response_fields" not in config:
            config["response_fields"] = {}
        
        if "enabled_fields" in data:
            config["response_fields"]["enabled_fields"] = data["enabled_fields"]
        
        if "description" in data:
            config["response_fields"]["description"] = data["description"]
        
        # Save configuration
        config_manager._config = config
        config_manager._save_config()
        
        return jsonify({
            "status": "success",
            "message": "Response fields configuration updated successfully"
        }), 200
    except Exception as e:
        logger.error(f"Error updating response fields: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Error updating response fields: {str(e)}"}), 500


@app.route("/api/users", methods=["GET"])
@requires_auth
def list_users_api():
    """List all users (API endpoint)"""
    try:
        auth = request.authorization
        user_role = auth_manager.get_user_role(auth.username)
        if user_role != "admin":
            return jsonify({"error": "Admin access required"}), 403
        
        users = auth_manager.list_users()
        return jsonify({"users": users}), 200
    except Exception as e:
        logger.error(f"Exception in /api/users GET: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/users", methods=["POST"])
@requires_auth
def add_user_api():
    """Add a new user (API endpoint)"""
    try:
        auth = request.authorization
        user_role = auth_manager.get_user_role(auth.username)
        if user_role != "admin":
            return jsonify({"error": "Admin access required"}), 403
        
        data = request.get_json()
        username = data.get("username")
        password = data.get("password")
        role = data.get("role", "user")
        
        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400
        
        if auth_manager.add_user(username, password, role):
            return jsonify({"message": f"User '{username}' added successfully"}), 201
        else:
            return jsonify({"error": f"User '{username}' already exists"}), 409
    except Exception as e:
        logger.error(f"Exception in /api/users POST: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    
    # Log server startup
    startup_logger.info(f"Starting Flask server on port {port}")
    startup_logger.info(f"Debug mode: {debug_mode}")
    startup_logger.info(f"Server will listen on 0.0.0.0:{port}")
    startup_logger.info("=" * 60)
    
    try:
        app.run(host="0.0.0.0", port=port, debug=debug_mode)
    except Exception as e:
        startup_logger.error(f"Failed to start server: {str(e)}\n{traceback.format_exc()}")
        logger.error(f"Failed to start server: {str(e)}\n{traceback.format_exc()}")
        raise

