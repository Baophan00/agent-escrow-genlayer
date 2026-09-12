# Agent Escrow — GenLayer Intelligent Contract

Agent-to-agent escrow contract for secure service payments. Built for the GenLayer ecosystem.

## Overview

This contract enables AI agents to transact with each other securely using an escrow mechanism:

1. **Buyer** creates and funds an escrow describing the service needed
2. **Seller** delivers the service and marks it as delivered with proof URL
3. **Buyer** confirms delivery → funds released to seller
4. **On dispute** → GenLayer consensus verifies delivery evidence via LLM

## Key Features

- **State machine**: FUNDED → DELIVERED → RESOLVED or DISPUTED → RESOLVED/REFUNDED
- **Dispute resolution**: Uses `gl.eq_principle.prompt_comparative` for consensus
- **Storage**: TreeMap + DynArray for efficient state management
- **Direct mode tests**: Full flow coverage

## Contract Structure

```
contracts/agent_escrow.py       — Main contract
tests/direct/test_agent_escrow.py — Direct mode tests
```

## Real Use Cases

- AI agents paying for API calls (data, compute, inference)
- Content generation services
- Data processing and analysis
- Task execution between autonomous agents

## Dispute Resolution

When a buyer disputes delivery:

1. Contract fetches delivery URL content via `gl.nondet.web.render`
2. LLM evaluates if delivery matches the original request
3. `gl.eq_principle.prompt_comparative` runs consensus across validators
4. Result: seller payment or buyer refund based on consensus

## Build & Test

```bash
# Lint
genvm-lint check contracts/agent_escrow.py

# Direct tests
pytest tests/direct/test_agent_escrow.py -v

# Deploy (requires GenLayer Studio)
genlayer deploy
```

## License

MIT
