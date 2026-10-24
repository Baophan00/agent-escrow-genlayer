#!/usr/bin/env node
/**
 * End to end run of AgentEscrow on GenLayer Studio Network.
 *
 * Two agents with their own keys: a buyer who funds and complains, a seller
 * who delivers. Nothing is mocked, every step is a real transaction against a
 * deployed contract, and the last one asks the validator set for a verdict.
 *
 *   node scripts/e2e_studionet.js <contractAddress> [scenario]
 *
 * scenario: "mismatch" (default), "match", or "reclaim" (seller never
 * delivers, the buyer takes the money back once the deadline passes)
 */
const fs = require("fs");
const path = require("path");
const { createClient, createAccount, generatePrivateKey } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");

const ADDRESS = process.argv[2];
const SCENARIO = process.argv[3] || "mismatch";
if (!ADDRESS) {
  console.error("usage: node scripts/e2e_studionet.js <contractAddress> [mismatch|match]");
  process.exit(1);
}

const KEYFILE = path.join(__dirname, ".e2e-keys.json");
function keys() {
  if (fs.existsSync(KEYFILE)) return JSON.parse(fs.readFileSync(KEYFILE, "utf8"));
  const k = { buyer: generatePrivateKey(), seller: generatePrivateKey() };
  fs.writeFileSync(KEYFILE, JSON.stringify(k, null, 2), { mode: 0o600 });
  return k;
}
const k = keys();
const buyerAccount = createAccount(k.buyer);
const sellerAccount = createAccount(k.seller);
const buyer = createClient({ chain: studionet, account: buyerAccount });
const seller = createClient({ chain: studionet, account: sellerAccount });
const reader = createClient({ chain: studionet });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withRetry(fn, label) {
  let last;
  for (let i = 0; i < 8; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      const msg = String(e && e.message);
      // The public Studio RPC occasionally answers with an HTML error page.
      if (!/DOCTYPE|fetch failed|ECONNRESET|timeout|temporarily unavailable|not available/i.test(msg)) throw e;
      console.log(`  ${label}: rpc hiccup, retry ${i + 1}`);
      await sleep(4000);
    }
  }
  throw last;
}

async function wait(client, hash) {
  return withRetry(
    () =>
      client.waitForTransactionReceipt({
        hash,
        // FINALIZED, not ACCEPTED: a write executed while the previous one is
        // still accepted-but-not-final sees the old state, so the next call
        // reports "escrow not found" even though the id exists.
        status: "FINALIZED",
        retries: 160,
        interval: 3000,
      }),
    "receipt"
  );
}

async function write(client, functionName, args, who) {
  console.log(`\n[${who}] ${functionName}(${args.map((a) => JSON.stringify(a)).join(", ")})`);
  const hash = await client.writeContract({ address: ADDRESS, functionName, args, value: 0n });
  console.log(`  tx ${hash}`);
  const receipt = await wait(client, hash);
  const status = receipt.status_name || receipt.status;
  console.log(`  ${status}  (execution ${receipt.result_name || receipt.result || "?"})`);
  // The CLI and the SDK both report the consensus result here, which can be
  // ACCEPTED while the contract call itself errored. The genvm trace is the
  // only honest signal, so print it when something looks wrong.
  const trace = JSON.stringify(receipt.genvm_result || receipt.last_round || {});
  if (trace.includes('"ERROR"') || /Traceback|Error:/.test(trace)) {
    console.log("  contract execution FAILED:", trace.slice(0, 700));
    throw new Error(`${functionName} failed inside the contract`);
  }
  return receipt;
}

async function read(functionName, args) {
  return withRetry(
    () => reader.readContract({ address: ADDRESS, functionName, args }),
    "read"
  );
}

