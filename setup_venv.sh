#!/usr/bin/env bash
# EMS Edge – Virtual environment setup for Linux (Raspberry Pi 5)
set -e

echo "============================================"
echo " EMS Edge - Virtual Environment Setup"
echo " Target: Linux / Raspberry Pi 5"
echo "============================================"

# --- Check Python 3 ---
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Install it with:"
    echo "        sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    exit 1
fi
echo "[INFO] Using $(python3 --version)"

# --- snap7 native lib (required by python-snap7) ---
if ! ldconfig -p | grep -q libsnap7; then
    echo "[INFO] libsnap7 not found – installing..."
    sudo apt-get update -qq
    sudo apt-get install -y libsnap7-1 libsnap7-dev
else
    echo "[INFO] libsnap7 already installed."
fi

# --- Create venv ---
VENV_DIR="$(dirname "$0")/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] Creating virtual environment at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
    echo "[OK] Virtual environment created."
else
    echo "[INFO] Virtual environment already exists, skipping."
fi

# --- Activate + upgrade pip ---
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "[INFO] Upgrading pip ..."
pip install --upgrade pip --quiet

# --- Install requirements ---
echo "[INFO] Installing packages from requirements.txt ..."
pip install -r "$(dirname "$0")/requirements.txt"

echo ""
echo "============================================"
echo " [OK] Setup complete!"
echo " To activate the venv manually:"
echo "     source .venv/bin/activate"
echo " To run the edge script:"
echo "     python Ems-edge.py"
echo "============================================"
