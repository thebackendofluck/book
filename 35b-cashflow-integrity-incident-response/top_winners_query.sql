-- top_winners_query.sql
-- Identify suspect winners in a given window. Use to determine whether the
-- cashflow incident is concentrated (single game, single operator, single group)
-- or platform-wide (suggests RNG / settlement bug).
--
-- Run against a read replica. Tune the WHERE clause as needed.

WITH suspect_window AS (
  SELECT player_id,
         game_id,
         operator_id,
         COUNT(*)                        AS rounds,
         SUM(bet_amount)                 AS total_bet,
         SUM(win_amount)                 AS total_win,
         SUM(win_amount - bet_amount)    AS net_to_player,
         MAX(win_amount / NULLIF(bet_amount, 0))  AS max_payout_ratio,
         AVG(win_amount / NULLIF(bet_amount, 0))  AS avg_payout_ratio,
         MIN(settled_at)                 AS first_round_at,
         MAX(settled_at)                 AS last_round_at
    FROM game_rounds
   WHERE settled_at >= NOW() - INTERVAL '1 hour'
GROUP BY player_id, game_id, operator_id
)
SELECT *
  FROM suspect_window
 WHERE net_to_player > 1000
 ORDER BY net_to_player DESC
 LIMIT 50;

-- Aggregate by game to see if one game dominates
-- Re-run with this version when the per-player view shows concentration
-- and you want the game-level total.
--
-- SELECT game_id,
--        COUNT(DISTINCT player_id) AS players,
--        SUM(net_to_player)         AS net_to_players,
--        AVG(max_payout_ratio)      AS avg_max_ratio
--   FROM suspect_window
--  WHERE net_to_player > 0
--  GROUP BY game_id
--  ORDER BY net_to_players DESC
--  LIMIT 20;
