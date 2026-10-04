# Code Execution with PMCP

## Overview

PMCP (Progressive MCP) is designed to reduce context bloat and enable efficient workflows through **code execution patterns**. Instead of making many individual tool calls that pass results through your context window, write code to orchestrate tools.

**About the examples.** `mcp.call_tool(name, arguments)` stands for whatever your execution environment uses to call an MCP tool; here it returns the gateway tool's JSON result as a dict. The downstream tool IDs and arguments (`gdrive::getSheet`, `github::listIssues`, ...) are illustrative: the real ones depend on which servers are connected, so find them with `gateway.catalog_search` and check their arguments with `gateway.describe`.

## What `gateway.invoke` returns

`gateway.invoke` does not hand back the downstream result directly. It returns an envelope:

```json
{
  "tool_id": "server::tool",
  "ok": true,
  "result": {"content": [{"type": "text", "text": "..."}], "isError": false},
  "truncated": false,
  "summary": null,
  "raw_size_estimate": 1234,
  "errors": null
}
```

- **Failures are returned, not raised.** An unknown tool, missing required arguments, a timeout or an offline server comes back as `"ok": false`, with `errors` holding strings. Almost all are JSON objects carrying `code` (e.g. `E301`, `E303`, `E304`), `message`, `suggestion` and `retryable`; one path (a downstream that requires URL-mode elicitation) returns the plain message `URL-mode elicitation required.`, so parse each entry defensively. A `try`/`except` around the call alone will not see them. (A call that breaks `gateway.invoke`'s own input schema, such as a missing `tool_id`, is refused as an MCP tool error before any envelope is built.)
- **`result` is the downstream `tools/call` result**: `content` blocks, `structuredContent` when the server provides it, and `isError`. A tool that reports its own failure gives `"ok": true` with `"isError": true` in `result`.
- **Large output is truncated.** The default limit is the policy's `max_output_bytes` (50 KB unless configured). `options.max_output_chars` sets a per-call budget of 4 bytes per character. When `truncated` is true, `result` may be a string rather than parsed JSON, and `summary` describes what was cut.
- **Task invocations** (the `task` argument) return `result: null` and a `task` record; fetch the outcome with `gateway.tasks_get` and `gateway.tasks_result`.

The examples below use this helper, which turns both kinds of failure into exceptions:

```python
def invoke(tool_id, arguments=None, options=None):
    request = {"tool_id": tool_id, "arguments": arguments or {}}
    if options:
        request["options"] = options  # e.g. {"timeout_ms": 60000, "max_output_chars": 20000}
    out = mcp.call_tool("gateway.invoke", request)
    if not out["ok"]:
        raise RuntimeError(out["errors"])
    result = out["result"]
    if isinstance(result, dict):
        if result.get("isError"):
            raise RuntimeError(result.get("content"))
        return result.get("structuredContent", result)
    return result  # a string when truncation broke the JSON
```

## Why Code Execution?

### 1. Context Efficiency
**Problem**: Direct tool calls load all results into context.
```python
# ❌ Without code execution - all rows flow through context
result = mcp.call_tool("gateway.invoke", {
    "tool_id": "gdrive::getSheet",
    "arguments": {"sheetId": "abc123"}
})
# 10,000 rows now in context!
```

**Solution**: Filter and transform in your execution environment.
```python
# ✅ With code execution - filter before returning
sheet = invoke("gdrive::getSheet", {"sheetId": "abc123"})
pending = [row for row in sheet["rows"] if row["Status"] == "pending"]
print(f"Found {len(pending)} pending items")  # Only summary in context
```

### 2. Batch Operations
**Problem**: Chaining individual tool calls is slow and verbose.

**Solution**: Use loops to process multiple items efficiently.
```python
# ✅ Batch process URLs
urls = ["https://example.com", "https://github.com", "https://anthropic.com"]
screenshots = []

for i, url in enumerate(urls):
    invoke("playwright::browser_navigate", {"url": url})
    invoke("playwright::browser_take_screenshot", {"filename": f"shot_{i}.png"})
    screenshots.append(f"shot_{i}.png")

print(f"Captured {len(screenshots)} screenshots")
```

### 3. Control Flow
**Problem**: Conditionals and error handling via tool calls is awkward.

**Solution**: Use familiar code patterns.
```python
# ✅ Conditional logic in code
try:
    issue = invoke("github::getIssue", {"issueId": "123"})

    if issue.get("state") == "closed":
        print("Issue already closed")
    else:
        invoke("github::closeIssue", {"issueId": "123"})
except RuntimeError as e:
    print(f"Error: {e}")
    # Fallback logic
```

### 4. Privacy & Security
**Problem**: Sensitive data flows through context window.

**Solution**: Process data in execution environment without exposing it.
```python
# ✅ Sensitive data stays in execution environment
customers = invoke("gdrive::getSheet", {"sheetId": "customer-data"})["rows"]

# Process PII without loading into context
for customer in customers:
    invoke("salesforce::updateLead", {
        "leadId": customer["id"],
        "email": customer["email"],  # Never printed
        "phone": customer["phone"]   # Never printed
    })

print(f"Updated {len(customers)} customer records")  # Only summary visible
```

To keep secrets out of what a tool returns, pass `"options": {"redact_secrets": true}` to `gateway.invoke`.

## Progressive Disclosure Methodology

PMCP uses a 4-layer approach to minimize context consumption:

### Layer 0: MCP Instructions
When you connect to PMCP, its server instructions list the connected servers' capabilities and, unless replaced by an operator's `custom_instructions`, this workflow guidance:
```
Workflow: catalog_search → describe → invoke.

When to use this gateway:
• Web scraping, search, or data extraction
• Browser automation or testing
• ...

Use gateway.request_capability("<what you need>") first; PMCP may return direct CLI guidance for a local tool or an MCP server candidate to provision.
```

### Layer 1: Search for Capabilities
Use `gateway.catalog_search` to find tools:
```python
found = mcp.call_tool("gateway.catalog_search", {
    "query": "browser automation"
})
```

It returns compact capability cards in `results`, plus `total_available`, `truncated`, and any `cli_hints`, `registry_candidates` and (with `include_offline: true`) `manifest_candidates`. Each card can carry a **code hint**:
```json
{
  "results": [
    {
      "tool_id": "playwright::browser_navigate",
      "short_description": "Navigate to a URL",
      "availability": "online",
      "code_hint": "loop"
    }
  ]
}
```

The hints are `loop`, `filter`, `if/else`, `try` and `poll`. A card has none when no pattern matches or hints are turned off.

### Layer 2: Get Tool Details
Use `gateway.describe` to see full schema:
```python
card = mcp.call_tool("gateway.describe", {
    "tool_id": "playwright::browser_navigate"
})
```

It returns the tool's `args` (name, type, required, description), `output_schema`, `annotations`, `constraints`, `safety_notes` and an `invoke_template` showing the `gateway.invoke` call. At guidance level `standard` it also includes a short `code_snippet` for tools that have one:
```python
# Navigate to multiple URLs
for url in urls:
    mcp.call_tool("gateway.invoke", {"tool_id": "playwright::browser_navigate", "arguments": {"url": url}})
```

### Layer 3: Full Methodology Guide
You're reading it! This guide is the MCP resource `pmcp://guidance/code-execution`, and it is only loaded when you read it.

## Common Patterns

### Pattern 1: Batch Processing with Loops
**When**: Operating on multiple items (URLs, files, records)
**Code hint**: "loop"

```python
items = ["item1", "item2", "item3"]
results = []

for item in items:
    results.append(invoke("server::tool_name", {"input": item}))

print(f"Processed {len(results)} items successfully")
```

### Pattern 2: Filtering and Transformation
**When**: Working with large datasets
**Code hint**: "filter"

```python
# Get all data
all_data = invoke("database::query", {"query": "SELECT * FROM orders"})["rows"]

# Filter locally (don't load all into context)
pending_orders = [
    order for order in all_data
    if order["status"] == "pending" and order["amount"] > 100
]

# Only show summary
print(f"Found {len(pending_orders)} high-value pending orders")
print(pending_orders[:5])  # Preview first 5
```

### Pattern 3: Conditional Logic
**When**: Decisions based on tool results
**Code hint**: "if/else"

```python
status = invoke("server::getStatus", {"id": "123"})

if status["is_running"]:
    print("Already running, skipping...")
else:
    invoke("server::start", {"id": "123"})
    print("Started successfully")
```

### Pattern 4: Error Handling
**When**: Tools might fail
**Code hint**: "try"

```python
failed = []
succeeded = []

for item in items:
    try:
        invoke("server::process", {"item": item})
        succeeded.append(item)
    except RuntimeError as e:  # raised by invoke() for ok=false or isError
        failed.append({"item": item, "error": str(e)})

print(f"Success: {len(succeeded)}, Failed: {len(failed)}")
if failed:
    print("Failed items:", failed)
```

### Pattern 5: Polling and Retry
**When**: Waiting for async operations
**Code hint**: "poll"

`gateway.provision` returns at once. Its `status` is `already_running`, `complete`, `failed`, or `started` with a `job_id` to poll:

```python
import time

job = mcp.call_tool("gateway.provision", {"server_name": "github"})

if job["status"] == "started":
    max_attempts = 30
    for attempt in range(max_attempts):
        status = mcp.call_tool("gateway.provision_status", {"job_id": job["job_id"]})

        if status["status"] == "complete":
            print("Provisioning complete!")
            break
        if status["status"] in ("failed", "timeout", "not_found"):
            print(f"Provisioning failed: {status.get('error') or status['message']}")
            break

        print(f"Waiting... {status['progress']}% ({attempt + 1}/{max_attempts})")
        time.sleep(2)
elif not job["ok"]:
    print(job["message"])  # e.g. missing credentials: see job["next_step"]
```

## Best Practices

### 1. Start with Search
Always use `gateway.catalog_search` before invoking tools:
```python
# ✅ Good: Discover first
found = mcp.call_tool("gateway.catalog_search", {"query": "screenshot"})
tool_id = found["results"][0]["tool_id"]

# ❌ Bad: Hardcode tool IDs
# tool_id = "playwright::browser_take_screenshot"  # Might not exist!
```

### 2. Filter Early
Don't load large datasets into context:
```python
# ✅ Good: Filter in execution environment
data = get_large_dataset()
filtered = [x for x in data if x["matches_criteria"]]
print(f"Found {len(filtered)} matches")
print(filtered[:5])  # Show sample

# ❌ Bad: Load everything into context
print(data)  # 10,000 items dumped to context!
```

### 3. Use Descriptive Summaries
When you process data, return human-readable summaries:
```python
# ✅ Good: Informative summary
print(f"Processed {total} orders: {success} succeeded, {failed} failed")
print(f"Revenue: ${total_revenue:.2f}")

# ❌ Bad: Raw data dump
print(all_orders)  # Bloats context
```

### 4. Handle Errors Gracefully
Check `ok` (or use the `invoke()` helper above, which raises on failure):
```python
# ✅ Good: Robust error handling
try:
    result = invoke("api::call", params)
except RuntimeError as e:
    print(f"API call failed: {e}")
    # Fallback or retry logic
```

### 5. Leverage Lazy Loading
Use `gateway.describe` only when you need detailed schemas:
```python
# ✅ Good: Progressive disclosure
search_results = mcp.call_tool("gateway.catalog_search", {"query": "github"})
# ... review results ...
# Only describe when needed:
schema = mcp.call_tool("gateway.describe", {"tool_id": "github::createIssue"})
```

## Configuration

The operator controls guidance in `~/.claude/gateway-guidance.yaml`:

```yaml
guidance:
  level: "minimal"  # Options: "off", "minimal", "standard"
```

The level decides which layers are on, and it overrides any per-layer `layers:` settings in the file:

| Level | L0 instructions | L1 code hints | L2 code snippets | L3 this guide |
|-------|-----------------|---------------|------------------|---------------|
| `off` | off | off | off | off |
| `minimal` (default) | on | on | off | on |
| `standard` | on | on | on | on |

`pmcp guidance --show-budget` prints the current settings and an estimated token cost: about 230 tokens for the instructions plus one 15-card search at `minimal`, and about 60 more per `describe` at `standard`.

## Examples

### Example 1: Screenshot Multiple Websites

```python
urls = [
    "https://anthropic.com",
    "https://github.com",
    "https://claude.ai"
]

screenshots = []
for i, url in enumerate(urls):
    print(f"Capturing {url}...")
    invoke("playwright::browser_navigate", {"url": url})
    invoke("playwright::browser_take_screenshot", {"filename": f"screenshot_{i}.png"})
    screenshots.append({"url": url, "file": f"screenshot_{i}.png"})

print(f"✓ Captured {len(screenshots)} screenshots")
```

### Example 2: Sync GitHub Issues to Notion

```python
# Get open GitHub issues
issues = invoke("github::listIssues", {
    "repo": "example-org/example-repo",
    "state": "open"
})["issues"]

# Filter high-priority
high_priority = [
    issue for issue in issues
    if "priority: high" in issue.get("labels", [])
]

# Create Notion pages
created = 0
for issue in high_priority:
    try:
        invoke("notion::createPage", {
            "title": issue["title"],
            "content": issue["body"],
            "properties": {
                "GitHub URL": issue["url"],
                "Status": "Open"
            }
        })
        created += 1
    except RuntimeError as e:
        print(f"Failed to create page for issue #{issue['number']}: {e}")

print(f"✓ Created {created}/{len(high_priority)} Notion pages")
```

### Example 3: Analyze Documentation Coverage

```python
# Get all TypeScript files
files = invoke("filesystem::listFiles", {"path": "./src", "pattern": "*.ts"})["files"]

# Analyze each file
undocumented = []
for file_path in files:
    content = invoke("filesystem::readFile", {"path": file_path})["text"]

    # Check for JSDoc comments (simple heuristic)
    has_docs = "/**" in content

    if not has_docs:
        undocumented.append(file_path)

print(f"Documentation coverage: {len(files) - len(undocumented)}/{len(files)} files")
if undocumented:
    print(f"Missing docs: {len(undocumented)} files")
    print(undocumented[:10])  # Show first 10
```

## Summary

**Key Principles**:
1. **Write code** to orchestrate tools instead of chaining tool calls
2. **Filter early** to keep large datasets out of context
3. **Use loops** for batch operations
4. **Check `ok`**: `gateway.invoke` returns failures instead of raising them
5. **Return summaries** instead of raw data dumps

**Progressive Disclosure**:
- L0: Workflow guidance in the server instructions
- L1: Code hints in search results
- L2: Code snippets in `describe` (level `standard` only)
- L3: This guide (`pmcp://guidance/code-execution`, read on demand)

**Token Budget**: about 230 tokens at `minimal`, plus about 60 per `describe` at `standard`. That is far less than loading every tool schema up front.

Happy orchestrating! 🎵
