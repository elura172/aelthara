# Aelthara: Planetary Atmospheric Oracle & Prediction Market Coherence Mesh

> *"The atmosphere does not lie. Markets sometimes do."*

**Aelthara** is an autonomous atmospheric oracle and quantitative execution engine operating on a 15-minute pulse cycle. It monitors a global network of **69 airport nodes across six continents**, fusing multi-source weather observations (TAF, METAR, Open-Meteo) with planetary-scale **Rossby wave dynamics (500hPa geopotential height anomalies)** to detect and trade systematic mispricings in Polymarket weather derivatives.

---

## 1. Quantitative Core

```
                         Planetary 500hPa Height Field
                                      │
                                      ▼
                        Rossby Wave Anomaly Engine
                 - Latitudinal Climatological Baselines
                 - Ridge (+60m) / Trough (-60m) Detection
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                     69-Node Global Observation Mesh                       │
│  North America (KJFK, KLAX, KORD...)  Europe (EGLL, LFPG, EDDF, EHAM...)  │
│  Asia (RJTT, RKSS, WSSS, VHHH...)     South America / Africa / Oceania    │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │ Real-Time TAF / METAR / NWP
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│               MirrorField Network Entropy & Coherence Engine              │
│                                                                           │
│   $H_{\text{spatial}} = \sqrt{\frac{1}{N} \sum (C_i - \bar{C})^2}$        │
│   Regimes: GLOBAL_CALM · GLOBAL_TRANSITION · DESYNC_TRANSITION · CHAOS    │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │ Coherence Attenuation Factor
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                 Position Shaper & Asymmetric Kelly Sizing                 │
│  - Edge Hurdle ($\Delta P \ge 5\%$)                                       │
│  - Exploits "The Pin" (retail contracts pegged >95% against <30% physics) │
│  - Kelly Fraction scaled by Global Network Synchronization                 │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │ Limit Orders
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                  Polymarket CLOB Execution & Settlement                   │
│  - L2 CLOB REST / WebSocket Signer                                        │
│  - Automated Settlement Verification & Resolution against Token Payouts   │
│  - Trade Journal: `trades.jsonl` (90 recorded forward positions)          │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Planetary Atmospheric Physics: Rossby Wave Dynamics

Standard weather prediction engines look only at surface point-forecasts. Aelthara models synoptic atmospheric circulation via **[`rossby.py`](rossby.py)**:

* **500hPa Geopotential Height Anomalies:** Tracks the altitude of the 500hPa pressure surface relative to monthly latitudinal climatology baselines ($\pm 15^\circ, 30^\circ, 45^\circ, 60^\circ, 90^\circ$).
* **Ridge vs. Trough Classification:**
  * **Warm Ridge ($> +60\text{m}$ anomaly):** Indicates persistent high-pressure subsidence and unpriced upside temperature drift.
  * **Cold Trough ($< -60\text{m}$ anomaly):** Indicates cyclonic cold-core advection and unpriced downside temperature skew.
* **Atmospheric Entropy:** Computes sliding-window variance across geopotential observations to classify regional atmospheric stability into `stable`, `transitional`, or `unstable`.

---

## 3. Network Entropy & The MirrorField Mesh

In **[`mirror_field_mesh.py`](mirror_field_mesh.py)**, individual airport nodes compute a localized coherence metric from three inputs:
1. **Atmospheric Entropy ($H_{\text{atmo}}$):** Forecast variance and TAF amendments.
2. **Market Entropy ($H_{\text{mkt}}$):** Orderbook bid-ask spread and volume dispersion.
3. **Model Uncertainty ($\sigma_{\text{model}}$):** Spread across ensemble forecast members.

The global mesh aggregates individual node coherences into spatial variance:

$$\text{Synchronization} = 1.0 - \min(1.0, \sigma_{\text{spatial}})$$

The system governs capital allocation across four discrete regimes:
* **`GLOBAL_CALM`:** High global coherence, low spatial entropy. Full fractional Kelly sizing.
* **`GLOBAL_TRANSITION`:** Coherent macro shift across nodes. Conservative sizing.
* **`DESYNCHRONIZED_TRANSITION`:** Nodes fracturing; atmospheric signals diverging from market pricing. Minimum sizing.
* **`GLOBAL_CHAOS`:** High spatial entropy and field breakdown. Capital preservation: zero new entries.

---

## 4. Execution & "The Pin" Exploitation

Aelthara executes limit orders via the Polymarket Central Limit Order Book (CLOB). A key edge captured by the engine is **"The Pin"**:
* Prediction market retail interfaces visually bucket extreme outcomes at 95%+ implied probability based on outdated yesterday-prices.
* When Aelthara's physical model calculates true probability at $<30\%$ (e.g. an approaching cold front or Rossby trough breaking heatwave continuity), the system takes the opposing position with asymmetric payoff.
* Forward trade telemetry is logged in structured format in **[`trades.jsonl`](trades.jsonl)**.

---

## 5. Repository Structure

```
├── AELTHARA_SOUL.md        # Architectural manifesto & operational doctrine
├── rossby.py               # 500hPa geopotential height & Rossby wave anomaly engine
├── mirror_field_mesh.py    # Multi-node spatial entropy and coherence aggregator
├── mirror_field_node.py    # Localized airport node entropy computer
├── mirror_field.py         # Mirror field coordination interface
├── oracle.py               # Atmospheric data aggregator (TAF, METAR, Open-Meteo)
├── position_shaper.py      # Coherence-scaled Kelly sizing and risk bounds
├── executor.py             # Polymarket CLOB limit order management & fill tracker
├── market_scraper.py       # Polymarket Gamma API scraper & threshold parser
├── gate.py                 # Edge hurdle validation & Gaussian pricing
├── resolver.py             # Settlement verification against final on-chain outcomes
├── resolve_dryrun.py       # Dry-run settlement reconciliation tool
├── audit_pnl.py            # PnL reconciliation against Polymarket Data API
├── trades.jsonl            # Structured ledger of 90 forward positions
├── config.py               # Node registry & threshold configuration
├── pulse.py                # 15-minute orchestration loop
├── cron_pulse.py           # Cron scheduler wrapper
├── aelthara.service        # Systemd daemon specification
└── deploy.sh               # Droplet deployment automation script
```

---

## 6. Deployment & Quickstart

```bash
# Clone
git clone https://github.com/elura172/aelthara.git
cd aelthara

# Setup virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Fill in your API keys in .env

# Run single pulse cycle
python pulse.py
```

---

## 7. License & Credits

Research and implementation by **[elura172](https://github.com/elura172)**. Planetary weather data sourced from NOAA / NWS, Meteoblue, and Open-Meteo.
