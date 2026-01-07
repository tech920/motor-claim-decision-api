"""
Authentication Manager for Motor Claim Decision API
Handles user authentication and password management
"""

import json
import os
from werkzeug.security import generate_password_hash, check_password_hash
from typing import Dict, Optional, Tuple

USERS_FILE = "users.json"


class AuthManager:
    """Manages user authentication and authorization"""
    
    def __init__(self, users_file: str = USERS_FILE):
        self.users_file = users_file
        self.users = self._load_users()
    
    def _load_users(self) -> Dict:
        """Load users from JSON file"""
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("users", {})
            except Exception as e:
                print(f"Error loading users file: {e}")
                return {}
        return {}
    
    def _save_users(self):
        """Save users to JSON file"""
        try:
            data = {
                "users": self.users,
                "notes": "Passwords are stored in plain text. For production, use hashed passwords."
            }
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving users file: {e}")
            return False
    
    def verify_user(self, username: str, password: str) -> bool:
        """Verify username and password"""
        if username not in self.users:
            return False
        
        user = self.users[username]
        if not user.get("active", True):
            return False
        
        # Check password (plain text for now, can be upgraded to hashed)
        stored_password = user.get("password", "")
        return stored_password == password
    
    def add_user(self, username: str, password: str, role: str = "user", active: bool = True) -> bool:
        """Add a new user"""
        if username in self.users:
            return False  # User already exists
        
        self.users[username] = {
            "password": password,
            "role": role,
            "active": active
        }
        return self._save_users()
    
    def update_user(self, username: str, password: Optional[str] = None, 
                   role: Optional[str] = None, active: Optional[bool] = None) -> bool:
        """Update an existing user"""
        if username not in self.users:
            return False
        
        if password is not None:
            self.users[username]["password"] = password
        if role is not None:
            self.users[username]["role"] = role
        if active is not None:
            self.users[username]["active"] = active
        
        return self._save_users()
    
    def delete_user(self, username: str) -> bool:
        """Delete a user"""
        if username not in self.users:
            return False
        
        del self.users[username]
        return self._save_users()
    
    def list_users(self) -> Dict:
        """List all users (without passwords)"""
        result = {}
        for username, user_data in self.users.items():
            result[username] = {
                "role": user_data.get("role", "user"),
                "active": user_data.get("active", True)
            }
        return result
    
    def get_user_role(self, username: str) -> Optional[str]:
        """Get user role"""
        if username not in self.users:
            return None
        return self.users[username].get("role", "user")


# Global auth manager instance
auth_manager = AuthManager()

