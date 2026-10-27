# Contributing
- `pip install -e '.[dev]'`, then `ruff check . && pytest` before opening a PR.
- Core logic must be testable without hardware: extend `tests/fakedevice.py` rather than skipping tests.
- If you tested on a board, add a row to `docs/HARDWARE.md`.
- Keep the default mode free of any code installed on the target.

## Tests against real MicroPython
`pytest` always runs the fast tests against a fake board. A second layer runs the same behaviours against the
real MicroPython unix port (`tests/test_real_micropython.py`); it is skipped unless a binary is available:
```
git clone --depth 1 --branch v1.26.0 https://github.com/micropython/micropython
make -C micropython/mpy-cross && make -C micropython/ports/unix submodules && make -C micropython/ports/unix
MICROPYTHON=$PWD/micropython/ports/unix/build-standard/micropython pytest tests/test_real_micropython.py -rsP
```
The unix port has no raw REPL, so file / console / sync tests run real MicroPython behind `tests/rawrepl_shim.py`
(a small script that speaks the raw-REPL framing); terminal tests use the unix port's own REPL. CI builds and
runs this on every push (the `micropython` job). Behaviour that differs from the fake board should be fixed in
the code or, if the fake is wrong, in `tests/fakedevice.py`.
