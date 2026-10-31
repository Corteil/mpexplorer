from mpexplorer.viewers import hexdump


def test_hexdump_full_and_partial_rows():
    data = bytes(range(0x41, 0x41 + 20))
    lines = hexdump(data).split("\n")
    assert lines[0] == "00000000  41 42 43 44 45 46 47 48  49 4a 4b 4c 4d 4e 4f 50  |ABCDEFGHIJKLMNOP|"
    assert lines[1] == "00000010  51 52 53 54                                       |QRST|"


def test_hexdump_unprintable_and_empty():
    assert hexdump(b"\x00\x7f\xff").endswith("|...|")
    assert hexdump(b"") == ""
