import unittest

from tglol.handlers import _parse_bulk_login_codes, _parse_bulk_login_phones


class BulkCodeLoginParserTests(unittest.TestCase):
    def test_parse_accepts_up_to_five_phone_lines(self):
        phones, error = _parse_bulk_login_phones("+15074486037\n15074486038\n+15074486039\n15074486040\n15074486041")

        self.assertIsNone(error)
        self.assertEqual(
            phones,
            [
                "+15074486037",
                "+15074486038",
                "+15074486039",
                "+15074486040",
                "+15074486041",
            ],
        )

    def test_parse_rejects_more_than_five_phone_lines(self):
        phones, error = _parse_bulk_login_phones("\n".join(f"+1507448603{index}" for index in range(6)))

        self.assertEqual(phones, [])
        self.assertIn("максимум 5", error)

    def test_parse_rejects_duplicate_phones(self):
        phones, error = _parse_bulk_login_phones("+15074486037\n15074486037")

        self.assertEqual(phones, [])
        self.assertIn("повторяется", error)

    def test_parse_codes_preserves_order(self):
        codes, error = _parse_bulk_login_codes("12345\n23456\n34567", 3)

        self.assertIsNone(error)
        self.assertEqual(codes, ["12345", "23456", "34567"])

    def test_parse_codes_requires_expected_count(self):
        codes, error = _parse_bulk_login_codes("12345\n23456", 3)

        self.assertEqual(codes, [])
        self.assertIn("Нужно отправить 3", error)


if __name__ == "__main__":
    unittest.main()