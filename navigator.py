"""
navigator.py — High-level navigation system for Pokemon Red AI

Provides the LLM with simple commands like:
  - "GO_STAIRS"     → walks to the stairs in the bedroom
  - "EXIT_HOUSE"    → walks out of the house
  - "GO_SOUTH"      → walks south on the current map
  - "GO_OAKS_LAB"   → walks to Oak's lab entrance

The navigator handles:
  - Pathfinding via waypoints (predefined per map)
  - Step-by-step movement with collision detection (position unchanged = blocked)
  - Automatic door/stair entry
  - Queuing multiple movement steps

The LLM picks a high-level goal, the navigator executes it.
"""

from dataclasses import dataclass, field


@dataclass
class Waypoint:
    """A target position on a map, optionally with a warp/transition."""
    x: int
    y: int
    name: str
    # After reaching this waypoint, press this direction to trigger warp/door
    enter_dir: str | None = None
    # Expected destination map after warping
    dest_map: int | None = None


# ============================================================
# Map data: waypoints and known points of interest
# ============================================================
# Coordinate system: x increases going RIGHT, y increases going DOWN
# Movement: UP = y-1, DOWN = y+1, LEFT = x-1, RIGHT = x+1

MAP_WAYPOINTS: dict[int, dict[str, Waypoint]] = {
    # Map 38: Player's bedroom (2F)
    38: {
        "STAIRS": Waypoint(x=7, y=1, name="Stairs down", enter_dir=None, dest_map=37),
    },
    # Map 37: Player's house (1F)
    37: {
        "EXIT": Waypoint(x=2, y=7, name="Front door", enter_dir="DOWN", dest_map=0),
        "STAIRS_UP": Waypoint(x=7, y=1, name="Stairs up", enter_dir=None, dest_map=38),
    },
    # Map 0: Pallet Town
    0: {
        "PLAYERS_HOUSE": Waypoint(x=5, y=5, name="Player's house", enter_dir="UP", dest_map=37),
        "OAKS_LAB": Waypoint(x=12, y=11, name="Oak's Lab", enter_dir="UP", dest_map=40),
        "RIVALS_HOUSE": Waypoint(x=13, y=5, name="Rival's house", enter_dir="UP", dest_map=39),
        "ROUTE_1": Waypoint(x=9, y=0, name="Route 1 (north exit)", enter_dir="UP", dest_map=12),
        "SOUTH_GRASS": Waypoint(x=9, y=17, name="South toward grass", enter_dir="DOWN"),
    },
    # Map 40: Oak's Lab
    40: {
        "EXIT": Waypoint(x=4, y=11, name="Lab exit", enter_dir="DOWN", dest_map=0),
        "OAK": Waypoint(x=5, y=3, name="Professor Oak"),
        "POKEBALL_LEFT": Waypoint(x=6, y=4, name="Left pokeball (Squirtle)"),
        "POKEBALL_MID": Waypoint(x=7, y=4, name="Middle pokeball (Charmander)"),
        "POKEBALL_RIGHT": Waypoint(x=8, y=4, name="Right pokeball (Bulbasaur)"),
    },
    # Map 12: Route 1
    12: {
        "SOUTH": Waypoint(x=9, y=35, name="South to Pallet", enter_dir="DOWN", dest_map=0),
        "NORTH": Waypoint(x=10, y=0, name="North to Viridian", enter_dir="UP", dest_map=1),
    },
    # Map 1: Viridian City
    1: {
        "SOUTH": Waypoint(x=18, y=35, name="South to Route 1", enter_dir="DOWN", dest_map=12),
        "POKECENTER": Waypoint(x=23, y=15, name="Pokemon Center", enter_dir="UP"),
        "POKEMART": Waypoint(x=23, y=11, name="Poke Mart", enter_dir="UP"),
    },
}

# High-level actions the LLM can choose from, per map
MAP_ACTIONS: dict[int, list[str]] = {
    38: ["GO_STAIRS"],
    37: ["EXIT_HOUSE", "GO_UPSTAIRS"],
    0: ["GO_OAKS_LAB", "GO_SOUTH", "GO_PLAYERS_HOUSE", "GO_RIVALS_HOUSE", "GO_ROUTE1"],
    40: ["EXIT_LAB", "GO_OAK", "PICK_SQUIRTLE", "PICK_CHARMANDER", "PICK_BULBASAUR"],
    12: ["GO_NORTH", "GO_SOUTH"],
    1: ["GO_SOUTH", "GO_POKECENTER", "GO_POKEMART"],
}

