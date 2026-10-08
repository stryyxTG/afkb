import unittest

from tglol.handlers import _parse_trigger_request_count


class TriggerCountParserTests(unittest.TestCase):
    def test_count_after_trigger(self):
        self.assertEqual(_parse_trigger_request_count("\u0442\u0433 5"), 5)

    def test_count_before_trigger(self):
        self.assertEqual(_parse_trigger_request_count("5 \u0442\u0433"), 5)

    def test_missing_count_defaults_to_one(self):
        self.assertEqual(_parse_trigger_request_count("\u0442\u0433"), 1)

    def test_count_is_clamped_to_ten(self):
        self.assertEqual(_parse_trigger_request_count("999 \u0442\u0433"), 10)


if __name__ == "__main__":
    unittest.main()
