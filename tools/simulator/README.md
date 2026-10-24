# BitPolito SeedSigner simulator

The simulator runs the real SeedSigner application with in-memory desktop
adapters for display, buttons, camera and microSD. It is development-only and
is never copied into the SeedSigner OS image.

## Prerequisites

- Python 3.10-3.12;
- repository submodules;
- a working virtual environment;
- native zbar for QR decoding;
- a desktop display for interactive mode, or headless mode in CI.

On Debian/Ubuntu install zbar with `sudo apt install libzbar0`; on Fedora use
`sudo dnf install zbar`.

## Setup

From the repository root:

~~~bash
./scripts/setup-dev.sh
~~~

The script installs application, test and simulator dependencies and compiles
translations.

## Run

Interactive mode:

~~~bash
.venv-simulator/bin/python tools/simulator/run_simulator.py
~~~

If a native window is unavailable, use the browser UI explicitly:

~~~bash
.venv-simulator/bin/python tools/simulator/run_simulator.py --ui web
~~~

Headless smoke test:

~~~bash
.venv-simulator/bin/python tools/simulator/run_simulator.py --headless-smoke
~~~

Choose an official display configuration:

~~~bash
.venv-simulator/bin/python tools/simulator/run_simulator.py --display-config st7789_320x240
.venv-simulator/bin/python tools/simulator/run_simulator.py --display-config ili9341_320x240
~~~

Boot from the `settings.json` you will copy to the microSD card. The app reads
a temporary copy exactly as SeedSigner OS reads `/mnt/microsd/settings.json`,
so its `display_config`, `color_inverted` and `locale` apply without
overrides; the source file is never modified. `--locale` still overrides the
language, and `--display-config` cannot be combined with this option:

~~~bash
.venv-simulator/bin/python tools/simulator/run_simulator.py \
  --headless-smoke --smoke-flow settings \
  --settings-json /Volumes/<card>/settings.json
~~~

Use a deterministic camera fixture:

~~~bash
.venv-simulator/bin/python tools/simulator/run_simulator.py \
  --headless-smoke --smoke-flow scan --smoke-timeout 20 \
  --camera-image tests/fixtures/bitpolito-settings-qr.png
~~~

The default configuration is ST7789 240x240. The simulator validates the
rendered RGB framebuffer and keeps settings in a temporary directory.

Headless smoke flows wait until the app is idle on the main menu, with no toast
on screen, before pressing any button: a toast polls the buttons too and would
swallow a fast press. Each press must produce a new frame or View, and the
`settings` flow must end on `SettingsMenuView`, so a slow machine fails loudly
instead of navigating somewhere else.

## Safety

A desktop is not air-gapped and does not reproduce Raspberry Pi hardware
security. Never enter real seeds, private keys or funds. Use test data only.
