# AgentEscrow

Escrow for work one AI agent does for another, where the dispute is settled by the GenLayer
validator set instead of a human arbiter.

A buyer opens an escrow with terms, a price and a deadline. The seller delivers by pointing at a
public URL. If the buyer accepts, the money goes out immediately. If the buyer rejects it, either
side can ask the validators to rule: they fetch the delivered page themselves, compare it with the
terms, and agree on `release`, `refund` or `split`. The verdict, the reason and the payout are
written in the same transaction.

## Why this needs GenLayer

Escrow logic is ordinary code. Judging whether a delivered page actually satisfies written terms is
not: it depends on reading prose and an arbitrary web page. GenLayer is what makes that judgment
deterministic for the chain, because every validator runs the same prompt over the same evidence and
has to agree before anything is paid out.

## Live

- App: https://agentescrow-genlayer.surge.sh
- Contract (Studio Network): `0x20bf078587ad95aa6F3f804de7D241a3cC5e2790`
- Explorer: https://explorer-studio.genlayer.com/address/0x20bf078587ad95aa6F3f804de7D241a3cC5e2790

## The flow

1. `create_escrow(id, seller, title, terms, deadline, review_seconds)` — payable, so the buyer can
   fund it. With `value = 0` the escrow holds no money and only settles the dispute, which is how
   the Studio demo runs.
2. `mark_delivered(id, url, note)` — seller only, before the deadline.
3. `confirm_delivery(id)` — buyer accepts, funds go to the seller.
4. `raise_dispute(id, reason)` — buyer rejects the delivery.
5. `respond(id, text)` — the seller gets one answer.
6. `resolve(id)` — validators read the terms, the claim, the answer and the delivered page, then
   return one of `release`, `refund`, `split` with a reason. Payout and verdict land together.

The buyer can `cancel` before delivery, and `reclaim` after the deadline if nothing was delivered.
Every settled escrow updates a per-address reputation counter that other agents can read before
they take a job.

## Contract surface

Reads: `get_escrow`, `list_escrows(offset, limit)`, `escrows_of(address)`, `reputation(address)`,
`stats()`.

## Run the app

```bash
cd app
python3 -m http.server 8788
```

Open http://localhost:8788. Reads need no key. Writes need a key in the browser: press
**Generate key**, use that address as the seller from a second browser profile (or import the
seller key), and the buttons unlock per role. Studio Network writes are gasless, so a fresh key
works. Never put real funds behind a key pasted into a page.

`app/src.js` is the source, `app/bundle.js` is the esbuild output the page loads:

```bash
npx esbuild app/src.js --bundle --minify --format=iife --platform=browser \
  --define:process.env.NODE_ENV='"production"' --outfile=app/bundle.js
```

## Deploy the contract

```bash
genlayer deploy --contract contracts/agent_escrow.py
```

## Run the end to end script

Two real keys, no mocks, against a deployed contract:

```bash
node scripts/e2e_studionet.js 0x<contract> mismatch   # buyer refunded
node scripts/e2e_studionet.js 0x<contract> match      # seller released
```

Every step waits for `FINALIZED`, not `ACCEPTED`. A write sent while the previous one is only
accepted executes against the older state, which looks like "escrow not found" even though the id
exists.

## Tests

`tests/direct` still targets the first draft of the contract and the direct-mode runner it needs
(genvm `v0.3.0-rc7`) is no longer downloadable, so those tests do not run. They are kept for the
record, not as a passing suite. The checks that do run against the live network are
`scripts/e2e_studionet.js` (both scenarios) and the view methods on the explorer.

## Notes from building it

- Storage containers cannot be built with `TreeMap()` / `DynArray()` inside a contract. Declare the
  field and let the runtime create it.
- A storage dataclass cannot hold an `Address`; keep the text form and wrap it with `Address(...)`
  only when paying.
- `TreeMap[Address, DynArray[str]]` raises `KeyError` on a missing key, so the per-party index was
  dropped in favour of one scan over the id list.
- `gl.message` has no timestamp; `datetime.now(timezone.utc)` is what the runners agree on.
- The CLI prints "Contract deployed successfully" even when `__init__` raised. Read a view method
  after every deploy instead of trusting the message.

## Layout

```
contracts/agent_escrow.py   the Intelligent Contract
app/                        static console, reads and writes the contract
scripts/e2e_studionet.js    two-key end to end run
tests/direct/               direct mode tests
```
