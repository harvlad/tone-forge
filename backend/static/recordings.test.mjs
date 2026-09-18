// recordings.test.mjs — DOM-free test for the recordings.js capture mix
// bus. Run: node recordings.test.mjs
//
// Pins the kit-pad-silence bug: attachSource must SUM sources (never
// replace the tap), bridge across AudioContexts (kit.js owns its own),
// and refuse an AudioDestinationNode (numberOfOutputs 0 — nothing can
// be tapped off it; the old jam.js fallback handed one in, threw, and
// takes silently lost the pads).
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./recordings.js", import.meta.url), "utf8");
const window = {};
const document = { createElement: () => ({}) };
new Function("window", "document", src)(window, document);

const R = window.JamnRecordings;
assert.equal(typeof R.attachSource, "function");
assert.equal(typeof R.createTap, "function");
assert.equal(typeof R.toggleRecord, "function");
assert.equal(typeof R.isRecording, "function");
assert.equal(R.isRecording(), false);

// classifySource: the pure routing decision behind attachSource.
const { classifySource } = R._pure;
const isStream = (x) => !!x && x.__stream === true;
const stream = { __stream: true };
assert.equal(classifySource(stream, isStream), "stream");
// MediaStreamAudioDestinationNode duck type: its .stream wins even
// though, like every destination, it has zero outputs.
assert.equal(
  classifySource({ stream, context: {}, connect() {}, numberOfOutputs: 0 }, isStream),
  "tap",
);
assert.equal(
  classifySource({ context: {}, connect() {}, numberOfOutputs: 1 }, isStream),
  "node",
);
// AudioDestinationNode: connectable-looking but 0 outputs → unusable.
assert.equal(
  classifySource({ context: {}, connect() {}, numberOfOutputs: 0 }, isStream),
  "unusable",
);
assert.equal(classifySource(null, isStream), "unusable");
assert.equal(classifySource({}, isStream), "unusable");

// ---- attachSource additivity across two AudioContexts (song bus in
// jam.js's context + pad bus in kit.js's own context). ----

function fakeCtx(name) {
  const ctx = { name, state: "running", taps: [], sources: [] };
  ctx.createMediaStreamDestination = () => {
    const dest = {
      context: ctx,
      numberOfOutputs: 0,
      stream: { fromCtx: name },
      connect() {},
    };
    ctx.taps.push(dest);
    return dest;
  };
  ctx.createMediaStreamSource = (s) => {
    const node = {
      context: ctx,
      stream: s,
      numberOfOutputs: 1,
      connectedTo: null,
      connect(n) { this.connectedTo = n; },
    };
    ctx.sources.push(node);
    return node;
  };
  return ctx;
}
function fakeNode(ctx) {
  return {
    context: ctx,
    numberOfOutputs: 1,
    connected: [],
    connect(n) { this.connected.push(n); },
  };
}

const ctxA = fakeCtx("A"); // song-player context
const ctxB = fakeCtx("B"); // kit context

// First source establishes the mix bus in its own context…
const songMaster = fakeNode(ctxA);
R.attachSource(songMaster);
assert.equal(ctxA.taps.length, 1, "mix bus created in first source's ctx");
const mixBus = ctxA.taps[0];
assert.deepEqual(songMaster.connected, [mixBus]);

// …and a second, FOREIGN-context source SUMS in over a stream bridge.
// The song connection must survive — the pre-fix code replaced it,
// which is exactly how takes ended up song-only.
const kitMaster = fakeNode(ctxB);
R.attachSource(kitMaster);
assert.deepEqual(songMaster.connected, [mixBus], "song attach survives");
assert.equal(ctxB.taps.length, 1, "kit got a local feeder dest");
assert.equal(kitMaster.connected[0], ctxB.taps[0]);
assert.equal(ctxA.sources.length, 1, "kit stream bridged into the mix ctx");
assert.equal(ctxA.sources[0].stream, ctxB.taps[0].stream);
assert.equal(ctxA.sources[0].connectedTo, mixBus);

// Idempotent per object: re-attaching the same node is a no-op, so
// hosts may re-attach on every (re)mount without doubling levels.
R.attachSource(kitMaster);
assert.equal(kitMaster.connected.length, 1);
assert.equal(ctxA.sources.length, 1);

// An AudioDestinationNode look-alike must be refused, never adopted.
R.attachSource({
  context: ctxB,
  numberOfOutputs: 0,
  connect() { throw new Error("must not connect from a destination"); },
});

// createTap from the mix context reuses the bus; from a foreign context
// it returns a LOCAL feeder that bridges in (it never steals the bus).
assert.equal(R.createTap(ctxA), mixBus);
const feeder = R.createTap(ctxB);
assert.notEqual(feeder, mixBus);
assert.equal(feeder.context, ctxB);
const lastBridge = ctxA.sources[ctxA.sources.length - 1];
assert.equal(lastBridge.stream, feeder.stream);
assert.equal(lastBridge.connectedTo, mixBus);

console.log("recordings.test.mjs OK");
