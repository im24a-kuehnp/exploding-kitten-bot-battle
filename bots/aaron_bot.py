from __future__ import annotations
 
import random
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
 
# Card type aliases (engine does not implement Imploding/DFB, so we adapt)
STF = "SeeTheFutureCard"
SKIP = "SkipCard"
ATTACK = "AttackCard"  # Use to push turns away (maps to Slap/Reverse intent)
SHUFFLE = "ShuffleCard"
FAVOR = "FavorCard"
NOPE = "NopeCard"
DEFUSE = "DefuseCard"
EXPLODING = "ExplodingKittenCard"
 
# Bot IDs are the bot names (with _2, _3 suffixes if duplicated)
STRATEGIC_BOT_PREFIX = "chatgpt"
 
 
class MyBot(Bot):
    """
    High-strength Exploding Kittens bot with probability-driven decisions,
    conservative defuse management, and lightweight opponent modeling.
    """
 
    def __init__(self) -> None:
        self._turn_count: int = 0
        self._initial_player_count: int | None = None
        self._first_ek_seen_from_other: bool = False
        self._kittens_removed: int = 0
        self._known_top_cards: tuple[str, ...] | None = None
        self._opponent_card_history: dict[str, list[str]] = {}
        self._opponent_card_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self._defuses_seen: int = 0
 
    @property
    def name(self) -> str:
        return "aaron"
 
    # ------------------------------------------------------------------ #
    # Main turn logic
    # ------------------------------------------------------------------ #
    def take_turn(self, view: BotView) -> Action:
        self._initialize_game_baseline(view)
        self._turn_count += 1
 
        # Compute current EK risk to steer decisions for this turn.
        ek_risk: float = self.calculate_ek_risk(view)
        if self._is_first_turn():
            # Early-game rule: draw only; do not burn tempo cards when risk is low.
            return self._draw_first_turn()
 
        # If someone else already surfaced an EK, bias toward caution.
        post_first = self._play_after_first_ek(view)
        if post_first is not None:
            return post_first
 
        return self.decide_action(view, ek_risk)
 
    # ------------------------------------------------------------------ #
    # Probability + opponent modeling
    # ------------------------------------------------------------------ #
    def calculate_ek_risk(self, view: BotView) -> float:
        """
        Estimate probability of drawing an EK next draw.
        Incorporates peek knowledge and defuse cushioning.
        """
        players: int = self._initial_player_count or len(view.turn_order)
        baseline_kittens: int = max(1, players - 1 - self._kittens_removed)
        deck_size: int = max(1, view.draw_pile_count)
 
        # Peek-aware adjustment
        if self._known_top_cards:
            visible = self._known_top_cards
            if visible and visible[0] == EXPLODING:
                return 1.0
            if EXPLODING in visible:
                # Uniform within the peek slice
                return 1.0 / float(len(visible))
            unseen = max(1, deck_size - len(visible))
            return min(1.0, baseline_kittens / float(unseen))
 
        # Coarse baseline
        risk: float = min(1.0, baseline_kittens / float(deck_size))
 
        # Holding defuse lowers elimination risk; be slightly braver
        defuses: int = view.count_cards_of_type(DEFUSE)
        if defuses > 0:
            risk *= 0.6
        return risk
 
    # Backwards-compatible alias (used only internally)
    def calculate_ek_probability(self, view: BotView) -> float:
        return self.calculate_ek_risk(view)
 
    def simulate_opponent(self, player_id: str) -> dict[str, float]:
        """
        Rough opponent model: probability they hold defense (Skip/Attack/Shuffle/Defuse/Nope).
        """
        history: Counter[str] = self._opponent_card_counts.get(player_id, Counter())
        total_seen: int = sum(history.values())
        if total_seen == 0:
            return {"defensive": 0.3, "nope": 0.2}
        defensive_cards = history[SKIP] + history[ATTACK] + history[SHUFFLE]
        nope_cards = history[NOPE]
        defensive_prob = min(0.8, defensive_cards / max(1, total_seen))
        nope_prob = min(0.7, nope_cards / max(1, total_seen))
        return {"defensive": defensive_prob, "nope": nope_prob}
 
    # ------------------------------------------------------------------ #
    # Decision tree
    # ------------------------------------------------------------------ #
    def decide_action(self, view: BotView, ek_probability: float) -> Action:
        # Against StrategicBot specifically, combos are extremely strong because
        # that bot only "Nope"s CARD_PLAYED events (Attack/Favor/Shuffle), and
        # combos produce COMBO_PLAYED events instead. So we bias heavily toward
        # playing combos to strip their hand.
        combo_action = self._try_play_best_combo(view)
        if combo_action is not None:
            return combo_action
 
        # Moderate/high risk and no knowledge: peek first to inform play.
        stf_cards: tuple[Card, ...] = view.get_cards_of_type(STF)
        if (
            stf_cards
            and self._known_top_cards is None
            and ek_probability >= 0.33
            and view.draw_pile_count > 0
        ):
            view.say("Peeking to anchor probabilities.")
            return PlayCardAction(card=stf_cards[0])
 
        if ek_probability < 0.33:
            return self._early_low_risk(view)
        if ek_probability < 0.5:
            return self._midgame_control(view)
        return self._late_high_risk(view, ek_probability)
 
    def _early_low_risk(self, view: BotView) -> Action:
        """Early game: draw and build hand; conserve tempo cards."""
        if view.my_turns_remaining > 1:
            attack_cards = view.get_cards_of_type(ATTACK)
            if attack_cards and view.other_players:
                target = self._choose_attack_target(view)
                if target:
                    view.say("Low risk: offloading turns to grow hand.")
                    return PlayCardAction(card=attack_cards[0], target_player_id=target)
        return DrawCardAction()
 
    def _midgame_control(self, view: BotView) -> Action:
        """Midgame: manage risk with info and soft avoidance."""
        shuffle_cards = view.get_cards_of_type(SHUFFLE)
        if shuffle_cards and self._known_top_cards and EXPLODING in self._known_top_cards:
            # StrategicBot tends to Nope Shuffle; prefer combo play (handled earlier).
            if not self._has_strategic_bot(view):
                view.say("Shuffling away a known threat.")
                return PlayCardAction(card=shuffle_cards[0])
 
        # Skip/Attack if we know the top is dangerous
        if self._known_top_cards and self._known_top_cards[0] == EXPLODING:
            defensive = self.play_defensive(view)
            if defensive:
                return defensive
 
        # Favor/steal to improve hand quality
        steal = self._steal_for_value(view, bias_for_stf=True)
        if steal is not None:
            return steal
 
        return DrawCardAction()
 
    def _late_high_risk(self, view: BotView, ek_probability: float) -> Action:
        """Late/high risk: prioritize survival, then push risk to opponents."""
        shuffle_cards = view.get_cards_of_type(SHUFFLE)
        if shuffle_cards:
            # StrategicBot tends to Nope Shuffle; prefer combos and skips first.
            if not self._has_strategic_bot(view):
                view.say("High risk: resetting deck.")
                return PlayCardAction(card=shuffle_cards[0])
 
        defensive = self.play_defensive(view)
        if defensive:
            return defensive
 
        aggressive = self.play_aggressive(view, ek_probability)
        if aggressive:
            return aggressive
 
        # If we know top is safe and have multiple turns, chain skip after draw
        if (
            self._known_top_cards
            and self._known_top_cards[0] != EXPLODING
            and view.my_turns_remaining > 1
        ):
            skip_cards = view.get_cards_of_type(SKIP)
            if skip_cards:
                return PlayCardAction(card=skip_cards[0])
 
        return DrawCardAction()
 
    # ------------------------------------------------------------------ #
    # Defensive / Aggressive modes
    # ------------------------------------------------------------------ #
    def play_defensive(self, view: BotView) -> Action | None:
        """
        Use skip/attack to avoid drawing when risk is high or top is dangerous.
        """
        skip_cards = view.get_cards_of_type(SKIP)
        if skip_cards:
            view.say("Defensive skip to avoid danger.")
            return PlayCardAction(card=skip_cards[0])
 
        attack_cards = view.get_cards_of_type(ATTACK)
        if attack_cards and view.other_players:
            # StrategicBot tends to Nope Attack; use only if it's not present.
            if self._has_strategic_bot(view):
                return None
            target = self._choose_attack_target(view)
            if target:
                view.say("Defensive attack: pass risk onward.")
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        return None
 
    def play_aggressive(self, view: BotView, ek_probability: float) -> Action | None:
        """
        Aggression: steal or push turns when opponents likely lack defense/defuse.
        """
        steal = self._steal_for_value(view, bias_for_stf=False)
        if steal is not None:
            return steal
 
        attack_cards = view.get_cards_of_type(ATTACK)
        if attack_cards and view.other_players:
            # StrategicBot tends to Nope Attack; prefer combos (handled earlier).
            if self._has_strategic_bot(view):
                return None
            target = self._choose_attack_target(view, prefer_weak_defense=True)
            if target:
                view.say("Aggressive attack to force risky draws.")
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        return None
 
    # ------------------------------------------------------------------ #
    # Event tracking / opponent model
    # ------------------------------------------------------------------ #
    def on_event(self, event: GameEvent, view: BotView) -> None:
        if event.event_type == EventType.BOT_CHAT:
            return
 
        if event.event_type == EventType.CARDS_PEEKED and event.player_id == view.my_id:
            card_types: Sequence[str] = tuple(event.data.get("card_types", ()))
            self._known_top_cards = tuple(card_types)
 
        if event.event_type in (EventType.DECK_SHUFFLED, EventType.EXPLODING_KITTEN_INSERTED):
            self._known_top_cards = None
 
        if event.event_type == EventType.CARD_DRAWN and self._known_top_cards:
            # Drop the known top card when any draw occurs.
            self._known_top_cards = self._known_top_cards[1:]
 
        if event.event_type == EventType.EXPLODING_KITTEN_DRAWN:
            if event.player_id != view.my_id:
                self._first_ek_seen_from_other = True
 
        if event.event_type == EventType.PLAYER_ELIMINATED:
            # Assume EK was consumed when a player dies without defuse.
            self._kittens_removed = max(0, self._kittens_removed + 1)
 
        if event.event_type == EventType.CARD_PLAYED:
            card_type = event.data.get("card_type", "")
            player_id = event.player_id
            if player_id and player_id != view.my_id and player_id in view.other_players:
                self._opponent_card_history.setdefault(player_id, []).append(card_type)
                self._opponent_card_counts[player_id][card_type] += 1
            if card_type == DEFUSE:
                self._defuses_seen += 1
 
    # ------------------------------------------------------------------ #
    # Reactions
    # ------------------------------------------------------------------ #
    def react(self, view: BotView, triggering_event: GameEvent) -> Action | None:
        nope_cards = view.get_cards_of_type(NOPE)
        if not nope_cards:
            return None
 
        event_type: EventType = triggering_event.event_type
        data: dict[str, object] = triggering_event.data
 
        if event_type == EventType.CARD_PLAYED:
            card_type = str(data.get("card_type", ""))
            target_id: str | None = (
                str(data.get("target_player_id")) if data.get("target_player_id") is not None else None
            )
 
            if card_type == ATTACK and target_id == view.my_id:
                view.say("Nope! Not taking extra turns.")
                return PlayCardAction(card=nope_cards[0])
 
            if card_type == FAVOR and target_id == view.my_id:
                view.say("Nope! Keep your hands off my cards.")
                return PlayCardAction(card=nope_cards[0])
 
        if event_type == EventType.FAVOR_REQUESTED:
            target_id = triggering_event.data.get("target")
            if target_id == view.my_id:
                return PlayCardAction(card=nope_cards[0])
 
        # Be stingier with random nopes; 10% when we have spares.
        if len(nope_cards) > 1 and random.random() < 0.1:
            return PlayCardAction(card=nope_cards[0])
 
        return None
 
    # ------------------------------------------------------------------ #
    # Special cases
    # ------------------------------------------------------------------ #
    def choose_defuse_position(self, view: BotView, draw_pile_size: int) -> int:
        """
        Put kitten back away from bottom to keep opponents exposed.
        """
        if draw_pile_size <= 1:
            return 0
        safe_top = 1
        safe_bottom = max(1, draw_pile_size - 1)
        return random.randint(safe_top, safe_bottom)
 
    def choose_card_to_give(self, view: BotView, requester_id: str) -> Card:
        """
        Favor response: give lowest-value card; keep defuse/nope.
        """
        hand: list[Card] = list(view.my_hand)
        cat_cards = [c for c in hand if "Cat" in c.card_type]
        if cat_cards:
            return cat_cards[0]
        expendable = [c for c in hand if c.card_type not in (DEFUSE, NOPE)]
        if expendable:
            return expendable[0]
        return hand[0]
 
    def on_explode(self, view: BotView) -> None:
        view.say("So long, cruel kitten universe!")
 
    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _initialize_game_baseline(self, view: BotView) -> None:
        if self._initial_player_count is None:
            self._initial_player_count = len(view.turn_order)
 
    def _is_first_turn(self) -> bool:
        return self._turn_count == 1
 
    def _choose_attack_target(self, view: BotView, prefer_weak_defense: bool = False) -> str | None:
        if not view.other_players:
            return None
        strategic = self._find_strategic_bot_id(view)
        if strategic is not None:
            # When StrategicBot is in the game, focus pressure on it.
            return strategic
        if prefer_weak_defense:
            # Pick opponent with fewest cards (likely weaker defenses)
            return min(
                view.other_players,
                key=lambda pid: view.other_player_card_counts.get(pid, 0),
            )
        return max(
            view.other_players,
            key=lambda pid: view.other_player_card_counts.get(pid, 0),
        )
 
    def _play_after_first_ek(self, view: BotView) -> Action | None:
        if not self._first_ek_seen_from_other:
            return None
        shuffle_cards = view.get_cards_of_type(SHUFFLE)
        if shuffle_cards:
            view.say("Resetting uncertainty after first EK appeared.")
            return PlayCardAction(card=shuffle_cards[0])
        stf_cards = view.get_cards_of_type(STF)
        if stf_cards and view.draw_pile_count > 0:
            return PlayCardAction(card=stf_cards[0])
        steal = self._steal_for_value(view, bias_for_stf=True)
        if steal:
            return steal
        defensive = self.play_defensive(view)
        if defensive:
            return defensive
        return DrawCardAction()
 
    def _draw_first_turn(self) -> Action:
        return DrawCardAction()
 
    def _steal_for_value(self, view: BotView, bias_for_stf: bool) -> Action | None:
        if not view.other_players:
            return None
        target = self._choose_attack_target(view, prefer_weak_defense=bias_for_stf)
        if target is None:
            return None
 
        # Prefer combos over Favor, especially vs StrategicBot (which nopes Favor).
        combos = self._find_possible_combos(view.my_hand)
        for combo_type, combo_cards in combos:
            if combo_type in ("three_of_a_kind", "two_of_a_kind", "five_different"):
                view.say("Combo value play.")
                return PlayComboAction(cards=combo_cards, target_player_id=target)
 
        favor_cards = view.get_cards_of_type(FAVOR)
        if favor_cards and not self._has_strategic_bot(view):
            view.say(f"Requesting a card from {target}.")
            return PlayCardAction(card=favor_cards[0], target_player_id=target)
        return None
 
    def _find_possible_combos(
        self, hand: tuple[Card, ...]
    ) -> tuple[tuple[str, tuple[Card, ...]], ...]:
        combo_candidates: tuple[Card, ...] = tuple(c for c in hand if c.can_combo())
        if not combo_candidates:
            return ()
        by_type: Counter[str] = Counter(c.card_type for c in combo_candidates)
        combos: list[tuple[str, tuple[Card, ...]]] = []
        for card_type, count in by_type.items():
            cards_of_type: list[Card] = [c for c in combo_candidates if c.card_type == card_type]
            if count >= 3:
                combos.append(("three_of_a_kind", tuple(cards_of_type[:3])))
            elif count >= 2:
                combos.append(("two_of_a_kind", tuple(cards_of_type[:2])))
        if len(by_type) >= 5:
            unique_cards: list[Card] = []
            for card_type in by_type.keys():
                if len(unique_cards) >= 5:
                    break
                for card in combo_candidates:
                    if card.card_type == card_type:
                        unique_cards.append(card)
                        break
            if len(unique_cards) >= 5:
                combos.append(("five_different", tuple(unique_cards[:5])))
        return tuple(combos)
 
    def _has_strategic_bot(self, view: BotView) -> bool:
        return self._find_strategic_bot_id(view) is not None
 
    def _find_strategic_bot_id(self, view: BotView) -> str | None:
        for pid in view.other_players:
            if pid.startswith(STRATEGIC_BOT_PREFIX):
                return pid
        return None
 
    def _try_play_best_combo(self, view: BotView) -> Action | None:
        """
        If a strong combo is available, play it now.
 
        Heuristic:
        - 5-different: only if top discard looks high-impact
        - 3-of-a-kind: great (steals random in this engine, but still strong)
        - 2-of-a-kind: steal random, very strong vs StrategicBot
        """
        if not view.other_players:
            return None
 
        # Must have a target with cards for steals to matter.
        target = self._choose_attack_target(view, prefer_weak_defense=False)
        if target is None:
            return None
        if view.other_player_card_counts.get(target, 0) <= 0:
            # Fallback to any player with cards
            with_cards = [pid for pid in view.other_players if view.other_player_card_counts.get(pid, 0) > 0]
            if not with_cards:
                return None
            target = with_cards[0]
 
        combos = self._find_possible_combos(view.my_hand)
        if not combos:
            return None
 
        # Five-different is only good if the discard top is a premium card.
        top_discard_type: str | None = view.discard_pile[-1].card_type if view.discard_pile else None
        premium = {DEFUSE, NOPE, SKIP, ATTACK, SHUFFLE, STF, FAVOR}
 
        best: tuple[str, tuple[Card, ...]] | None = None
        for combo_type, cards in combos:
            if combo_type == "five_different":
                if top_discard_type is not None and top_discard_type in premium:
                    best = (combo_type, cards)
                    break
                continue
            if combo_type == "three_of_a_kind":
                best = (combo_type, cards)
                break
            if combo_type == "two_of_a_kind" and best is None:
                best = (combo_type, cards)
 
        if best is None:
            return None
 
        combo_type, cards = best
        if combo_type == "five_different":
            view.say("Five different: taking top discard.")
            return PlayComboAction(cards=cards)
        view.say("Combo steal.")
        return PlayComboAction(cards=cards, target_player_id=target)
 
 