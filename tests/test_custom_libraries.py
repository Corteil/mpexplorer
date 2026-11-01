import io
import json

import pytest

from mpexplorer import custom_libraries as CL
from mpexplorer import libraries, mip_source
from mpexplorer import sources as S
from mpexplorer.backends import LocalBackend
from mpexplorer.settings import Settings

RAW = "https://raw.githubusercontent.com/"
PKG = {"urls": [["phew/__init__.py", "github:pimoroni/phew/phew/__init__.py"],
                ["phew/server.py", "github:pimoroni/phew/phew/server.py"]], "deps": [], "version": "0.0.3"}
FILES = {RAW + "pimoroni/phew/HEAD/phew/__init__.py": b"# init\n",
         RAW + "pimoroni/phew/HEAD/phew/server.py": b"# server\n"}


class Reply(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def site(pages, final=None):
    def opener(url):
        if url not in pages:
            raise OSError("404 " + url)
        return Reply(pages[url], final or url)
    return opener


def phew_site(pkg=PKG, extra=None):
    pages = {RAW + "pimoroni/phew/HEAD/package.json": json.dumps(pkg).encode(), **FILES, **(extra or {})}
    return site(pages)


PHEW = S.LIBRARY_PRESETS[0]
MAKER_URL = "https://dl.maker.example/p/package.json"
MAKER = {"name": "M", "kind": "mip", "for": "micropython", "url": MAKER_URL}


def test_github_specs_become_raw_addresses():
    assert mip_source.github_url("github:pimoroni/phew/phew/server.py") == RAW + "pimoroni/phew/HEAD/phew/server.py"
    assert mip_source.github_url("github:a/b/x.py@v1.2") == RAW + "a/b/v1.2/x.py"
    assert mip_source.github_url("micropython-lib-name") is None


def test_a_package_json_repo_resolves_installs_compares_and_removes(tmp_path):
    cat = CL.make_catalog(CL.validate_library_source(PHEW), str(tmp_path / "c"), phew_site())
    libs, _ = cat.libraries()
    assert [(lib.name, lib.version) for lib in libs] == [("phew", "0.0.3")]
    plan = cat.resolve(["phew"])
    assert plan == [("phew", "0.0.3", "py", [("phew/__init__.py", RAW + "pimoroni/phew/HEAD/phew/__init__.py"),
                                             ("phew/server.py", RAW + "pimoroni/phew/HEAD/phew/server.py")])]
    dest = str(tmp_path / "lib")
    assert libraries.install(cat, LocalBackend(), plan, dest) == (2, 0)
    assert libraries.compare(cat, LocalBackend(), ["phew"], None, dest) == {"phew": "current"}
    (tmp_path / "lib" / "phew" / "server.py").write_text("edited on the board")
    assert libraries.compare(cat, LocalBackend(), ["phew"], None, dest) == {"phew": "differs"}
    assert libraries.remove(cat, LocalBackend(), ["phew"], None, dest) == 2 and not (tmp_path / "lib" / "phew").exists()


def test_dependencies_come_first_and_a_micropython_lib_one_is_explained(tmp_path):
    dep = {"urls": [["dep/__init__.py", "github:me/dep/dep/__init__.py"]], "deps": [], "version": "1.0"}
    pkg = dict(PKG, deps=[["github:me/dep", "v1.0"]])
    extra = {RAW + "me/dep/v1.0/package.json": json.dumps(dep).encode(),
             RAW + "me/dep/v1.0/dep/__init__.py": b"# dep\n"}
    cat = CL.make_catalog(CL.validate_library_source(PHEW), str(tmp_path), phew_site(pkg, extra))
    assert [p[0] for p in cat.resolve(["phew"])] == ["dep", "phew"]
    needs_lib = phew_site(dict(PKG, deps=[["aioble", "latest"]]))
    odd = CL.make_catalog(CL.validate_library_source(PHEW), str(tmp_path / "o"), needs_lib)
    with pytest.raises(libraries.LibraryError, match="micropython-lib"):
        odd.resolve(["phew"])


@pytest.mark.parametrize("pkg,why", [
    (dict(PKG, urls=[["../evil.py", "github:pimoroni/phew/x.py"]]), "Unsafe"),
    (dict(PKG, urls=[["a.py", "ftp://example.com/a.py"]]), "https or github"),
    (dict(PKG, urls=[["a.py"]]), "[destination, source]"),
    (dict(PKG, urls="text"), "lists"),
])
def test_unsafe_or_malformed_packages_are_refused(tmp_path, pkg, why):
    cat = CL.make_catalog(CL.validate_library_source(PHEW), str(tmp_path), phew_site(pkg))
    with pytest.raises(libraries.LibraryError, match=why):
        cat.resolve(["phew"])


def test_files_must_stay_on_github_or_the_packages_own_host(tmp_path):
    cat = CL.make_catalog(CL.validate_library_source(PHEW), str(tmp_path), phew_site())
    with pytest.raises(libraries.LibraryError, match="Refusing"):
        cat.fetch("https://evil.example/a.py")
    pages = {MAKER_URL: json.dumps({"urls": [["a.py", "a.py"]], "version": "1"}).encode(),
             "https://dl.maker.example/p/a.py": b"x"}
    own = CL.make_catalog(MAKER, str(tmp_path / "m"), site(pages))
    plan = own.resolve([own.name])
    assert plan[0][3] == [("a.py", "https://dl.maker.example/p/a.py")] and own.fetch(plan[0][3][0][1]) == b"x"
    elsewhere = site({MAKER_URL: b'{"urls": [["a.py", "a.py"]]}'}, final="https://evil.example/package.json")
    sneaky = CL.make_catalog(MAKER, str(tmp_path / "s"), elsewhere)
    with pytest.raises(libraries.LibraryError, match="redirected"):
        sneaky.libraries()


def test_the_checks_say_what_they_found(tmp_path):
    got = CL.check_source(PHEW, Settings(), str(tmp_path), phew_site())
    assert got.ok and got.message == "Pimoroni phew: phew 0.0.3: 2 files" and got.trust_host is None
    bad = CL.check_source(PHEW, Settings(), str(tmp_path / "b"), site({}))
    assert not bad.ok and "Cannot read" in bad.message
    def message(**src):
        base = {"name": "x", "kind": "bundle", "repo": "a/b", "prefix": "p"}
        return CL.check_source(dict(base, **src), Settings()).message
    assert "owner/name" in message(repo="nonsense") and "prefix" in message(prefix="bad prefix")
    ok = CL.check_source(MAKER, Settings(), str(tmp_path / "t"), site({MAKER_URL: json.dumps(PKG).encode()}))
    assert ok.trust_host == "dl.maker.example"            # not GitHub: the owner is asked before saving


def test_a_custom_bundle_stays_on_its_own_repo_and_reports_pypi_needs(tmp_path):
    from test_cpbundle import PREFIX, TAG, site as bundle_site
    repo = "adafruit/Adafruit_CircuitPython_Bundle"
    src = CL.validate_library_source({"name": "Mine", "kind": "bundle", "repo": repo, "prefix": PREFIX})
    cat = CL.make_catalog(src, str(tmp_path), bundle_site()[0])
    libs, _ = cat.libraries()
    assert cat.tag == TAG and len(libs) == 3 and cat.title == "Mine"
    with pytest.raises(libraries.LibraryError, match="Refusing"):
        cat._read("https://github.com/someone/else/releases/download/x/y.json", 10)
    cat.meta["adafruit_thing"]["external_dependencies"] = ["sparkfun_qwiic_i2c", "adafruit_bus_device"]
    cat.resolve(["adafruit_thing"], 10)
    assert cat.external == ["sparkfun_qwiic_i2c"]        # the bundle's own library was installed, the PyPI one was not
