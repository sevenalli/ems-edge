#!/usr/bin/env bash
# install_service.sh — Register EMS Edge as a systemd service that starts on boot.
# Run once as a user with sudo rights:  chmod +x install_service.sh && ./install_service.sh
set -e

SERVICE_NAME="ems-edge"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CURRENT_USER="$(whoami)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "============================================"
echo " EMS Edge — Install systemd service"
echo " Project dir : $SCRIPT_DIR"
echo " Run as user : $CURRENT_USER"
echo " Python      : $PYTHON"
echo "============================================"

# --- Pre-flight checks ---
if [ ! -f "$SCRIPT_DIR/Ems-edge.py" ]; then
    echo "[ERROR] Ems-edge.py not found in $SCRIPT_DIR."
    echo "        Run this script from the project directory."
    exit 1
fi

if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] Virtual environment not found at $SCRIPT_DIR/.venv"
    echo "        Run ./setup_venv.sh first."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "[WARN] .env file not found. The service will use OS environment variables only."
fi

# --- Write service file ---
echo "[INFO] Writing $SERVICE_FILE ..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=EMS Edge PLC → MQTT Bridge
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON} ${SCRIPT_DIR}/Ems-edge.py
EnvironmentFile=${SCRIPT_DIR}/.env
Restart=always
RestartSec=10
User=${CURRENT_USER}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# --- Enable and start ---
echo "[INFO] Reloading systemd daemon ..."
sudo systemctl daemon-reload

echo "[INFO] Enabling service (starts on boot) ..."
sudo systemctl enable "$SERVICE_NAME"

echo "[INFO] Starting service now ..."
sudo systemctl start "$SERVICE_NAME"

# --- Status ---
echo ""
echo "============================================"
echo " [OK] Service installed and started!"
echo ""
echo " Useful commands:"
echo "   Status : sudo systemctl status $SERVICE_NAME"
echo "   Logs   : journalctl -u $SERVICE_NAME -f"
echo "   Stop   : sudo systemctl stop $SERVICE_NAME"
echo "   Restart: sudo systemctl restart $SERVICE_NAME"
echo "   Remove : sudo systemctl disable $SERVICE_NAME"
echo "            sudo rm $SERVICE_FILE"
echo "============================================"

sudo systemctl status "$SERVICE_NAME" --no-pager
