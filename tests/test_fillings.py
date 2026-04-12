import unittest
from unittest.mock import MagicMock

from aiogram.types import Chat, Message, User

from sophie_bot.modules.notes.utils.fillings import process_fillings


class TestProcessFillings(unittest.TestCase):
    def setUp(self):
        self.chat = MagicMock(spec=Chat)
        self.chat.id = 123456
        self.chat.title = "Test Chat"
        self.chat.username = "test_chat"

        self.message = MagicMock(spec=Message, new_chat_members=MagicMock())
        self.message.chat = self.chat

        self.user = MagicMock(spec=User)
        self.user.id = 78910
        self.user.first_name = "Test"
        self.user.last_name = "User"
        self.user.username = "testuser"
        self.additional_fillings = {"custom_key": "custom_value"}

    def test_with_empty_text(self):
        text = ""
        result = process_fillings(text, self.message, self.user, self.additional_fillings)
        self.assertEqual(result, "")

    def test_with_chat_fillings(self):
        text = "Chat ID: {chatid}, Name: {chatname}, Nick: {chatnick}"
        expected = "Chat ID: 123456, Name: Test Chat, Nick: test_chat"
        result = process_fillings(text, self.message, None, None)
        self.assertEqual(result, expected)

    def test_with_user_fillings(self):
        text = "User ID: {id}, First: {first}, Last: {last}, Full: {fullname}, Mention: {mention}"
        expected_start = "User ID: 78910, First: Test, Last: User, Full: Test User, Mention: "
        result = process_fillings(text, self.message, self.user, None)
        self.assertTrue(result.startswith(expected_start))
        self.assertIn(self.user.first_name, result)

    def test_with_custom_fillings(self):
        text = "Custom: {custom_key}"
        expected = "Custom: custom_value"
        result = process_fillings(text, self.message, None, self.additional_fillings)
        self.assertEqual(result, expected)

    def test_combined_fillings(self):
        text = "{chatname} - {first} {last}, {custom_key}. Chat ID: {chatid}, UserID: {id}"
        expected = "Test Chat - Test User, custom_value. Chat ID: 123456, UserID: 78910"
        result = process_fillings(text, self.message, self.user, self.additional_fillings)
        self.assertEqual(result, expected)

    def test_with_missing_custom_key(self):
        text = "Missing: {missing_key}"
        result = process_fillings(text, self.message, self.user, self.additional_fillings)
        self.assertEqual(result, text)

    def test_fullname_html_is_escaped(self):
        """HTML in first/last name must not pass through unescaped."""
        self.user.first_name = "<script>alert(1)</script>"
        self.user.last_name = "<b>Hacker</b>"
        result = process_fillings("{fullname}", self.message, self.user, None)
        self.assertNotIn("<script>", result)
        self.assertNotIn("<b>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_username_uses_username_field(self):
        """{username} should use user.username, not first_name."""
        self.user.username = "actual_username"
        self.user.first_name = "First"
        result = process_fillings("{username}", self.message, self.user, None)
        self.assertIn("actual_username", result)

    def test_username_falls_back_to_first_name_when_no_username(self):
        """{username} falls back to first_name when username is None."""
        self.user.username = None
        self.user.first_name = "NoUsername"
        result = process_fillings("{username}", self.message, self.user, None)
        self.assertIn("NoUsername", result)

    def test_username_html_is_escaped(self):
        """HTML in username must be escaped."""
        self.user.username = None
        self.user.first_name = "<img src=x onerror=alert(1)>"
        result = process_fillings("{username}", self.message, self.user, None)
        self.assertNotIn("<img", result)
        self.assertIn("&lt;img", result)

    def test_fullname_no_last_name(self):
        """fullname with no last_name should not include trailing space."""
        self.user.first_name = "Solo"
        self.user.last_name = None
        result = process_fillings("{fullname}", self.message, self.user, None)
        self.assertEqual(result, "Solo")


if __name__ == "__main__":
    unittest.main()
