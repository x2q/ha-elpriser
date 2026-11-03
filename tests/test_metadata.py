"""Manifest, hacs.json, translations and icons stay consistent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.elpriser.const import DK_GRID_COMPANIES, DK_MODES, DOMAIN
from custom_components.elpriser.sensor import SENSORS

ROOT = Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components" / DOMAIN


def keys(tree: object, prefix: str = "") -> set[str]:
    if isinstance(tree, dict):
        return {k for key, value in tree.items() for k in keys(value, f"{prefix}{key}.")}
    return {prefix.rstrip(".")}


def test_manifest() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == "elpriser" and manifest["name"] == "Elpriser"
    assert manifest["config_flow"] is True and manifest["iot_class"] == "cloud_polling"
    assert manifest["codeowners"] == ["@x2q"]
    assert manifest["issue_tracker"] == "https://github.com/x2q/ha-elpriser/issues"
    assert manifest["documentation"].startswith("https://github.com/x2q/ha-elpriser")
    assert manifest["version"].count(".") == 2
    assert manifest["requirements"] == []
    assert list(manifest)[:2] == ["domain", "name"]
    assert list(manifest)[2:] == sorted(list(manifest)[2:])  # hassfest: domain, name, then alphabetical


def test_hacs_json() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "Elpriser"


@pytest.mark.parametrize("lang", ["da", "de"])
def test_translations_have_the_same_keys_as_english(lang: str) -> None:
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    other = json.loads((COMPONENT / "translations" / f"{lang}.json").read_text())
    assert keys(other) == keys(en)


def test_strings_json_is_english() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert strings == en


def test_every_entity_and_mode_is_translated() -> None:
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    for description in SENSORS:
        assert en["entity"]["sensor"][description.key]["name"]
    assert en["entity"]["binary_sensor"]["cheap_hour_now"]["name"]
    assert set(en["selector"]["dk_mode"]["options"]) == set(DK_MODES)
    assert set(en["entity"]["sensor"]["price_level"]["state"]) == {"cheap", "normal", "expensive"}


def test_icons_cover_every_entity() -> None:
    icons = json.loads((COMPONENT / "icons.json").read_text())["entity"]
    assert {d.key for d in SENSORS} == set(icons["sensor"])
    assert set(icons["binary_sensor"]) == {"cheap_hour_now"}


def test_grid_company_table_is_sane() -> None:
    glns = [gln for companies in DK_GRID_COMPANIES.values() for _, gln in companies]
    assert len(glns) == len(set(glns)) == 31
    assert all(len(g) == 13 and g.isdigit() for g in glns)
