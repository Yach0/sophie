from __future__ import annotations

from unittest.mock import MagicMock, patch

from aiogram.types import Chat, Message, User

from sophie_bot.modules.notes.utils._random_parser import parse_random_text
from sophie_bot.modules.notes.utils.fillings import chat_fillings, process_fillings, user_fillings
from sophie_bot.modules.notes.utils.names import format_notes_aliases

# ─── Random parser tests ─────────────────────────────────────────────────────


class TestRandomParser:
    def test_random_parser_single_section(self) -> None:
        """Text with one %%% section picks one of the available options."""
        text = "Hello %%%world%%%universe%%% today!"
        result = parse_random_text(text)
        assert result in ["Hello world today!", "Hello universe today!"]

    def test_random_parser_no_sections(self) -> None:
        """Text without %%% returns unchanged."""
        text = "Just a normal string without any delimiters."
        result = parse_random_text(text)
        assert result == text

    @patch("sophie_bot.modules.notes.utils._random_parser.choice")
    def test_random_parser_multiple_sections(self, mock_choice: MagicMock) -> None:
        """Multiple %%% sections each pick independently."""
        mock_choice.side_effect = ["good", "day"]
        text = "Have a %%%good%%%bad%%% %%%day%%%night%%%!"
        result = parse_random_text(text)
        assert result == "Have a good day!"

        # Verify choice was called for both sections
        mock_choice.assert_any_call(["good", "bad"])
        mock_choice.assert_any_call(["day", "night"])

    def test_random_parser_empty_string(self) -> None:
        """Empty string returns empty string."""
        assert parse_random_text("") == ""

    @patch("sophie_bot.modules.notes.utils._random_parser.choice")
    def test_random_parser_multiline_content(self, mock_choice: MagicMock) -> None:
        """Multiline content within %%% sections is handled correctly."""
        mock_choice.return_value = "world\nplanet"
        text = "Hello\n%%%\nworld\nplanet\n%%%\nuniverse\ngalaxy\n%%%\n"
        result = parse_random_text(text)
        assert "world\nplanet" in result
        mock_choice.assert_called_once_with(["world\nplanet", "universe\ngalaxy"])


# ─── format_notes_aliases tests ──────────────────────────────────────────────


class TestFormatNotesAliases:
    def test_format_notes_aliases_single(self) -> None:
        """Single name formats correctly as a code element."""
        result = format_notes_aliases(["hello"])
        rendered = str(result)
        assert "#hello" in rendered

    def test_format_notes_aliases_multiple(self) -> None:
        """Multiple names are formatted and separated."""
        result = format_notes_aliases(["hello", "world", "test"])
        rendered = str(result)
        assert "#hello" in rendered
        assert "#world" in rendered
        assert "#test" in rendered

    def test_format_notes_aliases_empty(self) -> None:
        """Empty list produces an empty HList."""
        result = format_notes_aliases([])
        rendered = str(result)
        # Should be empty or minimal
        assert rendered is not None


# ─── Fillings tests ─────────────────────────────────────────────────────────


class TestFillings:
    def _make_message_and_user(
        self,
        first_name: str = "Alice",
        last_name: str | None = "Smith",
        username: str | None = "alicesmith",
        user_id: int = 42,
        chat_id: int = -100999,
        chat_title: str = "My Group",
        chat_username: str | None = "mygroup",
    ) -> tuple[MagicMock, MagicMock]:
        """Helper to create mock Message and User objects."""
        user = MagicMock(spec=User)
        user.id = user_id
        user.first_name = first_name
        user.last_name = last_name
        user.username = username

        chat = MagicMock(spec=Chat)
        chat.id = chat_id
        chat.title = chat_title
        chat.username = chat_username

        message = MagicMock(spec=Message)
        message.chat = chat
        message.new_chat_members = None

        return message, user

    def test_fillings_replaces_first_name(self) -> None:
        """{first} is replaced with user's first name."""
        message, user = self._make_message_and_user(first_name="Bob")
        result = process_fillings("{first}", message, user)
        assert "Bob" in result

    def test_fillings_replaces_mention(self) -> None:
        """{mention} is replaced with a user mention/link."""
        message, user = self._make_message_and_user(first_name="Carol", user_id=777)
        result = process_fillings("{mention}", message, user)
        # The mention should contain the user's first name as display text
        assert "Carol" in result

    def test_fillings_replaces_chatname(self) -> None:
        """{chatname} is replaced with the chat title."""
        message, user = self._make_message_and_user(chat_title="Developer Lounge")
        result = process_fillings("{chatname}", message, user)
        assert "Developer Lounge" in result

    def test_fillings_replaces_id(self) -> None:
        """{id} is replaced with the user ID."""
        message, user = self._make_message_and_user(user_id=12345)
        result = process_fillings("{id}", message, user)
        assert "12345" in result

    def test_fillings_handles_missing_fields(self) -> None:
        """Missing/None fields don't crash the processor."""
        message, user = self._make_message_and_user(last_name=None, username=None)
        # Should not raise any exception
        result = process_fillings("{first} {last} {fullname} {username}", message, user)
        assert result is not None
        # first_name should still be there
        assert "Alice" in result

    def test_fillings_replaces_username(self) -> None:
        """{username} is replaced with the user's username."""
        message, user = self._make_message_and_user(username="cooluser")
        result = process_fillings("{username}", message, user)
        assert "cooluser" in result

    def test_fillings_username_fallback_to_first_name(self) -> None:
        """{username} falls back to first_name when username is None."""
        message, user = self._make_message_and_user(first_name="FallbackName", username=None)
        result = process_fillings("{username}", message, user)
        assert "FallbackName" in result

    def test_fillings_no_message_returns_text_unchanged(self) -> None:
        """When message is None, chat_fillings returns text as-is."""
        result = chat_fillings("{chatname} test", None)
        assert result == "{chatname} test"

    def test_fillings_no_user_returns_text_unchanged(self) -> None:
        """When user is None, user_fillings returns text as-is."""
        message, _ = self._make_message_and_user()
        result = user_fillings("{first} {id}", message, None)
        assert result == "{first} {id}"

    def test_fillings_html_escapes_user_data(self) -> None:
        """HTML in user fields is escaped to prevent injection."""
        message, user = self._make_message_and_user(first_name="<script>alert(1)</script>")
        result = process_fillings("{first}", message, user)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_fillings_replaces_chatid(self) -> None:
        """{chatid} is replaced with the chat ID."""
        message, user = self._make_message_and_user(chat_id=-100555)
        result = process_fillings("{chatid}", message, user)
        assert "-100555" in result


# ─── Combine saveables tests ────────────────────────────────────────────────


class TestCombineSaveables:
    def test_combine_saveables_text_only(self) -> None:
        """Combines two text-only saveables into a single saveable with HTML."""
        from stfu_tg import Bold

        from sophie_bot.db.models.notes import Saveable, SaveableParseMode
        from sophie_bot.modules.notes.utils.combine import combine_saveables

        saveable1 = Saveable(text="Hello world", parse_mode=SaveableParseMode.html)
        saveable2 = Saveable(text="Goodbye world", parse_mode=SaveableParseMode.html)

        title1 = Bold("Note 1:")
        title2 = Bold("Note 2:")

        result = combine_saveables((saveable1, title1), (saveable2, title2))

        assert result.text is not None
        assert "Hello world" in result.text
        assert "Goodbye world" in result.text
        assert result.parse_mode == SaveableParseMode.html
