import tempfile
import unittest
from pathlib import Path

from ems_tag_parser import (
    AREA_DB,
    AREA_INPUT,
    AREA_MARKER,
    AREA_OUTPUT,
    load_csv_tags,
    parse_address,
)


class AddressParserTests(unittest.TestCase):
    def test_parses_db_word_address(self):
        self.assertEqual(
            parse_address('%DB102.DBW8'),
            {'area': AREA_DB, 'db_number': 102, 'offset': 8, 'byte_size': 2, 'bit': 0},
        )

    def test_parses_input_word_address_with_space(self):
        self.assertEqual(
            parse_address('IW 802'),
            {'area': AREA_INPUT, 'db_number': 0, 'offset': 802, 'byte_size': 2, 'bit': 0},
        )

    def test_parses_output_word_address(self):
        self.assertEqual(
            parse_address('QW10'),
            {'area': AREA_OUTPUT, 'db_number': 0, 'offset': 10, 'byte_size': 2, 'bit': 0},
        )

    def test_parses_marker_bit_address_without_x(self):
        self.assertEqual(
            parse_address('M12.3'),
            {'area': AREA_MARKER, 'db_number': 0, 'offset': 12, 'byte_size': 1, 'bit': 3},
        )

    def test_loads_semicolon_csv_using_adresse_when_adresse_abs_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tags.csv'
            path.write_text(
                'Adresse;Nom;Type;Commentaire;Adresse_ABS;Fonction;Categorie\n'
                'IW 802;_11_R_IND;WORD;Mot 24: index;;;\n'
                'QW 10;OUT_SPEED;WORD;Output word;;;\n',
                encoding='utf-8',
            )

            tags = load_csv_tags(str(path))

        self.assertEqual([tag['tag_id'] for tag in tags], ['_11_R_IND', 'OUT_SPEED'])
        self.assertEqual(tags[0]['address']['area'], AREA_INPUT)
        self.assertEqual(tags[1]['address']['area'], AREA_OUTPUT)


if __name__ == '__main__':
    unittest.main()
