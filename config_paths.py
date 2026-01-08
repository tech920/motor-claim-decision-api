import os
import pytesseract
import logging

# Configure OCR and Poppler paths for production
# User provided paths:
# Tesseract Data: /opt/Library/Tesseract-OCR/tessdata/
# Poppler Library: /opt/Library/poppler-25.07.0/Library/

# Define Paths
TESSERACT_BASE = "/opt/Library/Tesseract-OCR"
TESSERACT_CMD = os.path.join(TESSERACT_BASE, "tesseract")
TESSDATA_PREFIX = "/opt/Library/Tesseract-OCR/tessdata/"

# Poppler path - checking likely binary locations based on user input
POPPLER_BASE_USER = "/opt/Library/poppler-25.07.0/Library/"
POPPLER_BIN_GUESS = os.path.join(POPPLER_BASE_USER, "bin")

def setup_ocr_paths():
    """Configure OCR and Poppler paths"""
    try:
        # 1. Configure Tesseract
        if os.path.exists(TESSERACT_CMD):
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
            print(f"✓ Tesseract binary configured: {TESSERACT_CMD}")
        else:
            print(f"⚠️ Tesseract binary not found at {TESSERACT_CMD}, checking system PATH...")

        # Configure TESSDATA_PREFIX
        if os.path.exists(TESSDATA_PREFIX):
            os.environ["TESSDATA_PREFIX"] = TESSDATA_PREFIX
            print(f"✓ TESSDATA_PREFIX set: {TESSDATA_PREFIX}")
        else:
            print(f"⚠️ Tesseract data directory not found at {TESSDATA_PREFIX}")

        # 2. Configure Poppler
        # Add both the base Library path and a potential bin subdirectory to PATH
        poppler_paths = [POPPLER_BIN_GUESS, POPPLER_BASE_USER]
        existing_poppler_paths = [p for p in poppler_paths if os.path.exists(p)]
        
        if existing_poppler_paths:
            path_str = os.pathsep.join(existing_poppler_paths)
            os.environ["PATH"] += os.pathsep + path_str
            print(f"✓ Poppler paths added to PATH: {path_str}")
        else:
            print(f"⚠️ Poppler paths not found at {POPPLER_BASE_USER}")
            
    except Exception as e:
        print(f"Error configuring OCR paths: {e}")

# Run setup immediately on import
setup_ocr_paths()
