import requests
import socket

print("--- Querying Google DoH for www.football-data.co.uk ---")
doh_url = "https://dns.google/resolve?name=www.football-data.co.uk"
try:
    resp = requests.get(doh_url, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        print("DoH response:")
        print(data)
        answers = data.get("Answer", [])
        ips = [ans["data"] for ans in answers if ans["type"] == 1]
        print("Real IPs:", ips)
        
        if ips:
            real_ip = ips[0]
            print(f"\nTesting direct connection to real IP: {real_ip} with Host: www.football-data.co.uk")
            headers = {"Host": "www.football-data.co.uk"}
            # Fetch E0.csv using HTTP and real IP
            url = f"http://{real_ip}/mmz4281/2526/E0.csv"
            print("Fetching:", url)
            
            # Since requests doesn't support easy host routing with HTTPS, we do HTTP first
            test_resp = requests.get(url, headers=headers, timeout=15)
            print("Direct IP HTTP Status:", test_resp.status_code)
            print("Content preview:")
            print(test_resp.text[:200])
    else:
        print("DoH request failed with status:", resp.status_code)
except Exception as e:
    print("Error:", e)
