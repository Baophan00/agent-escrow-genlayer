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
- Contract (Studio Network): `0x5C4617747e1539b06038BfAc09aCE654D2E9fe9D`
- Explorer: https://explorer-studio.genlayer.com/address/0x5C4617747e1539b06038BfAc09aCE654D2E9fe9D

## Runs on the live contract

| what | transaction |
| --- | --- |
| deploy `0x5C4617747e1539b06038BfAc09aCE654D2E9fe9D` | `0xd9a42c697e6a84c214d0236c880da0534ca4c200fdc2bc2ae743c65705ba6ddf` |
| disputed delivery, validators refund the buyer | `0x33e1185248377d9916f0f4bdeaf54eec6b50109cc6873f9ae1dcc768518237f4` |
| no delivery, buyer reclaims after the deadline | `0x103c2afcff7db7e1c4585c87df0e32282328b6e1732811ca8964d84f114f76ce` |

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
Both buttons are wired in the app: `Reclaim` shows up for the buyer once the deadline has passed and
`Delivered` disappears for the seller at the same moment, so the console cannot offer a call the
contract would reject. Every settled escrow updates a per-address reputation counter that other
agents can read before they take a job.

## Where the evidence is read

`resolve` fetches the delivered page inside the equivalence-principle callback, not before it:

```python
def ask() -> str:                       # runs once for the leader, once per validator
    evidence = read_delivery()          # gl.nondet.web.render(...)
    ...

agreed = gl.eq_principle.prompt_comparative(ask, principle=principle)
```

Fetching outside the callback would let the leader read the page once and hand the validators its
conclusion, so a validator would agree about a page it never opened. Inside the callback every
validator re-reads the URL during the consensus round and a verdict only lands when they converge
on what they each fetched.

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
node scripts/deploy_studionet.js
```

The script deploys with `genlayer-js` (no CLI needed), prints the address, then reads `stats()`
back: a deploy whose `__init__` raised still gets a receipt, so the view is the real proof.
`genlayer deploy --contract contracts/agent_escrow.py` works the same way if the CLI is installed.

## Run the end to end script

Two real keys, no mocks, against a deployed contract:

```bash
node scripts/e2e_studionet.js 0x<contract> mismatch   # buyer refunded
node scripts/e2e_studionet.js 0x<contract> match      # seller released
node scripts/e2e_studionet.js 0x<contract> reclaim    # no delivery, buyer takes the money back
```

`reclaim` creates an escrow with a 90 second deadline, waits it out, and asserts the escrow ends in
`refunded` with the contract holding nothing.

Every step waits for `FINALIZED`, not `ACCEPTED`. A write sent while the previous one is only
accepted executes against the older state, which looks like "escrow not found" even though the id
exists.

## Tests

Nine direct-mode tests run against the current `genlayer-test` SDK and the runner the contract header
pins (`py-genlayer:1jb45aa…`), no network involved:

```bash
python -m pytest tests/direct -q     # 9 passed
```

Web pages and model answers are mocked by the SDK, so the suite covers the whole lifecycle offline:
fund and cancel, deliver and confirm, reclaim before and after the deadline (the clock is warped),
role checks, and the settlement branch that pays out in the same write.

Two of them cover the consensus callback specifically. `resolve` runs the leader, then
`direct_vm.run_validator()` replays the callback the way a validator would. The page served through
`_live_web_handler` is swapped between the two runs:

- `test_validator_fetches_the_page_inside_the_consensus_callback` — leader reads page one and the
  verdict is `refund`; the seller fixes the page; the validator run fetches a second time (the
  handler records two hits) and disagrees, so `run_validator()` returns `False`.
- `test_validator_agrees_when_the_page_is_unchanged` — same page both times, validator fetches
  again and agrees.

Pointed at the pre-fix contract, the first test fails with `fetches == 1` and `agreed == True`: the
validator never opened the page and inherited the leader's verdict. That is the failure mode the two
tests exist to catch.

Against the live network, `scripts/e2e_studionet.js` still runs all three scenarios, and every step
waits for `FINALIZED`.

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
