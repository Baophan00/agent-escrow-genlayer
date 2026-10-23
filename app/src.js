import { createClient, createAccount, generatePrivateKey } from "genlayer-js";
import { studionet } from "genlayer-js/chains";

const CONTRACT = "0x5C4617747e1539b06038BfAc09aCE654D2E9fe9D";
const EXPLORER = `https://explorer-studio.genlayer.com/address/${CONTRACT}`;
const KEY_SLOT = "agentescrow.keys";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let logTimer;
function log(msg) {
  const box = $("log");
  box.textContent = msg;
  box.style.display = "block";
  clearTimeout(logTimer);
  logTimer = setTimeout(() => (box.style.display = "none"), 6000);
}

function loadKeys() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY_SLOT) || "[]");
    return Array.isArray(raw) ? raw.filter((k) => typeof k === "string" && k) : [];
  } catch {
    return [];
  }
}
let keys = loadKeys();
let active = 0;
let account = keys.length ? createAccount(keys[0]) : null;
let me = account ? String(account.address) : "";
const reader = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });

function short(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "";
}

$("contractlink").textContent = short(CONTRACT);
$("contractlink").href = EXPLORER;

function paintKey() {
  const sel = $("acting");
  sel.replaceChildren();
  if (!keys.length) {
    const o = el("option", "", "no key");
    o.value = "";
    sel.append(o);
  }
  keys.forEach((k, i) => {
    const o = el("option", "", `key ${i + 1} · ${short(String(createAccount(k).address))}`);
    o.value = String(i);
    sel.append(o);
  });
  sel.value = String(active);
  $("addr").textContent = me || "no key yet";
  $("walletchip").textContent = me ? `You ${short(me)}` : "No key";
}

function saveKeys() {
  localStorage.setItem(KEY_SLOT, JSON.stringify(keys));
}

function actAs(i) {
  active = i;
  account = keys[i] ? createAccount(keys[i]) : null;
  me = account ? String(account.address) : "";
  paintKey();
  refresh();
}

$("acting").onchange = (e) => actAs(Number(e.target.value));

$("genkey").onclick = () => {
  keys.push(generatePrivateKey());
  saveKeys();
  actAs(keys.length - 1);
  log("Key added. Fill the seller field with the other key to run both sides.");
};

$("importkey").onclick = () => {
  const v = prompt("Private key (0x…). Stays in this browser.");
  if (!v) return;
  try {
    createAccount(v.trim());
    keys.push(v.trim());
    saveKeys();
    actAs(keys.length - 1);
    log("Key loaded.");
  } catch (e) {
    log("That key could not be read.");
  }
};

$("useseller").onclick = (ev) => {
  ev.preventDefault();
  const other = keys.find((k, i) => i !== active);
  if (!other) return log("Add a second key first.");
  $("f-seller").value = String(createAccount(other).address);
};

async function read(functionName, args = []) {
  return reader.readContract({ address: CONTRACT, functionName, args });
}

function writer() {
  if (!account) throw new Error("Add a key first.");
  return createClient({ chain: studionet, account });
}

async function send(functionName, args, value = 0n) {
  const client = writer();
  log(`${functionName}: sending…`);
  const hash = await client.writeContract({ address: CONTRACT, functionName, args, value });
  log(`${functionName}: waiting for finality…`);
  await client.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 160, interval: 3000 });
  log(`${functionName}: done`);
  await refresh();
}

const STATE_LABEL = {
  open: "open", funded: "funded", delivered: "delivered", disputed: "disputed",
  released: "released", refunded: "refunded", split: "split", cancelled: "cancelled",
};

function when(ts) {
  const n = Number(ts || 0);
  if (!n) return "—";
  return new Date(n * 1000).toLocaleString();
}

function actionButton(label, fn, primary = false) {
  const b = el("button", `small${primary ? " primary" : ""}`, label);
  b.onclick = async () => {
    b.disabled = true;
    try {
      await fn();
    } catch (e) {
      log(String(e.message || e).slice(0, 200));
    } finally {
      b.disabled = false;
    }
  };
  return b;
}

