import requests
import json

url = "http://localhost:8000/estimate"
data = {
    "district": 13,
    "area_sqm": 50,
    "num_rooms": 2,
    "needs_plumbing": True,
    "needs_electrical": True,
    "needs_flooring": True,
    "needs_full_demolition": True
}

try:
    response = requests.post(url, json=data)
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
