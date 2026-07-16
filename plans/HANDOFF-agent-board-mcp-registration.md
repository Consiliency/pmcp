> **Inbound coordination hand-off** from `Consiliency/consiliency-portal` PR #210 (message-board delivery plane).
> This is a proposal/input for **PMCP** to ratify and turn into its own half of the roadmap. The frozen
> interfaces it must satisfy: **IF-0-MBPLANE-CRED-1** (descriptor-only credential slot) and
> **IF-0-MBPLANE-ENDPOINT-1** (board endpoint contract). Accept/adapt/reject as owner; changes to a frozen
> gate require re-ratification with the portal + message-board halves. Spine + full contract:
> `consiliency-portal:plans/unification/message-board-plane/CONTRACT-message-board-plane.md`.

> **✅ RESOLVED — GO-LIVE COMPLETE (2026-07-16).** This inbound proposal is preserved as the historical
> coordination record. It is now fully satisfied: send↔receive is proven end-to-end through the PMCP
> gateway with descriptor-only credentials. **The proposed env var names below were superseded** by what
> the shipped client actually reads — the authoritative, proven config is
> `plans/agent-board-overlay.template.jsonc`, and the closeout is in
> `plans/ROADMAP-pmcp-agent-board-mcp.md` (§ "GO-LIVE COMPLETE"). Pin: public npm
> `@consiliency/agent-board-mcp@1.2.2`; mode: `service_role` + Ed25519 signing.

# Proposal: register `@consiliency/agent-board-mcp` in PMCP

**For:** the agent driving PMCP (currently mid-change on namespaced credential/secrets resolution — this is written to *fit* that model, not fight it).
**Goal:** let any harness (Claude/Codex/Gemini/…) reach the Message Board through the PMCP gateway (`pmcp_provision` / catalog) instead of pasting messages by hand.
**Status:** PMCP has **zero** agent-board references today. `@consiliency/agent-board-mcp` is a ready stdio MCP server (`McpServer` + `StdioServerTransport`, `bin: agent-board-mcp`).

## The one hard constraint (why this needs *your* credential model, not a guess)
`agent-board-mcp` authenticates via a **credential descriptor** — a *pointer* (`descriptorType: "1password"`), resolved by the board runtime itself. Its tool contract states: **"tools must not accept raw private signing keys or signing secrets as input."** So the PMCP credential slot must inject a **descriptor reference**, never key material. That's exactly what your `${…}` namespaced credential-slot resolution should hand it.

## Proposed catalog entry (`.mcp.json` shape)
```jsonc
{
  "mcpServers": {
    "agent-board": {
      "command": "npx",
      "args": ["-y", "@consiliency/agent-board-mcp@<PINNED_VERSION>"],
      "env": {
        // --- non-secret config (safe to ship in the catalog) ---
        "MESSAGE_BOARD_RUNTIME_ENDPOINT": "https://<board-gateway-url>",
        "MESSAGE_BOARD_SUPABASE_URL": "https://<ref>.supabase.co",
        "MESSAGE_BOARD_RUNTIME_MODE": "embedded_mcp",     // runtime embedded in this MCP server (enum: standalone|embedded_mcp). "Gateway-only delivery" is a board-side enforcement property, not a runtime mode.
        "MESSAGE_BOARD_RUNTIME_TRUST_DOMAIN": "consiliency",
        "MESSAGE_BOARD_RUNTIME_BOARD_SCOPE": "<scope>",
        "MESSAGE_BOARD_RUNTIME_MACHINE_ID": "${machine_id}",  // per-host identity

        // --- CREDENTIAL SLOT: a DESCRIPTOR POINTER, resolved by your secrets manifest.
        //     This is a 1Password item reference, NOT a signing key. ---
        "MESSAGE_BOARD_RUNTIME_CREDENTIAL_DESCRIPTOR": "${cred:agent-board/credential-descriptor}",
        "MESSAGE_BOARD_AGENT_1PASSWORD_ITEM": "${cred:agent-board/op-item}"
      }
    }
  }
}
```

## Env contract (verified against `agent-board-runtime/src/config.ts`)
| Var | Kind | Notes |
|---|---|---|
| `MESSAGE_BOARD_RUNTIME_ENDPOINT` | config | the board gateway URL (see the **open dependency** below) |
| `MESSAGE_BOARD_SUPABASE_URL` | config | the board's Supabase URL |
| `MESSAGE_BOARD_RUNTIME_MODE` | config | `embedded_mcp` (enum: standalone|embedded_mcp) |
| `MESSAGE_BOARD_RUNTIME_TRUST_DOMAIN` / `_BOARD_SCOPE` | config | trust/scope binding |
| `MESSAGE_BOARD_RUNTIME_MACHINE_ID` | config | per-host id (`${machine_id}`) |
| `MESSAGE_BOARD_RUNTIME_CREDENTIAL_DESCRIPTOR` | **credential slot** | descriptor **pointer** (1Password), never a key |
| `MESSAGE_BOARD_AGENT_1PASSWORD_ITEM` | **credential slot** | 1Password item ref for the agent credential |
| `MESSAGE_BOARD_AGENT_REFRESH_TOKEN` | credential (optional) | if refresh-token flow is used; slot, redacted |

## Tool-scope / safety profile (default provision should be least-privilege)
The server self-describes three tiers — the catalog default should be the **safe** tier:
- **Safe (default):** bounded status, inbox, notification, runtime diagnostics, fetch descriptors — read-only, metadata-safe.
- **Supervisor:** requires explicit human/supervisor intent — do **not** auto-enable.
- **Admin:** requires a harness allowlist; must never print secrets — gate behind PMCP scope policy.

## Open dependency (blocks a *working* provision, not the catalog entry)
`MESSAGE_BOARD_RUNTIME_ENDPOINT` needs a **real running gateway URL**. Today there is none deployed (the self-host PR #201 stands up portal/workers/backstage but **not** the board). So: this catalog entry can be authored now, but a live end-to-end provision waits on (a) the board gateway being deployed, and (b) portal#208 migration drift reconciled.

## Asks for the PMCP agent
1. Accept an `agent-board` catalog entry with the two credential slots above, resolving **descriptor pointers only** (reject raw-key injection — matches the server's own rule).
2. Confirm the `${cred:namespace/key}` slot syntax that your current secrets rework expects, so I bind these correctly.
3. Confirm the default provision uses the **safe** tool tier (supervisor/admin gated by scope policy).
