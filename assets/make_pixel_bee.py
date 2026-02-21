"""Generate 32x32 pixel art keepbee dance animation.

A chibi robot-bee: boxy head, antennae with ball tips, big eyes with highlights,
rosy cheeks, orange/yellow body with dark stripes, stubby limbs.
8-frame dance loop with squash-stretch and bouncing antennae.

Run: uv run python assets/make_pixel_bee.py
Output: assets/keepbee.gif (256x256, transparent, looping)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# ── Color palette ────────────────────────────────────────────────
C = {
    ".": None,  # transparent
    "B": (45, 45, 45, 255),  # black (outline, eyes)
    "O": (232, 155, 59, 255),  # orange body
    "L": (240, 176, 80, 255),  # light orange highlight
    "D": (196, 122, 42, 255),  # dark orange shadow
    "S": (93, 78, 55, 255),  # dark stripe
    "A": (240, 192, 64, 255),  # antenna balls
    "W": (255, 255, 255, 255),  # eye highlight
    "R": (240, 128, 128, 255),  # rosy cheeks
    "H": (220, 200, 170, 255),  # head/face light area
    "G": (200, 185, 155, 255),  # head shadow
    "F": (180, 165, 140, 255),  # feet/darker limbs
    "T": (160, 140, 110, 255),  # antenna stalk
    "Y": (250, 210, 100, 255),  # antenna ball highlight
    "K": (70, 60, 50, 255),  # dark outline accent
}

SCALE = 8  # 32 * 8 = 256px

# ── Frame grids (32x32 each) ────────────────────────────────────
# Each frame is a list of 32 strings, each 32 chars.
# Legend: . transparent, B black, O orange, L light-orange, D dark-orange,
#         S stripe, A antenna-ball, W white-highlight, R rosy-cheek,
#         H head-light, G head-shadow, F feet, T antenna-stalk,
#         Y antenna-highlight, K dark-accent

# Frame 1: Neutral standing pose
F1 = [
    "................................",  # 0
    "........A.............A.........",  # 1
    "........T.............T.........",  # 2
    "........T.............T.........",  # 3
    ".........T...........T..........",  # 4
    "..........BBBBBBBBBBB...........",  # 5
    ".........BHHHHHHHHHHHB..........",  # 6
    ".........BHHHHHHHHHHHHB.........",  # 7
    "........BHHHBBBHHHBBBHHB........",  # 8
    "........BHHHBWBHHHBWBHHB........",  # 9
    "........BHHBBBBHHHBBBBHB........",  # 10
    "........BHHHRHHHHHHRHHB.........",  # 11
    ".........BHHHHHHHHHHHB..........",  # 12
    "..........BBBBBBBBBBB...........",  # 13
    "..........BOLLLLLLLOB...........",  # 14
    ".........BOLLLLLLLLLLOB.........",  # 15
    ".........BOLLLLLLLLLLLOB........",  # 16
    "........BOSSSSSSSSSSSSOB........",  # 17
    "........BOLLLLLLLLLLLLLOB.......",  # 18
    "........BOSSSSSSSSSSSSSSB.......",  # 19
    ".........BOLLLLLLLLLLLLOB.......",  # 20
    ".........BOLLLLLLLLLLLOB........",  # 21
    "..........BOSSSSSSSSOB..........",  # 22
    "..........BOLLLLLLLOB...........",  # 23
    "...........BBBBBBBBBB...........",  # 24
    "..........BFB......BFB.........",  # 25
    "..........BFB......BFB.........",  # 26
    ".........BFFB.....BFFB.........",  # 27
    ".........BBBB.....BBBB.........",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 2: Squash (body compresses 1px, knees bend)
F2 = [
    "................................",  # 0
    ".......A..............A.........",  # 1
    "........T.............T.........",  # 2
    "........T.............T.........",  # 3
    ".........T...........T..........",  # 4
    "..........BBBBBBBBBBB...........",  # 5
    ".........BHHHHHHHHHHHB..........",  # 6
    ".........BHHHHHHHHHHHHB.........",  # 7
    "........BHHHBBBHHHBBBHHB........",  # 8
    "........BHHHBWBHHHBWBHHB........",  # 9
    "........BHHBBBBHHHBBBBHB........",  # 10
    "........BHHHRHHHHHHRHHB.........",  # 11
    ".........BHHHHHHHHHHHB..........",  # 12
    "..........BBBBBBBBBBB...........",  # 13
    "..........BOLLLLLLLOB...........",  # 14
    ".........BOLLLLLLLLLLOB.........",  # 15
    "........BOLLLLLLLLLLLLOB........",  # 16
    "........BOSSSSSSSSSSSSSOB.......",  # 17
    "........BOLLLLLLLLLLLLLOB.......",  # 18
    "........BOSSSSSSSSSSSSSOB.......",  # 19
    ".........BOLLLLLLLLLLLLOB.......",  # 20
    "..........BOSSSSSSSSSSOB........",  # 21
    "..........BOLLLLLLLLLLOB........",  # 22
    "...........BBBBBBBBBB...........",  # 23
    "..........BFB......BFB.........",  # 24
    ".........BFFB.....BFFB.........",  # 25
    ".........BBBB.....BBBB.........",  # 26
    "................................",  # 27
    "................................",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 3: Jump up + arms raised (body stretches, 2px hop)
F3 = [
    "................................",  # 0
    "..........A...........A.........",  # 1
    "..........T...........T.........",  # 2
    "..........T...........T.........",  # 3
    "..........BBBBBBBBBBB...........",  # 4
    ".........BHHHHHHHHHHHB..........",  # 5
    ".........BHHHHHHHHHHHHB.........",  # 6
    "........BHHHBBBHHHBBBHHB........",  # 7
    "........BHHHBWBHHHBWBHHB........",  # 8
    "........BHHBBBBHHHBBBBHB........",  # 9
    "........BHHHRHHHHHHRHHB.........",  # 10
    ".........BHHHHHHHHHHHB..........",  # 11
    "..........BBBBBBBBBBB...........",  # 12
    ".......BFB.BOLLLLOB..BFB.......",  # 13
    ".......BFB.BOLLLLLOB.BFB.......",  # 14
    "........B.BOLLLLLLLOB.B........",  # 15
    "..........BOSSSSSSSSOB..........",  # 16
    "..........BOLLLLLLLLOB..........",  # 17
    "..........BOSSSSSSSSSOB.........",  # 18
    "...........BOLLLLLLLOB..........",  # 19
    "...........BOLLLLLLOB...........",  # 20
    "............BBBBBBBBB...........",  # 21
    "...........BFB....BFB...........",  # 22
    "...........BBB....BBB...........",  # 23
    "................................",  # 24
    "................................",  # 25
    "................................",  # 26
    "................................",  # 27
    "................................",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 4: Peak (highest point, arms fully up, antennae bounce up)
F4 = [
    "..........A...........A.........",  # 0
    "..........T...........T.........",  # 1
    "..........BBBBBBBBBBB...........",  # 2
    ".........BHHHHHHHHHHHB..........",  # 3
    ".........BHHHHHHHHHHHHB.........",  # 4
    "........BHHHBBBHHHBBBHHB........",  # 5
    "........BHHHBWBHHHBWBHHB........",  # 6
    "........BHHBBBBHHHBBBBHB........",  # 7
    "........BHHHRHHHHHHRHHB.........",  # 8
    ".........BHHHHHHHHHHHB..........",  # 9
    "..........BBBBBBBBBBB...........",  # 10
    "......BFB..BOLLLLOB..BFB.......",  # 11
    "......BFB..BOLLLLLOB.BFB.......",  # 12
    ".......B..BOLLLLLLLOB.B........",  # 13
    "..........BOSSSSSSSSOB..........",  # 14
    "..........BOLLLLLLLLOB..........",  # 15
    "..........BOSSSSSSSSSOB.........",  # 16
    "...........BOLLLLLLLOB..........",  # 17
    "...........BOLLLLLLOB...........",  # 18
    "............BBBBBBBBB...........",  # 19
    "...........BFB....BFB...........",  # 20
    "...........BBB....BBB...........",  # 21
    "................................",  # 22
    "................................",  # 23
    "................................",  # 24
    "................................",  # 25
    "................................",  # 26
    "................................",  # 27
    "................................",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 5: Landing + slight lean right (subtle arm shift)
F5 = [
    "................................",  # 0
    "..........A...........A.........",  # 1
    "..........T...........T.........",  # 2
    "..........T...........T.........",  # 3
    "..........BBBBBBBBBBB...........",  # 4
    ".........BHHHHHHHHHHHHB.........",  # 5
    ".........BHHHHHHHHHHHHB.........",  # 6
    "........BHHHBBBHHHBBBHHB........",  # 7
    "........BHHHBWBHHHBWBHHB........",  # 8
    "........BHHBBBBHHHBBBBHB........",  # 9
    "........BHHHRHHHHHHRHHB.........",  # 10
    ".........BHHHHHHHHHHHB..........",  # 11
    "..........BBBBBBBBBBB...........",  # 12
    "..........BOLLLLLLLOB...........",  # 13
    ".........BOLLLLLLLLLLOB.........",  # 14
    ".........BOLLLLLLLLLLLOB........",  # 15
    "........BOSSSSSSSSSSSSOB........",  # 16
    "........BOLLLLLLLLLLLLLOB.......",  # 17
    "........BOSSSSSSSSSSSSSSB.......",  # 18
    ".........BOLLLLLLLLLLLLOB.......",  # 19
    ".........BOLLLLLLLLLLLOB........",  # 20
    "..........BOSSSSSSSSOB..........",  # 21
    "..........BOLLLLLLLOB...........",  # 22
    "...........BBBBBBBBBB...........",  # 23
    "..........BFB......BFB.........",  # 24
    "..........BFB......BFB.........",  # 25
    ".........BFFB.....BFFB.........",  # 26
    ".........BBBB.....BBBB.........",  # 27
    "................................",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 6: Squash again (landing compression, antennae bounce forward)
F6 = [
    "................................",  # 0
    "................................",  # 1
    ".......A..............A.........",  # 2
    ".......T..............T.........",  # 3
    "........T.............T.........",  # 4
    "..........BBBBBBBBBBB...........",  # 5
    ".........BHHHHHHHHHHHB..........",  # 6
    ".........BHHHHHHHHHHHHB.........",  # 7
    "........BHHHBBBHHHBBBHHB........",  # 8
    "........BHHHBWBHHHBWBHHB........",  # 9
    "........BHHBBBBHHHBBBBHB........",  # 10
    "........BHHHRHHHHHHRHHB.........",  # 11
    ".........BHHHHHHHHHHHB..........",  # 12
    "..........BBBBBBBBBBB...........",  # 13
    "..........BOLLLLLLLOB...........",  # 14
    ".........BOLLLLLLLLLLOB.........",  # 15
    "........BOLLLLLLLLLLLLOB........",  # 16
    "........BOSSSSSSSSSSSSSOB.......",  # 17
    "........BOLLLLLLLLLLLLLOB.......",  # 18
    "........BOSSSSSSSSSSSSSOB.......",  # 19
    ".........BOLLLLLLLLLLLLOB.......",  # 20
    "..........BOSSSSSSSSSSOB........",  # 21
    "..........BOLLLLLLLLLLOB........",  # 22
    "...........BBBBBBBBBB...........",  # 23
    "..........BFB......BFB.........",  # 24
    ".........BFFB.....BFFB.........",  # 25
    ".........BBBB.....BBBB.........",  # 26
    "................................",  # 27
    "................................",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 7: Jump + slight lean left (mirror of frame 3, subtler)
F7 = [
    "................................",  # 0
    "..........A...........A.........",  # 1
    "..........T...........T.........",  # 2
    "..........T...........T.........",  # 3
    "..........BBBBBBBBBBB...........",  # 4
    ".........BHHHHHHHHHHHB..........",  # 5
    ".........BHHHHHHHHHHHHB.........",  # 6
    "........BHHHBBBHHHBBBHHB........",  # 7
    "........BHHHBWBHHHBWBHHB........",  # 8
    "........BHHBBBBHHHBBBBHB........",  # 9
    "........BHHHRHHHHHHRHHB.........",  # 10
    ".........BHHHHHHHHHHHB..........",  # 11
    "..........BBBBBBBBBBB...........",  # 12
    "..........BOLLLLOB..............",  # 13
    ".........BOLLLLLOB..............",  # 14
    ".........BOLLLLLLLOB...........",  # 15
    "..........BOSSSSSSSSOB..........",  # 16
    "..........BOLLLLLLLLOB..........",  # 17
    "..........BOSSSSSSSSSOB.........",  # 18
    "...........BOLLLLLLLOB..........",  # 19
    "...........BOLLLLLLOB...........",  # 20
    "............BBBBBBBBB...........",  # 21
    "...........BFB....BFB...........",  # 22
    "...........BBB....BBB...........",  # 23
    "................................",  # 24
    "................................",  # 25
    "................................",  # 26
    "................................",  # 27
    "................................",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

# Frame 8: Landing/settling back to neutral
F8 = [
    "................................",  # 0
    ".........A..............A.......",  # 1
    ".........T..............T.......",  # 2
    ".........T..............T.......",  # 3
    ".........T.............T........",  # 4
    "..........BBBBBBBBBBB...........",  # 5
    ".........BHHHHHHHHHHHB..........",  # 6
    ".........BHHHHHHHHHHHHB.........",  # 7
    "........BHHHBBBHHHBBBHHB........",  # 8
    "........BHHHBWBHHHBWBHHB........",  # 9
    "........BHHBBBBHHHBBBBHB........",  # 10
    "........BHHHRHHHHHHRHHB.........",  # 11
    ".........BHHHHHHHHHHHB..........",  # 12
    "..........BBBBBBBBBBB...........",  # 13
    "..........BOLLLLLLLOB...........",  # 14
    ".........BOLLLLLLLLLLOB.........",  # 15
    ".........BOLLLLLLLLLLLOB........",  # 16
    "........BOSSSSSSSSSSSSOB........",  # 17
    "........BOLLLLLLLLLLLLLOB.......",  # 18
    "........BOSSSSSSSSSSSSSSB.......",  # 19
    ".........BOLLLLLLLLLLLLOB.......",  # 20
    ".........BOLLLLLLLLLLLOB........",  # 21
    "..........BOSSSSSSSSOB..........",  # 22
    "..........BOLLLLLLLOB...........",  # 23
    "...........BBBBBBBBBB...........",  # 24
    "..........BFB......BFB.........",  # 25
    "..........BFB......BFB.........",  # 26
    ".........BFFB.....BFFB.........",  # 27
    ".........BBBB.....BBBB.........",  # 28
    "................................",  # 29
    "................................",  # 30
    "................................",  # 31
]

ALL_FRAMES = [F1, F2, F3, F4, F5, F6, F7, F8]


def make_frame(grid: list[str]) -> Image.Image:
    """Render a 32-char grid to a 32x32 RGBA image, scaled to 256x256."""
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    pixels = img.load()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            color = C.get(ch)
            if color is not None:
                pixels[x, y] = color
    return img.resize((256, 256), Image.NEAREST)


def main() -> None:
    out = Path(__file__).parent / "keepbee.gif"
    frames = [make_frame(f) for f in ALL_FRAMES]

    # Save as animated GIF with transparency
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=120,
        loop=0,
        disposal=2,
        transparency=0,
    )
    size_kb = out.stat().st_size / 1024
    print(f"Saved {out} ({size_kb:.0f}KB, {len(frames)} frames, 256x256)")


if __name__ == "__main__":
    main()
