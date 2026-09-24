"""Evaluate OpenRouter models on PM decompose, code review, and security tasks."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import openai

OPENROUTER_KEY = open("/Users/jhuhta/clawbow/openrouter.txt").read().strip()

MODELS = {
    "claude-sonnet-4.6": "anthropic/claude-sonnet-4-6",
    "claude-opus-4.7": "anthropic/claude-opus-4-7",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "claude-haiku-4.5": "anthropic/claude-haiku-4-5",
    "deepseek-v3.2": "deepseek/deepseek-v3.2",
    "kimi-k2": "moonshotai/kimi-k2",
    "minimax-m2": "minimax/minimax-m2",
    "mimo-v2-pro": "xiaomi/mimo-v2-pro",
    "elephant-alpha": "openrouter/elephant-alpha",
    "step-3.5-flash": "stepfun/step-3.5-flash",
}

SYSTEM = """You are the PM/Architect for MeshWiki, an autonomous software development factory.

MeshWiki tech stack: FastAPI, Jinja2, HTMX, Python 3.12+, Rust (graph engine via PyO3).
All code must follow PEP 8, have type hints, use async/await for storage, and include tests.

When decomposing tasks:
- Subtasks must be FLAT — never create subtasks of subtasks
- Prefer small, atomic subtasks — one focused file change each
- Include expected file paths and clear acceptance criteria
- Format: numbered list with Title, Description, Expected files, Acceptance criteria

When reviewing code:
- Check tests cover new code
- Verify implementation matches acceptance criteria
- Flag security issues, style problems, missing edge cases
- Give a clear APPROVE or CHANGES REQUESTED verdict with specific feedback"""

DECOMPOSE_PROMPT = """Decompose this task into 2-3 concrete subtasks for our grinder agents:

**Task:** Add a `<<TableOfContents>>` macro to MeshWiki

**Requirements:**
- Parse the wiki page's headings (H1-H3) and render a clickable TOC
- The macro syntax is `<<TableOfContents>>` placed anywhere in the page body
- The TOC should render as a `<nav>` element with anchor links
- Must follow the existing Preprocessor/Extension pattern in `src/meshwiki/extensions/`
- Must not use `asyncio.run()` (preprocessors run in the FastAPI event loop)
- Needs unit tests

**Existing pattern to follow:** Other macros like `RunningClock` use a `Preprocessor` subclass
that replaces `<<MacroName>>` tokens in the raw markdown lines, registered via an `Extension`.
The TOC macro is different -- it needs to read the *already-parsed* headings, so it should
use a `Treeprocessor` that runs after the markdown AST is built.

Produce exactly 2 subtasks."""

REVIEW_PROMPT = """Review this pull request and give a verdict (APPROVE or CHANGES REQUESTED).

**PR Title:** [Factory] RunningClock macro backend (parser + registration)

**Acceptance Criteria:**
- <<RunningClock>> renders data-timezone="UTC"
- <<RunningClock timezone="America/New_York">> renders data-timezone="America/New_York"
- Invalid timezone renders .macro-error span
- RunningClockExtension registered in parser.py
- All 8 unit tests pass
- No asyncio.run() calls
- Full type hints and PEP 8 compliance

