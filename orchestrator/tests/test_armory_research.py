"""Tests for the armory-research consumer bot."""

from __future__ import annotations

import json

import pytest

from factory.bots import armory_research
from factory.bots.armory_research import ArmoryResearchBot

# `_is_valid` is a staticmethod on the bot.
_is_valid = ArmoryResearchBot._is_valid


def _finding(**over):
    f = {
        "vuln_class": "idor-bola",
        "artifact_path": "playbooks/idor-bola-generic.md",
        "scope": "generic",
        "target": None,
        "severity": "high",
        "source": "h1",
        "rationale": "No generic IDOR/BOLA playbook exists yet.",
        "detail": "Probe GET /api/<obj>/{id} for missing ownership checks.",
        "title": "Create generic playbook: IDOR/BOLA",
        "evidence": [
            {
                "kind": "h1_report",
                "ref": "H1 #123",
                "url": "https://h1/123",
                "title": "IDOR on notes",
            },
        ],
    }
    f.update(over)
    return f


# ---------------------------------------------------------------------------
# Rendering + identity
# ---------------------------------------------------------------------------


def test_page_name_stable_and_dateless():
    f = _finding()
    n1 = armory_research._page_name(f)
    n2 = armory_research._page_name(f)
    assert n1 == n2 == "Task_Playbook_idor_bola_generic"
    # No date/report-id component that would defeat dedup.
    assert not any(ch.isdigit() for ch in n1)


def test_page_name_target_specific():
    f = _finding(scope="target-specific", target="notekeeper")
    assert (
        armory_research._page_name(f)
        == "Task_Playbook_notekeeper_idor_bola_target_specific"
    )


def test_render_carries_stable_dedup_key_and_path():
    name, content = armory_research._render_task_page(
        _finding(), "jyrkihuhta/molly-armory"
    )
    assert name == "Task_Playbook_idor_bola_generic"
    assert "playbook_path: playbooks/idor-bola-generic.md" in content
    assert "repo: jyrkihuhta/molly-armory" in content
    assert "repo_root: playbooks" in content
    assert "artifact_type: playbook" in content
    assert "skip_decomposition: true" in content
    assert "vulnerability_class: idor-bola" in content
    # Evidence rendered as a reference link.
    assert "[H1 #123 — IDOR on notes](https://h1/123)" in content
    # Optional detail section present when provided.
    assert "## Detail" in content


def test_render_omits_detail_when_absent():
    _n, content = armory_research._render_task_page(_finding(detail=None), "r/a")
    assert "## Detail" not in content


def test_is_valid_rejects_missing_required():
    assert _is_valid(_finding()) is True
    assert _is_valid(_finding(title="")) is False
    assert _is_valid(_finding(artifact_path="")) is False
    assert _is_valid({"vuln_class": "x"}) is False
    assert _is_valid("not a dict") is False


# ---------------------------------------------------------------------------
# run(): dedup, cap, create — with a fake wiki client
# ---------------------------------------------------------------------------


class _FakeWiki:
    """One shared instance stands in for every ``MeshWikiClient()`` call."""

    def __init__(self, open_tasks=None, existing=None):
        self._open = open_tasks or {}
        self.existing = set(existing or [])
        self.created: dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def list_tasks(self, status=None, assignee=None, **_):
        return self._open.get(status, [])

    async def get_page(self, name):
        return {"name": name} if name in self.existing else None

    async def create_page(self, name, content):
        self.created[name] = content
        self.existing.add(name)
        return {"name": name}


def _install_fake_wiki(monkeypatch, fake):
    monkeypatch.setattr(armory_research, "MeshWikiClient", lambda *a, **k: fake)


@pytest.mark.asyncio
async def test_run_creates_tasks_for_fresh_findings(monkeypatch):
    fake = _FakeWiki()
    _install_fake_wiki(monkeypatch, fake)
    bot = ArmoryResearchBot(interval_seconds=1, max_tasks_per_run=5)

    findings = [
        _finding(),
        _finding(
            vuln_class="ssrf",
            artifact_path="playbooks/ssrf-generic.md",
            title="Create generic playbook: SSRF",
        ),
    ]

    async def _stub():
        return findings

    bot._fetch_findings = _stub
    result = await bot.run()

    assert result.actions_taken == 2
    assert set(fake.created) == {
        "Task_Playbook_idor_bola_generic",
        "Task_Playbook_ssrf_generic",
    }


