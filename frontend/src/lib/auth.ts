// Authentication & Brute-force protection helper

export function getAuthConfig() {
  const password =
    process.env.AK5_AUTH_PASSWORD ||
    process.env.AK5_WEB_PASSWORD ||
    "";
  const username =
    process.env.AK5_AUTH_USERNAME ||
    process.env.AK5_WEB_USER ||
    "admin";

  const isEnabled = Boolean(password && password.trim().length > 0);

  return {
    isEnabled,
    expectedUsername: username.trim(),
    expectedPassword: password.trim(),
  };
}

// In-memory rate limiting map for brute force prevention (per IP)
interface AttemptRecord {
  count: number;
  firstAttempt: number;
  blockedUntil: number;
}

const attemptsMap = new Map<string, AttemptRecord>();
const MAX_ATTEMPTS = 5;
const WINDOW_MS = 5 * 60 * 1000; // 5 minutes
const BLOCK_DURATION_MS = 10 * 60 * 1000; // 10 minutes block on breach

export function checkRateLimit(ip: string): { allowed: boolean; waitSeconds?: number } {
  const now = Date.now();
  const record = attemptsMap.get(ip);

  if (!record) {
    return { allowed: true };
  }

  if (record.blockedUntil > now) {
    const waitSeconds = Math.ceil((record.blockedUntil - now) / 1000);
    return { allowed: false, waitSeconds };
  }

  // If window expired, reset
  if (now - record.firstAttempt > WINDOW_MS) {
    attemptsMap.delete(ip);
    return { allowed: true };
  }

  if (record.count >= MAX_ATTEMPTS) {
    record.blockedUntil = now + BLOCK_DURATION_MS;
    return { allowed: false, waitSeconds: Math.ceil(BLOCK_DURATION_MS / 1000) };
  }

  return { allowed: true };
}

export function recordFailedAttempt(ip: string): void {
  const now = Date.now();
  const record = attemptsMap.get(ip);

  if (!record) {
    attemptsMap.set(ip, {
      count: 1,
      firstAttempt: now,
      blockedUntil: 0,
    });
  } else {
    record.count += 1;
    if (record.count >= MAX_ATTEMPTS) {
      record.blockedUntil = now + BLOCK_DURATION_MS;
    }
  }
}

export function recordSuccessfulAttempt(ip: string): void {
  attemptsMap.delete(ip);
}

// Generate simple hash for session cookie verification
export async function computeSessionHash(username: string, pass: string): Promise<string> {
  const data = new TextEncoder().encode(`ak5_salt_${username}_${pass}_2026`);
  const buffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(buffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function verifySessionCookie(cookieValue: string | undefined): Promise<boolean> {
  const { isEnabled, expectedUsername, expectedPassword } = getAuthConfig();
  if (!isEnabled) return true; // Auth disabled
  if (!cookieValue) return false;

  const expectedHash = await computeSessionHash(expectedUsername, expectedPassword);
  return cookieValue === expectedHash;
}
