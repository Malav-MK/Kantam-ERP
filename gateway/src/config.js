import fs from "node:fs";
import path from "node:path";
// Gateway configuration. Every security-relevant default is the restrictive one.
const list = (v, d = "") => (v ?? d).split(",").map((s) => s.trim()).filter(Boolean);

// Windows services point KANTAM_ENV_FILE at the protected config file instead of carrying secrets
// in their service definition. Real environment variables always win.
export function readEnvFile(file) {
  const out = {};
  if (!file || !fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, "ascii").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const i = line.indexOf("=");
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

export function loadConfig(baseEnv = process.env) {
  const env = { ...readEnvFile(baseEnv.KANTAM_ENV_FILE), ...baseEnv };
  const cfg = {
    port: Number(env.PORT || 8443),
    host: env.HOST || "0.0.0.0",
    backendUrl: env.BACKEND_URL || "http://backend:8000",
    gatewaySecret: env.ZT_GATEWAY_SECRET || "",
    // Factory networks only (brief p.28). Public internet / mobile data is refused.
    allowedCidrs: list(env.ZT_ALLOWED_CIDRS, "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1/128"),
    // Proxies whose X-Forwarded-For we trust (empty = trust none, use the socket address).
    trustedProxyCidrs: list(env.ZT_TRUSTED_PROXY_CIDRS, ""),
    tlsCert: env.TLS_CERT_FILE || "",
    tlsKey: env.TLS_KEY_FILE || "",
    // Windows installs use a PKCS#12 bundle (what the Windows certificate store exports).
    tlsPfx: env.TLS_PFX_FILE || "",
    tlsPfxPassphrase: env.TLS_PFX_PASSPHRASE || "",
    staticDir: env.STATIC_DIR ? path.resolve(env.STATIC_DIR) : "",
    requireDevice: (env.ZT_REQUIRE_DEVICE ?? "true").toLowerCase() !== "false",
    rateLimitPerMinute: Number(env.RATE_LIMIT_PER_MIN || 600),
    authRateLimitPerMinute: Number(env.AUTH_RATE_LIMIT_PER_MIN || 20),
    maxBodyBytes: Number(env.MAX_BODY_BYTES || 12 * 1024 * 1024),
    publicKeyRefreshMs: Number(env.PUBLIC_KEY_REFRESH_MS || 10 * 60 * 1000),
  };
  if (!cfg.gatewaySecret || cfg.gatewaySecret.length < 32) {
    throw new Error("ZT_GATEWAY_SECRET must be set (>= 32 chars) - shared only between gateway and backend");
  }
  return cfg;
}
