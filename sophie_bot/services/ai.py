from __future__ import annotations

from mistralai.client.sdk import Mistral

from sophie_bot.config import CONFIG

mistral_client = Mistral(api_key=CONFIG.mistral_api_key or "")
