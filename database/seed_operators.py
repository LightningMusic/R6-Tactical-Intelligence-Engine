from database.db_manager import DatabaseManager

# --------------------------------------
# 1️⃣ Operators
# --------------------------------------
# Format: (name, side, ability_name, ability_max_count)
OPERATORS = [
    ("Ash", "attack", "Breaching Round", 3),
    ("Thermite", "attack", "Exothermic Charge", 3),
    ("Twitch", "attack", "Shock Drone", 2),
    ("Sledge", "attack", "Sledgehammer", 25),
    ("Montagne", "attack", "Le Roc Shield", 1),
    ("Mute", "defense", "Signal Disruptor", 4),
    ("Smoke", "defense", "Remote Gas Grenade", 3),
    ("Castle", "defense", "Armor Panels", 4),
    ("Pulse", "defense", "Heartbeat Sensor", 1),
    ("Rook", "defense", "Armor Pack", 1),
    ("Doc", "defense", "Stim Pistol", 3),
    ("Bandit", "defense", "Shock Wire", 4),
    ("Jäger", "defense", "Active Defense System", 3),
    ("Valkyrie", "defense", "Black Eye Cameras", 3),
    ("Caveira", "defense", "Silent Step", 1),
    ("Echo", "defense", "Yokai Drone", 3),
    ("Frost", "defense", "Welcome Mat", 3),
    ("Kapkan", "defense", "Entry Denial Device", 5),
    ("Lesion", "defense", "Gu Mine", 8),
    ("Ela", "defense", "Grzmot Mine", 3),
    ("Vigil", "defense", "ERC-7", 1),
    ("Maestro", "defense", "Evil Eye", 3),
    ("Alibi", "defense", "Prisma", 3),
    ("Clash", "defense", "CCE Shield", 1),
    ("Nomad", "attack", "Airjab Launcher", 3),
    ("Gridlock", "attack", "Trax Stingers", 3),
    ("Nøkk", "attack", "HEL Presence Reduction", 1),
    ("Amaru", "attack", "Garra Hook", 3),
    ("Goyo", "defense", "Volcán Canister", 4),
    ("Wamai", "defense", "Mag-NET System", 6),
    ("Kaid", "defense", "Electroclaw", 2),
    ("Melusi", "defense", "Banshee Sonic Defense", 3),
    ("Aruni", "defense", "Surya Gate", 3),
    ("Thunderbird", "defense", "Kóna Station", 3),
    ("Thorn", "defense", "Razorbloom Shell", 3),
    ("Azami", "defense", "Kiba Barrier", 5),
    ("Flores", "attack", "RCE-Ratero Charge", 4),
    ("Brava", "attack", "Kludge Drone", 2),
    ("Zero", "attack", "Argus Launcher", 4),
    ("Glaz", "attack", "Flip Sight", 1),
    ("Finka", "attack", "Adrenal Surge", 3),
    ("Lion", "attack", "EE-ONE-D Drone", 3),
    ("Ace", "attack", "S.E.L.M.A. Aqua Breacher", 3),
    ("Blackbeard", "attack", "H.U.L.L. Adaptable Shield", 3),
    ("Blitz", "attack", "G52 Tactical Shield", 0),
    ("Buck", "attack", "Skeleton Key", 4),
    ("Capitão", "attack", "TAC Crossbow", 4),
    ("Deimos", "attack", "Deathmark Tracker", 3),
    ("Dokkaebi", "attack", "Logic Bomb", 2),
    ("Fuze", "attack", "APM-6 Cluster Charge", 4),
    ("Grim", "attack", "Kawan Hive Launcher", 5),
    ("Hibana", "attack", "X-KAIROS", 18),
    ("Iana", "attack", "Gemini Replicator", 0),
    ("IQ", "attack", "RED Mk III 'Electronics Detector'", 0),
    ("Jackal", "attack", "Eyenox Model III", 3),
    ("Kali", "attack", "LV Explosive Lance", 3),
    ("Maverick", "attack", "D.I.Y. Breaching Torch", 0),
    ("Osa", "attack", "Talon-8 Clear Shield", 2),
    ("Ram", "attack", "BU-GI Auto Breacher", 4),
    ("Rauora", "attack", "D.O.M. Panel Launcher", 4),
    ("Sens", "attack", "R.O.U. Projector System", 7),
    ("Solid Snake", "attack", "Soliton Radar Mk. III", 0),
    ("Striker", "attack", "Extra Secondary Gadget", 0),
    ("Fenrir", "defense", "F-NATT Dread Mine", 5),
    ("Mira", "defense", "Black Mirror", 2),
    ("Mozzie","defense","Pest Launcher" ,3),
    ("Oryx","defense","Remah Dash" ,3),
    ("Sentry","defense","Extra Secondary Gadget", 0),
    ("Skopós","defense","V10 Pantheon Shells" ,2),
    ("Solis","defense","SPEC-IO Electro-Sensor" ,0),
    ("Tachanka","defense","Shumikha Grenade Launcher" ,10),
    ("Tubarão","defense","Zoto Canister" ,4),
    ("Warden","defense","Glance Smart Glasses" ,0),
    ("Denari", "defense", "B.O.L.T. Micro-Pylon", 2),
    ("Thatcher", "attack", "E.G.S. Disruptor", 6),
    ("Ying", "attack", "Candela", 3),
    ("Zofia", "attack", "KS79 LIFELINE", 4),
    ("Noor", "defense", "Horus Lance Launcher", 5),  # Y11S3 "Operation Split Fire" (2026-09-01)
]

