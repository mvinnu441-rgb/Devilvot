import html
import logging
import re

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

# Invisible markers: text between them is user content and must NOT be restyled
SA, SB = "\ue000", "\ue001"


def esc(x):
    """HTML-escape user content and protect it from the style font."""
    return SA + html.escape("" if x is None else str(x)) + SB


def raw(x):
    """Protect plain user content (no HTML escaping) from the style font."""
    return SA + ("" if x is None else str(x)) + SB


_SC = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ")

# parts of a message that must stay untouched (tags, links, @mentions, /commands, {placeholders})
_TOKEN = re.compile(
    r"(<code>.*?</code>|<pre>.*?</pre>|<[^>\n]+>|&#?\w+;|https?://\S+|(?<!\w)t\.me/\S+"
    r"|(?<![\w@])@\w+|(?<!\S)/\w+|\{[^}\n]*\})"
)


def _plain(seg):
    out = []
    for line in seg.split("\n"):
        s = line.lstrip()
        if s.startswith("/") or s.startswith("Usage:"):
            out.append(line)
            continue
        parts = _TOKEN.split(line)
        out.append("".join(p if i % 2 else p.translate(_SC) for i, p in enumerate(parts)))
    return "\n".join(out)


def strip(text):
    return text.replace(SA, "").replace(SB, "")


def stylize(text):
    """lowercase -> small caps, everything protected is left as-is."""
    try:
        out, buf, depth = [], [], 0
        for ch in text:
            if ch == SA:
                if depth == 0:
                    out.append(_plain("".join(buf)))
                    buf = []
                depth += 1
            elif ch == SB:
                if depth > 0:
                    depth -= 1
                    if depth == 0:
                        out.append("".join(buf))
                        buf = []
            else:
                buf.append(ch)
        tail = "".join(buf)
        out.append(tail if depth > 0 else _plain(tail))
        return "".join(out)
    except Exception:
        logging.exception("stylize failed")
        return strip(text)


def _style_markup(mk):
    if isinstance(mk, InlineKeyboardMarkup):
        rows = [[b.model_copy(update={"text": stylize(b.text)}) for b in row] for row in mk.inline_keyboard]
        return mk.model_copy(update={"inline_keyboard": rows})
    return mk


class StyleMiddleware(BaseRequestMiddleware):
    """Every outgoing text / caption / button label goes through the style font."""

    async def __call__(self, make_request, bot, method):
        try:
            for attr in ("text", "caption"):
                v = getattr(method, attr, None)
                if isinstance(v, str) and v:
                    setattr(method, attr, stylize(v))
            rm = getattr(method, "reply_markup", None)
            if rm is not None:
                method.reply_markup = _style_markup(rm)
        except Exception:
            logging.exception("style middleware failed")
        return await make_request(bot, method)


async def edit(cb, text, **kw):
    """Edit the callback message. If it is a photo/video message, delete it and send a new text message."""
    m = cb.message
    if m is None:
        return
    if getattr(m, "photo", None) or getattr(m, "video", None) or getattr(m, "animation", None):
        try:
            await m.delete()
        except Exception:
            pass
        return await m.answer(text, **kw)
    try:
        return await m.edit_text(text, **kw)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        raise
