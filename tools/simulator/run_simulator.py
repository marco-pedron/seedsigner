#!/usr/bin/env python3
"""Run BitPolito SeedSigner with development-only hardware adapters."""

import argparse
import multiprocessing
import os
import subprocess
from pathlib import Path
from queue import Empty
import sys
import tempfile
import time

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LOCAL_NATIVE_LIB_DIR = REPO_ROOT / ".simulator-runtime" / "lib"
if (LOCAL_NATIVE_LIB_DIR / "libzbar.so.0").is_file():
    native_path = str(LOCAL_NATIVE_LIB_DIR)
    current_path = os.environ.get("LD_LIBRARY_PATH", "")
    current_entries = [entry for entry in current_path.split(os.pathsep) if entry]
    if native_path not in current_entries:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
            [native_path] + current_entries
        )


def configure_tcl_tk_paths() -> None:
    """Point a portable Python runtime at its bundled Tcl/Tk libraries."""

    library_roots = [
        Path(sys.base_prefix) / "lib",
        REPO_ROOT / ".simulator-runtime" / "python-3.12.8" / "lib",
    ]
    for variable, directory, marker in (
        ("TCL_LIBRARY", "tcl8.6", "init.tcl"),
        ("TK_LIBRARY", "tk8.6", "tk.tcl"),
    ):
        configured = os.environ.get(variable)
        if configured and (Path(configured) / marker).is_file():
            continue
        for root in library_roots:
            candidate = root / directory
            if (candidate / marker).is_file():
                os.environ[variable] = str(candidate.resolve())
                break


configure_tcl_tk_paths()

from tools.simulator.adapters import SUPPORTED_DISPLAY_CONFIGS, image_from_payload
from tools.simulator.runtime import run_application


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the real SeedSigner application with desktop-only display, GPIO, "
            "camera and microSD adapters."
        )
    )
    display_group = parser.add_mutually_exclusive_group()
    display_group.add_argument(
        "--display-config",
        choices=SUPPORTED_DISPLAY_CONFIGS,
        help="Official SeedSigner display configuration (default: st7789_240x240).",
    )
    parser.add_argument("--locale", default="en")
    parser.add_argument("--scale", type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument(
        "--ui",
        choices=("auto", "web", "tk"),
        default="auto",
        help="Interactive UI: browser fallback, native Tk, or automatic selection.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Browser UI bind address."
    )
    parser.add_argument("--port", type=int, default=8765, help="Browser UI port.")
    parser.add_argument("--camera-image", type=Path)
    parser.add_argument(
        "--headless-smoke",
        action="store_true",
        help="Verify real application startup without opening a window.",
    )
    parser.add_argument("--smoke-timeout", type=float, default=12.0)
    parser.add_argument(
        "--smoke-flow",
        choices=("startup", "settings", "scan", "screensaver"),
        default="startup",
        help="Optionally verify startup, Settings navigation, or camera-to-zbar scanning.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Write the last headless smoke frame to this PNG.",
    )
    return parser.parse_args()


def resolve_display_config(args) -> str:
    return args.display_config or "st7789_240x240"


def physical_display_size(display_config: str) -> tuple[int, int]:
    dimensions = display_config.split("_", maxsplit=1)[1]
    width, height = dimensions.split("x", maxsplit=1)
    return int(width), int(height)


def validate_desktop_dependencies() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pyzbar.pyzbar import ZBarSymbol; print(ZBarSymbol.QRCODE)",
        ],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip().splitlines()
        detail = detail[-1] if detail else "zbar probe failed"
        raise SystemExit(
            "Missing native zbar shared library. On Debian/Ubuntu install "
            "libzbar0; for a local build, add its lib directory to "
            "LD_LIBRARY_PATH before starting the simulator. "
            f"Probe result: {detail}"
        )


def probe_tk_window():
    try:
        import tkinter
    except ImportError as exc:
        return str(exc)

    try:
        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return None
    except tkinter.TclError as exc:
        return str(exc)


def select_interactive_ui(requested: str) -> str:
    if requested == "web":
        return "web"

    if requested == "auto" and not os.environ.get("DISPLAY"):
        print(
            "No X11 DISPLAY detected; using the local browser UI "
            "(the SeedSigner framebuffer is unchanged)."
        )
        return "web"

    tk_error = probe_tk_window()
    if tk_error is None:
        return "tk"
    if requested == "auto":
        print(f"Tk is unavailable ({tk_error}); using the local browser UI.")
        return "web"
    raise SystemExit(
        "Tk cannot open an interactive window. Use --ui web, or install/configure "
        f"Tcl/Tk and X11/XWayland. Tk result: {tk_error}"
    )


