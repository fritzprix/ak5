/** WebAuthn helpers for passkey registration / user-presence (#28, identity.md §6.1).
 *
 * Pure conversions between the server's JSON (base64url strings, py-webauthn
 * `options_to_json`) and the browser's `navigator.credentials` types.
 */

export const USER_PRESENCE_REQUIRED = "USER_PRESENCE_REQUIRED";

export function b64urlToBuffer(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  const bin = atob(padded + pad);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

export function bufferToB64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

interface DescriptorJSON {
  id: string;
  type: "public-key";
  transports?: AuthenticatorTransport[];
}

export interface CreationOptionsJSON {
  challenge: string;
  rp: { id?: string; name: string };
  user: { id: string; name: string; displayName: string };
  pubKeyCredParams: PublicKeyCredentialParameters[];
  timeout?: number;
  excludeCredentials?: DescriptorJSON[];
  authenticatorSelection?: AuthenticatorSelectionCriteria;
  attestation?: AttestationConveyancePreference;
}

export interface RequestOptionsJSON {
  challenge: string;
  rpId?: string;
  timeout?: number;
  allowCredentials?: DescriptorJSON[];
  userVerification?: UserVerificationRequirement;
}

function toDescriptor(d: DescriptorJSON): PublicKeyCredentialDescriptor {
  return { type: d.type, id: b64urlToBuffer(d.id), transports: d.transports };
}

export function toCreationOptions(json: CreationOptionsJSON): PublicKeyCredentialCreationOptions {
  return {
    ...json,
    challenge: b64urlToBuffer(json.challenge),
    user: { ...json.user, id: b64urlToBuffer(json.user.id) },
    excludeCredentials: (json.excludeCredentials ?? []).map(toDescriptor),
  };
}

export function toRequestOptions(json: RequestOptionsJSON): PublicKeyCredentialRequestOptions {
  return {
    ...json,
    challenge: b64urlToBuffer(json.challenge),
    allowCredentials: (json.allowCredentials ?? []).map(toDescriptor),
  };
}

export function registrationToJSON(cred: PublicKeyCredential): Record<string, unknown> {
  const res = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufferToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToB64url(res.clientDataJSON),
      attestationObject: bufferToB64url(res.attestationObject),
      transports: typeof res.getTransports === "function" ? res.getTransports() : undefined,
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  };
}

export function assertionToJSON(cred: PublicKeyCredential): Record<string, unknown> {
  const res = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufferToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToB64url(res.clientDataJSON),
      authenticatorData: bufferToB64url(res.authenticatorData),
      signature: bufferToB64url(res.signature),
      userHandle: res.userHandle ? bufferToB64url(res.userHandle) : undefined,
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  };
}

/** True when ``message`` is an API error with the `USER_PRESENCE_REQUIRED` code (readErrorDetail format). */
export function isPresenceRequiredMessage(message: string): boolean {
  return message.startsWith(`${USER_PRESENCE_REQUIRED}:`) || message === USER_PRESENCE_REQUIRED;
}

export function passkeysSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.credentials
  );
}

/** Human-friendly text for the errors `navigator.credentials` throws. */
export function describeWebAuthnError(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError") return "Passkey request was cancelled or timed out.";
    if (err.name === "InvalidStateError") return "This authenticator is already registered.";
    if (err.name === "SecurityError") {
      return "Passkeys need HTTPS or localhost, and the site origin must match the server's AK5_PUBLIC_URL / AK5_WEBAUTHN_ORIGIN.";
    }
  }
  return err instanceof Error ? err.message : "Passkey operation failed.";
}
