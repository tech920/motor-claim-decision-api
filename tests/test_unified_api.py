import pytest
import base64
import json
import sys
import os

# Add the workspace root to sys.path so we can import unified_api_server
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from unified_api_server import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@pytest.fixture
def auth_headers():
    # encoding credentials for Basic Auth
    credentials = f"admin:Surety@2030"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    return {
        'Authorization': f'Basic {encoded_credentials}',
        'Content-Type': 'application/json'
    }

def test_health_check(client):
    """Test the health check endpoint"""
    response = client.get('/health')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'healthy'
    assert data['service'] == 'unified'

def test_api_health_check(client):
    """Test the API health check endpoint"""
    response = client.get('/api/health')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'healthy'

def test_process_claim_no_data(client, auth_headers):
    """Test processing with no data"""
    response = client.post('/process-claim-simplified', headers=auth_headers, json={})
    assert response.status_code == 400
    data = json.loads(response.data)
    assert 'error' in data

def test_process_claim_missing_parties(client, auth_headers):
    """Test processing with missing Parties"""
    payload = {"claim_type": "TP"}
    response = client.post('/process-claim-simplified', headers=auth_headers, json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "Parties" in data.get('error', '')

def test_process_claim_invalid_claim_type(client, auth_headers):
    """Test processing with invalid claim_type"""
    payload = {
        "Parties": [],
        "claim_type": "INVALID"
    }
    response = client.post('/process-claim-simplified', headers=auth_headers, json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "claim_type" in data.get('error', '')

def test_process_claim_missing_claim_type(client, auth_headers):
    """Test processing with missing claim_type"""
    payload = {
        "Parties": []
    }
    response = client.post('/process-claim-simplified', headers=auth_headers, json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "claim_type" in data.get('error', '')

def test_auth_required(client):
    """Test that auth is required for protected endpoints"""
    response = client.post('/process-claim-simplified', json={})
    assert response.status_code == 401
