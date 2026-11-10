import unittest

from tglol.telegram_service import extract_login_codes


class TelegramCodeParserTests(unittest.TestCase):
    def test_prefers_login_code_over_year_in_same_message(self):
        text = "Your Telegram login code: 483921\n\nCopyright 2026 Telegram."
        self.assertEqual(extract_login_codes(text), ["483921"])

    def test_accepts_code_before_its_label(self):
        self.assertEqual(
            extract_login_codes("483921 is your login code. Do not share it."),
            ["483921"],
        )

    def test_ignores_year_when_only_distant_context_exists(self):
        text = "Your login code is shown above.\n\nCopyright 2026 Telegram."
        self.assertEqual(extract_login_codes(text), [])

    def test_accepts_russian_telegram_code(self):
        self.assertEqual(extract_login_codes("Код для входа в Telegram: 483921"), ["483921"])

    def test_returns_only_one_best_candidate(self):
        text = "Код входа: 483921. Номер обращения 778899."
        self.assertEqual(extract_login_codes(text), ["483921"])


if __name__ == "__main__":
    unittest.main()