# --------------------------------------
# 2️⃣ Gadgets (REAL secondary gadgets)
# --------------------------------------
# Format: (name, category)

GADGETS = [
    ("Frag Grenade", "explosive"),
    ("Smoke Grenade", "utility"),
    ("Claymore", "trap"),
    ("Stun Grenade", "utility"),
    ("Breach Charge", "breach"),
    ("Barbed Wire", "trap"),
    ("Deployable Shield", "defense"),
    ("Impact Grenade", "explosive"),
    ("Nitro Cell", "explosive"),
    ("Bulletproof Camera", "utility"),
    ("Proximity Alarm", "trap"),
    ("Impact EMP Grenade", "utility"),
    ("Hard Breach Charge", "breach"),
    ("Observation Blocker", "utility"),
]


# --------------------------------------
# 3️⃣ Operator → Gadget Options (FIXED)
# --------------------------------------
# Format: (operator_name, gadget_name, max_count)

OPERATOR_GADGET_OPTIONS = [

    # ATTACKERS
    ("Striker", "Breach Charge", 3),
    ("Striker", "Claymore", 2),
    ("Striker", "Impact EMP Grenade", 2),
    ("Striker", "Frag Grenade", 2),
    ("Striker", "Hard Breach Charge", 2),
    ("Striker", "Smoke Grenade", 2),
    ("Striker", "Stun Grenade", 2),

    ("Sledge", "Frag Grenade", 2),
    ("Sledge", "Stun Grenade", 2),
    ("Sledge", "Impact EMP Grenade", 2),

    ("Thatcher", "Claymore", 2),
    ("Thatcher", "Breach Charge", 3),

    ("Ash", "Breach Charge", 3),
    ("Ash", "Claymore", 2),

    ("Thermite", "Smoke Grenade", 2),
    ("Thermite", "Stun Grenade", 2),

    ("Twitch", "Claymore", 2),
    ("Twitch", "Smoke Grenade", 2),
    
    ("Montagne", "Impact EMP Grenade", 2),
    ("Montagne", "Smoke Grenade", 2),
    ("Montagne", "Hard Breach Charge", 2),

    ("Glaz", "Smoke Grenade", 2),
    ("Glaz", "Frag Grenade", 2),
    ("Glaz", "Claymore", 2),

    ("Fuze", "Breach Charge", 3),
    ("Fuze", "Hard Breach Charge", 2),
    ("Fuze", "Smoke Grenade", 2),

    ("Blitz", "Smoke Grenade", 2),
    ("Blitz", "Breach Charge", 3),

    ("IQ", "Frag Grenade", 2),
    ("IQ", "Claymore", 2),
    ("IQ", "Breach Charge", 3),

    ("Buck", "Stun Grenade", 2),
    ("Buck", "Claymore", 2),

    ("Blackbeard", "Frag Grenade", 2),
    ("Blackbeard", "Claymore", 2),

    ("Capitão", "Hard Breach Charge", 2),
    ("Capitão", "Impact EMP Grenade", 2),
    ("Capitão", "Claymore", 2),

    ("Hibana", "Breach Charge", 3),
    ("Hibana", "Stun Grenade", 2),
    ("Hibana", "Claymore", 2),

    ("Jackal", "Claymore", 2),
    ("Jackal", "Smoke Grenade", 2),

    ("Ying", "Hard Breach Charge", 2),
    ("Ying", "Smoke Grenade", 2),

    ("Zofia", "Hard Breach Charge", 2),
    ("Zofia", "Claymore", 2),

    ("Dokkaebi", "Smoke Grenade", 2),
    ("Dokkaebi", "Impact EMP Grenade", 2),
    ("Dokkaebi", "Stun Grenade", 2),

    ("Lion", "Frag Grenade", 2),
    ("Lion", "Stun Grenade", 2),
    ("Lion", "Claymore", 2),

    ("Finka", "Frag Grenade", 2),
    ("Finka", "Smoke Grenade", 2),
    ("Finka", "Stun Grenade", 2),

    ("Maverick", "Claymore", 2),
    ("Maverick", "Stun Grenade", 2),
    ("Maverick", "Frag Grenade", 2),

    ("Nomad", "Breach Charge", 3),
    ("Nomad", "Stun Grenade", 2),

    ("Gridlock", "Smoke Grenade", 2),
    ("Gridlock", "Frag Grenade", 2),
    ("Gridlock", "Impact EMP Grenade", 2),

    ("Nøkk", "Impact EMP Grenade", 2),
    ("Nøkk", "Hard Breach Charge", 2),
    ("Nøkk", "Frag Grenade", 2),

    ("Amaru", "Stun Grenade", 2),
    ("Amaru", "Hard Breach Charge", 2),

    ("Kali", "Claymore", 2),
    ("Kali", "Breach Charge", 3),
    ("Kali", "Smoke Grenade", 2),

    ("Iana", "Impact EMP Grenade", 2),
    ("Iana", "Smoke Grenade", 2),

    ("Ace", "Claymore", 2),
    ("Ace", "Stun Grenade", 2),

    ("Zero", "Hard Breach Charge", 2),
    ("Zero", "Claymore", 2),

    ("Flores", "Claymore", 2),
    ("Flores", "Stun Grenade", 2),

    ("Osa", "Claymore", 2),
    ("Osa", "Frag Grenade", 2),
    ("Osa", "Impact EMP Grenade", 2),

    ("Sens", "Frag Grenade", 2),
    ("Sens", "Hard Breach Charge", 2),
    ("Sens", "Claymore", 2),

    ("Grim", "Hard Breach Charge", 2),
    ("Grim", "Impact EMP Grenade", 2),
    ("Grim", "Claymore", 2),

    ("Brava", "Claymore", 2),
    ("Brava", "Smoke Grenade", 2),

    ("Ram", "Stun Grenade", 2),
    ("Ram", "Smoke Grenade", 2),

    ("Deimos", "Frag Grenade", 2),
    ("Deimos", "Hard Breach Charge", 2),

    ("Rauora", "Smoke Grenade", 2),
    ("Rauora", "Breach Charge", 3),

    ("Solid Snake", "Frag Grenade", 1),
    ("Solid Snake", "Stun Grenade", 1),
    ("Solid Snake", "Impact EMP Grenade", 1),
    ("Solid Snake", "Smoke Grenade", 1),
    ("Solid Snake", "Breach Charge", 1),



    # DEFENDERS
    ("Sentry", "Barbed Wire", 2),
    ("Sentry", "Bulletproof Camera", 1),
    ("Sentry", "Deployable Shield", 1),
    ("Sentry", "Observation Blocker", 3),
    ("Sentry", "Impact Grenade", 2),
    ("Sentry", "Nitro Cell", 1),
    ("Sentry", "Proximity Alarm", 2),

    ("Smoke", "Barbed Wire", 2),
    ("Smoke", "Proximity Alarm", 2),

    ("Mute", "Nitro Cell", 1),
    ("Mute", "Bulletproof Camera", 1),

    ("Castle", "Bulletproof Camera", 1),
    ("Castle", "Proximity Alarm", 2),

    ("Pulse", "Nitro Cell", 1),
    ("Pulse", "Deployable Shield", 1),
    ("Pulse", "Observation Blocker", 3),

    ("Doc", "Bulletproof Camera", 1),
    ("Doc", "Barbed Wire", 2),

    ("Rook", "Proximity Alarm", 2),
    ("Rook", "Impact Grenade", 2),
    ("Rook", "Nitro Cell", 1),

    ("Kapkan", "Bulletproof Camera", 1),
    ("Kapkan", "Barbed Wire", 2),

    ("Tachanka", "Barbed Wire", 2),
    ("Tachanka", "Deployable Shield", 1),
    ("Tachanka", "Proximity Alarm", 2),

    ("Jäger", "Bulletproof Camera", 1),
    ("Jäger", "Observation Blocker", 3),

    ("Bandit", "Barbed Wire", 2),
    ("Bandit", "Nitro Cell", 1),

    ("Frost", "Bulletproof Camera", 1),
    ("Frost", "Deployable Shield", 1),

    ("Valkyrie", "Impact Grenade", 2),
    ("Valkyrie", "Nitro Cell", 1),

    ("Caveira", "Impact Grenade", 2),
    ("Caveira", "Proximity Alarm", 2),
    ("Caveira", "Observation Blocker", 3),

    ("Echo", "Deployable Shield", 1),
    ("Echo", "Impact Grenade", 2),

    ("Mira", "Nitro Cell", 1),
    ("Mira", "Proximity Alarm", 2),

    ("Lesion", "Observation Blocker", 3),
    ("Lesion", "Bulletproof Camera", 1),

    ("Ela", "Deployable Shield", 1),
    ("Ela", "Impact Grenade", 2),
    ("Ela", "Barbed Wire", 2),

    ("Vigil", "Impact Grenade", 2),
    ("Vigil", "Bulletproof Camera", 1),

    ("Maestro", "Barbed Wire", 2),
    ("Maestro", "Impact Grenade", 2),
    ("Maestro", "Observation Blocker", 3),

    ("Alibi", "Proximity Alarm", 2),
    ("Alibi", "Observation Blocker", 3),

    ("Clash", "Barbed Wire", 2),
    ("Clash", "Impact Grenade", 2),

    ("Kaid", "Barbed Wire", 2),
    ("Kaid", "Observation Blocker", 3),
    ("Kaid", "Nitro Cell", 1),

    ("Mozzie", "Barbed Wire" ,2),
    ("Mozzie", "Nitro Cell" ,1),
    ("Mozzie", "Impact Grenade" ,2),

    ("Warden", "Deployable Shield" ,1),
    ("Warden", "Nitro Cell" ,1),
    ("Warden", "Observation Blocker" ,3),

    ("Goyo", "Impact Grenade" ,2),
    ("Goyo", "Proximity Alarm" ,2),
    ("Goyo", "Bulletproof Camera" ,1),

    ("Wamai", "Impact Grenade" ,2),
    ("Wamai", "Proximity Alarm" ,2),

    ("Oryx", "Barbed Wire" ,2),
    ("Oryx", "Proximity Alarm" ,2),

    ("Melusi", "Bulletproof Camera" ,1),
    ("Melusi", "Impact Grenade" ,2),

    ("Aruni", "Barbed Wire" ,2),
    ("Aruni", "Bulletproof Camera" ,1),

    ("Thunderbird", "Deployable Shield" ,1),
    ("Thunderbird", "Barbed Wire" ,2),
    ("Thunderbird", "Bulletproof Camera" ,1),

    ("Thorn", "Deployable Shield" ,1),
    ("Thorn", "Barbed Wire" ,2),

    ("Azami", "Barbed Wire" ,2),
    ("Azami", "Impact Grenade" ,2),

    ("Solis", "Proximity Alarm" ,2),
    ("Solis", "Impact Grenade" ,2),

    ("Fenrir", "Bulletproof Camera" ,1),
    ("Fenrir", "Observation Blocker" ,3),

    ("Tubarão", "Nitro Cell" ,1),
    ("Tubarão", "Proximity Alarm" ,2),

    ("Skopós", "Impact Grenade" ,2),
    ("Skopós", "Barbed Wire" ,2),

    ("Denari", "Observation Blocker" ,3),
    ("Denari", "Deployable Shield" ,1),

    ("Noor", "Deployable Shield", 1),
    ("Noor", "Barbed Wire", 2),

]
# --------------------------------------
# In-game IDs (what replays actually contain)
# --------------------------------------
# Baseline only. New IDs are learned from imported matches and new
# operators/gadgets from Ubisoft's operator pages -- see
# database/game_catalog.py and integration/ubisoft_catalog.py -- so this
# does not need editing when the game adds content.
#
# Operator IDs are cross-checked against r6-dissect's own table
# (r6-dissect/dissect/header.go). Recruit is deliberately absent: it
# appears on both sides, so it can never be learned as an operator.
OPERATOR_GAME_IDS: dict[int, str] = {
    92270642682: "Castle", 104189664704: "Aruni", 161289666230: "Kaid",
    174977508820: "Mozzie", 92270642708: "Pulse", 104189664390: "Ace",
    92270642214: "Echo", 378305069945: "Azami", 391752120891: "Solis",
    92270644215: "Capitão", 92270644189: "Zofia", 92270644267: "Dokkaebi",
    104189662920: "Warden", 92270644319: "Mira", 92270642344: "Sledge",
    104189664273: "Melusi", 92270642526: "Bandit", 92270642188: "Valkyrie",
    92270644059: "Rook", 92270641980: "Kapkan", 291191151607: "Zero",
    104189664038: "Iana", 92270642656: "Ash", 92270642136: "Blackbeard",
    288200867444: "Osa", 373711624351: "Thorn", 92270642604: "Jäger",
    104189663920: "Kali", 92270642760: "Thermite", 288200866821: "Brava",
    104189663607: "Amaru", 92270642292: "Ying", 92270642266: "Lesion",
    92270644007: "Doc", 104189661861: "Lion", 92270642032: "Fuze",
    92270642396: "Smoke", 92270644293: "Vigil", 92270642318: "Mute",
    104189663698: "Goyo", 104189663803: "Wamai", 92270644163: "Ela",
    92270644033: "Montagne", 104189663024: "Nøkk", 104189662071: "Alibi",
    104189661965: "Finka", 92270644241: "Caveira", 161289666248: "Nomad",
    288200867351: "Thunderbird", 384797789346: "Sens", 92270642578: "IQ",
    92270642539: "Blitz", 92270642240: "Hibana", 104189662384: "Maverick",
    328397386974: "Flores", 92270642474: "Buck", 92270644111: "Twitch",
    174977508808: "Gridlock", 92270642422: "Thatcher", 92270642084: "Glaz",
    92270644345: "Jackal", 374667788042: "Grim", 291437347686: "Tachanka",
    104189664155: "Oryx", 92270642500: "Frost", 104189662175: "Maestro",
    104189662280: "Clash", 288200867339: "Fenrir", 395943091136: "Ram",
    288200867549: "Tubarão", 374667787816: "Deimos", 409899350463: "Striker",
    409899350403: "Sentry", 386098331713: "Skopós", 386098331923: "Rauora",
    374667787937: "Denari", 444310693746: "Solid Snake",
    456757346397: "Noor",  # Y11S3, observed in a real replay
}

