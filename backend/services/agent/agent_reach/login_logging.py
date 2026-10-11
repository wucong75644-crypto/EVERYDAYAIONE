"""Keep one-use login codes/QR keys out of HTTP library and access logs."""
import logging

class LoginURLFilter(logging.Filter):
    def filter(self, record):
        def redact(value):
            text = str(value)
            if '?' in text and ('/agent-reach/oauth/callback?' in text or '/qrcode/poll?' in text or 'qrcode_key=' in text):
                return text.split('?',1)[0]+'?[redacted]'
            return value
        if isinstance(record.args,tuple): record.args=tuple(redact(value) for value in record.args)
        record.msg=redact(record.msg)
        return True

for name in ('httpx','uvicorn.access'):
    logger=logging.getLogger(name)
    if not any(isinstance(item,LoginURLFilter) for item in logger.filters): logger.addFilter(LoginURLFilter())
