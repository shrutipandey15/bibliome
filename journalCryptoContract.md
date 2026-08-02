# Journal crypto contract

*The irreversible decision, written down before the code. VISION §6 locked the
shape; this is the wire- and schema-level commitment that implements it.*

The one sentence: **the server stores ciphertext and wrapped key material, and
holds nothing it can decrypt.** Not "we promise not to look" — we cannot look.

---

## 1. Key hierarchy

```
password ──KDF(salt_p, params)──▶ password key ──wrap──▶ ┐
                                                          ├─▶ data key (DEK, random 256-bit)
recovery code ──KDF(salt_r, params)──▶ recovery key ──wrap──▶ ┘
                                                                    │
                                                                    ▼
                                              AEAD-encrypts every journal entry
```

- The **DEK** is generated client-side, once, at journal setup. It is random —
  never derived from the password. It never leaves the client in the clear.
- It is wrapped **twice**, independently: once under a key derived from the
  account password, once under a key derived from the recovery code (shown once,
  at setup). Two wrappings of the same DEK, two independent unlock paths.
- Entries are encrypted under the DEK with an AEAD (AES-GCM or
  XChaCha20-Poly1305), a **fresh nonce per entry version**.

### What the server stores (`journal_key_bundles`)

| column | meaning |
|---|---|
| `cipher` | AEAD used for wrapping *and* entries (`AES-GCM` \| `XChaCha20-Poly1305`) |
| `kdf`, `kdf_params` | `argon2id` \| `pbkdf2-sha256`, plus its cost parameters |
| `password_salt`, `wrapped_dek`, `wrapped_dek_nonce` | the password-wrapped DEK |
| `recovery_salt`, `wrapped_dek_recovery`, `wrapped_dek_recovery_nonce` | the recovery-code-wrapped DEK |
| `key_version` | bumped on DEK rotation; entries carry the version they were sealed under |
| `password_wrap_stale` | true when the account password changed without a re-wrap |

Everything in that table is inert without a secret the server never receives.
There is no server-side copy of the DEK, no escrow, no "support can recover it."

### What the server never sees

The password (only a bcrypt hash), the recovery code (not even a hash — the
server has no reason to verify it; the AEAD tag does that on the client), the
DEK, and any plaintext prose.

---

## 2. Storage split — the deliberate line

| | encrypted | why |
|---|---|---|
| entry prose | **yes** (`journal_entries.ciphertext`) | it's the incriminating part, and nothing server-side needs it |
| entry date | no | required to order and page "one continuous book" |
| emotion tags + strength | **no** (`journal_emotions`) | the only thing DNA needs, and not incriminating |

Prose encrypted, tags readable. That split is the whole design: it buys the
strongest privacy claim available while keeping the feature that justifies the
journal living inside Bibliome at all (shared emotional vocabulary → shared DNA).

Tags reuse the canonical 18-slug vocabulary and the same 1–10 per-emotion
strength model as book entries (`app/utils/emotions.py`). No second vocabulary.

---

## 3. Server-side validation boundary

The API validates **structure, never content**:

- base64 decodability and byte-length bounds on every ciphertext/nonce/salt field
- `cipher` / `kdf` against an allowlist
- `entry_date` is a date; tags are canonical slugs with strength 1–10

It cannot validate that the ciphertext decrypts, that it is well-formed AEAD, or
that it says anything at all. A client that sends garbage gets it back verbatim.

**Ciphertext is never logged.** Not at debug level, not in error handlers.

---

## 4. Search

There is no journal search endpoint, and there never will be one. Server-side
search over blobs the server cannot read is not a missing feature — it is
arithmetically impossible. Search is client-side, after decryption, over the
pages the client already holds. `GET /journal/entries` deliberately has no `q`
parameter (contrast `GET /entries`, which does).

---

## 5. Lifecycle

### Setup
`POST /journal/key` once, with both wrappings. `409` if a bundle already exists —
overwriting a bundle destroys the journal, so it is never an accident.

### Password change (`POST /user/change-password`)
The client re-wraps the DEK under the new password and sends the new bundle with
the same request. The server updates the password hash and the bundle in **one
transaction** — the server cannot do the re-wrap itself, so this is the only way
to avoid a window where the stored wrap doesn't match the password.

If the client omits the bundle, the password still changes and the server sets
`password_wrap_stale = true`. Honest bookkeeping: the password path is now dead,
the recovery path still works, and `GET /journal/key` says so.

### Password reset (`POST /auth/reset-password`)
The reset token proves control of the email, not knowledge of the old password —
so the old password-wrapped DEK becomes permanently unusable. The server marks
`password_wrap_stale = true` and the response says plainly:

```json
{"journal": {"locked": true, "recoverable_with_recovery_code": true,
             "message": "Your journal is encrypted with your old password. Only your recovery code can unlock it now."}}
```

**Without the recovery code the journal is gone.** Not degraded, not
recoverable-by-support — gone. We say so at reset time rather than letting the
user discover it later. That cost is also the proof the encryption is real.

### Recovery / re-wrap (`PUT /journal/key`)
The client unwraps the DEK with the recovery code, re-wraps under the current
password, and PUTs the new bundle — authenticated by the current account
password so a hijacked session alone cannot overwrite it. Clears
`password_wrap_stale`.

---

## 6. What this does not protect against

Stated plainly, because a privacy claim with unlisted gaps is marketing:

- **A malicious or compromised server can serve malicious client code.** E2E
  encryption in a web client trusts the delivered JavaScript. This defends
  against database dumps, backups, logs, subpoenas, and us — not against a
  hostile build of the frontend.
- **Metadata is visible**: which days you wrote, how much you wrote (ciphertext
  length), and every emotion tag. By design for tags; an accepted leak for the
  rest.
- **A forgotten password *and* lost recovery code is terminal.** There is no
  third path. This is the intended trade.
