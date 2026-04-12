"""
Construction Reference Database for Sam Axe
Real measurements, materials, and standards for residential construction.
Used to make image generation prompts architecturally accurate.
"""

# ── Kitchen Cabinets ──────────────────────────────────────────────────────────
CABINETS = {
    "base": {
        "height": "34.5 inches",
        "depth": "24 inches",
        "widths": "12, 15, 18, 21, 24, 27, 30, 33, 36, 42, 48 inches",
        "toe_kick": "4 inches high, 3 inches deep",
        "counter_height_with_counter": "36 inches from floor",
        "styles": ["shaker", "flat panel", "raised panel", "slab", "beadboard", "glass front"],
        "materials": ["solid wood", "plywood", "MDF", "particle board with laminate"],
        "colors": ["white", "gray", "navy", "black", "natural wood", "espresso", "cream"],
    },
    "wall": {
        "heights": "12, 24, 30, 36, 42 inches",
        "depth": "12 inches (always)",
        "widths": "12, 15, 18, 24, 30, 36 inches",
        "mount_height": "18 inches above countertop (54 inches from floor)",
        "styles": "same as base cabinets",
    },
    "tall_pantry": {
        "heights": "84, 90, 96 inches",
        "depth": "24 inches",
        "widths": "18, 24, 30, 36 inches",
    },
}

# ── Countertops ───────────────────────────────────────────────────────────────
COUNTERTOPS = {
    "thickness": "1.5 inches (standard for all materials)",
    "depth": "25 inches (24in cabinet + 1in front overhang)",
    "island_overhang_for_seating": "12-15 inches",
    "backsplash_height": "4 inches standard, 18 inches full tile",
    "materials": {
        "granite": {"cost": "$50-200/sqft installed", "look": "natural stone with veining and speckles"},
        "quartz": {"cost": "$60-150/sqft installed", "look": "engineered stone, uniform pattern"},
        "marble": {"cost": "$75-250/sqft installed", "look": "white/gray with dramatic veining"},
        "butcher_block": {"cost": "$40-100/sqft installed", "look": "warm wood grain, natural edges"},
        "laminate": {"cost": "$15-40/sqft installed", "look": "budget-friendly, many patterns"},
        "concrete": {"cost": "$65-135/sqft installed", "look": "industrial, matte finish"},
        "soapstone": {"cost": "$70-120/sqft installed", "look": "dark gray, soft matte feel"},
    },
}

# ── Flooring ──────────────────────────────────────────────────────────────────
FLOORING = {
    "hardwood": {
        "species": ["oak", "maple", "walnut", "hickory", "cherry", "ash", "bamboo"],
        "widths": "2.25, 3.25, 4, 5, 6, 7+ inches",
        "thickness": "3/4 inch solid, 3/8-1/2 inch engineered",
        "finishes": ["matte", "satin", "semi-gloss", "wire-brushed", "hand-scraped", "distressed"],
        "tones": ["natural", "golden", "honey", "medium brown", "dark walnut", "gray wash", "whitewash", "ebony"],
    },
    "tile": {
        "sizes": "6x6, 12x12, 12x24, 18x18, 24x24 inches",
        "types": ["ceramic", "porcelain", "natural stone", "mosaic"],
        "patterns": ["straight lay", "diagonal", "herringbone", "brick pattern", "basketweave"],
        "grout_width": "1/16 to 1/4 inch",
    },
    "vinyl_plank": {
        "widths": "5-9 inches",
        "lengths": "36-72 inches",
        "thickness": "2-8mm",
        "look": "mimics hardwood, waterproof",
    },
}

# ── Walls & Paint ─────────────────────────────────────────────────────────────
WALLS = {
    "drywall_thickness": "1/2 inch standard, 5/8 inch for ceilings and fire-rated",
    "standard_height": "8 feet (96 inches), 9 feet common in new construction",
    "paint_finishes": {
        "flat/matte": "hides imperfections, low sheen, ceilings and low-traffic walls",
        "eggshell": "slight sheen, easy to clean, most popular for living spaces",
        "satin": "moderate sheen, durable, good for kitchens/bathrooms/trim",
        "semi-gloss": "shiny, very durable, best for trim/doors/cabinets/bathrooms",
        "high-gloss": "very shiny, for accent and furniture, shows every imperfection",
    },
    "popular_colors": {
        "warm_gray": "Sherwin-Williams Agreeable Gray (SW 7029), Benjamin Moore Revere Pewter (HC-172)",
        "cool_gray": "Benjamin Moore Stonington Gray (HC-170), SW Mindful Gray (7016)",
        "warm_white": "Benjamin Moore White Dove (OC-17), SW Alabaster (7008)",
        "cool_white": "Benjamin Moore Chantilly Lace (OC-65), SW Extra White (7006)",
        "greige": "SW Accessible Beige (7036), BM Edgecomb Gray (HC-173)",
        "navy": "SW Naval (6244), BM Hale Navy (HC-154)",
        "sage_green": "SW Evergreen Fog (9130), BM October Mist (1495)",
        "black_accent": "SW Tricorn Black (6258), BM Black (2132-10)",
    },
}

