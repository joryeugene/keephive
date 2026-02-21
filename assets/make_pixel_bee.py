"""Generate 32x32 pixel art keepbee robot-bee animation.

A chibi robot-bee: boxy head with gray metal side panels, large kawaii eyes,
small honeycomb wings, chunky robot arms, amber/orange body with dark stripes.
10-frame bounce animation with wing flutter, spring antennae, and squash-stretch.

Run: uv run python assets/make_pixel_bee.py
Output: assets/keepbee.gif (256x256, transparent, looping)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# ── Color palette (robot-bee: warm amber + gray metal) ───────────
C = {
    ".": None,                          # transparent
    "B": (45,  45,  45,  255),          # black outline
    "O": (240, 170, 60,  255),          # amber/orange body
    "L": (250, 195, 95,  255),          # light amber highlight
    "S": (75,  65,  50,  255),          # dark stripe
    "A": (240, 180, 50,  255),          # antenna ball
    "W": (255, 255, 255, 255),          # eye/surface highlight
    "H": (230, 200, 160, 255),          # face front (warm cream)
    "G": (160, 155, 145, 255),          # gray metal panels
    "F": (140, 135, 125, 255),          # dark gray metal (fists, boots)
    "T": (65,  60,  50,  255),          # antenna stalk
    "Y": (250, 210, 90,  255),          # antenna ball highlight
    "P": (190, 185, 165, 255),          # wing
    "Q": (210, 205, 185, 255),          # wing highlight
    "M": (85,  75,  60,  255),          # mouth
}

SCALE = 8  # 32 * 8 = 256px

# ── Component patterns ───────────────────────────────────────────
# Each is a list of strings. '.' in patterns = skip (preserve underlying pixel).

# Boxy head with gray side panels, large eyes, tiny mouth
HEAD = [  # 16 wide x 12 tall
    "BBBBBBBBBBBBBBBB",
    "BGGHHHHHHHHHHGGB",
    "BGGHHHHHHHHHHGGB",
    "BGGHHHHHHHHHHGGB",
    "BGGHBBBHHBBBHGGB",  # eye tops
    "BGGHBWBHHBWBHGGB",  # eye highlights
    "BGGHBBBHHBBBHGGB",  # eye bottoms
    "BGGHHHHHHHHHHGGB",
    "BGGHHHHMMHHHHGGB",  # mouth (2px centered)
    "BGGHHHHHHHHHHGGB",
    "BGGHHHHHHHHHHGGB",
    "BBBBBBBBBBBBBBBB",
]

# Striped amber body, tapering at top and bottom
BODY_NORMAL = [  # 12 wide x 8 tall
    ".BLLLLLLLLB.",
    "BOLLLLLLLLOB",
    "BOSSSSSSSSOB",
    "BOLLLLLLLLOB",
    "BOSSSSSSSSOB",
    "BOLLLLLLLLOB",
    ".BSSSSSSSSB.",
    "..BBBBBBBB..",
]

# Wider, shorter body for squash frames
BODY_SQUASH = [  # 14 wide x 6 tall
    "BOLLLLLLLLLLOB",
    "BOSSSSSSSSSSOB",
    "BOLLLLLLLLLLOB",
    "BOSSSSSSSSSSOB",
    "BOLLLLLLLLLLOB",
    "..BBBBBBBBBB..",
]

# Orange legs with gray boots
LEGS_STAND = [  # 12 wide x 5 tall
    "..BOB..BOB..",
    "..BOB..BOB..",
    "..BGB..BGB..",
    "..BFB..BFB..",
    "..BBB..BBB..",
]

# Compressed legs for squash/landing frames
LEGS_BENT = [  # 12 wide x 3 tall
    "..BGB..BGB..",
    "..BFB..BFB..",
    "..BBB..BBB..",
]


# ── Grid helpers ─────────────────────────────────────────────────

def new_grid():
    return [["." for _ in range(32)] for _ in range(32)]


def set_px(grid, x, y, ch):
    if 0 <= y < 32 and 0 <= x < 32:
        grid[y][x] = ch


def stamp(grid, pattern, x, y):
    """Stamp pattern onto grid. '.' chars in pattern are skipped."""
    for dy, row in enumerate(pattern):
        for dx, ch in enumerate(row):
            if ch != ".":
                set_px(grid, x + dx, y + dy, ch)


def grid_to_strings(grid):
    return ["".join(row) for row in grid]


# ── Component drawing ────────────────────────────────────────────

def draw_antennae(grid, hx, hy, droop):
    """Draw antennae above head. droop: -1=perked, 0=normal, 1=drooping."""
    lx, rx = hx + 3, hx + 12  # stalk base cols (11, 20 for hx=8)

    if droop == 0:  # straight up
        for col in (lx, rx):
            set_px(grid, col, hy - 1, "T")
            set_px(grid, col, hy - 2, "T")
            set_px(grid, col, hy - 3, "A")
            set_px(grid, col, hy - 4, "Y")
    elif droop == 1:  # drooping outward (spring lag during squash)
        set_px(grid, lx, hy - 1, "T")
        set_px(grid, lx - 1, hy - 2, "T")
        set_px(grid, lx - 1, hy - 3, "A")
        set_px(grid, lx - 1, hy - 4, "Y")
        set_px(grid, rx, hy - 1, "T")
        set_px(grid, rx + 1, hy - 2, "T")
        set_px(grid, rx + 1, hy - 3, "A")
        set_px(grid, rx + 1, hy - 4, "Y")
    else:  # -1: perked up (compact, spring overshoot during jump)
        for col in (lx, rx):
            set_px(grid, col, hy - 1, "T")
            set_px(grid, col, hy - 2, "A")
            set_px(grid, col, hy - 3, "Y")


def draw_wings(grid, bx, by, bw, state):
    """Draw small wings beside body. bx/by/bw = body position and width."""
    if state == "up":
        set_px(grid, bx - 2, by, "Q")
        set_px(grid, bx - 1, by, "P")
        set_px(grid, bx + bw, by, "P")
        set_px(grid, bx + bw + 1, by, "Q")
    elif state == "down":
        # 3px per side so outer pixel survives arm overlap
        set_px(grid, bx - 3, by + 1, "P")
        set_px(grid, bx - 2, by + 1, "Q")
        set_px(grid, bx - 1, by + 1, "P")
        set_px(grid, bx + bw, by + 1, "P")
        set_px(grid, bx + bw + 1, by + 1, "Q")
        set_px(grid, bx + bw + 2, by + 1, "P")
    elif state == "spread":
        set_px(grid, bx - 3, by, "Q")
        set_px(grid, bx - 2, by, "P")
        set_px(grid, bx - 1, by + 1, "P")
        set_px(grid, bx + bw, by, "P")
        set_px(grid, bx + bw + 1, by, "Q")
        set_px(grid, bx + bw + 2, by + 1, "P")


def draw_arms(grid, hx, hy, bx, by, state, squash):
    """Draw robot arms. Position varies by pose state."""
    bw = 14 if squash else 12

    if state == "down":  # at body sides, dangling
        stamp(grid, ["BG", "BG", "BF", "BF", ".B"], bx - 2, by + 1)
        stamp(grid, ["GB", "GB", "FB", "FB", "B."], bx + bw, by + 1)
    elif state == "out":  # extended for balance (squash frames)
        stamp(grid, ["BGB", "BFB", ".B."], bx - 4, by + 2)
        stamp(grid, ["BGB", "BFB", ".B."], bx + bw + 1, by + 2)
    elif state == "mid":  # transitioning up/down
        stamp(grid, [".B", "BG", "BF", ".B"], bx - 3, by)
        stamp(grid, ["B.", "GB", "FB", "B."], bx + bw + 1, by)
    elif state == "up":  # raised beside head (jump celebration)
        stamp(grid, ["BF", "BG", "BG", ".B"], hx - 2, hy + 3)
        stamp(grid, ["FB", "GB", "GB", "B."], hx + 16, hy + 3)


# ── Frame builder ────────────────────────────────────────────────

FRAME_CFGS = [
    # y_offset, arm_state, wing_state, antenna_droop, squash, duration_ms
    {"y": 0,  "arm": "down", "wing": "up",     "droop": 0,  "squash": False, "dur": 120},
    {"y": 1,  "arm": "down", "wing": "down",   "droop": 0,  "squash": False, "dur": 100},
    {"y": 1,  "arm": "out",  "wing": "down",   "droop": 1,  "squash": True,  "dur": 80},
    {"y": 0,  "arm": "mid",  "wing": "up",     "droop": 1,  "squash": False, "dur": 80},
    {"y": -1, "arm": "up",   "wing": "spread", "droop": 0,  "squash": False, "dur": 100},
    {"y": -2, "arm": "up",   "wing": "up",     "droop": -1, "squash": False, "dur": 150},
    {"y": -1, "arm": "up",   "wing": "down",   "droop": -1, "squash": False, "dur": 100},
    {"y": 0,  "arm": "mid",  "wing": "down",   "droop": 0,  "squash": False, "dur": 80},
    {"y": 1,  "arm": "out",  "wing": "up",     "droop": 1,  "squash": True,  "dur": 80},
    {"y": 0,  "arm": "down", "wing": "down",   "droop": 0,  "squash": False, "dur": 120},
]


def build_frame(cfg):
    """Compose a 32x32 frame from components based on config."""
    grid = new_grid()
    y_off = cfg["y"]
    squash = cfg["squash"]

    # Head always at x=8, y shifts with offset
    hx, hy = 8, 4 + y_off

    # Body position/size depends on squash state
    if squash:
        bx, bw, body, legs = 9, 14, BODY_SQUASH, LEGS_BENT
        body_h = 6
    else:
        bx, bw, body, legs = 10, 12, BODY_NORMAL, LEGS_STAND
        body_h = 8

    by = hy + 12  # body starts right after 12-row head
    ly = by + body_h  # legs start after body

    # Draw back-to-front: wings → body → legs → head → arms → antennae
    draw_wings(grid, bx, by, bw, cfg["wing"])
    stamp(grid, body, bx, by)
    stamp(grid, legs, 10, ly)
    stamp(grid, HEAD, hx, hy)
    draw_arms(grid, hx, hy, bx, by, cfg["arm"], squash)
    draw_antennae(grid, hx, hy, cfg["droop"])

    result = grid_to_strings(grid)
    for i, row in enumerate(result):
        assert len(row) == 32, f"Frame row {i} has {len(row)} chars: {row}"
    return result


# ── Rendering ────────────────────────────────────────────────────

def make_image(grid_strings):
    """Render 32-char grid strings to a 256x256 RGBA image."""
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    pixels = img.load()
    for y, row in enumerate(grid_strings):
        for x, ch in enumerate(row):
            color = C.get(ch)
            if color is not None:
                pixels[x, y] = color
    return img.resize((256, 256), Image.NEAREST)


def main():
    out = Path(__file__).parent / "keepbee.gif"
    frames = [make_image(build_frame(cfg)) for cfg in FRAME_CFGS]
    durations = [cfg["dur"] for cfg in FRAME_CFGS]

    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        transparency=0,
    )
    size_kb = out.stat().st_size / 1024
    print(f"Saved {out} ({size_kb:.0f}KB, {len(frames)} frames, 256x256)")


if __name__ == "__main__":
    main()
