:star: If you appreciate this integration, please consider giving it a star! Your support encourages me to continue improving and expanding this project. Thank you! :star:

# Actual Budget integration for Home Assistant

This is a custom integration for Home Assistant that allows you to track your Actual Budget data.

Note: It's a work in progress, it should work but it may have some bugs and breaking changes.

# Features

- Creates sensors for all Actual Budget account balances
- Creates sensors for all budgets, using the accumulated budget balance as the sensor state
- Adds previous/current month budget details as extra sensor attributes
- Adds a `last_sync` sensor so dashboards can show when the integration last refreshed
- Provides service actions to synchronize bank transactions or pull the latest budget file on demand

# Installation

## HACS

1. Go to HACS page
2. Search for `Actual Budget`
3. Install it

Note: If this is not in HACS yet, you can add this repository manually.

## Add The Repository Manually

1.
<img width="933" alt="SCR-20240830-lpfj" src="https://github.com/user-attachments/assets/b8ebd9ca-ccc2-4f60-a9a9-6cf69b71cafe">

2.
<img width="492" alt="SCR-20240830-lovt" src="https://github.com/user-attachments/assets/1ed50bff-77a2-46d4-9dc1-3aff97ee585a">

3. Restart Home Assistant, then add it from Settings > Devices & services > Add integration > Actual Budget.

# Configuration

## Add an Actual Budget account

1. Go to Settings > Devices & services.
2. Click Add integration.
3. Search for `Actual Budget`.
4. Enter the connection details for your Actual Budget server and budget file.
5. Click Submit. The integration validates the connection before creating the account.

Each configured account is uniquely identified by its Actual Budget endpoint and file id, so you can add multiple budget files as separate integration entries. After the account is added, Home Assistant creates sensors for every account and budget category found in that Actual Budget file.

| Setting | Required | Description |
| ------- | -------- | ----------- |
| Endpoint | Yes | The URL for the Actual Budget server, including the scheme and port when needed. |
| Password | Yes | The password for the Actual Budget server. |
| File | Yes | The file id of the Actual Budget file to load. |
| Unit | Yes | The currency symbol or unit to use for monetary sensors. Defaults to `€`. |
| Skip certificate validation | Yes | Leave disabled for normal HTTPS validation. Enable only for self-signed or otherwise untrusted certificates. |
| Cert | No | Optional certificate value to use for the connection. Leave empty when using normal validation or the skip-validation toggle. |
| Encrypt Password | No | The password to decrypt the Actual Budget file, if the file is encrypted. |
| Prefix | No | Optional prefix for created entity names and unique ids. Defaults to `actualbudget`. |

Example:

```yaml
Endpoint: https://localhost:5001
Password: password
Encrypt Password: ""
File: ab7c8d8e-048b-41b1-a9cf-13f0679edc0b
Unit: €
Skip certificate validation: true
Cert: ""
Prefix: actualbudget
```

## Certificate validation

Previous versions used `Cert: 'SKIP'` to bypass certificate checks. The add-account flow now has a dedicated Skip certificate validation checkbox. Use that checkbox instead of entering `SKIP` in the certificate field.

# Service actions

The integration registers two service actions that can be called from Home Assistant automations, scripts, or Developer Tools > Actions:

- `actualbudget.bank_sync`: downloads the latest transactions from linked bank accounts and inserts them into Actual Budget.
- `actualbudget.budget_sync`: pulls the latest budget file from the server and refreshes sensor entities.

Both actions require selecting the Actual Budget integration entry to run against.
