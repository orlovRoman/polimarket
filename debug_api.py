import requests
import json

api_url = "https://gamma-api.polymarket.com/markets"
params = {"limit": 1}
response = requests.get(api_url, params=params)
print(json.dumps(response.json(), indent=2))
