import os
import requests
import time

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
API_HOST = "api-football-v1.p.rapidapi.com"

# In-memory Cache
CACHE = {}
CACHE_TTL = 10800  # ৩ ঘণ্টা (সেকেন্ডে)

def get_headers():
    return {
        "X-RapidAPI-Key": FOOTBALL_API_KEY,
        "X-RapidAPI-Host": API_HOST
    }

def fetch_upcoming_fixtures():
    current_time = time.time()
    if "upcoming_fixtures" in CACHE:
        data, timestamp = CACHE["upcoming_fixtures"]
        if current_time - timestamp < CACHE_TTL:
            return data

    url = f"https://{API_HOST}/v3/fixtures?next=10"
    headers = get_headers()
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            fixtures = res_json.get("response", [])
            CACHE["upcoming_fixtures"] = (fixtures, current_time)
            return fixtures
    except Exception as e:
        print(f"API Fetch Error: {e}")
    return []

def fetch_team_head_to_head(team1_id, team2_id):
    cache_key = f"h2h_{team1_id}_{team2_id}"
    current_time = time.time()
    if cache_key in CACHE:
        data, timestamp = CACHE[cache_key]
        if current_time - timestamp < CACHE_TTL:
            return data

    url = f"https://{API_HOST}/v3/fixtures/headtohead?h2h={team1_id}-{team2_id}&last=5"
    headers = get_headers()
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            h2h = res_json.get("response", [])
            CACHE[cache_key] = (h2h, current_time)
            return h2h
    except Exception as e:
        print(f"H2H API Error: {e}")
    return []
      
