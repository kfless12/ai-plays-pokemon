"""
context_builder.py — Assembles a ContextSnapshot from the actual state.json
produced by the Lua probe (state_agent.lua).

Design principles (Objective 1):
  - Assemble all available game state into a structured snapshot.
  - Resolve numeric IDs (species, moves, maps, items) to human-readable names.
  - Include: location, facing, money, badges, full party, battle info,
    nearby sprites/warps/signs, bag items, recent history.
  - Compact output: JSON for internal use.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Gen-1 lookup tables (index → name).  Index 0 = no pokemon / no move.
# Pokemon Red internal IDs are *not* National Dex order; they use the
# internal index stored at the species byte.  The table below maps the
# internal index used in RAM to the species name.
# ---------------------------------------------------------------------------

# fmt: off
SPECIES_BY_INTERNAL_ID: Dict[int, str] = {
    0: "(none)",
    1: "Rhydon", 2: "Kangaskhan", 3: "Nidoran♂", 4: "Clefairy",
    5: "Spearow", 6: "Voltorb", 7: "Nidoking", 8: "Slowbro",
    9: "Ivysaur", 10: "Exeggutor", 11: "Lickitung", 12: "Exeggcute",
    13: "Grimer", 14: "Gengar", 15: "Nidoran♀", 16: "Nidoqueen",
    17: "Cubone", 18: "Rhyhorn", 19: "Lapras", 20: "Arcanine",
    21: "Mew", 22: "Gyarados", 23: "Shellder", 24: "Tentacool",
    25: "Gastly", 26: "Scyther", 27: "Staryu", 28: "Blastoise",
    29: "Pinsir", 30: "Tangela", 33: "Growlithe", 34: "Onix",
    35: "Fearow", 36: "Pidgey", 37: "Slowpoke", 38: "Kadabra",
    39: "Graveler", 40: "Chansey", 41: "Machoke", 42: "Mr. Mime",
    43: "Hitmonlee", 44: "Hitmonchan", 45: "Arbok", 46: "Parasect",
    47: "Psyduck", 48: "Drowzee", 49: "Golem", 51: "Magmar",
    53: "Electabuzz", 54: "Magneton", 55: "Koffing", 57: "Mankey",
    58: "Seel", 59: "Diglett", 60: "Tauros", 64: "Farfetch'd",
    65: "Venonat", 66: "Dragonite", 70: "Doduo", 71: "Poliwag",
    72: "Jynx", 73: "Moltres", 74: "Articuno", 75: "Zapdos",
    76: "Ditto", 77: "Meowth", 78: "Krabby", 82: "Vulpix",
    83: "Ninetales", 84: "Pikachu", 85: "Raichu", 88: "Dratini",
    89: "Dragonair", 90: "Kabuto", 91: "Kabutops", 92: "Horsea",
    93: "Seadra", 96: "Sandshrew", 97: "Sandslash", 98: "Omanyte",
    99: "Omastar", 100: "Jigglypuff", 101: "Wigglytuff",
    102: "Eevee", 103: "Flareon", 104: "Jolteon", 105: "Vaporeon",
    106: "Machop", 107: "Zubat", 108: "Ekans", 109: "Paras",
    110: "Poliwhirl", 111: "Poliwrath", 112: "Weedle", 113: "Kakuna",
    114: "Beedrill", 116: "Dodrio", 117: "Primeape", 118: "Dugtrio",
    119: "Venomoth", 120: "Dewgong", 123: "Caterpie", 124: "Metapod",
    125: "Butterfree", 126: "Machamp", 128: "Golduck", 129: "Hypno",
    130: "Golbat", 131: "Mewtwo", 132: "Snorlax", 133: "Magikarp",
    136: "Muk", 138: "Kingler", 139: "Cloyster", 141: "Electrode",
    142: "Clefable", 143: "Weezing", 144: "Persian", 145: "Marowak",
    147: "Haunter", 148: "Abra", 149: "Alakazam", 150: "Pidgeotto",
    151: "Pidgeot", 152: "Starmie", 153: "Bulbasaur", 154: "Venusaur",
    155: "Tentacruel", 157: "Goldeen", 158: "Seaking",
    163: "Ponyta", 164: "Rapidash", 165: "Rattata", 166: "Raticate",
    167: "Nidorino", 168: "Nidorina", 169: "Geodude",
    170: "Porygon", 171: "Aerodactyl", 173: "Magnemite",
    176: "Charmander", 177: "Squirtle", 178: "Charmeleon",
    179: "Wartortle", 180: "Charizard", 185: "Oddish",
    186: "Gloom", 187: "Vileplume", 188: "Bellsprout",
    189: "Weepinbell", 190: "Victreebel",
}

MOVE_BY_ID: Dict[int, str] = {
    0: "(none)",
    1: "Pound", 2: "Karate Chop", 3: "Double Slap", 4: "Comet Punch",
    5: "Mega Punch", 6: "Pay Day", 7: "Fire Punch", 8: "Ice Punch",
    9: "Thunder Punch", 10: "Scratch", 11: "Vice Grip", 12: "Guillotine",
    13: "Razor Wind", 14: "Swords Dance", 15: "Cut", 16: "Gust",
    17: "Wing Attack", 18: "Whirlwind", 19: "Fly", 20: "Bind",
    21: "Slam", 22: "Vine Whip", 23: "Stomp", 24: "Double Kick",
    25: "Mega Kick", 26: "Jump Kick", 27: "Rolling Kick",
    28: "Sand Attack", 29: "Headbutt", 30: "Horn Attack",
    31: "Fury Attack", 32: "Horn Drill", 33: "Tackle", 34: "Body Slam",
    35: "Wrap", 36: "Take Down", 37: "Thrash", 38: "Double-Edge",
    39: "Tail Whip", 40: "Poison Sting", 41: "Twineedle",
    42: "Pin Missile", 43: "Leer", 44: "Bite", 45: "Growl",
    46: "Roar", 47: "Sing", 48: "Supersonic", 49: "Sonic Boom",
    50: "Disable", 51: "Acid", 52: "Ember", 53: "Flamethrower",
    54: "Mist", 55: "Water Gun", 56: "Hydro Pump", 57: "Surf",
    58: "Ice Beam", 59: "Blizzard", 60: "Psybeam", 61: "Bubble Beam",
    62: "Aurora Beam", 63: "Hyper Beam", 64: "Peck", 65: "Drill Peck",
    66: "Submission", 67: "Low Kick", 68: "Counter", 69: "Seismic Toss",
    70: "Strength", 71: "Absorb", 72: "Mega Drain", 73: "Leech Seed",
    74: "Growth", 75: "Razor Leaf", 76: "Solar Beam", 77: "Poison Powder",
    78: "Stun Spore", 79: "Sleep Powder", 80: "Petal Dance",
    81: "String Shot", 82: "Dragon Rage", 83: "Fire Spin",
    84: "Thunder Shock", 85: "Thunderbolt", 86: "Thunder Wave",
    87: "Thunder", 88: "Rock Throw", 89: "Earthquake", 90: "Fissure",
    91: "Dig", 92: "Toxic", 93: "Confusion", 94: "Psychic",
    95: "Hypnosis", 96: "Meditate", 97: "Agility", 98: "Quick Attack",
    99: "Rage", 100: "Teleport", 101: "Night Shade", 102: "Mimic",
    103: "Screech", 104: "Double Team", 105: "Recover", 106: "Harden",
    107: "Minimize", 108: "Smokescreen", 109: "Confuse Ray",
    110: "Withdraw", 111: "Defense Curl", 112: "Barrier",
    113: "Light Screen", 114: "Haze", 115: "Reflect", 116: "Focus Energy",
    117: "Bide", 118: "Metronome", 119: "Mirror Move", 120: "Self-Destruct",
    121: "Egg Bomb", 122: "Lick", 123: "Smog", 124: "Sludge",
    125: "Bone Club", 126: "Fire Blast", 127: "Waterfall", 128: "Clamp",
    129: "Swift", 130: "Skull Bash", 131: "Spike Cannon", 132: "Constrict",
    133: "Amnesia", 134: "Kinesis", 135: "Soft-Boiled", 136: "High Jump Kick",
    137: "Glare", 138: "Dream Eater", 139: "Poison Gas", 140: "Barrage",
    141: "Leech Life", 142: "Lovely Kiss", 143: "Sky Attack",
    144: "Transform", 145: "Bubble", 146: "Dizzy Punch",
    147: "Spore", 148: "Flash", 149: "Psywave", 150: "Splash",
    151: "Acid Armor", 152: "Crabhammer", 153: "Explosion",
    154: "Fury Swipes", 155: "Bonemerang", 156: "Rest", 157: "Rock Slide",
    158: "Hyper Fang", 159: "Sharpen", 160: "Conversion", 161: "Tri Attack",
    162: "Super Fang", 163: "Slash", 164: "Substitute", 165: "Struggle",
}

ITEM_BY_ID: Dict[int, str] = {
    0: "(none)",
    1: "Master Ball", 2: "Ultra Ball", 3: "Great Ball", 4: "Poke Ball",
    5: "Town Map", 6: "Bicycle", 7: "?????", 8: "Safari Ball",
    9: "Pokedex", 10: "Moon Stone", 11: "Antidote", 12: "Burn Heal",
    13: "Ice Heal", 14: "Awakening", 15: "Parlyz Heal", 16: "Full Restore",
    17: "Max Potion", 18: "Hyper Potion", 19: "Super Potion", 20: "Potion",
    29: "Escape Rope", 30: "Repel", 31: "Old Amber", 32: "Fire Stone",
    33: "Thunder Stone", 34: "Water Stone", 35: "HP Up", 36: "Protein",
    37: "Iron", 38: "Carbos", 39: "Calcium", 40: "Rare Candy",
    41: "Dome Fossil", 42: "Helix Fossil", 43: "Secret Key",
    48: "Bike Voucher", 49: "X Accuracy", 50: "Leaf Stone",
    51: "Card Key", 52: "Nugget", 53: "PP Up",
    54: "Poke Doll", 55: "Full Heal", 56: "Revive", 57: "Max Revive",
    58: "Guard Spec", 59: "Super Repel", 60: "Max Repel",
    61: "Dire Hit", 62: "Coin", 63: "Fresh Water",
    64: "Soda Pop", 65: "Lemonade", 66: "S.S. Ticket",
    67: "Gold Teeth", 68: "X Attack", 69: "X Defend",
    70: "X Speed", 71: "X Special", 72: "Coin Case",
    73: "Oaks Parcel", 74: "Itemfinder", 75: "Silph Scope",
    76: "Poke Flute", 77: "Lift Key", 78: "Exp. All",
    79: "Old Rod", 80: "Good Rod", 81: "Super Rod",
    82: "PP Up", 83: "Ether", 84: "Max Ether", 85: "Elixir", 86: "Max Elixir",
    196: "HM01", 197: "HM02", 198: "HM03", 199: "HM04", 200: "HM05",
    201: "TM01", 202: "TM02", 203: "TM03", 204: "TM04", 205: "TM05",
    206: "TM06", 207: "TM07", 208: "TM08", 209: "TM09", 210: "TM10",
}
# fmt: on

MAP_NAMES: Dict[int, str] = {
    0: "Pallet Town",
    1: "Viridian City",
    2: "Pewter City",
    3: "Cerulean City",
    4: "Lavender Town",
    5: "Vermilion City",
    6: "Celadon City",
    7: "Fuchsia City",
    8: "Cinnabar Island",
    9: "Indigo Plateau",
    10: "Saffron City",
    12: "Route 1",
    13: "Route 2",
    14: "Route 3",
    15: "Route 4",
    33: "Route 22",
    37: "Player's House 1F",
    38: "Player's House 2F",
    39: "Rival's House",
    40: "Oak's Lab",
    41: "Viridian Pokemon Center",
    42: "Viridian Pokemart",
    43: "Viridian School",
    44: "Viridian House",
    49: "Pewter Museum 1F",
    50: "Pewter Museum 2F",
    51: "Pewter Gym",
    52: "Pewter House 1",
    53: "Pewter Pokemart",
    54: "Pewter House 2",
    58: "Cerulean House 1",
    59: "Cerulean Pokemart",
    60: "Cerulean Pokemon Center",
    61: "Cerulean Gym",
    62: "Cerulean Bike Shop",
}


# ---------------------------------------------------------------------------
# Data classes for the context snapshot
# ---------------------------------------------------------------------------

@dataclass
class PartyMember:
    """One Pokemon in the player's party."""
    species: str
    level: int
    hp: int
    max_hp: int
    status: str              # "OK", "PSN", "BRN", "FRZ", "PAR", "SLP"
    moves: List[str]         # move names
    pp: List[int]            # PP remaining per move
    is_lead: bool = False


