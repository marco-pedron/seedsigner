# ShieldSigner: 320x240 display setup

The ShieldSigner runs the unmodified BitPolito SeedSigner firmware. Its larger
screen is selected at runtime by a `settings.json` file on the microSD card;
the firmware default stays `st7789_240x240` and no display driver, GPIO or
settings code is changed for this hardware.

## Hardware

| Part | ShieldSigner |
| --- | --- |
| Board | Raspberry Pi Zero 1.3 (`pi0` image) |
| Display | ST7789 2.8" 240x320 panel used in landscape (`st7789_320x240`) |
| Input | 5-way joystick plus KEY1-KEY3 |
| Camera | Raspberry Pi camera (needed only for the SettingsQR fallback) |

Buttons use the Waveshare 1.3" HAT pinout already defined in
`src/seedsigner/hardware/buttons.py` (physical BOARD numbering):

| Input | Pin | Input | Pin |
| --- | --- | --- | --- |
| UP | 31 | KEY1 | 40 |
| DOWN | 35 | KEY2 | 38 |
| LEFT | 29 | KEY3 | 36 |
| RIGHT | 37 | | |
| PRESS | 33 | | |

## Firmware version

`display_config` exists since SeedSigner **0.8.6**. Older images, including
0.8.5 and the first BitPolito image built on 0.8.0, ignore the file and keep
drawing a 240x240 picture. Use the official 0.8.7 `pi0` image or a
0.8.7-bitpolito build.

## settings.json

SeedSigner OS has a single FAT32 partition, boots fully into RAM and mounts the
card at `/mnt/microsd`; the app reads `/mnt/microsd/settings.json` at startup
(`src/seedsigner/models/settings.py`). After flashing, create this file in the
root of the card:

~~~json
{"persistent_settings": "E", "display_config": "st7789_320x240"}
~~~

Ready-made copies live in `docs/bitpolito-shieldsigner/`:

| File | Use it when |
| --- | --- |
| `settings.json` | default ShieldSigner configuration |
| `settings-inverted.json` | colours look inverted (adds `"color_inverted": "E"`) |
| `settings-ili9341.json` | the screen stays black or shows noise with the ST7789 driver |

Settings missing from the file keep their defaults. `persistent_settings`
must stay `"E"`: with persistence disabled SeedSigner deletes the file.

## Test before flashing

The simulator runs the real application with desktop adapters, so the layout,
translations and settings parsing can be checked without touching the card.
Set up the environment once with `./scripts/setup-dev.sh` (on macOS,
`brew install python@3.12 zbar fribidi`; keep the virtual environment outside
folders synchronised by iCloud, for example with
`BITPOLITO_VENV=~/.venvs/seedsigner`). `python` below is that environment's
interpreter.

1. Boot from the exact file you will copy to the card. The app reads it as it
   reads `/mnt/microsd/settings.json`; nothing forces the display size:

   ~~~bash
   python tools/simulator/run_simulator.py --headless-smoke \
     --settings-json docs/bitpolito-shieldsigner/settings.json \
     --screenshot artifacts/shieldsigner/startup.png
   python tools/simulator/run_simulator.py --headless-smoke --smoke-flow settings \
     --settings-json docs/bitpolito-shieldsigner/settings.json \
     --screenshot artifacts/shieldsigner/settings.png
   ~~~

   Both runs must print `SIMULATOR_SMOKE_OK ... size=320x240`, and the
   screenshots must fill the whole 320x240 frame.

2. Navigate by hand with the same file:

   ~~~bash
   python tools/simulator/run_simulator.py --ui web \
     --settings-json docs/bitpolito-shieldsigner/settings.json
   ~~~

3. Rehearse the SettingsQR fallback: start as a 240x240 device and press Scan;
   the simulated camera shows the ShieldSigner SettingsQR, and after the
   "Settings QR" confirmation the home screen fills 320x240:

   ~~~bash
   python tools/simulator/run_simulator.py --ui web \
     --display-config st7789_240x240 \
     --camera-image docs/bitpolito-shieldsigner/settingsqr-st7789_320x240.png
   ~~~

The simulator does **not** test the SPI driver and panel initialisation, the
physical rotation, colour inversion (it is electrical; the simulator only
records it), backlight, GPIO wiring or the real camera. Those are checked on
the device with the table below.

## Flashing a single microSD card (macOS)

1. Optional backup of the current card, to restore the old setup later.
   SeedSigner keeps no seeds on the card. Replace `N` with the card's disk
   number from `diskutil list` and double-check it:

   ~~~bash
   diskutil list
   diskutil unmountDisk /dev/diskN
   sudo dd if=/dev/rdiskN of=~/seedsigner-sd-backup.img bs=4m status=progress
   ~~~

2. Verify the image checksum and signature as described in the release notes.
3. Flash the image with balenaEtcher or Raspberry Pi Imager (custom image).
4. Re-insert the card and create `settings.json` in its root, for example
   `cp docs/bitpolito-shieldsigner/settings.json /Volumes/<card>/`. Do not
   write it with TextEdit, which can insert typographic quotes. Check it with
   `python3 -m json.tool /Volumes/<card>/settings.json`, then eject the card.
5. Boot the ShieldSigner.

| Symptom | Fix |
| --- | --- |
| Full-screen landscape UI | Done; check every button and KEY1-KEY3 |
| 240x240 picture in a corner | File not read: wrong name or location, invalid JSON, or firmware older than 0.8.6 |
| Wrong or inverted colours | Use `settings-inverted.json` |
| Black screen or noise | Use `settings-ili9341.json` |
| Card not reachable | Scan a SettingsQR from `docs/bitpolito-shieldsigner/` |

To go back, restore the backup with
`sudo dd if=~/seedsigner-sd-backup.img of=/dev/rdiskN bs=4m` or flash the
previous image again.

## SettingsQR fallback

With the camera connected, the display can be changed without removing the
card. Scanning one of these SettingsQR codes applies the display immediately
and, with the card inserted, stores it in `settings.json`:

| File | Content |
| --- | --- |
| `settingsqr-st7789_320x240.png` | `settings::v1 name=ShieldSigner persistent=E disp_conf=st7789_320x240` |
| `settingsqr-st7789_320x240-inverted.png` | `settings::v1 name=ShieldSigner_inverted persistent=E disp_conf=st7789_320x240 rgb_inv=E` |
| `settingsqr-ili9341_320x240.png` | `settings::v1 name=ShieldSigner_ili9341 persistent=E disp_conf=ili9341_320x240` |

A SettingsQR replaces every setting it does not list with its default value.
The images keep a wide white border so they also decode after the camera
crops the frame; print them or show them full screen on another device.
