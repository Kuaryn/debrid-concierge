import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path


def check_bundle(bundle: Path, warnings_dir: Path) -> None:
    targets = {"concierge", "pystray", "PIL", "Pillow", "tkinter", "_tkinter"}
    for role in ("handler", "tray", "worker"):
        exe = bundle / f"debrid-concierge-{role}.exe"
        if not exe.is_file():
            raise ValueError(f"missing executable: {exe.name}")
        report = (warnings_dir / f"warn-{role}.txt").read_text(encoding="utf-8")
        missing = re.findall(r"^(?:missing|excluded|invalid) module named (\S+) -", report,
                             re.MULTILINE)
        unresolved = [name for name in missing if name.strip("'\"").split(".")[0] in targets]
        if unresolved:
            raise ValueError(f"{role} unresolved bundle imports: {', '.join(unresolved)}")


def smoke_bundle(bundle: Path, scratch: Path) -> None:
    bundle = bundle.resolve()
    scratch = scratch.resolve()
    env = os.environ.copy()
    for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        env[key] = str(scratch)
    for key in ("DEBRID_CONCIERGE_TEST_KEY", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # I use early exits so these checks never reach config, dialogs, or cloud calls.
    probes = (
        ("handler", [str(scratch / "missing.torrent")], 2),
        ("worker", ["--help"], 0),
    )
    for role, args, expected in probes:
        result = subprocess.run(
            [str(bundle / f"debrid-concierge-{role}.exe"), *args],
            cwd=scratch, env=env, timeout=30, check=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != expected:
            raise ValueError(f"{role} exited {result.returncode}, expected {expected}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=Path("dist/debrid-concierge"))
    parser.add_argument("--warnings-dir", type=Path, default=Path("build/debrid-concierge"))
    args = parser.parse_args(argv)
    check_bundle(args.bundle, args.warnings_dir)
    with tempfile.TemporaryDirectory(prefix="concierge-smoke-") as scratch:
        smoke_bundle(args.bundle, Path(scratch))
    print("bundle checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
