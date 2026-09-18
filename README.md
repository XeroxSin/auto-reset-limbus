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
    },
    "heathcliff_or_ishmael_top": {
      "constraint_type": "or",
      "constraint_info": ["heathcliff_top_s3", "ishmael_top_s2_or_s3"]
    },
    "dq_not_last": {
      "constraint_type": "not",
      "constraint_info": {
        "constraint_type": "order_absolute",
        "constraint_info": {"unit": "Don Quixote", "position": 5}
      }
    }
  }
}
```

A turn passes this example when all of these hold:

| constraint                    | meaning                                                                                                                                                           |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `heathcliff_first`          | Heathcliff acts first (position 0).                                                                                                                               |
| `dq_in_front_three`         | Don Quixote's first action is in positions 0, 1 or 2.                                                                                                             |
| `sinclair_before_ishmael`   | Sinclair acts before Ishmael.                                                                                                                                     |
| `dq_next_s3`                | Don Quixote's next skill is a Skill 3.                                                                                                                            |
| `heathcliff_or_ishmael_top` | Heathcliff's top skill is a Skill 3 (`heathcliff_top_s3`), **or** at least one of Ishmael's columns has a Skill 2 or 3 on top (`ishmael_top_s2_or_s3`). |
| `dq_not_last`               | Don Quixote is**not** at position 5. The position check is written inline inside the `not`.                                                               |

`heathcliff_top_s3` and `ishmael_top_s2_or_s3` don't have to pass on their own, because `heathcliff_or_ishmael_top` uses them.

### `sinners`

The sinners involved, as a list. The tool only compares the portraits on screen against these sinners' identities, which makes each read roughly twice as fast. Any other deployed sinner is reported as "not listed", but still counts for the order positions.

Spell each name exactly like one of these:
`Yi Sang`, `Faust`, `Don Quixote`, `Ryoshu`, `Meursault`, `Hong Lu`, `Heathcliff`, `Ishmael`, `Rodion`, `Sinclair`, `Outis`, `Gregor`.

Names are case-sensitive.

### `constraints`

Each constraint has an id of your choice (the key) and two fields: a `constraint_type` and its `constraint_info`. The first three types check the battle; `and`, `or` and `not` combine other constraints (see [Combining constraints](#combining-constraints)).

| `constraint_type` | `constraint_info`                                                              | passes when                                                                                          |
| ------------------- | -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `order_absolute`  | `unit`, `position`                                                           | the unit's position in the action order is`position`. Positions count from **0** (leftmost). |
| `order_relative`  | `unit`, `relation` (`"before"` or `"after"`), `other`                  | `unit` acts before or after `other`                                                              |
| `skill_tier`      | `unit`, `slot` (`"bottom"`, `"top"` or `"next"`), `tier` (1, 2 or 3) | the unit's skill in that slot has that tier                                                          |
| `and`             | a list of constraints                                                            | every listed constraint passes                                                                       |
| `or`              | a list of constraints                                                            | at least one listed constraint passes                                                                |
| `not`             | one constraint                                                                   | that constraint fails                                                                                |

- `position` and `tier` can also be a list of allowed values, e.g. `"position": [0, 1]` or `"tier": [2, 3]`.
- `slot` refers to the skill icons for each unit:
  - `bottom`: the lower skill;
  - `top`: the upper skill;
  - `next`: the next skill in line.

**Units.** `"unit": "Faust"` refers to every action column Faust has. A sinner with more than one speed die can have several columns. To pick specific columns, use the object form, `{"sinner": "Faust", "column": ...}`:

| `column`               | the constraint passes when                                        |
| ------------------------ | ----------------------------------------------------------------- |
| `"all"` (default)      | every column of that sinner passes                                |
| `"any"`                | at least one column passes                                        |
| `"first"` / `"last"` | the leftmost / rightmost column passes                            |
| `0`, `1`, …         | that specific column of the sinner (counted from the left) passes |

### Combining constraints

Every constraint must pass, **except** constraints used inside an `and`, `or` or `not`. Those only count through the constraint that uses them.

For `and`, `or` and `not`, the `constraint_info` is the constraints they apply to:

- **`and` / `or`:** a list with as many constraints as you want.
- **`not`:** exactly one constraint, either on its own or as a one-item list.

Each item can be either:

- **the id of another constraint:**
  ```json
  "faust_has_s3": {"constraint_type": "or",
                   "constraint_info": ["faust_top_s3", "faust_next_s3"]}
  ```
- **a whole constraint written inline.** This keeps nested conditions in one place. For example, "Faust acts first, or (Sinclair has a Skill 3 next and Sinclair is not last)":
  ```json
  "opening": {
    "constraint_type": "or",
    "constraint_info": [
      {"constraint_type": "order_absolute", "constraint_info": {"unit": "Faust", "position": 0}},
      {"constraint_type": "and", "constraint_info": [
        {"constraint_type": "skill_tier", "constraint_info": {"unit": "Sinclair", "slot": "next", "tier": 3}},
        {"constraint_type": "not", "constraint_info":
          {"constraint_type": "order_absolute", "constraint_info": {"unit": "Sinclair", "position": 5}}}
      ]}
    ]
  }
  ```

**How inline constraints are named.** Inline constraints are named after where they sit, so they can be shown in the output:

- `opening.0` is the first item of `opening`;
- `opening.1.1` is the second item inside `opening`'s second item, which is the `not`;
- `opening.1.1.0` is the position check inside that `not`.

Other constraints can't refer to these generated names.

**Rules:**

- A constraint can be used by more than one `and`/`or`/`not`.
- A constraint can't include itself, directly or through others.

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
python tools/auto_reset.py -w 5                 # wait 5 s after a reset and before a re-capture (default 3)
python tools/auto_reset.py --no-pan             # never drag the battlefield
python tools/auto_reset.py --save               # also save every read to output/
```

