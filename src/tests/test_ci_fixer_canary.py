"""Canary test — deliberately imports a missing package to trigger CI failure."""

import lxml.etree  # noqa: F401  — not in deps, causes ModuleNotFoundError


def test_canary():
    assert True
