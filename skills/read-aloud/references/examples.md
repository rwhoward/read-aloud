# Conversion examples

Each example shows a source excerpt and the HTML fragment it becomes. Notice that every fact survives, nothing is invented, and the output usually runs longer than the input.

## 1. Opening orientation

The source is a three-section design doc titled "Session Token Refresh Redesign", from the `billing-portal` repo, dated 2026-09-30.

```html
<h1>The problem and its cause</h1>
<p>This is a listenable version of the session token refresh redesign, a design document from the billing portal repo written on September 30th, 2026. It explains why users are being logged out in the middle of their sessions, the fix the team settled on, and how that fix will roll out.</p>
<p>It comes in three parts. This first part covers the problem and what's causing it. Part two walks through the new refresh flow. Part three covers the rollout plan and the questions that are still open.</p>
```

The opening says what the document is, where it came from, and the shape of what's coming, then gets out of the way.

## 2. A bullet list of fragments

Source:

```markdown
## Decision
- Use Redis for session cache
- TTL: 30 min (sliding)
- Fallback to DB on cache miss
- Rejected: in-memory (doesn't survive restarts, no multi-instance)
```

Output:

```html
<h2>What the team decided</h2>
<p>The team decided to cache sessions in Redis. Each session stays in the cache for thirty minutes, and that timer is sliding, which means every request starts the thirty minutes over again. So an active user's session stays cached, and an idle one drops out half an hour after its last request.</p>
<p>When a session isn't found in the cache, the app falls back to looking it up in the database.</p>
<p>The team also considered keeping sessions in the app's own memory, and rejected that for two reasons. In-memory sessions don't survive a restart of the app. And they don't work with multiple instances of the app running at once.</p>
```

The explanation of "sliding" is welcome because it's what the term means. The output does not claim the database result gets written back to the cache, or that this decision fixes some particular bug, because the source doesn't say either thing. The rejection reasons are announced as "two reasons" so the listener knows how many to expect.

## 3. A table

Source:

```markdown
| Env     | Instances | Timeout | Autoscale |
|---------|-----------|---------|-----------|
| dev     | 1         | 30s     | no        |
| staging | 2         | 30s     | yes       |
| prod    | 4         | 10s     | yes       |
```

Output:

```html
<p>There are three environments, and they differ in three ways: how many instances they run, their request timeout, and whether they scale automatically.</p>
<p>Development runs a single instance with a 30 second timeout, and it doesn't autoscale. Staging runs two instances, keeps the same 30 second timeout, and does autoscale. Production runs four instances and autoscales, and its timeout is much tighter, at 10 seconds.</p>
<p>So apart from its size, what makes production different is that shorter timeout.</p>
```

The table is introduced before it's walked, every value is voiced, and the closing line points out the pattern a reader would have spotted at a glance.

## 4. A code block

Source:

````markdown
```ts
export async function refreshToken(userId: string) {
  const token = await tokenStore.get(userId);
  if (!token || token.expiresAt < Date.now() + 60_000) {
    const fresh = await authClient.refresh(token?.refreshToken);
    await tokenStore.set(userId, fresh);
    return fresh;
  }
  return token;
}
```
````

Narration:

```html
<p>The refresh token function takes a user ID and makes sure that user ends up with a token that's good for at least another minute. The exact code is on the reference card.</p>
<p>It starts by looking up the user's current token in the token store. If there's no token at all, or the token expires within the next 60 seconds, it asks the auth client for a fresh token. It passes along the old token's refresh token, if there was an old token. Then it saves the fresh token back to the token store and returns it.</p>
<p>If the existing token still has more than a minute left, the function returns it unchanged.</p>
```

Reference card entry, in `src/reference.html`:

```html
<h2>The refresh token function</h2>
<p>Discussed in part 2, under "How a token gets refreshed".</p>
<pre><code>export async function refreshToken(userId: string) {
  const token = await tokenStore.get(userId);
  ...
}</code></pre>
```

On the real card, include the full code rather than an ellipsis. The narration follows the code's actual logic in order, including the edge case where there's no old token, without reading any syntax aloud.

