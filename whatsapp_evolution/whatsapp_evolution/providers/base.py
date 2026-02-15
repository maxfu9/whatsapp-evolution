class BaseProvider:
    def __init__(self, settings):
        self.settings = settings or {}

    def send_message(self, to_number, message, **kwargs):
        raise NotImplementedError

    def parse_incoming(self, data):
        raise NotImplementedError
