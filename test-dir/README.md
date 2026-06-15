# ems-edge test parser setup

This directory is isolated from the original `Ems-edge.py`.

Files:
- `ems_tag_parser.py`: CSV/address parser that supports semicolon CSV, `Adresse_ABS`, `Adresse`, and `Address` columns.
- `Ems-edge-test.py`: copy of the edge script wired to the new parser.
- `HMK_300E_symbols_formatted.csv`: local copy of the current HMK tag CSV.
- `.env.test`: sample environment config for the test script.
- `test_ems_tag_parser.py`: unit tests for DB, input, output, marker, and semicolon CSV parsing.

Supported address examples:
- `%DB102.DBW8`, `%DB102.DBD0`, `%DB102.DBX278.0`
- `IW 802`, `IB 12`, `ID 20`, `I0.0`, `IX0.0`
- `QW 10`, `QB 12`, `QD 20`, `Q0.0`, `QX0.0`
- `MW 10`, `MB 12`, `MD 20`, `M12.3`, `MX12.3`

Quick checks:
```powershell
python -m unittest test_ems_tag_parser.py
python -c "from ems_tag_parser import load_csv_tags; tags=load_csv_tags('HMK_300E_symbols_formatted.csv'); print(len(tags)); print(tags[0])"
```

To run the test script with the local test config, copy `.env.test` to `.env` inside this directory first.