function renderEscrow(e) {
  const card = el("div", "esc");
  const head = el("div", "meta");
  head.append(el("span", `pill ${e.state}`, STATE_LABEL[e.state] || e.state));
  if (Number(e.amount) > 0) head.append(el("span", "pill", `${Number(e.amount) / 1e18} GEN`));
  else head.append(el("span", "pill", "no funds"));
  card.append(head);

  card.append(el("h3", "", e.title));
  card.append(el("div", "kv", e.terms));

  const line = el("div", "kv");
  line.innerHTML = `buyer <b class="mono">${short(e.buyer)}</b> · seller <b class="mono">${short(e.seller)}</b> · deadline ${when(e.deadline)}`;
  card.append(line);

  if (e.delivery_url) {
    const d = el("div", "kv");
    d.innerHTML = `delivery <a href="${e.delivery_url}" target="_blank" rel="noreferrer">${e.delivery_url.replace(/^https?:\/\//, "").slice(0, 48)}</a>`;
    card.append(d);
    if (e.delivery_note) card.append(el("div", "kv", e.delivery_note));
  }
  if (e.dispute_reason) card.append(el("div", "kv", `claim: ${e.dispute_reason}`));
  if (e.seller_response) card.append(el("div", "kv", `answer: ${e.seller_response}`));

  if (e.verdict) {
    const v = el("div", "verdict");
    v.append(el("b", "", `Verdict: ${e.verdict}`));
    v.append(document.createTextNode(e.verdict_reason || ""));
    card.append(v);
  }

  const isBuyer = me && me.toLowerCase() === String(e.buyer).toLowerCase();
  const isSeller = me && me.toLowerCase() === String(e.seller).toLowerCase();
  const expired = Number(e.deadline) > 0 && Date.now() / 1000 > Number(e.deadline);
  const acts = el("div", "actions");
  const id = e.id;

  if (isBuyer && (e.state === "open" || e.state === "funded")) {
    acts.append(actionButton("Cancel", () => send("cancel", [id])));
  }
  if (isBuyer && (e.state === "open" || e.state === "funded") && expired) {
    acts.append(actionButton("Reclaim", () => send("reclaim", [id]), true));
  }
  if (isSeller && (e.state === "open" || e.state === "funded") && !expired) {
    acts.append(actionButton("Delivered", async () => {
      const url = prompt("Public URL that proves the work");
      if (!url) return;
      const note = prompt("One line about what is there") || "";
      await send("mark_delivered", [id, url, note]);
    }, true));
  }
  if (isBuyer && e.state === "delivered") {
    acts.append(actionButton("Confirm", () => send("confirm_delivery", [id])));
    acts.append(actionButton("Dispute", async () => {
      const why = prompt("What is missing? One sentence.");
      if (!why) return;
      await send("raise_dispute", [id, why]);
    }));
  }
  if (isSeller && e.state === "disputed") {
    acts.append(actionButton("Answer", async () => {
      const txt = prompt("Your answer to the claim");
      if (!txt) return;
      await send("respond", [id, txt]);
    }));
  }
  if (e.state === "disputed") {
    acts.append(actionButton("Ask validators", () => send("resolve", [id]), true));
  }
  if (acts.children.length) card.append(acts);
  return card;
}

async function refresh() {
  try {
    const [stats, rows] = await Promise.all([read("stats"), read("list_escrows", [0, 30])]);
    const s = $("stats");
    s.replaceChildren();
    const cells = [
      ["Escrows", stats.total],
      ["Open", (stats.states && stats.states.open) + (stats.states && stats.states.funded) || 0],
      ["Disputed", (stats.states && stats.states.disputed) || 0],
      ["Settled", ((stats.states && (stats.states.released + stats.states.refunded + stats.states.split + stats.states.cancelled)) || 0)],
    ];
    for (const [k, v] of cells) {
      const c = el("div", "stat");
      c.append(el("span", "", k), el("b", "", String(v)));
      s.append(c);
    }

    const list = $("list");
    list.replaceChildren();
    if (!rows.length) list.append(el("p", "sub", "No escrows yet. Create the first one on the right."));
    for (const e of rows) list.append(renderEscrow(e));
  } catch (e) {
    log(`read failed: ${String(e.message || e).slice(0, 160)}`);
  }
}

$("create").onclick = async () => {
  const btn = $("create");
  btn.disabled = true;
  try {
    if (!account) throw new Error("Add a key first, that address is the buyer.");
    const seller = $("f-seller").value.trim();
    const title = $("f-title").value.trim();
    const terms = $("f-terms").value.trim();
    if (!/^0x[0-9a-fA-F]{40}$/.test(seller)) throw new Error("Seller address looks wrong.");
    if (!title || !terms) throw new Error("Title and terms are required.");
    const minutes = Math.max(1, Number($("f-deadline").value || 1440));
    // The contract rejects anything under 60 seconds, so do not offer it.
    const review = Math.max(60, Number($("f-review").value || 120));
    const gen = Number($("f-amount").value || 0);
    const value = BigInt(Math.round(gen * 1e18));
    const deadline = Math.floor(Date.now() / 1000) + minutes * 60;
    const id = `esc-${Date.now()}`;
    await send("create_escrow", [id, seller, title, terms, deadline, review], value);
    $("f-title").value = "";
    $("f-terms").value = "";
  } catch (e) {
    log(String(e.message || e).slice(0, 220));
  } finally {
    btn.disabled = false;
  }
};

paintKey();
window.addEventListener("error", (e) => log(`page error: ${String(e.message).slice(0, 160)}`));
// First paint can land while the CDN module is still cold; retry once instead
// of leaving the page blank.
refresh().then(() => {}).catch(() => setTimeout(refresh, 2500));
setInterval(refresh, 20000);
