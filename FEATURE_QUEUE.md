# Feature Queue — 13 features, one at a time

The working tracker for the current build push. `ROADMAP.md` is the older
phase-based backend plan; **this file is the live queue** and takes precedence on
sequencing.

## Rules of engagement
- **One feature at a time.** Do not start the next until the current one's
  checklist is fully ticked.
- **Agreed order:** 1 → 2 → 3 → 4 → 13 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12
- **Split before building.** Each feature declares what is backend and what is
  frontend *before* work starts. Frontend lives in the `bibliome-frontend` repo.
- **Add-ons don't float.** Anything discovered mid-build gets written into the
  "Add-ons" section below and attached to a specific numbered feature. Nothing
  gets fixed opportunistically outside its assigned feature.

Status key: `[ ]` not started · `[~]` in progress · `[x]` done

---

## The queue

### [x] 1 — Fix status route
**Split:** backend only.
Verify the status route accepts all 6 statuses; fix if not.

- [x] Confirmed `StatusUpdate` (`app/schemas/checkin.py`), `EntryStatus`
      (`app/schemas/entry.py`), and the `check_entry_status` constraint
      (`app/models/book_entry.py`) all already carry all 6. No code fix needed.
- [x] Route docstring corrected — it advertised 3 (`app/routers/entries.py`).
- [x] `test_status_vocabulary_is_one_vocabulary` — pins the three declarations to
      the same set so they can't drift again.
- [x] `test_status_patch_accepts_every_declared_status` — walks every status in
      the vocabulary through the PATCH route, derived from `EntryStatus` rather
      than hardcoded.

### [x] 2 — TBR fast-add
**Split:** backend (idempotent shelve endpoint + a DNA correctness fix) +
frontend (search surface, one-tap rows).

The premise "correctly invisible to DNA" turned out to be **false**, and #2 is
what made it dangerous — see add-on A2. Fixed as part of this feature.

Backend:
- [x] `dna_signals.OPENED_STATUSES` + `opened_only()` — the "was it actually
      opened" boundary, promoted from a private abandonment-only constant.
- [x] `build_dna` drops `want_to_read` once, at the boundary, so every book claim
      is computed over books the reader opened.
- [x] `entry_service.shelve_book()` — idempotent shelving, shared with echo's
      "to my shelf" (which now delegates to it instead of duplicating the rule).
