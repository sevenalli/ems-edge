import csv
import re

TYPE_MAP = {
    'REAL': (4, 'REAL'),
    'INT': (2, 'INT'),
    'DINT': (4, 'DINT'),
    'DWORD': (4, 'DWORD'),
    'WORD': (2, 'INT'),
    'BOOL': (1, 'BOOL'),
    'BYTE': (1, 'INT'),
    'STRING': (0, 'STRING'),
}

AREA_DB = 132
AREA_INPUT = 129
AREA_OUTPUT = 130
AREA_MARKER = 131

AREA_ALIASES = {
    'I': AREA_INPUT,
    'E': AREA_INPUT,
    'Q': AREA_OUTPUT,
    'A': AREA_OUTPUT,
    'M': AREA_MARKER,
}

SIZE_BYTES = {
    'B': 1,
    'W': 2,
    'D': 4,
    'X': 1,
    '': 1,
}


def _clean(value):
    return (value or '').strip()


def _parse_db_address(address):
    match = re.fullmatch(r'%?DB(\d+)\.DB([DWBX])(\d+)(?:\.(\d+))?', address, re.IGNORECASE)
    if not match:
        return None

    size_code = match.group(2).upper()
    bit = int(match.group(4)) if match.group(4) is not None else None
    if size_code == 'X' and bit is None:
        return None

    return {
        'area': AREA_DB,
        'db_number': int(match.group(1)),
        'offset': int(match.group(3)),
        'byte_size': SIZE_BYTES[size_code],
        'bit': bit if bit is not None else 0,
    }


def _parse_io_marker_address(address):
    normalized = re.sub(r'\s+', '', address).upper().lstrip('%')
    match = re.fullmatch(r'([IEQAM])([XWDB]?)(\d+)(?:\.(\d+))?', normalized)
    if not match:
        return None

    area_prefix, size_code, offset_text, bit_text = match.groups()
    if bit_text is not None and size_code in ('', 'X'):
        size_code = 'X'
    elif size_code == 'X' and bit_text is None:
        return None
    elif bit_text is not None:
        return None

    return {
        'area': AREA_ALIASES[area_prefix],
        'db_number': 0,
        'offset': int(offset_text),
        'byte_size': SIZE_BYTES[size_code],
        'bit': int(bit_text) if bit_text is not None else 0,
    }


def parse_address(address):
    address = _clean(address)
    if not address:
        return None
    return _parse_db_address(address) or _parse_io_marker_address(address)


def sniff_csv_dialect(handle):
    sample = handle.read(4096)
    handle.seek(0)
    try:
        return csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        class DefaultDialect(csv.excel):
            delimiter = ';' if sample.count(';') > sample.count(',') else ','
        return DefaultDialect


def load_csv_tags(csv_file):
    tags = []
    with open(csv_file, 'r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle, dialect=sniff_csv_dialect(handle))
        rows = list(reader)

    for row in rows:
        nom = _clean(row.get('Nom')) or _clean(row.get('Symbol'))
        plc_type = (_clean(row.get('Type')) or _clean(row.get('DataType'))).upper()
        address_text = _clean(row.get('Adresse_ABS')) or _clean(row.get('Adresse')) or _clean(row.get('Address'))

        if not nom or not address_text:
            continue

        type_info = TYPE_MAP.get(plc_type)
        if type_info is None:
            print(f"Skipping '{nom}': unsupported type '{plc_type}'")
            continue

        byte_size, simple_type = type_info
        if simple_type == 'STRING':
            continue

        parsed = parse_address(address_text)
        if parsed is None:
            print(f"Skipping '{nom}': unparseable address '{address_text}'")
            continue

        parsed = dict(parsed)
        parsed['byte_size'] = max(parsed['byte_size'], byte_size if byte_size > 0 else parsed['byte_size'])

        tags.append({
            'tag_id': nom,
            'simple_type': simple_type,
            'address': parsed,
        })

    return tags
