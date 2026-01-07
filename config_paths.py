import os
import pytesseract
from pdf2image import convert_from_path
import logging

# Configure OCR and Poppler paths for production
# Paths provided by user:
# OCR: /opt/Library/Tesseract-OCR/
# Poppler: /opt/Library/poppler-25.07.0/

# Define paths
TESSERACT_PATH = "/opt/Library/Tesseract-OCR/tesseract"
POPPLER_PATH = "/opt/Library/poppler-25.07.0/bin" # Assuming bin is where executables are
BASE_DEPLOY_PATH = "/opt/Motorclaimdecision_main/"

def setup_ocr_paths():
    """Configure OCR and Poppler paths"""
    try:
        # 1. Configure Tesseract
        if os.path.exists(TESSERACT_PATH):
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
            print(f"✓ Tesseract configured: {TESSERACT_PATH}")
        else:
            # Fallback to default/system path if specific path doesn't exist (e.g. dev environment)
            print(f"⚠️ Tesseract not found at {TESSERACT_PATH}, using default")

        # 2. Configure Poppler (add to PATH)
        if os.path.exists(POPPLER_PATH):
            os.environ["PATH"] += os.pathsep + POPPLER_PATH
            print(f"✓ Poppler added to PATH: {POPPLER_PATH}")
        else:
            print(f"⚠️ Poppler not found at {POPPLER_PATH}")
            
    except Exception as e:
        print(f"Error configuring OCR paths: {e}")

# Run setup
setup_ocr_paths()
