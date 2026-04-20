#!/usr/bin/env bash
# Aelthara Deployment Script (Private Repo Compatible)
#
# FIRST TIME SETUP:
#   1. Clone repo with SSH key or PAT:
#      git clone git@github.com:elura172/aelthara.git
#      # or: git clone https://<PAT>@github.com/elura172/aelthara.git
#
#   2. Run setup from repo directory:
#      cd aelthara
#      sudo ./deploy.sh setup
#
#   3. Configure API keys:
#      sudo nano /opt/aelthara/.env
#
#   4. Start the service:
#      sudo ./deploy.sh start
#
# UPDATES:
#   cd /opt/aelthara && sudo ./deploy.sh update
#
# COMMANDS:
#   setup    - Initial setup (install deps, copy files, setup systemd)
#   install  - Copy files, setup systemd, and ensure .env exists
#   update   - Pull latest changes and restart service
#   start    - Start the aelthara service
#   stop     - Stop the aelthara service
#   restart  - Restart the aelthara service
#   status   - Show service status and recent logs
#   logs     - Tail the pulse and telegram logs

set -euo pipefail

APP_DIR="/opt/aelthara"
SERVICE_NAME="aelthara"
SERVICE_FILE="aelthara.service"

require_repo_dir() {
    if [[ ! -f "pulse.py" ]] || [[ ! -f "oracle.py" ]]; then
        echo "❌ Error: Run this script from the aelthara repo directory"
        echo "   git clone git@github.com:elura172/aelthara.git"
        echo "   cd aelthara"
        echo "   sudo ./deploy.sh setup"
        exit 1
    fi
}

copy_repo_contents() {
    echo "📁 Setting up ${APP_DIR}..."
    mkdir -p "${APP_DIR}"
    cp -r . "${APP_DIR}/"
}

setup_python_environment() {
    echo "🐍 Setting up Python environment..."
    cd "${APP_DIR}"
    python3 -m venv venv
    # shellcheck disable=SC1091
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
}

ensure_env_file() {
    if [[ ! -f "${APP_DIR}/.env" ]]; then
        if [[ -f "${APP_DIR}/.env.example" ]]; then
            cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
            echo "📝 Created .env from .env.example - edit with your API keys"
        else
            touch "${APP_DIR}/.env"
            echo "📝 Created empty .env file - add your API keys"
        fi
    fi
}

install_systemd_service() {
    echo "⚙️ Installing systemd service..."
    if [[ ! -f "${APP_DIR}/${SERVICE_FILE}" ]]; then
        echo "❌ ${APP_DIR}/${SERVICE_FILE} not found."
        echo "   Add ${SERVICE_FILE} to the repository before installing the service."
        exit 1
    fi
    cp "${APP_DIR}/${SERVICE_FILE}" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
}

setup() {
    echo "🚀 Aelthara Setup"

    require_repo_dir

    echo "📦 Installing dependencies..."
    apt-get update
    apt-get install -y python3 python3-pip python3-venv

    copy_repo_contents
    setup_python_environment
    ensure_env_file
    install_systemd_service

    echo ""
    echo "✅ Setup complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Edit your API keys: sudo nano /opt/aelthara/.env"
    echo "  2. Start the service:  sudo systemctl start aelthara"
    echo "  3. Check status:       sudo systemctl status aelthara"
    echo "  4. View logs:          sudo ./deploy.sh logs"
}

install() {
    echo "📦 Installing Aelthara files and service..."

    require_repo_dir
    copy_repo_contents
    ensure_env_file
    install_systemd_service

    echo "✅ Install complete!"
}

update() {
    echo "🔄 Updating Aelthara..."

    cd "${APP_DIR}" || { echo "❌ /opt/aelthara not found. Run setup first."; exit 1; }

    echo "📥 Pulling latest changes..."
    git pull

    echo "📦 Updating dependencies..."
    # shellcheck disable=SC1091
    source venv/bin/activate
    pip install -r requirements.txt

    echo "🔄 Restarting service..."
    systemctl restart "${SERVICE_NAME}"

    echo "✅ Update complete!"
    systemctl status "${SERVICE_NAME}" --no-pager
}

start() {
    echo "▶️ Starting ${SERVICE_NAME}..."
    systemctl start "${SERVICE_NAME}"
    systemctl status "${SERVICE_NAME}" --no-pager
}

stop() {
    echo "⏹️ Stopping ${SERVICE_NAME}..."
    systemctl stop "${SERVICE_NAME}"
}

restart() {
    echo "🔄 Restarting ${SERVICE_NAME}..."
    systemctl restart "${SERVICE_NAME}"
    systemctl status "${SERVICE_NAME}" --no-pager
}

status() {
    systemctl status "${SERVICE_NAME}" --no-pager
}

logs() {
    tail -F "${APP_DIR}/pulse.log" "${APP_DIR}/telegram.log"
}

usage() {
    cat <<EOF
Usage: $0 {setup|install|update|start|stop|restart|status|logs}
EOF
}

main() {
    case "${1:-}" in
        setup) setup ;;
        install) install ;;
        update) update ;;
        start) start ;;
        stop) stop ;;
        restart) restart ;;
        status) status ;;
        logs) logs ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "${1:-}"
