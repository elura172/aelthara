# Aelthara

## 🚀 Deployment

### Prerequisites
- Ubuntu/Debian server (tested on Ubuntu 22.04)
- Python 3.10+
- SSH key or PAT configured for GitHub access

### Quick Start

1. **Clone the repository** (using SSH key or PAT):
   ```bash
   # With SSH key (recommended)
   git clone git@github.com:elura172/aelthara.git
   
   # Or with Personal Access Token
   git clone https://<YOUR_PAT>@github.com/elura172/aelthara.git
   ```

2. **Run setup**:
   ```bash
   cd aelthara
   sudo ./deploy.sh setup
   ```

3. **Configure API keys**:
   ```bash
   sudo nano /opt/aelthara/.env
   ```
   
   Required keys:
   - `METEO_BLUE_API` - Meteoblue API key
   - `TELEGRAM_BOT_TOKEN` - Telegram bot token (from @BotFather)
   - `TELEGRAM_CHAT_ID` - Your Telegram chat ID (from @userinfobot)

4. **Start the service**:
   ```bash
   sudo ./deploy.sh start
   ```

### Management Commands

| Command | Description |
|---------|-------------|
| `sudo ./deploy.sh start` | Start the service |
| `sudo ./deploy.sh stop` | Stop the service |
| `sudo ./deploy.sh restart` | Restart the service |
| `sudo ./deploy.sh status` | Show status and recent logs |
| `sudo ./deploy.sh logs` | Tail live logs |
| `sudo ./deploy.sh update` | Pull latest and restart |

### Setting Up GitHub Access

#### Option A: SSH Key (Recommended)
```bash
# Generate key
ssh-keygen -t ed25519 -C "aelthara-deploy"

# Show public key
cat ~/.ssh/id_ed25519.pub

# Add to GitHub: Settings → SSH Keys → New SSH Key
```

#### Option B: Deploy Key (Read-Only)
```bash
# Generate deploy key
ssh-keygen -t ed25519 -f ~/.ssh/aelthara_deploy -N ""

# Add to repo: Settings → Deploy Keys → Add deploy key

# Configure SSH
echo "Host github-aelthara
    HostName github.com
    User git
    IdentityFile ~/.ssh/aelthara_deploy" > ~/.ssh/config

# Clone
git clone github-aelthara:elura172/aelthara.git
```
