# Polymarket Relayer API Setup

## Overview
This documents the auth setup required for Aelthara to trade on Polymarket using a proxy wallet.

## Wallet Architecture
- **Signer** (`0x59b2...`): Your MetaMask wallet - signs transactions
- **Proxy/Funder** (`0xa70D...`): Polymarket proxy wallet - holds funds & positions

## Required Environment Variables

```bash
# Signer (MetaMask private key)
POLYMARKET_PRIVATE_KEY=<your-private-key>

# Proxy wallet (from Polymarket profile)
POLYMARKET_PROXY_ADDRESS=0x0000000000000000000000000000000000000000

# Key owner address (your signer)
RELAYER_API_KEY_ADDRESS=0x0000000000000000000000000000000000000000
```

## API Credentials (Derived, Not in UI)

The L2 API credentials are **derived from your wallet**, not shown in the UI:

```python
# These are derived at runtime - do not set manually
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
```

## SDK Configuration (executor.py)

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# Create client with signer + funder
client = ClobClient(
    'https://clob.polymarket.com',
    key=pk,                    # signer private key
    chain_id=137,             # Polygon
    signature_type=2,          # CRITICAL: 2 = proxy wallet auth
    funder=proxy_address,     # CRITICAL: proxy wallet address
)

# Derive API credentials (binds to current signer/funder)
creds = client.derive_api_key()
client.set_api_creds(ApiCreds(
    api_key=creds.api_key,
    api_secret=creds.api_secret,
    api_passphrase=creds.api_passphrase,
))
```

## Key Insights

1. **signature_type=2** is required for proxy wallet transactions
2. **funder** must be the proxy wallet address (not signer)
3. **derive_api_key()** must be called AFTER setting signature_type and funder
4. Balance is held in proxy - not visible on Polygonscan but visible in Polymarket UI

## Troubleshooting

| Error | Solution |
|-------|----------|
| 401 Unauthorized | Re-derive credentials: `client.derive_api_key()` |
| Invalid signature | Check `signature_type=2` is set |
| Balance: 0 | Verify USDC is in proxy wallet, not signer |
| "not enough balance" | USDC not in proxy wallet - deposit via Polymarket UI |

## Fresh Setup Steps

1. Send USDC to proxy wallet address (shown in Polymarket profile)
2. Ensure signer private key is correct
3. Set `POLYMARKET_PROXY_ADDRESS` to proxy address
4. Set `RELAYER_API_KEY_ADDRESS` to signer address
5. Code derives credentials automatically at runtime
