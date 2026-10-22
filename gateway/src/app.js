// Express application: the only ingress to the ERP. Order of enforcement:
// network -> request hygiene -> rate limit -> identity (token + device binding)
// -> signed forwarding to Django (which re-verifies everything).
import crypto from "node:crypto";
import path from "node:path";
import fs from "node:fs";
import express from "express";
import helmet from "helmet";
import cookieParser from "cookie-parser";
import rateLimit from "express-rate-limit";
import http from "node:http";
import { clientIp, deviceIdFromCookie, ipInRanges, parseCidrs, signRequest } from "./policy.js";

// Endpoints reachable without an access token (they carry their own checks in Django).
const PUBLIC_API = new Set([
  "GET /api/auth/device",
  "POST /api/auth/device/enroll",
  "POST /api/auth/login",
  "POST /api/auth/mfa/setup",
  "POST /api/auth/mfa/verify",
  "POST /api/auth/refresh",
]);

function deny(res, status, reason) {
  res.status(status).json({ error: status === 401 ? "unauthenticated" : "zero_trust_denied", reason });
}

export function createApp(cfg, verifier, { log = console } = {}) {
  const allowed = parseCidrs(cfg.allowedCidrs);
  const trustedProxies = parseCidrs(cfg.trustedProxyCidrs);
  const app = express();
  app.disable("x-powered-by");
  app.set("trust proxy", false);

  // 1. Request id + real client address + structured access log (never logs tokens).
  app.use((req, res, next) => {
    req.rid = crypto.randomUUID().replace(/-/g, "");
    req.clientIp = clientIp(req, trustedProxies);
    const started = Date.now();
    res.on("finish", () => {
      log.info?.(JSON.stringify({
        t: new Date().toISOString(), rid: req.rid, ip: req.clientIp, m: req.method, p: req.path,
        s: res.statusCode, ms: Date.now() - started, sub: req.identity?.sub, did: req.identity?.did,
      }));
    });
    res.setHeader("X-Request-Id", req.rid);
    next();
  });

  // 2. Network boundary: factory networks only; fail closed (brief p.28).
  app.use((req, res, next) => {
    if (!req.clientIp || !ipInRanges(req.clientIp, allowed)) return deny(res, 403, "network not approved");
    next();
  });

  // 3. Security headers. Camera is allowed for same-origin gate/task photo capture only.
  app.use(helmet({
    contentSecurityPolicy: {
      useDefaults: false,
      directives: {
        "default-src": ["'self'"], "script-src": ["'self'"], "style-src": ["'self'", "https://fonts.googleapis.com"],
        "font-src": ["'self'", "https://fonts.gstatic.com"], "img-src": ["'self'", "blob:", "data:"], "connect-src": ["'self'"],
        "frame-src": ["'self'", "blob:"], "object-src": ["'none'"], "base-uri": ["'none'"], "form-action": ["'self'"],
        "frame-ancestors": ["'none'"], "upgrade-insecure-requests": cfg.tlsCert ? [] : null,
      },
    },
    strictTransportSecurity: cfg.tlsCert ? { maxAge: 31536000, includeSubDomains: true } : false,
    referrerPolicy: { policy: "no-referrer" },
    crossOriginEmbedderPolicy: false,
  }));
  app.use((_req, res, next) => {
    res.setHeader("Permissions-Policy", "camera=(self), microphone=(), geolocation=(), usb=(), payment=()");
    next();
  });

  app.get("/healthz", (_req, res) => res.json({ status: "ok", keyLoaded: verifier.ready }));

  // Internal endpoints are never reachable from clients.
  app.use("/internal", (_req, res) => deny(res, 404, "not found"));

  // 4. Request hygiene for the API: no cross-site requests, no CORS, bounded bodies.
  app.use("/api", (req, res, next) => {
    const site = req.headers["sec-fetch-site"];
    if (site && !["same-origin", "none"].includes(site)) return deny(res, 403, "cross-site request refused");
    const origin = req.headers.origin;
    if (origin && req.method !== "GET") {
      try {
        if (new URL(origin).host !== req.headers.host) return deny(res, 403, "origin mismatch");
      } catch {
        return deny(res, 403, "bad origin");
      }
    }
    if (req.method === "OPTIONS") return deny(res, 403, "CORS is not offered");
    const len = Number(req.headers["content-length"] || 0);
    if (len > cfg.maxBodyBytes) return deny(res, 413, "body too large");
    // Strip anything that could impersonate gateway assertions.
    for (const h of Object.keys(req.headers)) if (h.startsWith("x-zt-")) delete req.headers[h];
    next();
  });

  // 5. Rate limits (brute force / scraping), keyed on the verified client IP.
  const keyGenerator = (req) => req.clientIp;
  app.use("/api/auth", rateLimit({ windowMs: 60_000, limit: cfg.authRateLimitPerMinute, keyGenerator, standardHeaders: "draft-7", legacyHeaders: false,
    skip: (req) => req.path === "/refresh" || req.path === "/device", message: { error: "throttled", reason: "too many auth attempts" } }));
  app.use("/api", rateLimit({ windowMs: 60_000, limit: cfg.rateLimitPerMinute, keyGenerator, standardHeaders: "draft-7", legacyHeaders: false,
    message: { error: "throttled", reason: "rate limit" } }));

  // 6. Identity: verify the EdDSA access token and its device binding before forwarding.
  app.use("/api", cookieParser(), async (req, res, next) => {
    const route = `${req.method} ${req.baseUrl}${req.path}`.replace(/\/$/, "");
    if (PUBLIC_API.has(route)) return next();
    if (!verifier.ready) return deny(res, 503, "gateway not ready");
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ")) return deny(res, 401, "token_missing");
    try {
      req.identity = await verifier.verify(auth.slice(7));
    } catch (e) {
      return deny(res, 401, e?.code === "ERR_JWT_EXPIRED" ? "token_expired" : "token_invalid");
    }
    const device = deviceIdFromCookie(req.cookies?.kt_dev);
    if (cfg.requireDevice || req.identity.did) {
      if (!device || device !== String(req.identity.did).toLowerCase()) return deny(res, 401, "device_not_trusted");
    }
    next();
  });

  // 7. Signed forwarding (micro-segmentation: Django rejects anything unsigned).
  //    Minimal streaming proxy on node:http - no third-party proxy/glob dependencies.
  const target = new URL(cfg.backendUrl);
  const HOP = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded"]);
  const forward = (req, res) => {
    const headers = {};
    for (const [k, v] of Object.entries(req.headers)) if (!HOP.has(k) && !k.startsWith("x-zt-")) headers[k] = v;
    // The browser-facing hostname was already checked (origin/cross-site rules above); the backend
    // only ever sees its own internal address, so ALLOWED_HOSTS never depends on plant DNS names.
    headers.host = target.host;
    const timestamp = String(Math.floor(Date.now() / 1000));
    headers["x-zt-timestamp"] = timestamp;
    headers["x-zt-client-ip"] = req.clientIp;
    headers["x-zt-request-id"] = req.rid;
    headers["x-zt-signature"] = signRequest(cfg.gatewaySecret, { timestamp, method: req.method, path: req.originalUrl, clientIp: req.clientIp, requestId: req.rid });
    const upstream = http.request({
      protocol: target.protocol, hostname: target.hostname, port: target.port || 80, method: req.method, path: req.originalUrl, headers, timeout: 60_000,
    }, (up) => {
      const out = {};
      for (const [k, v] of Object.entries(up.headers)) if (!HOP.has(k) && k !== "server" && k !== "x-powered-by") out[k] = v;
      res.writeHead(up.statusCode || 502, out);
      up.pipe(res);
    });
    upstream.on("timeout", () => upstream.destroy(new Error("upstream timeout")));
    upstream.on("error", () => {
      if (!res.headersSent) res.status(502).json({ error: "backend_unavailable" });
      else res.destroy();
    });
    req.on("aborted", () => upstream.destroy());
    req.pipe(upstream);
  };
  app.use((req, res, next) => (req.originalUrl.startsWith("/api/") ? forward(req, res) : next()));

  // 8. Static single-page app.
  if (cfg.staticDir && fs.existsSync(cfg.staticDir)) {
    app.use("/assets", express.static(path.join(cfg.staticDir, "assets"), { immutable: true, maxAge: "365d", index: false }));
    app.use(express.static(cfg.staticDir, { index: false, maxAge: 0 }));
    app.get("*", (_req, res) => {
      res.setHeader("Cache-Control", "no-store");
      res.sendFile(path.join(cfg.staticDir, "index.html"));
    });
  }
  app.use((_req, res) => deny(res, 404, "not found"));
  return app;
}
