# Bibliome — Product & Engineering Blueprint

*The strategic and technical plan for turning a personal reading journal into a calm, thoughtful reading community. No implementation code — this is the map, not the territory.*

Companion to `audit.md`. Read that first: its P0 findings are literally Phase 1 of this plan.

---

## 0. Founder's read — where I'm challenging the brief

You hired me to disagree, so here is the disagreement, concentrated. The rest of the document assumes we've resolved these.

### 0.1 The core tension: calm vs. the social machine
Your stated soul is *peaceful, thoughtful, anti-dopamine, low social comparison.* Your requested features (feed, matching, chat, notifications) are the standard apparatus of engagement-maximizing social apps. This is not fatal, but it forces a rule that must hold across every screen:

> **We compete on depth, never on volume. No metric that ranks humans against each other is ever shown.**

Concretely that means: no public follower/friend counts, no karma score on display, no "streaks" as pressure, reaction *counts* de-emphasized or hidden, feeds that **end**, and zero re-engagement guilt ("we miss you!"). If you don't accept this constraint, stop here — because otherwise we'll build a mediocre clone of things that already exist, minus their scale.

### 0.2 "Anonymous by default" undermines your anti-toxicity goal — use pseudonymity instead
True anonymity (no stable attribution) is the single biggest driver of online toxicity because it removes accountability, reputation, and meaningful blocking. It fights your own goal. What you *actually* want — privacy, no real-name exposure — is better served by:

> **Pseudonymous by default. One durable handle per account. Real identity is never collected, so it can never leak.** "Revealing your identity" is not a platform feature; it's users voluntarily sharing off-platform contact in conversation, which we neither facilitate nor store specially.

This is the Reddit model done cleanly, and it's *stronger* privacy than "hide the real name we're holding," because we hold nothing. It also gives us the accountability primitives (block a handle, throttle a handle, reputation behind the scenes) that make moderation tractable for a small team. I've built the entire identity layer on this. See §2.

### 0.3 Chat is sequenced too early and scoped too open
Anonymous 1:1 chat on a book app is a grooming/harassment surface. Your likely audience includes minors (YA/romance/fanfic readers skew young). A solo or small team running open anonymous DMs inherits: CSAM detection duties, harassment response SLAs, and real legal exposure. My recommendation:

> **Do not build open DMs. Build a constrained "Twin Conversation": text-only, unlocked only after mutual Twin consent, rate-limited, with block/report as first-class citizens, delayed to Phase 4.** Treat "should this become open chat?" as a decision requiring a dedicated trust-&-safety plan, not a feature toggle.

I designed the full system in §5 as you asked, but with this recommendation attached to every part.

### 0.4 Twin + private chat = an accidental dating app
Match-by-compatibility + private conversation reproduces dating-app dynamics no matter your intent. Guardrails baked into §4/§5: no photos ever, no "browse profiles" swipe deck, twins surfaced *periodically* (not on demand), matches always *explained*, and framing that is explicitly about co-reading, not connection-for-its-own-sake. If usage starts looking like dating, that's a regression to fix, not a growth signal to chase.

### 0.5 What I'd cut, merge, or delay
- **Merge** the current three "room" APIs and the two `RoomResponse` schemas into one. (Audit P3-2.)
- **Delay** the Reading Room decoration/gamification system. It's charming but it's dopamine mechanics wearing a cozy sweater, and it's unfinished. Revisit only after the core loop retains.
- **Cut** visible "achievements" unless they describe *reading substance* (e.g., "read across 9 emotional registers this year"), never activity volume ("logged 100 books!"). §3.
- **Delay** Chat to Phase 4, gated as above.
- **Rebuild, don't extend, Echo.** You already said this; I agree. The current version is a leaky public firehose with privacy bugs (audit P1-3).

### 0.6 Recommended v1 social scope (my actual advice)
If it were my call: **Phase 1 fixes the foundation. Phase 2 ships the private single-player journal done *excellently* (this is the real product and the real moat). Phase 3 ships Profile + Echo. Twin and Notifications come with them. Chat waits.** Single-player reading tools that respect you are rare; that's where I'd plant the flag before becoming a social network.

---

## 1. Design principles (the constitution every feature obeys)

Each has a reason, because you asked for reasons.

| Principle | Why | How it shows up |
|---|---|---|
| **No comparative metrics in the UI** | Social comparison is a top driver of anxiety and churn; it's antithetical to "peaceful." | No follower/karma/rank counts. Reactions private or hidden. |
| **Feeds end** | Infinite scroll exploits variable-reward loops (cognitive psychology: intermittent reinforcement). | Bounded daily sets, clear "you're caught up." |
| **Friction as a feature where it protects the user** | Small deliberate friction before posting/DMing reduces impulsive, low-quality, or abusive actions (HCI: friction curbs regretful actions). | Compose step asks "what are you actually saying?"; cool-downs on new accounts. |
| **Recognition over recall** | Cognitive load drops when the UI shows options rather than making users remember them (Nielsen). | Emotion/genre pickers, not free-text taxonomies. |
| **One primary action per screen** | Reduces decision cost, speeds the core loop. | Each view has a single obvious next step. |
| **Progressive disclosure** | Beginners see little; depth is available on demand. Manages complexity without dumbing down. | Advanced analytics behind a tap, not on the dashboard. |
| **Explainability builds trust** | People trust systems whose reasoning they can see (UX research on algorithmic trust). | Twin matches and feed ranking always say *why*. |
| **No dark patterns, ever** | Trust is the product. | No guilt notifications, no fake scarcity, no confirm-shaming, easy account deletion. |
| **Honest empty/error states** | The current app fabricates a "reading streak" and swallows errors (audit P2-10, P5-2). Lying erodes the one thing we sell. | Show real data or say "not enough yet." Distinguish error from emptiness. |
| **Accessible by default** | ~15–20% of users have a relevant disability; also it's just correct. | WCAG 2.2 AA target, never color-only meaning, focus-trapped modals, `aria-live` for async. |

