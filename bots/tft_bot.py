"""
==============================================================================
STRATEGIC BOT - Based on Exploding Kittens Strategy Guide
==============================================================================

Strategy based on probability and card management:
- Early game: Amass cards, don't waste them
- Track EK probability and play defensively/aggressively accordingly
- Use Shuffle to reset odds when possible
- Use See the Future strategically based on EK probability
- Save useless cards (Shuffle/STF) if only EKs remain
"""

import random

from game.bots.base import (
    Action,
    Bot,
    DrawCardAction,
    PlayCardAction,
    PlayComboAction,
)
from game.bots.view import BotView
from game.cards.base import Card
from game.history import GameEvent, EventType


class TFTBot(Bot):
    """
    Strategic bot implementing probability-based Exploding Kittens strategy.
    
    Key strategies:
    - Early game: Just draw, amass cards (14% first turn EK risk is acceptable)
    - After first EK: Prioritize Shuffle > STF > Steal > Skip Cards > Draw
    - Probability thresholds: 33% (consider STF), 40% (play STF), 50% (defensive/aggressive)
    - Defensive: Use Skip (passive), avoid Attack (retaliation risk)
    - Aggressive: Steal from one target, use Skip to pass turn, save Attacks for end game
    """
    
    def __init__(self) -> None:
        """Initialize bot with strategy tracking."""
        # Track game state
        self._first_ek_drawn: bool = False
        self._deck_shuffled: bool = True  # Start shuffled (unknown EK position)
        self._num_players: int = 0
        self._num_eks_remaining: int = 0
        
        # Track opponent actions
        self._opponent_card_history: dict[str, list[str]] = {}
        
        # Track our STF usage (can't see results, but track when we use it)
        self._last_stf_step: int = -1
        
        # Track if we just shuffled (to avoid wasting Shuffle cards)
        self._just_shuffled: bool = False
        
        # Track what we saw with See the Future (from CARDS_PEEKED events)
        self._last_peeked_cards: list[str] = []  # Card types we saw
        
        # Track if we know EK is on top (from STF)
        self._ek_on_top: bool = False
        
        # =====================================================================
        # CHAT PHRASES - Strategic bot personality
        # =====================================================================
        
        # General turn phrases
        self._turn_phrases: list[str] = [
            "Calculating my win condition...",
            "Playing for late game.",
            "This is a tempo play.",
            "Thinking about my next spike...",
            "I scale here.",
            "Time to make a macro move.",
            "Trust the process.",
        ]
        
        # Phrases when shuffling
        self._shuffle_phrases: list[str] = [
            "RNG diff incoming.",
            "Rolling the dice again.",
            "Resetting the lobby.",
            "New patch, new odds.",
            "Time to reroll.",
        ]
        
        # Phrases when using See the Future
        self._stf_phrases: list[str] = [
            "Scouting the lobby...",
            "Checking future rounds...",
            "Reading the meta...",
            "Let me see the next fight...",
            "Vision advantage secured.",
        ]
        
        # Phrases when stealing (Favor)
        self._steal_phrases: list[str] = [
            "Yoink.",
            "That's my item now.",
            "Thanks for the donation.",
            "Skill issue, hand it over.",
            "Tax collected.",
        ]
        
        # Phrases when playing Skip
        self._skip_phrases: list[str] = [
            "Playing safe this turn.",
            "No need to overextend.",
            "Slow rolling here.",
            "Holding tempo.",
            "We chill for now.",
        ]
        
        # Phrases when playing Attack
        self._attack_phrases: list[str] = [
            "All-in.",
            "Hard forcing this.",
            "Time to grief someone.",
            "Full send.",
            "Limit testing.",
        ]
        
        # Phrases when playing Nope
        self._nope_phrases: list[str] = [
            "Nope, cancelled.",
            "Not allowed.",
            "That doesn't go through.",
            "Denied by game knowledge.",
            "Counterplay exists.",
        ]
        
        # Phrases when defusing
        self._defuse_phrases: list[str] = [
            "Survived the fight!",
            "Clutched that round.",
            "Still in the game.",
            "Barely lived, but we take those.",
            "Outplayed.",
            "That was close, holy.",
        ]
        
        # Phrases when giving a card
        self._give_card_phrases: list[str] = [
            "Fine, take it.",
            "Unlucky trade.",
            "Here, enjoy.",
            "This better be worth it.",
            "I'm griefing myself.",
        ]
        
        # Phrases when observing events
        self._reaction_phrases: dict[str, list[str]] = {
            "elimination": [
                "Player diff.",
                "Lobby just got easier.",
                "GG go next.",
                "Outscaled.",
            ],
            "explosion": [
                "RNG diff.",
                "Unlucky.",
                "That's tragic.",
                "Not my problem.",
            ],
            "attack": [
                "That's a grief.",
                "Unlucky timing.",
                "Hate to see that.",
            ],
        }
        
        # Last words when exploding
        self._explosion_phrases: list[str] = [
            "Unlucky RNG, comp was correct.",
            "I played for late and didn't make it.",
            "Sometimes the game just says no.",
            "Good build, bad rolls.",
            "GG, see you next lobby.",
            "The macro was right, the outcome wasn't.",
        ]
    
    @property
    def name(self) -> str:
        """Return your bot's display name."""
        return "tft bot"
    
    def _calculate_ek_probability(self, view: BotView) -> float:
        """
        Calculate the probability of drawing an Exploding Kitten.
        
        Returns: Probability as a float (0.0 to 1.0)
        """
        if view.draw_pile_count == 0:
            return 0.0
        
        # More accurate: Track eliminations to estimate EKs remaining
        # Start with (num_players - 1) EKs, subtract eliminations
        alive_players = len(view.other_players) + 1  # +1 for us
        
        # If we tracked num_players at game start, use that
        if self._num_players > 0:
            eliminations = self._num_players - alive_players
            estimated_eks = max(1, (self._num_players - 1) - eliminations)
        else:
            # Fallback: estimate based on current players
            estimated_eks = max(1, alive_players - 1)
        
        return estimated_eks / view.draw_pile_count
    
    def _is_deck_shuffled(self, view: BotView) -> bool:
        """
        Check if deck is in shuffled state (unknown EK position).
        
        Deck is shuffled if:
        - A shuffle was just played
        - Someone just lost (EK drawn and not defused)
        """
        return self._deck_shuffled
    
    def take_turn(self, view: BotView) -> Action:
        """
        Main turn logic implementing the strategy guide.
        
        Strategy priority:
        1. Early game (before first EK): Just draw, amass cards
        2. After first EK: Shuffle > STF > Steal > Skip Cards > Draw
        3. Probability-based: 33% (consider STF), 40% (play STF), 50% (defensive/aggressive)
        """
        
        # Reset shuffle flag if we're starting a fresh turn (not continuing from multiple turns)
        if view.my_turns_remaining == 1:
            self._just_shuffled = False
        
        ek_probability = self._calculate_ek_probability(view)
        is_shuffled = self._is_deck_shuffled(view)
        
        # Random chance to say something during turn (20% chance)
        #if random.random() < 0.2:
        view.say(random.choice(self._turn_phrases))
        
        # =====================================================================
        # EARLY GAME: Before first EK drawn - JUST DRAW
        # =====================================================================
        if not self._first_ek_drawn:
            # 14% chance first player draws EK - acceptable risk
            # 80% chance someone else draws it first
            # Don't waste cards, just amass them
            return DrawCardAction()
        
        # =====================================================================
        # AFTER FIRST EK: Priority order strategy
        # =====================================================================
        
        # 1. PRIORITY: Shuffle (resets odds, best move)
        # But don't waste Shuffle cards - only shuffle once per turn cycle
        shuffle_cards = view.get_cards_of_type("ShuffleCard")
        if shuffle_cards and view.draw_pile_count > 0 and not self._just_shuffled:
            view.say(random.choice(self._shuffle_phrases))
            self._just_shuffled = True  # Mark that we shuffled
            return PlayCardAction(card=shuffle_cards[0])
        
        # 2. PRIORITY: If we know EK is on top, use Skip/Shuffle immediately
        if self._ek_on_top:
            # EK is on top - avoid drawing!
            skip_cards = view.get_cards_of_type("SkipCard")
            if skip_cards:
                view.say("Avoiding the top!")
                self._ek_on_top = False  # Reset after using skip
                return PlayCardAction(card=skip_cards[0])
            
            # No skip? Use shuffle to reset
            shuffle_cards = view.get_cards_of_type("ShuffleCard")
            if shuffle_cards and view.draw_pile_count > 0:
                view.say("Shuffling to avoid EK!")
                self._ek_on_top = False
                self._just_shuffled = True
                return PlayCardAction(card=shuffle_cards[0])
        
        # 3. PRIORITY: See the Future (great information)
        # Use based on probability thresholds
        stf_cards = view.get_cards_of_type("SeeTheFutureCard")
        if stf_cards and view.draw_pile_count > 0:
            # At 40%+, always use STF (even if only one)
            if ek_probability >= 0.40:
                view.say(random.choice(self._stf_phrases))
                return PlayCardAction(card=stf_cards[0])
            # At 33%+, use STF if we have more than one
            elif ek_probability >= 0.33 and len(stf_cards) > 1:
                view.say(random.choice(self._stf_phrases))
                return PlayCardAction(card=stf_cards[0])
            # At 25%+, use STF if we have 3+ (very safe to use)
            elif ek_probability >= 0.25 and len(stf_cards) >= 3:
                view.say(random.choice(self._stf_phrases))
                return PlayCardAction(card=stf_cards[0])
        
        # 4. PRIORITY: Play combos to steal valuable cards
        combos = self._find_possible_combos(view.my_hand)
        if combos and view.other_players:
            # Prefer three-of-a-kind (stronger - can name a card)
            for combo_type, combo_cards in combos:
                if combo_type == "three_of_a_kind":
                    valid_targets = [
                        pid for pid in view.other_players
                        if view.other_player_card_counts.get(pid, 0) > 0
                    ]
                    if valid_targets:
                        target = max(valid_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                        view.say("Combo time!")
                        return PlayComboAction(cards=combo_cards, target_player_id=target)
            
            # Fall back to two-of-a-kind
            combo_type, combo_cards = combos[0]
            if combo_type in ("two_of_a_kind", "three_of_a_kind"):
                valid_targets = [
                    pid for pid in view.other_players
                    if view.other_player_card_counts.get(pid, 0) > 0
                ]
                if valid_targets:
                    target = max(valid_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                    return PlayComboAction(cards=combo_cards, target_player_id=target)
        
        # 5. PRIORITY: Steal (Favor) - try to get Shuffle/STF/Defuse
        # Use when probability is getting high or we're low on cards
        # Also use if opponent has significantly more cards
        favor_cards = view.get_cards_of_type("FavorCard")
        if favor_cards:
            # Check if Favor can actually be played (targets must have cards)
            playable_favors = [
                card for card in favor_cards
                if card.can_play(view, is_own_turn=True)
            ]
            if playable_favors and view.other_players:
                # Find targets that actually have cards
                valid_targets = [
                    pid for pid in view.other_players
                    if view.other_player_card_counts.get(pid, 0) > 0
                ]
                if valid_targets:
                    # Target player with most cards (more likely to have Shuffle/STF)
                    target = max(valid_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                    target_card_count = view.other_player_card_counts.get(target, 0)
                    my_card_count = len(view.my_hand)
                    
                    # Steal if: high probability, low cards, or opponent has way more cards
                    should_steal = (
                        ek_probability >= 0.25 or 
                        my_card_count < 5 or 
                        (target_card_count >= my_card_count + 3)
                    )
                    
                    if should_steal:
                        view.say(random.choice(self._steal_phrases))
                        return PlayCardAction(card=playable_favors[0], target_player_id=target)
        
        # 6. PRIORITY: Skip Cards (defensive or when under attack)
        # Use Skip (passive) when under attack or playing defensively
        if view.my_turns_remaining > 1:
            skip_cards = view.get_cards_of_type("SkipCard")
            if skip_cards:
                # Defensive mode: use Skip to pass turn
                if ek_probability >= 0.50:
                    view.say(random.choice(self._skip_phrases))
                    return PlayCardAction(card=skip_cards[0])
                # Under attack: use Skip to end one turn
                elif view.my_turns_remaining > 1:
                    view.say(random.choice(self._skip_phrases))
                    return PlayCardAction(card=skip_cards[0])
        
        # 7. AGGRESSIVE MODE: At 50%+ probability, play aggressively
        if ek_probability >= 0.50:
            # Try to steal from one target repeatedly
            favor_cards = view.get_cards_of_type("FavorCard")
            if favor_cards:
                # Check if Favor can actually be played (targets must have cards)
                playable_favors = [
                    card for card in favor_cards
                    if card.can_play(view, is_own_turn=True)
                ]
                if playable_favors and view.other_players:
                    # Find targets that actually have cards
                    valid_targets = [
                        pid for pid in view.other_players
                        if view.other_player_card_counts.get(pid, 0) > 0
                    ]
                    if valid_targets:
                        # Target player with most cards
                        target = max(valid_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                        view.say(random.choice(self._steal_phrases))
                        return PlayCardAction(card=playable_favors[0], target_player_id=target)
            
            # Use Skip to pass turn (prefer Skip over Attack to avoid retaliation)
            skip_cards = view.get_cards_of_type("SkipCard")
            if skip_cards:
                view.say(random.choice(self._skip_phrases))
                return PlayCardAction(card=skip_cards[0])
        
        # 8. END GAME: Save Attacks for when we have 3+ and opponent has many cards
        # Don't pass 7-turn attack to someone with 1 card and high EK chance
        attack_cards = view.get_cards_of_type("AttackCard")
        if attack_cards and len(attack_cards) >= 3 and view.other_players:
            # Find opponent with 6+ cards (good target for attack chain)
            good_targets = [
                pid for pid in view.other_players
                if view.other_player_card_counts.get(pid, 0) >= 6
            ]
            if good_targets:
                target = max(good_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                view.say(random.choice(self._attack_phrases))
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        # 9. Use Attack if we have 2+ and opponent has 4+ cards (moderate aggression)
        if attack_cards and len(attack_cards) >= 2 and view.other_players:
            good_targets = [
                pid for pid in view.other_players
                if view.other_player_card_counts.get(pid, 0) >= 4
            ]
            if good_targets and ek_probability >= 0.40:
                target = max(good_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                view.say(random.choice(self._attack_phrases))
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        # =====================================================================
        # DEFAULT: Draw a card (if shuffled and probability < 33%, just draw)
        # =====================================================================
        if is_shuffled and ek_probability < 0.33:
            # Low probability, shuffled state - just draw
            return DrawCardAction()
        
        # Otherwise, we've exhausted our options - draw
        return DrawCardAction()
    
    def _find_possible_combos(
        self, hand: tuple[Card, ...]
    ) -> list[tuple[str, tuple[Card, ...]]]:
        """
        Find all possible combos in the given hand.
        
        Returns: List of (combo_type, cards) tuples
        """
        combos: list[tuple[str, tuple[Card, ...]]] = []
        
        # Filter to only cards that can combo
        combo_cards = [c for c in hand if c.can_combo()]
        
        if not combo_cards:
            return combos
        
        # Group cards by type
        by_type: dict[str, list[Card]] = {}
        for card in combo_cards:
            if card.card_type not in by_type:
                by_type[card.card_type] = []
            by_type[card.card_type].append(card)
        
        # Check for two-of-a-kind and three-of-a-kind
        for card_type, cards_of_type in by_type.items():
            if len(cards_of_type) >= 3:
                combos.append(("three_of_a_kind", tuple(cards_of_type[:3])))
            elif len(cards_of_type) >= 2:
                combos.append(("two_of_a_kind", tuple(cards_of_type[:2])))
        
        # Check for five different card types
        if len(by_type) >= 5:
            five_cards: list[Card] = []
            for card_type in list(by_type.keys())[:5]:
                five_cards.append(by_type[card_type][0])
            combos.append(("five_different", tuple(five_cards)))
        
        return combos
    
    def on_event(self, event: GameEvent, view: BotView) -> None:
        """
        Track game events for strategy decisions.
        
        Key tracking:
        - First EK drawn (triggers strategy change)
        - Deck shuffles (resets to shuffled state)
        - Player eliminations (updates EK count estimate)
        """
        # Skip chat events to avoid infinite loops
        if event.event_type == EventType.BOT_CHAT:
            return
        
        # Track first EK drawn
        if event.event_type == EventType.EXPLODING_KITTEN_DRAWN:
            if not self._first_ek_drawn:
                self._first_ek_drawn = True
                self._deck_shuffled = False  # EK position is now known (until shuffle)
            # Comment on explosions (15% chance, if not us)
            if random.random() < 0.15 and event.player_id != view.my_id:
                view.say(random.choice(self._reaction_phrases["explosion"]))
        
        # Track deck shuffles (resets to shuffled/unknown state)
        if event.event_type == EventType.DECK_SHUFFLED:
            self._deck_shuffled = True
            self._ek_on_top = False  # Reset EK tracking after shuffle
            # Note: We don't reset _just_shuffled here because we want to prevent
            # shuffling twice in the same multi-turn sequence. It resets when
            # we start a new turn cycle (my_turns_remaining == 1)
        
        # Track what we saw with See the Future
        if event.event_type == EventType.CARDS_PEEKED:
            if event.player_id == view.my_id:
                card_types = event.data.get("card_types", [])
                self._last_peeked_cards = card_types
                # Check if EK is in the top 3 cards
                if card_types and "ExplodingKittenCard" in card_types:
                    # Check if it's the first card (top of deck)
                    if card_types[0] == "ExplodingKittenCard":
                        self._ek_on_top = True
                    # Or if it's in top 3 and probability is high, be cautious
                    else:
                        # Calculate current EK probability
                        current_ek_prob = self._calculate_ek_probability(view)
                        if current_ek_prob >= 0.40:
                            # EK is in top 3, be careful
                            self._ek_on_top = True  # Conservative: treat as if on top
        
        # Track player eliminations (updates EK count estimate)
        if event.event_type == EventType.PLAYER_ELIMINATED:
            # EK was drawn and not defused - deck is now shuffled state
            self._deck_shuffled = True
            # Comment on eliminations (15% chance)
            if random.random() < 0.15 and event.player_id != view.my_id:
                view.say(random.choice(self._reaction_phrases["elimination"]))
        
        # Track cards played by opponents (for strategy)
        if event.event_type == EventType.CARD_PLAYED:
            player_id = event.player_id
            if player_id != view.my_id and player_id in view.other_players:
                card_type = event.data.get("card_type", "")
                if player_id not in self._opponent_card_history:
                    self._opponent_card_history[player_id] = []
                self._opponent_card_history[player_id].append(card_type)
                
                # Comment on attacks (10% chance)
                if card_type == "AttackCard" and random.random() < 0.1:
                    view.say(random.choice(self._reaction_phrases["attack"]))
        
        # Track number of players (for EK count estimation)
        if event.event_type == EventType.GAME_START:
            self._num_players = len(view.other_players) + 1
            self._num_eks_remaining = self._num_players - 1
    
    def react(self, view: BotView, triggering_event: GameEvent) -> Action | None:
        """
        Decide whether to play a Nope card.
        
        Strategy: Save Nope for critical actions:
        - Attacks targeting us (especially when we have few cards)
        - Favors targeting us (especially when we have valuable cards)
        - Combos that would steal from us
        """
        nope_cards = view.get_cards_of_type("NopeCard")
        
        if not nope_cards:
            return None
        
        # Get the event type and data
        event_type = triggering_event.event_type
        event_data = triggering_event.data
        
        # Always Nope attacks targeting us (high priority)
        if event_type == EventType.CARD_PLAYED:
            card_type = event_data.get("card_type", "")
            target_id = event_data.get("target_player_id")
            
            if card_type == "AttackCard" and target_id == view.my_id:
                view.say(random.choice(self._nope_phrases))
                return PlayCardAction(card=nope_cards[0])
            
            # Nope favors targeting us (especially if we have few cards)
            if card_type == "FavorCard" and target_id == view.my_id:
                # More likely to nope if we have few cards or valuable cards
                if len(view.my_hand) < 5 or view.has_card_type("DefuseCard"):
                    view.say(random.choice(self._nope_phrases))
                    return PlayCardAction(card=nope_cards[0])
        
        # Nope combos targeting us
        if event_type == EventType.COMBO_PLAYED:
            combo_target = event_data.get("target_player_id")
            if combo_target == view.my_id:
                view.say(random.choice(self._nope_phrases))
                return PlayCardAction(card=nope_cards[0])
        
        # Be conservative - save Nopes for when we really need them
        return None
    
    def choose_defuse_position(self, view: BotView, draw_pile_size: int) -> int:
        """
        Choose where to put the Exploding Kitten back.
        
        Strategy from guide:
        - Put it back randomly (not bottom, not top)
        - Common for next 2-3 players to play Skip/Reverse
        - Don't want to accidentally end up where card is being drawn
        - Still trying to preserve cards, want to go back to playing odds
        """
        view.say(random.choice(self._defuse_phrases))
        
        # Strategy: NEVER put at position 0 (top) unless absolutely forced
        # Position 0 = next draw, which is too obvious
        
        if draw_pile_size <= 1:
            # Edge case: only 1 card in deck, must put at position 0
            # But this should rarely happen - if it does, we have no choice
            return 0
        elif draw_pile_size == 2:
            # Only 2 cards: put at position 1 (not top)
            return 1
        elif draw_pile_size == 3:
            # 3 cards: put at position 1 or 2 (avoid top)
            return random.randint(1, 2)
        elif draw_pile_size <= 5:
            # Small deck: avoid top position (0), put at 1, 2, or 3
            return random.randint(1, min(3, draw_pile_size - 1))
        elif draw_pile_size <= 7:
            # Medium deck: avoid top 2 positions (0 and 1)
            return random.randint(2, draw_pile_size - 1)
        else:
            # Larger deck: avoid top 3 positions (where Skip might put us)
            # Put it in positions 4 to 75% of deck
            min_pos = 4
            max_pos = max(min_pos + 1, int(draw_pile_size * 0.75))
            return random.randint(min_pos, max_pos)
    
    def choose_card_to_give(self, view: BotView, requester_id: str) -> Card:
        """
        Choose which card to give when hit by Favor.
        
        Strategy from guide:
        - If only EKs remain, give useless cards (Shuffle/STF) - they seem useless
          but if someone steals from you, you have something useless to pass off
        - Otherwise: Cat cards > Action cards > Defuse/Nope (last resort)
        """
        hand = list(view.my_hand)
        ek_probability = self._calculate_ek_probability(view)
        
        # Strategy tip: If only EKs left (very high probability), keep useless cards
        # (Shuffle/STF) to give away if stolen from
        if ek_probability > 0.80:
            # Very high EK probability - might be only EKs left
            # Keep Shuffle/STF as "useless" cards to give if stolen from
            useless_cards = [
                c for c in hand
                if c.card_type in ("ShuffleCard", "SeeTheFutureCard")
            ]
            if useless_cards and len(hand) > 3:
                # Give useless card if we have other cards
                return useless_cards[0]
        
        # Random chance to comment when giving a card (30% chance)
        if random.random() < 0.3:
            view.say(random.choice(self._give_card_phrases))
        
        # 1. Try to give a cat card (least valuable)
        cat_cards = [c for c in hand if "Cat" in c.card_type]
        if cat_cards:
            return cat_cards[0]
        
        # 2. Give any action card that's not Defuse or Nope
        safe_to_give = [
            c for c in hand
            if c.card_type not in ("DefuseCard", "NopeCard")
        ]
        if safe_to_give:
            return safe_to_give[0]
        
        # 3. Last resort: give something (must give a card)
        return hand[0]
    
    def on_explode(self, view: BotView) -> None:
        """
        Say your last words before exploding.
        
        Strategy note: You played the odds and lost. Take comfort that
        most of the time your risk would have paid off.
        """
        view.say(random.choice(self._explosion_phrases))
