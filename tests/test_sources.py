import pytest

from mpexplorer import sources as S
from mpexplorer.settings import Settings


def test_presets_validate_and_the_default_pattern_reads_real_pimoroni_names():
    import re
    for preset in S.FIRMWARE_PRESETS:
        assert S.validate_firmware_source(preset)["repo"].startswith("pimoroni/")
    rx = re.compile(S.DEFAULT_PATTERN)
    for name, board, version in (("pico2_w-v1.29.0-2-pimoroni-micropython.uf2", "pico2_w", "v1.29.0-2"),
                                 ("pimoroni_pico_plus2-v1.29.0-micropython.uf2", "pimoroni_pico_plus2", "v1.29.0"),
                                 ("pimoroni_tiny2350-v1.26.1-micropython.uf2", "pimoroni_tiny2350", "v1.26.1")):
        m = rx.match(name)
        assert (m["board"], m["version"]) == (board, version)
    assert not rx.match("readme.txt")


def test_validation_cleans_a_source_and_refuses_the_rest():
    ok = S.validate_firmware_source({"name": " Mine ", "kind": "github", "repo": "https://github.com/a/b/"})
    assert ok == {"name": "Mine", "kind": "github", "repo": "a/b", "pattern": S.DEFAULT_PATTERN}
    for bad in ({"name": "x" * 41, "kind": "github", "repo": "a/b"}, {"name": "x", "kind": "ftp"},
                {"name": "x", "kind": "github", "repo": "a b/c"}, {"name": "x", "kind": "url", "url": "http://a/b"}):
        with pytest.raises(S.SourceError):
            S.validate_firmware_source(bad)


def test_the_settings_keep_sources_and_refuse_garbage():
    st = Settings()
    S.add_source(st, "firmware", S.validate_firmware_source(S.FIRMWARE_PRESETS[0]))
    S.add_source(st, "firmware", S.validate_firmware_source(S.FIRMWARE_PRESETS[0]))        # same name: replaced
    assert [s["name"] for s in st.get("sources.firmware")] == ["Pimoroni MicroPython"]
    S.add_source(st, "libraries", dict(S.LIBRARY_PRESETS[1]))
    assert st.get("sources.libraries")[0]["prefix"] == "qwiic-py"
    for key, bad in (("sources.firmware", [{"name": "x", "kind": "github", "surprise": "1"}]),
                     ("sources.firmware", [{"name": "", "kind": "github"}]), ("sources.libraries", "text"),
                     ("sources.trusted_hosts", [1]), ("boards.installed", {"chip:1": "text"})):
        with pytest.raises(ValueError):
            st.set(key, bad)


def test_trust_applies_to_other_sites_only_and_goes_when_the_last_source_goes():
    st = Settings()
    assert S.is_trusted(st, "github.com") and S.is_trusted(st, "release-assets.githubusercontent.com")
    assert not S.is_trusted(st, "dl.maker.example")
    S.trust_host(st, "github.com")
    assert st.get("sources.trusted_hosts") == []                       # never stored for GitHub
    S.trust_host(st, "dl.maker.example")
    S.add_source(st, "firmware", {"name": "Maker", "kind": "url", "url": "https://dl.maker.example/a.uf2"})
    S.remove_source(st, "firmware", "Nobody")
    assert S.is_trusted(st, "dl.maker.example")
    S.remove_source(st, "firmware", "Maker")
    assert st.get("sources.trusted_hosts") == [] and st.get("sources.firmware") == []


def test_version_order_and_the_newer_than_rule():
    assert S.version_key("v1.29.0-2") == (1, 29, 0, 2) and S.version_key("") == ()
    assert S.version_key("v1.29.0-10") > S.version_key("v1.29.0-9")
    assert S.newer_than("v1.29.0-3", installed="v1.29.0-2") and not S.newer_than("v1.29.0-2", installed="v1.29.0-2")
    assert S.newer_than("v1.30.0", running="1.29.0") and not S.newer_than("v1.29.0-2", running="1.29.0")
    assert not S.newer_than("v1.30.0")                                  # nothing known about the board: no claim
