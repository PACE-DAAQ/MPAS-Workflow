"""Generate conservative regular-grid -> MPAS ESMF weights."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def generate_conservative_weights(src_gridspec, dst_scrip, out_file, *, ignore_unmapped=True, dst_regional=False):
    """Generate ESMF conservative weights using ESMPy or ESMF_RegridWeightGen.

    The weight file is a reusable mesh/grid cache product and should normally be
    generated only once per source-grid fingerprint + MPAS-mesh fingerprint.
    """
    src_gridspec = Path(src_gridspec)
    dst_scrip = Path(dst_scrip)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        import esmpy as ESMF  # type: ignore
    except ImportError:
        try:
            import ESMF  # type: ignore
        except ImportError:
            ESMF = None

    if ESMF is not None:
        src = ESMF.Grid(filename=str(src_gridspec), filetype=ESMF.FileFormat.GRIDSPEC, add_corner_stagger=True)
        dst = ESMF.Mesh(filename=str(dst_scrip), filetype=ESMF.FileFormat.SCRIP)
        srcf = ESMF.Field(src, name="srcfield", staggerloc=ESMF.StaggerLoc.CENTER)
        dstf = ESMF.Field(dst, name="dstfield", meshloc=ESMF.MeshLoc.ELEMENT)
        kw = dict(filename=str(out_file), regrid_method=ESMF.RegridMethod.CONSERVE)
        if ignore_unmapped:
            kw["unmapped_action"] = ESMF.UnmappedAction.IGNORE
        regrid = ESMF.Regrid(srcf, dstf, **kw)
        try:
            regrid.destroy()
        except Exception:
            pass
        return out_file

    exe = shutil.which("ESMF_RegridWeightGen")
    if exe:
        cmd = [
            exe,
            "-s", str(src_gridspec),
            "-d", str(dst_scrip),
            "-w", str(out_file),
            "-m", "conserve",
            "--src_type", "GRIDSPEC",
            "--dst_type", "SCRIP",
        ]
        if ignore_unmapped:
            cmd.append("--ignore_unmapped")
        if dst_regional:
            cmd.append("--dst_regional")
        subprocess.run(cmd, check=True)
        return out_file

    raise RuntimeError(
        "Cannot generate ESMF weights: neither ESMPy/ESMF nor ESMF_RegridWeightGen is available"
    )
