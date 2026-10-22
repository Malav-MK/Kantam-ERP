import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import request from "supertest";
import { exportSPKI, generateKeyPair, SignJWT } from "jose";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createApp } from "../src/app.js";
import { loadConfig } from "../src/config.js";
import { signRequest, TokenVerifier, ipInRanges, parseCidrs } from "../src/policy.js";

const SECRET = "x".repeat(40);
const DEVICE = "11111111-2222-3333-4444-555555555555";
let backend, backendUrl, keys, verifier, seen;

before(async () => {
  backend = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    seen = { url: req.url, headers: req.headers, method: req.method, body };
    const h = req.headers;
    const ok = h["x-zt-signature"] === signRequest(SECRET, {
      timestamp: h["x-zt-timestamp"], method: req.method, path: req.url, clientIp: h["x-zt-client-ip"], requestId: h["x-zt-request-id"],
    });
    res.writeHead(ok ? 200 : 403, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ signatureValid: ok }));
  });
  await new Promise((r) => backend.listen(0, "127.0.0.1", r));
  backendUrl = `http://127.0.0.1:${backend.address().port}`;
  keys = await generateKeyPair("EdDSA", { crv: "Ed25519" });
  verifier = new TokenVerifier();
  await verifier.setPublicKey({ public_key_pem: await exportSPKI(keys.publicKey), issuer: "kantam-erp", audience: "kantam-gateway" });
});

after(() => backend.close());

const cfg = (over = {}) => ({
  backendUrl, gatewaySecret: SECRET, allowedCidrs: ["127.0.0.0/8", "::1/128"], trustedProxyCidrs: [], tlsCert: "", staticDir: "",
  requireDevice: true, rateLimitPerMinute: 1000, authRateLimitPerMinute: 1000, maxBodyBytes: 1024 * 1024, ...over,
});
const quiet = { log: {} };

async function token(claims = {}, key = keys.privateKey) {
  return new SignJWT({ sid: "s1", did: DEVICE, typ: "access", rv: 1, ...claims })
    .setProtectedHeader({ alg: "EdDSA" }).setSubject("u1").setIssuer("kantam-erp").setAudience("kantam-gateway")
    .setIssuedAt().setExpirationTime("10m").sign(key);
}

test("cidr matching", () => {
  const r = parseCidrs(["10.0.0.0/8"]);
  assert.equal(ipInRanges("10.20.30.40", r), true);
  assert.equal(ipInRanges("::ffff:10.1.1.1", r), true);
  assert.equal(ipInRanges("8.8.8.8", r), false);
  assert.equal(ipInRanges("garbage", r), false);
});

test("requests from outside approved networks fail closed", async () => {
  const app = createApp(cfg({ allowedCidrs: ["10.0.0.0/8"] }), verifier, quiet);
  const r = await request(app).get("/api/auth/me");
  assert.equal(r.status, 403);
  assert.equal(r.body.reason, "network not approved");
});

test("X-Forwarded-For is not trusted from untrusted peers", async () => {
  const app = createApp(cfg({ allowedCidrs: ["10.0.0.0/8"] }), verifier, quiet);
  const r = await request(app).get("/api/auth/device").set("X-Forwarded-For", "10.0.0.5");
  assert.equal(r.status, 403);
});

test("public auth endpoints are forwarded with a valid signature", async () => {
  const app = createApp(cfg(), verifier, quiet);
  const r = await request(app).get("/api/auth/device?x=1");
  assert.equal(r.status, 200);
  assert.equal(r.body.signatureValid, true);
  assert.equal(seen.url, "/api/auth/device?x=1");
});

test("protected API requires a token", async () => {
  const r = await request(createApp(cfg(), verifier, quiet)).get("/api/meta");
  assert.equal(r.status, 401);
  assert.equal(r.body.reason, "token_missing");
});

