"""
==============================================================================
MY BOT - A Strategic Bot Implementation
==============================================================================

This is your bot! Customize the strategy methods below to make it smarter.
"""

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


class MyBot(Bot):
    """
    Your custom bot implementation.
    
    TODO: Add your strategy here!
    """
    
    def __init__(self) -> None:
        """Initialize your bot with any state tracking you need."""
        # Example: Track which cards opponents have played
        self._opponent_card_history: dict[str, list[str]] = {}
        
        # Example: Track how many defuses we've seen
        self._defuses_played = 0
    
    @property
    def name(self) -> str:
        """Return your bot's display name."""
        return "MyBot"
    
    def take_turn(self, view: BotView) -> Action:
        """
        Decide what to do on your turn.
        
        This is where your main strategy lives!
        
        Strategy ideas:
        - Play Attack cards when you have multiple turns
        - Use See the Future before drawing if you have it
        - Save Nope cards for important actions
        - Play combos to steal valuable cards
        """
        
        # =====================================================================
        # EXAMPLE STRATEGY: Play See the Future if we have it
        # =====================================================================
        see_future_cards = view.get_cards_of_type("SeeTheFutureCard")
        if see_future_cards and view.draw_pile_count > 0:
            view.say("Let me check what's coming...")
            return PlayCardAction(card=see_future_cards[0])
        
        # =====================================================================
        # EXAMPLE STRATEGY: Play Attack if we have multiple turns
        # =====================================================================
        if view.my_turns_remaining > 1:
            attack_cards = view.get_cards_of_type("AttackCard")
            if attack_cards and view.other_players:
                # Attack the player with the most cards (biggest threat)
                target = max(
                    view.other_players,
                    key=lambda pid: view.other_player_card_counts.get(pid, 0)
                )
                view.say(f"Attacking {target}!")
                return PlayCardAction(card=attack_cards[0], target_player_id=target)
        
        # =====================================================================
        # EXAMPLE STRATEGY: Play Skip if we're under attack
        # =====================================================================
        if view.my_turns_remaining > 1:
            skip_cards = view.get_cards_of_type("SkipCard")
            if skip_cards:
                view.say("Skipping this turn!")
                return PlayCardAction(card=skip_cards[0])
        
        # =====================================================================
        # EXAMPLE STRATEGY: Try to play a combo if possible
        # =====================================================================
        combos = self._find_possible_combos(view.my_hand)
        if combos and view.other_players:
            # Prefer three-of-a-kind (stronger effect)
            for combo_type, combo_cards in combos:
                if combo_type == "three_of_a_kind":
                    target = max(
                        view.other_players,
                        key=lambda pid: view.other_player_card_counts.get(pid, 0)
                    )
                    view.say("Combo time!")
                    return PlayComboAction(cards=combo_cards, target_player_id=target)
            
            # Fall back to two-of-a-kind
            combo_type, combo_cards = combos[0]
            if combo_type in ("two_of_a_kind", "three_of_a_kind"):
                target = max(
                    view.other_players,
                    key=lambda pid: view.other_player_card_counts.get(pid, 0)
                )
                return PlayComboAction(cards=combo_cards, target_player_id=target)
        
        # =====================================================================
        # EXAMPLE STRATEGY: Play Favor if we're low on cards
        # =====================================================================
        if len(view.my_hand) < 3 and view.other_players:
            favor_cards = view.get_cards_of_type("FavorCard")
            if favor_cards:
                # Target player with most cards (more likely to have good cards)
                target = max(
                    view.other_players,
                    key=lambda pid: view.other_player_card_counts.get(pid, 0)
                )
                view.say(f"Favor from {target}!")
                return PlayCardAction(card=favor_cards[0], target_player_id=target)
        
        # =====================================================================
        # DEFAULT: Draw a card to end the turn
        # =====================================================================
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
        React to events that happen in the game.
        
        Use this to track what opponents are doing!
        """
        # Skip chat events to avoid infinite loops
        if event.event_type == EventType.BOT_CHAT:
            return
        
        # Track cards played by opponents
        if event.event_type == EventType.CARD_PLAYED:
            player_id = event.player_id
            if player_id != view.my_id and player_id in view.other_players:
                card_type = event.data.get("card_type", "")
                if player_id not in self._opponent_card_history:
                    self._opponent_card_history[player_id] = []
                self._opponent_card_history[player_id].append(card_type)
        
        # Track defuses played
        if event.event_type == EventType.CARD_PLAYED:
            card_type = event.data.get("card_type", "")
            if card_type == "DefuseCard":
                self._defuses_played += 1
    
    def react(self, view: BotView, triggering_event: GameEvent) -> Action | None:
        """
        Decide whether to play a Nope card.
        
        Strategy: Save Nope for important actions like:
        - Attacks targeting us
        - Favors targeting us
        - Combos that would steal from us
        """
        nope_cards = view.get_cards_of_type("NopeCard")
        
        if not nope_cards:
            return None
        
        # Get the event type and data
        event_type = triggering_event.event_type
        event_data = triggering_event.data
        
        # Nope attacks that target us
        if event_type == EventType.CARD_PLAYED:
            card_type = event_data.get("card_type", "")
            target_id = event_data.get("target_player_id")
            
            if card_type == "AttackCard" and target_id == view.my_id:
                view.say("Nope! Not attacking me!")
                return PlayCardAction(card=nope_cards[0])
            
            # Nope favors targeting us
            if card_type == "FavorCard" and target_id == view.my_id:
                view.say("Nope! Keep your hands off my cards!")
                return PlayCardAction(card=nope_cards[0])
        
        # Be conservative - save Nopes for when we really need them
        # Only use if we have multiple Nopes
        if len(nope_cards) > 1:
            # 30% chance to nope other actions
            import random
            if random.random() < 0.3:
                view.say("Nope!")
                return PlayCardAction(card=nope_cards[0])
        
        return None
    
    def choose_defuse_position(self, view: BotView, draw_pile_size: int) -> int:
        """
        Choose where to put the Exploding Kitten back.
        
        Strategy: Put it near the top so opponents draw it, but not at position 0
        (which would be too obvious).
        """
        view.say("Phew! That was close!")
        
        # Put it in the top 25% of the deck (but not at the very top)
        if draw_pile_size > 4:
            position = max(1, draw_pile_size // 4)
        else:
            position = max(1, draw_pile_size - 1)
        
        return position
    
    def choose_card_to_give(self, view: BotView, requester_id: str) -> Card:
        """
        Choose which card to give when hit by Favor.
        
        Strategy: Give away the least valuable cards first.
        Priority: Cat cards > Action cards > Defuse/Nope (last resort)
        """
        hand = list(view.my_hand)
        
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
        """Say your last words before exploding."""
        view.say("NOOOOO! I was so close to victory!")
