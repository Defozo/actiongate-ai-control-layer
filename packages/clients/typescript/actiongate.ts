/** Transport-only client. This library never grants authority or modifies a policy. */
export type Operation = {
  id: string; run_id: string; tool: string; decision: 'allow' | 'redact' | 'block' | 'require_approval';
  status: string; settlement_status: string; payload_hash: string; label: string;
  rule_ids: string[]; policy_generation: number; result?: unknown;
};
export class ActionGateError extends Error {
  readonly status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
export class ActionGate {
  #token: string;
  readonly baseUrl: string;
  readonly runId: string;
  constructor(baseUrl: string, workloadToken: string, runId: string) { this.baseUrl = baseUrl; this.#token = workloadToken; this.runId = runId; }
  private async request<T>(path: string, body?: unknown, key?: string): Promise<T> {
    const response = await fetch(`${this.baseUrl.replace(/\/$/, '')}${path}`, {
      method: body === undefined ? 'GET' : 'POST', redirect: 'error',
      headers: { Authorization: `Bearer ${this.#token}`, Accept: 'application/json',
        'X-ActionGate-Run-Id': this.runId, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(key ? { 'Idempotency-Key': key } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new ActionGateError(response.status, typeof payload.detail === 'string' ? payload.detail : 'Controlled request was rejected');
    return payload as T;
  }
  action(tool: string, arguments_: Record<string, unknown>, idempotencyKey = crypto.randomUUID()): Promise<Operation> {
    return this.request('/actions', { run_id: this.runId, tool, arguments: arguments_, idempotency_key: idempotencyKey });
  }
  operation(id: string): Promise<Operation> { return this.request(`/actions/${encodeURIComponent(id)}`); }
  chat(messages: { role: 'user' | 'assistant' | 'system'; content: string }[], options: { model?: string; max_tokens?: number; tools?: unknown[]; idempotencyKey?: string } = {}) {
    const { idempotencyKey, ...requestOptions } = options;
    return this.request('/v1/chat/completions', { model: 'local-business', max_tokens: 512, stream: false, ...requestOptions, messages }, idempotencyKey ?? crypto.randomUUID());
  }
}
