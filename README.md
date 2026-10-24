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
- Contract (Studio Network): `0x07B89eDD3Ef2d913a31bBe6f2Ad2E7b7ebb4b9f5`
- Explorer: https://explorer-studio.genlayer.com/address/0x07B89eDD3Ef2d913a31bBe6f2Ad2E7b7ebb4b9f5

## Runs on the live contract

| what | transaction |
| --- | --- |
| deploy `0x07B89eDD3Ef2d913a31bBe6f2Ad2E7b7ebb4b9f5` | `0xf4eb2df9b3814c9b5ab69686e91d0375abe2aab5a06c6afd46fcbba34c2699d1` |
| round 1: validators propose `refund`, money stays held | `0xefc7f12d1e7788f86a3405f8794faecd0411b52b83ba790c4e8cc533954b344a` |
| seller appeals the verdict | `0x5b1c0d479f3671c8301b043505b5f85f6d2e67421be858f563a1bae9c45aac73` |
| round 2: validators confirm, escrow settles | `0x1344d18f2b993592eb0f63390a73f60c0f03c7e4d049b35230e9278c8223b54d` |

The run behind the last three rows is `scripts/e2e_studionet.js` on the mismatch scenario: buyer funds
nothing (Studio net is gasless), seller delivers `https://example.com`, buyer disputes, seller answers,
round 1 returns `refund`, the seller appeals, round 2 confirms the verdict and settles. On the previous
contract the same transaction paid the buyer in round 1 and there was nothing left to appeal.

## The flow

1. `create_escrow(id, seller, title, terms, deadline, review_seconds)` — payable, so the buyer can
   fund it. With `value = 0` the escrow holds no money and only settles the dispute, which is how
   the Studio demo runs.
2. `mark_delivered(id, url, note)` — seller only, before the deadline.
3. `confirm_delivery(id)` — buyer accepts, funds go to the seller.
4. `raise_dispute(id, reason)` — buyer rejects the delivery.
5. `respond(id, text)` — the seller gets one answer.
6. `resolve(id)` — validators read the terms, the claim, the answer and the delivered page, then
   return one of `release`, `refund`, `split` with a reason. **The verdict is a proposal**: the money
   stays in the contract and a one hour appeal window opens.
7. `appeal(id, note)` — the side the verdict went against may contest it once, with a reason, while
   the window is open. Nobody else can, and a second appeal is rejected.
8. `resolve_appeal(id)` — a second consensus round. Its prompt carries the first verdict, its reason
   and the appeal, and it may confirm or overturn. `finalize(id)` settles a verdict nobody appealed;
   it is permissionless and needs no model call.

The buyer can `cancel` before delivery, and `reclaim` after the deadline if nothing was delivered.
Both buttons are wired in the app: `Reclaim` shows up for the buyer once the deadline has passed and
`Delivered` disappears for the seller at the same moment, so the console cannot offer a call the
contract would reject. Every settled escrow updates a per-address reputation counter that other
agents can read before they take a job.

## The appeal round

A single consensus round is one reading of one page. The model can misread a gallery link as a
placeholder, and a seller whose delivery was fine loses the money with no recourse. The appeal
round is the fix, and it is deliberately narrow:

- **The verdict does not pay.** `resolve` writes `proposed`, stores the verdict and opens a window;
  `stats().held` keeps counting the money until a later transaction settles it. There is no code
  path where a verdict and a transfer happen in the same call any more.
- **One appeal, two parties.** Only the buyer or the seller, only while the window is open, only
  once, and only with a written reason. An outsider is rejected, and so is an empty note.
- **Round two argues with round one.** The second prompt contains the first verdict, its reason and
  the appeal text, and the rules say overturning requires identifying something the first round got
  wrong about the delivered work, not disagreeing with its wording. A fresh set of validators
  re-reading the page from scratch would have been a coin toss between two equally plausible reads.
- **Silence settles it.** `finalize` pays the stored verdict once the window shuts. It is
  permissionless and makes no model call, so an escrow cannot be held hostage by an absent party and
  a stalled escrow costs nothing to close.

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

Seventeen direct-mode tests run against the current `genlayer-test` SDK and the runner the contract
header pins (`py-genlayer:1jb45aa…`), no network involved:

```bash
python -m pytest tests/direct -q     # 17 passed
```

Web pages and model answers are mocked by the SDK, so the suite covers the whole lifecycle offline:
fund and cancel, deliver and confirm, reclaim before and after the deadline (the clock is warped),
role checks, the appeal round, and the settlement branch.

The appeal cases are the ones that would have passed silently on the old contract:
`test_verdict_holds_the_money_until_the_appeal_window_closes` asserts `stats().held` is still the
full amount after `resolve` and only drops after `finalize`; `test_the_losing_side_can_appeal_once`
covers the single-appeal rule, `test_an_outsider_cannot_appeal` and `test_an_appeal_needs_an_argument`
cover who may appeal and what they must say, `test_the_window_closes` and
`test_finalize_waits_for_the_window` cover the clock, and
`test_the_second_round_can_overturn_the_first` runs round two against the earlier verdict in its
prompt and lands on a different answer.

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