- [x] `POST /api/entries/tbr` → `{ entry, created }`. Identity fields only; no
      intensity/emotions/status can be smuggled in. Never demotes an existing
      entry. Schedules no DNA recalc (shelving can't move the profile).
- [x] Tests: TBR endpoint (idempotency, no-demote, normalized dedupe, rating
      rejection, DNA-unmoved) + the pure DNA invariant. 332 passing.

Frontend:
- [x] `addToTbr()` in `api.js`; `shelveBook()` in `JournalContext`.
- [x] `TbrQuickAdd` component — debounced search, one-tap rows, per-row state
      (`Shelve → On your list / Already there / Try again`). Stays open after an
      add. No modal, no emotion picker, no save button.
- [x] "+ TO READ" control in the reading-room header.
- [x] DNA-progress count now uses `openedBooks()`, so 5 fast-adds no longer
      claim "DNA ready" over books nobody opened (the frontend mirror of A2).
- [x] 6 component tests. 244 passing.
- [x] **"The Stacks / TBR" — the heading itself is the toggle.** Two titles with
      a slash between; the inactive one is dimmed but legible, and TBR carries
      its count so the pile announces itself without being clicked. No separate
      control or chrome. The "· N on display" count moved below the books, where
      it reports on what is on screen. Defaults to The Stacks.
      The two lists are derived from `status`, never stored separately, so a book
      crosses over the moment its status changes: tap a TBR book → `EntryModal` →
      pick any status but `want_to_read` → it leaves TBR and joins The Stacks in
      the same render. Falls back to The Stacks when the pile empties. 5 tests on
      the split rule. 249 passing.
      *(Two wrong turns first: side-by-side columns — gives a third of the shelf
      permanently to unread books — then a segmented control beside the heading,
      which was chrome for a choice the title already expresses.)*

### [x] 3 — DNF + reason
**Split:** backend (A1 fix + drift guards) — the rest already existed.

The premise was wrong again: `dnf_reason` was **never free text**. It is already
a validated 6-value `Literal` (`DnfReason`), and `EntryModal` already renders it
as tappable `OneTap` chips. Decided 2026-08-16 to **keep all 6** rather than trim
to 4–5: each names a genuinely different reason to stop, and trimming would need
a migration remapping existing rows onto the survivors — real data loss for a
cosmetic gain.

- [x] **A1** — `reread` no longer wipes `finished_at`, on **both** write paths
      (`checkin_service.update_status` and `entry_service.update_entry`). They
      were separate branches, so history would have survived a status tap and
      died on an edit.
- [x] `test_only_reread_keeps_the_finish_date` — written over the whole status
      vocabulary, so a status added later must decide explicitly rather than
      inherit a branch.
- [x] Backend/frontend DNF vocabulary pinned to each other from both sides
      (`DnfReason` ↔ `DNF_OPTIONS`), the same way the status vocabulary is.
- [x] 337 backend / 251 frontend passing.

### [x] 4 — DNF insight
**Split:** backend only. No frontend work — insights render from the generic
payload, so a new category surfaces without a client change.

Built as a **new signal beside `abandonment()`, not inside it.** `abandonment()`
asks which EMOTION correlates with not finishing and stays silent when no emotion
does — so a reader whose DNFs share a stated reason but no emotion was told
nothing, despite answering the question every time. `dnf_reasons()` counts the
answers themselves.

- [x] `dna_signals.dnf_reasons()` — tallies stated reasons over books put down
      (`abandoned` + `paused`, matching `abandonment()`), ignoring books with no
      stated reason and finished books carrying a stale reason.
- [x] Three hand-written variants: `unanimous` ("4 books, same reason every
      time"), `dominant` ("5 bored you, 1 was too much, 1 caught you at the wrong
      time"), `spread` ("no one reason: … 5 books, 5 different reasons").
- [x] Gate of **3 stated reasons**, with `GATE_POPULATION["dnf_reason"] =
      "dnf_stated_count"` — the claim carries its own denominator, so it isn't
      gated on tagged books it never reads.
- [x] Number agreement in the tally ("1 **was** too much", not "1 were").
- [x] `_population` now tolerates a missing key (partial contexts lock rather
      than crash), with `test_every_gate_population_key_is_produced` capturing the
      real ctx so that tolerance can't hide a typo'd denominator. Verified the
      guard fails on an introduced typo.
- [x] 346 backend passing.

### [⏸] 13 — Ship Marginalia — **BLOCKED, skipped 2026-08-16**
Cannot locate the feature. "Marginalia" appears once in either repo, in
`docs/VISION.md`, describing Echo reactions ("Reactions are marginalia —
'underlined', 'to my shelf', 'made me reconsider'"). No `marginalia` branch,
commit or stash in `bookDNA` or `bookDNA-frontend`; every router is mounted in
`app/main.py` with nothing held behind a flag; **no age-gating code exists
anywhere** (no birthdate, no adult flag). Thread reporting at
`app/routers/threads.py:188` looks complete.

To unblock: point at the branch/repo/working copy that holds it, or confirm
which existing surface it means. Then it re-enters the queue before #5.

### [x] 5 — Shareable collections
**Split:** backend (membership, invites, item identity) + frontend (invite/join
flow).

Backend:
- [x] Migration `029_shared_collections` — `collection_members`,
      `collection_invites`, and `collection_items.{book_id, added_by}`.
- [x] **Items moved off `entry_id` onto `book_id`.** An entry is one reader's
      private copy; in a shared collection a member adding a book would attach a
      row nobody else can read, and the same book added twice would be two
      unrelated items. `entry_id` kept nullable, not dropped: items whose entry
      never resolved to a catalog row have no `book_id` to migrate onto, and
      dropping it would silently empty those collections.
- [x] `CollectionMember` — owner gets a row too, so membership is the single
      gate and no read path has to remember a separate ownership check.
- [x] `CollectionInvite` — SHA-256 stored like `ShareToken`; multi-use by design
      with optional `max_uses` / `expires_at`.
- [x] Routes: mint/revoke invite (owner), peek (names the collection before you
      commit), join, list members, remove member / leave, add book, remove book.
- [x] Permission model: owner does everything; member adds and removes their own.
      Non-members get **404, not 403** — 403 confirms an id exists.
- [x] 18 tests. Two real bugs caught by them: a member re-clicking a spent link
      got 404 instead of "already in", and `peek` lazy-loaded items on an async
      session. 364 backend passing.
- [x] Migration verified for real: applied from scratch, then downgraded, seeded
      with a resolved AND an unresolved entry, re-upgraded. Backfill confirmed —
      resolved item got `book_id`, unresolved kept `entry_id` with NULL
      `book_id`, owner membership created.

Frontend:
- [x] `api.js` — add/remove by `book_id`, mint/revoke/peek/join invites, members,
      leave.
- [x] `CollectionSharing` panel inside the collection drawer — member list with
      "(you)", invite link shown **once** with copy that says so, revoke with the
      line "people who already joined stay", and leave for non-owners. Invite
      controls appear only after the member list confirms ownership, so no
      control flashes and disappears.
- [x] `JoinCollectionPage` at `/collections/join/:token` — **peeks before it
      joins**. Names the collection, its reader and book counts, then asks.
      Handles dead links, already-a-member, and signed-out.
- [x] `services/pendingInvite.js` — a signed-out invite parks its token in
      sessionStorage; `AuthedLayout` picks it back up after sign-in and returns
      the reader to the invitation instead of dumping them on their own shelf.
      Its own module because `JoinCollectionPage` is lazy-imported by `App` and
      importing back would be a cycle.
- [x] 13 component tests; fixed `CollectionsEditor.test.jsx`, which the drawer's
      new auth dependency broke. 267 frontend passing.

### [x] 6 — Collection chat
**Split:** backend (conversations, moderation, block semantics) + frontend
(conversation list + room UI).

**No thread row.** A conversation is the pair `(collection_id, book_id)`. A
thread table would have to be created lazily, raced on the first message, and
reconciled every time a book joins or leaves the collection; the pair cannot
drift out of sync with itself. There is deliberately **no "general" room** — a
collection is a set of books, and a general channel turns it into a group chat
that happens to have books in it.

The four places a group room differs from a 1:1 resonance thread, each decided
explicitly:
- [x] **A block hides people, it does not close the room.** In a thread a block
      ends the conversation; here it must not evict either party from a
      collection they both belong to. Messages are *omitted*, not tombstoned — a
      "hidden" marker still tells you the blocked person is here and talking.
- [x] **A threat is refused, not held.** Echo can hold one invisibly because it
      has a feed to hide it in; a room of four is not a feed, and the sender
      would notice. Crisis sends + returns resources to the sender only. PII is
      allowed (a private group swapping details is the room working).
- [x] **Leaving is not deleting.** Words stay, exactly as added books do.
      `sender_id` cascades on account delete; `added_by` is SET NULL. Different
      on purpose: a departing member's books are the collection's, their words
      are theirs.
- [x] **The book is the anchor.** You cannot talk about a book the collection
      does not hold — re-checked on every post, since a book can be pulled
      mid-conversation. Removing it makes the room unreachable but **keeps the
      history**, which returns intact if the book is added back (`book_id`
      references `books`, not `collection_items`).

Also: keyset paging on `(created_at, id)` so two messages in the same
millisecond can't be skipped or repeated; live membership read for notifications
so a departed member stops being nudged; author-or-owner deletion, mirroring
book removal; report targets the conversation, and does **not** auto-hide it — a
private group must not be silenceable on one member's say-so.

- [x] Migration `030_collection_chat`; verified upgrade, downgrade, re-upgrade.
- [x] 17 tests. Two real bugs caught: PII was refused because `classify_text`
      returns HOLD for *both* threats and contact details (the verdict alone
      can't decide — the reason is what separates them), and a shadowed helper
      in the test file.
- [x] 381 backend passing.

Frontend:
- [x] `api.js` — conversations, keyset-paged messages (cursor carries BOTH
      halves), send, delete, report.
- [x] `CollectionChat` — book list ("start it" on ones nobody has spoken about)
      → one book's room. No general channel anywhere in the UI either.
- [x] Folded behind a `Discussion` summary in the drawer: a collection is a
      shelf first, and an always-open chat panel makes it a chat room with books
      in it.
- [x] Honest about the backend's answers: a **refused** message says plainly it
      did not send and **keeps the draft** (it is still unsaid); a crisis flag
      shows support and the message still posts; a refused delete surfaces the
      403 instead of dropping the row locally; report copy says "nothing here
      changes for anyone else" because reporting deliberately does not hide the
      room.
- [x] Reused the existing `CrisisInterstitial` rather than writing a second one.
- [x] `scrollIntoView` guarded — absent in jsdom and in older webviews, and a
      room that throws while scrolling is worse than one that doesn't scroll.
- [x] 8 component tests; fixed `CollectionsEditor.test.jsx` again for the new
      mount-time reads. 275 frontend passing.

### [ ] 7 — Book emotion vectors
**Split:** backend only (LLM pipeline + `books` column).
Nothing exists yet. LLM predicts an 18-emotion vector from blurb/reviews → new
column on `books`.
Estimate: 2–3 days. **Unblocks all recommendation features (#8–#12).**

### [ ] 8 — Prescription mode
**Split:** backend (matching endpoint) + frontend (feeling picker, logged-out).
Pick a feeling → matching books. Must work logged-out.
Estimate: 2 days. Depends on #7.

### [ ] 9 — Blind-spot recs
**Split:** backend mostly (`blind_spots_service` exists) + frontend surface.
"Never felt awe — try this".
Estimate: 1 day. Depends on #7.

### [ ] 10 — Stated-vs-revealed recs
**Split:** backend (rec endpoint) + frontend surface.
`stated_vs_revealed` is already computed. Recommend what they actually want, not
what they claim.
Estimate: 1 day. Depends on #7.

### [ ] 11 — Resonance recs
**Split:** backend (`resonance_service` matching) + frontend surface.
"The reader who felt what you felt also loved…"
Estimate: 2 days.

### [ ] 12 — Outcome prediction
**Split:** backend (prediction + confirm/refute) + frontend (prompt after finishing).
"This will wreck you" + confirm/refute after finishing.
Estimate: 1 week. Needs #7 + accumulated data.

---

## Add-ons
Discovered mid-build. Each is attached to a numbered feature and ships with it —
never separately, never opportunistically.

### [x] A3 — Web Push for discussion notifications → **shipped with #6**
In-app notifications existed; nothing reached a phone. Self-hosted **VAPID**, not
FCM/OneSignal — a vendor would learn who is notified about what, on a product
whose premise is a private mirror.

- Backend: `PushSubscription` (keyed on `endpoint`, which IS the device, so
  re-subscribing upserts), migration `031`, `push_service`, `/push/key`,
  `/push/subscribe`, `/push/unsubscribe`, `scripts/gen_vapid_keys.py`.
- **Push rides on `notify()`'s existing decisions** — prefs, blocks,
  self-suppression, quiet hours, batching. A second delivery channel, not a
  second set of rules. A batched burst is one buzz, and a quiet-hours deferral
  doesn't buzz at all.
- **The payload is a knock, not the message.** No body, no book, no handle — it
  is read on a lock screen by whoever is holding the phone. Tests assert nothing
  leaks, including for unknown kinds.
- **Failures are a courtesy, never a dependency**: a dead push can't fail the
  message that caused it. Only 404/410 prunes a subscription; transient failures
  leave it alone.
- Frontend: `public/sw.js` (push only — no fetch handler, so no cache smuggled in
  under "notifications"), `services/push.js`, `PushToggle` in Settings. Prompt
  only ever from a tap. 10 backend + 8 frontend tests.
- **Needs configuration to work:** run `python -m scripts.gen_vapid_keys` and set
  `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT`. Until then the
  server reports `enabled: false` and the toggle hides itself.

### [x] A2 — `want_to_read` was NOT invisible to DNA → **shipped with #2**
The stated premise of #2 was wrong. A `want_to_read` was excluded from book
aggregates and abandonment, but not from `book_count` or `intensity_signature`.
It carries no emotions (so the vectors were always safe) but it is still a row
with a placeholder `intensity=5`. Measured: 6 read books + 20 fast-adds moved
`book_count` 6 → 26 and rating style from mean 8.5 / `share_high` 1.0 (an
"8-or-nothing reader") to mean 5.81 / `share_high` 0.231 (a "careful 4–5 rater").
Fast-add is what turns that from a rounding error into a profile written by books
nobody opened. Fixed at one boundary in `build_dna`, not per-signal.

### [x] A1 — `reread` must not lose finish history → **shipped with #3**
`update_status` in `app/services/checkin_service.py` nulls `finished_at` on any
status that isn't `finished`. Correct for `reading` and `paused`; wrong for
`reread`, which silently erases the original finish date from the calendar and
mirror. A reread is evidence the book was finished, not evidence it wasn't.
Decided 2026-08-16: reread preserves history.