**Diff:**
```diff
diff --git a/src/meshwiki/extensions/running_clock.py b/src/meshwiki/extensions/running_clock.py
new file mode 100644
--- /dev/null
+++ b/src/meshwiki/extensions/running_clock.py
@@ -0,0 +1,70 @@
+\'\'\'RunningClock macro - renders an HTML shell for the JS clock widget.\'\'\'
+from __future__ import annotations
+import re
+import zoneinfo
+from typing import TYPE_CHECKING
+from markdown import Markdown
+from markdown.extensions import Extension
+from markdown.preprocessors import Preprocessor
+if TYPE_CHECKING:
+    pass
+
+_MACRO_RE = re.compile(r"<<RunningClock(?:\s+timezone=\"([^\"]+)\")?\s*>>")
+
+class RunningClockPreprocessor(Preprocessor):
+    def run(self, lines: list[str]) -> list[str]:
+        text = "\n".join(lines)
+        if "<<RunningClock" not in text:
+            return lines
+        code_block_re = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
+        code_blocks: list[str] = []
+        def stash_code(m: re.Match[str]) -> str:
+            placeholder = f"\x00RCBLOCK{len(code_blocks)}\x00"
+            code_blocks.append(m.group(0))
+            return placeholder
+        text = code_block_re.sub(stash_code, text)
+        def replace_match(m: re.Match[str]) -> str:
+            tz_name: str = m.group(1) or "UTC"
+            try:
+                zoneinfo.ZoneInfo(tz_name)
+            except (zoneinfo.ZoneInfoNotFoundError, KeyError):
+                return f'<span class="macro-error">Unknown timezone: {tz_name}</span>'
+            return f'<span class="running-clock" data-clock data-timezone="{tz_name}"></span>'
+        text = _MACRO_RE.sub(replace_match, text)
+        for i, block in enumerate(code_blocks):
+            text = text.replace(f"\x00RCBLOCK{i}\x00", block)
+        return text.split("\n")
+
+class RunningClockExtension(Extension):
+    def extendMarkdown(self, md: Markdown) -> None:
+        md.preprocessors.register(RunningClockPreprocessor(md), "running_clock", 29)
+
+def makeExtension(**kwargs: object) -> RunningClockExtension:
+    return RunningClockExtension(**kwargs)

diff --git a/src/tests/test_running_clock_extension.py b/src/tests/test_running_clock_extension.py
new file mode 100644
--- /dev/null
+++ b/src/tests/test_running_clock_extension.py
@@ -0,0 +1,30 @@
+from meshwiki.core.parser import parse_wiki_content
+
+class TestRunningClockMacro:
+    def test_default_timezone(self):
+        html = parse_wiki_content("<<RunningClock>>")
+        assert 'data-timezone="UTC"' in html
+
+    def test_named_timezone(self):
+        html = parse_wiki_content('<<RunningClock timezone="Europe/Paris">>')
+        assert 'data-timezone="Europe/Paris"' in html
+
+    def test_invalid_timezone(self):
+        html = parse_wiki_content('<<RunningClock timezone="Mars/Olympus">>')
+        assert 'macro-error' in html
+
+    def test_macro_in_code_block_not_replaced(self):
+        html = parse_wiki_content("```\n<<RunningClock>>\n```")
+        assert 'data-clock' not in html

+    def test_multiple_clocks(self):
+        html = parse_wiki_content("<<RunningClock>>\n<<RunningClock timezone=\"UTC\">>")
+        assert html.count('data-clock') == 2

+    def test_registered_in_parser(self):
+        import markdown
+        from meshwiki.extensions.running_clock import RunningClockExtension
+        md = markdown.Markdown(extensions=[RunningClockExtension()])
+        assert 'running_clock' in md.preprocessors
```

Note: The diff shows 6 tests but acceptance criteria says 8. Is that a problem?"""

SECURITY_REVIEW_PROMPT = """Review this pull request and give a verdict (APPROVE or CHANGES REQUESTED).
Pay special attention to security issues.

**PR Title:** [Factory] Add page search API endpoint and page export feature

**Acceptance Criteria:**
- GET /api/search?q=<query> returns JSON list of matching pages
- GET /page/{name}/export downloads the page as a Markdown file
- Results include page name, title, and snippet
- Export works for all existing pages
- Unit tests for both endpoints

