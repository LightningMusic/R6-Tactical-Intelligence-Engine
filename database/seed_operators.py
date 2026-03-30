from database.db_manager import DatabaseManager

# --------------------------------------
# 1️⃣ Operators
# --------------------------------------
# Format: (name, side, ability_name, ability_max_count)
OPERATORS = [
    ("Ash", "attack", "Breaching Round", 3),
    ("Thermite", "attack", "Exothermic Charge", 2),
    ("Twitch", "attack", "Shock Drone", 2),
    ("Sledge", "attack", "Sledgehammer", 25),
    ("Montagne", "attack", "Le Roc Shield", 1),
    ("Mute", "defense", "Signal Disruptor", 3),
    ("Smoke", "defense", "Remote Gas Grenade", 3),
    ("Castle", "defense", "Armor Panels", 3),
    ("Pulse", "defense", "Heartbeat Sensor", 1),
    ("Rook", "defense", "Armor Pack", 1),
    ("Doc", "defense", "Stim Pistol", 3),
    ("Bandit", "defense", "Shock Wire", 4),
    ("Jäger", "defense", "Active Defense System", 3),
    ("Valkyrie", "defense", "Black Eye Cameras", 3),
    ("Caveira", "defense", "Silent Step", 1),
    ("Echo", "defense", "Yokai Drone", 2),
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


]

# --------------------------------------
# Seeder Function
# --------------------------------------

def seed_database(db: DatabaseManager):
    
    with db.get_connection() as conn:
        # Seed Operators
        for op in OPERATORS:
            conn.execute(
                """
                INSERT OR IGNORE INTO operators (name, side, ability_name, ability_max_count)
                VALUES (?, ?, ?, ?)
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
            conn.execute(
                """
                INSERT INTO operator_gadget_options (operator_id, gadget_id, max_count)
                VALUES (?, ?, ?)
                ON CONFLICT(operator_id, gadget_id) DO UPDATE SET
                    max_count = excluded.max_count
                """,
                (op_id[0], gadget_id[0], max_count),
            )

            print(f"[OK] {operator_name} -> {gadget_name}")
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
        conn.commit()


if __name__ == "__main__":
    db = DatabaseManager()
    seed_database(db)
    print("Seeding complete: operators, gadgets, and mappings.")