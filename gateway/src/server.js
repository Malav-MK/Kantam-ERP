import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import { createApp } from "./app.js";
import { loadConfig } from "./config.js";
import { signRequest, TokenVerifier } from "./policy.js";

const cfg = loadConfig();
const verifier = new TokenVerifier();

// Fetch the token verification key over the signed internal channel.
async function refreshKey() {
  const path = "/internal/jwt-public-key";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const requestId = crypto.randomUUID().replace(/-/g, "");
  const res = await fetch(cfg.backendUrl + path, {
    headers: {
      "X-ZT-Timestamp": timestamp, "X-ZT-Client-IP": "127.0.0.1", "X-ZT-Request-Id": requestId,
      "X-ZT-Signature": signRequest(cfg.gatewaySecret, { timestamp, method: "GET", path, clientIp: "127.0.0.1", requestId }),
    },
  });
  if (!res.ok) throw new Error(`public key fetch failed: ${res.status}`);
  await verifier.setPublicKey(await res.json());
}

async function keyLoop() {
  for (let attempt = 1; !verifier.ready; attempt++) {
    try {
      await refreshKey();
      console.info("token verification key loaded");
    } catch (e) {
      console.warn(`waiting for backend (${attempt}): ${e.message}`);
      await new Promise((r) => setTimeout(r, Math.min(attempt * 1000, 10_000)));
    }
  }
  setInterval(() => refreshKey().catch((e) => console.error("key refresh failed", e.message)), cfg.publicKeyRefreshMs).unref();
}

const app = createApp(cfg, verifier);
const tlsOptions = cfg.tlsPfx
  ? { pfx: fs.readFileSync(cfg.tlsPfx), passphrase: cfg.tlsPfxPassphrase, minVersion: "TLSv1.2" }
  : cfg.tlsCert ? { cert: fs.readFileSync(cfg.tlsCert), key: fs.readFileSync(cfg.tlsKey), minVersion: "TLSv1.2" } : null;
const server = tlsOptions ? https.createServer(tlsOptions, app) : http.createServer(app);
server.headersTimeout = 30_000;
server.requestTimeout = 120_000;
server.listen(cfg.port, cfg.host, () => {
  console.info(`kantam gateway on ${tlsOptions ? "https" : "http"}://${cfg.host}:${cfg.port} -> ${cfg.backendUrl}`);
  if (!tlsOptions) console.warn("TLS is OFF - acceptable only for local development. Production must set TLS_PFX_FILE or TLS_CERT_FILE/TLS_KEY_FILE.");
});
keyLoop();