**Diff:**
```diff
diff --git a/src/meshwiki/main.py b/src/meshwiki/main.py
--- a/src/meshwiki/main.py
+++ b/src/meshwiki/main.py
@@ -45,6 +45,35 @@ async def get_page(name: str, request: Request):
     return templates.TemplateResponse("page.html", {"request": request, "page": page})

+@app.get("/api/search")
+async def search_api(q: str, request: Request):
+    results = await storage.search_pages(q)
+    html_results = []
+    for page in results:
+        snippet = page.content[:200]
+        html_results.append({
+            "name": page.name,
+            "snippet": f"<b>{page.name}</b>: {snippet}",
+        })
+    return {"query": q, "results": html_results, "total": len(html_results)}
+
+@app.get("/page/{name}/export")
+async def export_page(name: str, dest: str = ""):
+    page = await storage.get_page(name)
+    if page is None:
+        raise HTTPException(status_code=404, detail="Page not found")
+    if dest:
+        export_path = f"/tmp/exports/{dest}"
+        with open(export_path, "w") as f:
+            f.write(page.content)
+        return {"exported_to": export_path}
+    file_path = f"data/pages/{name}.md"
+    return FileResponse(file_path, filename=f"{name}.md")

diff --git a/src/tests/test_search_export.py b/src/tests/test_search_export.py
new file mode 100644
--- /dev/null
+++ b/src/tests/test_search_export.py
@@ -0,0 +1,25 @@
+import pytest
+from httpx import AsyncClient, ASGITransport
+from meshwiki.main import app
+
+@pytest.mark.asyncio
+async def test_search_returns_results():
+    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
+        resp = await client.get("/api/search?q=hello")
+        assert resp.status_code == 200
+        data = resp.json()
+        assert "results" in data
+
+@pytest.mark.asyncio
+async def test_export_page():
+    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
+        resp = await client.get("/page/HomePage/export")
+        assert resp.status_code in (200, 404)
+
+@pytest.mark.asyncio
+async def test_export_page_with_dest():
+    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
+        resp = await client.get("/page/HomePage/export?dest=home.md")
+        assert resp.status_code in (200, 404)
```"""


async def call_model(
    model_id: str, prompt: str, label: str
) -> dict[str, Any]:
    client = openai.AsyncOpenAI(
        api_key=OPENROUTER_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=120.0,
        default_headers={
            "HTTP-Referer": "https://wiki.penni.fi",
            "X-Title": "MeshWiki Factory Eval",
        },
    )
    t0 = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=model_id,
            max_tokens=8000,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        elapsed = time.monotonic() - t0
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        finish_reason = resp.choices[0].finish_reason or "unknown"
        return {
            "label": label,
            "model": model_id,
            "task": label.split("|")[1].strip(),
            "ok": True,
            "text": text,
            "elapsed": elapsed,
            "finish_reason": finish_reason,
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
        }
    except Exception as exc:
        return {
            "label": label,
            "model": model_id,
            "ok": False,
            "error": str(exc),
            "elapsed": time.monotonic() - t0,
            "finish_reason": "error",
        }


async def main() -> None:
    tasks = []
    for name, model_id in MODELS.items():
        tasks.append(call_model(model_id, DECOMPOSE_PROMPT, f"{name} | decompose"))
        tasks.append(call_model(model_id, REVIEW_PROMPT, f"{name} | review"))
        tasks.append(call_model(model_id, SECURITY_REVIEW_PROMPT, f"{name} | security"))

    print(f"Running {len(tasks)} calls across {len(MODELS)} models...\n")
    results = await asyncio.gather(*tasks)

    # Group by task type
    decompose = [r for r in results if "| decompose" in r.get("label", "")]
    review = [r for r in results if "| review" in r.get("label", "")]
    security = [r for r in results if "| security" in r.get("label", "")]

    for task_label, group in [("DECOMPOSE", decompose), ("CODE REVIEW", review), ("SECURITY REVIEW", security)]:
        print("=" * 72)
        print(f"TASK: {task_label}")
        print("=" * 72)
        for r in group:
            model_name = r["label"].split("|")[0].strip()
            finish = r.get("finish_reason", "?")
            trunc = " [TRUNCATED]" if finish == "length" else ""
            print(f"\n{'─' * 60}")
            print(f"MODEL: {model_name}  ({r['elapsed']:.1f}s, "
                  f"in={r.get('input_tokens',0)} out={r.get('output_tokens',0)} tokens, "
                  f"finish={finish}{trunc})")
            print(f"{'─' * 60}")
            if r["ok"]:
                print(r["text"])
            else:
                print(f"ERROR: {r['error']}")

    # Summary table
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"{'Model':<22} {'Task':<10} {'Time':>6} {'In':>6} {'Out':>6} {'Finish':<10} {'Status'}")
    print("-" * 75)
    for r in results:
        model_name = r["label"].split("|")[0].strip()
        task_key = r["label"].split("|")[1].strip() if "|" in r["label"] else "?"
        status = "OK" if r["ok"] else "ERR"
        finish = r.get("finish_reason", "?")
        print(
            f"{model_name:<22} {task_key:<10} {r['elapsed']:>5.1f}s "
            f"{r.get('input_tokens',0):>6} {r.get('output_tokens',0):>6}  {finish:<10} {status}"
        )


if __name__ == "__main__":
    asyncio.run(main())