1. **Start a battle.** Start the battle you want to reset, and let it reach the first turn, where you would choose skills.
2. **Run the command.** Then switch to the game before the countdown ends.
3. **Let it run.** Each round it waits for the turn, captures the screen and checks it.
   - If the read is unclear, it drags the battlefield up, waits, and captures again (see **Unknown results** below).
   - If the check fails, it moves the cursor out of the way, presses Esc, clicks **Retry Stage**, waits 3 seconds and checks again.
   - Once the check passes, it stops.

**While it runs:**

- **Pausing.** If you switch to another window, it pauses. When you switch back to the game, it resumes.
- **No reset limit.** Press **Ctrl+C** in the terminal to stop at any time.
- **Mouse settings.** While it moves the mouse, "Enhance pointer precision" is turned off and pointer speed is set to the default. Both are restored when it stops.

The terminal shows each check: every constraint (`valid`, `INVALID` or `unknown`), then the verdict. The constraints that an `and`, `or` or `not` uses are indented under it, one level deeper for each level of nesting.

```
check 3: capturing
check 3:
  INVALID  heathcliff_first
             Heathcliff at order 2, expected 0
  valid    dq_in_front_three
  valid    sinclair_before_ishmael
  valid    dq_next_s3
  valid    heathcliff_or_ishmael_top (or)
    INVALID  heathcliff_top_s3
               unit 2 (Heathcliff) top is tier 2, expected 3
    valid    ishmael_top_s2_or_s3
  valid    dq_not_last (not)
    INVALID  dq_not_last.0
               Don Quixote at order 1, expected 5
  => INVALID after 1 read(s)
   retrying the stage
```

**Unknown results.** A constraint is `unknown` when the tool couldn't read what it needs: the sinner wasn't found, a skill wasn't recognised, or the read had low confidence. A shaky read of one skill slot only makes checks of that slot unknown; a shaky identity makes every check of that unit unknown.

- If an unknown can change the verdict, the tool **drags the battlefield upward** and captures again, up to 3 reads in total. Dragging leaves the icons where they are but puts plain ground behind them, which the faint next-in-line icon reads much better on. `--no-pan` turns this off and `--pan-dy` changes how far it drags (140 px by default, in 1080p terms).
- If it's still unclear after 3 reads, the round counts as failed and the stage is retried.

**Logs.** Every run writes a detailed log to `output/logs/auto_reset_<date>_<time>.log`. The log includes identity and skill match scores, button scores, mouse movements and the raw reads.

### Other tools

| Command                                                | What it does                                                                                                                                            |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python tools/verify_state.py CONFIG STATE.json ...` | Checks saved reads against a config, without touching the game. The exit code is 0 for valid, 1 for invalid, 3 for unreadable and 2 for a config error. |
| `python tools/verify_state.py CONFIG --live`         | Captures the game once and checks it, without resetting.                                                                                                |
| `python tools/battle_state.py [-c CONFIG]`           | Captures the game and saves the read (order, identities, tiers) to`output/`.                                                                          |
| `python tools/battle_state.py screenshot.png`        | Reads a saved screenshot instead of the live game.                                                                                                      |
| `python tools/retry_stage.py`                        | Presses Esc and clicks Retry Stage once.                                                                                                                |
| `python tools/capture_screen.py`                     | Saves a screenshot of the game area.                                                                                                                    |
| `python tools/next_crops.py shot.png`               | Writes the patch each unit's next-in-line skill is read from to `output/next_crops/`, to check recognition by eye. |
| `python tools/check_labels.py`                      | Scores the skill reader against the labelled screenshots in `data/labelled/` (see the README there). |

Each tool prints its full usage with `--help`.

## 3. Credits and asset ownership

**None of the game assets in this repository are my own.** All of the following belong to **Project Moon**, the developer and publisher of **Limbus Company**, and all credit for them goes to Project Moon:

- the identity portraits in `assets/profiles/`;
- the skill borders in `assets/Skill borders/`;
- the button and frame templates in `assets/templates/`, which are cropped from game screenshots;
- the names of the sinners and identities.

This project isn't affiliated with or endorsed by Project Moon. Limbus Company and all related names and art are trademarks and copyrights of Project Moon. The assets are included only so the screen recognition can work. If you are a rights holder and want something removed, please open an issue.

The human-like mouse movement in `tools/humanmouse/movement/` comes from **[Charge Grinder](https://github.com/Walpth/Charge-Grinder)** 3.5.0 (GPL-3.0); the movement model was designed by Walpth. The screen-reading approach was also inspired by Charge Grinder.

Automating input may be against the game's terms of service. Use this tool at your own risk.

## 4. License

This project is licensed under the **GNU General Public License v3.0**; see [LICENSE](LICENSE). This license covers the code only. The Limbus Company assets described above remain the property of Project Moon and aren't covered by it.
