"""Armory artifact prompts and constants for the factory grinder.

Contains system-level documentation and constraints injected into grinder
task prompts for Molly armory artifact types (tool, playbook, wordlist,
toolspec). Also exports FORBIDDEN_IMPORTS used by validate_armory_node.
"""

from __future__ import annotations

FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "http.client",
    }
)

# ---------------------------------------------------------------------------
# Tool protocol documentation
# ---------------------------------------------------------------------------

TOOL_PROTOCOL = """\
## Molly Tool Protocol

Every Molly tool is a Python class that:
1. Inherits from `ToolBase` (imported from `molly.tools.base`).
2. Defines a class attribute `capability_name: str` — a short snake_case identifier
   (e.g. `"jwt_forge"`, `"nuclei_scan"`).
3. Implements `name(self) -> str` — human-readable tool name.
4. Implements `description(self) -> str` — one sentence describing what the tool does.
5. Implements `parameters_schema(self) -> dict` — returns an OpenAI function-calling
   JSON schema dict with `"type": "object"`, `"properties"`, and `"required"` keys.
6. Implements `async execute(self, **kwargs) -> dict` — performs the attack and returns
   a result dict.  Raise `ToolError` on unrecoverable errors.

### Forbidden imports — NEVER use these modules in execute():
- `urllib`, `urllib.request`, `urllib.parse`
- `requests`, `httpx`, `aiohttp`
- `socket`, `http.client`

Reason: Molly provides a managed HTTP client.  Use the `client` keyword argument
injected by the runner if you need to make HTTP requests.

### ToolError
Raise `molly.tools.base.ToolError` for expected failure modes (e.g. target unreachable,
invalid response).  Do not use bare `Exception` for recoverable errors.

### Example skeleton:
```python
from molly.tools.base import ToolBase, ToolError


class MyTool(ToolBase):
    capability_name = "my_tool"

    def name(self) -> str:
        return "My Tool"

    def description(self) -> str:
        return "Does something to the target endpoint."

    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target URL."},
            },
            "required": ["url"],
        }

    async def execute(self, *, url: str, client=None, **kwargs) -> dict:
        if client is None:
            raise ToolError("No HTTP client provided")
        resp = await client.get(url)
        return {"status": resp.status_code, "body": resp.text[:1000]}
```
"""

# ---------------------------------------------------------------------------
# Playbook schema documentation
# ---------------------------------------------------------------------------