@dataclass
class BattleInfo:
    """Battle state."""
    opponent_species: str
    opponent_level: int
    opponent_hp: int
    opponent_max_hp: int
    opponent_status: str
    own_species: str
    own_level: int
    own_hp: int
    own_max_hp: int
    own_moves: List[str]
    own_pp: List[int]
    battle_menu: str           # "FIGHT", "PKMN", "ITEM", "RUN", "NONE"


@dataclass
class BagItem:
    """An item in the player's bag."""
    name: str
    item_id: int
    quantity: int


@dataclass
class NearbySprite:
    """A sprite (NPC/object) visible on the current map."""
    sprite_id: int
    picture_id: int
    screen_x: int
    screen_y: int
    facing: str


@dataclass
class WarpPoint:
    """A warp/door/stairs on the current map."""
    x: int
    y: int
    dest_map: int
    dest_map_name: str


@dataclass
class SignPost:
    """A sign on the current map."""
    x: int
    y: int
    text_id: int


@dataclass
class ContextSnapshot:
    """Complete context snapshot for one decision point."""
    timestamp: float = field(default_factory=time.time)
    # Location
    map_name: str = "Unknown"
    map_id: int = 0
    x: int = 0
    y: int = 0
    facing: str = "unknown"
    # Player info
    money: int = 0
    badges: int = 0
    play_time: str = "0:00"
    # UI state
    phase: str = "UNKNOWN"          # TITLE_SCREEN, INTRO_SCRIPT, PLAYING
    textbox_active: bool = False
    menu_active: bool = False
    input_ignored: bool = False
    screen_text: str = ""           # Decoded text visible on screen (from tile map)
    # Task context (Objective 2)
    task_lines: list = field(default_factory=list)  # Lines describing current task/subtask for prompt
    # Party
    party_count: int = 0
    party: List[PartyMember] = field(default_factory=list)
    # Battle (None if not in battle)
    battle: Optional[BattleInfo] = None
    # Bag
    bag: List[BagItem] = field(default_factory=list)
    # World: nearby interactables
    sprites: List[NearbySprite] = field(default_factory=list)
    warps: List[WarpPoint] = field(default_factory=list)
    signs: List[SignPost] = field(default_factory=list)
    # Recent action/outcome history (bounded)
    recent_history: List[str] = field(default_factory=list)
    # Available high-level actions for the LLM to choose from
    available_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=None)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _species_name(internal_id: int) -> str:
    """Resolve internal species ID to name."""
    return SPECIES_BY_INTERNAL_ID.get(internal_id, f"Pokemon#{internal_id}")


