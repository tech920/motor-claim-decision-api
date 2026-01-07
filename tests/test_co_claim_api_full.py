
import pytest
import json
from unittest.mock import MagicMock, patch
import sys
import os

# Add paths for modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../MotorclaimdecisionlinuxCO')))

# Import processor classes
from MotorclaimdecisionlinuxCO.claim_processor_api import process_co_claim

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
  "Name_LD_rep_64bit": "",
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
    
    def session_post_side_effect(*args, **kwargs):
        json_data = kwargs.get('json', {})
        prompt = json_data.get('prompt', '')
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {'Content-Type': 'application/json'}
        
        # Translation Request
        if "Translate" in prompt:
            print("  -> Intercepted Translation Request")
            mock_resp.json.return_value = {"response": TRANSLATED_DESCRIPTION}
            mock_resp.text = json.dumps({"response": TRANSLATED_DESCRIPTION})
            return mock_resp
        
        # Decision Request
        print("  -> Intercepted Decision Request")
        if TRANSLATED_DESCRIPTION in prompt:
            print("     ✅ Decision prompt contains TRANSLATED English text")
        
        # Logic check:
        # Party 1 has 100% liability.
        # CO rules say 100% liability is NOT a rejection.
        # So decision should be ACCEPTED.
        # Subrogation check: Liability < 100? No (it is 100). So NO subrogation upgrade.
        
        decision_response = {
            "party_index": 1,
            "party_id": "2538690385",
            "tawuniya_identified": True,
            "decision": "ACCEPTED", 
            "reasoning": "Party is insured with Tawuniya, liability is 100% (which is covered under comprehensive), and no rejection rules (like fraud or invalid license) applied.",
            "classification": "Accepted - Comprehensive Cover",
            "applied_conditions": []
        }
        
        response_json_str = json.dumps(decision_response)
        ollama_response = {"response": response_json_str}
        
        mock_resp.json.return_value = ollama_response
        mock_resp.text = json.dumps(ollama_response)
        return mock_resp

    mock_session.post.side_effect = session_post_side_effect
    
    # 2. Mock direct requests.post
    mock_direct_post = MagicMock()
    mock_direct_post.side_effect = session_post_side_effect

    # Apply patches
    with patch('requests.Session', return_value=mock_session):
        with patch('requests.post', mock_direct_post):
            with patch('MotorclaimdecisionlinuxCO.claim_processor_api.jsonify', lambda x: x):
                with patch('MotorclaimdecisionlinuxCO.claim_processor_api.co_ocr_license_processor') as mock_ocr:
                    mock_ocr.process_claim_data_with_ocr.return_value = SAMPLE_REQUEST
                    
                    print("Running CO Claim API Simulation...")
                    try:
                        # Force reload of rules/config
                        import MotorclaimdecisionlinuxCO.claim_processor_api as api_module
                        api_module.co_processor.check_ollama_health = lambda: True
                        
                        # Process
                        result_tuple = process_co_claim(SAMPLE_REQUEST)
                        
                        if isinstance(result_tuple, tuple):
                            result = result_tuple[0]
                        else:
                            result = result_tuple
                            
                        print("\nProcessing completed successfully.")
                        print("Result:", json.dumps(result, indent=2, ensure_ascii=False))
                        
                        # Verification
                        parties = result.get('Parties', [])
                        p1 = next((p for p in parties if p.get('Party_ID') == '2538690385'), None)
                        
                        if p1:
                            print(f"\nParty 1 Decision: {p1.get('Decision')}")
                            if p1.get('Decision') == 'ACCEPTED':
                                print("✅ SUCCESS: Party 1 (100% Liability) is ACCEPTED (Correct for CO).")
                            else:
                                print(f"⚠️ Check: Expected ACCEPTED, got {p1.get('Decision')}")
                        else:
                            print("\n❌ FAILURE: Party 1 not found in response.")
                             
                    except Exception as e:
                        print(f"Processing failed: {e}")
                        import traceback
                        traceback.print_exc()
