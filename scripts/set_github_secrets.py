#!/usr/bin/env python3
"""
Tek seferlik kullanım: GitHub Actions secrets'larını API üzerinden ekler.

Kullanım (kendi terminalinde):
    pip install PyNaCl requests
    GITHUB_TOKEN=ghp_xxxxx python scripts/set_github_secrets.py

GITHUB_TOKEN için:
    github.com → Settings → Developer settings → Personal access tokens → Fine-grained
    İzinler: Repository secrets → Read and write
"""
import base64, os, sys
import requests
from nacl import public

OWNER = "sercan48"
REPO  = "guzeltahmin"

SECRETS = {
    "TELEGRAM_BOT_TOKEN":        os.getenv("TELEGRAM_BOT_TOKEN",        ""),
    "TELEGRAM_PERSONAL_CHANNEL": os.getenv("TELEGRAM_PERSONAL_CHANNEL", ""),
    "FOOTBALL_DATA_ORG_KEY":     os.getenv("FOOTBALL_DATA_ORG_KEY",     ""),
    "ODDS_API_KEY":              os.getenv("ODDS_API_KEY",              ""),
}

def main():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        print("Hata: GITHUB_TOKEN ortam değişkeni gerekli.")
        print("  export GITHUB_TOKEN=ghp_xxxxx")
        sys.exit(1)

    missing = [k for k, v in SECRETS.items() if not v]
    if missing:
        print(f"Hata: Şu değerler eksik (env var olarak ver veya scripte yaz): {missing}")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    pk_resp = requests.get(
        f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets/public-key",
        headers=headers, timeout=10,
    )
    if pk_resp.status_code == 401:
        print("Hata: Token geçersiz veya 'secrets' yazma izni yok.")
        sys.exit(1)
    pk_resp.raise_for_status()

    pk_data = pk_resp.json()
    box = public.SealedBox(public.PublicKey(base64.b64decode(pk_data["key"])))

    for name, value in SECRETS.items():
        encrypted = base64.b64encode(box.encrypt(value.encode())).decode()
        r = requests.put(
            f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets/{name}",
            headers=headers,
            json={"encrypted_value": encrypted, "key_id": pk_data["key_id"]},
            timeout=10,
        )
        if r.status_code in (201, 204):
            print(f"  ✓  {name}")
        else:
            print(f"  ✗  {name}  →  {r.status_code} {r.text}")

    print("\nTamamlandı. GitHub Actions sekmesinden workflow'u test edebilirsin.")

if __name__ == "__main__":
    main()
