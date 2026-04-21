import { useState, useRef, useEffect } from 'react';
import './App.css';

const API_BASE = import.meta.env.VITE_API_BASE ?? `http://${import.meta.env.VITE_SERVER_IP}:8000`;
const API_STREAM = `${API_BASE}/stream_message`;

interface Message {
  id: string;
  text: string;
  thinking?: string;
  sender: 'user' | 'bot';
  timestamp: Date;
}

const ThinkingBlock = ({ content }: { content: string }) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="thinking-block">
      <button className="thinking-toggle" onClick={() => setOpen(o => !o)}>
        <span className="thinking-icon">{open ? '▾' : '▸'}</span>
        <span>Cogitating…</span>
      </button>
      {open && <pre className="thinking-content">{content}</pre>}
    </div>
  );
};

const App: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([
    { id: '1', text: 'Praise the Omnissiah. How may I assist you?', sender: 'bot', timestamp: new Date() },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isAudio, setIsAudio] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/conversation`)
      .then(r => r.json())
      .then(data => {
        if (data.messages?.length > 0) {
          setMessages(data.messages.map((m: { role: string; content: string; created_at: string }) => ({
            id: m.created_at + m.role,
            text: m.content,
            sender: m.role === 'user' ? 'user' : 'bot',
            timestamp: new Date(m.created_at),
          })));
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleClearConversation = async () => {
    await fetch(`${API_BASE}/conversation`, { method: 'DELETE' }).catch(() => {});
    setMessages([{
      id: Date.now().toString(),
      text: 'Praise the Omnissiah. How may I assist you?',
      sender: 'bot',
      timestamp: new Date(),
    }]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      text: inputValue,
      sender: 'user',
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), 10 * 60 * 1000);

    try {
      const response = await fetch(API_STREAM, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: inputValue, audio: isAudio }),
        signal: abortController.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const botMessageId = Date.now().toString();
      setMessages(prev => [...prev, {
        id: botMessageId, text: '', thinking: undefined, sender: 'bot', timestamp: new Date(),
      }]);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

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
            if (parsed.done) break;
            if (!parsed.content) continue;

            if (parsed.type === 'thinking') {
              setMessages(prev => prev.map(msg =>
                msg.id === botMessageId
                  ? { ...msg, thinking: (msg.thinking ?? '') + parsed.content }
                  : msg
              ));
            } else {
              setMessages(prev => prev.map(msg =>
                msg.id === botMessageId
                  ? { ...msg, text: msg.text + parsed.content }
                  : msg
              ));
            }
          } catch { /* skip malformed */ }
        }
      }
    } catch (error) {
      const isTimeout = error instanceof DOMException && error.name === 'AbortError';
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        text: isTimeout ? 'Request timed out.' : 'An error occurred. Please try again.',
        sender: 'bot',
        timestamp: new Date(),
      }]);
    } finally {
      clearTimeout(timeoutId);
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <div className="chat-container">
        <header className="chat-header">
          <div className="header-title">
            <span className="header-cog">⚙</span>
            <span>SERVITOR</span>
            <span className="header-cog">⚙</span>
          </div>
          <div className="header-sub">Adeptus Mechanicus Interface</div>
        </header>

        <div className="messages-container">
          {messages.map(message => (
            <div key={message.id} className={`message ${message.sender}`}>
              {message.thinking && <ThinkingBlock content={message.thinking} />}
              <div className="message-text">{message.text}</div>
              <div className="message-time">
                {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="message bot typing">
              <div className="typing-indicator">
                <span /><span /><span />
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <form className="input-area" onSubmit={handleSubmit}>
          <div className="input-row">
            <input
              type="text"
              value={inputValue}
              onChange={e => setInputValue(e.target.value)}
              placeholder="Transmit your query…"
              disabled={isLoading}
              className="message-input"
            />
            <button type="submit" className="btn-send" disabled={isLoading || !inputValue.trim()}>
              Send
            </button>
          </div>
          <div className="controls-row">
            <label className="toggle-label">
              <input type="checkbox" checked={isAudio} onChange={() => setIsAudio(a => !a)} />
              <span>Audio Mode</span>
            </label>
            <button type="button" className="btn-clear" onClick={handleClearConversation} disabled={isLoading}>
              New Conversation
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default App;
