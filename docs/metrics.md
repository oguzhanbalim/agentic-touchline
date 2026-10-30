# Metric definitions

Generated from `src/touchline/metrics/definitions.py` by `python -m touchline.metrics.docs`. Do not edit by hand.

All locations use StatsBomb's coordinates: x from 0 to 120 along the pitch, y from 0 to 80 across it, with the acting team always attacking towards x = 120.

## minutes

**Definition.** Time on the pitch, from Starting XI, Tactical Shift and Substitution events and sending-off cards, walked in event order. A period ends at its last recorded event. Penalty shootouts do not count.

**Limitations.** Short spells off the pitch for treatment count as played. Per-90 values inherit this.

## shots_and_xg

**Definition.** Shot events in periods 1 to 4, excluding penalty shootouts. xG is StatsBomb's own `statsbomb_xg` field, summed. A goal is a shot with outcome Goal.

**Limitations.** xG is StatsBomb's model and is not recomputed or validated here. Own goals are credited to the team in match scores and to no player. Small samples: one match holds very few shots per player.

## passes

**Definition.** Pass events by the player. Attempted counts every pass. Completed means the pass has no outcome, which is how StatsBomb encodes completion. Open play excludes corners, free kicks, goal kicks, kick-offs and throw-ins.

**Limitations.** Completion rate does not account for pass difficulty, so it is not a measure of passing quality on its own.

## progressive_passes

**Definition.** Completed open-play passes whose end point is at least 25% closer to the centre of the opposition goal (120, 40) than the start point, and at least 5 yards closer.

**Limitations.** The thresholds are a convention chosen for this project; other providers use other cut-offs, so counts are not comparable across sources. It ignores whether a pass beat any opponent. Pitch units are StatsBomb's 120 x 80 scale, nominally yards.

## progressive_carries

**Definition.** Carry events meeting the same distance test as progressive passes, using the carry's start and end location.

**Limitations.** Same threshold caveats as progressive passes. StatsBomb's carries are derived between other on-ball events, so very short movements are not separate actions.

## pressures

**Definition.** Pressure events by the player. StatsBomb records one when a player applies pressure to an opponent receiving, carrying or releasing the ball. Thirds use the presser's own attacking direction: defensive below x = 40, attacking at x = 80 and above.

**Limitations.** Counts depend on how much the opponent has the ball, so a higher count can mean more time defending as well as more pressing intent. Whether the pressure succeeded is not measured.

## pass_network

**Definition.** For one team, from kick-off until its first substitution or player sent off: completed open-play passes between each pair, in both directions together. A player's average position is the mean of the locations at which they made and received those passes.

**Limitations.** It covers only the window before the first change, which can be short. An average position can sit where the player rarely stood, and carries and off-ball movement do not move it.

## space_at_reception

**Definition.** For a completed open-play reception with a usable 360 frame: distance in yards from the receiver to the nearest visible opponent, goalkeeper included. A frame is usable if the actor is within 5 yards of the event location and no visible goalkeeper is in the wrong half. A value is censored, and left out of the median, if the disc of that radius around the receiver is not fully inside the camera's visible area (off-pitch parts ignored), because a closer opponent could be out of shot.

**Limitations.** Dropping censored frames biases the median down: wide-open receptions are the most likely to be censored, so the reported median understates space. The censored count is always reported beside it. Positions come from broadcast video and are estimates. It is a snapshot at the moment of reception and ignores opponents' speed and direction.

## receptions_between_lines

**Definition.** A heuristic on completed open-play receptions with a usable frame showing at least 7 outfield opponents (assessable receptions). A reception counts when the receiver is between x = 60 and x = 102, and at least 2 outfield opponents are goal-side and at least 2 ball-side, all within 20 yards laterally and 15 yards longitudinally of the receiver.

**Limitations.** This approximates the idea; it has not been validated against human labels. Frames have no player identities, so it cannot tell a midfield line from a pair of retreating defenders, and it does not check that the opponents form a line. The parameters are a judgement call. Use it to compare roles and matches, not as an exact count.

## reception_zones

**Definition.** Completed open-play receptions grouped into 15 zones: thirds at x = 40 and x = 80; channels at y = 18, 30, 50 and 62, taken from the penalty-area and six-yard-box lines. Left and right are from the attacking team's view (low y is left).

**Limitations.** Zone edges are hard, so a small movement can change a zone. Zone shares say where the player received the ball, not where they were available and were not found.

## role_comparison

**Definition.** Per-90 values and reception rates against peers: other players in the ingested matches whose minutes in the same role group reach the minimum. Only events and minutes in that role group count, for both the player and the peers. Rank 1 is the highest value.

**Limitations.** The default dataset holds five matches of one team and its opponents, so peer groups are small and unrepresentative of the tournament, and per-90 values from short appearances are volatile. Role groups come from StatsBomb's position labels, not from how a player actually played.
