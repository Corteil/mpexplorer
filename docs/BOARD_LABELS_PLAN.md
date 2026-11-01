# Firmware markers (emoji) and board nicknames plan

Status (2026-11-01): plan only, nothing built. Asked for by the owner: the port list does not make clear which firmware a board
runs; add a marker per firmware type (the snake and the "inverted M") and a nickname for each unique board.

## What there is today
- The port list (`ports.list_ports` / `port_label`) is `"/dev/ttyACM4  Pico 2 (rp2350a)  #8a64"`: port, a model name (USB guess, or what the board
  said at its last connect: `boards.names`), and the end of the USB serial. Nothing says MicroPython or CircuitPython.
- The pane's source box is `Gtk.ComboBoxText.new_with_entry()` (`gtkui/pane.py:28`): text only. `device_of()` takes the port from the text before the
  first double space, so the text format matters.
- After connecting, the pane label says `CircuitPython 10.3.1` for CircuitPython only (`Connection.label`); a MicroPython board says nothing.
- Settings already keep per-board facts keyed by the board's USB id (`vid:pid:serial`) or, after connecting, its chip id (`chip:<hex>` from
  `machine.unique_id()`): `boards.names`, `boards.rts`, `folders.boards`. `remember.SHARED_USB_SERIALS` lists serials that are not unique
  (the 2022 badges all read `123456`).