# ── Standard Dimensions ──────────────────────────────────────────────────────
STANDARD_DIMS = {
    "doorway_width": "32-36 inches clear opening",
    "hallway_width": "36 inches minimum, 42-48 preferred",
    "stair_width": "36 inches minimum",
    "ceiling_height": "8 feet standard, 9 feet common new construction",
    "window_sill_height": "36-42 inches from floor",
    "electrical_outlet_height": "12-16 inches from floor",
    "light_switch_height": "48 inches from floor",
    "kitchen_island_clearance": "36-42 inches walkway around island",
    "bathroom_vanity_height": "30-36 inches",
    "toilet_rough_in": "12 inches from wall",
    "shower_head_height": "80 inches from floor",
}

# ── Nominal Lumber ────────────────────────────────────────────────────────────
LUMBER = {
    "2x4": {"actual": "1.5 x 3.5 inches", "use": "wall framing, non-load-bearing"},
    "2x6": {"actual": "1.5 x 5.5 inches", "use": "exterior walls, floor joists (short span)"},
    "2x8": {"actual": "1.5 x 7.25 inches", "use": "floor joists, headers"},
    "2x10": {"actual": "1.5 x 9.25 inches", "use": "floor joists, headers"},
    "2x12": {"actual": "1.5 x 11.25 inches", "use": "floor joists, ridge boards"},
    "4x4": {"actual": "3.5 x 3.5 inches", "use": "posts, deck supports"},
    "1x4": {"actual": "0.75 x 3.5 inches", "use": "trim, furring strips"},
}


def get_reference_for_edit(edit_request: str) -> str:
    """Given an edit request, return relevant construction reference data
    to inject into the image generation prompt for accuracy."""
    low = edit_request.lower()
    refs = []

    if any(k in low for k in ["cabinet", "cabinets", "kitchen"]):
        refs.append(
            f"CABINET REFERENCE: Base cabinets are {CABINETS['base']['height']} high, "
            f"{CABINETS['base']['depth']} deep. Wall cabinets are {CABINETS['wall']['depth']} deep, "
            f"mounted {CABINETS['wall']['mount_height']}. "
            f"Counter height with countertop = 36 inches from floor."
        )

    if any(k in low for k in ["counter", "countertop", "granite", "quartz", "marble"]):
        refs.append(
            f"COUNTER REFERENCE: Countertops are {COUNTERTOPS['thickness']} thick, "
            f"{COUNTERTOPS['depth']} deep (1in front overhang). "
            f"For islands with seating, add 12-15in overhang."
        )

    if any(k in low for k in ["floor", "hardwood", "tile", "vinyl", "wood floor"]):
        refs.append(
            f"FLOORING REFERENCE: Hardwood planks typically 3.25-5in wide. "
            f"Tile common sizes: 12x12, 12x24, 18x18 inches. "
            f"Popular tones: natural oak, honey, dark walnut, gray wash."
        )

    if any(k in low for k in ["paint", "color", "walls", "wall color"]):
        refs.append(
            f"PAINT REFERENCE: Popular finishes — eggshell for living spaces, "
            f"satin for kitchens/bathrooms. Popular colors: "
            f"warm gray (Agreeable Gray), warm white (White Dove), "
            f"navy (Naval/Hale Navy), sage green (Evergreen Fog)."
        )

    if any(k in low for k in ["island", "peninsula"]):
        refs.append(
            f"ISLAND REFERENCE: Standard island is 36in high (same as counter), "
            f"minimum 36-42in clearance around all sides for walkways. "
            f"Island with seating needs 12-15in countertop overhang, "
            f"base cabinets underneath at 24in depth."
        )

    if any(k in low for k in ["door", "doorway"]):
        refs.append(
            f"DOOR REFERENCE: Standard doorway is 32-36in clear opening, "
            f"80in tall. Door thickness is 1-3/8in (interior) or 1-3/4in (exterior)."
        )

    if not refs:
        refs.append(
            f"GENERAL: Residential ceiling height 8-9ft. "
            f"Standard wall stud spacing 16in on center. "
            f"Maintain realistic proportions and existing room geometry."
        )

    return " | ".join(refs)


# SD prompt suffix for architectural accuracy
ACCURACY_SUFFIX = (
    ", architecturally accurate proportions, "
    "realistic interior photography, "
    "proper perspective and vanishing points, "
    "correct scale relationships between objects, "
    "professional real estate photography style, "
    "natural lighting, photorealistic, 8k, sharp focus, "
    "same camera angle as original photo"
)

# SD negative prompt for architecture
ARCHITECTURE_NEGATIVE = (
    "distorted proportions, unrealistic scale, floating objects, "
    "impossible architecture, warped perspective, bent walls, "
    "wrong dimensions, oversized furniture, undersized doors, "
    "cartoon, anime, painting, illustration, sketch, drawing, "
    "blurry, low quality, watermark, text, nsfw, deformed, "
    "different camera angle, different viewpoint, rotated view, "
    "extra rooms, missing walls, wrong room shape"
)
