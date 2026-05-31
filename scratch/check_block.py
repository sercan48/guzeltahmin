import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "http://www.football-data.co.uk/mmz4281/2526/E0.csv"
print("Checking URL:", url)
try:
    resp = requests.get(url, timeout=10, verify=False)
    print("Status:", resp.status_code)
    print("Content preview:")
    print(resp.text[:200])
except Exception as e:
    print("Error:", e)