---

## 2. Identity & privacy architecture (the keystone — read before any feature)

Everything social depends on this. Get it wrong and every downstream feature is wrong.

### 2.1 The model
- **One account = one pseudonymous `handle`.** The handle is the *only* public identifier, everywhere: Echo posts, Profile, Twin, Chat. It is chosen at signup, shown consistently, and changeable rarely (rate-limited, with a short "was previously known as" grace to prevent impersonation whiplash).
- **Email is auth-only.** Never displayed, never searchable, never exposed in any API response body to other users. (Audit P1-3 flagged current enumeration leaks — fixed here structurally.)
- **We do not collect real names.** There is no legal-name field. "Real identity" simply does not exist in our schema, so §5's "reveal identity" is reframed as *users choosing to share their own off-platform contact in a conversation* — a message like any other, which we do not treat specially or persist as structured identity.
- **Pseudonymity ≠ anonymity.** The handle is durable and accountable: it can be blocked, throttled, and (privately, server-side) reputationally scored. Users are anonymous to each other and to the world, but not unaccountable to the system. This is the distinction that makes moderation possible without surveillance.

### 2.2 Why this beats "anonymous by default"
Fully anonymous posting gives you 4chan. Pseudonymity gives you Reddit. Your goals (thoughtful, low-toxicity) require the accountability that only a durable handle provides — while your privacy goal is fully satisfied because the handle reveals nothing real.

### 2.3 Visibility tiers (replaces the current tangled `is_public` / `share_token` / strict-public mess)
A single `profile_visibility` enum per account:
- `private` — only you. Default for new accounts.
- `community` — visible to signed-in members at your handle. No search-engine indexing.
- `public` — indexable, shareable link.

Plus per-object overrides where it matters (an individual review can be `private` even on a `public` profile). Share tokens become **revocable, optionally-expiring capability links**, not a second visibility system. (Audit P1-8, P3-2.)

### 2.4 Cross-cutting security posture (applies to every feature below)
- Refresh tokens **hashed at rest**, rotated, and **revoked on password change/reset** (audit P1-1). Access token in memory; refresh token in an httpOnly, Secure, SameSite cookie — not `localStorage`.
- Every rate limiter and lockout is **awaited** and **shared across workers via Redis** (audit P0-2, P1-4). Client IP derived from a **trusted** proxy header only (audit P1-5).
- All user-authored text is treated as untrusted on render (no `dangerouslySetInnerHTML`), and sanitized/length-capped on write.
- No user-controlled URL is ever fetched server-side without an allowlist + scheme check + size cap + no-redirect policy (audit P0-3).

---

## FEATURE 1 — ECHO (redesigned from zero)

### Purpose
A calm, pseudonymous space to share and discuss *one specific reading feeling or reaction*, anchored to a book or an emotion — not a general status feed. The structural anchoring is the anti-toxicity mechanism: you can't post "hot takes about strangers," only reflections about reading.

### The one structural decision that defines Echo
**An Echo is always attached to a book and/or a canonical emotion.** There is no freeform posting. This keeps Echo on-topic, makes discovery coherent, and makes moderation tractable (context is always known). It also means Echo grows the book graph for free.

### User journey
1. **Trigger.** After finishing/logging a book, or from a book's page, or from a daily *Prompt* ("A book that made you feel *longing* this month?").
2. **Compose.** A focused sheet: the book (prefilled if from a book), up to 2 emotions, and the reflection text (cap ~500 chars — long enough for thought, short enough to prevent monologue). A deliberate friction line: *"Say the true thing, not the clever thing."*
3. **Consent to visibility.** Post to `community` (default) or `public`. Never to "followers" — there are no followers.
4. **Publish.** Optimistic insert; server validates and de-dupes.
5. **Return.** Later, a batched notification: *"3 readers responded to your echo about* Piranesi*."* (Not "3 likes.")

### Feed structure
Three tabs, each **bounded**:
- **For You** — a *small daily set* (e.g., 15–25) ranked by reading affinity, not popularity. Ends with "You're caught up."
- **A Book** — all echoes on one book, chronological. This is the reading-club surface.
- **A Feeling** — all echoes tagged with one emotion this week. This is the serendipity surface.

No infinite scroll. No global "trending" that manufactures virality.

### Posting flow specifics (anti-dopamine)
- **Reactions are private by default.** You can react (a small set: *"felt this," "changed my mind," "adding to my list"* — note: no generic "like," no downvote-as-dislike). The author sees an aggregate; the public sees nothing. This removes the scoreboard.
- **Replies before reactions.** On opening an echo you see replies first; reaction affordances are secondary. Conversation is the point.
- **New-account cool-down.** Accounts younger than N hours / M logged books can read and react but post at a reduced rate — kills spam/throwaway abuse.

