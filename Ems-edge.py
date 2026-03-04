import snap7
import paho.mqtt.client as mqtt
import json
import time
import sys
import csv
import re
import os
from datetime import datetime
from snap7.util import get_real, get_int, get_dint, get_bool

# ================= SMART ENUM FINDER =================
Areas = None
try:
    import snap7.client
    if hasattr(snap7.client, 'Area'):  Areas = snap7.client.Area
    elif hasattr(snap7.client, 'Areas'): Areas = snap7.client.Areas
except ImportError: pass

if Areas is None:
    try: from snap7.snap7types import Area as Areas
    except ImportError: pass

if Areas is None:
    print("❌ CRITICAL ERROR: Could not find 'Area' Enum.")
    sys.exit(1)
# =====================================================


# ================= .ENV LOADER =======================
def _load_env(path: str = '.env') -> None:
    """
    Parse a KEY=VALUE .env file and inject values into os.environ.
    - Lines starting with '#' and blank lines are ignored.
    - Values are NOT quoted; inline comments are NOT supported.
    """
    if not os.path.exists(path):
        print(f"⚠️  No .env file found at '{path}'. Using OS environment variables.")
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())

_load_env()


def _env(key: str, default: str = '') -> str:
    return os.environ.get(key, default)

def _env_int(key: str, default: int) -> int:
    try: return int(_env(key, str(default)))
    except ValueError: return default

def _env_float(key: str, default: float) -> float:
    try: return float(_env(key, str(default)))
    except ValueError: return default
# =====================================================


# ================= CONFIGURATION =====================
# Equipment identity
EQUIPMENT_CODE = _env('EQUIPMENT_CODE', 'UNKNOWN')
EQUIPMENT_NAME = _env('EQUIPMENT_NAME', 'Unknown Equipment')
EQUIPMENT_TYPE = _env('EQUIPMENT_TYPE', 'Unknown')
SITE           = _env('SITE',      'default_site')
TERMINAL       = _env('TERMINAL',  'terminal1')

# PLC
PLC_IP   = _env('PLC_IP',   '192.168.0.1')
PLC_RACK = _env_int('PLC_RACK', 0)
PLC_SLOT = _env_int('PLC_SLOT', 0)

# MQTT
MQTT_BROKER = _env('MQTT_BROKER', '127.0.0.1')
MQTT_PORT   = _env_int('MQTT_PORT', 1883)
TOPIC_PUB   = f"{SITE}/{EQUIPMENT_CODE}"

# CSV tag mapping files (comma-separated list)
CSV_FILES = [p.strip() for p in _env('CSV_FILES', 'tags.csv').split(',') if p.strip()]

# Timing
CYCLE_TIME    = _env_float('CYCLE_TIME',    0.600)
BURST_SAMPLES = _env_int('BURST_SAMPLES',  3)
BURST_DELAY   = _env_float('BURST_DELAY',  0.010)
# =====================================================


# ================= CSV → READ PLAN ===================
_TYPE_MAP = {
    'REAL':   (4, 'REAL'),
    'INT':    (2, 'INT'),
    'DINT':   (4, 'DINT'),
    'DWORD':  (4, 'DWORD'),
    'WORD':   (2, 'INT'),    # read as signed 16-bit
    'BOOL':   (1, 'BOOL'),
    'STRING': (0, 'STRING'), # skipped – raw block decode not trivial
}

_DB_AREA_INT = 132  # snap7 Area.DB


