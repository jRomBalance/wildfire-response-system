import httpx

key = "673CC9F0-C4A8-4AD2-A280-453EEB8D1141"

# Try ZIP code approach instead of city name
url = "https://www.airnowapi.org/aq/observation/zipCode/current/"
params = {
    "format": "application/json",
    "zipCode": "48201",   # Detroit ZIP
    "distance": 25,
    "API_KEY": key,
}

with httpx.Client(follow_redirects=True, timeout=20.0) as client:
    r = client.get(url, params=params)
    print(f"Status: {r.status_code}")
    print(f"URL hit: {r.url}")
    print(f"Response: {r.text[:500]}")