PLAYBOOK_SCHEMA = """\
## Molly Playbook Schema

A Molly playbook is a Markdown file (.md) with YAML frontmatter. The
`checks:` list MUST be in the frontmatter — NOT in a fenced YAML block
in the body. Molly's PlaybookLoader.from_doc() reads only the
frontmatter; checks defined elsewhere are silently ignored.

### Required frontmatter fields:

```markdown
---
playbook: <slug>          # unique snake_case identifier, e.g. jwt-algorithm-confusion
name: <Human Name>        # display name
leaf_type: <type>         # endpoint category this applies to, e.g. auth_endpoint, rest_api, graphql_api
scope: generic            # REQUIRED: generic | target-specific (see rule below)
target: <handle>          # REQUIRED when scope is target-specific
applies_to:               # list of tags; playbook fires on any leaf whose
  - auth                  # tech_fingerprint contains any of these tags
  - jwt
checks:
  - id: check_slug
    name: Check display name
    mode: deterministic     # one of: deterministic | analytical | idea | oob
    category: auth-bypass   # any string; common ones: auth-bypass, idor-bola,
                            # graphql-abuse, ssrf, xss, race-condition,
                            # business-logic, misconfiguration, info
    severity: high          # one of: critical | high | medium | low | info | unknown
    # Optional fields below
    technique: |
      Multi-line description of the attack technique. Include the win
      condition and what response signals would confirm exploitation.
    requires_capabilities:
      - oob_callback        # capability tags from molly-armory/capabilities.json
    mutations:
      - body: '{"key": "value"}'    # JSON request body
        note: short description
      - header: X-Custom-Header
        value: malicious-value
        note: header injection variant
      - url_override: https://...   # override the request URL
        note: alternative endpoint
sub_tech:                    # optional list, used to gate by tech subtype
  - hasura
---
```

### Mode semantics (how Molly executes each check):

- `deterministic` — runs through Intruder. Each `mutations:` entry is
  applied to a baseline request; the response is diffed for anomalies.
  This is what most checks should be.
- `analytical` — invokes the 7-stage LLM pipeline. Use when behavior
  needs reasoning, not pattern matching.
- `idea` — dormant; only promoted to active during a research phase
  when matching evidence is found. Useful for placeholders.
- `oob` — substitutes `{{OOB_URL}}` in mutation bodies and polls the
  OOB server for callbacks. Requires `requires_capabilities:
  [oob_callback]`.

### Mutation shape (deterministic + oob modes):

Each mutation MUST be a YAML mapping (dict), NOT a bare string.
Recognized keys:
- `body`: full JSON/raw body to send
- `header`: header name (paired with `value`)
- `value`: header value when `header` is set
- `url_override`: replace the request URL entirely
- `note`: short description for log readability

Bare strings are silently dropped at load time. WRONG:

```yaml
mutations:
  - <script>alert(1)</script>     # WRONG — string, not a dict
  - ../../../etc/passwd            # WRONG
```

RIGHT:

```yaml
mutations:
  - body: '{"x": "<script>alert(1)</script>"}'
    note: reflected XSS in body
  - url_override: /etc/passwd
    note: path traversal in URL
```

### Note-only mutations (deterministic / oob modes):

For `mode: deterministic` or `mode: oob`, at least one mutation per check
MUST carry a real payload — i.e. one of `body`, `header`, or
`url_override`. A list of `- note: ...` entries with no payload key sends
nothing to Intruder beyond the baseline request, so the check no-ops at
runtime. WRONG:

```yaml
mutations:
  - note: baseline request 1
  - note: baseline request 2     # WRONG — neither entry mutates anything
```

`mode: analytical` and `mode: idea` are exempt — those are driven by the
brain LLM, which can pick up `note:` strings as inline reasoning hooks.

### Validation rules (enforced by the validator):

- `playbook`, `name`, `leaf_type`, `scope` required in frontmatter.
- `scope: generic` — mutations work against any matching REST/GraphQL/JWT
  API via tech-tag `applies_to`, not a specific target's quirks. This is
  the default to reach for; it's what makes a playbook valuable across
  every target Molly ever points at, present or future.
- `scope: target-specific` — mutations reference one target's proprietary
  API paths, headers, tokens, or data model (e.g. a target's own `/v3/`
  endpoint namespace, a locale-prefixed path, a tenant-hierarchy URL
  shape). REQUIRES a `target: <handle>` field naming which leaf it fires
  on. Without `target:`, a target-specific playbook fires on every target
  matching `applies_to`, producing massive false-positive noise on
  unrelated targets — so `scope: target-specific` with no `target:` is a
  validation error, not a lenient default.
- `checks:` must be a non-empty list in the frontmatter.
- Each check requires `id`, `name`, `mode`, `category`, `severity`.
- `mode` must be one of: `deterministic`, `analytical`, `idea`, `oob`.
  Words like `intruder`, `forge`, `nuclei` are NOT valid — those are
  routed via `requires_capabilities`, not `mode`.
- `severity` must be one of: `critical`, `high`, `medium`, `low`,
  `info`, `unknown`.
- `mutations:` if present must be a list of mappings, not strings.
- For `deterministic` / `oob` modes, at least one mutation per check
  must contain `body`, `header`, or `url_override` (note-only is rejected).
- All YAML must be syntactically valid.

### Reference: the absolute-minimum well-formed playbook

```markdown
---
playbook: contract-minimal
name: Minimal Example
leaf_type: rest_api
scope: generic
applies_to:
  - rest
checks:
  - id: c1
    name: Minimal check
    mode: analytical
    category: misc
    severity: medium
---

Body prose explaining what this playbook tests and why.
```

### Pitfalls to avoid

- **DO NOT** put `checks:` in a fenced ```yaml block in the body — it
  must be inside the `---` frontmatter delimiters.
- **DO NOT** write `mode: intruder` — use `mode: deterministic`.
- **DO NOT** write `mutations:` as a list of bare payload strings.
- **DO NOT** write a deterministic/oob check whose mutations are all
  `- note: ...` with no payload — that no-ops at runtime.
- **DO NOT** ship a playbook with only check `id`s and the rest of the
  fields populated as `?` — that's a sign the YAML is structurally
  wrong (probably emitted as a list of strings rather than mappings).

### Test filename MUST be derived from the playbook name

If you create a test file for the playbook in the same PR, the filename
MUST be derived from the playbook's `playbook:` slug — never invented
freely. A test file with a mismatched stem silently validates the wrong
artifact (pytest will run whatever lives at the imported path) and ships
to staging looking "green" while exercising nothing relevant.

Derivation rule (executed by `factory.test_filenames`):

1. Take the playbook's `playbook:` slug from its own frontmatter, e.g.
   `tenant-users-get-acronis`.
2. Convert it to snake_case (lowercase, replace hyphens with underscores):
   `tenant_users_get_acronis`.
3. Prefix with `test_` and suffix with `.py`:
   `test_tenant_users_get_acronis.py`.
4. Place it under `tests/` (e.g. `tests/test_tenant_users_get_acronis.py`
   or `tests/playbooks/test_tenant_users_get_acronis.py`).

The armory validator (`nodes/validate_armory._check_playbook_files`)
rejects the PR when a `tests/.../test_*.py` file's stem does not match
any `playbook:` slug touched by the same PR, with a message pointing at
`factory.test_filenames` so the failure is easy to fix. If the PR
contains no new test file (loader tests cover it) the validator is a
no-op for this rule.

### Local validation (skip Python linters)

Playbook `.md` files are NOT Python. Do not run `ruff check` /
`black --check` / `isort` on them — both linters report spurious
`No Python files found` / `Cannot parse: 1:3: ---` errors that waste
a round-trip on every grinder iteration.

To validate locally use a frontmatter-only YAML parser:

```bash
python -c "import yaml,sys; \
  d=yaml.safe_load(open(sys.argv[1]).read().split('---',2)[1]); \
  assert d.get('playbook') and d.get('name') and d.get('leaf_type') and d.get('scope')" \
  playbooks/foo.md
```

If the repo provides `scripts/validate_playbooks.py` (or equivalent
validator script), use that instead — it's the source of truth and
will catch the schema rules listed above.
"""

