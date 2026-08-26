export const API_BASE =
  import.meta.env.VITE_API_BASE ?? `http://${import.meta.env.VITE_SERVER_IP}:8000`;

export interface Session {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface StoredMessage {
  role: string;
  content: string;
  created_at: string;
}

export interface ContextUsage {
  session_id: number;
  used_tokens: number;
  output_tokens: number;
  max_tokens: number;
  reserved_tokens: number;
  model: string;
  /** last_turn = counted by Ollama on the real call; tokenizer = counted on
   *  demand with the same tokenizer; estimate = Ollama unreachable. */
  source: 'last_turn' | 'tokenizer' | 'estimate';
  exact: boolean;
}

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${input}`, init);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

const jsonBody = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  listSessions: () =>
    json<{ sessions: Session[]; active_id: number }>('/sessions'),

  createSession: (title?: string) =>
    json<Session>('/sessions', { method: 'POST', ...jsonBody({ title: title ?? null }) }),

  renameSession: (id: number, title: string) =>
    json<Session>(`/sessions/${id}`, { method: 'PATCH', ...jsonBody({ title }) }),

  deleteSession: (id: number) =>
    json<{ status: string; id: number; active_id: number }>(`/sessions/${id}`, { method: 'DELETE' }),

  activateSession: (id: number) =>
    json<{ active_id: number }>(`/sessions/${id}/activate`, { method: 'POST' }),

  conversation: (sessionId: number) =>
    json<{ messages: StoredMessage[]; session_id: number }>(`/conversation?session_id=${sessionId}`),

  clearConversation: (sessionId: number) =>
    json<{ status: string }>(`/conversation?session_id=${sessionId}`, { method: 'DELETE' }),

  contextUsage: (sessionId: number, refresh = false) =>
    json<ContextUsage>(`/context_usage?session_id=${sessionId}${refresh ? '&refresh=true' : ''}`),

  compact: (sessionId: number) =>
    json<{ compact: string }>(`/compact_conversation?session_id=${sessionId}`, { method: 'POST' }),
};