## 5. Dense, symbol-heavy prose

Source:

```markdown
The worker polls the queue (default interval: 5s, configurable via `POLL_INTERVAL_MS`) & processes msgs in batches of ≤50; on failure → exponential backoff w/ jitter (max 5 retries), then DLQ.
```

Output:

```html
<p>The worker checks the queue for new messages every five seconds by default. That interval is configurable through the poll interval setting, which is measured in milliseconds. The exact setting name is on the reference card.</p>
<p>The worker processes messages in batches of up to fifty at a time. When processing fails, it retries using exponential backoff, which means it waits longer after each failed attempt. It also adds jitter, a small random amount of extra delay on each wait. After five retries, a message that still fails goes to the dead letter queue, which is where messages that can't be processed are set aside.</p>
```

One crammed sentence becomes five plain ones. Every symbol and abbreviation is now a word, and the two jargon terms each get a one-clause definition.

## 6. A finding from an AI-generated analysis report

Source:

```markdown
### 🔴 HIGH — Hardcoded credentials
**Location:** `Config/AppSettings.cs:42`
**Impact:** DB conn string w/ plaintext pwd committed to repo; anyone w/ read access → prod DB.
**Recommendation:** Move to Key Vault / user secrets; rotate pwd immediately.
```

Output:

```html
<h3>Hardcoded database credentials</h3>
<p>This finding is rated high severity. The app settings file in the Config folder, at around line 42, contains the database connection string with the password in plain text, and that file is committed to the repo. The consequence is that anyone who can read the repo can reach the production database.</p>
<p>The report recommends two things. First, move the connection string into Key Vault or into user secrets. Second, rotate the password immediately.</p>
```

The severity label is kept and spoken, the emoji is gone, and "immediately" keeps its urgency. The file location is spoken as a place rather than a path, and the exact path goes on the reference card.

## 7. A diagram

Source:

````markdown
```
┌──────────┐     ┌──────────────┐     ┌────────────┐
│  Client  │────▶│  API Gateway │────▶│ Orders svc │
└──────────┘     └──────┬───────┘     └─────┬──────┘
                        │                   │ publishes
                        ▼                   ▼
                 ┌──────────────┐     ┌────────────┐
                 │  Auth svc    │     │ Service Bus│
                 └──────────────┘     └────────────┘
```
````

Output:

```html
<p>The diagram shows how a request moves through four services, plus a message bus. The original diagram is on the reference card.</p>
<p>A request starts at the client and goes to the API gateway. From the gateway, the request goes on to the orders service. The gateway also connects down to the auth service. The diagram doesn't label that connection, so it doesn't say whether the gateway calls the auth service on every request or only some of the time.</p>
<p>Finally, the orders service publishes to the Service Bus. That's the only arrow in the diagram with a label.</p>
```

The tour follows the flow from start to end. The unlabeled arrow is described as unlabeled rather than given a guessed meaning. The labeled arrow is described by its label and nothing more. It would be tempting to add that publishing means the orders service doesn't wait for a reply, but the diagram doesn't say that.

## 8. A source that contradicts itself

Source, from two different sections of the same report:

```markdown
## Summary
**4 endpoints** accept requests without authentication.
...
## Findings
- `GET /orders/{id}` — no [Authorize] attribute
- `POST /orders/import` — no [Authorize] attribute
- `GET /reports/daily` — AllowAnonymous
- `DELETE /cache` — AllowAnonymous
- `GET /health/details` — no [Authorize] attribute
```

Output:

```html
<p>The summary says four endpoints accept requests without authentication.</p>
...
<p>One thing here doesn't add up. The summary said four endpoints were unprotected, but the findings list five: getting a single order, importing orders, the daily report, clearing the cache, and the detailed health check. The report doesn't explain the difference, so treat the count of four with some caution. The exact routes are on the reference card.</p>
```

Each claim is narrated where the source makes it. The conflict is pointed out once, with specifics, and the narration doesn't pick a winner or quietly change the earlier figure.
