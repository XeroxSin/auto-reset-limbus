# auto-reset-limbus

Automatically retries a Limbus Company battle until the first turn looks the way you want.

Each round, the tool:

1. reads the battle screen: action order, which identity is in each slot, and the tier of each offered skill;
2. checks what it read against the rules in a config file;
3. stops if the rules pass; otherwise it opens the Esc menu, clicks **Retry Stage**, and tries again.

It only looks at the screen and uses the mouse and keyboard. It doesn't read or change the game's memory or files.

## Requirements

- Windows. Screen capture and input use the Windows API.
- Python 3.10 or newer (developed on 3.13).
- `pip install -r requirements.txt` (installs numpy and opencv-python).
- The game running in a window titled `LimbusCompany`, visible and not covered by other windows. The tool captures the desktop, so whatever is on top of the game is what it sees.

## 1. Create a config

A config is a JSON file that lists the sinners you care about and the conditions the first turn must meet.

### The default config

`tools/auto_reset.py` uses **`configs/config.json`** when you don't pass a config path.

The `configs/config.json` in this repository is an **example**: it uses every constraint type so you can copy from it. Replace its contents with your own team and conditions, or keep several configs and pass the one you want (see [Run the code](#2-run-the-code)).

```json
{
  "sinners": ["Heathcliff", "Sinclair", "Don Quixote", "Ishmael"],
  "constraints": {
    "heathcliff_first": {
      "constraint_type": "order_absolute",
      "constraint_info": {"unit": "Heathcliff", "position": 0}
    },
    "dq_in_front_three": {
      "constraint_type": "order_absolute",
      "constraint_info": {"unit": {"sinner": "Don Quixote", "column": "first"}, "position": [0, 1, 2]}
    },
    "sinclair_before_ishmael": {
      "constraint_type": "order_relative",
      "constraint_info": {"unit": "Sinclair", "relation": "before", "other": "Ishmael"}
    },
    "dq_next_s3": {
      "constraint_type": "skill_tier",
      "constraint_info": {"unit": "Don Quixote", "slot": "next", "tier": 3}
    },
    "heathcliff_top_s3": {
      "constraint_type": "skill_tier",
      "constraint_info": {"unit": "Heathcliff", "slot": "top", "tier": 3}
    },
    "ishmael_top_s2_or_s3": {
      "constraint_type": "skill_tier",
      "constraint_info": {"unit": {"sinner": "Ishmael", "column": "any"}, "slot": "top", "tier": [2, 3]}
    }
  },
  "logic": [
    {"or": ["heathcliff_top_s3", "ishmael_top_s2_or_s3"]}
  ]
}
```

A turn passes this example when all of these hold:

| constraint | meaning |
|---|---|
| `heathcliff_first` | Heathcliff acts first (position 0). |
| `dq_in_front_three` | Don Quixote's first action is in positions 0, 1 or 2. |
| `sinclair_before_ishmael` | Sinclair acts before Ishmael. |
| `dq_next_s3` | Don Quixote's next skill is a Skill 3. |
| `logic` | Heathcliff's top skill is a Skill 3 (`heathcliff_top_s3`), **or** at least one of Ishmael's columns has a Skill 2 or 3 on top (`ishmael_top_s2_or_s3`). |

Any other sinners on the team are ignored.

### `sinners`

The sinners involved, as a list. The tool only compares the portraits on screen against these sinners' identities, which makes each read roughly twice as fast. Any other deployed sinner is reported as "not listed", but still counts for the order positions.

Spell each name exactly like one of these:
`Yi Sang`, `Faust`, `Don Quixote`, `Ryoshu`, `Meursault`, `Hong Lu`, `Heathcliff`, `Ishmael`, `Rodion`, `Sinclair`, `Outis`, `Gregor`.

Names are case-sensitive.

### `constraints`

Each constraint has an id of your choice (the key) and two fields: a `constraint_type` and its `constraint_info`.

| `constraint_type` | `constraint_info` | passes when |
|---|---|---|
| `order_absolute` | `unit`, `position` | the unit's position in the action order is `position`. Positions count from **0** (leftmost). |
| `order_relative` | `unit`, `relation` (`"before"` or `"after"`), `other` | `unit` acts before or after `other` |
| `skill_tier` | `unit`, `slot` (`"bottom"`, `"top"` or `"next"`), `tier` (1, 2 or 3) | the unit's skill in that slot has that tier |

- `position` and `tier` can also be a list of allowed values, e.g. `"position": [0, 1]` or `"tier": [2, 3]`.
- `slot` refers to the skill icons for each unit:
  - `bottom`: the lower skill;
  - `top`: the upper skill;
  - `next`: the next skill in line.

**Units.** `"unit": "Faust"` refers to every action column Faust has. A sinner with more than one speed die can have several columns. To pick specific columns, use the object form, `{"sinner": "Faust", "column": ...}`:

| `column` | the constraint passes when |
|---|---|
| `"all"` (default) | every column of that sinner passes |
| `"any"` | at least one column passes |
| `"first"` / `"last"` | the leftmost / rightmost column passes |
| `0`, `1`, … | that specific column of the sinner (counted from the left) passes |

### `logic` (optional)

By default, **every constraint must pass**. Use `logic` when you need "or" or "not". Each entry is one of:

- a constraint id;
- `{"or": [...]}`;
- `{"and": [...]}`;
- `{"not": ...}`.

Entries can be nested. Everything in `logic` must hold, **and** every constraint that `logic` doesn't mention must pass.

```json
"logic": [
  {"or": ["heathcliff_top_s3", "ishmael_top_s2_or_s3"]},
  {"not": "dq_next_s3"}
]
```

### Checking a config

When a config has a mistake, the tools stop before touching the game and name the problem:

```
error: configs/config.json: constraints.dq_next_s3.constraint_info.unit.sinner: 'don quixote' is not in sinners
```

## 2. Run the code

Run the commands below from the repository folder.

### Auto-reset (the main tool)

```
python tools/auto_reset.py                      # uses configs/config.json
python tools/auto_reset.py configs/other.json   # uses another config
python tools/auto_reset.py -d 10                # 10 s to switch to the game (default 5)
python tools/auto_reset.py --save               # also save every read to output/
```

1. **Start a battle.** Start the battle you want to reset, and let it reach the first turn, where you would choose skills.
2. **Run the command.** Then switch to the game before the countdown ends.
3. **Let it run.** Each round it waits for the turn, captures the screen and checks it.
   - If the check fails, it moves the cursor out of the way, presses Esc, clicks **Retry Stage**, waits 3 seconds and checks again.
   - Once the check passes, it stops.

**While it runs:**

- **Pausing.** If you switch to another window, it pauses. When you switch back to the game, it resumes.
- **No reset limit.** Press **Ctrl+C** in the terminal to stop at any time.
- **Mouse settings.** While it moves the mouse, "Enhance pointer precision" is turned off and pointer speed is set to the default. Both are restored when it stops.

The terminal shows each check: every constraint (`valid`, `INVALID` or `unknown`), each `logic` group, and the verdict.

```
check 3: capturing
check 3:
  INVALID  heathcliff_first
             Heathcliff at order 2, expected 0
  valid    dq_in_front_three
  valid    sinclair_before_ishmael
  valid    dq_next_s3
  INVALID  heathcliff_top_s3
             unit 2 (Heathcliff) top is tier 2, expected 3
  valid    ishmael_top_s2_or_s3
  valid    logic: or(heathcliff_top_s3, ishmael_top_s2_or_s3)
  => INVALID after 1 read(s)
   retrying the stage
```

**Unknown results.** A constraint is `unknown` when the tool couldn't read what it needs: the sinner wasn't found, a skill wasn't recognised, or the read had low confidence.

- If an unknown can change the verdict, the screen is captured again, up to 3 times in total.
- If it's still unclear after that, the round counts as failed and the stage is retried.

**Logs.** Every run writes a detailed log to `output/logs/auto_reset_<date>_<time>.log`. The log includes identity and skill match scores, button scores, mouse movements and the raw reads.

### Other tools

| Command | What it does |
|---|---|
| `python tools/verify_state.py CONFIG STATE.json ...` | Checks saved reads against a config, without touching the game. The exit code is 0 for valid, 1 for invalid, 3 for unreadable and 2 for a config error. |
| `python tools/verify_state.py CONFIG --live` | Captures the game once and checks it, without resetting. |
| `python tools/battle_state.py [-c CONFIG]` | Captures the game and saves the read (order, identities, tiers) to `output/`. |
| `python tools/battle_state.py screenshot.png` | Reads a saved screenshot instead of the live game. |
| `python tools/retry_stage.py` | Presses Esc and clicks Retry Stage once. |
| `python tools/capture_screen.py` | Saves a screenshot of the game area. |

Each tool prints its full usage with `--help`.

## 3. Credits and asset ownership

**None of the game assets in this repository are my own.** All of the following belong to **Project Moon**, the developer and publisher of **Limbus Company**, and all credit for them goes to Project Moon:

- the identity portraits in `assets/profiles/`;
- the skill borders in `assets/Skill borders/`;
- the button and frame templates in `assets/templates/`, which are cropped from game screenshots;
- the names of the sinners and identities.

This project isn't affiliated with or endorsed by Project Moon. Limbus Company and all related names and art are trademarks and copyrights of Project Moon. The assets are included only so the screen recognition can work. If you are a rights holder and want something removed, please open an issue.

The human-like mouse movement in `tools/humanmouse/movement/` comes from **Charge Grinder** 3.5.0 (GPL-3.0); the movement model was designed by Walpth. The screen-reading approach was also inspired by Charge Grinder.

Automating input may be against the game's terms of service. Use this tool at your own risk.

## 4. License

This project is licensed under the **GNU General Public License v3.0**; see [LICENSE](LICENSE). This license covers the code only. The Limbus Company assets described above remain the property of Project Moon and aren't covered by it.
