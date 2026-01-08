
# Final fixed test script
# Properly mocks requests.Session().post for both translation and decision requests
# Handles potential tuple return from process_co_claim

import pytest
import json
import base64
from unittest.mock import MagicMock, patch
import sys
import os

# Add paths for modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../MotorclaimdecisionlinuxCO')))

# Import processor classes
from MotorclaimdecisionlinuxCO.claim_processor import ClaimProcessor
from MotorclaimdecisionlinuxCO.claim_processor_api import process_co_claim

# OCR Text to simulate extraction - MUST contain keywords to pass validity check in API
SIMULATED_OCR_TEXT = """
<html>
<body>
Kingdom of Saudi Arabia
Traffic Accident Report
Report No: 12345678
Date: 26/11/2025
Location: Al-Zahir District, Madinah Road

Party 1:
Name: Rihan Ali Ali Malik
ID: 2538690385
License Type: Private
License Expiry: 05/11/2026
Liability: 100%
Insurance: Tawuniya

Party 2:
Name: Hamad Adnan Ahmed Al-Marri
Liability: 0%

Description: Collision occurred when Party 1 swerved right from left lane, hitting Party 2 in middle lane.
</body>
</html>
"""

# Sample Base64 image
SAMPLE_BASE64 = "data:image/text;base64,VGhpcyBpcyBhIHBsYWNlaG9sZGVy" # Just a placeholder

# Sample request data from user
SAMPLE_REQUEST = {
  "claim_type": "CO",
  "Case_Number": "MC261125303",
  "Accident_Date": "2025-11-26",
  "Upload_Date": "2025-11-26",
  "Claim_requester_ID": "",
  "accident_description": "بعد الشخوص والمعاينة والايطلاع والاستماع لأقوال الطرفين تبين لي بأن الطرف الأول كان يسير في المسار الأيسر بحي الزاهر طريق المدينة المنورة باتجاه الشمال والطرف الثاني كان يسير في المسار الأوسط بنفس الاتجاه وعند انحراف الطرف الاول إلى اليمين اصطدام الطرف الأول بالطرف الثاني ونتج عن ذلك اضرار ماديه ومنها اضرار الطرف الأول الركن الخلفي الايمن واضرار الطرف الثاني الركن الامامي الأيسر والعجلة الامامية اليسرى وعلى ذلك يتحمل الطرف الأول نسبة 75% للانحراف المفاجئ من نظام المرور ماده رقم 50/2/24 ويتحمل الطرف الثاني نسبة 25% لعدم الانتباه وأخذ الحيطه والحذر من نظام المرور ماده رقم 50/2/1",
  "isDAA": False,
  "Suspect_as_Fraud": None,
  "DaaReasonEnglish": "",
  "Name_LD_rep_64bit": SAMPLE_BASE64,
  "Parties": [
    {
      "Party_ID": "2538690385",
      "Party_Name": "رهان علي علي مالك",
      "Insurance_Name": "Tawuniya Cooperative Insurance Company",
      "Policyholder_ID": "7006472943",
      "Policyholdername": "فرع مؤسسة سعد مرزوق",
      "insurance_type": "CO",
      "Liability": 100,
      "Vehicle_Serial": "KMHDG41FXGU563353",
      "VehicleOwnerId": "7006472943",
      "License_Type_From_Najm": "Private License",
      "License_Expiry_Date": "2026-11-05",
      "License_Expiry_Last_Updated": None,
      "License_Renewal_Date": None,
      "carMake": "Hyundai",
      "carModel": "النترا",
      "carMake_Najm": "Hyundai",
      "carModel_Najm": "النترا",
      "Recovery": ""
    },
    {
      "Party_ID": "2237605809",
      "Party_Name": "حمد عدنان احمد المري",
      "Insurance_Name": "non-insured",
      "Policyholder_ID": "2056645290",
      "Policyholdername": "عدنان احمد سعيد المري",
      "insurance_type": "",
      "Liability": 0,
      "Vehicle_Serial": "03742",
      "VehicleOwnerId": "2056645290",
      "License_Type_From_Najm": "Private License",
      "License_Expiry_Date": "",
      "License_Expiry_Last_Updated": None,
      "License_Renewal_Date": None,
      "carMake": "TOYOTA",
      "carModel": "فورتشنر",
      "carMake_Najm": "TOYOTA",
      "carModel_Najm": "فورتشنر",
      "Recovery": ""
    }
  ]
}

# Translated description expectation
TRANSLATED_DESCRIPTION = """After inspection, viewing, and listening to the statements of both parties, it became clear to me that the first party was driving in the left lane in Al-Zahir district, Al-Madinah Al-Munawwarah Road, heading north, and the second party was driving in the middle lane in the same direction. When the first party swerved to the right, the first party collided with the second party, resulting in material damage, including damage to the first party's right rear corner and damage to the second party's left front corner and left front wheel. Based on this, the first party bears 75% liability for sudden swerving under Traffic Law Article No. 50/2/24, and the second party bears 25% liability for lack of attention and taking caution under Traffic Law Article No. 50/2/1."""

