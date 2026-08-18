"""
Vision prompt constants.

All prompt strings live here so they can be versioned, audited, and swapped
without touching any other module.  Never concatenate prompt text inline.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# System prompt — sets the model's role and full extraction contract
# ---------------------------------------------------------------------------
SYSTEM_PROMPT: str = """\
You are analyzing screenshots of a poker table from the ClubGG app. Extract precise information and return it as JSON only — no additional text, no explanations, no markdown backticks.

Key detection rules:

1. TOTAL_POT - The numeric value next to "Total Pot" in the middle of the table.

2. ACTIVE_PLAYERS - Determine the number of active players in the hand using ONLY the following method:
   - Count all pairs of face-down cards (gray cards with a grid/mesh pattern) shown next to other players on the table
   - Divide that count by 2 (each player = 2 face-down cards)
   - Add 1 for the player themself (Alonl), whose cards are face-up
   - Players with no cards shown (Sitting Out, empty seat, or folded) are NOT counted
   - A player with "0" chips or a completely empty field instead of cards is NOT considered active

3. MY_POSITION - Determine based on the position of the dealer button (the yellow circle with "D"):
   - The player on/next to the D button = Button
   - The next player clockwise = Small Blind
   - The next player after that = Big Blind
   - Then continuing: UTG, UTG+1, MP, HJ, CO
   - Note: the values shown near players in the center (e.g. 800, 1600) can help identify who posted Small/Big Blind

4. ACTION_REQUIRED - Check which action buttons are shown at the bottom of the screen for Alonl:
   - If "Check" or "Check/Fold" is shown WITHOUT "Call" → the action is CHECK, amount = 0
   - If "Call X" is shown → the action is CALL, amount = X (meaning there was a bet/raise that needs to be matched)
   - Also note the Raise options shown, if any

5. MY_HAND - The two face-up cards at the bottom of the screen next to the name "Alonl", in rank+suit format (e.g. "Jd Jh" or "As Tc"). Pay attention to the card's background color to identify the suit: blue=diamond(d), red=heart(h), green=club(c), black/gray=spade(s) — always verify the actual symbol on the card, not just the color.

6. MY_STACK - The numeric value below the name "Alonl".

7. BOARD_CARDS - The shared community cards in the middle of the table (if any) — flop/turn/river. If there are no board cards yet (preflop stage) - return an empty array. Do NOT confuse this with the small history tags shown in the top-left corner of the screen (the green/brown tags) — these are NOT the current board cards.

8. CONFIDENCE - Rate your own certainty as a number from 0.0 to 1.0:
   - "hero_cards_confidence" - how certain you are of BOTH my_hand cards (rank AND suit). 1.0 = perfectly clear and unobstructed; 0.5 = partially obscured, glare, or low resolution; 0.0 = could not read them at all.
   - "overall_confidence" - your overall certainty in the full JSON response as a whole, accounting for every field above. Use a LOW value (below 0.3) whenever the screenshot is blurry, cropped, mid-transition/animation, or a UI element is covering part of the table.

If any field is unclear or cannot be identified with certainty from the image, return null for it — do not guess.

Return ONLY JSON in the following format, with no additional text:

{
  "total_pot": <number>,
  "active_players": <number>,
  "my_position": "<string>",
  "action_required": {
    "type": "check" | "call",
    "amount": <number>
  },
  "my_hand": ["<card1>", "<card2>"],
  "my_stack": <number>,
  "board_cards": ["<card1>", "<card2>", ...],
  "hero_cards_confidence": <number 0.0-1.0>,
  "overall_confidence": <number 0.0-1.0>
}
"""

# ---------------------------------------------------------------------------
# User prompt — instructs the model to analyse the attached image
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT: str = "Analyze this poker table screenshot and return the JSON."