def _move_name(move_id: int) -> str:
    """Resolve move ID to name."""
    if move_id == 0:
        return ""
    return MOVE_BY_ID.get(move_id, f"Move#{move_id}")


def _item_name(item_id: int) -> str:
    """Resolve item ID to name."""
    if item_id == 0:
        return ""
    return ITEM_BY_ID.get(item_id, f"Item#{item_id}")


def _map_name(map_id: int) -> str:
    """Resolve map ID to human-readable name."""
    return MAP_NAMES.get(map_id, f"Map {map_id}")


# ---------------------------------------------------------------------------
# ContextBuilder
# ---------------------------------------------------------------------------

class ContextBuilder:
    """
    Builds a ContextSnapshot from the raw state.json produced by state_agent.lua.

    The state.json schema (from Lua v3):
      {
        "frame": int,
        "map": int,
        "x": int,
        "y": int,
        "facing": str,
        "money": int,
        "badges": int,
        "badge_bits": int,
        "battleType": int,
        "ui": { ... },
        "party": [ { species, lvl, hp, maxhp, status, moves, pps }, ... ],
        "slot1": { lvl, hp, maxhp, moves, pps },  // backward compat
        "enemy": { species, lvl, hp, maxhp, status } | null,
        "bag": [ { id, qty }, ... ],
        "sprites": [ { sprite_id, picture_id, screen_y, screen_x, facing }, ... ],
        "warps": [ { y, x, dest_map }, ... ],
        "signs": [ { y, x, text_id }, ... ],
        "wram_slice": "hex...",
        "last_action": "..."
      }
    """

    def __init__(self, state_path: str = "/home/kfless/pokemon_ai/state.json"):
        self.state_path = Path(state_path)
        self._action_history: List[str] = []
        self._max_history = 20

    # -- History management (short-term memory buffer) --

    def record_action(self, action: str, outcome: str = ""):
        """Append an action+outcome to the rolling history buffer."""
        entry = action
        if outcome:
            entry = f"{action} → {outcome}"
        self._action_history.append(entry)
        if len(self._action_history) > self._max_history:
            self._action_history = self._action_history[-self._max_history:]

    def get_recent_history(self, n: int = 10) -> List[str]:
        return self._action_history[-n:]

    def clear_history(self):
        self._action_history.clear()

    # -- State loading --

    def load_raw_state(self) -> Dict[str, Any]:
        """Load and return the raw state.json dict."""
        try:
            text = self.state_path.read_text()
            if not text.strip():
                return {}
            return json.loads(text)
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return {}

    # -- Snapshot assembly --

    def _build_party(self, state: Dict[str, Any]) -> List[PartyMember]:
        """Build party member list from state."""
        ui = state.get("ui", {})
        party_count = ui.get("party_count", 0)
        members: List[PartyMember] = []

        if party_count == 0:
            return members

        # Use the new "party" array if available (Lua v3)
        party_array = state.get("party", [])
        if party_array:
            for i, mon in enumerate(party_array):
                species_id = mon.get("species", 0)
                raw_moves = mon.get("moves", [0, 0, 0, 0])
                raw_pp = mon.get("pps", [0, 0, 0, 0])
                moves = [_move_name(m) for m in raw_moves]
                move_names = [m for m in moves if m]
                pp_values = [raw_pp[j] for j in range(len(raw_moves)) if moves[j]]

                members.append(PartyMember(
                    species=_species_name(species_id),
                    level=mon.get("lvl", 0),
                    hp=mon.get("hp", 0),
                    max_hp=mon.get("maxhp", 0),
                    status=mon.get("status", "OK"),
                    moves=move_names,
                    pp=pp_values,
                    is_lead=(i == 0),
                ))
            return members

        # Fallback: use slot1 (Lua v2 backward compat)
        slot1 = state.get("slot1", {})
        lvl = slot1.get("lvl", 0)
        hp = slot1.get("hp", 0)
        maxhp = slot1.get("maxhp", 0)
        raw_moves = slot1.get("moves", [0, 0, 0, 0])
        raw_pp = slot1.get("pps", [0, 0, 0, 0])

        moves = [_move_name(m) for m in raw_moves]
        move_names = [m for m in moves if m]
        pp_values = [raw_pp[i] for i in range(len(raw_moves)) if moves[i]]

        if lvl > 0 or maxhp > 0:
            members.append(PartyMember(
                species="Lead Pokemon",
                level=lvl,
                hp=hp,
                max_hp=maxhp,
                status="OK",
                moves=move_names,
                pp=pp_values,
                is_lead=True,
            ))

        return members

    def _build_battle(self, state: Dict[str, Any]) -> Optional[BattleInfo]:
        """Build battle info if currently in battle."""
        ui = state.get("ui", {})
        if not ui.get("in_battle", False):
            return None

        enemy = state.get("enemy") or {}
        # Get own Pokemon from party[0] or slot1
        party_array = state.get("party", [])
        if party_array:
            own = party_array[0]
            own_species = _species_name(own.get("species", 0))
            own_lvl = own.get("lvl", 0)
            own_hp = own.get("hp", 0)
            own_maxhp = own.get("maxhp", 0)
            raw_moves = own.get("moves", [0, 0, 0, 0])
            raw_pp = own.get("pps", [0, 0, 0, 0])
        else:
            slot1 = state.get("slot1", {})
            own_species = "Lead Pokemon"
            own_lvl = slot1.get("lvl", 0)
            own_hp = slot1.get("hp", 0)
            own_maxhp = slot1.get("maxhp", 0)
            raw_moves = slot1.get("moves", [0, 0, 0, 0])
            raw_pp = slot1.get("pps", [0, 0, 0, 0])

        moves = [_move_name(m) for m in raw_moves]
        move_names = [m for m in moves if m]
        pp_values = [raw_pp[i] for i in range(len(raw_moves)) if moves[i]]

        battle_menu = ui.get("battle_menu_selection", "NONE")

        return BattleInfo(
            opponent_species=_species_name(enemy.get("species", 0)),
            opponent_level=enemy.get("lvl", 0),
            opponent_hp=enemy.get("hp", 0),
            opponent_max_hp=enemy.get("maxhp", 0),
            opponent_status=enemy.get("status", "OK"),
            own_species=own_species,
            own_level=own_lvl,
            own_hp=own_hp,
            own_max_hp=own_maxhp,
            own_moves=move_names,
            own_pp=pp_values,
            battle_menu=battle_menu,
        )

    def _build_bag(self, state: Dict[str, Any]) -> List[BagItem]:
        """Build bag item list from state."""
        raw_bag = state.get("bag", [])
        items: List[BagItem] = []
        for entry in raw_bag:
            item_id = entry.get("id", 0)
            qty = entry.get("qty", 0)
            if item_id > 0:
                items.append(BagItem(
                    name=_item_name(item_id),
                    item_id=item_id,
                    quantity=qty,
                ))
        return items

    def _build_sprites(self, state: Dict[str, Any]) -> List[NearbySprite]:
        """Build nearby sprite list from state."""
        raw = state.get("sprites", [])
        sprites: List[NearbySprite] = []
        for sp in raw:
            sprites.append(NearbySprite(
                sprite_id=sp.get("sprite_id", 0),
                picture_id=sp.get("picture_id", 0),
                screen_x=sp.get("screen_x", 0),
                screen_y=sp.get("screen_y", 0),
                facing=sp.get("facing", "unknown"),
            ))
        return sprites

    def _build_warps(self, state: Dict[str, Any]) -> List[WarpPoint]:
        """Build warp point list from state."""
        raw = state.get("warps", [])
        warps: List[WarpPoint] = []
        for w in raw:
            dest_map = w.get("dest_map", 0)
            warps.append(WarpPoint(
                x=w.get("x", 0),
                y=w.get("y", 0),
                dest_map=dest_map,
                dest_map_name=_map_name(dest_map),
            ))
        return warps

    def _build_signs(self, state: Dict[str, Any]) -> List[SignPost]:
        """Build sign list from state."""
        raw = state.get("signs", [])
        signs: List[SignPost] = []
        for s in raw:
            signs.append(SignPost(
                x=s.get("x", 0),
                y=s.get("y", 0),
                text_id=s.get("text_id", 0),
            ))
        return signs

    def build(
        self,
        state: Optional[Dict[str, Any]] = None,
        available_actions: Optional[List[str]] = None,
    ) -> ContextSnapshot:
        """
        Build a complete ContextSnapshot.

        Args:
            state: Raw state dict. If None, loads from state_path.
            available_actions: High-level actions the LLM can choose from.

        Returns:
            A ContextSnapshot with all available information assembled.
        """
        if state is None:
            state = self.load_raw_state()

        if not state:
            return ContextSnapshot()

        ui = state.get("ui", {})
        map_id = state.get("map", 0)

        snapshot = ContextSnapshot(
            # Location
            map_name=_map_name(map_id),
            map_id=map_id,
            x=state.get("x", 0),
            y=state.get("y", 0),
            facing=state.get("facing", "unknown"),
            # Player info
            money=state.get("money", 0),
            badges=state.get("badges", 0),
            play_time=state.get("play_time", "0:00"),
            # UI state
            phase=ui.get("startup_phase", "UNKNOWN"),
            textbox_active=ui.get("textbox_active", False),
            menu_active=ui.get("menu_active", False),
            input_ignored=ui.get("joypad_disabled", ui.get("input_ignored", False)),
            screen_text=ui.get("screen_text", ""),
            # Party
            party_count=ui.get("party_count", 0),
            party=self._build_party(state),
            # Battle
            battle=self._build_battle(state),
            # Bag
            bag=self._build_bag(state),
            # World
            sprites=self._build_sprites(state),
            warps=self._build_warps(state),
            signs=self._build_signs(state),
            # History
            recent_history=self.get_recent_history(10),
            # Actions
            available_actions=available_actions or [],
        )

        return snapshot


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def build_context_snapshot(
    state_path: str = "/home/kfless/pokemon_ai/state.json",
    state: Optional[Dict[str, Any]] = None,
    available_actions: Optional[List[str]] = None,
    action_history: Optional[List[str]] = None,
) -> ContextSnapshot:
    """
    One-shot convenience: build a ContextSnapshot without managing a
    ContextBuilder instance.  Useful for testing or simple integrations.
    """
    builder = ContextBuilder(state_path=state_path)
    if action_history:
        for a in action_history:
            builder.record_action(a)
    return builder.build(
        state=state,
        available_actions=available_actions,
    )
