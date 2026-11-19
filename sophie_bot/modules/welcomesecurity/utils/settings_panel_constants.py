from datetime import timedelta

from sophie_bot.db.models.greetings import WELCOMEMUTE_DEFAULT_TIME

MEDIA_DURATION_OPTIONS = (timedelta(hours=1), timedelta(hours=12), WELCOMEMUTE_DEFAULT_TIME, timedelta(weeks=1))
