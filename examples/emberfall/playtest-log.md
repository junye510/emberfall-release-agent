# Emberfall 0.4.0-rc.3 playtest log

Build: 0.4.0-rc.3. Dates: 2026-10-12 to 2026-10-17. Six couch pairs and eleven solo runs.

## BUG-118
- title: Orchard Heart root slam hard-crashes at empty ember
- severity: crash
- area: boss
- summary: Empty ember gauge plus the root slam aborts to desktop and drops the seed. Repro on rc.3, solo and couch. Let the gauge hit zero and stand in the slam.

## BUG-121
- title: Ember gust shoves the player through the extraction flare
- severity: major
- area: movement
- summary: A high-wind gust during extraction pushes the hurtbox through the flare collider. The flare cannot be grabbed until you walk out and back in.

## BUG-124
- title: Heat-sink relic uses the old ash icon
- severity: minor
- area: ui
- summary: The relic applies the right stat change, but the HUD and chest popup still show the retired ash-charm sprite.

## BUG-126
- title: Pause menu quit row says Surival
- severity: cosmetic
- area: ui
- summary: The quit row still reads "Quit Surival?" on keyboard and pad. The button still quits.