### Discovery
- Anchored (book, emotion) — deterministic and safe.
- Affinity ranking for "For You" (see algorithm).
- Explicit "why am I seeing this": *"Because you also read literary fiction and tag* grief *often."*
- **No people-search.** You cannot browse users. You encounter handles only through their echoes. This is a deliberate anti-stalking, anti-comparison choice.

### Ranking algorithm (For You)
A transparent, popularity-suppressed score per candidate echo:

```
score =
    w1 * reading_affinity(viewer, echo)      // cosine sim of emotion/genre vectors — you already compute these
  + w2 * response_quality(echo)              // replies & saves, NOT reactions; log-damped
  + w3 * freshness(echo)                     // gentle time decay, ~72h half-life
  + w4 * diversity_bonus(echo, session)      // penalize repeat authors/books in one session
  - p1 * report_pressure(echo)               // auto-suppress while under review
```
- **Raw reaction counts are deliberately absent** from ranking — that's the lever that creates dopamine feeds.
- Precomputed candidate set per user (nightly + on-write invalidation), ranked at request time. Cap author frequency so no one dominates a feed.
- Every ranked item stores its top contributing factor for the "why" string.

### Moderation
- **Pre-publish**: length caps, rate limits, a lightweight text classifier for slurs/threats/self-harm/PII → hold-for-review or soft-block with explanation.
- **Post-publish**: report → immediate ranking suppression above a threshold → human queue. Repeated confirmed violations throttle then suspend the *handle*.
- **Context always known** (book/emotion) makes review fast.
- **Self-harm content** routes to a supportive interstitial with resources rather than a punitive block. (This is both ethical and required care.)
- Small-team reality: build the **report→auto-throttle** loop first so the system protects itself before you can staff review.

### Privacy
- Echoes inherit account visibility but can be individually downgraded.
- No location, no device info, no real identity — nothing to leak.
- `public` echoes are indexable; `community` are not (noindex + auth-gated).

