import logging
import sys


DEFAULT_FORMATTER = logging.Formatter(
    datefmt = "%Y-%m-%dT%H:%M:%S",
    fmt = "#{levelname} {name} {asctime},{msecs:0>3,.0f}\n{message}",
    style="{"
)


class ModernLogRecord(logging.LogRecord):
    def getMessage(self):
        msg = self.msg
        if self.args:
            msg = msg.format(*self.args)

        return msg


class MaxLevelFilter(logging.Filter):
    def __init__(self, max_level = logging.ERROR):
        self._max_level = max_level

    def filter(self, record):
        return record.levelno < self._max_level


logging.setLogRecordFactory(ModernLogRecord)

logger = logging.getLogger("centimani")
handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.ERROR)
handler.setFormatter(DEFAULT_FORMATTER)
logger.addHandler(handler)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
handler.setFormatter(DEFAULT_FORMATTER)
handler.addFilter(MaxLevelFilter(logging.ERROR))
logger.addHandler(handler)
