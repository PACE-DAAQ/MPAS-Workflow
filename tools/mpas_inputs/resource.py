"""Resolve original data resources with local/HPC-first and HTTPS fallback."""
from __future__ import annotations
from pathlib import Path
import os, shutil, subprocess


def expand(s: str, **kw) -> str:
    return os.path.expanduser(os.path.expandvars(str(s))).format(**kw)


def obtain(*, local_candidates, url_candidates=(), cache_dir, output_name, template_vars=None):
    """Return a local source file, preferring original HPC holdings.

    Candidates are tried in order.  If no local candidate exists, HTTPS URLs are
    downloaded to ``cache_dir`` using curl with normal ~/.netrc authentication.
    """
    kw = dict(template_vars or {})
    for item in local_candidates or []:
        p = Path(expand(item, **kw))
        if p.exists():
            return p, {"access": "local", "source": str(p)}
    cache = Path(cache_dir); cache.mkdir(parents=True, exist_ok=True)
    dst = cache / output_name
    if dst.exists() and dst.stat().st_size > 0:
        return dst, {"access": "cache", "source": str(dst)}
    for item in url_candidates or []:
        url = expand(item, **kw)
        tmp = dst.with_suffix(dst.suffix + ".part")
        cmd = ["curl", "-fL", "--retry", "3", "--retry-delay", "3", "--netrc", "-o", str(tmp), url]
        try:
            subprocess.run(cmd, check=True)
            tmp.replace(dst)
            return dst, {"access": "https", "source": url, "cached_as": str(dst)}
        except (subprocess.CalledProcessError, FileNotFoundError):
            if tmp.exists(): tmp.unlink()
    raise FileNotFoundError(f"resource unavailable: local={local_candidates}, urls={url_candidates}")


def link_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink(): dst.unlink()
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        shutil.copy2(src, dst)
