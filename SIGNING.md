# Code signing (removes the "Windows protected your PC" warning)

The build signs `CoverArt.exe` and the installer automatically once these GitHub secrets exist.
Until then it builds unsigned.

1. Create an Azure account (portal.azure.com) and a **Trusted Signing** (Artifact Signing) account, Basic plan.
2. In it, complete **Identity validation** as an individual (government ID). Approval can take a few days.
3. Create a **Certificate profile** (Public Trust).
4. Create an **App registration** in Microsoft Entra ID, add a client secret, and give it the
   "Trusted Signing Certificate Profile Signer" role on the signing account.
5. In GitHub: repo Settings > Secrets and variables > Actions, add:

| Secret | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID of the app registration |
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_CLIENT_SECRET` | The client secret's value |
| `AZURE_SIGNING_ENDPOINT` | e.g. `https://wus2.codesigning.azure.net/` (shown on the signing account) |
| `AZURE_SIGNING_ACCOUNT` | Signing account name |
| `AZURE_SIGNING_PROFILE` | Certificate profile name |