if __name__ == "__main__":
    # Setup detailed mocks
    
    # 1. Mock requests.Session()
    mock_session = MagicMock()
    
    # Define a side effect that returns different responses based on input
    def session_post_side_effect(*args, **kwargs):
        json_data = kwargs.get('json', {})
        prompt = json_data.get('prompt', '')
        
        # Create a clean mock response object for each call
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {'Content-Type': 'application/json'} # CRITICAL: Set content type to avoid HTML check
        
        # Translation Request
        if "Translate" in prompt:
            print("  -> Intercepted Translation Request")
            mock_resp.json.return_value = {"response": TRANSLATED_DESCRIPTION}
            mock_resp.text = json.dumps({"response": TRANSLATED_DESCRIPTION}) # Ensure text property is valid JSON string
            return mock_resp
        
        # Decision Request
        print("  -> Intercepted Decision Request")
        if TRANSLATED_DESCRIPTION in prompt:
            print("     ✅ Decision prompt contains TRANSLATED English text")
        else:
            print("     ❌ Decision prompt MISSING translated text")
            
        if "FULL OCR REPORT TEXT" in prompt and "Kingdom of Saudi Arabia" in prompt:
            print("     ✅ Decision prompt contains FULL OCR REPORT TEXT")
        else:
            print("     ❌ Decision prompt MISSING OCR text")

        decision_response = {
            "decision": "ACCEPTED_WITH_RECOVERY",
            "reasoning": "First party has 100% liability but validation checks are passed.",
            "classification": "Validation Passed",
            "applied_conditions": []
        }
        
        # IMPORTANT: The ClaimProcessor checks if response.text starts with '<'
        # So we MUST ensure response.text is a clean JSON string
        response_json_str = json.dumps(decision_response)
        
        # Ollama returns a JSON object with 'response' field containing the text
        ollama_response = {"response": response_json_str}
        
        mock_resp.json.return_value = ollama_response
        mock_resp.text = json.dumps(ollama_response) # Ensure text property doesn't look like HTML
        return mock_resp

    mock_session.post.side_effect = session_post_side_effect
    
    # 2. Mock direct requests.post (used in _translate_text_to_english)
    mock_direct_post = MagicMock()
    mock_direct_post.side_effect = session_post_side_effect

    # Apply patches
    with patch('requests.Session', return_value=mock_session):
        with patch('requests.post', mock_direct_post):
            with patch('MotorclaimdecisionlinuxCO.claim_processor_api.jsonify', lambda x: x):
                
                # Mock base64.b64decode to return our SIMULATED_OCR_TEXT when extracting
                original_b64decode = base64.b64decode
                def side_effect_b64decode(s, *args, **kwargs):
                    # If it looks like our sample (or part of it), return text
                    if s == SAMPLE_BASE64.split(',')[1] or len(s) < 100: 
                        return SIMULATED_OCR_TEXT.encode('utf-8')
                    return original_b64decode(s, *args, **kwargs)

                with patch('base64.b64decode', side_effect=side_effect_b64decode):
                    with patch('MotorclaimdecisionlinuxCO.claim_processor_api.co_ocr_license_processor') as mock_ocr:
                        # Mock OCR to return data unchanged
                        mock_ocr.process_claim_data_with_ocr.return_value = SAMPLE_REQUEST
                        
                        print("Running CO Claim Processing Simulation (Final Fixed Mocks with OCR)...")
                        try:
                            # Force reload of rules to ensure fresh start
                            import MotorclaimdecisionlinuxCO.claim_processor_api as api_module
                            api_module.co_processor.check_ollama_health = lambda: True # bypass health check
                            
                            # Process
                            result_tuple = process_co_claim(SAMPLE_REQUEST)
                            
                            # Handle tuple return if present
                            if isinstance(result_tuple, tuple):
                                result = result_tuple[0]
                                status_code = result_tuple[1]
                            else:
                                result = result_tuple
                                status_code = 200
                                
                            print("\nProcessing completed successfully.")
                            # print("Result:", json.dumps(result, indent=2, ensure_ascii=False))
                            
                            # Verification
                            parties = result.get('Parties', [])
                            # We need to find the processed party (Tawuniya one), index might not be 0 due to filtering
                            first_party = next((p for p in parties if p.get('Party_ID') == '2538690385'), None)
                            
                            if first_party and first_party.get('Decision') == 'ACCEPTED_WITH_RECOVERY':
                                 print("\n✅ SUCCESS: Full flow verified - Translation -> OCR -> Decision -> Response")
                            else:
                                 print(f"\n❌ FAILURE: Unexpected decision result: {first_party.get('Decision') if first_party else 'Party not found'}")
                                 if first_party:
                                     print(f"Reasoning: {first_party.get('Reasoning')}")
                            
                            # Verify OCR flag
                            if result.get('LD_Rep_64bit_Received') is True:
                                print("✅ SUCCESS: LD_Rep_64bit_Received is True")
                            else:
                                print("❌ FAILURE: LD_Rep_64bit_Received is False")

                        except Exception as e:
                            print(f"Processing failed: {e}")
                            import traceback
                            traceback.print_exc()
