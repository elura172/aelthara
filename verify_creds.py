from dotenv import load_dotenv
import os

load_dotenv()   # loads .env from the current folder

from py_clob_client.client import ClobClient

print("✅ .env loaded. POLYMARKET_PRIVATE_KEY =", "present" if os.getenv("POLYMARKET_PRIVATE_KEY") else "MISSING")

client = ClobClient(
    host="https://clob.polymarket.com",
    key=os.getenv("POLYMARKET_PRIVATE_KEY"),
    chain_id=137,
)

creds = client.create_or_derive_api_creds()

print("\n🎉 L2 CLOB Credentials generated successfully!")
print("API Key     :", creds.api_key)
print("Secret      :", creds.api_secret)
print("Passphrase  :", creds.api_passphrase)