### Reporting & blocking
- **Report**: categorized (harassment, hate, sexual content involving minors → priority path, spam, self-harm, PII, other). One tap, no free-text required (reduces friction to report).
- **Block**: blocking a handle removes their echoes and replies from your entire experience bidirectionally, silently (they aren't told — telling invites retaliation). Blocks are permanent until lifted and cross-surface (Echo, Twin, Chat).
- **Mute** (softer): hide without severing, for lower-stakes avoidance.

### UI/UX decisions
- Echo cards are typographic and quiet: book title, up to 2 emotion glyphs (with text labels — never color-only, audit P2-9), the reflection, then replies. No avatars (there are no photos), just handles.
- Emotion labels come from **one shared vocabulary source** — the current backend/frontend divergence (audit P2-9) is fixed by a single source of truth consumed by both.
- Compose is a bottom sheet on mobile, centered modal on desktop, both focus-trapped, `Esc`-closable.

### Components
`EchoComposer`, `EchoCard`, `EchoThread`, `ReplyComposer`, `FeedTabs`, `PromptOfTheDay`, `ReportSheet`, `BlockMuteMenu`, `WhyThisChip`, `EmptyState`, `CaughtUpState`.

### Backend requirements
Echo service (CRUD + visibility), ranking service (candidate gen + scoring + "why"), reply service, reaction service (private aggregates), moderation service (classifier + report queue + auto-throttle), block/mute service (cross-surface).

### Database changes
- `echoes` (id, author_id, book_id nullable, primary_emotion, secondary_emotion nullable, body, visibility, status[active/held/removed], created_at, edited_at). Indexes on (book_id), (primary_emotion, created_at), (author_id).
- `echo_replies` (id, echo_id, author_id, body, status, created_at).
- `echo_reactions` (echo_id, user_id, kind) — unique (echo_id, user_id, kind); counts are private.
- `reports` (id, reporter_id, target_type, target_id, category, status, created_at, resolved_by, resolution).
- `blocks` (blocker_id, blocked_id, created_at) unique; `mutes` likewise.
- `echo_feed_candidates` (user_id, echo_id, score, top_factor, generated_at) — materialized ranking cache.
- Drop the old public-echo-on-`book_entries` design; migrate existing `public_echo` text into `echoes` where non-empty.

### API endpoints
`POST /echoes`, `GET /echoes/feed?tab=`, `GET /echoes/book/{id}`, `GET /echoes/emotion/{slug}`, `GET /echoes/{id}`, `PATCH /echoes/{id}`, `DELETE /echoes/{id}`, `POST /echoes/{id}/replies`, `POST /echoes/{id}/reactions` / `DELETE`, `POST /reports`, `POST /blocks` / `DELETE`, `POST /mutes` / `DELETE`, `GET /prompts/today`. All authed; feed endpoints rate-limited; reports rate-limited to prevent report-bombing.

### Edge cases
- Book deleted/merged after echo → keep echo, relink or show "book removed."
- Blocked user replies to your echo → hidden from you, visible to others (unless removed).
- Report-bombing a legit post → require multiple distinct reporters + reporter-reputation weighting before auto-suppress.
- Editing an echo after replies exist → mark "edited," keep replies.
- Emotion vocabulary changes → canonicalize on read (audit P2-12).

### Security considerations
Untrusted-text rendering, PII classifier before publish, no SSRF (no server fetch of user URLs), report queue authz (only mods see it), block enforcement server-side (never client-only).

### Performance considerations
Feed served from precomputed candidates (no per-request N+1). Reaction aggregates denormalized. Book/emotion feeds paginated with keyset cursors done correctly (audit P2-2). Classifier runs async where possible.

### Accessibility
Full keyboard nav, focus-trapped composer, emotion meaning in text + glyph, `aria-live` on publish/reply success, reduced-motion honored, contrast AA.

### Complexity
**Large** (ranking + moderation are the weight). The MVP subset — anchored echoes, book/emotion feeds, replies, report→throttle, block — is **Medium**; the "For You" ranking and classifier push it to Large.

---

## FEATURE 2 — PROFILE (redesigned)

### Purpose
Communicate a *reader's identity through their reading*, not through metrics. A profile should feel like walking into someone's study, not reading their résumé.

### Information hierarchy (top to bottom = most to least identity-defining)
1. **Identity strip** — handle, one-line self-description (optional, ~80 chars), reading "disposition" derived from data (the DNA type, stated in plain language: *"reads for catharsis; drawn to grief and awe"*). No counts here.
2. **Now** — currently reading + optionally a recent reflection. The single most human, most returned-to element.
3. **Emotional signature** — the DNA visualization (once the engine is fixed — audit P0-1). This *is* the vanity-free "personality" metric: it describes *how* they read, not *how much*.
4. **Collections** — user-curated shelves ("books that rearranged me," "comfort re-reads"). Curation is self-expression; this is where personality lives. Ordered by the user.
5. **Reviews / reflections** — their public echoes and reviews, reverse-chronological, filterable by emotion.
6. **Reading history** — the full logged library, browsable, *not* framed as a leaderboard number. Emphasis on range and pattern, not tally.
7. **Meaningful milestones** (only if honest) — "read across all 13 emotional registers," "a year of consistent reflection." Never "100 books!" (volume) or streak pressure.

### What we deliberately omit
Follower counts, "profile views," karma, streak flames, "X% more than other readers." All are comparison vanity. (Your explicit ask; I strongly agree.)

### User flow
View own (editable inline) vs. others' (respects visibility + blocks). Empty profiles get a gentle "start with one book," never a shaming void.

### UI/UX decisions
Editorial, print-inspired layout (matches existing aesthetic). Collections are drag-orderable. Everything below the identity strip is progressively disclosed — the profile opens calm, expands on intent. Others' profiles never show anything the viewer's block/visibility rules forbid, enforced server-side.

### Components
`ProfileHeader`, `NowReading`, `EmotionalSignature` (shared with DNA view), `CollectionGrid`, `CollectionEditor`, `ReviewList` (shared with Echo), `LibraryBrowser`, `MilestoneRow`, `VisibilityBadge`, `EmptyProfile`.

### Backend requirements
Profile aggregation service (composes from entries/echoes/collections/DNA respecting visibility+blocks), collection CRUD, milestone computation (nightly, from real data only).

### Database changes
- `collections` (id, user_id, title, description, position, visibility, created_at).
- `collection_items` (collection_id, entry_id, position) unique.
- `profiles` extension: `bio` (short, sanitized), `profile_visibility` enum (§2.3).
- `milestones` (user_id, kind, achieved_at) — only substance-based kinds.

### API endpoints
`GET /profile/{handle}` (visibility+block aware), `GET /me/profile`, `PATCH /me/profile`, `POST /collections`, `PATCH /collections/{id}`, `DELETE`, `POST /collections/{id}/items`, `PATCH /collections/{id}/reorder`.

### Edge cases
Private profile viewed by stranger → minimal card, no data leak (audit P1-3). Blocked viewer → profile appears not to exist. Handle change → old links resolve via "previously known as" for a grace window. Book in a public collection later marked private review → collection shows the book, hides the review.

### Security
Server-side visibility+block enforcement on every field. No email/real data anywhere in payloads. Bio sanitized and length-capped.

### Performance
Profile is a composed read — cache per (viewer-visibility-class, handle) with invalidation on write. Library browse is keyset-paginated. Milestones precomputed.

### Accessibility
Landmark regions, heading order, keyboard-reorderable collections (not drag-only), alt text conventions for any book cover, AA contrast.

### Complexity
**Medium.**

---

## FEATURE 4 — READING TWIN (placed before Chat intentionally)

*(Numbered per your brief; sequenced before Chat because Chat depends on Twin consent.)*

### Purpose
Periodically introduce a reader to 1–3 others whose *reading interior* resembles theirs, with a transparent explanation, to enable rare, meaningful connection over books — not a match-browsing utility.

### Guardrails that keep it from becoming a dating app
- **No photos, ever.** No physical anything.
- **No swipe deck / no on-demand browsing.** Twins are *surfaced periodically* (e.g., a weekly "someone reads like you"), which lowers gamification and objectification.
- **Every match is explained** in reading terms.
- **Framing is co-reading**, not connection: the CTA is "compare shelves" / "read something together," not "message them."
- **Mutual consent required** before any private interaction (see Chat).

### Matching algorithm (behavior, never demographics)
Composite normalized vector per user, cosine similarity, precomputed nightly:
- **Emotional signature** — the emotion-frequency/intensity vectors you already compute (reuse `cached_dna_profile`; don't recompute per request — audit P2-3).
- **Genre distribution** — from book metadata.
- **Intensity profile** — do they read gently or for devastation?
- **Review sentiment / style** — length, sentiment, vocabulary richness (lightweight, privacy-preserving features — not content storage for profiling).
- **Co-read overlap** — Jaccard on libraries (shared obscure books weight higher than shared bestsellers — TF-IDF-style rarity weighting; loving the same *weird* book is more diagnostic than both having read a megahit).
- **Temporal rhythm** — cadence compatibility (a soft factor).

`similarity = cosine(composite_A, composite_B)`, filtered by mutual visibility, no active block, and a minimum-data threshold (≥ some books/echoes) so matches are grounded.

### Why users would trust matches (this is the crux)
1. **Explainability** — "You matched because you both rate grief-heavy literary fiction highly and both loved *[obscure shared book]*." Trust follows visible reasoning.
2. **No demographics** — nothing creepy; it's demonstrably about reading.
3. **Un-gameable** — based on accumulated genuine behavior, not a fillable form.
4. **Low pressure** — periodic, consented, no scoreboard — signals the system isn't trying to hook them.

### User experience
Weekly (opt-in) "A reader like you" card → view *their* explanation + public shelves/echoes (respecting their visibility) → optionally send **one** structured "connection request" (a reading-based opener, not free text). If both consent, a Twin Conversation unlocks (§5). No consent = nothing happens, no notification of rejection (avoids sting + retaliation).

### Privacy
Opt-in entirely (`twin_enabled`). You are only ever shown to people you'd also be allowed to see. Blocks are absolute. You can disable and be removed from candidate pools immediately.

### Backend architecture
Nightly batch: build/refresh composite vectors → ANN/similarity computation (start simple: periodic pairwise within cohorts; scale later to an approximate-nearest-neighbor index) → store top candidates per user with explanation factors. Request path only *reads* precomputed candidates. Connection requests + consent state machine.

### Database changes
- `reader_vectors` (user_id, composite JSONB or typed columns, updated_at).
- `twin_candidates` (user_id, candidate_id, similarity, factors JSONB, generated_at) — top-K per user.
- `twin_requests` (id, from_id, to_id, opener, status[pending/accepted/declined/expired], created_at) unique active pair.
- `user_prefs`: `twin_enabled` bool.

### API endpoints
`GET /twins/suggestions`, `POST /twins/requests`, `POST /twins/requests/{id}/accept` / `decline`, `GET /twins/active`, `PATCH /me/prefs/twin`.

### Edge cases
Too little data → "keep reading; we'll find your readers when we know you better" (honest). Both delete accounts → cascade. Gaming attempts (mirroring someone's shelf) → rarity-weighting + behavioral accrual resist it. Someone spamming requests → rate-limited + reputation-gated.

### Security
No vector leakage to clients beyond the human explanation. Consent enforced server-side. Requests rate-limited and block-aware.

### Performance
All heavy compute is offline/batch. Reuse existing DNA vectors. Request path is O(read top-K). This directly fixes the current O(all users × entries) twin endpoint (audit P2-3).

### Accessibility
Explanations in text, no meaning conveyed by color/graph alone, keyboard-navigable, no time-pressured UI.

### Complexity
**Large** (batch pipeline + trust surface). A cohort-based MVP reusing DNA vectors is **Medium**.

---

## FEATURE 3 — CHAT / TWIN CONVERSATION (designed, but I recommend delaying)

### My recommendation restated
Do **not** ship open anonymous DMs. Ship a **constrained Twin Conversation** in Phase 4, and only after a written trust-&-safety plan exists. Everything below assumes that constrained form.

### Purpose
Let two mutually-consented Twins have a private, text-only, reading-focused conversation — the rare payoff of a good match, not a general messaging system.

### Chat flow
Unlocks *only* after mutual Twin consent (§4). Opens with the shared reading context ("you both loved X") to seed non-creepy conversation. Text only. Rate-limited, especially early. Either party can leave/block at any time; leaving ends the conversation for both.

### Permissions
No conversation exists without mutual consent. No one can initiate chat from a profile or an echo — the *only* path is Twin acceptance. This single rule eliminates the entire "stranger DMs you" abuse class.

### Identity reveal (reframed per §2)
There is no platform "reveal identity" button, because we hold no real identity. If users choose to share off-platform contact, that's ordinary message content and their own decision. We add one safety affordance: a **gentle interstitial** the first time message content looks like contact-sharing — *"Sharing personal contact is your choice. There's no way to un-send it."* — no blocking, just informed consent.

### Blocking / reporting / moderation
- Block = immediate, permanent, cross-surface, silent. Ends the conversation.
- Report a message/conversation → queue with the message context.
- **Abuse prevention:** text-only (no images → removes the CSAM-image vector, a major reason to stay text-only), new-account and new-conversation rate limits, per-conversation report shortcut, server-side profanity/threat/grooming-pattern classifier flagging for review, message retention sufficient for investigation but with a user-facing deletion story.
- **Minors:** if you ever detect/segment minor accounts, they should not be in adult Twin pools, full stop. This is a policy decision to make *before* launch, not after an incident.

### Privacy model
Conversations visible only to the two participants (and, on valid report, moderators — disclosed in policy). No conversation metadata leaks to others. No "last seen," no read receipts by default (they manufacture pressure and enable stalking-lite).

### Database architecture
- `conversations` (id, created_at, status, twin_request_id).
- `conversation_participants` (conversation_id, user_id, joined_at, left_at) — exactly two.
- `messages` (id, conversation_id, sender_id, body, status[active/removed], created_at) indexed (conversation_id, created_at).
- Reuse global `blocks`, `reports`.
- Retention/deletion policy fields as required by your jurisdiction.

### API endpoints
`GET /conversations`, `GET /conversations/{id}/messages` (keyset paginated), `POST /conversations/{id}/messages` (rate-limited), `POST /conversations/{id}/leave`, plus shared block/report. Realtime via WebSocket **later**; start with simple polling to avoid standing up realtime infra before the feature proves out.

### Edge cases
One party blocks mid-chat → conversation frozen, both informed neutrally. Account deleted → messages tombstoned. Classifier false positive → human review, don't hard-block private speech automatically. Reveal-then-regret → we can't un-send, and we say so up front.

### Security
Participant-only authz on every message read/write (server-side, never trust client). Rate limits shared across workers. Classifier on ingest. No SSRF, no images, no link unfurling (link preview = server fetch = SSRF + tracking risk).

### Performance
Keyset pagination; polling before WebSockets; conversations are tiny (2 people) so this scales trivially until volume justifies realtime.

### Accessibility
Standard messaging a11y: labeled inputs, keyboard send, screen-reader-friendly message list with author + time, `aria-live` for incoming, reduced-motion.

### Complexity
**Large** — not because messaging is hard, but because *safe* anonymous messaging is. The engineering is Medium; the trust-&-safety is what makes it Large. That gap is exactly why I'm delaying it.

---

## FEATURE 5 — NOTIFICATIONS

### Purpose
Tell users the *few* things they'll be glad to know, in a way that respects attention. A notification system whose success metric is *"users don't disable it,"* not *"click-through."*

### Priority tiers (the priority rules)
- **Tier 0 — Security/account (immediate, non-disableable):** new-device login, password/email change, reset requested. These protect the user; they're never marketing.
- **Tier 1 — Direct & consented (batched, few/day default):** a reply to your echo, a Twin accepted your request, a new Twin Conversation message. Things *you* set in motion or that require your response.
- **Tier 2 — Ambient/community (weekly digest only):** "your reading week," a resurfaced memory, a new prompt that fits you. Never real-time.

### Anti-fatigue rules (each with a reason)
- **Batch by default.** Tier 1 collapses ("3 readers responded to your echoes") rather than pinging per event — reduces interruption count, the actual fatigue driver.
- **No comparative or vanity notifications.** Never "X reacted" as a standalone ping (reactions are private anyway, §1). Never "you're behind."
- **No re-engagement guilt.** No "we miss you," no manufactured FOMO. These are dark patterns you explicitly want to avoid.
- **Quiet hours + user-set cadence.** Default to daytime, user-adjustable, honored per timezone.
- **Every notification is actionable and honest** — it maps to a real thing with a real destination.

### User flow
In-app notification center (source of truth) + optional email/push for Tier 0–1 only, opt-in. Granular per-category toggles. A one-tap "fewer notifications" that a fatigued user reaches for instead of disabling everything.

### Components
`NotificationCenter`, `NotificationItem`, `NotificationPrefs`, `DigestCard`, `QuietHoursPicker`.

### Backend requirements
Event bus → notification service (classify tier, apply prefs + quiet hours + batching window) → deliver (in-app always; email/push per prefs). Digest job (scheduled). Preference service.

### Database changes
- `notifications` (id, user_id, tier, kind, payload JSONB, batch_key, read_at, created_at) indexed (user_id, created_at), (user_id, read_at).
- `notification_prefs` (user_id, per-category channel settings, quiet_hours, timezone).
- `notification_digests` (user_id, period, payload, sent_at).

### API endpoints
`GET /notifications`, `POST /notifications/read` (bulk), `GET /me/notification-prefs`, `PATCH /me/notification-prefs`. Push/email handled server-side via jobs.

### Edge cases
Event for a since-blocked/deleted actor → suppress. Batch window vs. quiet hours interaction → defined precedence (security bypasses quiet hours; nothing else does). Timezone missing → sensible default, ask gently once. Email bounce → disable email channel, keep in-app.

### Security
Tier 0 events must be reliable (security depends on them). No sensitive content in push payloads (they surface on lock screens). Prefs authz per user.

### Performance
Batching reduces write/deliver volume. Digest is a scheduled batch job. In-app center is keyset-paginated. Event processing async.

### Accessibility
`aria-live` for new in-app notifications (polite, not assertive — don't hijack focus), keyboard-navigable center, honors reduced-motion, meaningful not color-only status.

### Complexity
**Medium.** In-app + digest is Medium; adding push/email reliably nudges toward Large.

---

## PRODUCT IMPROVEMENTS (beyond your five features)

Only things that solve real user problems.

**Onboarding & first-run**
- Cold-start is the biggest risk: an empty reading journal is useless. Seed value fast — let users *import* (Goodreads/StoryGraph CSV, or quick-add recent reads) so the DNA/Twin features have data on day one. Without this, every social feature starves.
- First-run should log *one* book beautifully, not tour every feature. One completed action > ten explained ones.

**Reading experience (the actual core loop)**
- Make logging a book genuinely delightful and complete: the current EntryModal omits `notes`, dates, status, and the "finish reflection" the analytics depend on (audit P5-7). Fix the loop before adding surfaces on top of it.
- **Currently-reading check-ins** (the "how's it feeling now?" beat) are already in the backend and unbuilt on the client (audit P3-1) — this is a high-value, low-risk single-player feature that makes the app a daily companion, not a logging chore.
- Ship the **Finish Flow** (the three-beat emotional arc) — it's the product bible's "core interaction" and it's entirely unshipped.

**Search**
- Book search needs a real backing (API key, caching, dedupe — audit P4-5/P4-6) or it 429s and creates duplicate catalog books. Search is load-bearing for logging; it can't be flaky.
- Add *in-library* search/filter (find your own books by emotion, title, author) — trivial value, currently missing.

**Discoverability**
- "A Feeling" browsing (in Echo) is your differentiated discovery: find books via the emotion you're chasing, which no mainstream competitor does well.
- Book pages as first-class destinations (aggregate echoes, emotional consensus, "readers who felt X").

**Navigation & IA**
- Put tab state in the URL and make the back button work (audit P5-4). Deep-linkable everything.
- Collapse the three overlapping "room" APIs and dual schemas (audit P3-2) — internal clarity that prevents user-facing bugs.

**Personalization**
- The emotional signature, once correct, personalizes everything (feed, twins, recommendations) from one honest source. Fix the engine (audit P0-1) and it pays off five times.
- Reading recommendations from emotional adjacency ("you love catharsis-after-dread; try…") — a genuinely novel rec basis.

**Mobile responsiveness**
- Compose/modals as bottom sheets, thumb-reachable primary actions, no hover-only affordances (the spine view currently relies on hover — audit P5-9).
- Offline-tolerant reading of your own library (the cache exists; make it correct and per-account — audit P5-3).

**Retention (the honest kind)**
- Weekly "your reading week" digest (Tier 2) — a calm reason to return that isn't a streak.
- Resurfaced memories ("three months ago, *[book]* wrecked you") — the Mirror service already does this and it's unshipped.
- The retention thesis is *depth and self-knowledge over time*, not habit loops. Lean into it.

**Community health**
- Reporter reputation + report-bombing resistance from day one (small teams get brigaded).
- A written, public, plain-language community policy. Trust requires stated rules.

**Security/perf** — all audit P0/P1 items; they're the price of admission for anything social.

**Accessibility** — treat WCAG 2.2 AA as a definition-of-done checklist item per feature, not a phase. Focus traps, `aria-live`, non-color meaning, keyboard parity.

---

## IMPLEMENTATION ROADMAP

Ordering rule: **security & correctness → the single-player core → identity/social primitives → social features → delight.** Each phase depends on the prior. Rationale after each.

### PHASE 1 — Foundation (make it correct, safe, and honest)
*Nothing social ships on a broken base. This is the audit's P0/P1 turned into work.*

- **Backend:** fix the emotion vocabulary in the DNA engine + unlock checks (P0-1, P0-6); `await` all rate limiters + move shared state to Redis (P0-2, P1-4); validate `cover_url` / kill SSRF (P0-3); add `aiosmtplib` and make email failures loud (P0-4); hash + rotate + revoke refresh tokens, fix error-handler CORS (P1-1, P1-2); fix enumeration + lockout-as-DoS (P1-3); one transaction/session pattern, background work scheduled post-commit (P3-4, P2-1); collapse room APIs/schemas (P3-2).
- **Database:** migration fixing the broken migration filename (P6-2); `profile_visibility` enum + share-token revocation model (§2.3); refresh-token hash column; indexes for keyset pagination.
- **API:** correct keyset pagination contract (P2-2); consistent cache/dirty protocol for DNA/heatmap/stats (P2-5); remove GET-with-side-effects (P2-7).
- **Frontend:** single-flight token refresh (P2-11); honest empty/error states, stop fabricating stats (P2-10, P5-2); per-account cache + proper logout clearing (P5-3); shared emotion vocabulary consumed by client (P2-9/P2-12); URL-driven tabs + working back button (P5-4).
- **Testing:** stand up pytest + vitest + CI (P6-1). First tests: **DNA engine invariants** (every personality emotion ∈ valid slugs — the test that would have caught P0-1), auth/refresh/lockout flows, one API smoke test. This is the safety net for everything after.

*Why first: every later feature reuses the emotion vectors, the auth layer, the pagination, and the session model. Fixing them once here prevents re-fixing them five times, and the tests stop regressions as velocity rises.*

### PHASE 2 — Core Product (the single-player journal, done excellently)
*This is the real moat. A reading tool that respects you is rare; prove that before becoming a network.*

- **Frontend:** complete logging flow (notes, dates, status, finish reflection — P5-7); **Finish Flow** UI (three-beat arc); currently-reading check-ins; in-library search/filter; the corrected emotional-signature visualization; accessible modals (focus trap, `Esc`, `aria-live`).
- **Backend:** wire the already-built finish/checkin/arc/status endpoints (P3-1); recommendation-by-emotional-adjacency (basic); Mirror memories/insights surfaced.
- **Database:** import pipeline tables (Goodreads/StoryGraph CSV); collections tables (used by Profile next).
- **API:** import endpoint(s); in-library search; recommendation endpoint.
- **Testing:** logging/finish/checkin flow tests; import parser tests; engine + stats correctness tests with fixtures.

*Why here: the social features are worthless without rich per-user reading data. Onboarding-by-import + a delightful core loop is what generates the vectors Twin and Echo-ranking need. Ship retention on single-player before adding people.*

### PHASE 3 — Social primitives (identity, Profile, Echo)
*Now that identity (§2) is clean and data is rich, open up carefully.*

- **Frontend:** Profile (all sections, visibility-aware); Echo (composer, book/emotion feeds, replies, private reactions, "why this"); report/block/mute UI; caught-up/empty states.
- **Backend:** echo service + reply + private reactions; profile aggregation (visibility+block aware); collections CRUD; moderation MVP = **report → auto-throttle → queue**; block/mute cross-surface service; feed candidate generation + popularity-suppressed ranking.
- **Database:** `echoes`, `echo_replies`, `echo_reactions`, `reports`, `blocks`, `mutes`, `echo_feed_candidates`, `collections`, `collection_items`, `milestones`.
- **API:** all Echo/Profile/collection/report/block endpoints; feed with correct keyset pagination.
- **Testing:** visibility/block enforcement tests (critical — this is where leaks happen, P1-3); ranking determinism + popularity-suppression tests; moderation throttle tests; report-bombing resistance test.

*Why before Twin/Chat: Twin needs public profiles/echoes to show, and block/report/moderation must exist **before** any stranger interaction. Building the safety substrate here means Twin and Chat inherit it rather than reinventing it.*

### PHASE 4 — Connection (Twin, then constrained Chat)
*Stranger interaction, gated behind everything built above.*

- **Frontend:** Twin suggestions (periodic card, explanations), connection-request flow, consent state; Twin Conversation UI (text-only, block/report inline, contact-share interstitial).
- **Backend:** nightly reader-vector + candidate pipeline (reuse DNA vectors — P2-3 fix); twin request/consent state machine; conversation service (participant-only authz); message classifier on ingest; retention policy.
- **Database:** `reader_vectors`, `twin_candidates`, `twin_requests`, `conversations`, `conversation_participants`, `messages`, prefs.
- **API:** twin + conversation endpoints; polling-based messages first (WebSockets deferred).
- **Testing:** consent-gating tests (no conversation without mutual accept), participant-only authz tests, block-mid-chat behavior, classifier flag path, rate-limit tests. **A written trust-&-safety + minor-protection policy is a required deliverable of this phase, not an afterthought.**

*Why last among social: Chat is the highest-liability surface and depends on Twin consent, which depends on Profile/Echo visibility, which depends on Phase 1 identity. This is the strict dependency chain. Delaying Chat also buys time to see whether the community is healthy enough to warrant private messaging at all.*

### PHASE 5 — Delight & future vision
*Only after the above retains.*

- Notifications full system (Tier 0–2, digests, quiet hours) — note Tier 0 security notices can/should land earlier in Phase 1; the *system* matures here.
- Book pages as destinations (emotional consensus per book).
- Realtime chat (WebSockets) *if* Chat proves valuable and safe.
- Reading Room / collections-as-space, reconsidered without dopamine mechanics.
- Yearly "reading in review" (honest, substance-based).
- Recommendation depth; "read this together" for Twins.
- Reconsider gamification only if data shows it serves users, not metrics.

*Why last: these are multipliers on an already-loved product. Building delight before retention is decoration on an empty room.*

### Why this overall order is optimal
1. **Dependencies flow one way:** identity → data → profiles/echoes → matching → chat. Building against that grain means constant rework.
2. **Safety substrate precedes stranger interaction:** block/report/moderation exist before Twin/Chat, so risky features inherit protection instead of shipping unprotected.
3. **Value precedes network:** single-player retention (Phase 2) de-risks the entire social bet — if people don't love the journal alone, adding people won't save it, and you'll have learned that cheaply.
4. **Correctness precedes everything:** Phase 1 + the test net mean each later phase builds on verified ground, so velocity *increases* over time instead of collapsing under regressions.

---

## Open decisions I need from you (before Phase 3)
1. **Do you accept the "no comparative metrics, feeds end" constraint?** (§0.1) The whole design assumes yes.
2. **Pseudonymous, not anonymous — agreed?** (§0.2/§2) This is load-bearing.
3. **Chat delayed to Phase 4, constrained, text-only — agreed?** (§0.3/§5)
4. **Minimum age & minor policy** — what's our stance, and do we attempt age assurance? This gates Chat and shapes moderation.
5. **Import sources for cold-start** — Goodreads CSV first? This shapes Phase 2 onboarding.

---

*This blueprint is a living document. As decisions land, we revise it — same as the audit. The two together are the source of truth for what Bibliome is and the order in which it becomes real.*
