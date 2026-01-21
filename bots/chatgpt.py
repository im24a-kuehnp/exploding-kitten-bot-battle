from game.bots.base import (
    Bot,
    Action,
    DrawCardAction,
    PlayCardAction,
    PlayComboAction,
)
from game.bots.view import BotView
from game.cards.base import Card
from game.history import GameEvent, EventType


class StrategicBot(Bot):
    @property
    def name(self) -> str:
        return "StrategicBot"

    # ----------------------------
    # TURN LOGIC
    # ----------------------------
    def take_turn(self, view: BotView) -> Action:
        hand = list(view.my_hand)

        # 1. If deck is getting dangerous, try to avoid drawing
        if view.draw_pile_count <= 5:
            action = self._play_skip_or_attack(view, hand)
            if action:
                return action

        # 2. Use Favor if opponents have more cards than us
        favor = self._find_card(hand, "FavorCard")
        if favor:
            # Check if Favor can actually be played (targets must have cards)
            if favor.can_play(view, is_own_turn=True):
                target = self._best_favor_target(view)
                if target and view.other_player_card_counts.get(target, 0) > 0:
                    return PlayCardAction(card=favor, target_player_id=target)

        # 3. Play two-of-a-kind combo if available
        combo = self._two_of_a_kind_combo(hand, view)
        if combo:
            return combo

        # 4. Shuffle only if deck is small and we cannot skip
        shuffle = self._find_card(hand, "ShuffleCard")
        if shuffle and view.draw_pile_count <= 6:
            return PlayCardAction(card=shuffle)

        # 5. Otherwise, draw
        return DrawCardAction()

    # ----------------------------
    # REACTIONS
    # ----------------------------
    def react(self, view: BotView, triggering_event: GameEvent) -> Action | None:
        nope = self._find_card(list(view.my_hand), "NopeCard")
        if not nope:
            return None

        # Only nope serious threats
        if triggering_event.event_type == EventType.CARD_PLAYED:
            card_type = triggering_event.data.get("card_type", "")
            if card_type in {
                "AttackCard",
                "FavorCard",
                "ShuffleCard",
            }:
                return PlayCardAction(card=nope)

        return None

    # ----------------------------
    # DEFUSE STRATEGY
    # ----------------------------
    def choose_defuse_position(self, view: BotView, draw_pile_size: int) -> int:
        # Safest possible position
        return draw_pile_size

    # ----------------------------
    # FAVOR RESPONSE
    # ----------------------------
    def choose_card_to_give(self, view: BotView, requester_id: str) -> Card:
        # Give away weakest card (prefer cats)
        cats = [c for c in view.my_hand if "Cat" in c.card_type]
        return cats[0] if cats else view.my_hand[0]

    # ----------------------------
    # EVENT HANDLING
    # ----------------------------
    def on_event(self, event: GameEvent, view: BotView) -> None:
        # Intentionally minimal: avoids overfitting and infinite chat loops
        pass

    def on_explode(self, view: BotView) -> None:
        view.say("I regret nothing.")

    # ----------------------------
    # HELPER METHODS
    # ----------------------------
    def _find_card(self, hand: list[Card], card_type: str) -> Card | None:
        for c in hand:
            if c.card_type == card_type:
                return c
        return None

    def _play_skip_or_attack(self, view: BotView, hand: list[Card]) -> Action | None:
        skip = self._find_card(hand, "SkipCard")
        if skip:
            return PlayCardAction(card=skip)

        attack = self._find_card(hand, "AttackCard")
        if attack and view.other_players:
            target = self._most_cards_player(view)
            return PlayCardAction(card=attack, target_player_id=target)

        return None

    def _most_cards_player(self, view: BotView) -> str | None:
        if not view.other_player_card_counts:
            return None
        return max(
            view.other_player_card_counts.items(),
            key=lambda x: x[1],
        )[0]

    def _best_favor_target(self, view: BotView) -> str | None:
        return self._most_cards_player(view)

    def _two_of_a_kind_combo(self, hand: list[Card], view: BotView) -> Action | None:
        if not view.other_players:
            return None

        by_type: dict[str, list[Card]] = {}
        for c in hand:
            # Only include cards that can combo
            if c.can_combo():
                by_type.setdefault(c.card_type, []).append(c)

        for cards in by_type.values():
            if len(cards) >= 2:
                target = self._most_cards_player(view)
                if target:
                    return PlayComboAction(
                        cards=tuple(cards[:2]),
                        target_player_id=target,
                    )
        return None
