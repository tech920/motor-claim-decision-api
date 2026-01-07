<<<<<<< HEAD
"""
Unified REST API Server for Motor Claim Decision System (CO + TP)
Provides HTTP endpoints to process claims via Ollama for both Comprehensive and Third Party
All services run on port 5000
"""

from flask import Flask, request, jsonify, Response
import os
import json
import base64
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import traceback
import sys

# Add both CO and TP directories to path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CO_DIR = os.path.join(BASE_DIR, "MotorclaimdecisionlinuxCO")
TP_DIR = os.path.join(BASE_DIR, "MotorclaimdecisionlinuxTP")

# Import CO modules
sys.path.insert(0, CO_DIR)
# Change to CO directory so AuthManager can find users.json
original_cwd = os.getcwd()
os.chdir(CO_DIR)
from claim_processor import ClaimProcessor as COClaimProcessor
from excel_ocr_license_processor import ExcelOCRLicenseProcessor as COExcelOCRLicenseProcessor
from unified_processor import UnifiedClaimProcessor as COUnifiedClaimProcessor
from auth_manager import AuthManager
from config_manager import ConfigManager

# Create CO config manager with explicit path to CO directory
co_config_file = os.path.join(CO_DIR, "claim_config.json")
co_config_manager = ConfigManager(config_file=co_config_file)

# Verify CO config manager is using correct file (use print for startup messages)
if co_config_manager.config_file != co_config_file:
    print(f"ERROR: CO Config Manager file mismatch! Expected: {co_config_file}, Got: {co_config_manager.config_file}")
else:
    print(f"✓ CO Config Manager initialized with file: {co_config_file}")

# Create CO auth manager with explicit path
co_auth_manager = AuthManager(users_file=os.path.join(CO_DIR, "users.json"))

# Import TP modules
os.chdir(TP_DIR)
from claim_processor import ClaimProcessor as TPClaimProcessor
from excel_ocr_license_processor import ExcelOCRLicenseProcessor as TPExcelOCRLicenseProcessor
from unified_processor import UnifiedClaimProcessor as TPUnifiedClaimProcessor
from auth_manager import AuthManager as TPAuthManager
from config_manager import ConfigManager as TPConfigManager

# Create TP config manager with explicit path to TP directory
tp_config_file = os.path.join(TP_DIR, "claim_config.json")
tp_config_manager = TPConfigManager(config_file=tp_config_file)

# Verify TP config manager is using correct file (use print for startup messages)
if tp_config_manager.config_file != tp_config_file:
    print(f"ERROR: TP Config Manager file mismatch! Expected: {tp_config_file}, Got: {tp_config_manager.config_file}")
else:
    print(f"✓ TP Config Manager initialized with file: {tp_config_file}")

# Create TP auth manager with explicit path
tp_auth_manager = TPAuthManager(users_file=os.path.join(TP_DIR, "users.json"))

# Restore original working directory
os.chdir(original_cwd)

# Final verification - ensure files are different (use print for startup messages)
if co_config_file == tp_config_file:
    print(f"CRITICAL: CO and TP config files are the same! Both pointing to: {co_config_file}")
else:
    print(f"✓ Config files verified: CO={co_config_file}, TP={tp_config_file}")

# Verify files exist (use print for startup messages)
if os.path.exists(co_config_file):
    print(f"✓ CO config file exists: {co_config_file}")
else:
    print(f"⚠ WARNING: CO config file NOT found: {co_config_file}")

if os.path.exists(tp_config_file):
    print(f"✓ TP config file exists: {tp_config_file}")
else:
    print(f"⚠ WARNING: TP config file NOT found: {tp_config_file}")

app = Flask(__name__)

# Setup logging configuration
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Daily transaction log file - logs all API requests
current_date = datetime.now().strftime('%Y-%m-%d')
TRANSACTION_LOG_FILE = os.path.join(LOG_DIR, f"api_transactions_unified_{current_date}.log")

# Transaction logger
transaction_logger = logging.getLogger("transaction_unified")
transaction_logger.setLevel(logging.INFO)
transaction_handler = TimedRotatingFileHandler(
    TRANSACTION_LOG_FILE,
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8',
    utc=False
)
transaction_formatter = logging.Formatter(
    '%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
transaction_handler.setFormatter(transaction_formatter)
transaction_handler.suffix = '%Y-%m-%d'
transaction_logger.addHandler(transaction_handler)
transaction_logger.propagate = False

_last_log_date = current_date

# Error log file
ERROR_LOG_FILE = os.path.join(LOG_DIR, "error.log")
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
    ]
)

logger = logging.getLogger(__name__)

# Log config manager initialization (after logger is set up)
logger.info(f"CO Config Manager file: {co_config_manager.config_file}")
logger.info(f"TP Config Manager file: {tp_config_manager.config_file}")
if co_config_manager.config_file != tp_config_manager.config_file:
    logger.info("✓ CO and TP config managers are using different files")
else:
    logger.error("CRITICAL: CO and TP config managers are using the same file!")

# Initialize processors
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TRANSLATION_MODEL = os.getenv("OLLAMA_TRANSLATION_MODEL", "llama3.2:latest")

# CO Processors
co_processor = COClaimProcessor(
    ollama_base_url=OLLAMA_URL,
    model_name=OLLAMA_MODEL,
    translation_model=OLLAMA_TRANSLATION_MODEL
)
co_ocr_license_processor = COExcelOCRLicenseProcessor()
co_unified_processor = COUnifiedClaimProcessor()

# TP Processors
tp_processor = TPClaimProcessor(
    ollama_base_url=OLLAMA_URL,
    model_name=OLLAMA_MODEL,
    translation_model=OLLAMA_TRANSLATION_MODEL
)
tp_ocr_license_processor = TPExcelOCRLicenseProcessor()
tp_unified_processor = TPUnifiedClaimProcessor()

# Request logging middleware
@app.before_request
def log_request_info():
    """Log all incoming requests"""
    global _last_log_date
    
    current_date = datetime.now().strftime('%Y-%m-%d')
    if current_date != _last_log_date:
        new_log_file = os.path.join(LOG_DIR, f"api_transactions_unified_{current_date}.log")
        transaction_logger.handlers.clear()
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
    
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    if ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    
    method = request.method
    path = request.path
    user_agent = request.headers.get('User-Agent', 'unknown')
    
    transaction_logger.info(
        f"UNIFIED | REQUEST | {method} | {path} | IP: {client_ip} | User-Agent: {user_agent[:100]}"
    )

@app.after_request
def log_response_info(response):
    """Log all outgoing responses and add CORS headers"""
    method = request.method
    path = request.path
    status_code = response.status_code
    
    # Add CORS headers to allow cross-origin requests
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    
    # Handle OPTIONS preflight requests
    if method == 'OPTIONS':
        response.status_code = 200
    
    try:
        response_size = len(response.get_data())
    except:
        response_size = 0
    
    transaction_logger.info(
        f"UNIFIED | RESPONSE | {method} | {path} | Status: {status_code} | Size: {response_size} bytes"
    )
    
    return response

