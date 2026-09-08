// kit.test.mjs — DOM-free smoke test for kit.js. Run: node kit.test.mjs
// Stubs window/document just enough to evaluate the classic script and
// exercise the pure helpers + the no-root mount/unmount no-throw paths.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./kit.js", import.meta.url), "utf8");
const window = { location: { origin: "https://jamn.app" } };
const document = { getElementById: () => null };
new Function("window", "document", src)(window, document);

const K = window.JamnKit;
assert.equal(typeof K.mount, "function");
assert.equal(typeof K.unmount, "function");

const { resolveStemUrl, parseColor } = K._internals;
assert.equal(resolveStemUrl("/api/stems/x.wav"), "https://jamn.app/api/stems/x.wav");
assert.equal(resolveStemUrl("https://r2.example.com/a.wav?sig=1"), "https://r2.example.com/a.wav?sig=1");
assert.equal(resolveStemUrl(null), null);

assert.deepEqual(parseColor("#3B82F6"), { r: 59, g: 130, b: 246 });
assert.deepEqual(parseColor("3B82F6"), { r: 59, g: 130, b: 246 });
assert.deepEqual(parseColor(0xff0000), { r: 255, g: 0, b: 0 });
assert.deepEqual(parseColor("garbage"), { r: 139, g: 92, b: 246 }); // accent fallback
assert.deepEqual(parseColor(undefined), { r: 139, g: 92, b: 246 });

K.mount(null); // no #kit-root in the stub → silent no-op, must not throw
K.unmount(); // nothing mounted → must not throw

console.log("kit.test.mjs: all assertions passed");
