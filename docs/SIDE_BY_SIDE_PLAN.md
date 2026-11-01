# Side-by-side boards plan

Status (2026-11-01): plan only, nothing built. Goal: either pane can show the host or a board, so two boards can be connected
at once and files copied straight between them. Each board is told apart by its chip id, whatever its USB serial says.

## Telling boards apart: the chip id (decided by the owner, 2026-11-01)
A board is identified by its chip id, `machine.unique_id()`, read when the app connects. Nothing is written to the board, so the "nothing is
installed" rule stands. An id file was considered and dropped.

- **Why:** it is set at the factory and survives erase, install and restore. Two 2022 badges that both report USB serial `123456` have different ids
  (MACs `F4:12:FA:4E:4D:B0` and `...A0` in their bootloader).
- **Used for:** remembered folders and any name given to a board, kept in the app's settings on this computer. Existing entries keyed by USB id move
  across the first time a board connects, except where the USB serial is shared (`123456`), since nobody can say which badge those belonged to.
- **Stays on the USB id:** `boards.rts` and the model shown in the port list, because both are needed before a board can be read. Until it is connected, a
  board can only show its USB name, so two 2022 badges look alike in the list.
- **The port** only tells apart boards connected at that moment: it follows plug-in order, so nothing is remembered by it.
- **No `unique_id`** (a build without it): fall back to the USB id, as today.

`paste-other-board` should be merged first, once tried on real boards. It still helps when the two boards are not plugged in together.

## Connections
A new GUI-free `Connection` holds what `App` keeps for its one board today: port, board id, `RawREPL`, `DeviceBackend`, console session, badge
screen, RTS. In `gtkui/app.py` today: `self.device` 17 uses, `self.board` 8, `self.badge` 9, `self.session` 5, `self.console` 6.

- `App.connections` holds up to two. A port can be used by one pane only: the second pane's list greys it out.
- **One worker thread stays.** Jobs on two boards run one after another, which is safe and needs no locking changes. The cost: a long job on one
  board makes the other wait, and Cancel (`cancel_all`) empties the queue for both. A worker per board is possible later, not in this plan.
- Copies between boards already work: `copy_tree` takes any two backends. Speed is the slower board's, typically 6-10 KB/s from a badge.

## Window
- **Each pane** gets its own source list (Host, then each board), and Connect / Disconnect. These replace the toolbar's Port row. The right pane
  starts as today: a board, and the left as Host, so one-board use looks the same.
- **Toolbar arrows:** "Upload" and "Download" become "Copy to right" and "Copy to left", since either pane can be either thing.
- **Device menu, console, Run script, Set time, Free space:** act on the active pane's board. They are off if the active pane is Host.
- **Console:** one per board, titled with the board, listed in the Window menu.
- **Open with / edit in place:** uses the board of the pane it came from. It is already keyed by board.
- **Badge status screen:** per connection. A job shows on the badges it touches, which needs each job to say which boards it uses.
- **Firmware tools:** disconnect only the board being worked on, not both.
- **Remembered at start:** each pane's source, with no auto-connect, as today.

## Phases
Each ends with the suite green and a hand test; each can be merged alone.

1. **Chip id:** read it on connect, key remembered folders by it, move existing entries, tests on the fake board. Hand test: all four kinds of board, and that the two 2022 badges now remember separately.
2. **`Connection`, one board still:** move the state out of `App` with no visible change. The largest step and the riskiest for regressions. The existing GUI tests are the safety net.
3. **Second connection:** panes with a source list, duplicate-port guard, arrows renamed. Hand test: Pico to Pico, Pico to badge, including cancel and a conflict.
4. **Everything else follows the active pane:** Device menu, console per board, Run, Open with, Sync between boards, badge screens, firmware tools.
5. **Docs:** README, `DESIGN.md`, `HANDOFF.md`, and `CLAUDE.md`, plus fake-board tests with two boards.

## Risks
- **Unplugging one of two:** it must close that pane only, leaving the other connected and its queued jobs alone.
- **Both badges at once:** both report `123456`, so `board_id` cannot separate them until the chip id is read. Everything before that read must go by port.
- **Fake board:** it stores files in one host folder, so tests need two fake boards with separate folders.
- **Hand-test burden:** every board kind against every other is a lot. Pico to Pico and Pico to Tildagon are the minimum; 2022 badge pairs need its no-soft-reset rule checked.

## Not in this plan
Three or more boards, jobs running on two boards at once, and agent mode.
