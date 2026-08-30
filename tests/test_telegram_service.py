import unittest

from tglol.telegram_service import extract_login_codes


class VerificationCodeExtractionTests(unittest.TestCase):
    def test_extracts_verification_codes_bot_message(self):
        self.assertEqual(extract_login_codes("Your code is 450389"), ["450389"])

    def test_ignores_numbers_without_code_context(self):
        self.assertEqual(extract_login_codes("Balance 450389"), [])


if __name__ == "__main__":
    unittest.main()