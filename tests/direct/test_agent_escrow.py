"""Direct-mode tests for AgentEscrow, run with the current genlayer-test SDK.

    python -m pytest tests/direct -q

The tests use the SDK's own nondeterministic harness: web and LLM calls are
mocked, `resolve` runs the leader, and `direct_vm.run_validator()` replays the
consensus callback the way a validator would, so the evidence each validator
reads can be inspected instead of assumed.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

CONTRACT = "contracts/agent_escrow.py"

DELIVERED_PAGE = "FETCHED-PAGE deluxe-logo-pack: source files, 12 svg logos, license included"
PLACEHOLDER_PAGE = "FETCHED-PAGE under construction, nothing uploaded yet"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def address(raw):
    """Wrap the SDK address bytes the way calldata decoding does on chain."""
    from genlayer import Address

    return Address(raw)


def deploy(direct_deploy):
    return direct_deploy(CONTRACT)


def create(contract, direct_vm, buyer, seller, escrow_id="esc-1", amount=1000, seconds=3600):
    direct_vm.sender = buyer
    direct_vm.value = amount
    deadline = int(time.time()) + seconds
    contract.create_escrow(
        escrow_id,
        address(seller),
        "Logo pack",
        "twelve vector logos, editable files",
        deadline,
        60,
    )
    direct_vm.value = 0
    return escrow_id


def delivered_and_disputed(contract, direct_vm, buyer, seller):
    create(contract, direct_vm, buyer, seller)
    direct_vm.sender = seller
    contract.mark_delivered("esc-1", "https://seller.example/logo-pack", "all twelve files")
    direct_vm.sender = buyer
    contract.raise_dispute("esc-1", "the archive only has three logos")


def mock_page(direct_vm, body):
    direct_vm.mock_web(r"https://seller\.example/logo-pack", {"status": 200, "body": body})


def mock_verdict(direct_vm, evidence_marker, verdict):
    """Answer the judging prompt only when the fetched page carries the marker.

    Two markers means the summary the model sees really does come from the page
    that the caller fetched, not from a page fetched earlier.
    """
    direct_vm.mock_llm(
        rf"(?s).*{evidence_marker}.*",
        json.dumps({"verdict": verdict, "confidence": 90, "reason": f"page said {evidence_marker}"}),
    )


class TestEscrowFlow:
    def test_create_funds_and_cancel_refunds(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        create(contract, direct_vm, direct_alice, direct_bob, amount=1000)

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "funded"
        assert escrow["amount"] == "1000"
        assert contract.stats()["held"] == "1000"

        direct_vm.sender = direct_alice
        contract.cancel("esc-1")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "cancelled"
        assert escrow["amount"] == "1000"
        assert contract.stats()["held"] == "0"

    def test_deliver_then_confirm_releases(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        create(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_bob
        contract.mark_delivered("esc-1", "https://seller.example/logo-pack", "all twelve files")
        assert contract.get_escrow("esc-1")["state"] == "delivered"

        direct_vm.sender = direct_alice
        contract.confirm_delivery("esc-1")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "released"
        assert escrow["verdict"] == "release"
        assert contract.reputation(address(direct_bob))["settled"] == "1"

    def test_only_seller_can_mark_delivered(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        create(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("only the seller"):
            contract.mark_delivered("esc-1", "https://seller.example/logo-pack", "mine now")


class TestReclaim:
    def test_reclaim_after_the_deadline_refunds_the_buyer(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        create(contract, direct_vm, direct_alice, direct_bob, seconds=3600)

        direct_vm.warp(iso(start + 7200))
        direct_vm.sender = direct_alice
        contract.reclaim("esc-1")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "refunded"
        assert escrow["verdict"] == "refund"
        assert "did not deliver before the deadline" in escrow["verdict_reason"]
        assert contract.stats()["held"] == "0"

    def test_reclaim_before_the_deadline_is_rejected(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        create(contract, direct_vm, direct_alice, direct_bob, seconds=3600)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("deadline has not passed yet"):
            contract.reclaim("esc-1")

        assert contract.get_escrow("esc-1")["state"] == "funded"

    def test_only_the_buyer_can_reclaim(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        create(contract, direct_vm, direct_alice, direct_bob, seconds=3600)

        direct_vm.warp(iso(start + 7200))
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("only the buyer can reclaim"):
            contract.reclaim("esc-1")


class TestValidatorEvidence:
    """The delivery page must be read by every validator, not by the leader only."""

    @staticmethod
    def _agree_on_verdicts(direct_vm):
        """Stand in for the EqComparative prompt GenVM asks the validators for."""

        def hook(vm, request):
            template = request.get("ExecPromptTemplate")
            if template is None:
                return None
            leader = json.loads(template["leader_answer"])
            validator = json.loads(template["validator_answer"])
            return {"ok": leader["verdict"] == validator["verdict"]}

        direct_vm._gl_call_hook = hook

    def test_validator_fetches_the_page_inside_the_consensus_callback(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        delivered_and_disputed(contract, direct_vm, direct_alice, direct_bob)

        fetches = []
        page = {"body": PLACEHOLDER_PAGE}

        def serve(request):
            fetches.append(request["url"])
            return {"ok": {"response": {"status": 200, "body": page["body"].encode()}}}

        direct_vm._live_web_handler = serve
        mock_verdict(direct_vm, "under construction", "refund")
        self._agree_on_verdicts(direct_vm)

        # Leader round: one fetch, verdict refund.
        result = contract.resolve("esc-1")
        assert result["verdict"] == "refund"
        assert contract.get_escrow("esc-1")["state"] == "proposed"
        assert len(fetches) == 1

        # The seller fixes the page between the leader round and the validator
        # round. A validator that reused the leader's page would still agree.
        page["body"] = DELIVERED_PAGE
        mock_verdict(direct_vm, "FETCHED-PAGE deluxe-logo-pack", "release")

        agreed = direct_vm.run_validator()

        assert len(fetches) == 2, "the validator did not read the delivery page itself"
        assert fetches[0] == fetches[1] == "https://seller.example/logo-pack"
        assert agreed is False, "validator agreed without reading the changed page"

    def test_validator_agrees_when_the_page_is_unchanged(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        delivered_and_disputed(contract, direct_vm, direct_alice, direct_bob)

        fetches = []

        def serve(request):
            fetches.append(request["url"])
            return {"ok": {"response": {"status": 200, "body": DELIVERED_PAGE.encode()}}}

        direct_vm._live_web_handler = serve
        mock_verdict(direct_vm, "FETCHED-PAGE deluxe-logo-pack", "release")
        self._agree_on_verdicts(direct_vm)

        result = contract.resolve("esc-1")
        assert result["verdict"] == "release"
        assert len(fetches) == 1

        agreed = direct_vm.run_validator()

        assert len(fetches) == 2
        assert agreed is True

    def test_verdict_holds_the_money_until_the_appeal_window_closes(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        start = int(time.time())
        delivered_and_disputed(contract, direct_vm, direct_alice, direct_bob)

        mock_page(direct_vm, DELIVERED_PAGE)
        mock_verdict(direct_vm, "FETCHED-PAGE deluxe-logo-pack", "split")

        contract.resolve("esc-1")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "proposed"
        assert escrow["round"] == "1"
        assert escrow["closed_at"] == "0"
        assert contract.stats()["held"] == "1000", "the verdict paid out before the appeal window"
        assert contract.reputation(address(direct_bob))["settled"] == "0"

        direct_vm.warp(iso(start + 3700))
        contract.finalize("esc-1")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "split"
        assert escrow["closed_at"] != "0"
        assert contract.stats()["held"] == "0"
        assert contract.reputation(address(direct_bob))["settled"] == "1"


class TestAppeal:
    def _proposed(self, direct_vm, direct_deploy, alice, bob, verdict="refund"):
        contract = deploy(direct_deploy)
        delivered_and_disputed(contract, direct_vm, alice, bob)
        mock_page(direct_vm, PLACEHOLDER_PAGE)
        mock_verdict(direct_vm, "under construction", verdict)
        contract.resolve("esc-1")
        return contract

    def test_the_losing_side_can_appeal_once(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._proposed(direct_vm, direct_deploy, direct_alice, direct_bob)
        assert contract.get_escrow("esc-1")["state"] == "proposed"

        direct_vm.sender = direct_bob
        contract.appeal("esc-1", "the page has the files behind one link")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "appealed"
        assert escrow["round"] == "2"
        assert escrow["appeal_by"].lower() == str(address(direct_bob)).lower()
        assert contract.stats()["held"] == "1000"

        with direct_vm.expect_revert("already been appealed"):
            contract.appeal("esc-1", "and another thing")

    def test_an_outsider_cannot_appeal(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._proposed(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = address("0x" + "11" * 20)
        with direct_vm.expect_revert("only the buyer or the seller"):
            contract.appeal("esc-1", "i want a say")

    def test_an_appeal_needs_an_argument(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._proposed(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("say why the verdict is wrong"):
            contract.appeal("esc-1", "")

    def test_the_window_closes(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        delivered_and_disputed(contract, direct_vm, direct_alice, direct_bob)
        mock_page(direct_vm, PLACEHOLDER_PAGE)
        mock_verdict(direct_vm, "under construction", "refund")
        contract.resolve("esc-1")

        direct_vm.warp(iso(start + 3700))
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("appeal window has closed"):
            contract.appeal("esc-1", "too late")

        # Nobody appealed, so the proposal stands and anyone may settle it.
        direct_vm.sender = address("0x" + "22" * 20)
        contract.finalize("esc-1")
        assert contract.get_escrow("esc-1")["state"] == "refunded"

    def test_finalize_waits_for_the_window(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._proposed(direct_vm, direct_deploy, direct_alice, direct_bob)
        with direct_vm.expect_revert("appeal window is still open"):
            contract.finalize("esc-1")

    def test_finalize_is_permissionless_and_llm_free(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        start = int(time.time())
        delivered_and_disputed(contract, direct_vm, direct_alice, direct_bob)
        mock_page(direct_vm, DELIVERED_PAGE)
        mock_verdict(direct_vm, "FETCHED-PAGE deluxe-logo-pack", "release")
        contract.resolve("esc-1")

        direct_vm.warp(iso(start + 3700))
        direct_vm.sender = address("0x" + "22" * 20)
        contract.finalize("esc-1")

        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "released"
        assert contract.stats()["held"] == "0"

    def test_the_second_round_can_overturn_the_first(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = self._proposed(direct_vm, direct_deploy, direct_alice, direct_bob, verdict="refund")

        direct_vm.sender = direct_bob
        contract.appeal("esc-1", "the audit trail is on the page, the buyer read the wrong one")

        # The round-one mocks would match this prompt too, so drop them first.
        direct_vm.clear_mocks()
        mock_page(direct_vm, PLACEHOLDER_PAGE)
        # Round two sees the first verdict in its prompt and lands elsewhere.
        direct_vm.mock_llm(
            r"(?s).*ALREADY RULED.*",
            json.dumps({"verdict": "release", "confidence": 88, "reason": "the page does carry the files"}),
        )
        result = contract.resolve_appeal("esc-1")

        assert result["verdict"] == "release"
        escrow = contract.get_escrow("esc-1")
        assert escrow["state"] == "released"
        assert escrow["round"] == "2"
        assert contract.stats()["held"] == "0"
        assert contract.reputation(address(direct_bob))["settled"] == "1"

    def test_resolve_appeal_needs_an_appeal(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._proposed(direct_vm, direct_deploy, direct_alice, direct_bob)
        with direct_vm.expect_revert("no appeal to rule on"):
            contract.resolve_appeal("esc-1")
