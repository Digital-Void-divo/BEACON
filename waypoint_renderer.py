"""
Waypoint image renderer for B34C0N.
Builds the 5×3 badge grid image — pure PIL, no Discord dependency.
"""

from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageEnhance


# ─── WAYPOINTS ────────────────────────────────────────────────────────────────

WAYPOINTS = [
    {"id": "first_transmission", "name": "First Transmission", "description": "Bump for the first time"},
    {"id": "signal_booster",     "name": "Signal Booster",     "description": "Bump 10 times"},
    {"id": "tower_operator",     "name": "Tower Operator",     "description": "Bump 50 times"},
    {"id": "grid_architect",     "name": "Grid Architect",     "description": "Bump 100 times"},
    {"id": "signal_thief",       "name": "Signal Thief",       "description": "Steal a bump for the first time"},
    {"id": "scavenger",          "name": "Scavenger",          "description": "Steal 5 bumps"},
    {"id": "frequency_jacker",   "name": "Frequency Jacker",   "description": "Steal 25 bumps"},
    {"id": "ransomware",         "name": "Ransomware",         "description": "Steal 50 bumps"},
    {"id": "wasteland_champion", "name": "Wasteland Champion", "description": "Finish 1st in a cycle"},
    {"id": "dynasty",            "name": "Dynasty",            "description": "Finish 1st in two cycles in a row"},
    {"id": "podium_regular",     "name": "Podium Regular",     "description": "Finish top 3 in three cycles"},
    {"id": "speedy",             "name": "Speedy",             "description": "Bump within 10 seconds of cooldown reset"},
    {"id": "clockwork",          "name": "Clockwork",          "description": "Bump within 5 seconds of cooldown reset"},
    {"id": "race_condition",     "name": "Race Condition",     "description": "Bump within 1 second of cooldown reset"},
    {"id": "reliable_signal",    "name": "Reliable Signal",    "description": "Bump at least once a day for 7 consecutive days"},
]


# ─── ASSET PATHS ──────────────────────────────────────────────────────────────

ASSET_DIR        = Path(__file__).parent
WAYPOINT_IMG_DIR = ASSET_DIR / "waypoints"


# ─── GRID / LAYOUT CONSTANTS ─────────────────────────────────────────────────

# Oval interior bounds as fractions of background image dimensions
# Calibrated for 1536x1024 background
OVAL_LEFT_F   = 0.10
OVAL_TOP_F    = 0.165
OVAL_RIGHT_F  = 0.90
OVAL_BOTTOM_F = 0.845

# Badge grid layout
GRID_COLS   = 5
GRID_ROWS   = 3
GRID_PAD_X  = 30    # px padding inside oval on each side
GRID_PAD_Y  = 22
BADGE_GAP_X = 16    # px gap between badge columns
BADGE_GAP_Y = 14    # px gap between badge rows

# Slot image regions as fractions of badge dimensions
SLOT_WP_LEFT       = 0.06   # waypoint image paste region within slot
SLOT_WP_TOP        = 0.04
SLOT_WP_RIGHT      = 0.94
SLOT_WP_BOTTOM     = 0.76
SLOT_TEXT_CENTER_Y = 0.885  # vertical center of nameplate text


# ─── RENDERER ─────────────────────────────────────────────────────────────────