# Authentication decorator
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            auth = request.authorization
            if not auth:
                return Response(
                    'Could not verify your access level for that URL.\n'
                    'You have to login with proper credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Login Required"'}
                )
            
            # Check both CO and TP auth managers
            # Use verify_user method (not verify_password)
            try:
                co_verified = co_auth_manager.verify_user(auth.username, auth.password)
            except Exception as e:
                logger.error(f"Error verifying CO auth: {str(e)}")
                transaction_logger.error(f"AUTH_CO_ERROR | {str(e)}")
                co_verified = False
            
            try:
                tp_verified = tp_auth_manager.verify_user(auth.username, auth.password)
            except Exception as e:
                logger.error(f"Error verifying TP auth: {str(e)}")
                transaction_logger.error(f"AUTH_TP_ERROR | {str(e)}")
                tp_verified = False
            
            if not co_verified and not tp_verified:
                return Response(
                    'Could not verify your access level for that URL.\n'
                    'You have to login with proper credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Login Required"'}
                )
            
            return f(*args, **kwargs)
        except Exception as e:
            error_msg = f"Authentication error: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            transaction_logger.error(f"AUTH_ERROR | {error_msg}")
            return Response(
                f'Authentication error: {str(e)}', 500,
                {'Content-Type': 'text/plain'}
            )
    return decorated

@app.route("/health", methods=["GET", "POST", "OPTIONS"])
def health_check():
    """Health check endpoint - supports GET, POST, and OPTIONS for CORS"""
    return jsonify({
        "status": "healthy",
        "service": "unified",
        "ollama_url": OLLAMA_URL,
        "decision_model": OLLAMA_MODEL,
        "translation_model": OLLAMA_TRANSLATION_MODEL,
        "co_available": True,
        "tp_available": True
    }), 200

@app.route("/api/health", methods=["GET", "POST", "OPTIONS"])
def api_health_check():
    """Alternative health check endpoint"""
    return health_check()

@app.route("/", methods=["GET"])
@requires_auth
def index():
    """Serve unified web interface"""
    try:
        # Try multiple possible paths
        # Get the actual working directory from the service
        working_dir = os.getenv("WORKING_DIRECTORY", BASE_DIR)
        if not working_dir:
            working_dir = BASE_DIR
        
        possible_paths = [
            os.path.join(working_dir, "unified_web_interface.html"),
            os.path.join(BASE_DIR, "unified_web_interface.html"),
            "unified_web_interface.html",
            os.path.join(os.getcwd(), "unified_web_interface.html"),
            "/opt/Motorclaimdecision_main/unified_web_interface.html"
        ]
        
        html_path = None
        for path in possible_paths:
            if os.path.exists(path) and os.path.isfile(path):
                html_path = path
                break
        
        if not html_path:
            error_msg = f"HTML file not found. Tried: {', '.join(possible_paths)}"
            logger.error(error_msg)
            transaction_logger.error(f"WEB_INTERFACE_NOT_FOUND | {error_msg}")
            # Return a simple HTML error page instead of JSON
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Web Interface Not Found</title></head>
<body>
    <h1>Web Interface Not Found</h1>
    <p>The web interface file (unified_web_interface.html) could not be found.</p>
    <p>Please ensure the file exists in the project directory.</p>
    <p>Tried paths: {', '.join(possible_paths)}</p>
    <p>Current directory: {os.getcwd()}</p>
    <p>BASE_DIR: {BASE_DIR}</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 404
        
        # Check if file is readable
        if not os.access(html_path, os.R_OK):
            error_msg = f"HTML file exists but is not readable: {html_path}"
            logger.error(error_msg)
            transaction_logger.error(f"WEB_INTERFACE_NOT_READABLE | {error_msg}")
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>The web interface file exists but cannot be read.</p>
    <p>File: {html_path}</p>
    <p>Please check file permissions.</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 403
        
        # Read and return the HTML file
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            transaction_logger.info(f"WEB_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes")
            return Response(content, mimetype="text/html")
        except UnicodeDecodeError as e:
            error_msg = f"Unicode decode error reading HTML file: {str(e)}"
            logger.error(error_msg)
            transaction_logger.error(f"WEB_INTERFACE_UNICODE_ERROR | {error_msg}")
            # Try with different encoding
            try:
                with open(html_path, "r", encoding="latin-1") as f:
                    content = f.read()
                transaction_logger.info(f"WEB_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes (latin-1)")
                return Response(content, mimetype="text/html")
            except Exception as e2:
                raise Exception(f"Failed to read file with both utf-8 and latin-1: {str(e2)}")
        
    except PermissionError as e:
        error_msg = f"Permission denied reading HTML file: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"WEB_INTERFACE_PERMISSION_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>Permission denied when trying to read the web interface file.</p>
    <p>Error: {str(e)}</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 403
        
    except Exception as e:
        error_msg = f"Error loading web interface: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"WEB_INTERFACE_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Internal Server Error</title></head>
<body>
    <h1>Internal Server Error</h1>
    <p>An error occurred while loading the web interface.</p>
    <p>Error: {str(e)}</p>
    <p>Please check the server logs for more details.</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 500

@app.route("/config", methods=["GET"])
@requires_auth
def config_page():
    """Serve unified configuration interface with tabs"""
    try:
        # Try multiple possible paths
        working_dir = os.getenv("WORKING_DIRECTORY", BASE_DIR)
        if not working_dir:
            working_dir = BASE_DIR
        
        possible_paths = [
            os.path.join(working_dir, "unified_config_interface.html"),
            os.path.join(BASE_DIR, "unified_config_interface.html"),
            "unified_config_interface.html",
            os.path.join(os.getcwd(), "unified_config_interface.html"),
            "/opt/Motorclaimdecision_main/unified_config_interface.html"
        ]
        
        html_path = None
        for path in possible_paths:
            if os.path.exists(path) and os.path.isfile(path):
                html_path = path
                break
        
        if not html_path:
            error_msg = f"Config HTML file not found. Tried: {', '.join(possible_paths)}"
            logger.error(error_msg)
            transaction_logger.error(f"CONFIG_INTERFACE_NOT_FOUND | {error_msg}")
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Configuration Interface Not Found</title></head>
<body>
    <h1>Configuration Interface Not Found</h1>
    <p>The configuration interface file (unified_config_interface.html) could not be found.</p>
    <p>Please ensure the file exists in the project directory.</p>
    <p>Tried paths: {', '.join(possible_paths)}</p>
    <p>Current directory: {os.getcwd()}</p>
    <p>BASE_DIR: {BASE_DIR}</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 404
        
        # Check if file is readable
        if not os.access(html_path, os.R_OK):
            error_msg = f"Config HTML file exists but is not readable: {html_path}"
            logger.error(error_msg)
            transaction_logger.error(f"CONFIG_INTERFACE_NOT_READABLE | {error_msg}")
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>The configuration interface file exists but cannot be read.</p>
    <p>File: {html_path}</p>
    <p>Please check file permissions.</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 403
        
        # Read and return the HTML file
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            transaction_logger.info(f"CONFIG_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes")
            return Response(content, mimetype="text/html")
        except UnicodeDecodeError as e:
            error_msg = f"Unicode decode error reading config HTML file: {str(e)}"
            logger.error(error_msg)
            transaction_logger.error(f"CONFIG_INTERFACE_UNICODE_ERROR | {error_msg}")
            # Try with different encoding
            try:
                with open(html_path, "r", encoding="latin-1") as f:
                    content = f.read()
                transaction_logger.info(f"CONFIG_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes (latin-1)")
                return Response(content, mimetype="text/html")
            except Exception as e2:
                raise Exception(f"Failed to read config file with both utf-8 and latin-1: {str(e2)}")
    except PermissionError as e:
        error_msg = f"Permission denied reading config HTML file: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"CONFIG_INTERFACE_PERMISSION_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>Permission denied when trying to read the configuration interface file.</p>
    <p>Error: {str(e)}</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 403
    except Exception as e:
        error_msg = f"Error loading config interface: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"CONFIG_INTERFACE_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Internal Server Error</title></head>
<body>
    <h1>Internal Server Error</h1>
    <p>An error occurred while loading the configuration interface.</p>
    <p>Error: {str(e)}</p>
    <p>Please check the server logs for more details.</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 500

@app.route("/process-claim-simplified", methods=["POST"])
@requires_auth
def process_claim_simplified():
    """
    MAIN ROUTER: Receives all requests and routes to CO or TP path based on claim_type
    
    This is the main entry point that:
    1. Receives all claim processing requests
    2. Validates claim_type parameter
    3. Routes to CO path if claim_type = "CO"
    4. Routes to TP path if claim_type = "TP"
    5. All functionality comes from the respective path directory
    
    Required parameters:
    - claim_type: "CO" or "TP" (mandatory) - determines which path to use
    
    Optional per-party:
    - insurance_type: "CO" or "TP" (optional) - if not provided, uses claim_type
    """
    try:
        # Log incoming request
        transaction_logger.info(
            f"MAIN_ROUTER_REQUEST | Method: {request.method} | "
            f"IP: {request.remote_addr} | "
            f"User-Agent: {request.headers.get('User-Agent', 'Unknown')[:100]}"
        )
        
        data = request.get_json()
        
        if not data:
            error_msg = "No data provided"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
        
        if "Parties" not in data or not isinstance(data["Parties"], list):
            error_msg = "Invalid structure: 'Parties' array is required"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
        
        # Get claim_type (mandatory) - THIS DETERMINES THE ROUTING
        claim_type = data.get("claim_type", "").upper().strip()
        if not claim_type or claim_type not in ["CO", "TP"]:
            error_msg = "Missing or invalid 'claim_type' parameter. Required: 'CO' or 'TP'"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
        
        # Extract case_number for logging (optional field)
        case_number = data.get("Case_Number", "Unknown")
        
        # MAIN ROUTER: Route to appropriate path based on claim_type
        transaction_logger.info(
            f"MAIN_ROUTER_ROUTING | Claim_Type: {claim_type} | Case: {case_number} | "
            f"CO_Path: {CO_DIR} | TP_Path: {TP_DIR} | "
            f"Routing_to: {'CO_PATH' if claim_type == 'CO' else 'TP_PATH'}"
        )
        
        # Route to CO path - ALL processing in MotorclaimdecisionlinuxCO/
        if claim_type == "CO":
            transaction_logger.info(
                f"ROUTING_TO_CO_PATH | Case: {case_number} | "
                f"CO_Directory: {CO_DIR} | "
                f"Importing: MotorclaimdecisionlinuxCO.claim_processor_api"
            )
            # Import and call CO processing module using importlib to ensure correct path
            import importlib.util
            import importlib
            
            # Clear any cached modules to ensure fresh import
            # CRITICAL: Clear ALL claim_processor, config_manager, unified_processor modules
            # to prevent loading wrong modules from root or TP directory
            modules_to_clear = [
                k for k in list(sys.modules.keys())
                if any(x in k for x in [
                    'claim_processor_api', 'co_claim_processor_api',
                    'claim_processor', 'config_manager', 'unified_processor',
                    'excel_ocr_license_processor', 'MotorclaimdecisionlinuxCO',
                    'MotorclaimdecisionlinuxTP.claim_processor'  # Clear TP modules too
                ]) and 'MotorclaimdecisionlinuxCO' not in k
            ]
            for mod in modules_to_clear:
                try:
                    del sys.modules[mod]
                except:
                    pass
            
            co_module_path = os.path.join(CO_DIR, "claim_processor_api.py")
            if not os.path.exists(co_module_path):
                error_msg = f"CO module not found: {co_module_path}"
                transaction_logger.error(f"MAIN_ROUTER_ERROR | {error_msg}")
                return jsonify({"error": error_msg}), 500
            
            # Use unique module name with timestamp to avoid cache conflicts
            import time
            unique_name = f"co_claim_processor_api_{int(time.time() * 1000000)}"
            spec = importlib.util.spec_from_file_location(unique_name, co_module_path)
            co_module = importlib.util.module_from_spec(spec)
            
            # Temporarily change to CO directory and modify sys.path for relative imports
            original_cwd = os.getcwd()
            original_path = sys.path[:]
            try:
                os.chdir(CO_DIR)
                # Ensure CO directory is first in path for relative imports
                sys.path.insert(0, CO_DIR)
                spec.loader.exec_module(co_module)
            finally:
                os.chdir(original_cwd)
                sys.path[:] = original_path
            
            return co_module.process_co_claim(data)
        
        # Route to TP path - ALL processing in MotorclaimdecisionlinuxTP/
        elif claim_type == "TP":
            transaction_logger.info(
                f"ROUTING_TO_TP_PATH | Case: {case_number} | "
                f"TP_Directory: {TP_DIR} | "
                f"Importing: MotorclaimdecisionlinuxTP.claim_processor_api"
            )
            # Import and call TP processing module using importlib to ensure correct path
            import importlib.util
            import importlib
            
            # Clear any cached modules to ensure fresh import
            # CRITICAL: Clear ALL claim_processor, config_manager, unified_processor modules
            # to prevent loading wrong modules from root or CO directory
            modules_to_clear = [
                k for k in list(sys.modules.keys())
                if any(x in k for x in [
                    'claim_processor_api', 'tp_claim_processor_api',
                    'claim_processor', 'config_manager', 'unified_processor',
                    'excel_ocr_license_processor', 'MotorclaimdecisionlinuxTP',
                    'MotorclaimdecisionlinuxCO.claim_processor'  # Clear CO modules too
                ]) and 'MotorclaimdecisionlinuxTP' not in k
            ]
            for mod in modules_to_clear:
                try:
                    del sys.modules[mod]
                except:
                    pass
            
            tp_module_path = os.path.join(TP_DIR, "claim_processor_api.py")
            if not os.path.exists(tp_module_path):
                error_msg = f"TP module not found: {tp_module_path}"
                transaction_logger.error(f"MAIN_ROUTER_ERROR | {error_msg}")
                return jsonify({"error": error_msg}), 500
            
            # Use unique module name with timestamp to avoid cache conflicts
            import time
            unique_name = f"tp_claim_processor_api_{int(time.time() * 1000000)}"
            spec = importlib.util.spec_from_file_location(unique_name, tp_module_path)
            tp_module = importlib.util.module_from_spec(spec)
            
            # Temporarily change to TP directory and modify sys.path for relative imports
            original_cwd = os.getcwd()
            original_path = sys.path[:]
            try:
                os.chdir(TP_DIR)
                # Ensure TP directory is first in path for relative imports
                sys.path.insert(0, TP_DIR)
                spec.loader.exec_module(tp_module)
            finally:
                os.chdir(original_cwd)
                sys.path[:] = original_path
            
            return tp_module.process_tp_claim(data)
        else:
            error_msg = f"Invalid claim_type: {claim_type}. Must be 'CO' or 'TP'"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
    
    except Exception as e:
        error_msg = f"Main router error: {str(e)[:200]}"
        transaction_logger.error(
            f"MAIN_ROUTER_EXCEPTION | Error: {error_msg} | "
            f"Traceback: {traceback.format_exc()[:500]}"
        )
        return jsonify({"error": "Internal server error", "details": error_msg}), 500

