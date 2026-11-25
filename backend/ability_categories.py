"""Tactical category for every GRID ability id seen in the VALORANT feeds.

Each ability has one primary category, chosen by what it does. When an ability
does two jobs, the earlier category in CATEGORY_ORDER wins (e.g. Spycam is a
Placeable rather than Recon; Arc Rose is a Placeable rather than a Flash).

    Placeable  unattended deployables that hold space or gather info: traps, cams, turrets, sensors
    Recon      reveals enemy positions: bolts, drones, dogs, prowlers
    Smoke      blocks vision: smokes, vision-blocking walls
    Stall      delays a push or retake: mollies, slows, concussions, nets, damage zones
    Flash      blinds or nearsights
    Other      movement, heals, self-buffs, decoys and duel ults
"""

CATEGORY_ORDER = ["Placeable", "Recon", "Smoke", "Stall", "Flash"]
OTHER = "Other"

ABILITY_CATEGORY: dict[str, str] = {
    # ---- Placeable --------------------------------------------------------
    "trapwire": "Placeable", "spycam": "Placeable",                       # Cypher
    "alarmbot": "Placeable", "turret": "Placeable",                       # Killjoy
    "nanoswarm": "Placeable", "lockdown": "Placeable",
    "trademark": "Placeable",                                             # Chamber
    "sonic-sensor": "Placeable",                                          # Deadlock
    "shear": "Placeable", "arc-rose": "Placeable", "razorvine": "Placeable",  # Vyse
    "chokehold": "Placeable", "interceptor": "Placeable",                 # Veto
    # ---- Recon ------------------------------------------------------------
    "recon-bolt": "Recon", "owl-drone": "Recon",                          # Sova
    "haunt": "Recon", "prowler": "Recon", "nightfall": "Recon",           # Fade
    "trailblazer": "Recon", "seekers": "Recon",                           # Skye
    "stealth-drone": "Recon",                                             # Tejo
    "zero-point": "Recon",                                                # KAY/O
    "neural-theft": "Recon",                                              # Cypher
    # ---- Smoke ------------------------------------------------------------
    "dark-cover": "Smoke", "sky-smoke": "Smoke", "cloudburst": "Smoke",
    "poison-cloud": "Smoke", "toxic-screen": "Smoke", "viper's-pit": "Smoke",
    "nebula-dissipate": "Smoke", "cyber-cage": "Smoke", "ruse": "Smoke",
    "high-tide": "Smoke", "cove": "Smoke", "cascade": "Smoke",
    "waveform": "Smoke", "contingency": "Smoke", "blaze": "Smoke", "fast-lane": "Smoke",
    # ---- Stall ------------------------------------------------------------
    "incendiary": "Stall", "orbital-strike": "Stall",                     # Brimstone
    "snake-bite": "Stall", "hot-hands": "Stall", "mosh-pit": "Stall",
    "slow-orb": "Stall", "barrier-orb": "Stall",                          # Sage
    "gravnet": "Stall", "barrier-mesh": "Stall",                          # Deadlock
    "gravity-well": "Stall", "nova-pulse": "Stall",                       # Astra
    "fault-line": "Stall", "aftershock": "Stall", "rolling-thunder": "Stall",  # Breach
    "shock-bolt": "Stall", "frag-ment": "Stall", "paint-shells": "Stall",
    "special-delivery": "Stall", "guided-salvo": "Stall", "armageddon": "Stall",  # Tejo
    "wingman": "Stall", "thrash": "Stall",                                # Gekko
    "seize": "Stall", "relay-bolt": "Stall", "reckoning": "Stall",
    "m-pulse": "Stall", "bassquake": "Stall",                             # Miks
    "saturate": "Stall", "convergent-paths": "Stall",                     # Waylay
    "steel-garden": "Stall", "undercut": "Stall", "meddle": "Stall",
    # ---- Flash ------------------------------------------------------------
    "flashpoint": "Flash", "curveball": "Flash", "blindside": "Flash",
    "flash-drive": "Flash", "guiding-light": "Flash", "dizzy": "Flash",
    "leer": "Flash", "paranoia": "Flash", "storm-surge": "Flash",
    # ---- Other ------------------------------------------------------------
    "stars": OTHER,  # Astra star placement; the smoke/pulse/well it becomes is logged separately
    "updraft": OTHER, "tailwind": OTHER, "high-gear": OTHER, "blast-pack": OTHER,
    "light-speed": OTHER, "refract": OTHER, "rendezvous": OTHER, "shrouded-step": OTHER,
    "gatecrash": OTHER, "from-the-shadows": OTHER, "dimensional-drift": OTHER, "fakeout": OTHER,
    "crosscut": OTHER, "healing-orb": OTHER, "regrowth": OTHER, "stim-beacon": OTHER,
    "harmonize": OTHER, "pick-me-up": OTHER, "resurrection": OTHER, "not-dead-yet": OTHER,
    "devour": OTHER, "double-tap": OTHER, "kill-contract": OTHER, "headhunter": OTHER,
    "tour-de-force": OTHER, "showstopper": OTHER, "boom-bot": OTHER, "hunter's-fury": OTHER,
    "null-cmd": OTHER, "annihilation": OTHER,
}

# Display names where the current valorant-api kit no longer has the ability GRID logged.
NAME_OVERRIDES = {"cascade": "Cascade", "stars": "Stars"}


def category_of(ability_id: str | None) -> str:
    return ABILITY_CATEGORY.get(ability_id or "", OTHER)


def abilities_in(category: str) -> list[str]:
    return [a for a, c in ABILITY_CATEGORY.items() if c == category]
