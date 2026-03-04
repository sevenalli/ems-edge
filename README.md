# EMS Edge — Raspberry Pi 5 Setup & Usage Guide

## Project Structure

```
EMS-edge/
├── Ems-edge.py          # Main edge script
├── .env                 # Per-equipment configuration (edit this!)
├── requirements.txt     # Python dependencies
├── setup_venv.sh        # One-time setup script
└── *.csv                # Tag mapping files (referenced in .env)
```

---

## 1 — Transfer files to the Pi

From your PC (replace `<PI_IP>` with your Pi's IP):
```bash
scp -r EMS-edge/ pi@<PI_IP>:~/ems-edge
```
Or clone/pull from your repo on the Pi directly.

---

## 2 — One-time setup

SSH into the Pi, then:
```bash
cd ~/ems-edge
chmod +x setup_venv.sh
./setup_venv.sh
```

This will:
- Install the **`libsnap7`** native library via `apt`
- Create a Python virtual environment in `.venv/`
- Install all Python packages from `requirements.txt`

---

## 3 — Configure the edge

Edit `.env` for this specific equipment:
```bash
nano .env
```

Key fields to change per deployment:

| Key | Example | Description |
|---|---|---|
| `EQUIPMENT_CODE` | `MM1GM11702` | Unique ID — used in MQTT topic |
| `EQUIPMENT_NAME` | `Grue Mobile 1` | Human-readable name |
| `EQUIPMENT_TYPE` | `Grue Mobile` | Equipment category |
| `SITE` | `sma` | Site identifier |
| `TERMINAL` | `terminal1` | Terminal/zone |
| `PLC_IP` | `192.0.0.2` | S7 PLC IP address |
| `MQTT_BROKER` | `100.119.71.77` | MQTT broker IP |
| `CSV_FILES` | `DB102_duplicates.csv` | Comma-separated CSV tag map files |

MQTT messages are published to: **`{SITE}/{EQUIPMENT_CODE}`**

Multiple CSV files example:
```
CSV_FILES=DB102_duplicates.csv,DB200_vars.csv
```

---

## 4 — Run the script

```bash
cd ~/ems-edge
source .venv/bin/activate
python Ems-edge.py
```

Expected startup output:
```
=======================================================
  EMS Edge — Grue Mobile (MM1GM11702)
  Site: sma | Terminal: terminal1
  PLC: 192.0.0.2  |  Broker: 100.119.71.77:1883
  Topic: sma/MM1GM11702
  CSV files: ['DB102_duplicates.csv']
=======================================================
   📄 DB102_duplicates.csv: 390 tags loaded.
✅ Total: 390 tags across 1 DB block(s).
✅ MQTT Connected to 100.119.71.77:1883
⏳ Connecting to PLC 192.0.0.2 ...
✅ PLC Connected.
🚀 Starting loop — 3 burst reads / 0.6s cycle...
```

Press **`Ctrl+C`** to stop gracefully.

---

## 5 — Run automatically on boot (systemd)

Create a service file:
```bash
sudo nano /etc/systemd/system/ems-edge.service
```

Paste:
```ini
[Unit]
Description=EMS Edge PLC → MQTT Bridge
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/pi/ems-edge
ExecStart=/home/pi/ems-edge/.venv/bin/python Ems-edge.py
EnvironmentFile=/home/pi/ems-edge/.env
Restart=always
RestartSec=10
User=pi

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable ems-edge
sudo systemctl start ems-edge
```

Check status / logs:
```bash
sudo systemctl status ems-edge
journalctl -u ems-edge -f
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `libsnap7 not found` | Run `sudo apt install libsnap7-1 libsnap7-dev` |
| PLC connection refused | Check `PLC_IP`, rack/slot, and that the PLC allows S7 comms |
| MQTT connection error | Verify broker IP, port 1883 is open, broker is running |
| CSV skipped with warnings | Check `Adresse_ABS` column is not empty and follows `%DBx.DBx` format |
| Tags all read as 0 | Increase `BURST_SAMPLES` or check PLC data block number |
