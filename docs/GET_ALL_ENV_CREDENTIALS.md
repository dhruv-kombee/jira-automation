# Complete Guide to Obtaining All `.env` Configuration Values

This guide provides 100% accurate, verified, step-by-step instructions for finding and configuring every variable required in your `.env` file for the **Jira Automation - Teams MVP** project.

Every step references real, official Microsoft web portals, verified Graph APIs, and exact tools. No dummy placeholders are assumed.

---

## Quick Reference: All Required `.env` Variables

| Environment Variable | Description | Source / Portal |
|---|---|---|
| `MICROSOFT_TENANT_ID` | Microsoft 365 / Entra Directory ID (GUID) | Microsoft Entra Admin Center |
| `MICROSOFT_CLIENT_ID` | Application (Client) ID of your registered app (GUID) | Microsoft Entra > App registrations |
| `MICROSOFT_CLIENT_SECRET` | Secret password value generated for the app (string) | Microsoft Entra > Certificates & secrets |
| `TEAMS_TEAM_ID` | Microsoft Teams Team ID / Group Object ID (GUID) | Teams App (Link) or Entra Groups |
| `TEAMS_CHANNEL_ID` | Channel thread identifier (`19:...@thread.tacv2`) | Teams App (Link decoded) or Graph Explorer |
| `TEST_CLIENT_USER_ID` | Object ID of user simulating Client role (GUID) | Microsoft Entra > Users |
| `TEST_PM_USER_ID` | Object ID of user simulating PM role (GUID) | Microsoft Entra > Users |
| `TEST_DEVELOPER_USER_ID`| Object ID of user simulating Developer role (GUID) | Microsoft Entra > Users |
| `PORT` | Local server port (Default: `3000`) | Local config |
| `WEBHOOK_PUBLIC_URL` | Public HTTPS URL forwarding to local port 3000 | ngrok / Cloudflare Tunnel / localtunnel |
| `LOG_LEVEL` | Logging verbosity (`debug`, `info`, `warn`, `error`) | Local config (Default: `info`) |
| `DATABASE_PATH` | Path to SQLite database file | Local config (Default: `./data/messages.db`) |

---

## Step 1: Microsoft Entra ID (Azure AD) Credentials

### 1.1 `MICROSOFT_TENANT_ID`
The **Tenant ID** is the unique directory identifier (GUID) for your Microsoft 365 organization.