# Map "world IDs". Every rework ships under a new ID, hence several per
# map. Sources: r6-dissect's header.go plus IDs observed in real replays.
# Deliberately left out:
#   417890697769 -- r6-dissect says modernized Lair, the old hand-kept
#                   lookup said Clubhouse. Not asserted here; the import
#                   links it by r6-dissect's name unless the match's bomb
#                   sites were already seen on a different map, in which
#                   case it's flagged for a human instead of guessed.
#   108179795804.. -- a block from the old lookup that runs in alphabetical
#                   order in even steps (and names maps like "Donut");
#                   not plausible as real game IDs.
MAP_GAME_IDS: dict[int, str] = {
    355496559878: "Bank", 413779563590: "Bank",
    305979357167: "Border", 407987100456: "Border", 419662876236: "Border",
    419965653950: "Calypso Casino",
    259816839773: "Chalet", 407558616688: "Chalet",
    837214085: "Clubhouse", 407193663917: "Clubhouse", 422790217276: "Clubhouse",
    42090092951: "Coastline", 412551493246: "Coastline", 436375283234: "Coastline",
    2609221242: "Consulate", 379218689149: "Consulate", 418126004176: "Consulate",
    365284490964: "Emerald Plains",
    329867321446: "Favela",
    126196841359: "Fortress", 398899676157: "Fortress",
    127951053400: "Hereford Base",
    237873412352: "House",
    1378191338: "Kafe Dostoyevsky", 413845419788: "Kafe Dostoyevsky",
    1460220617: "Kanal",
    388073319671: "Lair",
    378595635123: "Nighthaven Labs", 418119057546: "Nighthaven Labs",
    231702797556: "Oregon", 409880628150: "Oregon", 434715462383: "Oregon",
    362605108559: "Outback", 415956890521: "Outback",
    2609218856: "Plane",
    276279025182: "Skyscraper", 423767322185: "Skyscraper",
    270063334510: "Stadium Bravo",
    199824623654: "Theme Park", 430788891316: "Theme Park",
    53627213396: "Tower",
    88107330328: "Villa", 409325881472: "Villa",
    1767965020: "Yacht",
}