def _parse_adresse_abs(adresse_abs: str):
    """
    Parse S7 absolute addresses, e.g.:
      %DB102.DBD0     → (102, 0,   None)  4-byte block
      %DB102.DBW8     → (102, 8,   None)  2-byte block
      %DB102.DBX278.0 → (102, 278, 0)     bit 0 of byte 278
    Returns (db_number, byte_offset, bit_or_None) or None on failure.
    """
    m = re.match(
        r'%DB(\d+)\.(DB[DWBX])(\d+)(?:\.(\d+))?',
        adresse_abs.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    db_num  = int(m.group(1))
    byte_off = int(m.group(3))
    bit_num = int(m.group(4)) if m.group(4) is not None else None
    return db_num, byte_off, bit_num


def _load_csv_tags(csv_file: str) -> list:
    """Return a list of tag dicts from a single CSV file."""
    tags = []
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except FileNotFoundError:
        print(f"❌ CSV not found: '{csv_file}' — skipped.")
        return tags

    for row in rows:
        nom         = row.get('Nom', '').strip()
        plc_type    = row.get('Type', '').strip().upper()
        adresse_abs = row.get('Adresse_ABS', '').strip()

        if not nom or not adresse_abs:
            continue

        parsed = _parse_adresse_abs(adresse_abs)
        if parsed is None:
            print(f"⚠️  [{csv_file}] Skipping '{nom}': unparseable address '{adresse_abs}'")
            continue

        db_num, byte_off, bit_num = parsed
        type_info = _TYPE_MAP.get(plc_type)
        if type_info is None:
            print(f"⚠️  [{csv_file}] Skipping '{nom}': unsupported type '{plc_type}'")
            continue

        byte_size, simple_type = type_info
        if simple_type == 'STRING':
            continue

        tags.append({
            'tag_id':      nom,
            'simple_type': simple_type,
            'address': {
                'area':      _DB_AREA_INT,
                'db_number': db_num,
                'offset':    byte_off,
                'byte_size': byte_size if byte_size > 0 else 2,
                'bit':       bit_num if bit_num is not None else 0,
            },
        })
    return tags


def build_read_plans(csv_files: list) -> list:
    """
    Load one or more CSV mapping files, merge all tags, then return
    snap7 block-read plans grouped by (area, db_number).
    """
    all_tags = []
    for csv_file in csv_files:
        file_tags = _load_csv_tags(csv_file)
        print(f"   📄 {csv_file}: {len(file_tags)} tags loaded.")
        all_tags.extend(file_tags)

    if not all_tags:
        print("❌ No tags loaded from any CSV file. Exiting.")
        sys.exit(1)

    # Group by DB block for efficient bulk reads
    groups: dict = {}
    for tag in all_tags:
        key = (tag['address']['area'], tag['address']['db_number'])
        groups.setdefault(key, []).append(tag)

    plans = []
    for (area_int, db_num), group_tags in groups.items():
        min_off = min(t['address']['offset'] for t in group_tags)
        max_tag = max(group_tags, key=lambda t: t['address']['offset'])
        max_off = max_tag['address']['offset'] + max_tag['address']['byte_size']

        plans.append({
            'area':  area_int,
            'db':    db_num,
            'start': min_off,
            'size':  max_off - min_off,
            'tags':  group_tags,
        })

    print(f"✅ Total: {len(all_tags)} tags across {len(plans)} DB block(s).")
    return plans
# =====================================================


def main():
    print("=" * 55)
    print(f"  EMS Edge — {EQUIPMENT_NAME} ({EQUIPMENT_CODE})")
    print(f"  Site: {SITE} | Terminal: {TERMINAL}")
    print(f"  PLC: {PLC_IP}  |  Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  Topic: {TOPIC_PUB}")
    print(f"  CSV files: {CSV_FILES}")
    print("=" * 55)

    read_plans = build_read_plans(CSV_FILES)

    # --- MQTT Setup ---
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    client.max_inflight_messages_set(100)
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print(f"✅ MQTT Connected to {MQTT_BROKER}:{MQTT_PORT}")
    except Exception as e:
        print(f"❌ MQTT Error: {e}")
        return

    # --- PLC Setup ---
    plc = snap7.client.Client()
    try:
        print(f"⏳ Connecting to PLC {PLC_IP} (rack={PLC_RACK}, slot={PLC_SLOT})...")
        plc.connect(PLC_IP, PLC_RACK, PLC_SLOT)
        print("✅ PLC Connected.")
    except Exception as e:
        print(f"❌ PLC Error: {e}")
        return

    print(f"🚀 Starting loop — {BURST_SAMPLES} burst reads / {CYCLE_TIME}s cycle...")

    try:
        while True:
            loop_start = time.perf_counter()
            best_values: dict = {}

            # --- BURST READ ---
            for _ in range(BURST_SAMPLES):
                for plan in read_plans:
                    try:
                        area_enum = Areas(plan['area'])
                        raw_data  = plc.read_area(area_enum, plan['db'], plan['start'], plan['size'])

                        for tag in plan['tags']:
                            relative_offset = tag['address']['offset'] - plan['start']
                            val = None
                            s_type = tag.get('simple_type', 'REAL')
                            tag_id = tag['tag_id']

                            try:
                                if s_type == 'REAL':
                                    val = round(get_real(raw_data, relative_offset), 3)
                                elif s_type == 'INT':
                                    val = get_int(raw_data, relative_offset)
                                elif s_type == 'BOOL':
                                    val = get_bool(raw_data, relative_offset, tag['address']['bit'])
                                elif s_type in ('DINT', 'DWORD'):
                                    val = get_dint(raw_data, relative_offset)
                            except ValueError:
                                continue

                            if val is not None:
                                if tag_id in best_values:
                                    if isinstance(val, bool):
                                        best_values[tag_id] = best_values[tag_id] or val
                                    elif abs(val) > abs(best_values[tag_id]):
                                        best_values[tag_id] = val
                                else:
                                    best_values[tag_id] = val

                    except RuntimeError:
                        if not plc.get_connected():
                            try: plc.connect(PLC_IP, PLC_RACK, PLC_SLOT)
                            except: pass
                    except Exception:
                        pass

                time.sleep(BURST_DELAY)

            # --- PUBLISH ---
            if best_values:
                snapshot = {
                    "site":      SITE,
                    "terminal":  TERMINAL,
                    "type":      EQUIPMENT_TYPE,
                    "host":      EQUIPMENT_CODE,
                    "name":      EQUIPMENT_NAME,
                    "ts":        datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "data":      best_values,
                }
                try:
                    client.publish(TOPIC_PUB, json.dumps(snapshot))
                except Exception:
                    pass

            elapsed    = time.perf_counter() - loop_start
            sleep_time = CYCLE_TIME - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()
        plc.disconnect()
        print("\n🛑 Stopped.")


if __name__ == "__main__":
    main()