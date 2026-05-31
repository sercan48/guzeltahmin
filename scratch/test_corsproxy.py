import requests
import urllib3
import socket
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Test corsproxy.io DNS resolution first
try:
    ip = socket.gethostbyname("corsproxy.io")
    print("corsproxy.io IP:", ip)
except Exception as e:
    print("corsproxy.io DNS Error:", e)

url = "https://corsproxy.io/?url=http://www.football-data.co.uk/new/NOR.csv"
print("Fetching from:", url)
try:
    resp = requests.get(url, timeout=15, verify=False)
    print("Status:", resp.status_code)
    print("Length:", len(resp.content))
    print("Content preview:")
    print(resp.text[:200])
except Exception as e:
    print("Error:", e)