MAPS = [
    "Bank",
    "Border",
    "Calypso Casino",
    "Chalet",
    "Clubhouse",
    "Coastline",
    "Consulate",
    "Emerald Plains",
    "Favela",
    "Fortress",
    "Hereford Base",
    "House",
    "Kafe Dostoyevsky",
    "Kanal",
    "Lair",
    "Nighthaven Labs",
    "Oregon",
    "Outback",
    "Plane",
    "Skyscraper",
    "Stadium Bravo",
    "Theme Park",
    "Tower",
    "Villa",
    "Yacht",
]
# --------------------------------------
# Seeder Function
# --------------------------------------

def seed_database(db: DatabaseManager):
    
    with db.get_connection() as conn:
        # Seed Operators. Ability *names* are only supplied for new rows:
        # after that they belong to the Ubisoft sync, which follows the
        # official names through reworks -- overwriting them here would
        # revert its changes on every launch. Charge counts aren't
        # published anywhere, so those stay owned by this list.
        for op in OPERATORS:
            conn.execute(
                """
                INSERT INTO operators (name, side, ability_name, ability_max_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    ability_max_count = excluded.ability_max_count
                """,
                op
            )

        # Seed Gadgets
        for g in GADGETS:
            conn.execute(
                """
                INSERT OR IGNORE INTO gadgets (name, category)
                VALUES (?, ?)
                """,
                g
            )

        # Seed Operator → Gadget Options (WITH DEBUG)
        print("\n=== DEBUG: Seeding Operator Gadget Mappings ===")

        success_count = 0
        fail_count = 0

        # Which gadgets an operator can carry now belongs to the Ubisoft
        # sync, which also REMOVES links when a rework changes a loadout.
        # So the seed only fills in operators that have no links at all
        # yet, and otherwise only refreshes counts on pairs that already
        # exist. Re-inserting every seed pair on each startup would undo
        # the sync's removals every time the app launched.
        operators_with_links = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT operator_id FROM operator_gadget_options"
            )
        }

        for mapping in OPERATOR_GADGET_OPTIONS:
            operator_name, gadget_name, max_count = mapping

            op_id = conn.execute(
                "SELECT operator_id FROM operators WHERE name = ?",
                (operator_name,),
            ).fetchone()

            gadget_id = conn.execute(
                "SELECT gadget_id FROM gadgets WHERE name = ?",
                (gadget_name,),
            ).fetchone()

            if not op_id:
                print(f"[ERROR] Missing operator in OPERATORS list: {operator_name}")
                fail_count += 1
                continue

            if not gadget_id:
                print(f"[ERROR] Gadget NOT FOUND: {gadget_name}")
                fail_count += 1
                continue
            if op_id is None:
                print(f"[ERROR] Missing operator in OPERATORS list: {operator_name}")
                fail_count += 1
                continue

            if gadget_id is None:
                print(f"[ERROR] Gadget NOT FOUND: {gadget_name}")
                fail_count += 1
                continue

            updated = conn.execute(
                """UPDATE operator_gadget_options SET max_count = ?
                   WHERE operator_id = ? AND gadget_id = ?""",
                (max_count, op_id[0], gadget_id[0]),
            ).rowcount
            if not updated and op_id[0] not in operators_with_links:
                conn.execute(
                    """INSERT INTO operator_gadget_options (operator_id, gadget_id, max_count)
                       VALUES (?, ?, ?)""",
                    (op_id[0], gadget_id[0], max_count),
                )

            success_count += 1

        print(f"\n=== Gadget Mapping Complete ===")
        print(f"SUCCESS: {success_count}")
        print(f"FAILED: {fail_count}\n")
        # --------------------------------------
        # 4️⃣ Default Team Players
        # --------------------------------------

        DEFAULT_TEAM_PLAYERS = [
            "Player1",
            "Player2",
            "Player3",
            "Player4",
            "Player5",
        ]


        # Seed Team Players
        for name in DEFAULT_TEAM_PLAYERS:
            conn.execute(
                """
                INSERT OR REPLACE INTO players (name, is_team_member)
                VALUES (?, 1)
                """,
                (name,)
            )
        for map_name in MAPS:
            conn.execute(
                """
                INSERT INTO maps (name, is_active_pool)
                VALUES (?, 1)
                ON CONFLICT(name) DO NOTHING
                """,
                (map_name,)
            )

        # In-game ID links. OR IGNORE: an ID already linked -- by an import,
        # or by someone naming a flagged map -- is never overwritten here.
        for game_id, op_name in OPERATOR_GAME_IDS.items():
            conn.execute(
                """INSERT OR IGNORE INTO operator_game_ids (game_id, operator_id, source)
                   SELECT ?, operator_id, 'seed' FROM operators WHERE name = ?""",
                (game_id, op_name),
            )
        for game_id, map_name in MAP_GAME_IDS.items():
            conn.execute(
                """INSERT OR IGNORE INTO map_game_ids (game_id, map_id, source)
                   SELECT ?, map_id, 'seed' FROM maps WHERE name = ?""",
                (game_id, map_name),
            )
        conn.commit()


if __name__ == "__main__":
    db = DatabaseManager()
    seed_database(db)
    print("Seeding complete: operators, gadgets, and mappings.")