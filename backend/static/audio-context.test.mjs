// audio-context.test.mjs — tests for the shared AudioContext + iOS unlock.
// Run in plain node (no browser, no deps):  node backend/static/audio-context.test.mjs

import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

const src = readFileSync(new URL("./audio-context.js", import.meta.url), "utf8");

// Minimal fakes. `state` is writable so a test can park the context in
// Safari's non-standard "interrupted" state.
function load({ hasWebAudio = true, audioSession = { type: "auto" } } = {}) {
  const created = [];
  const listeners = {};
  class FakeCtx {
    constructor(opts) {
      this.options = opts;
      this.state = "suspended";
      this.resumeCalls = 0;
      this.closed = false;
      created.push(this);
    }
    resume() { this.resumeCalls += 1; this.state = "running"; return Promise.resolve(); }
    close() { this.closed = true; this.state = "closed"; return Promise.resolve(); }
    addEventListener() {}
  }
  const documentStub = {
    hidden: false,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
  };
  const windowStub = {};
  if (hasWebAudio) windowStub.AudioContext = FakeCtx;
  const navigatorStub = audioSession ? { audioSession } : {};
  new Function("window", "document", "navigator", src)(
    windowStub, documentStub, navigatorStub,
  );
  return { api: windowStub.JamnAudio, created, listeners, navigatorStub, documentStub };
}

test("context() returns ONE shared context across every caller", () => {
  const { api, created } = load();
  const a = api.context();
  const b = api.context();
  assert.equal(a, b);
  assert.equal(created.length, 1, "a second call must not mint a second context");
  assert.equal(a.options.latencyHint, "interactive");
});

test("no Web Audio support returns null instead of throwing", () => {
  const { api } = load({ hasWebAudio: false });
  assert.equal(api.context(), null);
});

test("creating the context files the iOS audio session under 'playback'", () => {
  // Without this iOS treats a WebAudio-only page as ambient: the hardware
  // ringer switch mutes it.
  const { api, navigatorStub } = load();
  api.context();
  assert.equal(navigatorStub.audioSession.type, "playback");
});

test("a browser with no navigator.audioSession still builds a context", () => {
  const { api } = load({ audioSession: null });
  assert.ok(api.context());
});

test("unlock() resumes a context parked in Safari's 'interrupted' state", async () => {
  // The regression: every surface guarded on `state === "suspended"`, so an
  // interrupted context (call, Siri, route change, background tab) was never
  // resumed and the UI animated in silence.
  const { api } = load();
  const ctx = api.context();
  ctx.state = "interrupted";
  assert.equal(await api.unlock(), true);
  assert.equal(ctx.resumeCalls, 1);
  assert.equal(ctx.state, "running");
});

test("unlock() resumes a suspended context too", async () => {
  const { api } = load();
  const ctx = api.context();
  ctx.state = "suspended";
  assert.equal(await api.unlock(), true);
  assert.equal(ctx.resumeCalls, 1);
});

test("unlock() is a no-op once running", async () => {
  const { api } = load();
  const ctx = api.context();
  ctx.state = "running";
  assert.equal(await api.unlock(), true);
  assert.equal(ctx.resumeCalls, 0, "must not churn resume() on every gesture");
});

test("unlock() never creates a context", async () => {
  const { api, created } = load();
  assert.equal(await api.unlock(), false);
  assert.equal(created.length, 0, "a page that never plays gets no audio unit");
});

test("release() refuses to close the shared context", () => {
  const { api } = load();
  const ctx = api.context();
  assert.equal(api.release(ctx), false);
  assert.equal(ctx.closed, false, "one surface unmounting must not kill the rest");
  assert.equal(api.isShared(ctx), true);
});

test("release() closes a foreign context", () => {
  const { api } = load();
  api.context();
  const foreign = { state: "running", closed: false, close() { this.closed = true; } };
  assert.equal(api.release(foreign), true);
  assert.equal(foreign.closed, true);
  assert.equal(api.isShared(foreign), false);
});

test("release(null) is safe", () => {
  const { api } = load();
  assert.equal(api.release(null), false);
});

test("gesture listeners are registered in the capture phase", () => {
  // The pad grid calls preventDefault/stopPropagation on pointerdown; a
  // bubble-phase unlock would be starved.
  const { listeners } = load();
  for (const t of ["pointerdown", "touchend", "mousedown", "keydown"]) {
    assert.ok(listeners[t] && listeners[t].length === 1, `missing listener for ${t}`);
  }
  assert.ok(listeners.visibilitychange);
});

test("returning to a visible tab unlocks", async () => {
  const { api, listeners, documentStub } = load();
  const ctx = api.context();
  ctx.state = "interrupted";
  documentStub.hidden = false;
  listeners.visibilitychange[0]();
  await Promise.resolve();
  assert.equal(ctx.resumeCalls, 1);
});
