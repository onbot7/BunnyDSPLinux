import os
import json
import tanchjim_bunny

def load_all_presets():
    catalog = [
        {
            "category": "Official & Stock Targets",
            "items": []
        },
        {
            "category": "Reference Targets",
            "items": []
        },
        {
            "category": "Gaming & Tactical Audio",
            "items": []
        },
        {
            "category": "Community Audiophile Signatures",
            "items": []
        },
        {
            "category": "IEM Emulations & Device Tunings",
            "items": []
        }
    ]

    cat_map = {c["category"]: c["items"] for c in catalog}

    cat_map["Official & Stock Targets"].append({
        "id": "tanchjim_bunny_dsp",
        "name": "Tanchjim Bunny DSP",
        "desc": "Official signature sound: controlled sub-bass warmth, natural midrange clarity, and airy treble.",
        "pregain": -2.5,
        "bands": [
            {"freq": 75, "gain": 3.0, "q": 0.80, "type": "LSQ"},
            {"freq": 180, "gain": 1.0, "q": 1.00, "type": "PK"},
            {"freq": 800, "gain": 0.0, "q": 1.00, "type": "PK"},
            {"freq": 2800, "gain": 2.5, "q": 1.80, "type": "PK"},
            {"freq": 4500, "gain": 0.5, "q": 2.00, "type": "PK"},
            {"freq": 6500, "gain": -2.0, "q": 3.00, "type": "PK"},
            {"freq": 9000, "gain": 1.0, "q": 2.00, "type": "PK"},
            {"freq": 12500, "gain": 2.0, "q": 1.00, "type": "HSQ"},
        ]
    })

    cat_map["Reference Targets"].append({
        "id": "flat",
        "name": "Flat Neutral (0 dB Bypass)",
        "desc": "Resets all 8 hardware biquad filters to zero gain for uncolored hardware passthrough.",
        "pregain": 0.0,
        "bands": [
            {"freq": 80, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 150, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 500, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 1000, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 2800, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 4500, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 5500, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 12000, "gain": 0.0, "q": 1.0, "type": "HSQ"},
        ]
    })

    # Load all files from presets/
    presets_dir = os.path.join(tanchjim_bunny.BASE_DIR, "presets")
    if os.path.isdir(presets_dir):
        for fn in sorted(os.listdir(presets_dir)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(presets_dir, fn)
            try:
                with open(path, "r") as f:
                    d = json.load(f)
            except Exception:
                continue

            pid = fn[:-5]
            raw_name = d.get("name") or pid
            desc = d.get("description") or d.get("desc") or ""
            pregain = float(d.get("pregain", 0.0))
            bands = d.get("bands", [])

            # Ensure 8 bands format
            formatted_bands = []
            for i in range(8):
                if i < len(bands):
                    b = bands[i]
                    formatted_bands.append({
                        "freq": int(b.get("freq", 1000)),
                        "gain": float(b.get("gain", 0.0)),
                        "q": float(b.get("q", 1.0)),
                        "type": b.get("type", "PK")
                    })
                else:
                    formatted_bands.append({
                        "freq": 1000,
                        "gain": 0.0,
                        "q": 1.0,
                        "type": "PK"
                    })

            if fn == "stock_backup.json":
                cat = "Official & Stock Targets"
                raw_name = "Factory Calibration Backup"
                desc = "Original factory calibration backup with 5 active hardware PEQ filters."
            elif fn.startswith("stock_"):
                cat = "Official & Stock Targets"
            elif fn.startswith("game_"):
                cat = "Gaming & Tactical Audio"
            elif fn.startswith("community_"):
                cat = "Community Audiophile Signatures"
            elif fn.startswith("iem_"):
                cat = "IEM Emulations & Device Tunings"
            elif fn in ("harman_iem.json", "bass_boost.json", "treble_tame.json", "vocal_forward.json"):
                cat = "Reference Targets"
            else:
                cat = "Official & Stock Targets"

            cat_map[cat].append({
                "id": pid,
                "name": raw_name,
                "desc": desc,
                "pregain": pregain,
                "bands": formatted_bands
            })

    # Return only categories that have items
    return [c for c in catalog if c["items"]]

PRESETS_CATALOG = load_all_presets()
