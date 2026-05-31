import socket

domains = [
    'www.football-data.co.uk',
    'football-data.co.uk',
    'api.allorigins.win',
    'google.com'
]

for d in domains:
    try:
        ip = socket.gethostbyname(d)
        print(f"Domain: {d} -> IP: {ip}")
    except Exception as e:
        print(f"Domain: {d} -> Error: {e}")
