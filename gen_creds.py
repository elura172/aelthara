from dotenv import load_dotenv
import os
load_dotenv("/opt/aelthara/.env")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# Step 1: Init with just private key
client = ClobClient(
    "https://clob.polymarket.com",
    key=os.environ["POLYMARKET_PRIVATE_KEY"],
    chain_id=137,
)