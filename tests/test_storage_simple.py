import unittest

from tglol.keyboards import account_detail_menu


class StorageMenuTests(unittest.TestCase):
    def test_account_detail_menu_has_no_registration_buttons(self) -> None:
        markup = account_detail_menu(1, account_stage="nereg", origin="common", ref_id=0, page=0)
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        lowered = [button.lower() for button in buttons]

        self.assertNotIn("отметить аккаунт реганным", lowered)
        self.assertNotIn("сменить сервисы", lowered)
        self.assertNotIn("перенести в нерег", lowered)


if __name__ == "__main__":
    unittest.main()