# Map action → waypoint key mapping
ACTION_TO_WAYPOINT: dict[str, tuple[int, str]] = {
    "GO_STAIRS":        (38, "STAIRS"),
    "EXIT_HOUSE":       (37, "EXIT"),
    "GO_UPSTAIRS":      (37, "STAIRS_UP"),
    "GO_OAKS_LAB":      (0, "OAKS_LAB"),
    "GO_SOUTH":         (0, "SOUTH_GRASS"),  # default; overridden per map
    "GO_PLAYERS_HOUSE": (0, "PLAYERS_HOUSE"),
    "GO_RIVALS_HOUSE":  (0, "RIVALS_HOUSE"),
    "GO_ROUTE1":        (0, "ROUTE_1"),
    "EXIT_LAB":         (40, "EXIT"),
    "GO_OAK":           (40, "OAK"),
    "PICK_SQUIRTLE":    (40, "POKEBALL_LEFT"),
    "PICK_CHARMANDER":  (40, "POKEBALL_MID"),
    "PICK_BULBASAUR":   (40, "POKEBALL_RIGHT"),
    "GO_NORTH":         (12, "NORTH"),
    "GO_POKECENTER":    (1, "POKECENTER"),
    "GO_POKEMART":      (1, "POKEMART"),
}


def get_available_actions(map_id: int) -> list[str]:
    """Get the list of high-level actions available on the current map."""
    actions = MAP_ACTIONS.get(map_id, [])
    # Always include generic actions
    return actions + ["INTERACT", "WAIT"]


def get_waypoint_for_action(action: str, map_id: int) -> Waypoint | None:
    """Resolve a high-level action to a waypoint."""
    # Handle GO_SOUTH/GO_NORTH which vary by map
    if action == "GO_SOUTH":
        wp_map = MAP_WAYPOINTS.get(map_id, {})
        if "SOUTH" in wp_map:
            return wp_map["SOUTH"]
        if "SOUTH_GRASS" in wp_map:
            return wp_map["SOUTH_GRASS"]
        return None
    if action == "GO_NORTH":
        wp_map = MAP_WAYPOINTS.get(map_id, {})
        if "NORTH" in wp_map:
            return wp_map["NORTH"]
        if "ROUTE_1" in wp_map:
            return wp_map["ROUTE_1"]
        return None

    if action in ACTION_TO_WAYPOINT:
        expected_map, wp_key = ACTION_TO_WAYPOINT[action]
        waypoints = MAP_WAYPOINTS.get(expected_map, {})
        return waypoints.get(wp_key)

    return None


