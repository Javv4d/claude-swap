"""``cswap panel`` — the cswap dashboard as a macOS menu bar drop-down.

A status item ("CS") in the menu bar; clicking it drops down a popover with
the live TUI in an embedded terminal — the same interaction as the system
Displays or Wi-Fi items. No Dock icon, no Terminal window.

The panel is a small Swift app (``panel/`` next to this module, shipped in
the wheel as source). It is built on demand with the Swift toolchain that
comes with the Xcode Command Line Tools, into the backup root — never into
site-packages — and rebuilt only when its sources or the cswap version
change. The build fetches SwiftTerm from GitHub the first time, so the first
``cswap panel --install-service`` needs network and about a minute.

Everything here is macOS-only; the CLI guards on ``sys.platform`` and the
public functions refuse elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from claude_swap import __version__, launch_agent
from claude_swap.exceptions import ClaudeSwitchError

LABEL = "com.cswap.panel"
APP_NAME = "CswapPanel"
BUNDLE_ID = "com.cswap.panel"

# Written once the first-run offer has been shown, whatever the answer, so a
# declined offer never nags again. Sibling of settings.json in the backup root.
OFFER_MARKER = ".panel-offered"

_SOURCE_DIR = Path(__file__).parent / "panel"
_SOURCE_FILES = ("Package.swift", "Sources/CswapPanel/main.swift")


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise ClaudeSwitchError("The menu bar panel is only available on macOS.")


# -- locations ---------------------------------------------------------------


def panel_dir(backup_root: Path) -> Path:
    """Everything the panel builds or stores lives under here."""
    return backup_root / "panel"


def app_bundle(backup_root: Path) -> Path:
    return panel_dir(backup_root) / f"{APP_NAME}.app"


def binary_path(backup_root: Path) -> Path:
    return app_bundle(backup_root) / "Contents" / "MacOS" / APP_NAME


def _stamp_path(backup_root: Path) -> Path:
    return panel_dir(backup_root) / "build-stamp.json"


# -- toolchain ---------------------------------------------------------------


def toolchain_missing(run=subprocess.run) -> str | None:
    """Why a build cannot happen, or None when ``swift`` is usable.

    ``/usr/bin/swift`` exists on every Mac as a shim that only errors until
    the Command Line Tools are installed, so ``which`` alone proves nothing;
    ``xcode-select -p`` is the check Apple's own tooling uses.
    """
    if shutil.which("swift") is None:
        return "swift not found"
    try:
        probe = run(["xcode-select", "-p"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "xcode-select not found"
    if probe.returncode != 0:
        return "no developer tools selected"
    return None


_TOOLCHAIN_HINT = (
    "The menu bar panel is built with the Swift toolchain from the Xcode "
    "Command Line Tools.\n  Install them with: xcode-select --install\n"
    "  then re-run this command."
)


# -- build -------------------------------------------------------------------


def source_fingerprint(source_dir: Path = _SOURCE_DIR) -> str:
    """Hash of the Swift sources plus the cswap version — the rebuild key."""
    h = hashlib.sha256()
    h.update(__version__.encode())
    for rel in _SOURCE_FILES:
        h.update(rel.encode())
        h.update((source_dir / rel).read_bytes())
    return h.hexdigest()


def is_built(backup_root: Path, source_dir: Path = _SOURCE_DIR) -> bool:
    """Whether a binary from the current sources is already in place."""
    if not binary_path(backup_root).is_file():
        return False
    try:
        stamp = json.loads(_stamp_path(backup_root).read_text())
    except (OSError, ValueError):
        return False
    return stamp.get("fingerprint") == source_fingerprint(source_dir)


def _info_plist() -> bytes:
    return plistlib.dumps(
        {
            "CFBundleName": "cswap",
            "CFBundleDisplayName": "cswap panel",
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": APP_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": __version__,
            "CFBundleShortVersionString": __version__,
            # Menu bar only: no Dock icon, no app switcher entry.
            "LSUIElement": True,
            "LSMinimumSystemVersion": "13.0",
        }
    )


def build(
    backup_root: Path,
    source_dir: Path = _SOURCE_DIR,
    run=subprocess.run,
    log=None,
) -> Path:
    """Compile the panel and assemble its ``.app`` bundle. Returns the binary.

    Sources are copied into the backup root first so SwiftPM's
    ``Package.resolved`` and build tree never land in site-packages. The
    bundle is ad-hoc signed: unsigned binaries are refused a status item on
    recent macOS releases.
    """
    _require_macos()
    missing = toolchain_missing(run)
    if missing:
        raise ClaudeSwitchError(f"{_TOOLCHAIN_HINT}\n  ({missing})")

    work = panel_dir(backup_root)
    src = work / "src"
    scratch = work / ".build"
    work.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.rmtree(src)
    shutil.copytree(source_dir, src)

    if log:
        log("Building the menu bar panel (first build fetches SwiftTerm; ~1 min)…")
    built = run(
        [
            "swift", "build", "-c", "release",
            "--package-path", str(src),
            "--scratch-path", str(scratch),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        detail = (built.stderr or built.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-15:])
        raise ClaudeSwitchError(
            f"swift build failed (exit {built.returncode})" + (f":\n{tail}" if tail else "")
        )
    compiled = scratch / "release" / APP_NAME
    if not compiled.is_file():
        raise ClaudeSwitchError(f"swift build produced no binary at {compiled}")

    bundle = app_bundle(backup_root)
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    shutil.copy2(compiled, binary_path(backup_root))
    (bundle / "Contents" / "Info.plist").write_bytes(_info_plist())

    signed = run(
        ["codesign", "--sign", "-", "--force", str(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    if signed.returncode != 0:
        detail = (signed.stderr or signed.stdout or "").strip()
        raise ClaudeSwitchError(
            f"codesign failed (exit {signed.returncode})" + (f": {detail}" if detail else "")
        )

    _stamp_path(backup_root).write_text(
        json.dumps({"fingerprint": source_fingerprint(source_dir), "version": __version__})
    )
    return binary_path(backup_root)


def ensure_built(backup_root: Path, run=subprocess.run, log=None) -> Path:
    """Build only when the binary is missing or stale."""
    if is_built(backup_root):
        return binary_path(backup_root)
    return build(backup_root, run=run, log=log)


# -- launching ---------------------------------------------------------------


def environment(backup_root: Path, program: list[str] | None = None) -> dict[str, str]:
    """What the panel binary needs to find cswap and its data."""
    program = program or launch_agent.resolve_program()
    return {
        "CSWAP_PANEL_PROGRAM": program[0],
        "CSWAP_PANEL_DATA_DIR": str(backup_root),
    }


def run_foreground(backup_root: Path, run=subprocess.run, log=None) -> int:
    """``cswap panel``: build if needed, then run the app until it quits."""
    _require_macos()
    binary = ensure_built(backup_root, run=run, log=log)
    env = {**os.environ, **environment(backup_root)}
    proc = run([str(binary)], env=env, check=False)
    return proc.returncode


def install_service(backup_root: Path, run=subprocess.run, log=None, **kw) -> dict:
    """Build if needed and hand the panel to launchd (starts at login)."""
    _require_macos()
    binary = ensure_built(backup_root, run=run, log=log)
    return launch_agent.install(
        label=LABEL,
        program=[str(binary)],
        arguments=(),
        environment=environment(backup_root),
        **kw,
    )


def uninstall_service(**kw) -> dict:
    _require_macos()
    return launch_agent.uninstall(label=LABEL, **kw)


def service_status(**kw) -> dict:
    _require_macos()
    return launch_agent.status(label=LABEL, **kw)


# -- first-run offer ----------------------------------------------------------


def offer_pending(backup_root: Path, home: Path | None = None) -> bool:
    """Should ``cswap add`` ask about the menu bar right now?

    Only once (the marker), only on macOS, and never when the service is
    already installed by hand.
    """
    if sys.platform != "darwin":
        return False
    if (backup_root / OFFER_MARKER).exists():
        return False
    return not launch_agent.plist_path(LABEL, home).exists()


def mark_offered(backup_root: Path) -> None:
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        (backup_root / OFFER_MARKER).write_text("")
    except OSError:
        pass


def offer(
    backup_root: Path,
    *,
    interactive: bool | None = None,
    ask=input,
    out=print,
    run=subprocess.run,
) -> bool:
    """After a successful ``cswap add``: one-time "put it in the menu bar?".

    Returns True when the service was installed. Quiet when stdin/stdout are
    not a terminal (scripts, CI) so the prompt can never hang a pipeline; a
    non-interactive run leaves the offer pending for a later interactive one.
    Any failure is reported, never raised — the account was already added.
    """
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive or not offer_pending(backup_root):
        return False
    mark_offered(backup_root)
    out("")
    out("cswap can live in your menu bar: click \"CS\" for the dashboard, no terminal needed.")
    try:
        answer = ask("Show the dashboard in the menu bar? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        out("")
        return False
    if answer not in ("y", "yes"):
        out("Later: cswap panel --install-service")
        return False
    try:
        result = install_service(backup_root, run=run, log=out)
    except ClaudeSwitchError as e:
        out(f"Could not set up the menu bar panel: {e}")
        out("Retry later with: cswap panel --install-service")
        return False
    out(f"Menu bar panel installed ({result['label']}). Look for \"CS\" in your menu bar.")
    return True