def stop_process(process):
    if process.is_alive():
        process.terminate()
    process.join(timeout=3)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


# Key presses sent once the app is ready; each one must produce a response.
SMOKE_FLOW_KEYS = {
    "startup": (),
    "settings": ("KEY_DOWN", "KEY_RIGHT", "KEY_PRESS"),
    "scan": ("KEY_PRESS",),
    "screensaver": ("KEY_PRESS",),
}
# View the Controller must be running when the flow ends.
SMOKE_FLOW_FINAL_VIEW = {
    "startup": "MainMenuView",
    "settings": "SettingsMenuView",
}
SMOKE_KEY_RESPONSE_TIMEOUT = 3.0
SMOKE_SETTLE_SECONDS = 0.5
SMOKE_QUIET_SECONDS = 2.5


class SmokeAppState:
    """Tracks the status events published by the simulator child process."""

    def __init__(self):
        self.views: list[str] = []
        self.active_toasts: set[str] = set()
        self.screensaver_started = False

    def apply(self, kind: str, name: str):
        if kind == "view":
            self.views.append(name)
        elif kind == "toast-start":
            self.active_toasts.add(name)
        elif kind == "toast-end":
            self.active_toasts.discard(name)
        elif kind == "screensaver":
            self.screensaver_started = True

    @property
    def current_view(self) -> str | None:
        return self.views[-1] if self.views else None

    def is_ready_for(self, flow: str) -> bool:
        """True once real hardware input would reach the screen under test.

        A toast polls the buttons too, so a fast press sent while it is active
        is consumed by the toast instead of the main menu.
        """
        if self.current_view != "MainMenuView" or self.active_toasts:
            return False
        if flow == "screensaver":
            return self.screensaver_started
        return True


def run_headless_smoke(
    process,
    frame_queue,
    input_queue,
    error_queue,
    status_queue,
    timeout,
    screenshot,
    flow,
    expected_size,
):
    from tools.simulator.adapters import SimulatorButtonPins

    started_at = time.monotonic()
    deadline = started_at + timeout
    state = SmokeAppState()
    pending_keys = list(SMOKE_FLOW_KEYS[flow])
    last_payload = None
    frame_count = 0
    flow_frame_count = 0
    last_frame_at = None
    ready_at = None
    awaiting_key = None
    key_sent_at = None
    frames_at_key = 0
    views_at_key = 0
    required_flow_frames = 1 if flow == "screensaver" else 3

    while time.monotonic() < deadline:
        while True:
            try:
                state.apply(*status_queue.get_nowait())
            except Empty:
                break

        try:
            error = error_queue.get_nowait()
        except Empty:
            error = None
        if error:
            raise RuntimeError(f"Simulator application failed:\n{error}")

        now = time.monotonic()
        if awaiting_key is not None:
            if frame_count > frames_at_key or len(state.views) > views_at_key:
                awaiting_key = None
            elif now - key_sent_at >= SMOKE_KEY_RESPONSE_TIMEOUT:
                raise RuntimeError(
                    f"Simulator did not respond to {awaiting_key} during {flow} "
                    f"(current view: {state.current_view})"
                )

        if ready_at is None and pending_keys and state.is_ready_for(flow):
            ready_at = now
            # The timeout covers the flow itself, not the time spent booting.
            deadline = max(deadline, ready_at + timeout)

        # The screensaver animates continuously, so it never settles.
        settled = flow == "screensaver" or (
            last_frame_at is not None and now - last_frame_at >= SMOKE_SETTLE_SECONDS
        )
        if ready_at is not None and pending_keys and awaiting_key is None and settled:
            key_name = pending_keys.pop(0)
            pin = getattr(SimulatorButtonPins, key_name)
            # Deliberately enqueue an immediate LOW/HIGH pair. This verifies
            # the desktop GPIO latch, not a forgiving artificial key hold.
            input_queue.put((pin, 0))
            input_queue.put((pin, 1))
            awaiting_key = key_name
            key_sent_at = now
            frames_at_key = frame_count
            views_at_key = len(state.views)

        try:
            payload = frame_queue.get(timeout=0.1)
        except Empty:
            if process.exitcode not in (None, 0):
                raise RuntimeError(
                    f"Simulator process exited with code {process.exitcode}"
                )
            if flow == "startup":
                done = (
                    frame_count >= 2
                    and state.current_view == "MainMenuView"
                )
            else:
                done = (
                    ready_at is not None
                    and not pending_keys
                    and awaiting_key is None
                    and flow_frame_count >= required_flow_frames
                )
            if (
                done
                and last_frame_at is not None
                and time.monotonic() - last_frame_at >= SMOKE_QUIET_SECONDS
            ):
                break
            continue

        last_payload = payload
        frame_count += 1
        if ready_at is not None and key_sent_at is not None:
            flow_frame_count += 1
        last_frame_at = time.monotonic()

    if last_payload is None:
        raise RuntimeError("Simulator produced no display frames")
    if flow != "startup" and (pending_keys or awaiting_key is not None):
        raise RuntimeError(
            f"Simulator was not ready for the {flow} input before the timeout "
            f"(current view: {state.current_view}, "
            f"active toasts: {sorted(state.active_toasts)})"
        )
    if flow != "startup" and flow_frame_count < required_flow_frames:
        raise RuntimeError(
            f"Simulator did not render the expected navigation frames for {flow}"
        )
    expected_view = SMOKE_FLOW_FINAL_VIEW.get(flow)
    if expected_view and state.current_view != expected_view:
        raise RuntimeError(
            f"Simulator ended the {flow} flow on {state.current_view}, "
            f"expected {expected_view} (views: {' > '.join(state.views)})"
        )
    if flow == "scan" and (
        "ScanView" not in state.views or state.current_view == "ScanView"
    ):
        # The camera frame must be decoded and routed past the scanner.
        raise RuntimeError(
            f"Simulator did not decode the camera image "
            f"(views: {' > '.join(state.views)})"
        )

    frame = image_from_payload(last_payload)
    if frame.mode != "RGB" or frame.size != expected_size:
        raise RuntimeError(
            f"Unexpected simulator frame: mode={frame.mode}, size={frame.size}"
        )

    if screenshot:
        screenshot = screenshot.expanduser().resolve()
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        frame.save(screenshot)

    print(
        "SIMULATOR_SMOKE_OK "
        f"flow={flow} frames={frame_count} "
        f"size={frame.width}x{frame.height} mode={frame.mode} "
        f"view={state.current_view}"
    )


