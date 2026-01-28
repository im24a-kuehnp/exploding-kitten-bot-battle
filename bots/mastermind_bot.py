"""
==============================================================================
MASTERMIND BOT - Survival-First Strategic Bot
==============================================================================

Core Principles:
1. Survival Over Aggression: Every action must reduce immediate explosion risk
2. Deck Awareness: Track risk as deck thins, play cautiously in late-game
3. Information is Power: Use STF to plan multiple turns ahead
4. Defuse Cards are for Control: Strategic placement to target weak players
5. Card Evaluation: Different priorities for early/mid vs late game
6. Resource Conservation: Save Skip/Attack until EK is imminent
7. Hand Disruption: Use Favor/Pairs to deplete opponents, especially when they have few cards
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Sequence

from game.bots.base import (
    Action,
    Bot,
    DrawCardAction,
    PlayCardAction,
    PlayComboAction,
)
from game.bots.view import BotView
from game.cards.base import Card
from game.history import EventType, GameEvent

# Card type constants
STF = "SeeTheFutureCard"
SKIP = "SkipCard"
ATTACK = "AttackCard"
SHUFFLE = "ShuffleCard"
FAVOR = "FavorCard"
NOPE = "NopeCard"
DEFUSE = "DefuseCard"
EXPLODING = "ExplodingKittenCard"


class MastermindBot(Bot):
    """
    Mastermind bot with advanced strategic decision-making.
    
    Core strategies:
    1. Combo-first: Prioritize combos (harder to Nope, high value)
    2. Information warfare: Use STF strategically, shuffle when needed
    3. Adaptive phases: Early (draw), Mid (control), Late (survival/aggression)
    4. Opponent modeling: Track behavior, target weakest
    5. Probability mastery: Precise EK risk calculation with peek awareness
    6. Resource optimization: Smart card usage based on game state
    """
    
    def __init__(self) -> None:
        """Initialize bot with comprehensive state tracking."""
        # Game state
        self._turn_count: int = 0
        self._initial_player_count: int | None = None
        self._first_ek_seen: bool = False
        self._kittens_removed: int = 0
        self._defuses_used: int = 0
        
        # Information tracking
        self._known_top_cards: tuple[str, ...] | None = None
        self._deck_shuffled: bool = True  # Start in shuffled state
        self._last_shuffle_turn: int = -1
        
        # Opponent modeling
        self._opponent_card_history: dict[str, list[str]] = {}
        self._opponent_card_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self._opponent_defense_prob: dict[str, float] = {}  # Probability they have defense
        self._opponent_nope_prob: dict[str, float] = {}  # Probability they have Nope
        self._opponent_defuses_used: dict[str, bool] = {}  # Track who has used defuses
        self._opponent_last_seen_hand_size: dict[str, int] = {}  # Track hand sizes
        
        # Strategic state
        self._game_phase: str = "early"  # early, mid, late
        self._my_defuses: int = 0
        self._just_peeked: bool = False
        self._stf_planning_turns: int = 0  # How many turns ahead we're planning
        
    @property
    def name(self) -> str:
        """Return bot name."""
        return "Mastermind"
    
    # ========================================================================
    # MAIN TURN LOGIC
    # ========================================================================
    
    def take_turn(self, view: BotView) -> Action:
        """Main turn decision logic with adaptive strategy."""
        self._update_game_state(view)
        self._turn_count += 1
        
        # Update game phase
        self._update_game_phase(view)
        
        # Calculate precise EK risk
        ek_risk = self._calculate_ek_risk(view)
        
        # Early game: conservative draw strategy
        if self._game_phase == "early":
            return self._early_game_strategy(view, ek_risk)
        
        # Mid game: information and control
        if self._game_phase == "mid":
            return self._midgame_strategy(view, ek_risk)
        
        # Late game: survival and aggression
        return self._late_game_strategy(view, ek_risk)
    
    # ========================================================================
    # PROBABILITY CALCULATION
    # ========================================================================
    
    def _calculate_ek_risk(self, view: BotView) -> float:
        """
        Calculate precise EK drawing risk with peek awareness.
        
        Incorporates:
        - Known top cards from STF
        - Defuse cushioning (having defuses reduces effective risk)
        - Accurate EK count tracking
        """
        if view.draw_pile_count == 0:
            return 0.0
        
        # Calculate baseline EK count
        players = self._initial_player_count or len(view.turn_order)
        baseline_kittens = max(1, players - 1 - self._kittens_removed)
        
        # Peek-aware adjustment
        if self._known_top_cards:
            visible = self._known_top_cards
            if visible and visible[0] == EXPLODING:
                # EK is definitely next - 100% risk
                return 1.0
            if EXPLODING in visible:
                # EK is in top 3, calculate probability within peek
                ek_position = visible.index(EXPLODING)
                # If EK is 2nd or 3rd, risk is lower (need draws to reach it)
                if ek_position == 0:
                    return 1.0
                elif ek_position == 1:
                    return 0.5  # 50% chance next draw hits it
                else:
                    return 0.33  # 33% chance
            # No EK in peek, recalculate with unseen cards
            unseen = max(1, view.draw_pile_count - len(visible))
            risk = min(1.0, baseline_kittens / float(unseen))
        else:
            # No peek info - use baseline
            risk = min(1.0, baseline_kittens / float(view.draw_pile_count))
        
        # Defuse cushioning: having defuses reduces effective risk
        defuses = view.count_cards_of_type(DEFUSE)
        if defuses > 0:
            # Each defuse reduces risk perception (we can survive one EK)
            risk *= max(0.3, 1.0 - (defuses * 0.25))
        
        return risk
    
    # ========================================================================
    # GAME PHASE STRATEGIES
    # ========================================================================
    
    def _early_game_strategy(self, view: BotView, ek_risk: float) -> Action:
        """
        Early game: Focus on building hand, conserve cards.
        
        Strategy:
        - Draw cards to build hand
        - Use STF proactively to plan ahead (not just avoid next draw)
        - Only use combos if opponent has few cards (high chance of Defuse)
        - Avoid wasting Skip/Attack (save for when EK is imminent)
        """
        # First turn: always draw (low risk, need cards)
        if self._turn_count == 1:
            return DrawCardAction()
        
        # Priority 1: SURVIVAL - If EK is on top, avoid it
        if self._known_top_cards and self._known_top_cards[0] == EXPLODING:
            return self._avoid_ek_on_top(view)
        
        # Priority 2: Information gathering with STF (plan multiple turns ahead)
        if not self._known_top_cards and not self._just_peeked:
            stf_cards = view.get_cards_of_type(STF)
            if stf_cards and view.draw_pile_count > 0 and ek_risk >= 0.20:
                view.say("Planning ahead...")
                self._just_peeked = True
                return PlayCardAction(card=stf_cards[0])
        
        # Priority 3: Use combos to steal from players with few cards (high Defuse chance)
        if view.other_players:
            weak_targets = [
                pid for pid in view.other_players
                if view.other_player_card_counts.get(pid, 0) <= 2
            ]
            if weak_targets:
                combo_action = self._try_combo_against_targets(view, weak_targets)
                if combo_action:
                    return combo_action
        
        # Priority 4: If risk is still low, keep drawing to build hand
        if ek_risk < 0.25 and not self._first_ek_seen:
            return DrawCardAction()
        
        # Transition to midgame strategy
        return self._midgame_strategy(view, ek_risk)
    
    def _midgame_strategy(self, view: BotView, ek_risk: float) -> Action:
        """
        Midgame: Start disrupting opponents, control turn order.
        
        Strategy:
        - Use Favor/Pairs to reduce opponent hand size (makes Defuse easier to steal)
        - Track which players have used Defuses
        - Use Attack to push dangerous draws onto unprepared opponents
        - Continue information gathering
        """
        # Priority 1: SURVIVAL - If EK is on top, avoid it
        if self._known_top_cards and self._known_top_cards[0] == EXPLODING:
            return self._avoid_ek_on_top(view)
        
        # Priority 2: Hand disruption - target players with few cards (high Defuse chance)
        if view.other_players:
            weak_targets = [
                pid for pid in view.other_players
                if view.other_player_card_counts.get(pid, 0) <= 3
                and not self._opponent_defuses_used.get(pid, False)
            ]
            if weak_targets:
                # Use combos first (harder to Nope)
                combo_action = self._try_combo_against_targets(view, weak_targets)
                if combo_action:
                    return combo_action
                
                # Use Favor to deplete opponents
                favor_cards = view.get_cards_of_type(FAVOR)
                if favor_cards:
                    target = min(weak_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
                    view.say("Depleting opponent resources.")
                    return PlayCardAction(card=favor_cards[0], target_player_id=target)
        
        # Priority 3: Information gathering (plan ahead)
        if not self._known_top_cards and not self._just_peeked and ek_risk >= 0.25:
            stf_cards = view.get_cards_of_type(STF)
            if stf_cards and view.draw_pile_count > 0:
                self._just_peeked = True
                view.say("Gathering intelligence...")
                return PlayCardAction(card=stf_cards[0])
        
        # Priority 4: Shuffle to disrupt opponents who may have peeked
        if self._known_top_cards and EXPLODING in self._known_top_cards:
            shuffle_cards = view.get_cards_of_type(SHUFFLE)
            if shuffle_cards and self._turn_count > self._last_shuffle_turn + 2:
                view.say("Disrupting opponent intel.")
                self._last_shuffle_turn = self._turn_count
                return PlayCardAction(card=shuffle_cards[0])
        
        # Priority 5: General stealing (if we have large hand and risk is low)
        if len(view.my_hand) >= 6 and ek_risk < 0.35:
            steal_action = self._try_steal(view, ek_risk)
            if steal_action:
                return steal_action
        
        # Priority 6: Defensive play if risk is high (but save Skip/Attack for EK on top)
        if ek_risk >= 0.50:
            defensive = self._play_defensive(view)
            if defensive:
                return defensive
        
        # Default: draw if risk is acceptable
        if ek_risk < 0.40:
            return DrawCardAction()
        
        # High risk: use defensive cards
        return self._play_defensive(view) or DrawCardAction()
    
    def _late_game_strategy(self, view: BotView, ek_risk: float) -> Action:
        """
        Late game: Survival first, then force opponents to take multiple draws.
        
        Strategy:
        - Skip/Attack cards are now vital - use when EK is imminent
        - Force opponents to take multiple draws from thin deck
        - Defuse cards are most valuable - protect them
        - Use Attack to push dangerous draws onto unprepared opponents
        """
        # Priority 1: SURVIVAL - If EK is on top, use Skip/Attack (this is when they're vital)
        if self._known_top_cards and self._known_top_cards[0] == EXPLODING:
            return self._avoid_ek_on_top(view)
        
        # Priority 2: If risk is very high and deck is thin, use Skip/Attack
        if ek_risk >= 0.60 and view.draw_pile_count <= 10:
            defensive = self._play_defensive(view)
            if defensive:
                return defensive
        
        # Priority 3: Use Attack to force multiple draws from thin deck
        if ek_risk >= 0.50 and view.draw_pile_count <= 15 and len(view.other_players) > 1:
            attack_cards = view.get_cards_of_type(ATTACK)
            if attack_cards:
                # Target weakest opponent (likely to lack defense)
                target = self._choose_weakest_target(view)
                if target:
                    view.say("Forcing multiple draws from thin deck.")
                    return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        # Priority 4: Shuffle to reset odds (if not recently shuffled)
        shuffle_cards = view.get_cards_of_type(SHUFFLE)
        if shuffle_cards and ek_risk >= 0.50 and self._turn_count > self._last_shuffle_turn + 1:
            view.say("Critical shuffle!")
            self._last_shuffle_turn = self._turn_count
            return PlayCardAction(card=shuffle_cards[0])
        
        # Priority 5: Steal from players with few cards (high Defuse chance)
        if view.other_players:
            weak_targets = [
                pid for pid in view.other_players
                if view.other_player_card_counts.get(pid, 0) <= 2
            ]
            if weak_targets:
                combo_action = self._try_combo_against_targets(view, weak_targets)
                if combo_action:
                    return combo_action
        
        # Priority 6: Defensive play (survival first)
        if ek_risk >= 0.45:
            defensive = self._play_defensive(view)
            if defensive:
                return defensive
        
        # Priority 7: Information gathering (if we have STF and no info)
        if not self._known_top_cards:
            stf_cards = view.get_cards_of_type(STF)
            if stf_cards and view.draw_pile_count > 0:
                view.say("Last chance intel.")
                return PlayCardAction(card=stf_cards[0])
        
        # Default: draw (we've exhausted options)
        return DrawCardAction()
    
    # ========================================================================
    # SPECIFIC ACTION STRATEGIES
    # ========================================================================
    
    def _avoid_ek_on_top(self, view: BotView) -> Action:
        """Avoid drawing when EK is known to be on top."""
        # Try Skip first (safest)
        skip_cards = view.get_cards_of_type(SKIP)
        if skip_cards:
            view.say("Dodging danger!")
            return PlayCardAction(card=skip_cards[0])
        
        # Try Shuffle to reset
        shuffle_cards = view.get_cards_of_type(SHUFFLE)
        if shuffle_cards:
            view.say("Emergency shuffle!")
            self._last_shuffle_turn = self._turn_count
            return PlayCardAction(card=shuffle_cards[0])
        
        # Try Attack to pass turns (risky but better than drawing EK)
        attack_cards = view.get_cards_of_type(ATTACK)
        if attack_cards and view.other_players:
            target = self._choose_best_target(view, prefer_weak=True)
            if target:
                view.say("Passing the risk...")
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        # No options - must draw (unlucky)
        return DrawCardAction()
    
    def _try_best_combo(self, view: BotView) -> Action | None:
        """
        Try to play the best available combo.
        
        Priority:
        1. Three-of-a-kind (targeted steal, highest value)
        2. Two-of-a-kind (random steal, still valuable)
        3. Five-different (only if discard has premium card)
        """
        if not view.other_players:
            return None
        
        combos = self._find_all_combos(view.my_hand)
        if not combos:
            return None
        
        # Find valid targets
        valid_targets = [
            pid for pid in view.other_players
            if view.other_player_card_counts.get(pid, 0) > 0
        ]
        if not valid_targets:
            return None
        
        target = self._choose_best_target(view, prefer_weak=False)
        if not target:
            target = valid_targets[0]
        
        # Priority: three-of-a-kind > two-of-a-kind > five-different
        for combo_type, cards in combos:
            if combo_type == "three_of_a_kind":
                view.say("Targeted strike!")
                return PlayComboAction(cards=cards, target_player_id=target)
        
        for combo_type, cards in combos:
            if combo_type == "two_of_a_kind":
                view.say("Random grab!")
                return PlayComboAction(cards=cards, target_player_id=target)
        
        # Five-different only if discard has premium card
        for combo_type, cards in combos:
            if combo_type == "five_different":
                top_discard = view.discard_pile[-1] if view.discard_pile else None
                if top_discard and top_discard.card_type in (DEFUSE, NOPE, SHUFFLE, STF):
                    view.say("Premium recovery!")
                    return PlayComboAction(cards=cards)
        
        return None
    
    def _try_steal(self, view: BotView, ek_risk: float) -> Action | None:
        """
        Try to steal cards via Favor or combos.
        
        Strategy: Prefer targeting players with few cards (high chance of Defuse).
        Goal is often to deplete opponents, not just get valuable cards.
        """
        if not view.other_players:
            return None
        
        # Prefer combos (harder to Nope)
        combo_action = self._try_best_combo(view)
        if combo_action:
            return combo_action
        
        # Use Favor if we have it and target has cards
        favor_cards = view.get_cards_of_type(FAVOR)
        if not favor_cards:
            return None
        
        valid_targets = [
            pid for pid in view.other_players
            if view.other_player_card_counts.get(pid, 0) > 0
        ]
        if not valid_targets:
            return None
        
        # Prefer targeting players with few cards (high Defuse chance)
        weak_targets = [pid for pid in valid_targets if view.other_player_card_counts.get(pid, 0) <= 3]
        if weak_targets:
            target = min(weak_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
        else:
            # Fallback: target player with most cards
            target = max(valid_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
        
        # Steal if: high risk, low cards, or opponent has significantly more
        my_cards = len(view.my_hand)
        target_cards = view.other_player_card_counts.get(target, 0)
        
        should_steal = (
            ek_risk >= 0.30 or
            my_cards < 4 or
            target_cards >= my_cards + 2 or
            target_cards <= 3  # High chance of Defuse
        )
        
        if should_steal:
            view.say("Depleting opponent.")
            return PlayCardAction(card=favor_cards[0], target_player_id=target)
        
        return None
    
    def _play_defensive(self, view: BotView) -> Action | None:
        """
        Play defensively to avoid drawing.
        
        Strategy: Skip is safest (no retaliation). Only use Attack if we have
        multiple and can target weak opponent. Save these cards for when EK is imminent.
        """
        # Skip is safest (no retaliation, ends turn)
        skip_cards = view.get_cards_of_type(SKIP)
        if skip_cards:
            view.say("Avoiding the draw.")
            return PlayCardAction(card=skip_cards[0])
        
        # Attack only if we have multiple and opponent likely lacks defense
        # In late game with thin deck, Attack can force multiple dangerous draws
        attack_cards = view.get_cards_of_type(ATTACK)
        if attack_cards and view.other_players:
            target = self._choose_weakest_target(view)
            if target:
                # Use Attack if deck is thin (forces multiple draws) or opponent is weak
                if view.draw_pile_count <= 15 or self._opponent_defense_prob.get(target, 0.5) < 0.4:
                    view.say("Defensive attack - passing risk.")
                    return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        return None
    
    def _play_aggressive(self, view: BotView, ek_risk: float) -> Action | None:
        """Play aggressively to push risk to opponents."""
        # Steal from weakest opponent
        steal_action = self._try_steal(view, ek_risk)
        if steal_action:
            return steal_action
        
        # Attack weakest opponent (likely to lack defense)
        attack_cards = view.get_cards_of_type(ATTACK)
        if attack_cards and view.other_players:
            target = self._choose_best_target(view, prefer_weak=True)
            if target:
                view.say("Applying pressure.")
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        return None
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def _update_game_state(self, view: BotView) -> None:
        """Update internal game state tracking."""
        if self._initial_player_count is None:
            self._initial_player_count = len(view.turn_order)
        
        self._my_defuses = view.count_cards_of_type(DEFUSE)
    
    def _update_game_phase(self, view: BotView) -> None:
        """Determine current game phase."""
        alive_players = len(view.other_players) + 1
        total_players = self._initial_player_count or alive_players
        
        if alive_players == total_players:
            self._game_phase = "early"
        elif alive_players >= total_players * 0.6:
            self._game_phase = "mid"
        else:
            self._game_phase = "late"
    
    def _choose_best_target(self, view: BotView, prefer_weak: bool = False) -> str | None:
        """Choose best target based on strategy."""
        if not view.other_players:
            return None
        
        valid_targets = [
            pid for pid in view.other_players
            if view.other_player_card_counts.get(pid, 0) > 0
        ]
        if not valid_targets:
            return None
        
        if prefer_weak:
            # Target weakest (fewest cards, lowest defense probability)
            return min(
                valid_targets,
                key=lambda pid: (
                    view.other_player_card_counts.get(pid, 0),
                    self._opponent_defense_prob.get(pid, 0.5)
                )
            )
        else:
            # Target strongest (most cards, more valuable steals)
            return max(
                valid_targets,
                key=lambda pid: view.other_player_card_counts.get(pid, 0)
            )
    
    def _choose_weakest_target(self, view: BotView) -> str | None:
        """Choose weakest target (fewest cards, no defuse used)."""
        if not view.other_players:
            return None
        
        valid_targets = [
            pid for pid in view.other_players
            if view.other_player_card_counts.get(pid, 0) > 0
        ]
        if not valid_targets:
            return None
        
        # Prefer targets who haven't used defuse and have few cards
        weak_candidates = [
            pid for pid in valid_targets
            if not self._opponent_defuses_used.get(pid, False)
        ]
        if weak_candidates:
            return min(weak_candidates, key=lambda pid: view.other_player_card_counts.get(pid, 0))
        
        # Fallback to any weak target
        return min(valid_targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
    
    def _try_combo_against_targets(self, view: BotView, targets: list[str]) -> Action | None:
        """Try to play combo against specific targets."""
        if not targets:
            return None
        
        combos = self._find_all_combos(view.my_hand)
        if not combos:
            return None
        
        # Choose weakest target from provided list
        target = min(targets, key=lambda pid: view.other_player_card_counts.get(pid, 0))
        
        # Priority: three-of-a-kind > two-of-a-kind
        for combo_type, cards in combos:
            if combo_type == "three_of_a_kind":
                view.say("Targeted strike on weak opponent!")
                return PlayComboAction(cards=cards, target_player_id=target)
        
        for combo_type, cards in combos:
            if combo_type == "two_of_a_kind":
                view.say("Random grab from weak opponent!")
                return PlayComboAction(cards=cards, target_player_id=target)
        
        return None
    
    def _find_all_combos(self, hand: tuple[Card, ...]) -> list[tuple[str, tuple[Card, ...]]]:
        """Find all possible combos in hand."""
        combos: list[tuple[str, tuple[Card, ...]]] = []
        combo_cards = [c for c in hand if c.can_combo()]
        
        if not combo_cards:
            return combos
        
        # Group by type
        by_type: dict[str, list[Card]] = {}
        for card in combo_cards:
            if card.card_type not in by_type:
                by_type[card.card_type] = []
            by_type[card.card_type].append(card)
        
        # Find pairs and triplets
        for card_type, cards_of_type in by_type.items():
            if len(cards_of_type) >= 3:
                combos.append(("three_of_a_kind", tuple(cards_of_type[:3])))
            elif len(cards_of_type) >= 2:
                combos.append(("two_of_a_kind", tuple(cards_of_type[:2])))
        
        # Find five different
        if len(by_type) >= 5:
            five_cards: list[Card] = []
            for card_type in list(by_type.keys())[:5]:
                five_cards.append(by_type[card_type][0])
            combos.append(("five_different", tuple(five_cards)))
        
        return combos
    
    # ========================================================================
    # EVENT TRACKING
    # ========================================================================
    
    def on_event(self, event: GameEvent, view: BotView) -> None:
        """Track game events for strategic decisions."""
        if event.event_type == EventType.BOT_CHAT:
            return
        
        # Track peeked cards
        if event.event_type == EventType.CARDS_PEEKED and event.player_id == view.my_id:
            card_types: Sequence[str] = tuple(event.data.get("card_types", ()))
            self._known_top_cards = tuple(card_types)
            self._just_peeked = True
        
        # Reset peek info on shuffle or EK insertion
        if event.event_type in (EventType.DECK_SHUFFLED, EventType.EXPLODING_KITTEN_INSERTED):
            self._known_top_cards = None
            self._deck_shuffled = True
        
        # Update peek info when cards are drawn
        if event.event_type == EventType.CARD_DRAWN and self._known_top_cards:
            self._known_top_cards = self._known_top_cards[1:] if len(self._known_top_cards) > 1 else None
            self._just_peeked = False
        
        # Track first EK
        if event.event_type == EventType.EXPLODING_KITTEN_DRAWN:
            if not self._first_ek_seen:
                self._first_ek_seen = True
                self._deck_shuffled = False
        
        # Track eliminations (EK consumed)
        if event.event_type == EventType.PLAYER_ELIMINATED:
            self._kittens_removed += 1
            self._deck_shuffled = True
        
        # Track opponent behavior
        if event.event_type == EventType.CARD_PLAYED:
            card_type = event.data.get("card_type", "")
            player_id = event.player_id
            if player_id and player_id != view.my_id and player_id in view.other_players:
                self._opponent_card_history.setdefault(player_id, []).append(card_type)
                self._opponent_card_counts[player_id][card_type] += 1
                self._update_opponent_model(player_id)
        
        # Track defuse usage (critical for strategic placement)
        if event.event_type == EventType.DEFUSE_USED:
            player_id = event.player_id
            if player_id:
                self._opponent_defuses_used[player_id] = True
                if player_id == view.my_id:
                    self._defuses_used += 1
        
        # Track hand sizes (for identifying weak targets)
        if event.event_type == EventType.CARD_PLAYED or event.event_type == EventType.CARD_DRAWN:
            player_id = event.player_id
            if player_id and player_id in view.other_players:
                # Update last seen hand size (approximate)
                card_count = view.other_player_card_counts.get(player_id, 0)
                if card_count > 0:
                    self._opponent_last_seen_hand_size[player_id] = card_count
    
    def _update_opponent_model(self, player_id: str) -> None:
        """Update opponent behavior model."""
        history = self._opponent_card_counts[player_id]
        total = sum(history.values())
        if total == 0:
            return
        
        # Calculate defense probability
        defensive_cards = history[SKIP] + history[ATTACK] + history[SHUFFLE] + history[DEFUSE]
        self._opponent_defense_prob[player_id] = min(0.9, defensive_cards / max(1, total))
        
        # Calculate Nope probability
        nope_count = history[NOPE]
        self._opponent_nope_prob[player_id] = min(0.8, nope_count / max(1, total))
    
    # ========================================================================
    # REACTIONS
    # ========================================================================
    
    def react(self, view: BotView, triggering_event: GameEvent) -> Action | None:
        """Decide whether to Nope an action."""
        nope_cards = view.get_cards_of_type(NOPE)
        if not nope_cards:
            return None
        
        event_type = triggering_event.event_type
        event_data = triggering_event.data
        
        # Always Nope attacks targeting us
        if event_type == EventType.CARD_PLAYED:
            card_type = event_data.get("card_type", "")
            target_id = event_data.get("target_player_id")
            
            if card_type == ATTACK and target_id == view.my_id:
                view.say("Nope! Not taking that.")
                return PlayCardAction(card=nope_cards[0])
            
            # Nope favors if we have few cards or valuable cards
            if card_type == FAVOR and target_id == view.my_id:
                if len(view.my_hand) < 5 or view.has_card_type(DEFUSE):
                    view.say("Nope! My cards are mine.")
                    return PlayCardAction(card=nope_cards[0])
        
        # Nope combos targeting us
        if event_type == EventType.COMBO_PLAYED:
            combo_target = event_data.get("target_player_id")
            if combo_target == view.my_id:
                view.say("Nope! No stealing from me.")
                return PlayCardAction(card=nope_cards[0])
        
        # Conservative: save Nopes for critical moments
        return None
    
    # ========================================================================
    # SPECIAL ACTIONS
    # ========================================================================
    
    def choose_defuse_position(self, view: BotView, draw_pile_size: int) -> int:
        """
        Choose optimal defuse position - strategic placement to target weak players.
        
        Strategy:
        - If we know a weak player (no defuse used, few cards) is coming up, place EK where they'll draw it
        - Otherwise: Not too obvious (not top), not too safe (not bottom)
        - In late game with thin deck, place closer to top to force threat on next player
        """
        if draw_pile_size <= 1:
            return 0
        
        # Find weak targets (no defuse used, few cards)
        weak_targets = [
            pid for pid in view.other_players
            if not self._opponent_defuses_used.get(pid, False)
            and view.other_player_card_counts.get(pid, 0) <= 3
        ]
        
        # If we have weak targets and deck is thin, place EK near top to target them
        if weak_targets and draw_pile_size <= 10:
            # Place in top 30% to force threat on next few players
            max_pos = max(1, int(draw_pile_size * 0.3))
            return max(1, min(max_pos, draw_pile_size - 1))
        
        # Late game with thin deck: place closer to top (not position 0, but close)
        if draw_pile_size <= 15:
            # Place in positions 1-3 (forces threat soon)
            return max(1, min(3, draw_pile_size - 1))
        
        # Mid game: avoid top 2 positions, avoid bottom
        min_pos = max(2, draw_pile_size // 4)
        max_pos = min(draw_pile_size - 2, int(draw_pile_size * 0.75))
        
        if max_pos < min_pos:
            max_pos = min_pos
        
        # Prefer middle-upper range
        position = (min_pos + max_pos) // 2
        return max(min_pos, min(position, max_pos))
    
    def choose_card_to_give(self, view: BotView, requester_id: str) -> Card:
        """
        Choose card to give when hit by Favor.
        
        Strategy: Give least valuable card.
        Priority: Cat cards > expendable actions > last resort
        """
        hand = list(view.my_hand)
        
        # Give cat cards first (least valuable)
        cat_cards = [c for c in hand if "Cat" in c.card_type]
        if cat_cards:
            return cat_cards[0]
        
        # Give expendable action cards (not Defuse/Nope)
        expendable = [
            c for c in hand
            if c.card_type not in (DEFUSE, NOPE, SHUFFLE, STF)
        ]
        if expendable:
            return expendable[0]
        
        # Last resort: give something (must give a card)
        return hand[0]
    
    def on_explode(self, view: BotView) -> None:
        """Last words before exploding."""
        view.say("The odds were not in my favor. GG!")