# Configuration endpoints for CO
@app.route("/api/config/co/prompts", methods=["GET", "POST"])
@requires_auth
def co_config_prompts():
    """CO prompts configuration - reads from CO directory"""
    # Ensure we're reading from CO directory
    co_config_file = os.path.join(CO_DIR, "claim_config.json")
    if not os.path.exists(co_config_file):
        return jsonify({"error": f"CO config file not found: {co_config_file}"}), 404
    
    if request.method == "GET":
        # Verify we're using the correct config manager
        if co_config_manager.config_file != co_config_file:
            logger.error(f"CO Config Manager file mismatch in prompts endpoint! Expected: {co_config_file}, Got: {co_config_manager.config_file}")
            return jsonify({"error": f"Configuration error: CO config manager using wrong file"}), 500
        
        # Reload config to ensure latest data from CO file
        co_config_manager.reload_config()
        prompts = co_config_manager.get_prompts()
        logger.info(f"CO Prompts loaded from: {co_config_file} | Config file verified: {co_config_manager.config_file}")
        return jsonify(prompts), 200
    else:
        data = request.get_json()
        if "prompts" not in data:
            return jsonify({"error": "No 'prompts' field provided"}), 400
        # Reload before update
        co_config_manager.reload_config()
        if co_config_manager.update_prompts(data["prompts"]):
            logger.info(f"CO Prompts saved to: {co_config_file}")
            return jsonify({"status": "success", "message": "CO prompts updated successfully"}), 200
        return jsonify({"error": "Failed to update CO prompts"}), 500

