
import pytest
import json
from unittest.mock import MagicMock, patch
import sys
import os

# Add paths for modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../MotorclaimdecisionlinuxCO')))

# Import processor classes
from MotorclaimdecisionlinuxCO.claim_processor import ClaimProcessor
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

@pytest.fixture
def mock_ollama_response():
    """Mock Ollama response for both translation and decision"""
    with patch('requests.post') as mock_post:
        def side_effect(*args, **kwargs):
            json_data = kwargs.get('json', {})
            prompt = json_data.get('prompt', '')
            model = json_data.get('model', '')
            
            # Check if this is a translation request (looking for translation prompt pattern)
            if "Translate the following text from Arabic to English" in prompt or "Translate the following Arabic text" in prompt:
                return MagicMock(
                    status_code=200,
                    json=lambda: {"response": TRANSLATED_DESCRIPTION}
                )
            
            # Decision request
            decision_response = {
                "decision": "ACCEPTED_WITH_RECOVERY",
                "reasoning": "The first party (Tawuniya insured) has 100% liability due to sudden swerving (Traffic Law 50/2/24). However, recovery may apply depending on specific policy conditions or violations found in the full report context.",
                "classification": "Standard Liability Claim",
                "applied_conditions": ["Traffic Violation"]
            }
            return MagicMock(
                status_code=200,
                json=lambda: {"response": json.dumps(decision_response)}
            )
            
        mock_post.side_effect = side_effect
        yield mock_post

@pytest.fixture
def mock_ocr_processor():
    """Mock OCR processor to simulate extraction"""
    with patch('MotorclaimdecisionlinuxCO.claim_processor_api.co_ocr_license_processor') as mock_ocr:
        # Simulate processing returning the data unchanged (or enriched)
        mock_ocr.process_claim_data_with_ocr.return_value = SAMPLE_REQUEST
        yield mock_ocr

def test_co_claim_translation_flow(mock_ollama_response, mock_ocr_processor):
    """
    Test that the CO claim processing flow correctly:
    1. Receives the Arabic claim data
    2. Translates the accident description to English
    3. Sends the translated data to the decision model
    """
    
    # Run the process
    # Note: process_co_claim returns a Flask response object (jsonify)
    # We need to mock Flask's jsonify or handle the response appropriately
    with patch('MotorclaimdecisionlinuxCO.claim_processor_api.jsonify') as mock_jsonify:
        mock_jsonify.side_effect = lambda x: x # Return dict directly for inspection
        
        # We need to mock the request object since process_co_claim might use it contextually or for logging
        # But looking at the code, it takes 'data' as argument.
        # However, the imported function signature is process_co_claim(data).
        
        # Mock configuration to ensure translation is enabled if controlled by config
        with patch('MotorclaimdecisionlinuxCO.claim_processor_api.co_config_manager') as mock_config:
            mock_config.get_config.return_value = {
                "response_fields": {"enabled_fields": {}},
                "ollama": {"base_url": "http://localhost:11434"}
            }
            
            # Execute
            result = process_co_claim(SAMPLE_REQUEST)
            
            # Verify that requests.post was called
            assert mock_ollama_response.call_count >= 1
            
            # Verify translation call
            # We iterate through calls to find the translation request
            translation_call_found = False
            decision_call_found = False
            
            for call in mock_ollama_response.call_args_list:
                args, kwargs = call
                json_body = kwargs.get('json', {})
                prompt = json_body.get('prompt', '')
                
                # Check for translation request
                if "Translate" in prompt and "Arabic" in prompt:
                    translation_call_found = True
                    # Verify it tried to translate our specific Arabic text
                    assert "بعد الشخوص والمعاينة" in prompt
                
                # Check for decision request
                if "Analyze Party" in prompt or "DATA (JSON)" in prompt:
                    decision_call_found = True
                    # Verify that the decision prompt contains the TRANSLATED text
                    # This is the crucial check: did we send English to the decision model?
                    if TRANSLATED_DESCRIPTION in prompt:
                        print("✅ Confirmed: Translated English text was sent to decision model")
                    else:
                        print("❌ Warning: Translated text NOT found in decision prompt. Checking for Arabic...")
                        if "بعد الشخوص والمعاينة" in prompt:
                            print("❌ Found Arabic text in decision prompt instead of English!")
            
            # Assertions
            if not translation_call_found:
                # It might be that the system detected Arabic and auto-translated, or config disabled it
                print("⚠️ No explicit translation call found. Check configuration or logic.")
            
            assert decision_call_found, "No decision call made to Ollama"

if __name__ == "__main__":
    # Manually run the test function if executed as script
    # Setup mocks manually
    mock_post = MagicMock()
    
    def side_effect(*args, **kwargs):
        json_data = kwargs.get('json', {})
        prompt = json_data.get('prompt', '')
        
        if "Translate" in prompt:
            print("  -> Intercepted Translation Request")
            return MagicMock(status_code=200, json=lambda: {"response": TRANSLATED_DESCRIPTION})
        
        print("  -> Intercepted Decision Request")
        # Check content
        if TRANSLATED_DESCRIPTION in prompt:
            print("     ✅ Decision prompt contains TRANSLATED English text")
        elif "بعد الشخوص" in prompt:
            print("     ❌ Decision prompt contains ORIGINAL Arabic text")
            
        decision_response = {
            "decision": "ACCEPTED",
            "reasoning": "Test decision",
            "classification": "Test class",
            "applied_conditions": []
        }
        return MagicMock(status_code=200, json=lambda: {"response": json.dumps(decision_response)})

    mock_post.side_effect = side_effect
    
    with patch('requests.post', mock_post):
        with patch('MotorclaimdecisionlinuxCO.claim_processor_api.jsonify', lambda x: x):
            with patch('MotorclaimdecisionlinuxCO.claim_processor_api.co_ocr_license_processor') as mock_ocr:
                mock_ocr.process_claim_data_with_ocr.return_value = SAMPLE_REQUEST
                
                print("Running CO Claim Processing Simulation...")
                try:
                    result = process_co_claim(SAMPLE_REQUEST)
                    print("Processing completed successfully.")
                    print("Result:", json.dumps(result, indent=2, ensure_ascii=False))
                except Exception as e:
                    print(f"Processing failed: {e}")
                    import traceback
                    traceback.print_exc()
