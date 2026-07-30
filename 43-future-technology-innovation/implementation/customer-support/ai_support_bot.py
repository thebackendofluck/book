#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AI-Powered Customer Support Chatbot for iGaming Platforms
==========================================================

Handles common player inquiries including FAQ, account issues, payment
status, bonus terms, and responsible gambling referrals. Designed for
first-contact resolution with seamless human escalation.

Covers:
- Intent classification using keyword + pattern matching
- FAQ knowledge base with gambling-specific content
- Account status lookup (simulated API integration)
- Responsible gambling detection and immediate escalation
- Conversation state machine with context retention
- Human agent handoff with full conversation transcript

Feasibility Assessment:
- Rule-based intent matching handles 70-80% of gambling support queries
- Pattern matching for RG signals is critical for player safety
- Production: replace intent matcher with fine-tuned BERT or GPT endpoint
- Conversation state is session-scoped (Redis in production)
- No external dependencies for core

Dependencies: None for core
"""

import re
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class Intent(Enum):
    GREETING = "greeting"
    FAQ_GENERAL = "faq_general"
    ACCOUNT_STATUS = "account_status"
    ACCOUNT_VERIFICATION = "account_verification"
    DEPOSIT_ISSUE = "deposit_issue"
    WITHDRAWAL_STATUS = "withdrawal_status"
    BONUS_INQUIRY = "bonus_inquiry"
    GAME_ISSUE = "game_issue"
    RESPONSIBLE_GAMBLING = "responsible_gambling"
    SELF_EXCLUSION = "self_exclusion"
    COMPLAINT = "complaint"
    HUMAN_AGENT = "human_agent"
    GOODBYE = "goodbye"
    UNKNOWN = "unknown"


class ConversationState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    AWAITING_INFO = "awaiting_info"
    ESCALATED = "escalated"
    CLOSED = "closed"


class EscalationPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"  # responsible gambling concerns


@dataclass
class Message:
    role: str  # "player" or "bot" or "system"
    content: str
    timestamp: str = ""
    intent: Optional[Intent] = None
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Conversation:
    conversation_id: str
    player_id: str
    state: ConversationState = ConversationState.IDLE
    messages: list[Message] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    escalation_priority: Optional[EscalationPriority] = None
    assigned_agent: Optional[str] = None
    created_at: str = ""
    resolved: bool = False
    resolution_category: str = ""


# ---------------------------------------------------------------------------
# Intent classifier
# ---------------------------------------------------------------------------

class IntentClassifier:
    """
    Rule-based intent classifier using keyword patterns.

    Production upgrade path:
    1. Start with these rules (day 1, no training data needed)
    2. Log all classified intents + player messages
    3. After 10K conversations, fine-tune a BERT classifier
    4. A/B test rule-based vs ML classifier
    5. Graduate to ML when it exceeds 90% accuracy
    """

    PATTERNS: dict[Intent, list[str]] = {
        Intent.GREETING: [
            r"\b(hi|hello|hey|good\s*(morning|afternoon|evening))\b",
            r"^(hi|hello|hey)\s*$",
        ],
        Intent.ACCOUNT_STATUS: [
            r"\b(account|profile)\s*(status|info|details|locked|blocked|suspended)\b",
            r"\b(my account|login|can'?t log\s*in|sign\s*in)\b",
            r"\b(locked out|access denied|frozen)\b",
        ],
        Intent.ACCOUNT_VERIFICATION: [
            r"\b(verif|kyc|identity|document|passport|id\s*check)\b",
            r"\b(upload|submit)\s*(document|id|proof)\b",
            r"\b(pending\s*verification|verify\s*my)\b",
        ],
        Intent.DEPOSIT_ISSUE: [
            r"\b(deposit|payment)\s*(fail|issue|problem|stuck|pending|declined)\b",
            r"\b(can'?t\s*deposit|money\s*not\s*showing|card\s*declined)\b",
            r"\b(deposit\s*limit|minimum\s*deposit|payment\s*method)\b",
        ],
        Intent.WITHDRAWAL_STATUS: [
            r"\b(withdraw|withdrawal|cash\s*out|payout)\b",
            r"\b(where\s*is\s*my\s*money|when\s*will\s*i\s*get)\b",
            r"\b(pending\s*withdrawal|withdrawal\s*time)\b",
        ],
        Intent.BONUS_INQUIRY: [
            r"\b(bonus|free\s*spin|promo|promotion|offer|wagering)\b",
            r"\b(welcome\s*bonus|deposit\s*bonus|no\s*deposit)\b",
            r"\b(rollover|playthrough|bonus\s*term)\b",
        ],
        Intent.GAME_ISSUE: [
            r"\b(game)\s*(crash|freeze|error|bug|issue|problem|not\s*loading)\b",
            r"\b(slot|roulette|blackjack)\s*(stuck|frozen|error)\b",
            r"\b(round\s*interrupted|disconnected\s*during)\b",
            r"\b(rng|fairness|rigged)\b",
        ],
        Intent.RESPONSIBLE_GAMBLING: [
            r"\b(gambling\s*problem|addict|can'?t\s*stop|help\s*me\s*stop)\b",
            r"\b(spend\s*too\s*much|lost\s*everything|out\s*of\s*control)\b",
            r"\b(gamcare|gamstop|gambling\s*help|helpline)\b",
            r"\b(cooling\s*off|take\s*a\s*break|reality\s*check)\b",
            r"\b(limit\s*my|set\s*limit|deposit\s*limit|loss\s*limit)\b",
        ],
        Intent.SELF_EXCLUSION: [
            r"\b(self[- ]?exclud|exclude\s*my|close\s*my\s*account)\b",
            r"\b(permanently\s*close|delete\s*account|ban\s*me)\b",
            r"\b(gamstop|self[- ]?exclu)\b",
        ],
        Intent.COMPLAINT: [
            r"\b(complain|complaint|unhappy|dissatisfied|terrible|worst)\b",
            r"\b(report|escalat|supervisor|manager)\b",
            r"\b(not\s*fair|scam|fraud|cheating)\b",
        ],
        Intent.HUMAN_AGENT: [
            r"\b(human|real\s*person|agent|operator|speak\s*to\s*someone)\b",
            r"\b(transfer\s*me|live\s*chat|talk\s*to)\b",
        ],
        Intent.GOODBYE: [
            r"\b(bye|goodbye|thank|thanks|cheers|that'?s\s*all)\b",
        ],
    }

    def classify(self, text: str) -> tuple[Intent, float]:
        """Classify user message into an intent with confidence score."""
        text_lower = text.lower().strip()

        # Priority: responsible gambling and self-exclusion always checked first
        for priority_intent in [Intent.SELF_EXCLUSION, Intent.RESPONSIBLE_GAMBLING]:
            patterns = self.PATTERNS[priority_intent]
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return priority_intent, 0.95

        # Check all other intents
        matches: list[tuple[Intent, int]] = []
        for intent, patterns in self.PATTERNS.items():
            if intent in (Intent.SELF_EXCLUSION, Intent.RESPONSIBLE_GAMBLING):
                continue
            match_count = 0
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    match_count += 1
            if match_count > 0:
                matches.append((intent, match_count))

        if not matches:
            return Intent.UNKNOWN, 0.3

        # Return intent with most pattern matches
        matches.sort(key=lambda x: x[1], reverse=True)
        best_intent, best_count = matches[0]
        confidence = min(0.90, 0.50 + best_count * 0.15)
        return best_intent, confidence


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

FAQ_KNOWLEDGE_BASE = {
    "account_verification": {
        "question": "How do I verify my account?",
        "answer": (
            "To verify your account, please upload the following documents in "
            "your Account Settings > Verification section:\n\n"
            "1. Government-issued photo ID (passport or driving licence)\n"
            "2. Proof of address (utility bill or bank statement, less than 3 months old)\n"
            "3. Proof of payment method (photo of card used for deposits, first 6 and last 4 digits visible)\n\n"
            "Verification typically takes 24-48 hours. You can continue playing while "
            "verification is pending, but withdrawals require a verified account."
        ),
    },
    "deposit_methods": {
        "question": "What deposit methods do you accept?",
        "answer": (
            "We accept the following deposit methods:\n\n"
            "- Visa / Mastercard (instant)\n"
            "- Bank Transfer (1-3 business days)\n"
            "- PayPal (instant, UK/EU only)\n"
            "- Skrill / Neteller (instant)\n"
            "- Paysafecard (instant)\n"
            "- Apple Pay / Google Pay (instant, mobile only)\n\n"
            "Minimum deposit is 10 EUR/GBP. All transactions are secured with "
            "256-bit SSL encryption."
        ),
    },
    "withdrawal_times": {
        "question": "How long do withdrawals take?",
        "answer": (
            "Withdrawal processing times vary by method:\n\n"
            "- E-wallets (PayPal, Skrill, Neteller): 0-24 hours\n"
            "- Visa / Mastercard: 1-3 business days\n"
            "- Bank Transfer: 3-5 business days\n\n"
            "All withdrawals go through a security review. First-time withdrawals "
            "require account verification. There is a 24-hour pending period during "
            "which you can cancel the withdrawal."
        ),
    },
    "bonus_terms": {
        "question": "How do bonus wagering requirements work?",
        "answer": (
            "Wagering requirements specify how many times you must wager the bonus "
            "amount before you can withdraw winnings. For example:\n\n"
            "- 100 EUR bonus with 35x wagering = 3,500 EUR total wagers needed\n"
            "- Maximum bet while wagering: 5 EUR per spin/hand\n"
            "- Game contributions: Slots 100%, Table Games 10%, Live Casino 10%\n"
            "- Bonus expires 30 days after activation\n\n"
            "Check the specific terms of each bonus in your Bonus section."
        ),
    },
    "game_fairness": {
        "question": "Are your games fair?",
        "answer": (
            "Yes. All our games use certified Random Number Generators (RNG) "
            "that are independently tested and audited by:\n\n"
            "- eCOGRA (independent testing laboratory)\n"
            "- iTech Labs (RNG certification)\n"
            "- GLI (Gaming Laboratories International)\n\n"
            "Our platform is licensed and regulated by the UK Gambling Commission "
            "and/or Malta Gaming Authority. RTP (Return to Player) percentages "
            "are published for every game. You can view audit certificates "
            "in our Fairness section."
        ),
    },
    "responsible_gambling_tools": {
        "question": "What responsible gambling tools are available?",
        "answer": (
            "We provide the following responsible gambling tools:\n\n"
            "- Deposit Limits: Set daily, weekly, or monthly deposit limits\n"
            "- Loss Limits: Cap your losses over a period\n"
            "- Session Time Limits: Get reminders after set play time\n"
            "- Reality Checks: Pop-up notifications showing session duration and spend\n"
            "- Cooling Off: Take a break for 24 hours, 7 days, or 30 days\n"
            "- Self-Exclusion: Exclude yourself for 6 months, 1 year, or 5 years\n"
            "- GAMSTOP: Register at www.gamstop.co.uk for UK-wide self-exclusion\n\n"
            "If you need support, contact GamCare at 0808 8020 133 (free, 24/7)."
        ),
    },
}


# ---------------------------------------------------------------------------
# Response generator
# ---------------------------------------------------------------------------

class ResponseGenerator:
    """Generates contextual responses based on classified intent."""

    def __init__(self):
        self.knowledge_base = FAQ_KNOWLEDGE_BASE

    def generate(
        self,
        intent: Intent,
        confidence: float,
        context: dict,
        player_message: str,
    ) -> tuple[str, dict]:
        """
        Generate response text and any action metadata.
        Returns (response_text, action_metadata).
        """
        handlers = {
            Intent.GREETING: self._handle_greeting,
            Intent.ACCOUNT_STATUS: self._handle_account_status,
            Intent.ACCOUNT_VERIFICATION: self._handle_verification,
            Intent.DEPOSIT_ISSUE: self._handle_deposit_issue,
            Intent.WITHDRAWAL_STATUS: self._handle_withdrawal,
            Intent.BONUS_INQUIRY: self._handle_bonus,
            Intent.GAME_ISSUE: self._handle_game_issue,
            Intent.RESPONSIBLE_GAMBLING: self._handle_responsible_gambling,
            Intent.SELF_EXCLUSION: self._handle_self_exclusion,
            Intent.COMPLAINT: self._handle_complaint,
            Intent.HUMAN_AGENT: self._handle_human_request,
            Intent.GOODBYE: self._handle_goodbye,
            Intent.UNKNOWN: self._handle_unknown,
        }

        handler = handlers.get(intent, self._handle_unknown)
        return handler(context, player_message)

    def _handle_greeting(self, ctx: dict, msg: str) -> tuple[str, dict]:
        player_name = ctx.get("player_name", "there")
        return (
            f"Hello {player_name}! Welcome to our support chat. "
            f"How can I help you today? I can assist with:\n\n"
            f"- Account & verification questions\n"
            f"- Deposits & withdrawals\n"
            f"- Bonus terms & offers\n"
            f"- Game issues\n"
            f"- Responsible gambling tools\n\n"
            f"Just describe your issue and I'll do my best to help!",
            {"action": "none"},
        )

    def _handle_account_status(self, ctx: dict, msg: str) -> tuple[str, dict]:
        # In production, query account service API
        account_status = ctx.get("account_status", "active")
        if "locked" in msg.lower() or "blocked" in msg.lower():
            return (
                "I understand you're having trouble accessing your account. "
                "This can happen for several reasons:\n\n"
                "1. Failed login attempts (auto-lock after 5 attempts)\n"
                "2. Pending identity verification\n"
                "3. Security review triggered by unusual activity\n\n"
                "To unlock your account, please try resetting your password via "
                "the 'Forgot Password' link. If the issue persists, I'll connect "
                "you with our security team. Would you like me to do that?",
                {"action": "check_account_lock", "needs_followup": True},
            )
        return (
            f"Your account status is: {account_status}. "
            "Is there a specific issue you're experiencing with your account?",
            {"action": "none"},
        )

    def _handle_verification(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            self.knowledge_base["account_verification"]["answer"],
            {"action": "none"},
        )

    def _handle_deposit_issue(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "I'm sorry to hear you're having trouble with a deposit. "
            "Let me help troubleshoot:\n\n"
            "1. Ensure your payment method is enabled for online gambling transactions "
            "(some banks block gambling payments by default)\n"
            "2. Check that you haven't exceeded your deposit limit\n"
            "3. Verify the card hasn't expired\n"
            "4. Try a different payment method\n\n"
            + self.knowledge_base["deposit_methods"]["answer"]
            + "\n\nIf the issue persists, I can escalate to our payments team. "
            "Would you like me to do that?",
            {"action": "check_deposit", "needs_followup": True},
        )

    def _handle_withdrawal(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            self.knowledge_base["withdrawal_times"]["answer"]
            + "\n\nWould you like me to check the status of a specific withdrawal? "
            "If so, please provide your withdrawal reference number.",
            {"action": "check_withdrawal", "needs_followup": True},
        )

    def _handle_bonus(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            self.knowledge_base["bonus_terms"]["answer"]
            + "\n\nWould you like to know about your current active bonuses "
            "or available promotions?",
            {"action": "none", "needs_followup": True},
        )

    def _handle_game_issue(self, ctx: dict, msg: str) -> tuple[str, dict]:
        if re.search(r"\b(rigged|cheat|scam|unfair)\b", msg.lower()):
            return (
                "I understand your frustration. I want to assure you that all our games "
                "use independently certified Random Number Generators.\n\n"
                + self.knowledge_base["game_fairness"]["answer"]
                + "\n\nIf you experienced a specific technical issue during gameplay "
                "(disconnection, crash, or error), please provide the game name and "
                "approximate time, and I'll investigate the game round.",
                {"action": "investigate_game_round", "needs_followup": True},
            )
        return (
            "I'm sorry you experienced a game issue. To help investigate:\n\n"
            "1. Which game were you playing?\n"
            "2. What happened? (crash, freeze, error message)\n"
            "3. Approximately when did this occur?\n\n"
            "If you were disconnected mid-round, the game result is determined "
            "by the RNG at the time of your bet. Any winnings will be credited "
            "to your account automatically. Check your transaction history to confirm.",
            {"action": "investigate_game_round", "needs_followup": True},
        )

    def _handle_responsible_gambling(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "Thank you for reaching out. Your wellbeing is our top priority.\n\n"
            + self.knowledge_base["responsible_gambling_tools"]["answer"]
            + "\n\nI'm connecting you with a trained responsible gambling advisor "
            "who can provide confidential support. Please hold for a moment.",
            {
                "action": "escalate",
                "priority": "urgent",
                "team": "responsible_gambling",
                "reason": "Player expressed gambling concern",
            },
        )

    def _handle_self_exclusion(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "I understand you'd like to self-exclude. This is an important decision "
            "and I'm here to help.\n\n"
            "Self-exclusion options:\n"
            "- 6 months\n"
            "- 1 year\n"
            "- 5 years (permanent on this platform)\n\n"
            "For UK-wide self-exclusion across all licensed operators, visit "
            "www.gamstop.co.uk\n\n"
            "I'm connecting you with a specialist who can process your request "
            "and provide additional support resources. Please hold.",
            {
                "action": "escalate",
                "priority": "urgent",
                "team": "responsible_gambling",
                "reason": "Player requesting self-exclusion",
            },
        )

    def _handle_complaint(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "I'm sorry to hear about your experience. We take all complaints "
            "seriously.\n\n"
            "I'm going to connect you with a senior support agent who can "
            "properly log and investigate your complaint. You also have the right "
            "to escalate unresolved complaints to our Alternative Dispute Resolution "
            "(ADR) provider - details are available at the bottom of our website.\n\n"
            "Please hold while I transfer you.",
            {
                "action": "escalate",
                "priority": "high",
                "team": "complaints",
                "reason": "Player complaint",
            },
        )

    def _handle_human_request(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "Of course! I'll connect you with a live support agent right away. "
            "Our average wait time is under 2 minutes. Please hold.",
            {
                "action": "escalate",
                "priority": "medium",
                "team": "general_support",
                "reason": "Player requested human agent",
            },
        )

    def _handle_goodbye(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "Thank you for chatting with us! If you need help in the future, "
            "we're available 24/7. Have a great day and play responsibly!",
            {"action": "close_conversation"},
        )

    def _handle_unknown(self, ctx: dict, msg: str) -> tuple[str, dict]:
        return (
            "I'm not sure I understood that. Could you rephrase your question? "
            "I can help with:\n\n"
            "- Account issues (login, verification)\n"
            "- Payments (deposits, withdrawals)\n"
            "- Bonuses and promotions\n"
            "- Game technical issues\n"
            "- Responsible gambling tools\n\n"
            "Or type 'agent' to speak with a human support agent.",
            {"action": "none"},
        )


# ---------------------------------------------------------------------------
# Chatbot engine
# ---------------------------------------------------------------------------

class AISupportBot:
    """
    Main chatbot engine managing conversations, routing, and escalations.

    Production architecture:
        WebSocket connection -> this bot -> intent classifier -> response generator
        Escalation -> Zendesk/Intercom ticket with full transcript
        Metrics -> Datadog (resolution rate, avg handle time, CSAT)
    """

    def __init__(self):
        self.classifier = IntentClassifier()
        self.responder = ResponseGenerator()
        self.conversations: dict[str, Conversation] = {}
        self._conv_counter = 0

    def start_conversation(self, player_id: str, player_context: Optional[dict] = None) -> Conversation:
        """Start a new support conversation."""
        self._conv_counter += 1
        conv_id = f"CONV-{self._conv_counter:06d}"

        conversation = Conversation(
            conversation_id=conv_id,
            player_id=player_id,
            state=ConversationState.ACTIVE,
            context=player_context or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Add system greeting
        greeting = Message(
            role="system",
            content="Conversation started",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        conversation.messages.append(greeting)

        self.conversations[conv_id] = conversation
        logger.info(f"Started conversation {conv_id} for player {player_id}")
        return conversation

    def handle_message(self, conversation_id: str, player_message: str) -> Message:
        """Process a player message and return bot response."""
        conversation = self.conversations.get(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        if conversation.state == ConversationState.ESCALATED:
            return Message(
                role="bot",
                content="You're currently connected with a live agent. "
                       "They will respond shortly.",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        if conversation.state == ConversationState.CLOSED:
            return Message(
                role="bot",
                content="This conversation has been closed. "
                       "Please start a new chat if you need further help.",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Record player message
        player_msg = Message(
            role="player",
            content=player_message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        conversation.messages.append(player_msg)

        # Classify intent
        intent, confidence = self.classifier.classify(player_message)
        player_msg.intent = intent
        player_msg.confidence = confidence

        # Generate response
        response_text, action_metadata = self.responder.generate(
            intent, confidence, conversation.context, player_message
        )

        # Handle actions
        if action_metadata.get("action") == "escalate":
            conversation.state = ConversationState.ESCALATED
            priority_str = action_metadata.get("priority", "medium")
            conversation.escalation_priority = EscalationPriority(priority_str)
            logger.info(
                f"Escalating {conversation.conversation_id} to "
                f"{action_metadata.get('team', 'general')} "
                f"(priority: {priority_str})"
            )

        if action_metadata.get("action") == "close_conversation":
            conversation.state = ConversationState.CLOSED
            conversation.resolved = True

        # Record bot response
        bot_msg = Message(
            role="bot",
            content=response_text,
            timestamp=datetime.now(timezone.utc).isoformat(),
            intent=intent,
            confidence=confidence,
            metadata=action_metadata,
        )
        conversation.messages.append(bot_msg)

        return bot_msg

    def get_transcript(self, conversation_id: str) -> list[dict]:
        """Get full conversation transcript for handoff or audit."""
        conversation = self.conversations.get(conversation_id)
        if not conversation:
            return []

        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp,
                "intent": msg.intent.value if msg.intent else None,
                "confidence": msg.confidence,
            }
            for msg in conversation.messages
        ]

    def get_metrics(self) -> dict:
        """Get chatbot performance metrics."""
        total = len(self.conversations)
        if total == 0:
            return {"total_conversations": 0}

        resolved = sum(1 for c in self.conversations.values() if c.resolved)
        escalated = sum(
            1 for c in self.conversations.values()
            if c.state == ConversationState.ESCALATED
        )
        urgent = sum(
            1 for c in self.conversations.values()
            if c.escalation_priority == EscalationPriority.URGENT
        )

        avg_messages = sum(
            len(c.messages) for c in self.conversations.values()
        ) / total

        return {
            "total_conversations": total,
            "resolved_by_bot": resolved,
            "escalated_to_human": escalated,
            "urgent_escalations": urgent,
            "bot_resolution_rate": round(resolved / total, 3) if total else 0,
            "avg_messages_per_conversation": round(avg_messages, 1),
        }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate customer support chatbot interactions."""

    bot = AISupportBot()

    print("\n" + "=" * 70)
    print("  AI Customer Support Chatbot - Simulation")
    print("=" * 70)

    # Scenario 1: Bonus inquiry (resolved by bot)
    print("\n  --- Scenario 1: Bonus Inquiry ---")
    conv1 = bot.start_conversation("PLR-10001", {"player_name": "Alex"})
    messages_1 = [
        "Hi there!",
        "I want to know about the wagering requirements on my bonus",
        "Thanks, that's helpful. Goodbye!",
    ]
    for msg in messages_1:
        print(f"\n  Player: {msg}")
        response = bot.handle_message(conv1.conversation_id, msg)
        # Truncate for display
        display = response.content[:200] + "..." if len(response.content) > 200 else response.content
        print(f"  Bot [{response.intent.value if response.intent else 'N/A'}]: {display}")

    # Scenario 2: Withdrawal issue (needs follow-up)
    print(f"\n  --- Scenario 2: Withdrawal Status ---")
    conv2 = bot.start_conversation("PLR-10002", {"player_name": "Sarah"})
    messages_2 = [
        "Where is my withdrawal? I requested it 5 days ago",
    ]
    for msg in messages_2:
        print(f"\n  Player: {msg}")
        response = bot.handle_message(conv2.conversation_id, msg)
        display = response.content[:200] + "..." if len(response.content) > 200 else response.content
        print(f"  Bot [{response.intent.value if response.intent else 'N/A'}]: {display}")

    # Scenario 3: Responsible gambling (urgent escalation)
    print(f"\n  --- Scenario 3: Responsible Gambling (URGENT) ---")
    conv3 = bot.start_conversation("PLR-10003", {"player_name": "James"})
    messages_3 = [
        "I think I have a gambling problem, I can't stop playing",
    ]
    for msg in messages_3:
        print(f"\n  Player: {msg}")
        response = bot.handle_message(conv3.conversation_id, msg)
        display = response.content[:200] + "..." if len(response.content) > 200 else response.content
        print(f"  Bot [{response.intent.value if response.intent else 'N/A'}]: {display}")
    print(f"  Status: {conv3.state.value} | Priority: {conv3.escalation_priority.value if conv3.escalation_priority else 'N/A'}")

    # Scenario 4: Self-exclusion request
    print(f"\n  --- Scenario 4: Self-Exclusion Request ---")
    conv4 = bot.start_conversation("PLR-10004", {"player_name": "Emma"})
    messages_4 = [
        "I want to self-exclude from the platform",
    ]
    for msg in messages_4:
        print(f"\n  Player: {msg}")
        response = bot.handle_message(conv4.conversation_id, msg)
        display = response.content[:200] + "..." if len(response.content) > 200 else response.content
        print(f"  Bot [{response.intent.value if response.intent else 'N/A'}]: {display}")

    # Scenario 5: Game fairness complaint
    print(f"\n  --- Scenario 5: Game Issue / Fairness Concern ---")
    conv5 = bot.start_conversation("PLR-10005", {"player_name": "Mike"})
    messages_5 = [
        "Your slots are rigged, I lost 20 times in a row",
        "I want to speak to a real person",
    ]
    for msg in messages_5:
        print(f"\n  Player: {msg}")
        response = bot.handle_message(conv5.conversation_id, msg)
        display = response.content[:200] + "..." if len(response.content) > 200 else response.content
        print(f"  Bot [{response.intent.value if response.intent else 'N/A'}]: {display}")

    # Metrics
    print(f"\n  {'=' * 60}")
    print("  Chatbot Performance Metrics:")
    metrics = bot.get_metrics()
    for key, value in metrics.items():
        print(f"    {key.replace('_', ' ').title()}: {value}")

    # Transcript example
    print(f"\n  --- Conversation Transcript (Scenario 1) ---")
    transcript = bot.get_transcript(conv1.conversation_id)
    for entry in transcript:
        if entry["role"] == "system":
            continue
        indent = "    Player" if entry["role"] == "player" else "    Bot"
        text = entry["content"][:100] + "..." if len(entry["content"]) > 100 else entry["content"]
        print(f"  {indent}: {text}")

    print(f"\n  Production integration:")
    print("    WebSocket -> this bot -> Zendesk/Intercom for escalation")
    print("    Intent logs -> BigQuery -> fine-tune BERT classifier quarterly")
    print("    Metrics -> Datadog dashboard (resolution rate, CSAT, handle time)\n")


if __name__ == "__main__":
    demo()
