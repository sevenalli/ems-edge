import snap7
import paho.mqtt.client as mqtt
import json
import time
import sys
import csv
import re
import os
import contextlib
from datetime import datetime
from snap7.util import get_real, get_int, get_dint, get_bool


@contextlib.contextmanager
def _silence_snap7():
    """Redirect OS-level stdout/stderr to /dev/null for the snap7 C library.
    Python-level exceptions are unaffected; only the direct C printf() noise
    (b'CLI : function refused by CPU ...') is suppressed.
    """
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(devnull_fd)
        os.close(saved_out)
        os.close(saved_err)

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
TOPIC_PUB   = f"{SITE}/{EQUIPMENT_CODE}/logs"

# CSV tag mapping files (comma-separated list)
CSV_FILES = [p.strip() for p in _env('CSV_FILES', 'tags.csv').split(',') if p.strip()]

# Timing
CYCLE_TIME    = _env_float('CYCLE_TIME',    0.600)
BURST_SAMPLES = _env_int('BURST_SAMPLES',  3)
BURST_DELAY   = _env_float('BURST_DELAY',  0.010)

# S7 PDU Read Limit
# S7-300/400: 222 bytes  |  S7-1200/1500: 462 bytes
# Set PDU_MAX_READ in .env to match your PLC model.
PDU_MAX_READ  = _env_int('PDU_MAX_READ',   222)
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


def _chunk_tags(group_tags: list, pdu_max: int) -> list:
    """
    Split a list of tags (all in the same DB) into PDU-sized read chunks.
    Each chunk covers a contiguous byte range ≤ pdu_max bytes.
    Returns a list of chunk dicts: {start, size, tags}.
    """
    # Sort tags by byte offset so we can walk them in order.
    sorted_tags = sorted(group_tags, key=lambda t: t['address']['offset'])
    chunks = []
    chunk_tags = []
    chunk_start = None

    for tag in sorted_tags:
        off  = tag['address']['offset']
        end  = off + tag['address']['byte_size']

        if chunk_start is None:
            # First tag in a new chunk
            chunk_start = off
            chunk_tags  = [tag]
        elif end - chunk_start <= pdu_max:
            # Tag still fits in the current chunk
            chunk_tags.append(tag)
        else:
            # Current tag doesn't fit — flush the current chunk and start a new one
            chunk_end = max(
                t['address']['offset'] + t['address']['byte_size']
                for t in chunk_tags
            )
            chunks.append({'start': chunk_start,
                           'size':  chunk_end - chunk_start,
                           'tags':  chunk_tags})
            chunk_start = off
            chunk_tags  = [tag]

    if chunk_tags:
        chunk_end = max(
            t['address']['offset'] + t['address']['byte_size']
            for t in chunk_tags
        )
        chunks.append({'start': chunk_start,
                       'size':  chunk_end - chunk_start,
                       'tags':  chunk_tags})

    return chunks


def build_read_plans(csv_files: list) -> list:
    """
    Load one or more CSV mapping files, merge all tags, then return
    snap7 block-read plans grouped by (area, db_number).
    Each plan is split into PDU-sized sub-chunks to avoid
    'function refused by CPU' errors on S7 PLCs.
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
    total_chunks = 0
    for (area_int, db_num), group_tags in groups.items():
        chunks = _chunk_tags(group_tags, PDU_MAX_READ)
        total_chunks += len(chunks)
        plans.append({
            'area':   area_int,
            'db':     db_num,
            'chunks': chunks,      # list of {start, size, tags}
        })

    print(f"✅ Total: {len(all_tags)} tags across {len(plans)} DB block(s), "
          f"{total_chunks} PDU chunk(s) (PDU_MAX_READ={PDU_MAX_READ} B).")
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

    # --- PLC Diagnostics ---
    print("🔍 PLC Diagnostics:")
    try:
        info = plc.get_cpu_info()
        print(f"   Module:      {bytes(info.ModuleTypeName).rstrip(b'\x00').decode(errors='replace')}")
        print(f"   Module Name: {bytes(info.ModuleName).rstrip(b'\x00').decode(errors='replace')}")
        print(f"   Serial:      {bytes(info.SerialNumber).rstrip(b'\x00').decode(errors='replace')}")
    except Exception as e:
        print(f"   ❌ get_cpu_info failed: {type(e).__name__}: {e}")
        print("   ⚠️  This usually means PUT/GET access is disabled in PLC properties.")

    try:
        pdu = plc.get_param(snap7.snap7types.Parameter.LocalPort
                            if hasattr(snap7.snap7types, 'Parameter') else 10)
    except Exception:
        pdu = None

    # Probe the first DB with a 1-byte read to get a clear error message
    if read_plans:
        first_plan = read_plans[0]
        first_chunk = first_plan['chunks'][0]
        try:
            area_enum = Areas(first_plan['area'])
            with _silence_snap7():
                plc.read_area(area_enum, first_plan['db'], first_chunk['start'], 1)
            print(f"   ✅ Test read DB{first_plan['db']} offset {first_chunk['start']} → OK")
        except Exception as e:
            err_str = str(e)
            print(f"   ❌ Test read DB{first_plan['db']} failed: {type(e).__name__}: {err_str}")
            if 'refused' in err_str.lower() or '0x00200000' in err_str:
                print("")
                print("   ╔══ FIX REQUIRED (PLC-side) ══════════════════════════════╗")
                print("   ║  Error 0x00200000 = 'Function refused by CPU'           ║")
                print("   ║  Most likely causes (in order):                         ║")
                print("   ║  1. S7-1200/1500: PUT/GET access NOT enabled            ║")
                print("   ║     → TIA Portal → PLC Properties → Protection         ║")
                print("   ║       → ✅ 'Permit access with PUT/GET...'              ║")
                print("   ║  2. S7-1200/1500: DB102 has 'Optimized block access'    ║")
                print("   ║     → TIA Portal → DB102 Properties                    ║")
                print("   ║       → ✅ UN-check 'Optimized block access'            ║")
                print("   ║  3. S7-1200: Wrong slot — try PLC_SLOT=1 in .env       ║")
                print("   ╚═════════════════════════════════════════════════════════╝")
                print("")

    print(f"🚀 Starting loop — {BURST_SAMPLES} burst reads / {CYCLE_TIME}s cycle...")

    try:
        while True:
            loop_start = time.perf_counter()
            best_values: dict = {}

            # --- BURST READ ---
            for _ in range(BURST_SAMPLES):
                for plan in read_plans:
                    area_enum = Areas(plan['area'])
                    for chunk in plan['chunks']:
                        try:
                            with _silence_snap7():
                                raw_data = plc.read_area(
                                    area_enum,
                                    plan['db'],
                                    chunk['start'],
                                    chunk['size'],
                                )

                            for tag in chunk['tags']:
                                relative_offset = tag['address']['offset'] - chunk['start']
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

                        except RuntimeError as e:
                            if not plc.get_connected():
                                try: plc.connect(PLC_IP, PLC_RACK, PLC_SLOT)
                                except: pass
                        except Exception as e:
                            # Log distinct errors (avoid flooding the console)
                            err_key = (plan['db'], chunk['start'], type(e).__name__)
                            if not hasattr(main, '_logged_errs'):
                                main._logged_errs = {}
                            if main._logged_errs.get(err_key, 0) < 3:
                                main._logged_errs[err_key] = main._logged_errs.get(err_key, 0) + 1
                                print(f"   ⚠️  DB{plan['db']} chunk@{chunk['start']}: "
                                      f"{type(e).__name__}: {e}")

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