## Facts checked on the bench (2026-11-01)
| Port | USB id | manufacturer / product | serial | Runs |
|---|---|---|---|---|
| ttyACM2, 3, 5 | `2e8a:0005` | `MicroPython` / `Board in FS mode` | `15ed...`, `e661...`, **`000000000000`** | MicroPython |
| ttyACM4 | `2e8a:000b` | `Raspberry Pi` / `Pico 2` | `559C...8A64` (= the chip UID) | CircuitPython 10.3.1 |
| Pico W under CircuitPython (seen earlier) | **`239a:8120`** (Adafruit's vendor id) | `Raspberry Pi` / `Pico W` | chip UID | CircuitPython |
| ttyACM0, 1 | `16d0:120e` | `Electromagnetic Field` / `TiLDAGON` | `64e8...`, `7c4f...` | MicroPython (badge firmware) |
- So the USB data alone separates the two families on these boards: the MicroPython builds report manufacturer `MicroPython` / product `Board in FS mode`
  (the same on every board), CircuitPython reports the board's own name. It is a good guess, not a proof: a custom MicroPython build can change the strings.
- **A board with serial `000000000000` is not uniquely identified by USB.** Only the chip id, read after connecting, tells two of them apart.
- **Emoji (checked with `fc-list ":charset=..."`):** 🐍 (U+1F40D) has **no font on this Pi**, so it would show as an empty box. The Debian package
  `fonts-noto-color-emoji` (2.042, in apt) provides it; installing it is a system change that needs the owner's yes and `sudo`. The inverted M exists as a plain
  letter, **Ɯ (U+019C, LATIN CAPITAL LETTER TURNED M)**, and it renders already (Noto Sans Display). There is no emoji for MicroPython; the circled M `Ⓜ` (U+24C2)
  also renders (Noto Sans Symbols) but is not inverted.
- MicroPython's own logo (micropython/logo) is itself a snake on a chip, and CircuitPython's mascot (Blinka) is a snake too: see the question below.

## Decisions needed from the owner
1. **Which marker is which?** "The snake" and "the inverted M". Both firmwares have snake mascots, so this is not obvious. Plan default, to be confirmed:
   **🐍 = CircuitPython, Ɯ (turned M) = MicroPython.**
2. **Install the emoji font on this Pi?** `sudo apt install fonts-noto-color-emoji` (about 10 MB). Without it 🐍 is an empty box here. The owner chose emoji
   (2026-11-01); this is the one system change the plan needs. Other computers need an emoji font too (most desktops have one); the app falls back (see B).
3. **Nickname editing:** Device menu item `Name this board...` plus a pencil button next to the port box (plan default), or only the menu?
4. Boards of unknown firmware (a custom build, a board never connected to): no marker, or a dot? (Plan default: the dot `·`.)

## Design
### A. Firmware kind of a board (GUI-free, new `mpexplorer/boardinfo.py`)
`firmware_kind(usb_info, learned)` returns `"micropython"`, `"circuitpython"` or `None`.
1. **Learned wins:** `boards.firmware[<usb key>]` is written when the app connects (`circuitpython.detect` already returns the name) and by the firmware tools
   after an install (`Installed`) or when they read a running board. A reinstall of the other firmware therefore corrects the marker at once.
2. **Otherwise guess from USB:** manufacturer `MicroPython` -> MicroPython; vendor `239a` -> CircuitPython; vendor `2e8a` with any other product
   (a non-MicroPython RP2 USB string) -> CircuitPython; the EMF badge ids (`16d0:120e`, `16d0:1114`, `303a:4001`) -> MicroPython; everything else `None`.
3. The guess is shown as the marker without any hint; nothing says "guessed". A wrong guess is fixed by one connect.
`ports.list_ports` and the firmware tools use this one function.

### B. Markers (the owner chose emoji, 2026-11-01)
- A **text prefix**, so the source box stays a `ComboBoxText` and nothing about `device_of()`, `Pane.source()` or the tests' use of the box changes:
  `🐍 /dev/ttyACM4  Pico 2 (rp2350a)  #8a64` and `Ɯ /dev/ttyACM2  Pico 2 (RP2350)  #1e0a`. An unknown board gets a plain dot `·`. `device_of()` must skip the marker:
  it takes the first token that starts with `/dev/` or `COM` (a marker is one character plus a space, never a path).
- **Emoji font check, once at start** (`boardinfo.emoji_ok()`): ask Pango which font would draw U+1F40D (`Pango.Context.load_fontset(...).get_font(0x1F40D)`;
  a family with "Emoji" in its name means yes). If not, the snake falls back to the letters **CP** and the turned M to **MP** in the same place, so the list is
  still clear without any font. Ɯ needs no emoji font.
- The pane's frame label (`Device: /dev/ttyACM4  Pico 2 (rp2350a)  CircuitPython 10.3.1`) starts with the marker too, and always names the firmware:
  `MicroPython 1.29.0` as well as `CircuitPython 10.3.1`. Console window titles and the Window menu get the marker as text. The firmware tools' device list gets the
  marker on running boards ("running CircuitPython" / "running MicroPython" is already there in the details).
- **Fallback if emoji turn out ugly or blurry** (colour emoji can look odd in a small dropdown on a light theme): the earlier design, two small drawn images in a
  `Gtk.ComboBox` with a pixbuf cell, is kept as plan B and costs one extra phase.

### C. Nicknames
- Setting `boards.nicknames`: board key -> text (at most 24 characters, control characters removed, blank = remove). The key is the chip id (`chip:<hex>`) when the
  board has been connected once, else the USB key. `boards.ids` maps a USB key to its chip key, learned at connect (as `boards.names` is), so a board connected
  once shows its nickname in the port list next time, **before** connecting.
- **Shared serials** (`000000000000`, `123456`): the USB key is not unique, so the nickname only shows after connecting, from the chip id; the port list
  shows no nickname for those rows (a nickname on the wrong board would be worse than none). The Name dialog says so when it applies.
- Where it shows: the port list (`<marker> /dev/ttyACM4  "Bench CP"  Pico 2 (rp2350a)  #8a64`: the nickname right after the port, in quotes, so the existing
  `device_of` split still finds the port), the pane label, the console title, the Window menu, status messages (`Connected to "Bench CP"`), and the firmware tools'
  running-board rows (by USB key only).
- Editing: `Device > Name this board...` (needs a connected board in the active pane: its chip id is known) using the existing `prompt()` dialog, showing the
  current nickname; empty removes it. Two boards may share a nickname; the dialog warns when the name is already used.
- A nickname is stored on the host in the settings file only; nothing is written to the board. It survives replugging into another port (same key), reinstalling
  firmware (chip id unchanged) and a MicroPython/CircuitPython switch.

### D. Phases
1. **`boardinfo.py` and settings** (GUI-free, fully tested): `firmware_kind`, `boards.firmware`, `boards.nicknames`, `boards.ids`, label formatting, shared-serial
   handling. Tests with fake `ListPortInfo` rows built from the bench table above. No owner test needed.
2. **Markers:** the emoji font check and fallback, the prefix in the port list, `device_of` skipping it, the frame label and the firmware name in every label.
   Tests with and without an emoji font (the check is injectable), a rendered screenshot under Xvfb with the font installed. Owner test: look at the list with
   three or more boards plugged in.
3. **Nicknames:** the menu item and pencil button, propagation to all the places above. Owner test: name two boards of one kind (two Pico 2s), replug in a
   different order, and connect.
4. **Firmware tools and polish:** learned firmware kind written after an install, marker refresh after a switch, the dot for unknown, docs and README text.

### E. Risks
- The marker is part of the text in the box, so a saved source string (`general.last_port`, per-pane remembered sources) must be stored without it and
  matched by port: `remember_source` and `port_choice` strip the marker, and an old setting without a marker still matches.
- A custom MicroPython build with another USB manufacturer string is guessed as unknown until it connects: acceptable, and corrected by learning.
- Colour emoji in a GTK combo box row can be taller than the text and make the rows uneven: check on the Pi 5 display with `grim` before settling.
- Nicknames with odd characters in settings keys: keys go through `settings.segment()` already; values are plain strings.
- Some boards read the same serial on every unit (`000000000000`): nothing here can separate them before connecting; the chip id does after.
