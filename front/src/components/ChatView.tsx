import { useEffect, useRef, useState } from 'react';
import { API_BASE, api, type ContextUsage, type StoredMessage } from '../api';
import ContextMeter from './ContextMeter';

export interface Message {
  id: string;
  text: string;
  thinking?: string;
  sender: 'user' | 'bot';
  timestamp: Date;
}

const GREETING = 'Louvado seja o Omnissiah. Como posso auxiliar?';

const greetingMessage = (): Message => ({
  id: `greet-${Date.now()}`,
  text: GREETING,
  sender: 'bot',
  timestamp: new Date(),
});

const fromStored = (m: StoredMessage): Message => ({
  id: `${m.created_at}-${m.role}-${m.content.length}`,
  text: m.content,
  sender: m.role === 'user' ? 'user' : 'bot',
  timestamp: new Date(m.created_at.replace(' ', 'T')),
});

const ThinkingBlock = ({ content }: { content: string }) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="thinking-block">
      <button className="thinking-toggle" onClick={() => setOpen(o => !o)}>
        <span className="thinking-icon">{open ? '▾' : '▸'}</span>
        <span>Cogitando…</span>
      </button>
      {open && <pre className="thinking-content">{content}</pre>}
    </div>
  );
};

interface Props {
  sessionId: number | null;
  sessionTitle: string;
  onActivity: () => void;
  onToggleSidebar: () => void;
}

