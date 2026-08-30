import unittest

from tglol.bulk_input import parse_bulk_phone_input


class BulkInputParserTests(unittest.TestCase):
    def test_parse_bulk_phone_input_ignores_forwarded_headers(self) -> None:
        text = (
            "Переслано от givenchyy | кyc\n"
            "+79322549871\n+79322550128"
        )
        result = parse_bulk_phone_input(text)
        self.assertEqual(result, ['+79322549871', '+79322550128'])

    def test_parse_bulk_input_rejects_bad_format(self) -> None:
        text = "+79322549871\n12345\n+79322550128"
        result = parse_bulk_phone_input(text)
        self.assertIsNone(result)

    def test_parse_bulk_phone_list_accepts_even_lines_as_phones(self) -> None:
        text = "+79322549871\n+79322550128\n+79322550129\n+79322550130"

        self.assertEqual(
            parse_bulk_phone_input(text),
            ['+79322549871', '+79322550128', '+79322550129', '+79322550130'],
        )


if __name__ == '__main__':
    unittest.main()
