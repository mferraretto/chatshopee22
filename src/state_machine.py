from __future__ import annotations

"""Conversation state machine utilities.

This module defines the states used by the bot to track each conversation and
provides helpers to update the state in a controlled manner. The machine is
simple but allows future expansion.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable


class ConversationState(str, Enum):
    """Possible stages of a conversation."""

    PRE_VENDA = "pre_venda"
    POS_VENDA_SEM_PROBLEMA = "pos_venda_sem_problema"
    POS_VENDA_PROBLEMA = "pos_venda_problema"
    PAGAMENTO_CHECKOUT = "pagamento/checkout"
    SILENCIO_DO_CLIENTE = "silencio_do_cliente"
    ENCERRADO = "encerrado"


# Allowed transitions between states. The machine is conservative: if a
# transition is not listed, the current state is preserved.
TRANSITIONS: Dict[ConversationState, Iterable[ConversationState]] = {
    ConversationState.PRE_VENDA: {
        ConversationState.PAGAMENTO_CHECKOUT,
        ConversationState.POS_VENDA_SEM_PROBLEMA,
        ConversationState.POS_VENDA_PROBLEMA,
    },
    ConversationState.POS_VENDA_SEM_PROBLEMA: {
        ConversationState.POS_VENDA_PROBLEMA,
        ConversationState.ENCERRADO,
    },
    ConversationState.POS_VENDA_PROBLEMA: {
        ConversationState.POS_VENDA_SEM_PROBLEMA,
        ConversationState.ENCERRADO,
    },
    ConversationState.PAGAMENTO_CHECKOUT: {
        ConversationState.POS_VENDA_SEM_PROBLEMA,
        ConversationState.SILENCIO_DO_CLIENTE,
    },
    ConversationState.SILENCIO_DO_CLIENTE: {
        ConversationState.POS_VENDA_SEM_PROBLEMA,
        ConversationState.ENCERRADO,
    },
    ConversationState.ENCERRADO: set(),
}


def transition_state(
    current: ConversationState, desired: ConversationState
) -> ConversationState:
    """Return the next state given a desired target.

    If the transition is not explicitly allowed, ``current`` is returned.
    """

    allowed = TRANSITIONS.get(current, set())
    if desired == current or desired in allowed:
        return desired
    return current


@dataclass
class ConversationStateMachine:
    """Small helper to keep track of a conversation state."""

    state: ConversationState = ConversationState.PRE_VENDA

    def update(self, desired: str | ConversationState) -> ConversationState:
        try:
            desired_state = (
                desired
                if isinstance(desired, ConversationState)
                else ConversationState(desired)
            )
        except ValueError:
            # Unknown state → keep current
            return self.state
        self.state = transition_state(self.state, desired_state)
        return self.state
