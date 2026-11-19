from aiogram.fsm.state import State, StatesGroup


class WelcomeSecurityFSM(StatesGroup):
    captcha = State()
    security_message = State()