const ChatView = ({ sessionId, sessionTitle, onActivity, onToggleSidebar }: Props) => {
  const [messages, setMessages] = useState<Message[]>([greetingMessage()]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isAudio, setIsAudio] = useState(false);
  const [usage, setUsage] = useState<ContextUsage | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Load the selected session's history and its real token usage.
  useEffect(() => {
    if (sessionId === null) return;
    let cancelled = false;
    setUsage(null);
    api.conversation(sessionId)
      .then(data => {
        if (cancelled) return;
        setMessages(data.messages.length ? data.messages.map(fromStored) : [greetingMessage()]);
      })
      .catch(err => console.warn('Falha ao carregar conversa:', err));
    api.contextUsage(sessionId)
      .then(u => { if (!cancelled) setUsage(u); })
      .catch(err => console.warn('Falha ao medir contexto:', err));
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const refreshUsage = async () => {
    if (sessionId === null) return;
    setRefreshing(true);
    try {
      setUsage(await api.contextUsage(sessionId, true));
    } catch (err) {
      console.warn('Falha ao recontar contexto:', err);
    } finally {
      setRefreshing(false);
    }
  };

  const handleClear = async () => {
    if (sessionId === null) return;
    await api.clearConversation(sessionId).catch(err => console.warn('Falha ao limpar:', err));
    setMessages([greetingMessage()]);
    onActivity();
    refreshUsage();
  };

  const runCompact = async () => {
    if (sessionId === null) return;
    setIsLoading(true);
    try {
      const data = await api.compact(sessionId);
      setMessages([{
        id: Date.now().toString(),
        text: `${data.compact}\n\n— Conversa compactada —`,
        sender: 'bot',
        timestamp: new Date(),
      }]);
      onActivity();
      refreshUsage();
    } catch (err) {
      console.warn('[chat] compact error:', err);
    } finally {
      setInputValue('');
      setIsLoading(false);
    }
  };

  const send = async () => {
    const text = inputValue.trim();
    if (!text || isLoading || sessionId === null) return;

    if (text === '/compact') {
      await runCompact();
      return;
    }

    setMessages(prev => [...prev, {
      id: Date.now().toString(), text, sender: 'user', timestamp: new Date(),
    }]);
    setInputValue('');
    setIsLoading(true);

    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), 10 * 60 * 1000);

    try {
      const response = await fetch(`${API_BASE}/stream_message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, audio: isAudio, session_id: sessionId }),
        signal: abortController.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const botMessageId = `bot-${Date.now()}`;
      setMessages(prev => [...prev, {
        id: botMessageId, text: '', sender: 'bot', timestamp: new Date(),
      }]);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let streamDone = false;
      let sawUsage = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const parsed = JSON.parse(line.slice(6));
            if (parsed.type === 'usage' && parsed.usage) {
              sawUsage = true;
              setUsage(prev => ({
                session_id: sessionId,
                used_tokens: parsed.usage.input_tokens,
                output_tokens: parsed.usage.output_tokens ?? 0,
                max_tokens: parsed.usage.context_window ?? prev?.max_tokens ?? 32768,
                reserved_tokens: prev?.reserved_tokens ?? 0,
                model: parsed.usage.model ?? prev?.model ?? '',
                source: 'last_turn',
                exact: true,
              }));
              continue;
            }
            if (parsed.done) { streamDone = true; break; }
            if (!parsed.content) continue;

            setMessages(prev => prev.map(msg => {
              if (msg.id !== botMessageId) return msg;
              return parsed.type === 'thinking'
                ? { ...msg, thinking: (msg.thinking ?? '') + parsed.content }
                : { ...msg, text: msg.text + parsed.content };
            }));
          } catch { /* skip malformed */ }
        }
        if (streamDone) break;
      }
      onActivity();
      // The backend only reports usage when the provider hands it over; if it
      // did not, recount so the meter never shows a stale number.
      if (!sawUsage) refreshUsage();
    } catch (error) {
      const isTimeout = error instanceof DOMException && error.name === 'AbortError';
      const msg = isTimeout
        ? 'Requisição expirou após 10 minutos.'
        : error instanceof Error ? `Erro: ${error.message}` : 'Ocorreu um erro. Tente novamente.';
      setMessages(prev => [...prev, {
        id: Date.now().toString(), text: msg, sender: 'bot', timestamp: new Date(),
      }]);
      console.warn('[chat] fetch error:', error);
    } finally {
      clearTimeout(timeoutId);
      setIsLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    send();
  };

  // Rough client-side count for text not yet sent; server replaces it with the
  // real tokenizer count as soon as the turn completes.
  const pendingTokens = Math.ceil(inputValue.length / 4);

  return (
    <section className="chat-pane">
      <header className="chat-topbar">
        <button className="btn-icon menu-toggle" onClick={onToggleSidebar} title="Sessões">☰</button>
        <div className="topbar-title" title={sessionTitle}>{sessionTitle}</div>
        <div className="topbar-actions">
          <label className="toggle-label">
            <input type="checkbox" checked={isAudio} onChange={() => setIsAudio(a => !a)} />
            <span>Áudio</span>
          </label>
          <button className="btn-mini" onClick={runCompact} disabled={isLoading}>Compactar</button>
          <button className="btn-mini danger" onClick={handleClear} disabled={isLoading}>Limpar</button>
        </div>
      </header>

      <ContextMeter
        usage={usage}
        pending={pendingTokens}
        onRefresh={refreshUsage}
        refreshing={refreshing}
      />

      <div className="messages-container">
        <div className="messages-inner">
          {messages.map(message => (
            <div key={message.id} className={`message ${message.sender}`}>
              {message.thinking && <ThinkingBlock content={message.thinking} />}
              <div className="message-text">{message.text}</div>
              <div className="message-time">
                {message.timestamp.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="message bot typing">
              <div className="typing-indicator"><span /><span /><span /></div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <form className="input-area" onSubmit={handleSubmit}>
        <div className="input-inner">
          <textarea
            ref={inputRef}
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Transmita sua consulta…"
            title="Enter envia · Shift+Enter quebra linha"
            disabled={isLoading}
            rows={1}
            className="message-input"
          />
          <button type="submit" className="btn-send" disabled={isLoading || !inputValue.trim()}>
            Enviar
          </button>
        </div>
      </form>
    </section>
  );
};

export default ChatView;