def build_waypoint_image(earned_ids: list, custom_wps: list, page: int = 0) -> BytesIO:
    """
    Render the 5x3 Waypoint grid onto the oval background.
    page=0 is the first page. Custom earned waypoints always appear first.
    Required assets (relative to bot script):
      waypoint_background.png, waypoint_slot.png, waypoint_slot_custom.png,
      WaypointFont.otf, waypoints/<waypoint_id>.png
    """
    bg           = Image.open(ASSET_DIR / "waypoint_background.png").convert("RGBA")
    slot_std     = Image.open(ASSET_DIR / "waypoint_slot.png").convert("RGBA")
    slot_cst_src = None
    cst_path     = ASSET_DIR / "waypoint_slot_custom.png"
    if cst_path.exists():
        slot_cst_src = Image.open(cst_path).convert("RGBA")
    bg_w, bg_h = bg.size

    # Oval interior in pixels
    ox0 = int(bg_w * OVAL_LEFT_F)
    oy0 = int(bg_h * OVAL_TOP_F)
    ox1 = int(bg_w * OVAL_RIGHT_F)
    oy1 = int(bg_h * OVAL_BOTTOM_F)

    # Badge dimensions
    usable_w = (ox1 - ox0) - 2 * GRID_PAD_X - (GRID_COLS - 1) * BADGE_GAP_X
    usable_h = (oy1 - oy0) - 2 * GRID_PAD_Y - (GRID_ROWS - 1) * BADGE_GAP_Y
    badge_w  = usable_w // GRID_COLS
    badge_h  = usable_h // GRID_ROWS

    # Font — shrink until longest label fits within nameplate width
    max_label_w = int(badge_w * 0.92)
    font_size   = max(11, int(badge_h * 0.115))
    font        = None
    while font_size >= 10:
        try:
            f    = ImageFont.truetype(str(ASSET_DIR / "WaypointFont.otf"), font_size)
            bbox = f.getbbox("Wasteland Champion")
            if (bbox[2] - bbox[0]) <= max_label_w:
                font = f
                break
        except Exception:
            break
        font_size -= 1
    if font is None:
        font = ImageFont.load_default()

    # Build ordered slot list for this page:
    # Custom earned first (only earned ones show), then all 15 standard waypoints
    earned_custom = [wp for wp in custom_wps if wp["id"] in earned_ids]
    all_slots = [{"wp": wp, "is_custom": True} for wp in earned_custom] + \
                [{"wp": wp, "is_custom": False} for wp in WAYPOINTS]
    page_slots = all_slots[page * 15 : (page + 1) * 15]

    result = bg.copy()

    for idx, slot_info in enumerate(page_slots):
        wp         = slot_info["wp"]
        is_custom  = slot_info["is_custom"]
        row = idx // GRID_COLS
        col = idx % GRID_COLS

        bx = ox0 + GRID_PAD_X + col * (badge_w + BADGE_GAP_X)
        by = oy0 + GRID_PAD_Y + row * (badge_h + BADGE_GAP_Y)

        earned = wp["id"] in earned_ids

        # Choose correct slot frame
        src  = (slot_cst_src if slot_cst_src else slot_std) if is_custom else slot_std
        slot = src.resize((badge_w, badge_h), Image.LANCZOS).copy()

        if not earned:
            # Partially desaturate and darken for unearned standard slots
            rgb  = slot.convert("RGB")
            rgb  = ImageEnhance.Color(rgb).enhance(0.15)
            rgb  = ImageEnhance.Brightness(rgb).enhance(0.50)
            r, g, b = rgb.split()
            slot = Image.merge("RGBA", (r, g, b, slot.split()[3]))

        wp_path = WAYPOINT_IMG_DIR / f"{wp['id']}.png"
        if wp_path.exists():
            wp_img = Image.open(wp_path).convert("RGBA")
            rx0 = int(badge_w * SLOT_WP_LEFT)
            ry0 = int(badge_h * SLOT_WP_TOP)
            rx1 = int(badge_w * SLOT_WP_RIGHT)
            ry1 = int(badge_h * SLOT_WP_BOTTOM)
            wp_img = wp_img.resize((rx1 - rx0, ry1 - ry0), Image.LANCZOS)
            if not earned:
                # Desaturate and dim the waypoint art for unearned slots
                rgb_wp  = wp_img.convert("RGB")
                rgb_wp  = ImageEnhance.Color(rgb_wp).enhance(0.10)
                rgb_wp  = ImageEnhance.Brightness(rgb_wp).enhance(0.40)
                r2, g2, b2 = rgb_wp.split()
                wp_img  = Image.merge("RGBA", (r2, g2, b2, wp_img.split()[3]))
            slot.paste(wp_img, (rx0, ry0), wp_img.split()[3])

        # Nameplate text
        draw  = ImageDraw.Draw(slot)
        label = wp["name"] if earned else "???"
        color = (255, 215, 80, 255) if earned else (160, 160, 160, 255)

        bbox   = font.getbbox(label)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = max(2, (badge_w - tw) // 2)
        ty = int(badge_h * SLOT_TEXT_CENTER_Y) - th // 2
        draw.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 230))
        draw.text((tx,     ty),     label, font=font, fill=color)

        result.paste(slot, (bx, by), slot)

    buf = BytesIO()
    result.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf
