"""Conservative screening shared by offline import and AI consolidation."""
import re

SENSITIVE = re.compile(r'(?i)(?:sk-[a-z0-9_-]{16,}|-----BEGIN .*PRIVATE KEY|(?:password|api[_ -]?key|token|密码|密钥)\s*[:：=]\s*\S{4,}|(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[a-z]{2,})')
