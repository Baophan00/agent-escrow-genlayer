"""Direct mode tests for AgentEscrow contract."""

import pytest
from genlayer import *


class TestAgentEscrow:
    """Test agent escrow contract functionality."""

    def test_create_escrow(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Test creating an escrow."""
        contract = direct_deploy("contracts/agent_escrow.py")
        direct_vm.sender = direct_alice

        # Create escrow with 1000 wei
        escrow_id = contract.create_escrow(
            escrow_id="test-1",
            seller=direct_bob,
            description="Generate 10 images of cats",
            value=1000,
        )

        assert escrow_id == "test-1"

        # Check escrow details
        details = contract.get_escrow("test-1")
        assert details["state"] == "funded"
        assert details["amount"] == "1000"

    def test_mark_delivered_and_confirm(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Test full flow: fund -> deliver -> confirm."""
        contract = direct_deploy("contracts/agent_escrow.py")
        
        # Buyer creates escrow
        direct_vm.sender = direct_alice
        contract.create_escrow(
            escrow_id="test-2",
            seller=direct_bob,
            description="Analyze sentiment of 100 tweets",
            value=2000,
        )

        # Seller marks delivered
        direct_vm.sender = direct_bob
        contract.mark_delivered(
            escrow_id="test-2",
            delivery_url="https://example.com/delivery",
        )

        details = contract.get_escrow("test-2")
        assert details["state"] == "delivered"

        # Buyer confirms
        direct_vm.sender = direct_alice
        contract.confirm_delivery("test-2")

        details = contract.get_escrow("test-2")
        assert details["state"] == "resolved"

    def test_dispute_flow(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Test dispute resolution flow."""
        contract = direct_deploy("contracts/agent_escrow.py")
        
        # Setup escrow
        direct_vm.sender = direct_alice
        contract.create_escrow(
            escrow_id="test-3",
            seller=direct_bob,
            description="Write blog post about AI",
            value=3000,
        )

        # Seller delivers
        direct_vm.sender = direct_bob
        contract.mark_delivered(
            escrow_id="test-3",
            delivery_url="https://example.com/blog",
        )

        # Buyer disputes
        direct_vm.sender = direct_alice
        contract.raise_dispute("test-3")

        details = contract.get_escrow("test-3")
        assert details["state"] == "disputed"

        # Mock web for resolution
        direct_vm.mock_web(
            r".*example\.com.*",
            {
                "status": 200,
                "body": "This is a comprehensive blog post about artificial intelligence...",
            },
        )
        direct_vm.mock_llm(
            r".*Verify if the delivered.*",
            '{"matches_request": true, "quality": "good", "reason": "Content matches requested topic"}',
        )

        # Resolve dispute
        result = contract.resolve_dispute("test-3")
        assert result == "seller_wins"

    def test_unauthorized_access(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Test that only authorized users can perform actions."""
        contract = direct_deploy("contracts/agent_escrow.py")
        
        direct_vm.sender = direct_alice
        contract.create_escrow(
            escrow_id="test-4",
            seller=direct_bob,
            description="Test service",
            value=1000,
        )

        # Bob cannot confirm his own delivery (only buyer can)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("Only buyer can confirm"):
            contract.confirm_delivery("test-4")

    def test_get_buyer_escrows(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Test listing buyer's escrows."""
        contract = direct_deploy("contracts/agent_escrow.py")
        direct_vm.sender = direct_alice

        contract.create_escrow("e1", direct_bob, "Service 1", value=100)
        contract.create_escrow("e2", direct_bob, "Service 2", value=200)

        escrows = contract.get_buyer_escrows(direct_alice)
        assert "e1" in escrows
        assert "e2" in escrows

    def test_duplicate_escrow_id(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Test that duplicate escrow IDs are rejected."""
        contract = direct_deploy("contracts/agent_escrow.py")
        direct_vm.sender = direct_alice

        contract.create_escrow("dup", direct_bob, "Service", value=100)

        with direct_vm.expect_revert("Escrow already exists"):
            contract.create_escrow("dup", direct_bob, "Another", value=200)
