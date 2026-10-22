// Zero-trust policy primitives: network allowlist, request signing, token verification.
import crypto from "node:crypto";
import ipaddr from "ipaddr.js";
import { importSPKI, jwtVerify } from "jose";

export function parseCidrs(cidrs) {
  return cidrs.map((c) => {
    const [addr, bits] = c.split("/");
    return [ipaddr.parse(addr), Number(bits ?? (addr.includes(":") ? 128 : 32))];
  });
}

export function normaliseIp(raw) {
  if (!raw || !ipaddr.isValid(raw)) return null;
  let addr = ipaddr.parse(raw);
  if (addr.kind() === "ipv6" && addr.isIPv4MappedAddress()) addr = addr.toIPv4Address();
  return addr;
}

export function ipInRanges(raw, ranges) {
  const addr = normaliseIp(raw);
  if (!addr) return false;
  return ranges.some(([net, bits]) => net.kind() === addr.kind() && addr.match(net, bits));
}

// Resolve the real client address. X-Forwarded-For is honoured only when the
// direct peer is an explicitly trusted proxy - otherwise it is spoofable.
export function clientIp(req, trustedProxies) {
  const peer = normaliseIp(req.socket.remoteAddress)?.toString() ?? null;
  if (peer && trustedProxies.length && ipInRanges(peer, trustedProxies)) {
    const fwd = (req.headers["x-forwarded-for"] || "").split(",").map((s) => s.trim()).filter(Boolean);
    for (let i = fwd.length - 1; i >= 0; i--) {
      if (!ipInRanges(fwd[i], trustedProxies)) return normaliseIp(fwd[i])?.toString() ?? null;
    }
  }
  return peer;
}

// Must match apps/core/security.py gateway_signature().
export function signRequest(secret, { timestamp, method, path, clientIp, requestId }) {
  const message = `${timestamp}\n${method.toUpperCase()}\n${path}\n${clientIp}\n${requestId}`;
  return crypto.createHmac("sha256", secret).update(message).digest("hex");
}

export function deviceIdFromCookie(raw) {
  if (!raw || !raw.includes(".")) return null;
  const id = raw.split(".", 1)[0];
  return /^[0-9a-f-]{36}$/i.test(id) ? id.toLowerCase() : null;
}

export class TokenVerifier {
  constructor() {
    this.key = null;
    this.issuer = null;
    this.audience = null;
  }

  async setPublicKey({ public_key_pem, issuer, audience }) {
    this.key = await importSPKI(public_key_pem, "EdDSA");
    this.issuer = issuer;
    this.audience = audience;
  }

  get ready() {
    return this.key !== null;
  }

  async verify(token) {
    const { payload } = await jwtVerify(token, this.key, {
      algorithms: ["EdDSA"],
      issuer: this.issuer,
      audience: this.audience,
      requiredClaims: ["exp", "sub", "sid", "typ"],
    });
    if (payload.typ !== "access") throw new Error("wrong token type");
    return payload;
  }
}
