# Failure mode analysis

At least three failure scenarios the system handles **without crashing the batch**, with **logged** behavior.

## 1. Transient tool timeout (`get_order`)

**Scenario:** The mock order service raises `TimeoutError` once (when `AGENT_SIMULATE_FAULTS` is enabled).

**Response:** `with_retry` applies exponential backoff with jitter and retries up to **3** attempts. On success, the audit step records the winning attempt number. If all attempts fail, the step is marked `failed` and processing follows escalation / exception handling.

**Evidence:** `agent/retry.py`, `agent/tools.py` (`get_order`), audit steps with `status: failed` or successful retry.

## 2. Malformed / partial tool payload (knowledge base)

**Scenario:** The first `search_knowledge_base` call returns an invalid shape (e.g. missing `answer` key) when fault simulation is on.

**Response:** `_validate_tool_output` marks the result `invalid_output`, `_call_tool` retries; a subsequent call returns a valid `{ "answer": "..." }` dict. This demonstrates **schema validation before acting**.

**Evidence:** `agent/tools.py` (`search_knowledge_base`), `agent/processor.py` (`_validate_tool_output`).

## 3. Non-retriable refund amount mismatch (`issue_refund`)

**Scenario:** `issue_refund(order_id, amount)` is invoked with an amount that does not match the order total (simulated integrity check before the irreversible write).

**Response:** The tool raises **`PermanentToolFailure`** (not in the retry set for transient errors). The processor escalates with a structured summary instead of looping forever or mutating bad data.

**Evidence:** `agent/tools.py` (`PermanentToolFailure`, `issue_refund`), `agent/processor.py` (`except PermanentToolFailure`).

## 4. Escalation transport failure → dead letter (bonus)

**Scenario:** The `escalate` tool throws after retries (e.g. persistent outage — not enabled in the default mock).

**Response:** The ticket is recorded in `logs/dead_letter.json` with error and summary; result status `dead_lettered`.

**Evidence:** `agent/processor.py` (`_escalate_with_summary`, `dead_letter`).
