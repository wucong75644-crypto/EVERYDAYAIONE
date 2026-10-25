"""Model-authored knowledge URLs are proposals, never trusted navigation targets."""
import re
from urllib.parse import urlparse,parse_qsl

PATTERN=re.compile(r'(?:https?://[^\s/<>\[\]()]+)?/knowledge\?[A-Za-z0-9_%=&+.:~-]+')


def key(url):
    parsed=urlparse(url)
    if parsed.path!='/knowledge':return None
    pairs=parse_qsl(parsed.query,keep_blank_values=True)
    if len({k for k,_ in pairs})!=len(pairs):return None
    return tuple(sorted(pairs))


def normalize(text, sources):
    known={key(source):source for source in sources}
    def replace(match):
        return known.get(key(match.group()),'（知识来源未核验）')
    def markdown(match):
        label,url=match.groups()
        if not PATTERN.fullmatch(url):return match.group()
        canonical=known.get(key(url))
        return '['+label+']('+canonical+')' if canonical else label+'（知识来源未核验）'
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',markdown,text)
    return PATTERN.sub(replace,text)
