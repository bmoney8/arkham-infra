# Buzz Desktop Windows Configuration
> Path traversal reference for WSL2 accessing Windows Buzz Desktop config.

## Windows Config Locations

Buzz Desktop stores its configuration in the user's AppData directory:
- Windows path: `C:\Users\<username>\AppData\Roaming\buzz-desktop\`
- WSL2 equivalent: `/mnt/c/Users/<username>/AppData/Roaming/buzz-desktop/`

## Key Config Files

| File | Purpose |
|------|---------|
| `config.json` | Application configuration |
| `keys.json` | Nostr key pairs (ENCRYPTED — never plaintext in git) |
| `relay.json` | Relay connection settings |

## WSL2 Path Traversal

From WSL2, access Windows AppData via the /mnt/c mount:
```
/mnt/c/Users/bryce/AppData/Roaming/buzz-desktop/
```

**Critical:** WSL2 cannot see Windows loopback (127.0.0.1). Services bound to
127.0.0.1 on Windows are invisible from WSL2. Use the mesh IP (100.64.0.2)
or the WSL2 gateway IP for cross-boundary communication.

## Pubkey Configuration

For V5.5 Buzz membership signing, use the **Bryce user pubkey**:
- Pubkey: `9304ee222609977e56e9c00e7c1e0ddb816f7acec4245520bbc45aeb51d5c6d7`
- This is the human operator key, NOT agent keys (makima/jeeves/light/etc.)
- All channel join/membership operations MUST use this pubkey for authentication

## Primary Buzz Channel

Channel ID: `eb9d7de2ea81ec4953a3aabdb157c1a21a5c6910bc65f510cde69418f8eb649b`
