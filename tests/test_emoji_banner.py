from io import BytesIO

import pytest
from PIL import Image
from PIL.ImageFont import FreeTypeFont

from sophie_bot.utils import emoji_banner
from sophie_bot.utils.emoji_banner import EmojiBanner


class TinyEmojiBanner(EmojiBanner):
    width = 4
    height = 3


class FakeFont:
    def __init__(self, size: int) -> None:
        self.size = size

    def getbbox(self, text: str) -> tuple[int, int, int, int]:
        return (0, 0, len(text) * self.size, self.size)


def test_gradient_background_interpolates_theme_colors() -> None:
    image = TinyEmojiBanner._gradient_background("#000000", "#ffffff")

    assert image.size == (4, 3)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert image.getpixel((3, 1)) == (85, 85, 85)
    assert image.getpixel((2, 2)) == (170, 170, 170)


def test_fit_font_downsizes_until_text_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_sizes: list[int] = []

    def fake_truetype(font_path: str, size: int) -> FreeTypeFont:
        loaded_sizes.append(size)
        return FakeFont(size)  # type: ignore[return-value]

    monkeypatch.setattr(emoji_banner, "truetype", fake_truetype)

    fitted_font = EmojiBanner._fit_font("wide", "font.ttf", max_width=32, start_size=10)

    assert isinstance(fitted_font, FakeFont)
    assert fitted_font.size == 8
    assert loaded_sizes == [10, 9, 8]


def test_fit_font_retries_invalid_pixel_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_sizes: list[int] = []

    def fake_truetype(font_path: str, size: int) -> FreeTypeFont:
        loaded_sizes.append(size)
        if size > 11:
            raise OSError("invalid pixel size")
        return FakeFont(size)  # type: ignore[return-value]

    monkeypatch.setattr(emoji_banner, "truetype", fake_truetype)

    fitted_font = EmojiBanner._fit_font("ok", "emoji.ttf", max_width=100, start_size=13)

    assert isinstance(fitted_font, FakeFont)
    assert fitted_font.size == 11
    assert loaded_sizes == [13, 12, 11]


def test_fit_font_reraises_invalid_pixel_size_at_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_sizes: list[int] = []

    def fake_truetype(font_path: str, size: int) -> FreeTypeFont:
        loaded_sizes.append(size)
        raise OSError("invalid pixel size")

    monkeypatch.setattr(emoji_banner, "truetype", fake_truetype)

    with pytest.raises(OSError, match="invalid pixel size"):
        EmojiBanner._fit_font("text", "emoji.ttf", max_width=100, start_size=8)

    assert loaded_sizes == [8]


def test_fit_font_reraises_unrelated_font_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_truetype(font_path: str, size: int) -> FreeTypeFont:
        raise OSError("cannot open resource")

    monkeypatch.setattr(emoji_banner, "truetype", fake_truetype)

    with pytest.raises(OSError, match="cannot open resource"):
        EmojiBanner._fit_font("text", "missing.ttf", max_width=100, start_size=12)


def test_render_accepts_sequence_and_named_theme() -> None:
    banner_bytes = EmojiBanner.render(["✅", "🚀"], " Coverage ", "blue")

    assert banner_bytes.startswith(b"\xff\xd8")
    with Image.open(BytesIO(banner_bytes)) as image:
        assert image.format == "JPEG"
        assert image.size == (EmojiBanner.width, EmojiBanner.height)


def test_render_accepts_string_emojis_and_falls_back_to_default_theme() -> None:
    banner_bytes = EmojiBanner.render("✨", "Default theme", "unknown")

    assert banner_bytes.startswith(b"\xff\xd8")
    with Image.open(BytesIO(banner_bytes)) as image:
        assert image.format == "JPEG"
        assert image.size == (EmojiBanner.width, EmojiBanner.height)
