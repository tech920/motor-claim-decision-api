"""
Configuration Manager for Prompts and Rules
Stores and manages Ollama prompts and decision rules/controls
"""

import json
import os
from typing import Dict, Any, Optional
from datetime import datetime
import threading

# Get base directory - works in both Windows (dev) and Linux (production)
def _get_base_dir():
    """Get base directory - auto-detects Windows dev or Linux production"""
    # 1. Check environment variable first
    env_dir = os.getenv("MOTORCLAIM_BASE_DIR")
    if env_dir and os.path.exists(env_dir):
        return env_dir
    
    # 2. Use script directory (works in both environments)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 3. Check if we're in Windows dev environment
    if os.name == 'nt':  # Windows
        # Check common Windows dev paths
        windows_paths = [
            r"D:\Motorclaimdecisionlinux",
            r"D:\Motorclaimdecision",
            script_dir
        ]
        for path in windows_paths:
            if os.path.exists(path) and os.path.isdir(path):
                return path
    
    # 4. Check if we're in Linux production
    linux_paths = [
        "/opt/motorclaimdecision",
        script_dir
    ]
    for path in linux_paths:
        if os.path.exists(path) and os.path.isdir(path):
            return path
    
    # 5. Fallback to script directory
    return script_dir

BASE_DIR = _get_base_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "claim_config.json")
CONFIG_LOCK = threading.Lock()


class ConfigManager:
    """Manages configuration for prompts and rules"""
    
    def __init__(self, config_file: str = CONFIG_FILE):
        self.config_file = config_file
        self._config = None
        self._load_config()
    
    def _load_config(self):
        """Load configuration from file"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}")
                self._config = self._get_default_config()
        else:
            self._config = self._get_default_config()
            self._save_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "prompts": {
                "main_prompt": """Hi Ahmed,

Let's try this:

///////////////////////////////////////////////////

You are a specialized model for analyzing motor vehicle accident reports and claims, determining the final insurance decision with very high accuracy.
You must read the record as is, without adding, without assuming, without interpreting, and without inferring any information not present in the text.
 
⚠️ ⚠️ ⚠️ CRITICAL RULE - Read carefully ⚠️ ⚠️ ⚠️
Each analysis is performed for the specified party only (Party by Party).  
- Liability percentage comes from Parameters (Excel) - do not take it from the accident description
- Information about other parties is provided for context only (such as cooperative rules) - their liability does not cause rejection of the current party
- The accident description explains the accident only - do not use it to determine liability
 
Do not use any information outside the record.  
The output must be in JSON format only.
 
=====================================================================
🔴 FIRST: Claim Rejection Rules (REJECTED)
=====================================================================
⚠️ Basic Rule #1 — Most Important and Highest Priority
1) If the liability percentage of the party you are analyzing now (Liability) = 100%
→ Mandatory Decision: REJECTED  
→ Applies to all companies without any exception, including Tawuniya (Cooperative).  
→ This rule cannot be overridden.
→ ⚠️ ⚠️ ⚠️ This rule applies only to the party you are analyzing - does not apply to other parties ⚠️ ⚠️ ⚠️
 
------------------------------------------------------
The claim is REJECTED for the party being analyzed if any of the following applies:
------------------------------------------------------
2) The sum of liability for parties insured under Tawuniya (Cooperative) insurance company is 0 then reject all parties of the accident.
3) The damaged vehicle is owned by the at-fault party.  
4) The claim concerns property of the insured or property under their management/custody.  
5) The claim is due to death of the insured or driver.  
6) Use of the vehicle in racing or capability testing.  
7) Entering a prohibited area without permission.  
8) Intentional damage to the vehicle.  
9) Collusion or staged accident.  
10) Intentional accident.  
11) Fleeing the scene without acceptable excuse.  
12) Reckless driving (drifting).  
13) Use of drugs/alcohol/medications that prevent driving.  
14) Natural disasters.  
15) Failure to notify authorities immediately.  
16) More than 5 years have passed since the accident.  
17) Fraud exists.
 
If the 17 rejection reasons above does not apply then the claim is either accepted or accepted with recovery. To identify if the claim is accepted or accepted with recovery follow the below rules:
🟡 Second: Accepted with recovery rules (ACCEPTED_WITH_RECOVERY)
=====================================================================
ACCEPTED_WITH_RECOVERY applies when the at-fault party (i.e., one with Liability > 0) committed any of the following:
 
1) Wrong-way driving (reversing direction)  as per the accident description
2) Crossing a red light as per the accident description
3) Exceeding passenger capacity as per the accident description
4) if the vehicle was stolen as per the accident description
5) If License_Expiry_Date < Accident_Date  
   — Verification is performed only if the value actually exists  
   — If it is "Not Identify", empty, or not present → this violation does not apply  
6) If License_Type_From_Make_Model ≠ "Any License"  
   and does not match or resemble License_Type_From_Request  
   — If license data is "Not Identify" → not considered a violation  
 
