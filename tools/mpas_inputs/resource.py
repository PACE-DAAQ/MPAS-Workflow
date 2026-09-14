"""Resolve original data resources with local/HPC-first and HTTPS fallback."""
from __future__ import annotations
from pathlib import Path
import os, re, shutil, subprocess, tempfile


def expand(s: str, **kw) -> str:
    expanded = os.path.expanduser(os.path.expandvars(str(s)))
    if re.search(r'\$(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*)', expanded):
        raise FileNotFoundError(f"resource candidate contains an unset environment variable: {s}")
    return expanded.format(**kw)


def obtain(*, local_candidates, url_candidates=(), cache_dir, output_name, template_vars=None):
    """Return a local source file, preferring original HPC holdings.

    Candidates are tried in order.  If no local candidate exists, HTTPS URLs are
    downloaded to ``cache_dir`` using curl with normal ~/.netrc authentication.
    """
    kw = dict(template_vars or {})
    for item in local_candidates or []:
        try:
            p = Path(expand(item, **kw))
        except FileNotFoundError:
            continue
        if p.is_file():
            return p, {"access": "local", "source": str(p)}
    cache = Path(cache_dir); cache.mkdir(parents=True, exist_ok=True)
    dst = cache / output_name
    if dst.exists() and dst.stat().st_size > 0:
        return dst, {"access": "cache", "source": str(dst)}
    for item in url_candidates or []:
        try:
            url = expand(item, **kw)
        except FileNotFoundError:
            continue
        # Jobs may share a monthly OVP cache. Each download owns its temporary
        # file; readers see only a complete artifact after atomic replacement.
        fd, name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".part", dir=cache)
        os.close(fd)
        tmp = Path(name)
        cmd = ["curl", "-fL", "--retry", "3", "--retry-delay", "3", "--netrc", "-o", str(tmp), url]
        try:
            subprocess.run(cmd, check=True)
            if tmp.stat().st_size == 0:
                continue
            tmp.replace(dst)
            return dst, {"access": "https", "source": url, "cached_as": str(dst)}
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        finally:
            tmp.unlink(missing_ok=True)
    raise FileNotFoundError(f"resource unavailable: local={local_candidates}, urls={url_candidates}")


def link_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink(): dst.unlink()
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        shutil.copy2(src, dst)