@app.route("/api/config/co/rules", methods=["GET", "POST"])
@requires_auth
def co_config_rules():
    """CO rules configuration - reads from CO directory"""
    # Ensure we're reading from CO directory
    co_config_file = os.path.join(CO_DIR, "claim_config.json")
    if not os.path.exists(co_config_file):
        return jsonify({"error": f"CO config file not found: {co_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from CO file
        co_config_manager.reload_config()
        config = co_config_manager.get_config()
        rules = config.get("rules", {})
        processing_filters = config.get("processing_filters", {})
        logger.info(f"CO Rules loaded from: {co_config_file}")
        return jsonify({
            "rules": rules,
            "processing_filters": processing_filters
        }), 200
    else:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Reload before update
        co_config_manager.reload_config()
        success = True
        
        # Update rules if provided
        if "rules" in data:
            if not co_config_manager.update_rules(data["rules"]):
                success = False
        
        # Update processing_filters if provided
        if "processing_filters" in data:
            if not co_config_manager.update_processing_filters(data["processing_filters"]):
                success = False
        
        if success:
            logger.info(f"CO Rules/Processing Filters saved to: {co_config_file}")
            return jsonify({"status": "success", "message": "CO rules and processing filters updated successfully"}), 200
        return jsonify({"error": "Failed to update CO rules/processing filters"}), 500

@app.route("/api/config/co/response-fields", methods=["GET", "POST"])
@requires_auth
def co_config_response_fields():
    """CO response fields configuration - reads from CO directory"""
    # Ensure we're reading from CO directory
    co_config_file = os.path.join(CO_DIR, "claim_config.json")
    if not os.path.exists(co_config_file):
        return jsonify({"error": f"CO config file not found: {co_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from CO file
        co_config_manager.reload_config()
        config = co_config_manager.get_config()
        response_fields = config.get("response_fields", {})
        logger.info(f"CO Response Fields loaded from: {co_config_file}")
        return jsonify(response_fields), 200
    else:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        # Reload before update
        co_config_manager.reload_config()
        config = co_config_manager.get_config()
        if "response_fields" not in config:
            config["response_fields"] = {}
        if "enabled_fields" in data:
            config["response_fields"]["enabled_fields"] = data["enabled_fields"]
        if "description" in data:
            config["response_fields"]["description"] = data["description"]
        co_config_manager._config = config
        co_config_manager._save_config()
        logger.info(f"CO Response Fields saved to: {co_config_file}")
        return jsonify({"status": "success", "message": "CO response fields updated successfully"}), 200

# Configuration endpoints for TP
@app.route("/api/config/tp/prompts", methods=["GET", "POST"])
@requires_auth
def tp_config_prompts():
    """TP prompts configuration - reads from TP directory"""
    # Ensure we're reading from TP directory
    tp_config_file = os.path.join(TP_DIR, "claim_config.json")
    if not os.path.exists(tp_config_file):
        return jsonify({"error": f"TP config file not found: {tp_config_file}"}), 404
    
    if request.method == "GET":
        # Verify we're using the correct config manager
        if tp_config_manager.config_file != tp_config_file:
            logger.error(f"TP Config Manager file mismatch in prompts endpoint! Expected: {tp_config_file}, Got: {tp_config_manager.config_file}")
            return jsonify({"error": f"Configuration error: TP config manager using wrong file"}), 500
        
        # Reload config to ensure latest data from TP file
        tp_config_manager.reload_config()
        prompts = tp_config_manager.get_prompts()
        logger.info(f"TP Prompts loaded from: {tp_config_file} | Config file verified: {tp_config_manager.config_file}")
        return jsonify(prompts), 200
    else:
        data = request.get_json()
        if "prompts" not in data:
            return jsonify({"error": "No 'prompts' field provided"}), 400
        # Reload before update
        tp_config_manager.reload_config()
        if tp_config_manager.update_prompts(data["prompts"]):
            logger.info(f"TP Prompts saved to: {tp_config_file}")
            return jsonify({"status": "success", "message": "TP prompts updated successfully"}), 200
        return jsonify({"error": "Failed to update TP prompts"}), 500

@app.route("/api/config/tp/rules", methods=["GET", "POST"])
@requires_auth
def tp_config_rules():
    """TP rules configuration - reads from TP directory"""
    # Ensure we're reading from TP directory
    tp_config_file = os.path.join(TP_DIR, "claim_config.json")
    if not os.path.exists(tp_config_file):
        return jsonify({"error": f"TP config file not found: {tp_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from TP file
        tp_config_manager.reload_config()
        rules = tp_config_manager.get_rules()
        logger.info(f"TP Rules loaded from: {tp_config_file}")
        return jsonify({"rules": rules}), 200
    else:
        data = request.get_json()
        if "rules" not in data:
            return jsonify({"error": "No 'rules' field provided"}), 400
        # Reload before update
        tp_config_manager.reload_config()
        if tp_config_manager.update_rules(data["rules"]):
            logger.info(f"TP Rules saved to: {tp_config_file}")
            return jsonify({"status": "success", "message": "TP rules updated successfully"}), 200
        return jsonify({"error": "Failed to update TP rules"}), 500

@app.route("/api/config/tp/response-fields", methods=["GET", "POST"])
@requires_auth
def tp_config_response_fields():
    """TP response fields configuration - reads from TP directory"""
    # Ensure we're reading from TP directory
    tp_config_file = os.path.join(TP_DIR, "claim_config.json")
    if not os.path.exists(tp_config_file):
        return jsonify({"error": f"TP config file not found: {tp_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from TP file
        tp_config_manager.reload_config()
        config = tp_config_manager.get_config()
        response_fields = config.get("response_fields", {})
        logger.info(f"TP Response Fields loaded from: {tp_config_file}")
        return jsonify(response_fields), 200
    else:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        # Reload before update
        tp_config_manager.reload_config()
        config = tp_config_manager.get_config()
        if "response_fields" not in config:
            config["response_fields"] = {}
        if "enabled_fields" in data:
            config["response_fields"]["enabled_fields"] = data["enabled_fields"]
        if "description" in data:
            config["response_fields"]["description"] = data["description"]
        tp_config_manager._config = config
        tp_config_manager._save_config()
        logger.info(f"TP Response Fields saved to: {tp_config_file}")
        return jsonify({"status": "success", "message": "TP response fields updated successfully"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    
    print(f"Starting Unified Motor Claim Decision API Server on port {port}")
    print(f"CO and TP services available on single endpoint")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
=======
"""
Unified REST API Server for Motor Claim Decision System (CO + TP)
Provides HTTP endpoints to process claims via Ollama for both Comprehensive and Third Party
All services run on port 5000
"""

from flask import Flask, request, jsonify, Response
import os
import json
import base64
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import traceback
import sys

# Add both CO and TP directories to path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CO_DIR = os.path.join(BASE_DIR, "MotorclaimdecisionlinuxCO")
TP_DIR = os.path.join(BASE_DIR, "MotorclaimdecisionlinuxTP")

# Import CO modules
sys.path.insert(0, CO_DIR)
# Change to CO directory so AuthManager can find users.json
original_cwd = os.getcwd()
os.chdir(CO_DIR)
from claim_processor import ClaimProcessor as COClaimProcessor
from excel_ocr_license_processor import ExcelOCRLicenseProcessor as COExcelOCRLicenseProcessor
from unified_processor import UnifiedClaimProcessor as COUnifiedClaimProcessor
from auth_manager import AuthManager
from config_manager import ConfigManager

# Create CO config manager with explicit path to CO directory
co_config_file = os.path.join(CO_DIR, "claim_config.json")
co_config_manager = ConfigManager(config_file=co_config_file)

# Verify CO config manager is using correct file (use print for startup messages)
if co_config_manager.config_file != co_config_file:
    print(f"ERROR: CO Config Manager file mismatch! Expected: {co_config_file}, Got: {co_config_manager.config_file}")
else:
    print(f"✓ CO Config Manager initialized with file: {co_config_file}")

# Create CO auth manager with explicit path
co_auth_manager = AuthManager(users_file=os.path.join(CO_DIR, "users.json"))

# Import TP modules
os.chdir(TP_DIR)
from claim_processor import ClaimProcessor as TPClaimProcessor
from excel_ocr_license_processor import ExcelOCRLicenseProcessor as TPExcelOCRLicenseProcessor
from unified_processor import UnifiedClaimProcessor as TPUnifiedClaimProcessor
from auth_manager import AuthManager as TPAuthManager
from config_manager import ConfigManager as TPConfigManager

# Create TP config manager with explicit path to TP directory
tp_config_file = os.path.join(TP_DIR, "claim_config.json")
tp_config_manager = TPConfigManager(config_file=tp_config_file)

# Verify TP config manager is using correct file (use print for startup messages)
if tp_config_manager.config_file != tp_config_file:
    print(f"ERROR: TP Config Manager file mismatch! Expected: {tp_config_file}, Got: {tp_config_manager.config_file}")
else:
    print(f"✓ TP Config Manager initialized with file: {tp_config_file}")

# Create TP auth manager with explicit path
tp_auth_manager = TPAuthManager(users_file=os.path.join(TP_DIR, "users.json"))

# Restore original working directory
os.chdir(original_cwd)

# Final verification - ensure files are different (use print for startup messages)
if co_config_file == tp_config_file:
    print(f"CRITICAL: CO and TP config files are the same! Both pointing to: {co_config_file}")
else:
    print(f"✓ Config files verified: CO={co_config_file}, TP={tp_config_file}")

# Verify files exist (use print for startup messages)
if os.path.exists(co_config_file):
    print(f"✓ CO config file exists: {co_config_file}")
else:
    print(f"⚠ WARNING: CO config file NOT found: {co_config_file}")

if os.path.exists(tp_config_file):
    print(f"✓ TP config file exists: {tp_config_file}")
else:
    print(f"⚠ WARNING: TP config file NOT found: {tp_config_file}")

app = Flask(__name__)

# Setup logging configuration
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Daily transaction log file - logs all API requests
current_date = datetime.now().strftime('%Y-%m-%d')
TRANSACTION_LOG_FILE = os.path.join(LOG_DIR, f"api_transactions_unified_{current_date}.log")

# Transaction logger
transaction_logger = logging.getLogger("transaction_unified")
transaction_logger.setLevel(logging.INFO)
transaction_handler = TimedRotatingFileHandler(
    TRANSACTION_LOG_FILE,
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8',
    utc=False
)
transaction_formatter = logging.Formatter(
    '%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
transaction_handler.setFormatter(transaction_formatter)
transaction_handler.suffix = '%Y-%m-%d'
transaction_logger.addHandler(transaction_handler)
transaction_logger.propagate = False

_last_log_date = current_date

# Error log file
ERROR_LOG_FILE = os.path.join(LOG_DIR, "error.log")
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
    ]
)

logger = logging.getLogger(__name__)

# Log config manager initialization (after logger is set up)
logger.info(f"CO Config Manager file: {co_config_manager.config_file}")
logger.info(f"TP Config Manager file: {tp_config_manager.config_file}")
if co_config_manager.config_file != tp_config_manager.config_file:
    logger.info("✓ CO and TP config managers are using different files")
else:
    logger.error("CRITICAL: CO and TP config managers are using the same file!")

# Initialize processors
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TRANSLATION_MODEL = os.getenv("OLLAMA_TRANSLATION_MODEL", "llama3.2:latest")

# CO Processors
co_processor = COClaimProcessor(
    ollama_base_url=OLLAMA_URL,
    model_name=OLLAMA_MODEL,
    translation_model=OLLAMA_TRANSLATION_MODEL
)
co_ocr_license_processor = COExcelOCRLicenseProcessor()
co_unified_processor = COUnifiedClaimProcessor()

# TP Processors
tp_processor = TPClaimProcessor(
    ollama_base_url=OLLAMA_URL,
    model_name=OLLAMA_MODEL,
    translation_model=OLLAMA_TRANSLATION_MODEL
)
tp_ocr_license_processor = TPExcelOCRLicenseProcessor()
tp_unified_processor = TPUnifiedClaimProcessor()

# Request logging middleware
@app.before_request
def log_request_info():
    """Log all incoming requests"""
    global _last_log_date
    
    current_date = datetime.now().strftime('%Y-%m-%d')
    if current_date != _last_log_date:
        new_log_file = os.path.join(LOG_DIR, f"api_transactions_unified_{current_date}.log")
        transaction_logger.handlers.clear()
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
    
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    if ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    
    method = request.method
    path = request.path
    user_agent = request.headers.get('User-Agent', 'unknown')
    
    transaction_logger.info(
        f"UNIFIED | REQUEST | {method} | {path} | IP: {client_ip} | User-Agent: {user_agent[:100]}"
    )

@app.after_request
def log_response_info(response):
    """Log all outgoing responses and add CORS headers"""
    method = request.method
    path = request.path
    status_code = response.status_code
    
    # Add CORS headers to allow cross-origin requests
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    
    # Handle OPTIONS preflight requests
    if method == 'OPTIONS':
        response.status_code = 200
    
    try:
        response_size = len(response.get_data())
    except:
        response_size = 0
    
    transaction_logger.info(
        f"UNIFIED | RESPONSE | {method} | {path} | Status: {status_code} | Size: {response_size} bytes"
    )
    
    return response

# Authentication decorator
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            auth = request.authorization
            if not auth:
                return Response(
                    'Could not verify your access level for that URL.\n'
                    'You have to login with proper credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Login Required"'}
                )
            
            # Check both CO and TP auth managers
            # Use verify_user method (not verify_password)
            try:
                co_verified = co_auth_manager.verify_user(auth.username, auth.password)
            except Exception as e:
                logger.error(f"Error verifying CO auth: {str(e)}")
                transaction_logger.error(f"AUTH_CO_ERROR | {str(e)}")
                co_verified = False
            
            try:
                tp_verified = tp_auth_manager.verify_user(auth.username, auth.password)
            except Exception as e:
                logger.error(f"Error verifying TP auth: {str(e)}")
                transaction_logger.error(f"AUTH_TP_ERROR | {str(e)}")
                tp_verified = False
            
            if not co_verified and not tp_verified:
                return Response(
                    'Could not verify your access level for that URL.\n'
                    'You have to login with proper credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Login Required"'}
                )
            
            return f(*args, **kwargs)
        except Exception as e:
            error_msg = f"Authentication error: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            transaction_logger.error(f"AUTH_ERROR | {error_msg}")
            return Response(
                f'Authentication error: {str(e)}', 500,
                {'Content-Type': 'text/plain'}
            )
    return decorated

@app.route("/health", methods=["GET", "POST", "OPTIONS"])
def health_check():
    """Health check endpoint - supports GET, POST, and OPTIONS for CORS"""
    return jsonify({
        "status": "healthy",
        "service": "unified",
        "ollama_url": OLLAMA_URL,
        "decision_model": OLLAMA_MODEL,
        "translation_model": OLLAMA_TRANSLATION_MODEL,
        "co_available": True,
        "tp_available": True
    }), 200

@app.route("/api/health", methods=["GET", "POST", "OPTIONS"])
def api_health_check():
    """Alternative health check endpoint"""
    return health_check()

@app.route("/", methods=["GET"])
@requires_auth
def index():
    """Serve unified web interface"""
    try:
        # Try multiple possible paths
        # Get the actual working directory from the service
        working_dir = os.getenv("WORKING_DIRECTORY", BASE_DIR)
        if not working_dir:
            working_dir = BASE_DIR
        
        possible_paths = [
            os.path.join(working_dir, "unified_web_interface.html"),
            os.path.join(BASE_DIR, "unified_web_interface.html"),
            "unified_web_interface.html",
            os.path.join(os.getcwd(), "unified_web_interface.html"),
            "/opt/Motorclaimdecision_main/unified_web_interface.html"
        ]
        
        html_path = None
        for path in possible_paths:
            if os.path.exists(path) and os.path.isfile(path):
                html_path = path
                break
        
        if not html_path:
            error_msg = f"HTML file not found. Tried: {', '.join(possible_paths)}"
            logger.error(error_msg)
            transaction_logger.error(f"WEB_INTERFACE_NOT_FOUND | {error_msg}")
            # Return a simple HTML error page instead of JSON
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Web Interface Not Found</title></head>
<body>
    <h1>Web Interface Not Found</h1>
    <p>The web interface file (unified_web_interface.html) could not be found.</p>
    <p>Please ensure the file exists in the project directory.</p>
    <p>Tried paths: {', '.join(possible_paths)}</p>
    <p>Current directory: {os.getcwd()}</p>
    <p>BASE_DIR: {BASE_DIR}</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 404
        
        # Check if file is readable
        if not os.access(html_path, os.R_OK):
            error_msg = f"HTML file exists but is not readable: {html_path}"
            logger.error(error_msg)
            transaction_logger.error(f"WEB_INTERFACE_NOT_READABLE | {error_msg}")
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>The web interface file exists but cannot be read.</p>
    <p>File: {html_path}</p>
    <p>Please check file permissions.</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 403
        
        # Read and return the HTML file
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            transaction_logger.info(f"WEB_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes")
            return Response(content, mimetype="text/html")
        except UnicodeDecodeError as e:
            error_msg = f"Unicode decode error reading HTML file: {str(e)}"
            logger.error(error_msg)
            transaction_logger.error(f"WEB_INTERFACE_UNICODE_ERROR | {error_msg}")
            # Try with different encoding
            try:
                with open(html_path, "r", encoding="latin-1") as f:
                    content = f.read()
                transaction_logger.info(f"WEB_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes (latin-1)")
                return Response(content, mimetype="text/html")
            except Exception as e2:
                raise Exception(f"Failed to read file with both utf-8 and latin-1: {str(e2)}")
        
    except PermissionError as e:
        error_msg = f"Permission denied reading HTML file: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"WEB_INTERFACE_PERMISSION_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>Permission denied when trying to read the web interface file.</p>
    <p>Error: {str(e)}</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 403
        
    except Exception as e:
        error_msg = f"Error loading web interface: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"WEB_INTERFACE_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Internal Server Error</title></head>
<body>
    <h1>Internal Server Error</h1>
    <p>An error occurred while loading the web interface.</p>
    <p>Error: {str(e)}</p>
    <p>Please check the server logs for more details.</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 500

@app.route("/config", methods=["GET"])
@requires_auth
def config_page():
    """Serve unified configuration interface with tabs"""
    try:
        # Try multiple possible paths
        working_dir = os.getenv("WORKING_DIRECTORY", BASE_DIR)
        if not working_dir:
            working_dir = BASE_DIR
        
        possible_paths = [
            os.path.join(working_dir, "unified_config_interface.html"),
            os.path.join(BASE_DIR, "unified_config_interface.html"),
            "unified_config_interface.html",
            os.path.join(os.getcwd(), "unified_config_interface.html"),
            "/opt/Motorclaimdecision_main/unified_config_interface.html"
        ]
        
        html_path = None
        for path in possible_paths:
            if os.path.exists(path) and os.path.isfile(path):
                html_path = path
                break
        
        if not html_path:
            error_msg = f"Config HTML file not found. Tried: {', '.join(possible_paths)}"
            logger.error(error_msg)
            transaction_logger.error(f"CONFIG_INTERFACE_NOT_FOUND | {error_msg}")
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Configuration Interface Not Found</title></head>
<body>
    <h1>Configuration Interface Not Found</h1>
    <p>The configuration interface file (unified_config_interface.html) could not be found.</p>
    <p>Please ensure the file exists in the project directory.</p>
    <p>Tried paths: {', '.join(possible_paths)}</p>
    <p>Current directory: {os.getcwd()}</p>
    <p>BASE_DIR: {BASE_DIR}</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 404
        
        # Check if file is readable
        if not os.access(html_path, os.R_OK):
            error_msg = f"Config HTML file exists but is not readable: {html_path}"
            logger.error(error_msg)
            transaction_logger.error(f"CONFIG_INTERFACE_NOT_READABLE | {error_msg}")
            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>The configuration interface file exists but cannot be read.</p>
    <p>File: {html_path}</p>
    <p>Please check file permissions.</p>
</body>
</html>"""
            return Response(error_html, mimetype="text/html"), 403
        
        # Read and return the HTML file
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            transaction_logger.info(f"CONFIG_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes")
            return Response(content, mimetype="text/html")
        except UnicodeDecodeError as e:
            error_msg = f"Unicode decode error reading config HTML file: {str(e)}"
            logger.error(error_msg)
            transaction_logger.error(f"CONFIG_INTERFACE_UNICODE_ERROR | {error_msg}")
            # Try with different encoding
            try:
                with open(html_path, "r", encoding="latin-1") as f:
                    content = f.read()
                transaction_logger.info(f"CONFIG_INTERFACE_SERVED | Path: {html_path} | Size: {len(content)} bytes (latin-1)")
                return Response(content, mimetype="text/html")
            except Exception as e2:
                raise Exception(f"Failed to read config file with both utf-8 and latin-1: {str(e2)}")
    except PermissionError as e:
        error_msg = f"Permission denied reading config HTML file: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"CONFIG_INTERFACE_PERMISSION_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Permission Error</title></head>
<body>
    <h1>Permission Error</h1>
    <p>Permission denied when trying to read the configuration interface file.</p>
    <p>Error: {str(e)}</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 403
    except Exception as e:
        error_msg = f"Error loading config interface: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        transaction_logger.error(f"CONFIG_INTERFACE_ERROR | {error_msg}")
        error_html = f"""<!DOCTYPE html>
<html>
<head><title>Internal Server Error</title></head>
<body>
    <h1>Internal Server Error</h1>
    <p>An error occurred while loading the configuration interface.</p>
    <p>Error: {str(e)}</p>
    <p>Please check the server logs for more details.</p>
</body>
</html>"""
        return Response(error_html, mimetype="text/html"), 500

@app.route("/process-claim-simplified", methods=["POST"])
@requires_auth
def process_claim_simplified():
    """
    MAIN ROUTER: Receives all requests and routes to CO or TP path based on claim_type
    
    This is the main entry point that:
    1. Receives all claim processing requests
    2. Validates claim_type parameter
    3. Routes to CO path if claim_type = "CO"
    4. Routes to TP path if claim_type = "TP"
    5. All functionality comes from the respective path directory
    
    Required parameters:
    - claim_type: "CO" or "TP" (mandatory) - determines which path to use
    
    Optional per-party:
    - insurance_type: "CO" or "TP" (optional) - if not provided, uses claim_type
    """
    try:
        # Log incoming request
        transaction_logger.info(
            f"MAIN_ROUTER_REQUEST | Method: {request.method} | "
            f"IP: {request.remote_addr} | "
            f"User-Agent: {request.headers.get('User-Agent', 'Unknown')[:100]}"
        )
        
        data = request.get_json()
        
        if not data:
            error_msg = "No data provided"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
        
        if "Parties" not in data or not isinstance(data["Parties"], list):
            error_msg = "Invalid structure: 'Parties' array is required"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
        
        # Get claim_type (mandatory) - THIS DETERMINES THE ROUTING
        claim_type = data.get("claim_type", "").upper().strip()
        if not claim_type or claim_type not in ["CO", "TP"]:
            error_msg = "Missing or invalid 'claim_type' parameter. Required: 'CO' or 'TP'"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
        
        # Extract case_number for logging (optional field)
        case_number = data.get("Case_Number", "Unknown")
        
        # MAIN ROUTER: Route to appropriate path based on claim_type
        transaction_logger.info(
            f"MAIN_ROUTER_ROUTING | Claim_Type: {claim_type} | Case: {case_number} | "
            f"CO_Path: {CO_DIR} | TP_Path: {TP_DIR} | "
            f"Routing_to: {'CO_PATH' if claim_type == 'CO' else 'TP_PATH'}"
        )
        
        # Route to CO path - ALL processing in MotorclaimdecisionlinuxCO/
        if claim_type == "CO":
            transaction_logger.info(
                f"ROUTING_TO_CO_PATH | Case: {case_number} | "
                f"CO_Directory: {CO_DIR} | "
                f"Importing: MotorclaimdecisionlinuxCO.claim_processor_api"
            )
            # Import and call CO processing module using importlib to ensure correct path
            import importlib.util
            import importlib
            
            # Clear any cached modules to ensure fresh import
            # CRITICAL: Clear ALL claim_processor, config_manager, unified_processor modules
            # to prevent loading wrong modules from root or TP directory
            modules_to_clear = [
                k for k in list(sys.modules.keys())
                if any(x in k for x in [
                    'claim_processor_api', 'co_claim_processor_api',
                    'claim_processor', 'config_manager', 'unified_processor',
                    'excel_ocr_license_processor', 'MotorclaimdecisionlinuxCO',
                    'MotorclaimdecisionlinuxTP.claim_processor'  # Clear TP modules too
                ]) and 'MotorclaimdecisionlinuxCO' not in k
            ]
            for mod in modules_to_clear:
                try:
                    del sys.modules[mod]
                except:
                    pass
            
            co_module_path = os.path.join(CO_DIR, "claim_processor_api.py")
            if not os.path.exists(co_module_path):
                error_msg = f"CO module not found: {co_module_path}"
                transaction_logger.error(f"MAIN_ROUTER_ERROR | {error_msg}")
                return jsonify({"error": error_msg}), 500
            
            # Use unique module name with timestamp to avoid cache conflicts
            import time
            unique_name = f"co_claim_processor_api_{int(time.time() * 1000000)}"
            spec = importlib.util.spec_from_file_location(unique_name, co_module_path)
            co_module = importlib.util.module_from_spec(spec)
            
            # Temporarily change to CO directory and modify sys.path for relative imports
            original_cwd = os.getcwd()
            original_path = sys.path[:]
            try:
                os.chdir(CO_DIR)
                # Ensure CO directory is first in path for relative imports
                sys.path.insert(0, CO_DIR)
                spec.loader.exec_module(co_module)
            finally:
                os.chdir(original_cwd)
                sys.path[:] = original_path
            
            return co_module.process_co_claim(data)
        
        # Route to TP path - ALL processing in MotorclaimdecisionlinuxTP/
        elif claim_type == "TP":
            transaction_logger.info(
                f"ROUTING_TO_TP_PATH | Case: {case_number} | "
                f"TP_Directory: {TP_DIR} | "
                f"Importing: MotorclaimdecisionlinuxTP.claim_processor_api"
            )
            # Import and call TP processing module using importlib to ensure correct path
            import importlib.util
            import importlib
            
            # Clear any cached modules to ensure fresh import
            # CRITICAL: Clear ALL claim_processor, config_manager, unified_processor modules
            # to prevent loading wrong modules from root or CO directory
            modules_to_clear = [
                k for k in list(sys.modules.keys())
                if any(x in k for x in [
                    'claim_processor_api', 'tp_claim_processor_api',
                    'claim_processor', 'config_manager', 'unified_processor',
                    'excel_ocr_license_processor', 'MotorclaimdecisionlinuxTP',
                    'MotorclaimdecisionlinuxCO.claim_processor'  # Clear CO modules too
                ]) and 'MotorclaimdecisionlinuxTP' not in k
            ]
            for mod in modules_to_clear:
                try:
                    del sys.modules[mod]
                except:
                    pass
            
            tp_module_path = os.path.join(TP_DIR, "claim_processor_api.py")
            if not os.path.exists(tp_module_path):
                error_msg = f"TP module not found: {tp_module_path}"
                transaction_logger.error(f"MAIN_ROUTER_ERROR | {error_msg}")
                return jsonify({"error": error_msg}), 500
            
            # Use unique module name with timestamp to avoid cache conflicts
            import time
            unique_name = f"tp_claim_processor_api_{int(time.time() * 1000000)}"
            spec = importlib.util.spec_from_file_location(unique_name, tp_module_path)
            tp_module = importlib.util.module_from_spec(spec)
            
            # Temporarily change to TP directory and modify sys.path for relative imports
            original_cwd = os.getcwd()
            original_path = sys.path[:]
            try:
                os.chdir(TP_DIR)
                # Ensure TP directory is first in path for relative imports
                sys.path.insert(0, TP_DIR)
                spec.loader.exec_module(tp_module)
            finally:
                os.chdir(original_cwd)
                sys.path[:] = original_path
            
            return tp_module.process_tp_claim(data)
        else:
            error_msg = f"Invalid claim_type: {claim_type}. Must be 'CO' or 'TP'"
            transaction_logger.error(f"MAIN_ROUTER_ERROR | Error: {error_msg} | Status: 400")
            return jsonify({"error": error_msg}), 400
    
    except Exception as e:
        error_msg = f"Main router error: {str(e)[:200]}"
        transaction_logger.error(
            f"MAIN_ROUTER_EXCEPTION | Error: {error_msg} | "
            f"Traceback: {traceback.format_exc()[:500]}"
        )
        return jsonify({"error": "Internal server error", "details": error_msg}), 500

# Configuration endpoints for CO
@app.route("/api/config/co/prompts", methods=["GET", "POST"])
@requires_auth
def co_config_prompts():
    """CO prompts configuration - reads from CO directory"""
    # Ensure we're reading from CO directory
    co_config_file = os.path.join(CO_DIR, "claim_config.json")
    if not os.path.exists(co_config_file):
        return jsonify({"error": f"CO config file not found: {co_config_file}"}), 404
    
    if request.method == "GET":
        # Verify we're using the correct config manager
        if co_config_manager.config_file != co_config_file:
            logger.error(f"CO Config Manager file mismatch in prompts endpoint! Expected: {co_config_file}, Got: {co_config_manager.config_file}")
            return jsonify({"error": f"Configuration error: CO config manager using wrong file"}), 500
        
        # Reload config to ensure latest data from CO file
        co_config_manager.reload_config()
        prompts = co_config_manager.get_prompts()
        logger.info(f"CO Prompts loaded from: {co_config_file} | Config file verified: {co_config_manager.config_file}")
        return jsonify(prompts), 200
    else:
        data = request.get_json()
        if "prompts" not in data:
            return jsonify({"error": "No 'prompts' field provided"}), 400
        # Reload before update
        co_config_manager.reload_config()
        if co_config_manager.update_prompts(data["prompts"]):
            logger.info(f"CO Prompts saved to: {co_config_file}")
            return jsonify({"status": "success", "message": "CO prompts updated successfully"}), 200
        return jsonify({"error": "Failed to update CO prompts"}), 500

@app.route("/api/config/co/rules", methods=["GET", "POST"])
@requires_auth
def co_config_rules():
    """CO rules configuration - reads from CO directory"""
    # Ensure we're reading from CO directory
    co_config_file = os.path.join(CO_DIR, "claim_config.json")
    if not os.path.exists(co_config_file):
        return jsonify({"error": f"CO config file not found: {co_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from CO file
        co_config_manager.reload_config()
        config = co_config_manager.get_config()
        rules = config.get("rules", {})
        processing_filters = config.get("processing_filters", {})
        logger.info(f"CO Rules loaded from: {co_config_file}")
        return jsonify({
            "rules": rules,
            "processing_filters": processing_filters
        }), 200
    else:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Reload before update
        co_config_manager.reload_config()
        success = True
        
        # Update rules if provided
        if "rules" in data:
            if not co_config_manager.update_rules(data["rules"]):
                success = False
        
        # Update processing_filters if provided
        if "processing_filters" in data:
            if not co_config_manager.update_processing_filters(data["processing_filters"]):
                success = False
        
        if success:
            logger.info(f"CO Rules/Processing Filters saved to: {co_config_file}")
            return jsonify({"status": "success", "message": "CO rules and processing filters updated successfully"}), 200
        return jsonify({"error": "Failed to update CO rules/processing filters"}), 500

@app.route("/api/config/co/response-fields", methods=["GET", "POST"])
@requires_auth
def co_config_response_fields():
    """CO response fields configuration - reads from CO directory"""
    # Ensure we're reading from CO directory
    co_config_file = os.path.join(CO_DIR, "claim_config.json")
    if not os.path.exists(co_config_file):
        return jsonify({"error": f"CO config file not found: {co_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from CO file
        co_config_manager.reload_config()
        config = co_config_manager.get_config()
        response_fields = config.get("response_fields", {})
        logger.info(f"CO Response Fields loaded from: {co_config_file}")
        return jsonify(response_fields), 200
    else:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        # Reload before update
        co_config_manager.reload_config()
        config = co_config_manager.get_config()
        if "response_fields" not in config:
            config["response_fields"] = {}
        if "enabled_fields" in data:
            config["response_fields"]["enabled_fields"] = data["enabled_fields"]
        if "description" in data:
            config["response_fields"]["description"] = data["description"]
        co_config_manager._config = config
        co_config_manager._save_config()
        logger.info(f"CO Response Fields saved to: {co_config_file}")
        return jsonify({"status": "success", "message": "CO response fields updated successfully"}), 200

# Configuration endpoints for TP
@app.route("/api/config/tp/prompts", methods=["GET", "POST"])
@requires_auth
def tp_config_prompts():
    """TP prompts configuration - reads from TP directory"""
    # Ensure we're reading from TP directory
    tp_config_file = os.path.join(TP_DIR, "claim_config.json")
    if not os.path.exists(tp_config_file):
        return jsonify({"error": f"TP config file not found: {tp_config_file}"}), 404
    
    if request.method == "GET":
        # Verify we're using the correct config manager
        if tp_config_manager.config_file != tp_config_file:
            logger.error(f"TP Config Manager file mismatch in prompts endpoint! Expected: {tp_config_file}, Got: {tp_config_manager.config_file}")
            return jsonify({"error": f"Configuration error: TP config manager using wrong file"}), 500
        
        # Reload config to ensure latest data from TP file
        tp_config_manager.reload_config()
        prompts = tp_config_manager.get_prompts()
        logger.info(f"TP Prompts loaded from: {tp_config_file} | Config file verified: {tp_config_manager.config_file}")
        return jsonify(prompts), 200
    else:
        data = request.get_json()
        if "prompts" not in data:
            return jsonify({"error": "No 'prompts' field provided"}), 400
        # Reload before update
        tp_config_manager.reload_config()
        if tp_config_manager.update_prompts(data["prompts"]):
            logger.info(f"TP Prompts saved to: {tp_config_file}")
            return jsonify({"status": "success", "message": "TP prompts updated successfully"}), 200
        return jsonify({"error": "Failed to update TP prompts"}), 500

@app.route("/api/config/tp/rules", methods=["GET", "POST"])
@requires_auth
def tp_config_rules():
    """TP rules configuration - reads from TP directory"""
    # Ensure we're reading from TP directory
    tp_config_file = os.path.join(TP_DIR, "claim_config.json")
    if not os.path.exists(tp_config_file):
        return jsonify({"error": f"TP config file not found: {tp_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from TP file
        tp_config_manager.reload_config()
        rules = tp_config_manager.get_rules()
        logger.info(f"TP Rules loaded from: {tp_config_file}")
        return jsonify({"rules": rules}), 200
    else:
        data = request.get_json()
        if "rules" not in data:
            return jsonify({"error": "No 'rules' field provided"}), 400
        # Reload before update
        tp_config_manager.reload_config()
        if tp_config_manager.update_rules(data["rules"]):
            logger.info(f"TP Rules saved to: {tp_config_file}")
            return jsonify({"status": "success", "message": "TP rules updated successfully"}), 200
        return jsonify({"error": "Failed to update TP rules"}), 500

@app.route("/api/config/tp/response-fields", methods=["GET", "POST"])
@requires_auth
def tp_config_response_fields():
    """TP response fields configuration - reads from TP directory"""
    # Ensure we're reading from TP directory
    tp_config_file = os.path.join(TP_DIR, "claim_config.json")
    if not os.path.exists(tp_config_file):
        return jsonify({"error": f"TP config file not found: {tp_config_file}"}), 404
    
    if request.method == "GET":
        # Reload config to ensure latest data from TP file
        tp_config_manager.reload_config()
        config = tp_config_manager.get_config()
        response_fields = config.get("response_fields", {})
        logger.info(f"TP Response Fields loaded from: {tp_config_file}")
        return jsonify(response_fields), 200
    else:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        # Reload before update
        tp_config_manager.reload_config()
        config = tp_config_manager.get_config()
        if "response_fields" not in config:
            config["response_fields"] = {}
        if "enabled_fields" in data:
            config["response_fields"]["enabled_fields"] = data["enabled_fields"]
        if "description" in data:
            config["response_fields"]["description"] = data["description"]
        tp_config_manager._config = config
        tp_config_manager._save_config()
        logger.info(f"TP Response Fields saved to: {tp_config_file}")
        return jsonify({"status": "success", "message": "TP response fields updated successfully"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    
    print(f"Starting Unified Motor Claim Decision API Server on port {port}")
    print(f"CO and TP services available on single endpoint")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
>>>>>>> 21fcbcc27f4d592ac48567ca74c7cbdd1496059f