(async () => {
  const buyerAddr = buyerAccount.address;
  const sellerAddr = sellerAccount.address;
  const id = `e2e-${Date.now()}`;
  const now = Math.floor(Date.now() / 1000);
  // The reclaim scenario needs a deadline that actually passes during the run.
  const deadline = now + (SCENARIO === "reclaim" ? 90 : 3600);

  console.log("contract", ADDRESS);
  console.log("buyer   ", buyerAddr);
  console.log("seller  ", sellerAddr);
  console.log("scenario", SCENARIO);

  if (SCENARIO === "reclaim") {
    await write(
      buyer,
      "create_escrow",
      [id, sellerAddr, "Ten product photos for the launch page",
       "Ten original photos of the product, 2000px wide, delivered as a public gallery.",
       deadline, 60],
      "buyer"
    );
    console.log(`\nno delivery this time; waiting for the deadline at ${new Date(deadline * 1000).toISOString()}`);
    for (;;) {
      const left = deadline + 8 - Math.floor(Date.now() / 1000);
      if (left <= 0) break;
      console.log(`  ${left}s left`);
      await sleep(Math.min(left, 30) * 1000);
    }
    await write(buyer, "reclaim", [id], "buyer");
    const escrow = await read("get_escrow", [id]);
    console.log("\nreclaimed escrow");
    console.log(JSON.stringify(escrow, null, 2));
    console.log("\nstats");
    console.log(JSON.stringify(await read("stats", []), null, 2));
    if (escrow.state !== "refunded") throw new Error(`reclaim left the escrow in state ${escrow.state}`);
    console.log("\nVERDICT: buyer took the money back after the deadline");
    return;
  }

  if (SCENARIO === "match") {
    await write(
      buyer,
      "create_escrow",
      [id, sellerAddr, "Summarise the GenLayer docs home page",
       "A 3 sentence summary of what GenLayer is, taken from https://docs.genlayer.com",
       deadline, 120],
      "buyer"
    );
    await write(
      seller,
      "mark_delivered",
      [id, "https://docs.genlayer.com", "Summary written in the note"],
      "seller"
    );
    await write(buyer, "raise_dispute", [id, "The summary is missing entirely."], "buyer");
    await write(seller, "respond", [id, "The summary is on the delivered page."], "seller");
  } else {
    await write(
      buyer,
      "create_escrow",
      [id, sellerAddr, "10 product photos, 2000px wide",
       "Ten original photos of the product, 2000px wide, delivered as a public gallery.",
       deadline, 120],
      "buyer"
    );
    await write(
      seller,
      "mark_delivered",
      [id, "https://example.com", "Delivered, see the page"],
      "seller"
    );
    await write(buyer, "raise_dispute", [id, "No photos were delivered at all, that is a placeholder page."], "buyer");
    await write(seller, "respond", [id, "The gallery is on the page I submitted."], "seller");
  }

  const count = await read("escrow_count", []);
  console.log("\nescrows on chain:", count);
  if (Number(count) === 0) {
    throw new Error("the create call did not persist: check the genvm trace of its transaction");
  }

  console.log("\nstate before the verdict");
  console.log(JSON.stringify(await read("get_escrow", [id]), null, 2));

  console.log("\n[validator set] round 1: resolve");
  const t0 = Date.now();
  const hash = await buyer.writeContract({ address: ADDRESS, functionName: "resolve", args: [id], value: 0n });
  console.log(`  tx ${hash}`);
  const receipt = await wait(buyer, hash);
  console.log(`  ${receipt.status_name} after ${Math.round((Date.now() - t0) / 1000)}s`);

  const proposed = await read("get_escrow", [id]);
  const heldAfterRound1 = (await read("stats", [])).held;
  console.log("\nround 1 escrow");
  console.log(JSON.stringify(proposed, null, 2));
  console.log("held after round 1:", heldAfterRound1);
  if (proposed.state !== "proposed") {
    throw new Error(`round 1 left the escrow in ${proposed.state}; the verdict must be held, not paid`);
  }
  // Studio net is gasless, so these keys hold nothing to stake: the escrow is
  // created unfunded (state "open") and this run exercises the verdict, appeal
  // and settlement state machine, not a transfer. The money-holding side is
  // covered by the direct-mode tests, which fund the escrow.
  const funded = Number(proposed.amount || 0) > 0;
  console.log("escrow amount:", proposed.amount, funded ? "(funded)" : "(unfunded ruling)");
  if (funded && String(heldAfterRound1) === "0") {
    throw new Error("the verdict paid out before the appeal window: the money was released in round 1");
  }

  // The side the verdict went against is the one with a reason to appeal.
  const appellant = proposed.verdict === "release" ? buyer : seller;
  const appellantName = proposed.verdict === "release" ? "buyer" : "seller";
  await write(
    appellant,
    "appeal",
    [id, "The delivered page does carry the work; the first round read the placeholder instead of the gallery link."],
    appellantName
  );

  const appealed = await read("get_escrow", [id]);
  console.log("\nstate after appeal:", appealed.state, "| round:", appealed.round, "| held:", (await read("stats", [])).held);
  if (appealed.state !== "appealed") throw new Error(`the appeal left the escrow in ${appealed.state}`);

  console.log("\n[validator set] round 2: resolve_appeal");
  const t1 = Date.now();
  const appealHash = await buyer.writeContract({ address: ADDRESS, functionName: "resolve_appeal", args: [id], value: 0n });
  console.log(`  tx ${appealHash}`);
  const appealReceipt = await wait(buyer, appealHash);
  console.log(`  ${appealReceipt.status_name} after ${Math.round((Date.now() - t1) / 1000)}s`);

  const final = await read("get_escrow", [id]);
  console.log("\nfinal escrow");
  console.log(JSON.stringify(final, null, 2));
  console.log("\nstats");
  console.log(JSON.stringify(await read("stats", []), null, 2));

  if (!["released", "refunded", "split"].includes(final.state)) {
    throw new Error(`round 2 did not settle: state is ${final.state}`);
  }
  if (final.round !== "2") throw new Error(`expected round 2, got ${final.round}`);
  console.log("\n1st verdict:", proposed.verdict, "| final verdict:", final.verdict);
  console.log(final.verdict === proposed.verdict
    ? "\nAPPEAL: the second round confirmed the first verdict"
    : "\nAPPEAL: the second round overturned the first verdict");
})().catch((e) => {
  console.error("\nFAILED:", e.message);
  if (e.cause) console.error(String(e.cause).slice(0, 800));
  process.exit(1);
});
