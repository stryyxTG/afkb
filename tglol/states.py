from aiogram.fsm.state import State, StatesGroup


class AddByCode(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_twofa = State()


class AddByZip(StatesGroup):
    waiting_zip = State()


class AddWorker(StatesGroup):
    waiting_user_id = State()
    waiting_limit = State()

class AddAdmin(StatesGroup):
    waiting_user_id = State()


class ChangeWorkerLimit(StatesGroup):
    waiting_limit = State()


class ProxyState(StatesGroup):
    waiting_proxy = State()

class TriggerState(StatesGroup):
    waiting_chat_id = State()
    waiting_word = State()
