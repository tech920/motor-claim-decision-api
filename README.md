# Motor Claim Decision API (TP + CO)

Unified REST API for processing motor insurance claims - Third Party (TP) and Comprehensive (CO) claims.

## 🏗️ Repository Structure

```
Motorclaimde/
├── unified_api_server.py          # Main API server (handles both TP and CO)
├── MotorclaimdecisionlinuxTP/    # Third Party claim processing
│   ├── claim_processor.py
│   ├── claim_processor_api.py
│   ├── config_manager.py
│   ├── unified_processor.py
│   ├── excel_ocr_license_processor.py
│   ├── auth_manager.py
│   ├── api_server.py
│   ├── claim_config.json
│   └── users.json
├── MotorclaimdecisionlinuxCO/    # Comprehensive claim processing
│   ├── claim_processor.py
│   ├── claim_processor_api.py
│   ├── config_manager.py
│   ├── unified_processor.py
│   ├── excel_ocr_license_processor.py
│   ├── auth_manager.py
│   ├── api_server.py
│   ├── claim_config.json
│   └── users.json
├── requirements.txt
└── README.md
```

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Ollama running locally or accessible via API

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure Ollama endpoints in:
   - `MotorclaimdecisionlinuxTP/claim_config.json` (for TP)
   - `MotorclaimdecisionlinuxCO/claim_config.json` (for CO)

3. Run the unified API server:
```bash
python unified_api_server.py
```

The API will start on `http://localhost:5000`

## 🔌 API Endpoints

### Third Party (TP) Claims
**POST** `/api/tp/process`

### Comprehensive (CO) Claims
**POST** `/api/co/process`

### Health Check
**GET** `/health`

## 📁 Core Files

### Main Server
- `unified_api_server.py` - Routes requests to TP or CO processors

### TP Module (MotorclaimdecisionlinuxTP/)
- `claim_processor.py` - TP claim processing logic
- `claim_processor_api.py` - TP API endpoint handlers
- `config_manager.py` - TP configuration management
- `unified_processor.py` - XML/JSON conversion for TP
- `excel_ocr_license_processor.py` - Excel/OCR processing for TP
- `auth_manager.py` - TP authentication
- `api_server.py` - Standalone TP server (optional)
- `claim_config.json` - TP business rules, prompts, settings
- `users.json` - TP user credentials

### CO Module (MotorclaimdecisionlinuxCO/)
- `claim_processor.py` - CO claim processing logic
- `claim_processor_api.py` - CO API endpoint handlers
- `config_manager.py` - CO configuration management
- `unified_processor.py` - XML/JSON conversion for CO
- `excel_ocr_license_processor.py` - Excel/OCR processing for CO
- `auth_manager.py` - CO authentication
- `api_server.py` - Standalone CO server (optional)
- `claim_config.json` - CO business rules, prompts, settings
- `users.json` - CO user credentials

## ⚙️ Configuration

All business logic, rules, and prompts are in:
- `MotorclaimdecisionlinuxTP/claim_config.json` (for TP)
- `MotorclaimdecisionlinuxCO/claim_config.json` (for CO)

**Important CO Feature:** If `data.liability < 100` and decision is `ACCEPTED`, the code automatically upgrades to `ACCEPTED_WITH_SUBROGATION`.

Changes to config files require service restart.

## 🧪 Testing

### Test TP API:
```bash
curl -X POST http://localhost:5000/api/tp/process \
  -H "Content-Type: application/json" \
  -d @your_tp_claim_data.json
```

### Test CO API:
```bash
curl -X POST http://localhost:5000/api/co/process \
  -H "Content-Type: application/json" \
  -d @your_co_claim_data.json
```

## 📤 Repository

**GitHub:** https://github.com/tech920/motor-claim-decision-api

### Upload Instructions

If you need to upload files, see:
- `QUICK_UPLOAD_WEB.md` - Upload via GitHub web interface (easiest)
- `GITHUB_UPLOAD_INSTRUCTIONS.md` - Complete upload guide
- `FIX_UPLOAD_ISSUE.md` - Troubleshooting upload issues

### Using Git (if installed)

```bash
cd D:\Motorclaimde
git init
git add .
git commit -m "Initial commit: Motor Claim Decision API (TP + CO)"
git remote add origin https://github.com/tech920/motor-claim-decision-api.git
git branch -M main
git push -u origin main
```

**If asked for password:** Use Personal Access Token (not your password)

## 🤖 Connect Cursor AI

1. Open **Cursor**
2. Press `Ctrl+Shift+P`
3. Type: `Git: Clone`
4. Paste: `https://github.com/tech920/motor-claim-decision-api.git`
5. Select destination folder
6. Open cloned folder

Then press `Ctrl+L` to chat with AI about your code!

## 📝 Notes

- TP and CO modules are completely isolated
- Each has its own configuration and authentication
- Unified server routes requests to appropriate module
- All business logic is in config files (no hardcoded rules)
- API runs on port 5000 by default
- Requires Ollama running locally or accessible

## 🔗 Features

- ✅ Separate TP and CO processing
- ✅ Unified API server
- ✅ Configuration-driven rules
- ✅ Automatic decision upgrades (CO)
- ✅ Excel/OCR license processing
- ✅ Authentication support
- ✅ Comprehensive logging

---

**Ready for production use!** 🎉