class Navigator:
    """
    Handles step-by-step pathfinding to a waypoint.
    
    Usage:
        nav = Navigator()
        nav.set_goal("GO_STAIRS", map_id=38)
        
        # Each tick:
        action = nav.next_step(current_x, current_y, current_map)
        # Returns "RIGHT 16", "UP 16", "A", or None if done/stuck
    """

    def __init__(self):
        self.target: Waypoint | None = None
        self.goal_name: str = ""
        self.active: bool = False
        self.last_x: int = -1
        self.last_y: int = -1
        self.stuck_count: int = 0
        self.max_stuck: int = 3  # after 3 failed moves, try alternate path
        self.steps_taken: int = 0
        self.max_steps: int = 100  # give up after this many steps
        self.arrived: bool = False
        self._alt_directions: list[str] = []  # alternate directions to try when stuck

    def set_goal(self, action: str, map_id: int) -> bool:
        """
        Set a navigation goal. Returns True if the goal is valid.
        """
        wp = get_waypoint_for_action(action, map_id)
        if wp is None:
            return False

        self.target = wp
        self.goal_name = action
        self.active = True
        self.stuck_count = 0
        self.steps_taken = 0
        self.arrived = False
        self.last_x = -1
        self.last_y = -1
        self._alt_directions = []
        return True

    def cancel(self):
        """Cancel current navigation."""
        self.active = False
        self.target = None
        self.arrived = False

    def is_active(self) -> bool:
        return self.active

    def is_arrived(self) -> bool:
        return self.arrived

    def get_status(self) -> str:
        if not self.active:
            return "idle"
        if self.arrived:
            return f"arrived at {self.goal_name}"
        return f"navigating to {self.goal_name} ({self.steps_taken} steps)"

    def next_step(self, x: int, y: int, map_id: int) -> str | None:
        """
        Compute the next movement action to reach the target.
        
        Returns:
            - "RIGHT 16", "LEFT 16", "UP 16", "DOWN 16" for movement
            - "A" for interaction (entering doors, talking)
            - None if navigation is complete or cancelled
        """
        if not self.active or self.target is None:
            return None

        # Check if we exceeded max steps
        if self.steps_taken >= self.max_steps:
            self.cancel()
            return None

        # Check for collision (position didn't change after a move)
        if self.last_x == x and self.last_y == y and self.steps_taken > 0:
            self.stuck_count += 1
        else:
            self.stuck_count = 0
            self._alt_directions = []

        self.last_x = x
        self.last_y = y

        tx, ty = self.target.x, self.target.y
        dx = tx - x
        dy = ty - y

        # Check if we've arrived at the target
        if dx == 0 and dy == 0:
            self.arrived = True
            self.active = False
            # If the waypoint has an enter direction, use it
            if self.target.enter_dir:
                return f"{self.target.enter_dir} 16"
            # Otherwise interact
            return "A"

        # Adjacent to target (within 1 tile) — might need to face and interact
        if abs(dx) <= 1 and abs(dy) <= 1 and self.target.enter_dir:
            # We're close enough, try the enter direction
            if abs(dx) + abs(dy) == 1:
                # Directly adjacent
                if dx == 0 and dy == -1 and self.target.enter_dir == "UP":
                    return "UP 16"
                if dx == 0 and dy == 1 and self.target.enter_dir == "DOWN":
                    return "DOWN 16"

        self.steps_taken += 1

        # If stuck, try alternate directions to get around obstacles
        if self.stuck_count >= self.max_stuck:
            return self._unstuck_step(dx, dy)

        # Normal pathfinding: move toward target
        # Prefer the axis with greater distance
        if abs(dx) >= abs(dy):
            # Move horizontally first
            if dx > 0:
                return "RIGHT 16"
            elif dx < 0:
                return "LEFT 16"
        
        # Move vertically
        if dy > 0:
            return "DOWN 16"
        elif dy < 0:
            return "UP 16"

        # Shouldn't reach here, but just in case
        return None

    def _unstuck_step(self, dx: int, dy: int) -> str:
        """
        When stuck against a wall/obstacle, try perpendicular directions
        to navigate around it.
        """
        # Build list of alternate directions if not already set
        if not self._alt_directions:
            # Primary direction we were trying
            if abs(dx) >= abs(dy):
                # Was trying horizontal, try vertical to get around
                self._alt_directions = ["UP 16", "DOWN 16"]
            else:
                # Was trying vertical, try horizontal to get around
                self._alt_directions = ["LEFT 16", "RIGHT 16"]
            # Add the opposite of primary as last resort
            if dx > 0:
                self._alt_directions.append("LEFT 16")
            elif dx < 0:
                self._alt_directions.append("RIGHT 16")
            if dy > 0:
                self._alt_directions.append("UP 16")
            elif dy < 0:
                self._alt_directions.append("DOWN 16")

        # Cycle through alternate directions
        self.stuck_count = 0  # reset so we try the alt direction a few times
        if self._alt_directions:
            direction = self._alt_directions.pop(0)
            # Put it at the back so we cycle
            self._alt_directions.append(direction)
            return direction

        # Absolute fallback
        return "RIGHT 16"


def describe_location(map_id: int, x: int, y: int) -> str:
    """Get a human-readable description of the current location."""
    MAP_NAMES = {
        0: "Pallet Town",
        1: "Viridian City",
        12: "Route 1",
        37: "Your house (1F)",
        38: "Your bedroom (2F)",
        39: "Rival's house",
        40: "Oak's Lab",
    }
    name = MAP_NAMES.get(map_id, f"Map {map_id}")
    return f"{name} at ({x},{y})"
