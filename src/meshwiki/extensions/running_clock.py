"""RunningClock macro — renders an HTML shell for the JS clock widget.

Usage in wiki markup:
    <<RunningClock>>
    <<RunningClock timezone="Europe/London">>
"""

from __future__ import annotations

import re
import zoneinfo
from typing import TYPE_CHECKING

from markdown import Markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

if TYPE_CHECKING:
    pass

_MACRO_RE = re.compile(r"<<RunningClock(?:\s+timezone=\"([^\"]+)\")?\s*>>")


class RunningClockPreprocessor(Preprocessor):
    """Replace <<RunningClock …>> tokens with the clock HTML shell."""

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)
        if "<<RunningClock" not in text:
            return lines

        code_block_re = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
        code_blocks: list[str] = []

        def stash_code(m: re.Match[str]) -> str:
            placeholder = f"\x00RCBLOCK{len(code_blocks)}\x00"
            code_blocks.append(m.group(0))
            return placeholder

        text = code_block_re.sub(stash_code, text)

        def replace_match(m: re.Match[str]) -> str:
            tz_name: str = m.group(1) or "UTC"
            try:
                zoneinfo.ZoneInfo(tz_name)
            except (zoneinfo.ZoneInfoNotFoundError, KeyError):
                return f'<span class="macro-error">Unknown timezone: {tz_name}</span>'
            return f'<span class="running-clock" data-clock data-timezone="{tz_name}"></span>'

        text = _MACRO_RE.sub(replace_match, text)

        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00RCBLOCK{i}\x00", block)

        return text.split("\n")


class RunningClockExtension(Extension):
    """Markdown extension that registers the RunningClock preprocessor."""

    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(
            RunningClockPreprocessor(md),
            "running_clock",
            29,
        )


def makeExtension(**kwargs: object) -> RunningClockExtension:
    return RunningClockExtension(**kwargs)
