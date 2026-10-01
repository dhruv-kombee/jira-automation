# Teams MVP Phase 1 — Testing Guide

This document provides exact manual testing steps for verifying the MVP Phase 1 implementation.

---

## Prerequisites

Before testing, ensure:

- [x] `.env` is configured with all required values (see [TEAMS_MVP_SETUP.md](TEAMS_MVP_SETUP.md))
- [x] Backend is running (`python main.py`)
- [x] Secure tunnel is active and `WEBHOOK_PUBLIC_URL` is set
- [x] Graph subscription is created (`python scripts/manage_subscription.py create`)
- [x] Test users are configured in `.env` (TEST_CLIENT_USER_ID, etc.)

---

## Test 1 — Health Check

Verify the server is running and responsive.

### Steps

```bash
curl http://localhost:3000/health
```

### Expected Response

```json
{
  "status": "ok",
  "service": "teams-mvp",
  "timestamp": "2026-09-30T12:00:00.000Z",
  "uptime": 42.123
}
```

### Expected HTTP Status

```
200 OK
```

### Pass Criteria

- [x] Returns 200 OK
- [x] Response contains `"status": "ok"`

---

## Test 2 — Subscription Validation

Verify the webhook endpoint handles Microsoft Graph's validation handshake.

### Steps

```bash
curl "http://localhost:3000/webhooks/teams?validationToken=test-token-abc123"
```

### Expected Response

```
test-token-abc123
```

The response must be:
- **Plain text** (not JSON)
- **Status 200**
- **Content-Type: text/plain**
- **Body exactly matches the validationToken value**

### Pass Criteria

- [x] Returns 200 OK
- [x] Content-Type is text/plain
- [x] Body is the exact validationToken string

---

## Test 3 — Normal Client Message

Verify the backend correctly receives, processes, and stores a normal Teams message.

### Steps

1. Open Microsoft Teams
2. Navigate to the configured Team and Channel
3. **From the test client account**, send:

```
Hello team, this is a test message.
```

### Expected Backend Console Output

```
══════════════════════════════════════════════════
  Teams message received
══════════════════════════════════════════════════

  Message ID:   <graph-message-id>
  Team ID:      <configured-team-id>
  Channel ID:   <configured-channel-id>

  Sender:
    User ID:      <client-user-id>
    Display Name: Test Client
    Role:         CLIENT

  Message:
    Hello team, this is a test message.

  Created:      2026-09-30T12:00:00.000Z
  Modified:     N/A
  Web URL:      <teams-message-url>

  Reply To:     N/A
  Attachments:  0
══════════════════════════════════════════════════
```

### Verify Stored Message

```bash
curl http://localhost:3000/api/messages
```

Expected: The message appears in the response with:
- `message_text`: `Hello team, this is a test message.`
- `sender_display_name`: `Test Client` (or the actual display name)
- `sender_user_id`: matches `TEST_CLIENT_USER_ID`

### Pass Criteria

- [x] Backend receives notification
- [x] Backend retrieves message from Graph
- [x] Backend identifies sender as CLIENT
- [x] Backend stores the message
- [x] Message text is correct
- [x] Sender information is correct
- [x] Timestamps are present

---

## Test 4 — Issue-Like Message

Verify the backend receives an issue-like message without any classification or Jira action.

### Steps

1. From the **test client account**, send in the configured channel:

```
Issue: The payment button is not working on the checkout page.
```

### Expected Backend Output

```
  Sender:
    User ID:      <client-user-id>
    Display Name: Test Client
    Role:         CLIENT

  Message:
    Issue: The payment button is not working on the checkout page.
```

### Pass Criteria

- [x] Backend receives notification
- [x] Sender = Test Client (identified by user ID, not display name)
- [x] Message text = exact message sent
- [x] **No** Jira ticket is created
- [x] **No** LLM classification occurs
- [x] **No** PM approval is triggered
- [x] The message is simply stored as-is

---

## Test 5 — Duplicate Notification

Verify the backend does not store the same message twice.

### Steps

#### Option A: Simulate via API

Send a simulated Graph notification with the same message ID twice:

```bash
curl -X POST http://localhost:3000/webhooks/teams \
  -H "Content-Type: application/json" \
  -d '{
    "value": [
      {
        "subscriptionId": "test-sub",
        "changeType": "created",
        "resource": "teams('\''<team-id>'\'')/channels('\''<channel-id>'\'')/messages('\''test-msg-001'\'')",
        "resourceData": {
          "id": "test-msg-001",
          "@odata.type": "#Microsoft.Graph.chatMessage"
        }
      }
    ]
  }'
```

> Note: This will attempt to fetch the message from Graph and may fail if the message ID is fake. The duplicate protection is at the database level.

#### Option B: Verify via database

After Test 3 or Test 4, check the stored messages:

```bash
curl http://localhost:3000/api/messages
```

Even if Graph sends multiple notifications for the same message, only one record should exist per `(team_id, channel_id, message_id)` combination.

### Expected Log Output (on duplicate)

```
DUPLICATE_MESSAGE: Duplicate message ignored
  messageId: <id>
  teamId: <id>
  channelId: <id>
```

### Pass Criteria

- [x] Only one message record exists for the same `team_id + channel_id + message_id`
- [x] Backend logs the duplicate event
- [x] Backend does not return an error

---

## Test 6 — PM Message

Verify sender role identification works for the PM user.

### Steps

1. From the **test PM account**, send:

```
I'll review this issue.
```

### Expected

```
  Sender:
    Role:         PM
```

### Pass Criteria

- [x] Sender role is identified as PM (based on configured user ID)

---

## Test 7 — Developer Message

### Steps

1. From the **test developer account**, send:

```
Looking into this now.
```

### Expected

```
  Sender:
    Role:         DEVELOPER
```

### Pass Criteria

- [x] Sender role is identified as DEVELOPER (based on configured user ID)

---

## Test 8 — Unknown Sender

### Steps

1. From an account that is **not** configured in `.env`, send a message in the channel

### Expected

```
  Sender:
    Role:         UNKNOWN
```

### Expected Log

```
UNKNOWN_SENDER: Unknown sender
  userId: <unknown-user-id>
```

### Pass Criteria

- [x] Sender role is UNKNOWN
- [x] Message is still stored
- [x] No crash or error

---

## Verifying All Stored Messages

At any time, view all stored messages:

```bash
curl http://localhost:3000/api/messages | python -m json.tool
```

Or with `jq`:

```bash
curl -s http://localhost:3000/api/messages | jq '.messages'
```

---

## Summary Checklist

| # | Test | Status |
|---|------|--------|
| 1 | Health check returns 200 | ☐ |
| 2 | Subscription validation echoes token | ☐ |
| 3 | Normal client message received + stored | ☐ |
| 4 | Issue-like message received (no Jira) | ☐ |
| 5 | Duplicate notification = 1 record | ☐ |
| 6 | PM role identified | ☐ |
| 7 | Developer role identified | ☐ |
| 8 | Unknown sender handled gracefully | ☐ |
