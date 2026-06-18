# Azure Entra ID App Registration for DepthFusion

This runbook guides you through setting up DepthFusion as a registered application in Azure Entra ID (formerly Azure AD), configuring OIDC authentication, and preparing your test tenant.

## Prerequisites

- Access to an Azure subscription with permissions to register applications (Application Developer role minimum)
- Azure CLI installed and authenticated (`az login`)
- DepthFusion repository cloned locally
- Test tenant or ability to create one

## Register the Application

1. **Navigate to Azure Portal**
   - Go to `https://portal.azure.com`
   - Select **Azure Active Directory** (or search for it)

2. **Create a New App Registration**
   - Click **App registrations** in the left sidebar
   - Click **+ New registration**
   - Fill in the registration details:
     - **Name**: `DepthFusion`
     - **Supported account types**: `Accounts in this organizational directory only (Single tenant)`
     - **Redirect URI**: Platform: `Mobile and desktop applications`
       - Add two URIs:
         1. `http://localhost:8080/callback` (for local development)
         2. `depthfusion://auth/callback` (for deep linking / native apps)
   - Click **Register**

3. **Note the IDs**
   - Copy the **Application (client) ID** — this is your `DEPTHFUSION_ENTRA_CLIENT_ID`
   - Copy the **Directory (tenant) ID** — this is your `DEPTHFUSION_ENTRA_TENANT_ID`

## Configure PKCE

1. **Enable Public Client Flow**
   - In your app registration, go to **Authentication** in the left sidebar
   - Under **Advanced settings**, toggle **Allow public client flows** to **Yes**
   - Click **Save**

2. **Verify Redirect URIs**
   - Confirm both redirect URIs are listed under **Redirect URIs**
   - For mobile/desktop app types, PKCE is automatically enforced

## Group Claims

1. **Add Groups Claim to Tokens**
   - Go to **Token configuration** in the left sidebar
   - Click **+ Add groups claim**
   - Select **Security groups** (for testing with security groups)
   - Under **ID** and **Access** tokens, select which claims should include groups
   - Click **Add**

2. **Verify Configuration**
   - The token will now include a `groups` claim with object IDs of the user's group memberships

## Required API Permissions

1. **Add Microsoft Graph Permissions**
   - Go to **API permissions** in the left sidebar
   - Click **+ Add a permission**
   - Select **Microsoft Graph**
   - Choose **Delegated permissions**
   - Search for and add the following:
     - `openid` (already implicit)
     - `profile` (UserRead.All or profile scope)
     - `email` (already implicit)
     - `offline_access` (for refresh tokens)
     - `User.Read` (for user profile info)
   - Click **Add permissions**

2. **Grant Admin Consent** (if required)
   - Click **Grant admin consent for [Tenant]**
   - Confirm the action

## Test Tenant Setup

### Create a Test User

1. **Navigate to Users**
   - Go to **Azure Active Directory** > **Users** > **New user**
   - Fill in:
     - **User principal name**: `testuser@yourtenant.onmicrosoft.com`
     - **Display name**: `Test User`
     - **Password**: Auto-generate or set your own (note: you must change on first login if auto-generated)
   - Click **Create**

2. **Set a Permanent Password** (optional, for automation)
   - Go to the user's profile
   - Click **Reset password**
   - Un-check **Reset password on next sign-in** if you want a permanent test password
   - Copy the temporary password and save it

### Create a Test Group and Add User

1. **Create Security Group**
   - Go to **Azure Active Directory** > **Groups** > **New group**
   - Fill in:
     - **Group type**: `Security`
     - **Group name**: `depthfusion-users`
     - **Group description**: `Test group for DepthFusion OIDC testing`
   - Click **Create**

2. **Add Test User to Group**
   - Open the newly created group
   - Click **Members** > **+ Add members**
   - Search for and select `testuser@yourtenant.onmicrosoft.com`
   - Click **Select**

3. **Note the Group Object ID**
   - From the group's overview page, copy the **Object ID**
   - This is useful for testing group claims in tokens

## Environment Variables

Create a file at `tests/fixtures/entra/test-tenant.env` with the following variables (copy from `test-tenant.env.example` and fill in your values):

```bash
# Your Azure Entra Tenant ID
DEPTHFUSION_ENTRA_TENANT_ID=your-tenant-id-here

# Your registered application's client ID
DEPTHFUSION_ENTRA_CLIENT_ID=your-client-id-here

# Microsoft Entra ID authority endpoint
DEPTHFUSION_ENTRA_AUTHORITY=https://login.microsoftonline.com/your-tenant-id-here

# JWKS (JSON Web Key Set) endpoint for token validation
DEPTHFUSION_JWKS_URI=https://login.microsoftonline.com/your-tenant-id-here/discovery/v2.0/keys

# Token audience (typically the client ID)
DEPTHFUSION_ENTRA_AUDIENCE=api://your-client-id-here

# Token issuer URL (for JWT validation)
DEPTHFUSION_TOKEN_ISSUER=https://sts.windows.net/your-tenant-id-here/

# Test user UPN
DEPTHFUSION_TEST_USER_UPN=testuser@yourtenant.onmicrosoft.com

# Test group name or object ID
DEPTHFUSION_TEST_USER_GROUP=depthfusion-users
```

**Do not commit this file to git.** It contains tenant-specific secrets. Add it to `.gitignore`.

## Troubleshooting

### Token Validation Fails
- **Problem**: JWT signature validation fails
- **Solution**: Ensure `DEPTHFUSION_JWKS_URI` is correct and publicly accessible. Verify the JWKS contains the key referenced in the token's `kid` header.

### Redirect URI Not Recognized
- **Problem**: Authorization flow redirects to an error page
- **Solution**: Double-check the exact redirect URIs in the app registration match what your client is sending (case-sensitive, including trailing slashes).

### Groups Claim Not in Token
- **Problem**: User is in a group but the `groups` claim is missing from the token
- **Solution**: 
  1. Verify the group claim is configured in **Token configuration**
  2. Ensure the user is actually a member of the group (check in **Members** tab)
  3. For large numbers of groups, groups claims may need group assignment or filtering

### PKCE Validation Error
- **Problem**: Authorization server rejects `code_challenge` parameter
- **Solution**: Ensure you've enabled **Allow public client flows** in the app's **Authentication** settings.

### Scope Not Granted
- **Problem**: Authorization fails with "Requested scopes exceed granted consent"
- **Solution**: Grant admin consent in **API permissions** or have the test user consent manually during the first auth flow.

## Next Steps

- Implement OIDC client code to exchange authorization codes for tokens
- Store tokens securely (avoid localStorage in web apps; use HTTP-only cookies)
- Validate JWTs using the JWKS endpoint
- Implement token refresh using `offline_access` scope if long-lived sessions are needed