def main():
    args = parse_args()
    display_config = resolve_display_config(args)
    expected_display_size = physical_display_size(display_config)
    validate_desktop_dependencies()
    selected_ui = None
    if not args.headless_smoke:
        selected_ui = select_interactive_ui(args.ui)
    if args.camera_image:
        args.camera_image = args.camera_image.expanduser().resolve()
        if not args.camera_image.is_file():
            raise SystemExit(f"Camera image not found: {args.camera_image}")
    if args.smoke_flow == "scan" and not args.camera_image:
        raise SystemExit("The scan smoke flow requires --camera-image")
    if args.smoke_flow == "screensaver":
        args.smoke_timeout = max(args.smoke_timeout, 42.0)

    multiprocessing.freeze_support()
    context = multiprocessing.get_context("spawn")
    frame_queue = context.Queue(maxsize=3)
    input_queue = context.Queue()
    error_queue = context.Queue()
    status_queue = context.Queue() if args.headless_smoke else None

    with tempfile.TemporaryDirectory(prefix="bitpolito-simulator-") as runtime_dir:
        config = {
            "runtime_dir": runtime_dir,
            "display_config": display_config,
            "locale": args.locale,
            "camera_image": str(args.camera_image) if args.camera_image else None,
        }
        process = context.Process(
            target=run_application,
            args=(frame_queue, input_queue, error_queue, config, status_queue),
            name="BitPolitoSeedSigner",
        )
        process.start()

        try:
            if args.headless_smoke:
                run_headless_smoke(
                    process,
                    frame_queue,
                    input_queue,
                    error_queue,
                    status_queue,
                    args.smoke_timeout,
                    args.screenshot,
                    args.smoke_flow,
                    expected_display_size,
                )
            elif selected_ui == "tk":
                from tools.simulator.ui import SimulatorWindow

                SimulatorWindow(
                    frame_queue,
                    input_queue,
                    error_queue,
                    process,
                    scale=args.scale,
                ).run()
            else:
                from tools.simulator.web_ui import SimulatorWebServer

                SimulatorWebServer(
                    frame_queue,
                    input_queue,
                    error_queue,
                    process,
                    host=args.host,
                    port=args.port,
                ).run()
        finally:
            stop_process(process)


if __name__ == "__main__":
    main()
