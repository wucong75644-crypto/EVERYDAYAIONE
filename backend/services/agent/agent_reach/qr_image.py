"""Isolated QR renderer using the already pinned runtime dependency."""
import base64
import io
import json
import sys
import qrcode

payload = json.load(sys.stdin)
text = payload['text']
if not isinstance(text, str) or len(text) > 4096:
    raise ValueError('Invalid QR payload')
image = qrcode.make(text)
buffer = io.BytesIO()
image.save(buffer, format='PNG')
print(json.dumps({'image':'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}))