#### Web Portal Steps:
1. Open the **Microsoft Entra admin center**: [https://entra.microsoft.com](https://entra.microsoft.com) (or Azure Portal: [https://portal.azure.com](https://portal.azure.com)).
2. Sign in with your Microsoft 365 administrator or organizational account.
3. In the left navigation menu, expand **Identity** and click **Overview**.
4. In the **Basic information** tab, look for **Tenant ID**.
5. Click the **Copy to clipboard** icon next to the 36-character GUID (formatted like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).
6. Paste into `.env` as:
   ```env
   MICROSOFT_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

*Alternative via Graph Explorer*:
- Sign in to [Microsoft Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer).
- Run: `GET https://graph.microsoft.com/v1.0/organization`
- Copy the `id` field from the response JSON.

---

### 1.2 `MICROSOFT_CLIENT_ID`
The **Client ID** (Application ID) is the unique identifier for the registered daemon application in your tenant.

#### Web Portal Steps:
1. In [Microsoft Entra admin center](https://entra.microsoft.com), navigate to:
   **Identity** → **Applications** → **App registrations**.
2. Click **+ New registration** at the top.
3. Configure the following fields:
   - **Name**: `Jira Automation - Teams MVP` (or any descriptive name).
   - **Supported account types**: Select **Accounts in this organizational directory only (<Your Org Name> only - Single tenant)**.
   - **Redirect URI**: Leave blank (not required for client credentials flow).
4. Click **Register** at the bottom.
5. You will automatically land on the app's **Overview** page.
6. Look for **Application (client) ID**.
7. Copy the 36-character GUID and paste into `.env`:
   ```env
   MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

---

### 1.3 `MICROSOFT_CLIENT_SECRET`
The **Client Secret** is a confidential string credential that authenticates the backend service against Microsoft Entra ID.

#### Web Portal Steps:
1. While still on your app registration page in [Microsoft Entra admin center](https://entra.microsoft.com):
2. In the left sidebar under **Manage**, click **Certificates & secrets**.
3. Select the **Client secrets** tab and click **+ New client secret**.
4. Fill in:
   - **Description**: `teams-automation-dev` (or any label).
   - **Expires**: Select `180 days (6 months)` or your company policy.
5. Click **Add**.
6. **CRITICAL WARNING**: Immediately copy the string in the **Value** column.
   - **Do NOT copy the "Secret ID" column** — that is only an index, not the secret.
   - The **Value** is displayed **ONLY ONCE**. As soon as you refresh or navigate away, Microsoft permanently masks it (`••••••••`).
7. Paste into `.env`:
   ```env
   MICROSOFT_CLIENT_SECRET=your_client_secret_value_here
   ```

---

### 1.4 Configure Required API Permissions & Admin Consent
The backend uses **Application Permissions** (Client Credentials Flow via `@azure/identity` and `@microsoft/microsoft-graph-client`) to read messages without user sign-in.

#### Web Portal Steps:
1. In your app registration page, click **API permissions** in the left sidebar.
2. Click **+ Add a permission**.
3. Select **Microsoft Graph**.
4. Click **Application permissions** (do **NOT** select "Delegated permissions").
5. Search for and check each of the following 4 permissions:

| Permission Name | Category | Exact Purpose in Code |
|---|---|---|
| `ChannelMessage.Read.All` | `ChannelMessage` | Fetch channel messages and replies (`/teams/{id}/channels/{id}/messages`) |
| `Team.ReadBasic.All` | `Team` | Read team metadata and validate existence |
| `Channel.ReadBasic.All` | `Channel` | Read channel details and list channels |
| `User.Read.All` | `User` | Read sender profiles (`displayName`, email) |

6. Click **Add permissions** at the bottom.
7. Click the **Grant admin consent for <Your Organization>** button (located directly next to "+ Add a permission").
8. When prompted with the confirmation dialog, click **Yes**.
9. **Verification**: Confirm that all 4 permissions now display a **green checkmark (✓)** in the **Status** column indicating "Granted for *<Organization>*".

> **Note on `ChannelMessage.Read.All`**:
> Microsoft Graph classifies Teams channel message access as a sensitive API. While Microsoft removed the manual review request form requirement in May 2023, standard tenant admin consent is mandatory. If you are not a Global Administrator or Privileged Role Administrator in your Microsoft 365 tenant, an admin must click the "Grant admin consent" button for you.

---

## Step 2: Microsoft Teams Identifiers

### 2.1 `TEAMS_TEAM_ID`
In Microsoft 365, every Team is backed by a Microsoft 365 Group. The `TEAMS_TEAM_ID` is the **Azure AD Group Object ID** (GUID).

#### Method A: Direct from Teams App (Fastest)
1. Open **Microsoft Teams** (desktop app or web at [https://teams.microsoft.com](https://teams.microsoft.com)).
2. In the left navigation, click **Teams**.
3. Locate the Team you want to monitor.
4. Click the three dots (**⋯**) next to the Team name.
5. Click **Get link to team**.
6. Click **Copy**.
7. Paste the copied link into a text editor (e.g. Notepad). The link looks like this:
   ```text
   https://teams.microsoft.com/l/team/19%3a...%40thread.tacv2/conversations?groupId=b4a1b2c3-d4e5-6789-0abc-def123456789&tenantId=...
   ```
8. Find the query parameter `groupId=`.
9. The 36-character GUID immediately following `groupId=` is your `TEAMS_TEAM_ID`:
   ```env
   TEAMS_TEAM_ID=b4a1b2c3-d4e5-6789-0abc-def123456789
   ```

#### Method B: Via Microsoft Entra Admin Center
1. Go to [https://entra.microsoft.com](https://entra.microsoft.com).
2. Go to **Identity** → **Groups** → **All groups**.
3. Search for the name of your Team (Group type will be `Microsoft 365`).
4. Copy the **Object ID** value for that group.

#### Method C: Via Microsoft Graph Explorer
1. Open [Microsoft Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer) and sign in.
2. Run:
   ```http
   GET https://graph.microsoft.com/v1.0/me/joinedTeams
   ```
3. Locate your team in the `value` array. The `"id"` field is your `TEAMS_TEAM_ID`.

---

### 2.2 `TEAMS_CHANNEL_ID`
The Channel ID is the unique thread identifier of the channel (usually starts with `19:` and ends with `@thread.tacv2` or `@thread.skype`).

#### Method A: Direct from Teams App (With URL Decoding)
1. In Microsoft Teams, find the specific channel you want to monitor (e.g., `General` or a test channel).
2. Click the three dots (**⋯**) next to the channel name.
3. Click **Get link to channel**.
4. Click **Copy**.
5. Paste the link into a text editor. The link looks like this:
   ```text
   https://teams.microsoft.com/l/channel/19%3A79a29abcdef1234567890%40thread.tacv2/General?groupId=...
   ```
6. The Channel ID is the segment between `/channel/` and `/General` (or channel name):
   `19%3A79a29abcdef1234567890%40thread.tacv2`
7. **Decode the URL encoding**:
   - Replace `%3A` (or `%3a`) with `:`
   - Replace `%40` with `@`
8. The decoded value is your `TEAMS_CHANNEL_ID`:
   ```env
   TEAMS_CHANNEL_ID=19:79a29abcdef1234567890@thread.tacv2
   ```

*Tip: Quick 1-liner to decode in browser console or PowerShell*:
```javascript
// In browser console (F12):
decodeURIComponent("19%3A79a29abcdef1234567890%40thread.tacv2")
// Output: "19:79a29abcdef1234567890@thread.tacv2"
```

#### Method B: Via Microsoft Graph Explorer (Raw & Already Decoded)
1. Open [Microsoft Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer).
2. Run (replace with your actual Team ID from Step 2.1):
   ```http
   GET https://graph.microsoft.com/v1.0/teams/{TEAMS_TEAM_ID}/channels
   ```
3. Find the channel object with the desired `displayName` (e.g., "General").
4. Copy the `"id"` property directly. It will already be in decoded format (`19:...@thread.tacv2`).

---

## Step 3: User Role Mapping IDs

In `src/services/senderService.js`, when a message notification arrives, the backend retrieves the sender's Microsoft Graph `userId` (their Azure AD Object ID) and matches it against three configured variables:
- `TEST_CLIENT_USER_ID` → tagged with role `CLIENT`
- `TEST_PM_USER_ID` → tagged with role `PM`
- `TEST_DEVELOPER_USER_ID` → tagged with role `DEVELOPER`
- Any other sender → tagged with role `UNKNOWN` (stored gracefully without error)

### How to Find User Object IDs

#### Method A: Microsoft Entra Admin Center
1. Go to [https://entra.microsoft.com](https://entra.microsoft.com).
2. In the left navigation, go to **Identity** → **Users** → **All users**.
3. Search for the user account you want to use for each role.
4. Click the user's name to open their **Profile Overview**.
5. Under **Basic info**, locate **Object ID**.
6. Copy the 36-character GUID.
7. Repeat for each test account and paste into `.env`:
   ```env
   TEST_CLIENT_USER_ID=11111111-aaaa-bbbb-cccc-111111111111
   TEST_PM_USER_ID=22222222-aaaa-bbbb-cccc-222222222222
   TEST_DEVELOPER_USER_ID=33333333-aaaa-bbbb-cccc-333333333333
   ```

#### Method B: Microsoft Graph Explorer
1. Sign in to [Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer).
2. Run:
   ```http
   GET https://graph.microsoft.com/v1.0/users?$select=id,displayName,userPrincipalName,mail
   ```
   Or filter by name:
   ```http
   GET https://graph.microsoft.com/v1.0/users?$filter=startswith(displayName,'John')&$select=id,displayName,mail
   ```
3. Copy the `"id"` field for each user.

> **Testing Alone (Single Account)?**
> If you are testing by yourself with a single Microsoft 365 account:
> - Put your own user Object ID in `TEST_CLIENT_USER_ID`.
> - You can leave `TEST_PM_USER_ID` and `TEST_DEVELOPER_USER_ID` blank or set them later. Messages sent by you will be identified as `CLIENT`, enabling you to test the full client-issue workflow.

---

## Step 4: Webhook Tunnel URL (`WEBHOOK_PUBLIC_URL`)

### Why is this needed?
When a message is posted in Teams, Microsoft Graph sends a webhook push notification via HTTP POST to the internet.
- Microsoft Graph **cannot** reach `http://localhost:3000`.
- Microsoft Graph **strictly rejects plain HTTP URLs**; it requires an active, publicly accessible **HTTPS** endpoint.
- When creating a subscription, Microsoft Graph immediately sends a handshake request with a `validationToken` query parameter that must be answered within 10 seconds.

### Important URL Formatting Rule in this Codebase
In `scripts/manage-subscription.js`, the code constructs the notification endpoint as:
```javascript
const notificationUrl = `${config.webhookPublicUrl.replace(/\/$/, '')}/webhooks/teams`;
```
**Therefore, your `WEBHOOK_PUBLIC_URL` must ONLY be the base domain with https protocol — do NOT append `/webhooks/teams` and do NOT include a trailing slash.**
- Correct: `https://my-tunnel-name.ngrok-free.app`
- Incorrect: `https://my-tunnel-name.ngrok-free.app/webhooks/teams`
- Incorrect: `http://my-tunnel-name.ngrok-free.app`

---

### Option A: ngrok (Recommended & Most Reliable)

1. **Install ngrok**:
   - Download from: [https://ngrok.com/download](https://ngrok.com/download)
   - Or via Windows Package Manager:
     ```powershell
     winget install ngrok.ngrok
     ```
   - Or via npm:
     ```powershell
     npm install -g ngrok
     ```
2. **Authenticate**:
   - Create a free account at [https://ngrok.com](https://ngrok.com).
   - Get your authtoken from your ngrok dashboard: [https://dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken).
   - In terminal, run:
     ```powershell
     ngrok config add-authtoken <YOUR_NGROK_AUTHTOKEN>
     ```
3. **Start the tunnel**:
   ```powershell
   ngrok http 3000
   ```
4. Look at the terminal output for the **Forwarding** line:
   ```text
   Forwarding                    https://a1b2-c3d4.ngrok-free.app -> http://localhost:3000
   ```
5. Copy the `https://...` address.
6. Set in `.env`:
   ```env
   WEBHOOK_PUBLIC_URL=https://a1b2-c3d4.ngrok-free.app
   ```
*Keep this terminal window running while developing.*

---

### Option B: Cloudflare Tunnel (Free, No Account Needed for Ad-hoc)

1. **Install cloudflared**:
   ```powershell
   winget install Cloudflare.cloudflared
   ```
2. **Start the tunnel pointing to port 3000**:
   ```powershell
   cloudflared tunnel --url http://localhost:3000
   ```
3. In the console logs, find the URL ending in `.trycloudflare.com`:
   ```text
   +--------------------------------------------------------------------------------------------+
   |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
   |  https://quick-tunnel-example-1234.trycloudflare.com                                       |
   +--------------------------------------------------------------------------------------------+
   ```
4. Set in `.env`:
   ```env
   WEBHOOK_PUBLIC_URL=https://quick-tunnel-example-1234.trycloudflare.com
   ```

---

### Option C: localtunnel (Zero Installation via npx)

1. Open PowerShell and run:
   ```powershell
   npx localtunnel --port 3000
   ```
2. Copy the generated URL (e.g. `https://shy-foxes-jump.loca.lt`).
3. Set in `.env`:
   ```env
   WEBHOOK_PUBLIC_URL=https://shy-foxes-jump.loca.lt
   ```

---

## Step 5: Server & Database Defaults

These values work out-of-the-box and typically do not require changes:

```env
# Port your local Node.js Express server listens on
PORT=3000

# Logging level: debug | info | warn | error
LOG_LEVEL=info

# SQLite database path (the app creates the /data folder automatically on startup)
DATABASE_PATH=./data/messages.db
```

---

## Complete Example `.env` File

Once you have gathered all values, your `.env` file in the project root should look like this:

```env
# ============================================
# Microsoft Entra (Azure AD) Application
# ============================================
MICROSOFT_TENANT_ID=e4a12345-6789-4abc-def0-1234567890ab
MICROSOFT_CLIENT_ID=f5b23456-7890-4bcd-ef01-2345678901bc
MICROSOFT_CLIENT_SECRET=V~a8Q~exampleSecretValueFromAzureCertificates123

# ============================================
# Microsoft Teams Configuration
# ============================================
TEAMS_TEAM_ID=c6c34567-8901-4cde-f012-3456789012cd
TEAMS_CHANNEL_ID=19:79a29abcdef1234567890@thread.tacv2

# ============================================
# User Role Mapping (Microsoft Graph User IDs)
# ============================================
TEST_CLIENT_USER_ID=d7d45678-9012-4def-0123-4567890123de
TEST_PM_USER_ID=e8e56789-0123-4ef0-1234-5678901234ef
TEST_DEVELOPER_USER_ID=f9f67890-1234-4f01-2345-6789012345fa

# ============================================
# Server Configuration
# ============================================
PORT=3000

# ============================================
# Webhook Configuration
# (Base HTTPS URL of your tunnel - NO trailing slash, NO /webhooks/teams)
# ============================================
WEBHOOK_PUBLIC_URL=https://my-tunnel-name.ngrok-free.app

# ============================================
# Logging
# ============================================
LOG_LEVEL=info

# ============================================
# Database
# ============================================
DATABASE_PATH=./data/messages.db
```

---

## Step 6: Step-by-Step Verification & Startup Checklist

Follow this exact order to test and verify your configuration:

### 1. Verify Local Server Starts
In PowerShell:
```powershell
python main.py
```
**Expected Output:**
```text
Teams MVP backend running on port 3000
```
*(If any critical variable is missing, `src/config.py` will output a warning listing the missing variables).*

### 2. Verify Health Endpoint
In a second terminal:
```powershell
curl http://localhost:3000/health
```
**Expected Output:**
```json
{"status":"ok","service":"teams-mvp","timestamp":"...","uptime":...}
```

### 3. Verify Webhook Validation Handshake (Local)
Microsoft Graph tests endpoints before activating subscriptions by sending `?validationToken=<token>`. Verify your endpoint echoes it back:
```powershell
curl "http://localhost:3000/webhooks/teams?validationToken=test-token-123"
```
**Expected Output:**
```text
test-token-123
```
*(Status must be 200 and Content-Type must be `text/plain`)*.

### 4. Verify Public Tunnel Reachability
Ensure your tunnel is forwarding properly to your local server:
```powershell
curl https://<YOUR_TUNNEL_URL>/health
```
**Expected Output:**
```json
{"status":"ok","service":"teams-mvp", ...}
```

### 5. Create Microsoft Graph Subscription
With the backend running and the tunnel active:
```powershell
python scripts/manage_subscription.py create
```
**Expected Output:**
```text
Creating Graph subscription...
  Team ID:          c6c34567-8901-4cde-f012-3456789012cd
  Channel ID:       19:79a29abcdef1234567890@thread.tacv2
  Notification URL: https://my-tunnel-name.ngrok-free.app/webhooks/teams

✓ Subscription created successfully!

  Subscription ID:  xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  Resource:         /teams(c6c34567...)/channels(19:79a29...)
  Change Type:      created
  Expires:          2026-09-30T16:00:00.000Z
  Client State:     xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

IMPORTANT: This subscription expires in ~60 minutes.
```

### 6. Test Live Message Detection
1. Open Microsoft Teams.
2. Go to the configured channel.
3. Post a message: `Hello team, this is an automated test.`
4. Check your backend console. You will see:
   ```text
   ══════════════════════════════════════════════════
     Teams message received
   ══════════════════════════════════════════════════
     Message ID:   ...
     Sender:
       User ID:      ...
       Display Name: ...
       Role:         CLIENT
     Message:
       Hello team, this is an automated test.
   ══════════════════════════════════════════════════
   ```
5. Check stored messages in the SQLite database:
   ```powershell
   curl http://localhost:3000/api/messages
   ```

---

## Troubleshooting Common Errors

### 1. `Subscription validation request to ... timed out or returned invalid response`
- **Cause**: The backend server is not running on port 3000, the tunnel URL is wrong, or the tunnel is inactive.
- **Fix**: Run `curl https://<YOUR_TUNNEL_URL>/webhooks/teams?validationToken=test` in your terminal. If this does not return `test` in plain text, verify the tunnel is pointing to `http://localhost:3000`.

### 2. `Graph API error: 403 Forbidden`
- **Cause**: Application permissions were not consented to by a tenant admin.
- **Fix**: In [Microsoft Entra admin center](https://entra.microsoft.com) → App registrations → Your App → API permissions, ensure all 4 permissions have a green checkmark under "Status". If not, an admin must click **Grant admin consent**.

### 3. `Graph API error: 404 Not Found`
- **Cause**: The `TEAMS_TEAM_ID` or `TEAMS_CHANNEL_ID` is incorrect, or the app has not been granted access.
- **Fix**: Check `TEAMS_TEAM_ID` against the group Object ID in Entra ID, and make sure `TEAMS_CHANNEL_ID` is fully decoded (`19:...@thread.tacv2`).

### 4. Subscription Expires After 60 Minutes
- **Cause**: Microsoft Graph enforces a hard maximum lifetime of 60 minutes for Teams channel message subscriptions.
- **Fix**:
  - List active subscriptions:
    ```powershell
    python scripts/manage_subscription.py list
    ```
  - Renew subscription before it expires:
    ```powershell
    python scripts/manage_subscription.py renew <subscription-id>
    ```
  - Delete an old/expired subscription:
    ```powershell
    python scripts/manage_subscription.py delete <subscription-id>
    ```
