# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from dataclasses import dataclass
from enum import Enum
from genlayer import *


class EscrowState(Enum):
    CREATED = "created"
    FUNDED = "funded"
    DELIVERED = "delivered"
    DISPUTED = "disputed"
    RESOLVED = "resolved"
    REFUNDED = "refunded"


@allow_storage
@dataclass
class Escrow:
    id: str
    buyer: Address
    seller: Address
    amount: u256
    state: str
    description: str
    delivery_url: str
    created_at: str
    resolved_at: str
    resolution: str


class AgentEscrow(gl.Contract):
    """
    Agent-to-agent escrow contract for service payments.
    
    Flow:
    1. Buyer creates escrow with description of service needed
    2. Buyer funds the escrow
    3. Seller delivers service (provides URL as proof)
    4. Buyer confirms delivery OR raises dispute
    5. On dispute, GenLayer consensus verifies delivery evidence
    
    Real use case: AI agents paying other agents for API calls, 
    data processing, compute tasks, content generation.
    """

    escrows: TreeMap[str, Escrow]
    buyerEscrows: TreeMap[Address, DynArray[str]]
    sellerEscrows: TreeMap[Address, DynArray[str]]

    def __init__(self):
        self.escrows = TreeMap()
        self.buyerEscrows = TreeMap()
        self.sellerEscrows = TreeMap()

    @gl.public.write.payable
    def create_escrow(
        self,
        escrow_id: str,
        seller: Address,
        description: str,
    ) -> str:
        """Create a new escrow for agent-to-agent service payment."""
        assert self.escrows.get(escrow_id) is None, "Escrow already exists"
        assert seller != gl.message.sender_address, "Cannot create escrow with yourself"
        assert gl.message.value > 0, "Must send value to fund escrow"

        escrow = Escrow(
            id=escrow_id,
            buyer=gl.message.sender_address,
            seller=seller,
            amount=u256(gl.message.value),
            state=EscrowState.FUNDED.value,
            description=description,
            delivery_url="",
            created_at=str(gl.message.timestamp),
            resolved_at="",
            resolution="",
        )
        self.escrows[escrow_id] = escrow

        if self.buyerEscrows.get(gl.message.sender_address) is None:
            self.buyerEscrows[gl.message.sender_address] = DynArray()
        self.buyerEscrows[gl.message.sender_address].append(escrow_id)

        if self.sellerEscrows.get(seller) is None:
            self.sellerEscrows[seller] = DynArray()
        self.sellerEscrows[seller].append(escrow_id)

        return escrow_id

    @gl.public.write
    def confirm_delivery(self, escrow_id: str) -> None:
        """Buyer confirms service delivery, releasing funds to seller."""
        escrow = self.escrows[escrow_id]
        assert escrow is not None, "Escrow not found"
        assert escrow.buyer == gl.message.sender_address, "Only buyer can confirm"
        assert escrow.state == EscrowState.DELIVERED.value, "Must be delivered state"

        escrow.state = EscrowState.RESOLVED.value
        escrow.resolved_at = str(gl.message.timestamp)
        escrow.resolution = "buyer_confirmed"
        self.escrows[escrow_id] = escrow

        # Transfer funds to seller
        pay(escrow.seller, int(escrow.amount))

    @gl.public.write
    def mark_delivered(self, escrow_id: str, delivery_url: str) -> None:
        """Seller marks service as delivered with proof URL."""
        escrow = self.escrows[escrow_id]
        assert escrow is not None, "Escrow not found"
        assert escrow.seller == gl.message.sender_address, "Only seller can mark delivered"
        assert escrow.state == EscrowState.FUNDED.value, "Must be funded state"
        assert len(delivery_url) > 0, "Delivery URL required"

        escrow.state = EscrowState.DELIVERED.value
        escrow.delivery_url = delivery_url
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def raise_dispute(self, escrow_id: str) -> None:
        """Buyer raises dispute on delivery quality."""
        escrow = self.escrows[escrow_id]
        assert escrow is not None, "Escrow not found"
        assert escrow.buyer == gl.message.sender_address, "Only buyer can dispute"
        assert escrow.state == EscrowState.DELIVERED.value, "Must be delivered state"

        escrow.state = EscrowState.DISPUTED.value
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def resolve_dispute(self, escrow_id: str) -> str:
        """
        Resolve dispute via GenLayer consensus.
        Validators check delivery URL and verify service was provided.
        Uses prompt_comparative for subjective quality assessment.
        """
        escrow = self.escrows[escrow_id]
        assert escrow is not None, "Escrow not found"
        assert escrow.state == EscrowState.DISPUTED.value, "Must be disputed state"

        # Consensus check: verify delivery evidence
        def verify_delivery() -> str:
            delivery_data = gl.nondet.web.render(escrow.delivery_url, mode="text")

            prompt = f"""
Verify if the delivered service matches the requested service.

Requested: {escrow.description}

Delivered content (first 2000 chars):
{delivery_data[:2000]}

Respond in JSON:
{{
    "matches_request": true/false,
    "quality": "poor" | "acceptable" | "good",
    "reason": "brief explanation"
}}
"""
            result = gl.nondet.exec_prompt(prompt, response_format="json")
            return json.dumps(result)

        principle = """
A delivery matches the request if the core service described was provided.
Quality 'acceptable' or 'good' should result in seller payment.
Quality 'poor' with clear mismatch should result in buyer refund.
When in doubt, split 50/50.
"""

        result_str = gl.eq_principle.prompt_comparative(
            verify_delivery, principle=principle
        )
        result = json.loads(result_str)

        if result.get("matches_request") and result.get("quality") in ["acceptable", "good"]:
            escrow.state = EscrowState.RESOLVED.value
            escrow.resolution = "consensus_seller_wins"
            escrow.resolved_at = str(gl.message.timestamp)
            self.escrows[escrow_id] = escrow
            pay(escrow.seller, int(escrow.amount))
            return "seller_wins"
        else:
            escrow.state = EscrowState.REFUNDED.value
            escrow.resolution = "consensus_buyer_refund"
            escrow.resolved_at = str(gl.message.timestamp)
            self.escrows[escrow_id] = escrow
            pay(escrow.buyer, int(escrow.amount))
            return "buyer_refund"

    @gl.public.view
    def get_escrow(self, escrow_id: str) -> dict:
        """View escrow details."""
        escrow = self.escrows[escrow_id]
        assert escrow is not None, "Escrow not found"
        return {
            "id": escrow.id,
            "buyer": str(escrow.buyer),
            "seller": str(escrow.seller),
            "amount": str(escrow.amount),
            "state": escrow.state,
            "description": escrow.description,
            "delivery_url": escrow.delivery_url,
            "created_at": escrow.created_at,
            "resolved_at": escrow.resolved_at,
            "resolution": escrow.resolution,
        }

    @gl.public.view
    def get_buyer_escrows(self, buyer: Address) -> list:
        """Get all escrow IDs for a buyer."""
        escrow_ids = self.buyerEscrows.get(buyer)
        if escrow_ids is None:
            return []
        return list(escrow_ids)

    @gl.public.view
    def get_seller_escrows(self, seller: Address) -> list:
        """Get all escrow IDs for a seller."""
        escrow_ids = self.sellerEscrows.get(seller)
        if escrow_ids is None:
            return []
        return list(escrow_ids)
