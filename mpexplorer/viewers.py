"""GUI-free helpers for the hex and image viewers."""

HEX_WIDTH = 16


def hexdump(data, width=HEX_WIDTH):
    """Classic hex dump: offset, hex bytes (gap in the middle), printable ASCII."""
    lines = []
    for off in range(0, len(data), width):
        row = data[off:off + width]
        cells = ["%02x" % b for b in row]
        half = width // 2
        left, right = " ".join(cells[:half]), " ".join(cells[half:])
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append("%08x  %-*s  %-*s  |%s|" % (off, half * 3 - 1, left, half * 3 - 1, right, text))
    return "\n".join(lines)
