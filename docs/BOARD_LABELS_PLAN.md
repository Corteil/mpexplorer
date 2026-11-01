# Firmware badges (CP / MP) and board nicknames plan

Status (2026-11-01): phase 1 built on branch `board-info` (`mpexplorer/boardinfo.py`, `ports.list_boards`, settings `boards.firmware` / `boards.nicknames` / `boards.ids`, `tests/test_boardinfo.py`): no screen yet, nothing visible changes. Phases 2-4 not built. Asked for by the owner: the port list does not make clear which firmware a board
runs; add a marker per firmware type and a nickname for each unique board. **Settled 2026-11-01:** the markers are the letters **CP** and **MP**
shown as filled badges (text and background colours swapped, "inverted"); no emoji. (The owner first asked for 🐍 and an inverted M, then clarified.)

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
- **Emoji were considered and dropped:** 🐍 (U+1F40D) has no font on this Pi (`fonts-noto-color-emoji` is in apt but not installed) and there is no emoji for
  MicroPython. Plain letters in a coloured badge need no font anywhere.

## Decisions
Settled: letters **CP** and **MP**, as inverted (filled) badges. Plan defaults for the rest, to be changed on request:
1. **Colours:** white bold text on a filled rounded badge, a different fill per firmware so they differ at a glance and not only by the letters:
   CircuitPython purple `#6B3FA0`, MicroPython teal `#1F7A6D` (white on either is above 4.5:1 contrast). Unknown firmware: a grey outlined badge with `??`.
2. **Nickname editing:** `Device > Name this board...` plus a small pencil button next to the port box.
3. **Both panes:** each pane shows its own badge, because each can hold a different board.

## Design
### A. Firmware kind of a board (GUI-free, new `mpexplorer/boardinfo.py`)
`firmware_kind(usb_info, learned)` returns `"micropython"`, `"circuitpython"` or `None`.
1. **Learned wins:** `boards.firmware[<usb key>]` is written when the app connects (`circuitpython.detect` already returns the name) and by the firmware tools
   after an install (`Installed`) or when they read a running board. A reinstall of the other firmware therefore corrects the marker at once.
2. **Otherwise guess from USB:** manufacturer `MicroPython` -> MicroPython; vendor `239a` -> CircuitPython; vendor `2e8a` with any other product
   (a non-MicroPython RP2 USB string) -> CircuitPython; the EMF badge ids (`16d0:120e`, `16d0:1114`, `303a:4001`) -> MicroPython; everything else `None`.
3. The guess is shown as the marker without any hint; nothing says "guessed". A wrong guess is fixed by one connect.
`ports.list_ports` and the firmware tools use this one function.

### B. Badges
- **In the drop-down rows:** the source box becomes a `Gtk.ComboBox` with an entry over a `ListStore(badge text, badge fill, row text)`. Each row has a
  `CellRendererText` for the badge (`foreground` white, `background` the fill, bold, a little `xpad`) before the row's own text. The row text stays exactly as
  it is today (`/dev/ttyACM4  Pico 2 (rp2350a)  #8a64`) so `device_of()`, `Pane.source()` and every saved port string keep working. `fill_sources` takes
  `(badge, text)` pairs; the badge is never part of the entry's text.
- **Beside the box:** the box's entry cannot hold a coloured part, so a small `Gtk.Label` styled by CSS (`.fw-cp`, `.fw-mp`, `.fw-unknown`) sits left of it and shows the
  badge of the chosen row; it updates when the choice changes and when a connect or install teaches the app the firmware. The CSS goes in the app's one stylesheet
  and uses the theme's own font; the fill colours are fixed, the text white, so it reads on light and dark themes.
- **Pane label:** the frame label (`Device: /dev/ttyACM4  Pico 2 (rp2350a)  CircuitPython 10.3.1`) is replaced by a small box (badge + label) through
  `Gtk.Frame.set_label_widget`, and always names the firmware: `MicroPython 1.29.0` as well as `CircuitPython 10.3.1`.
- **Plain text places** (console window title, Window menu, status messages): a bracketed prefix, `[CP]` / `[MP]`, since menus cannot show colours reliably.
- **Firmware tools' device list** (a text `ComboBoxText`): the same `[CP]` / `[MP]` prefix on running boards.

### C. Nicknames
- Setting `boards.nicknames`: board key -> text (at most 24 characters, control characters removed, blank = remove). The key is the chip id (`chip:<hex>`) when the
  board has been connected once, else the USB key. `boards.ids` maps a USB key to its chip key, learned at connect (as `boards.names` is), so a board connected
  once shows its nickname in the port list next time, **before** connecting.
- **Shared serials** (`000000000000`, `123456`): the USB key is not unique, so the nickname only shows after connecting, from the chip id; the port list
  shows no nickname for those rows (a nickname on the wrong board would be worse than none). The Name dialog says so when it applies.
- Where it shows: the port list (`/dev/ttyACM4  "Bench CP"  Pico 2 (rp2350a)  #8a64` with its badge in front: the nickname right after the port, in quotes, so the existing
  `device_of` split still finds the port), the pane label, the console title, the Window menu, status messages (`Connected to "Bench CP"`), and the firmware tools'
  running-board rows (by USB key only).
- Editing: `Device > Name this board...` (needs a connected board in the active pane: its chip id is known) using the existing `prompt()` dialog, showing the
  current nickname; empty removes it. Two boards may share a nickname; the dialog warns when the name is already used.
- A nickname is stored on the host in the settings file only; nothing is written to the board. It survives replugging into another port (same key), reinstalling
  firmware (chip id unchanged) and a MicroPython/CircuitPython switch.

### D. Phases
1. **`boardinfo.py` and settings** (GUI-free, fully tested): `firmware_kind`, `boards.firmware`, `boards.nicknames`, `boards.ids`, label formatting, shared-serial
   handling. Tests with fake `ListPortInfo` rows built from the bench table above. No owner test needed.
2. **Badges:** the `ComboBox` change with the badge cell, the badge label beside it, the stylesheet, the pane label with the badge and the firmware name, the `[CP]`/`[MP]`
   prefixes. Tests under Xvfb, the pane tests adapted to `(badge, text)` rows, and rendered screenshots (`Gdk.pixbuf_get_from_window`) of the list with several boards.
   Owner test: look at the list and both panes with three or more boards plugged in.
3. **Nicknames:** the menu item and pencil button, propagation to all the places above. Owner test: name two boards of one kind (two Pico 2s), replug in a
   different order, and connect.
4. **Firmware tools and polish:** learned firmware kind written after an install, badge refresh after a switch, the grey `??` badge for unknown, docs and README text.

### E. Risks
- Swapping `ComboBoxText` for `ComboBox` touches every test that calls `append_text` / `get_active_text` on the source box: done once, behind `Pane.fill_sources` and
  `Pane.source()`. Typing a port by hand in the entry must still work (the box keeps its entry), and a typed port gets the badge of any known row for it, else `??`.
- A custom MicroPython build with another USB manufacturer string is guessed as unknown until it connects: acceptable, and corrected by learning.
- A coloured cell background in a drop-down row can look heavy with some themes (PiXflat is the Pi's): check on the Pi 5 display with `grim`, and use a thin badge (small `ypad`) rather than a full-height block.
- Nicknames with odd characters in settings keys: keys go through `settings.segment()` already; values are plain strings.
- Some boards read the same serial on every unit (`000000000000`): nothing here can separate them before connecting; the chip id does after.
