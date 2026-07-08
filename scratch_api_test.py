import urllib.request
import json
import traceback

try:
    url = "http://localhost:8050/api/signals?strategy=scout&days=all&limit=20&page=1"
    resp = urllib.request.urlopen(url)
    data = json.loads(resp.read())
    print("Keys:", data.keys())
    print("Stats:", data.get('stats'))
except Exception as e:
    traceback.print_exc()
