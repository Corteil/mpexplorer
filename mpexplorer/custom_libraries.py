"""Custom library sources: a catalog for each (bundle repo or `package.json` repo) and the Check before one is saved."""
import re

from . import cpbundle, mip_source, sources
from .libraries import LibraryError


def validate_library_source(src):
    """The source cleaned up, or SourceError saying what is wrong with it."""
    name, kind = (src.get("name") or "").strip(), src.get("kind")
    if not name or len(name) > sources.MAX_NAME:
        raise sources.SourceError("Give the source a name of up to %d characters." % sources.MAX_NAME)
    firmware = src.get("for") or ("circuitpython" if kind == "bundle" else "micropython")
    if firmware not in ("micropython", "circuitpython"):
        raise sources.SourceError("The libraries are for micropython or circuitpython.")
    out = {"name": name, "kind": kind, "for": firmware}
    if kind == "bundle":
        repo = (src.get("repo") or "").strip().removeprefix("https://github.com/").strip("/")
        prefix = (src.get("prefix") or "").strip()
        if not sources.valid_repo(repo):
            raise sources.SourceError("A GitHub repo looks like owner/name, for example sparkfun/qwiic_py.")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", prefix):
            raise sources.SourceError("The prefix is the start of the release file names, e.g. qwiic-py for "
                                      "qwiic-py-10.x-mpy-20260227.zip.")
        out.update(repo=repo, prefix=prefix)
    elif kind == "mip":
        repo = (src.get("repo") or "").strip().removeprefix("https://github.com/").strip("/")
        url = (src.get("url") or "").strip()
        if url:
            if not url.startswith("https://") or not sources.host_of(url):
                raise sources.SourceError("The address must start with https://")
            out["url"] = url
        elif sources.valid_repo(repo.partition("@")[0]):
            out["repo"] = repo
        else:
            raise sources.SourceError("A GitHub repo looks like owner/name (add @branch for a branch), for example "
                                      "pimoroni/phew; or give the address of a package.json.")
    else:
        raise sources.SourceError("Unknown kind of library source: %r" % kind)
    return out


def make_catalog(src, cache_dir=None, opener=None):
    kw = {"opener": opener} if opener else {}
    if src["kind"] == "bundle":
        return cpbundle.Catalog(src, cache_dir, **kw)
    return mip_source.MipRepo(src, cache_dir, **kw)


def check_source(source, settings, cache_dir=None, opener=None):
    """The Check every new library source passes before it is saved. sources.Check(ok, message, trust_host)."""
    try:
        src = validate_library_source(source)
        catalog = make_catalog(src, cache_dir, opener)
        host = sources.host_of(src.get("url") or "https://github.com/")
        ask = host if not sources.is_trusted(settings, host) else None
        libs, _saved = catalog.libraries()
        if not libs:
            raise LibraryError("Found no libraries in %s." % src["name"])
        if src["kind"] == "bundle":
            summary = "%d libraries, newest release %s" % (len(libs), catalog.tag)
        else:
            files = len(catalog.resolve([catalog.name])[-1][3])
            summary = "%s %s: %d file%s" % (catalog.name, libs[0].version, files, "" if files == 1 else "s")
        return sources.Check(True, "%s: %s" % (src["name"], summary), ask)
    except (sources.SourceError, LibraryError) as e:
        return sources.Check(False, str(e), None)