# ---------------------------------------------------------------------------
# Wordlist format documentation
# ---------------------------------------------------------------------------

WORDLIST_FORMAT = """\
## Molly Wordlist Format

A Molly wordlist is a plain-text file (.txt) with one entry per line.

Rules:
- UTF-8 encoding.
- One path, payload, or word per line.
- No trailing whitespace.
- Lines starting with `#` are comments (ignored by the scanner).
- Empty lines are ignored.
- Keep entries focused: quality over quantity.
- Include a comment block at the top explaining the purpose and source of the entries.

Example (`sensitive-paths.txt`):
```
# Sensitive file paths for directory enumeration
# Sources: common misconfigurations, OWASP, HackerOne disclosures
.env
.git/config
backup.zip
config.php.bak
```
"""

# ---------------------------------------------------------------------------
# Toolspec format documentation
# ---------------------------------------------------------------------------

TOOLSPEC_FORMAT = """\
## Molly Toolspec Format

A toolspec is NOT a working tool — it's a tracked proposal for a new Molly
capability that doesn't exist yet, living in `toolspecs/` until a human or
a later `artifact_type: tool` task decides to forge it for real. Use this
when the gap is "Molly's tool primitives can't do X yet" rather than
"there's an untested attack class X on an existing endpoint type" (that's
a playbook gap instead — see the Playbook Schema).

A toolspec is a Markdown file (.md) with YAML frontmatter:

```markdown
---
toolspec: <slug>              # unique snake_case identifier, e.g. jwt_confusion_forge
name: <Human Name>            # display name
capability_name: <slug>       # matches the eventual ToolBase.capability_name
                               # if/when this is forged into a real tool
status: proposed              # always "proposed" — toolspecs don't self-promote
category: <string>            # e.g. auth-bypass, oob, race-condition, misconfiguration
---

## Problem

What Molly cannot currently do, and why the existing built-in capabilities
(http_mutation / llm_analysis / concurrent_batch — see capabilities.json)
or existing forged tools in tools/ don't cover it. 2-4 sentences.

## Proposed Capability

What the tool would do, in terms of inputs and outputs, without writing the
actual implementation. What playbooks/checks would gain the ability to use
`requires_capabilities: [<capability_name>]` once this exists.

## Example Usage

A concrete scenario: which vulnerability class or target quirk this would
newly make detectable, and roughly how a check's `mutations`/`technique`
would invoke it.

## References

At least one external source: a tool that does something similar
elsewhere, a CVE/H1 report describing the technique this would enable, or
a paper.
```

### Validation rules:

- `toolspec`, `name`, `capability_name`, `status`, `category` required in
  frontmatter.
- `status` MUST be `proposed` — a toolspec is a tracked idea, not a
  build in progress. Promoting one to an actual tool is a separate,
  later `artifact_type: tool` task (see Tool Protocol above).
- `capability_name` must be DISTINCT from every existing entry in
  `capabilities.json` and every `capability_name` already used by a file
  in `tools/` — don't propose a toolspec for something already forged.
- Body must contain non-empty Problem, Proposed Capability, Example Usage,
  and References sections.

### Pitfalls to avoid

- **DO NOT** write a full Python implementation — that's what
  `artifact_type: tool` is for. A toolspec is prose + frontmatter only.
- **DO NOT** propose a toolspec for a gap that a `scope: generic` playbook
  could already cover with the existing `http_mutation`/`llm_analysis`
  capabilities — toolspecs are for genuinely new tool primitives, not an
  excuse to avoid writing a playbook.
"""

# ---------------------------------------------------------------------------
# Convenience accessor
# ---------------------------------------------------------------------------

_ARTIFACT_PROMPTS: dict[str, str] = {
    "tool": TOOL_PROTOCOL,
    "playbook": PLAYBOOK_SCHEMA,
    "wordlist": WORDLIST_FORMAT,
    "toolspec": TOOLSPEC_FORMAT,
}


def get_armory_prompt(artifact_type: str | None) -> str:
    """Return the armory protocol/schema documentation for *artifact_type*.

    Args:
        artifact_type: One of ``"tool"``, ``"playbook"``, ``"wordlist"``,
            ``"toolspec"``, or ``None`` / ``"code"`` for MeshWiki tasks
            (returns empty string).

    Returns:
        Multi-line documentation string to embed in the grinder task prompt,
        or an empty string for non-armory artifact types.
    """
    if artifact_type is None or artifact_type == "code":
        return ""
    return _ARTIFACT_PROMPTS.get(artifact_type, "")
