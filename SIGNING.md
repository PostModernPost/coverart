# Code signing (removes the "Windows protected your PC" / unknown publisher warning)

The build signs `LinerNotes.exe` and the installer automatically once the GitHub secrets below exist.
Until then it builds unsigned. Signing uses Microsoft's Azure Artifact Signing (formerly Trusted
Signing): $9.99/month on the Basic plan, open to individuals in the US and Canada.

1. Make an Azure account at portal.azure.com, with a billing account of type **Individual** whose
   legal name and address match your government ID (Artifact Signing reads them from there).
2. Subscriptions > your subscription > Resource providers: register **Microsoft.CodeSigning**.
3. Create an **Artifact Signing account** (Basic plan, a US region such as West US 2 or East US).
4. Give yourself the **Artifact Signing Identity Verifier** role on it, then create an
   **Identity validation** request (Individual). It takes 1 to 20 business days; watch for emails.
5. Once approved, create a **Certificate profile** of type **Public Trust** using that identity.
6. In Microsoft Entra ID, create an **App registration**, add a client secret, and on the signing
   account give it the **Artifact Signing Certificate Profile Signer** role.
7. In GitHub: repo Settings > Secrets and variables > Actions, add:

| Secret | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID of the app registration |
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_CLIENT_SECRET` | The client secret's value |
| `AZURE_SIGNING_ENDPOINT` | The signing account's endpoint, e.g. `https://wus2.codesigning.azure.net/` |
| `AZURE_SIGNING_ACCOUNT` | Signing account name |
| `AZURE_SIGNING_PROFILE` | Certificate profile name |

The next release is then signed. The client secret expires (you choose 6 to 24 months):
make a new one and update `AZURE_CLIENT_SECRET` before then.
