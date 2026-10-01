# Teams MVP Phase 1 — Setup Guide

This guide walks through every step to configure the Microsoft Entra application, obtain the required IDs, and prepare the local environment.

> 💡 **Detailed Credentials Walkthrough**: For an exhaustive, step-by-step walkthrough covering exact web portal navigation, URL decoding, and free tunnel setups for every variable in `.env`, see [GET_ALL_ENV_CREDENTIALS.md](GET_ALL_ENV_CREDENTIALS.md).

---

## 1. Create a Microsoft Entra Application

1. Go to the **Azure Portal**: [https://portal.azure.com](https://portal.azure.com)
2. Navigate to **Microsoft Entra ID** → **App registrations** → **New registration**
3. Fill in:
   - **Name**: `Jira Automation - Teams MVP` (or any descriptive name)
   - **Supported account types**: `Accounts in this organizational directory only` (single tenant)
   - **Redirect URI**: Leave blank (not needed for client credentials flow)
4. Click **Register**
5. Note the following from the **Overview** page:
   - **Application (client) ID** → this is your `MICROSOFT_CLIENT_ID`
   - **Directory (tenant) ID** → this is your `MICROSOFT_TENANT_ID`

## 2. Create a Client Secret

1. In your app registration, go to **Certificates & secrets** → **Client secrets** → **New client secret**
2. Set a description (e.g., `teams-mvp-dev`) and an expiry period
3. Click **Add**
4. **Copy the secret Value immediately** (it will not be shown again)
5. This is your `MICROSOFT_CLIENT_SECRET`

> ⚠️ **Never commit this secret to source control.**

## 3. Configure API Permissions

1. In your app registration, go to **API permissions** → **Add a permission**
2. Select **Microsoft Graph** → **Application permissions**
3. Add the following permissions:

| Permission | Type | Purpose |
|------------|------|---------|
| `ChannelMessage.Read.All` | Application | Read Teams channel messages |
| `Team.ReadBasic.All` | Application | Read team metadata |
| `Channel.ReadBasic.All` | Application | Read channel metadata |
| `User.Read.All` | Application | Read user profiles (for sender info) |

4. Click **Grant admin consent for [your org]**
5. Verify all permissions show a green ✓ status

> **Note**: `ChannelMessage.Read.All` is a **protected API** in Microsoft Graph. You may need to submit a request to Microsoft for approval, or your organization's admin may need to approve it. See [Microsoft's documentation on protected APIs](https://learn.microsoft.com/en-us/graph/teams-protected-apis).

## 4. Find Your Team ID

### Option A: Microsoft Graph Explorer

1. Go to [Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer)
2. Sign in with your Microsoft 365 account
3. Run: `GET https://graph.microsoft.com/v1.0/me/joinedTeams`
4. Find your team in the response and copy the `id` field

### Option B: Teams Desktop App

1. In Microsoft Teams, click the **⋯** menu next to the team name
2. Select **Get link to team**
3. The Team ID is in the URL: `groupId=<TEAM_ID>`

### Option C: PowerShell

```powershell
# Install the Microsoft Graph PowerShell module if needed
Install-Module Microsoft.Graph -Scope CurrentUser

Connect-MgGraph -Scopes "Team.ReadBasic.All"
Get-MgUserJoinedTeam -UserId "me" | Select-Object DisplayName, Id
```

The `Id` value is your `TEAMS_TEAM_ID`.

## 5. Find Your Channel ID

### Option A: Microsoft Graph Explorer

1. Run: `GET https://graph.microsoft.com/v1.0/teams/{TEAM_ID}/channels`
2. Find your channel and copy the `id` field

### Option B: Teams Desktop App

1. Right-click the channel → **Get link to channel**
2. Decode the URL — the Channel ID is in the URL parameter

### Option C: PowerShell

```powershell
Get-MgTeamChannel -TeamId "<TEAM_ID>" | Select-Object DisplayName, Id
```

The `Id` value is your `TEAMS_CHANNEL_ID`.

> **Note**: Channel IDs typically look like `19:xxxxx@thread.tacv2`

## 6. Find User IDs

To configure role mapping, you need the Microsoft Graph user IDs for your test users.

### Option A: Graph Explorer

```
GET https://graph.microsoft.com/v1.0/users?$filter=displayName eq 'Test Client'&$select=id,displayName,mail
```

### Option B: Azure Portal

1. Go to **Microsoft Entra ID** → **Users**
2. Search for the user
3. Copy the **Object ID** — this is the user's Graph user ID

### Option C: PowerShell

```powershell
Get-MgUser -Filter "displayName eq 'Test Client'" | Select-Object DisplayName, Id
```

## 7. Configure Environment Variables

1. Copy the example file:

```bash
cp .env.example .env
```

2. Fill in all values:

```env
# From step 1
MICROSOFT_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# From step 2
MICROSOFT_CLIENT_SECRET=your-secret-value-here

# From steps 4 and 5
TEAMS_TEAM_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TEAMS_CHANNEL_ID=19:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx@thread.tacv2

# From step 6
TEST_CLIENT_USER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TEST_PM_USER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TEST_DEVELOPER_USER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Your public tunnel URL (see step 8)
WEBHOOK_PUBLIC_URL=https://your-tunnel-url.example.com

PORT=3000
LOG_LEVEL=info
DATABASE_PATH=./data/messages.db
```

## 8. Set Up a Secure Tunnel (Local Development)

Microsoft Graph requires a **publicly accessible HTTPS URL** for webhook notifications. During local development, you need a secure tunnel to expose your local server.

### Common options:

| Tool | Notes |
|------|-------|
| [ngrok](https://ngrok.com) | Popular, free tier available |
| [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) | Free, production-grade |
| [VS Code Dev Tunnels](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/) | Built into VS Code |
| [localtunnel](https://github.com/localtunnel/localtunnel) | Open source, simple |

### Example with a generic tunnel:

```bash
# Start your tunnel pointing to your local server port
# The tool will provide a public HTTPS URL like:
# https://abc123.your-tunnel.example.com

# Set this URL in your .env:
WEBHOOK_PUBLIC_URL=https://abc123.your-tunnel.example.com
```

> **Important**: The tunnel URL must use HTTPS. HTTP will be rejected by Microsoft Graph.

## 9. Install Dependencies

```bash
pip install -r requirements.txt
```

## 10. Start the Backend

```bash
# Start server with auto-reload
python main.py

# Or via uvicorn directly
uvicorn src.app:app --host 0.0.0.0 --port 3000 --reload
```

You should see:

```
Teams MVP backend running on port 3000
```

## 11. Create the Graph Subscription

With the backend running and the tunnel active:

```bash
python scripts/manage_subscription.py create
```

Expected output:

```
Creating Graph subscription...
  Team ID:          xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  Channel ID:       19:xxxxx@thread.tacv2
  Notification URL: https://your-tunnel.example.com/webhooks/teams

✓ Subscription created successfully!

  Subscription ID:  xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  Resource:         /teams(xxxxxxxx)/channels(19:xxxxx)/messages
  Change Type:      created
  Expires:          2026-09-30T15:00:00.000Z
  Client State:     xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

IMPORTANT: This subscription expires in ~60 minutes.
```

## 12. Subscription Lifecycle

### Expiration
- Channel message subscriptions expire after a **maximum of 60 minutes**
- The backend does **not** auto-renew in Phase 1 (TODO for Phase 2)

### Manual Renewal

```bash
# List active subscriptions to get the ID
python scripts/manage_subscription.py list

# Renew a specific subscription (extends by 60 minutes)
python scripts/manage_subscription.py renew <subscription-id>
```

### Manual Deletion

```bash
python scripts/manage_subscription.py delete <subscription-id>
```

### Failure Handling
- If the tunnel URL changes, delete the old subscription and create a new one
- If Graph returns 403, check that admin consent was granted (step 3)
- If Graph returns 400 on creation, verify the team/channel IDs are correct

---

## Troubleshooting

### "ChannelMessage.Read.All requires a protected API request"
This permission requires approval. See [Protected APIs in Microsoft Graph](https://learn.microsoft.com/en-us/graph/teams-protected-apis).

### "Subscription validation failed"
- Ensure the backend is running
- Ensure the tunnel is active and forwarding to the correct port
- Ensure the notification URL uses HTTPS
- Check that the backend returns the `validationToken` as plain text within 10 seconds

### "403 Forbidden" on message retrieval
- Verify API permissions are of type **Application** (not Delegated)
- Verify admin consent has been granted
- Wait a few minutes after granting consent for propagation

### "404 Not Found" on message retrieval
- Verify the Team ID and Channel ID are correct
- The message may have been deleted before retrieval

### "429 Too Many Requests"
- Microsoft Graph is throttling your application
- The backend logs the retry-after header value
- Wait and retry
