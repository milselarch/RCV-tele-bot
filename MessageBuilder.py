class MessageBuilder(object):
    def __init__(self, callback=None):
        self.callback = callback
        self.lines = []
        self.sent = False

    def add(self, text, max_length=4096):
        if len(text) > max_length:
            print('LONGTEXT', text, len(text))
            raise ValueError('TEXT TOO LONG')

        self.lines.append(text)

    def get_content(self):
        str_lines = []

        for line in self.lines:
            if isinstance(line, MessageBuilder):
                str_lines.append(line.get_content())
            else:
                str_lines.append(line)

        return '\n'.join(str_lines)

    def __len__(self):
        return len(self.lines)

    async def call(self, caller):
        assert not self.sent
        await caller(self.get_content().strip())
        self.sent = True

    async def send(self, callback=None, text=None):
        assert not self.sent
        if text is not None:
            self.add(text)

        if callback is None:
            assert self.callback is not None
            callback = self.callback
        else:
            assert self.callback is None

        await callback(self.get_content().strip())
        self.sent = True