If the above 6 rules applies then respond with ACCEPTED_WITH_RECOVERY, if it does not apply then respond with ACCEPTED.
 
=====================================================================
📦 Required Output — JSON Only
=====================================================================
 
Return the result for the specified party only:
 
{
  "decision": "REJECTED | ACCEPTED | ACCEPTED_WITH_RECOVERY",
  "reasoning": "Very brief reason based only on the data (in English)",
  "classification": "Must include the rule/condition in English used to make the decision",
  "applied_conditions": ["List of conditions/rules that were applied"]
}
 
❗ Do not write anything outside JSON  
❗ Do not use examples  
❗ Do not assume any information  
❗ Work only on the specified row
 
/////////////////////////////////////////////////////////""",
                "compact_prompt_template": """Analyze Party {party_index} for insurance claim decision.

DATA (JSON):
{data}

RULES:
1. If liability=100% → REJECTED (always, no exception)
2. If non-cooperative + liability in [0,25,50,75]% → ACCEPTED (unless rejection condition 1-16 applies)
3. If cooperative + liability<100% + responsible party (100%) not cooperative → REJECTED
4. If cooperative + liability<100% + responsible party (100%) cooperative → ACCEPTED
5. If liability=0% + non-cooperative → ACCEPTED
6. If liability=0% + cooperative + responsible party not cooperative → REJECTED
7. If rejection condition 1-16 applies → REJECTED
8. If recovery condition applies → ACCEPTED_WITH_RECOVERY

REJECTION CONDITIONS (1-16):
2) Sum liability for Tawuniya parties = 0
3) Damaged vehicle owned by at-fault party
4) Property of insured/under management
5) Death of insured/driver
6) Racing/testing
7) Prohibited area
8) Intentional damage
9) Collusion/staged
10) Intentional accident
11) Fleeing scene
12) Reckless driving
13) Drugs/alcohol
14) Natural disasters
15) Failure to notify
16) >5 years passed
17) Fraud

RECOVERY CONDITIONS:
1) Wrong-way driving
2) Red light violation
3) Exceeding capacity
4) Stolen vehicle
5) License_Expiry_Date < Accident_Date
6) License type mismatch

OUTPUT (JSON only):
{{
  "decision": "REJECTED|ACCEPTED|ACCEPTED_WITH_RECOVERY",
  "reasoning": "Brief reason in English",
  "classification": "Rule/condition used (e.g., Basic Rule #1 - 100% liability)",
  "applied_conditions": ["1", "2", ...]
}}""",
                "translation_prompt": """You are a professional translator specializing in motor vehicle accident reports and insurance claims (LD reports).

Translate the following Arabic text to English. Maintain technical terms and insurance terminology accurately.

Arabic Text:
{text}

Provide only the English translation, no explanations."""
            },
            "rules": {
                "basic_rules": {
                    "rule_1_100_percent_liability": {
                        "enabled": True,
                        "description": "If liability = 100% → REJECTED (always, no exception)",
                        "applies_to": "all_companies"
                    },
                    "rule_2_non_cooperative_0_25_50_75": {
                        "enabled": True,
                        "description": "If non-cooperative + liability in [0,25,50,75]% → ACCEPTED (unless rejection condition applies)",
                        "applies_to": "non_tawuniya"
                    },
                    "rule_3_cooperative_responsible_party_not_cooperative": {
                        "enabled": True,
                        "description": "If cooperative + liability<100% + responsible party (100%) not cooperative → REJECTED",
                        "applies_to": "tawuniya"
                    },
                    "rule_4_cooperative_responsible_party_cooperative": {
                        "enabled": True,
                        "description": "If cooperative + liability<100% + responsible party (100%) cooperative → ACCEPTED",
                        "applies_to": "tawuniya"
                    },
                    "rule_5_zero_liability_non_cooperative": {
                        "enabled": True,
                        "description": "If liability=0% + non-cooperative → ACCEPTED",
                        "applies_to": "non_tawuniya"
                    },
                    "rule_6_zero_liability_cooperative_responsible_not_cooperative": {
                        "enabled": True,
                        "description": "If liability=0% + cooperative + responsible party not cooperative → REJECTED",
                        "applies_to": "tawuniya"
                    }
                },
                "rejection_conditions": {
                    "condition_2_tawuniya_sum_zero": {
                        "enabled": True,
                        "description": "Sum liability for Tawuniya parties = 0"
                    },
                    "condition_3_damaged_vehicle_owned_by_at_fault": {
                        "enabled": True,
                        "description": "Damaged vehicle owned by at-fault party"
                    },
                    "condition_4_property_of_insured": {
                        "enabled": True,
                        "description": "Property of insured/under management"
                    },
                    "condition_5_death_of_insured_driver": {
                        "enabled": True,
                        "description": "Death of insured/driver"
                    },
                    "condition_6_racing_testing": {
                        "enabled": True,
                        "description": "Racing/testing"
                    },
                    "condition_7_prohibited_area": {
                        "enabled": True,
                        "description": "Prohibited area"
                    },
                    "condition_8_intentional_damage": {
                        "enabled": True,
                        "description": "Intentional damage"
                    },
                    "condition_9_collusion_staged": {
                        "enabled": True,
                        "description": "Collusion/staged"
                    },
                    "condition_10_intentional_accident": {
                        "enabled": True,
                        "description": "Intentional accident"
                    },
                    "condition_11_fleeing_scene": {
                        "enabled": True,
                        "description": "Fleeing scene"
                    },
                    "condition_12_reckless_driving": {
                        "enabled": True,
                        "description": "Reckless driving"
                    },
                    "condition_13_drugs_alcohol": {
                        "enabled": True,
                        "description": "Drugs/alcohol"
                    },
                    "condition_14_natural_disasters": {
                        "enabled": True,
                        "description": "Natural disasters"
                    },
                    "condition_15_failure_to_notify": {
                        "enabled": True,
                        "description": "Failure to notify"
                    },
                    "condition_16_more_than_5_years": {
                        "enabled": True,
                        "description": "More than 5 years passed"
                    },
                    "condition_17_fraud": {
                        "enabled": True,
                        "description": "Fraud exists"
                    }
                },
                "recovery_conditions": {
                    "condition_1_wrong_way_driving": {
                        "enabled": True,
                        "description": "Wrong-way driving"
                    },
                    "condition_2_red_light_violation": {
                        "enabled": True,
                        "description": "Red light violation"
                    },
                    "condition_3_exceeding_capacity": {
                        "enabled": True,
                        "description": "Exceeding capacity"
                    },
                    "condition_4_stolen_vehicle": {
                        "enabled": True,
                        "description": "Stolen vehicle"
                    },
                    "condition_5_license_expired": {
                        "enabled": True,
                        "description": "License_Expiry_Date < Accident_Date"
                    },
                    "condition_6_license_type_mismatch": {
                        "enabled": True,
                        "description": "License type mismatch"
                    }
                }
            },
            "response_fields": {
                "enabled_fields": {
                    "Party": True,
                    "Party_ID": True,
                    "Party_Name": True,
                    "Liability": True,
                    "Decision": True,
                    "Classification": True,
                    "Reasoning": True,
                    "Applied_Conditions": True,
                    "isDAA": True,
                    "Suspect_as_Fraud": True,
                    "DaaReasonEnglish": True,
                    "Suspected_Fraud": True,
                    "model_recovery": True,
                    "License_Type_From_Make_Model": True,
                    "Policyholder_ID": True
                },
                "description": {
                    "Party": "Party identifier",
                    "Party_ID": "Party ID from request",
                    "Party_Name": "Party name from request",
                    "Liability": "Liability percentage",
                    "Decision": "Decision (ACCEPTED/REJECTED/ACCEPTED_WITH_RECOVERY)",
                    "Classification": "Rule/condition used for decision",
                    "Reasoning": "Brief explanation in English",
                    "Applied_Conditions": "List of condition numbers that applied",
                    "isDAA": "DAA flag from request",
                    "Suspect_as_Fraud": "Suspect as fraud flag from request",
                    "DaaReasonEnglish": "DAA reason in English from request",
                    "Suspected_Fraud": "Suspected fraud flag (calculated: 'Suspected Fraud' if isDAA=TRUE, else null)",
                    "model_recovery": "Model recovery flag (calculated based on license type mismatch)",
                    "License_Type_From_Make_Model": "License type from make/model lookup",
                    "Policyholder_ID": "Policyholder ID from request"
                }
            }
        }
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration"""
        with CONFIG_LOCK:
            if self._config is None:
                self._load_config()
            return self._config.copy()
    
    def get_prompts(self) -> Dict[str, str]:
        """Get all prompts"""
        config = self.get_config()
        return config.get("prompts", {})
    
    def get_rules(self) -> Dict[str, Any]:
        """Get all rules"""
        config = self.get_config()
        return config.get("rules", {})
    
    def update_prompts(self, prompts: Dict[str, str]) -> bool:
        """Update prompts"""
        with CONFIG_LOCK:
            try:
                if self._config is None:
                    self._load_config()
                self._config["prompts"].update(prompts)
                self._config["last_updated"] = datetime.now().isoformat()
                self._save_config()
                return True
            except Exception as e:
                print(f"Error updating prompts: {e}")
                return False
    
    def update_rules(self, rules: Dict[str, Any]) -> bool:
        """Update rules"""
        with CONFIG_LOCK:
            try:
                if self._config is None:
                    self._load_config()
                # Deep merge rules
                self._deep_merge(self._config["rules"], rules)
                self._config["last_updated"] = datetime.now().isoformat()
                self._save_config()
                return True
            except Exception as e:
                print(f"Error updating rules: {e}")
                return False
    
    def _deep_merge(self, base: Dict, update: Dict):
        """Deep merge two dictionaries"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def _save_config(self):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")
            raise
    
    def reload_config(self):
        """Reload configuration from file"""
        with CONFIG_LOCK:
            self._load_config()


# Global instance
config_manager = ConfigManager()

