# Aelthara Agent Guidance

## Essential Commands
- **Setup**: `sudo ./deploy.sh setup` (first time only)
- **Update**: `sudo ./deploy.sh update` (pulls latest & restarts)
- **Start**: `sudo ./deploy.sh start`
- **Stop**: `sudo ./deploy.sh stop`
- **Logs**: `sudo ./deploy.sh logs` (tails pulse.log)
- **Status**: `sudo ./deploy.sh status`

## Cron Mode (15-minute scan)
For cron-based orchestration instead of continuous daemon:

```bash
# Crontab entry (runs every 15 minutes)
*/15 * * * * cd /opt/aelthara && /opt/aelthara/venv/bin/python cron_pulse.py >> /var/log/aelthara-cron.log 2>&1
```

## Environment & Dependencies
- Uses Python virtual environment (`venv/`) - activate with `source venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- API keys required in `/opt/aelthara/.env`:
  - `METEO_BLUE_API` (Meteoblue)
  - `POLYMARKET_PRIVATE_KEY` (trading)
  - `POLYMARKET_API_KEY`
  - `POLYMARKET_API_SECRET`
  - `POLYMARKET_API_PASSPHRASE`
  - `POLYMARKET_PROXY_ADDRESS`
  - `AELTHARA_BANKROLL` (default $3.65)

## Execution Flow
- Main entrypoint: `pulse.py` (async loop, 15-min poll by default)
- Cron entrypoint: `cron_pulse.py` (single cycle, for cron)
- Oracle: weather prediction logic (`oracle.py`)
- Gate: decision making (`gate.py`) 
- Executor: trade execution (`executor.py`)
- Market data: `market_scraper.py`
- Rossby: 500hPa atmospheric phase (`rossby.py`)

## Important Notes
- Service runs as systemd service `aelthara` (not direct python execution)
- Logs written to `/opt/aelthara/pulse.log`
- Do NOT edit files in `/opt/aelthara/` directly — update repo then run `deploy.sh update`
- For development: run `python pulse.py` after activating venv
- Use `cron_pulse.py` for cron-based deployments