test("token must be bound to the presenting device", async () => {
  const app = createApp(cfg(), verifier, quiet);
  const t = await token();
  const noCookie = await request(app).get("/api/meta").set("Authorization", `Bearer ${t}`);
  assert.equal(noCookie.body.reason, "device_not_trusted");
  const otherDevice = await request(app).get("/api/meta").set("Authorization", `Bearer ${t}`).set("Cookie", "kt_dev=99999999-2222-3333-4444-555555555555.secret");
  assert.equal(otherDevice.status, 401);
  const ok = await request(app).get("/api/meta").set("Authorization", `Bearer ${t}`).set("Cookie", `kt_dev=${DEVICE}.secret`);
  assert.equal(ok.status, 200);
  assert.equal(ok.body.signatureValid, true);
});

test("forged tokens are rejected", async () => {
  const other = await generateKeyPair("EdDSA", { crv: "Ed25519" });
  const t = await token({}, other.privateKey);
  const r = await request(createApp(cfg(), verifier, quiet)).get("/api/meta").set("Authorization", `Bearer ${t}`).set("Cookie", `kt_dev=${DEVICE}.s`);
  assert.equal(r.status, 401);
  assert.equal(r.body.reason, "token_invalid");
});

test("client-supplied X-ZT headers are stripped", async () => {
  const app = createApp(cfg(), verifier, quiet);
  await request(app).get("/api/auth/device").set("X-ZT-Client-IP", "10.9.9.9");
  assert.equal(seen.headers["x-zt-client-ip"], "127.0.0.1");
  assert.equal(seen.headers.host, new URL(backendUrl).host);  // backend never sees the public hostname
});

test("cross-site and CORS requests are refused", async () => {
  const app = createApp(cfg(), verifier, quiet);
  assert.equal((await request(app).post("/api/auth/login").set("Sec-Fetch-Site", "cross-site")).status, 403);
  assert.equal((await request(app).post("/api/auth/login").set("Origin", "https://evil.example")).status, 403);
  assert.equal((await request(app).options("/api/auth/login")).status, 403);
});

test("internal endpoints are not exposed", async () => {
  const r = await request(createApp(cfg(), verifier, quiet)).get("/internal/jwt-public-key");
  assert.equal(r.status, 404);
});

test("security headers are set", async () => {
  const r = await request(createApp(cfg(), verifier, quiet)).get("/healthz");
  assert.match(r.headers["content-security-policy"], /frame-ancestors 'none'/);
  assert.equal(r.headers["x-powered-by"], undefined);
  assert.match(r.headers["permissions-policy"], /microphone=\(\)/);
});

test("auth endpoints are rate limited", async () => {
  const app = createApp(cfg({ authRateLimitPerMinute: 3 }), verifier, quiet);
  let last;
  for (let i = 0; i < 4; i++) last = await request(app).post("/api/auth/login").send({});
  assert.equal(last.status, 429);
});

test("request bodies stream through unchanged and upstream failures fail closed", async () => {
  const app = createApp(cfg(), verifier, quiet);
  const r = await request(app).post("/api/auth/login").set("Content-Type", "application/json").send({ username: "u", password: "p" });
  assert.equal(r.body.signatureValid, true);
  assert.deepEqual(JSON.parse(seen.body), { username: "u", password: "p" });
  assert.equal(seen.headers["x-forwarded-for"], undefined);
  const down = createApp(cfg({ backendUrl: "http://127.0.0.1:9" }), verifier, quiet);
  const d = await request(down).get("/api/auth/device");
  assert.equal(d.status, 502);
});

test("settings can come from the protected env file; real environment wins", () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "kt-")), "kantam.env");
  fs.writeFileSync(file, "# comment\r\nZT_GATEWAY_SECRET=" + "s".repeat(40) + "\r\nPORT=9443\r\nHOST=127.0.0.1\r\n");
  const cfg = loadConfig({ KANTAM_ENV_FILE: file, HOST: "0.0.0.0" });
  assert.equal(cfg.gatewaySecret, "s".repeat(40));
  assert.equal(cfg.port, 9443);
  assert.equal(cfg.host, "0.0.0.0");
});