@pytest.mark.asyncio
async def test_run_dedups_against_open_tasks(monkeypatch):
    # An open task already targets idor-bola-generic.md → must not re-file it.
    fake = _FakeWiki(
        open_tasks={
            "planned": [
                {
                    "name": "Task_Playbook_idor_bola_generic",
                    "metadata": {"playbook_path": "playbooks/idor-bola-generic.md"},
                },
            ],
        }
    )
    _install_fake_wiki(monkeypatch, fake)
    bot = ArmoryResearchBot()

    async def _stub():
        return [
            _finding(),
            _finding(
                vuln_class="ssrf",
                artifact_path="playbooks/ssrf-generic.md",
                title="SSRF",
            ),
        ]

    bot._fetch_findings = _stub
    result = await bot.run()

    assert result.actions_taken == 1
    assert set(fake.created) == {"Task_Playbook_ssrf_generic"}


@pytest.mark.asyncio
async def test_run_caps_per_run(monkeypatch):
    fake = _FakeWiki()
    _install_fake_wiki(monkeypatch, fake)
    bot = ArmoryResearchBot(max_tasks_per_run=2)

    async def _stub():
        return [
            _finding(
                vuln_class=f"class-{i}",
                artifact_path=f"playbooks/class-{i}-generic.md",
                title=f"Class {i}",
            )
            for i in range(5)
        ]

    bot._fetch_findings = _stub
    result = await bot.run()
    assert result.actions_taken == 2
    assert len(fake.created) == 2


@pytest.mark.asyncio
async def test_run_skips_existing_page(monkeypatch):
    fake = _FakeWiki(existing={"Task_Playbook_idor_bola_generic"})
    _install_fake_wiki(monkeypatch, fake)
    bot = ArmoryResearchBot()

    async def _stub():
        return [_finding()]

    bot._fetch_findings = _stub
    result = await bot.run()
    assert result.actions_taken == 0
    assert fake.created == {}


@pytest.mark.asyncio
async def test_run_fetch_failure_is_reported_not_raised(monkeypatch):
    fake = _FakeWiki()
    _install_fake_wiki(monkeypatch, fake)
    bot = ArmoryResearchBot()

    async def _boom():
        raise RuntimeError("module blew up")

    bot._fetch_findings = _boom
    result = await bot.run()
    assert result.actions_taken == 0
    assert result.errors and "module blew up" in result.errors[0]


@pytest.mark.asyncio
async def test_run_drops_invalid_findings(monkeypatch):
    fake = _FakeWiki()
    _install_fake_wiki(monkeypatch, fake)
    bot = ArmoryResearchBot()

    async def _stub():
        return [_finding(), {"vuln_class": "broken"}]  # second missing keys

    bot._fetch_findings = _stub
    result = await bot.run()
    assert result.actions_taken == 1
    assert "invalid=1" in result.details


# ---------------------------------------------------------------------------
# _fetch_findings transport (subprocess)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._out, self._err, self.returncode = stdout, stderr, returncode

    async def communicate(self):
        return self._out, self._err

    def kill(self):  # pragma: no cover - only on timeout path
        pass


@pytest.mark.asyncio
async def test_fetch_findings_parses_findings_object(monkeypatch):
    # Frozen contract: {"findings": [...]} (sibling "unmapped" ignored).
    payload = json.dumps(
        {"findings": [_finding()], "unmapped": [{"ignored": True}]}
    ).encode()

    async def _fake_exec(*argv, **kw):
        # The agreed CLI contract: our flags are appended.
        assert "--format" in argv and "json" in argv
        return _FakeProc(stdout=payload, returncode=0)

    monkeypatch.setattr(armory_research.asyncio, "create_subprocess_exec", _fake_exec)
    bot = ArmoryResearchBot()
    out = await bot._fetch_findings()
    assert out == [_finding()]


@pytest.mark.asyncio
async def test_fetch_findings_rejects_bare_array(monkeypatch):
    # A bare array is NOT the frozen contract and must be rejected.
    async def _fake_exec(*argv, **kw):
        return _FakeProc(stdout=json.dumps([_finding()]).encode(), returncode=0)

    monkeypatch.setattr(armory_research.asyncio, "create_subprocess_exec", _fake_exec)
    bot = ArmoryResearchBot()
    with pytest.raises(RuntimeError, match="findings"):
        await bot._fetch_findings()


@pytest.mark.asyncio
async def test_fetch_findings_raises_on_nonzero_exit(monkeypatch):
    async def _fake_exec(*argv, **kw):
        return _FakeProc(stdout=b"", stderr=b"boom", returncode=2)

    monkeypatch.setattr(armory_research.asyncio, "create_subprocess_exec", _fake_exec)
    bot = ArmoryResearchBot()
    with pytest.raises(RuntimeError, match="exited 2"):
        await bot._fetch_findings()
