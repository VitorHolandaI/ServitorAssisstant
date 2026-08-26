import { useEffect, useRef, useState } from 'react';
import type { Session } from '../api';

type View = 'chat' | 'tasks';

interface Props {
  sessions: Session[];
  activeId: number | null;
  view: View;
  open: boolean;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
  onView: (view: View) => void;
  onClose: () => void;
}

const relativeDay = (stamp: string) => {
  const d = new Date(stamp.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return stamp;
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  if (sameDay) return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'ontem';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
};

const Sidebar = ({
  sessions, activeId, view, open,
  onSelect, onCreate, onDelete, onRename, onView, onClose,
}: Props) => {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (editingId !== null) inputRef.current?.select(); }, [editingId]);

  const startRename = (s: Session) => {
    setConfirmId(null);
    setEditingId(s.id);
    setDraft(s.title);
  };

  const commitRename = () => {
    if (editingId === null) return;
    const title = draft.trim();
    const current = sessions.find(s => s.id === editingId);
    if (title && current && title !== current.title) onRename(editingId, title);
    setEditingId(null);
  };

  return (
    <>
      {open && <div className="sidebar-scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-brand">
          <span className="header-cog">⚙</span>
          <div>
            <div className="brand-name">SERVITOR</div>
            <div className="brand-sub">Adeptus Mechanicus</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          <button
            className={`nav-item ${view === 'chat' ? 'active' : ''}`}
            onClick={() => onView('chat')}
          >Cogitador</button>
          <button
            className={`nav-item ${view === 'tasks' ? 'active' : ''}`}
            onClick={() => onView('tasks')}
          >Tasks</button>
        </nav>

        <div className="sidebar-section">
          <span>Sessões</span>
          <button className="btn-mini" onClick={onCreate} title="Nova sessão">+ Nova</button>
        </div>

        <ul className="session-list">
          {sessions.length === 0 && <li className="session-empty">Nenhuma sessão ainda.</li>}
          {sessions.map(s => (
            <li
              key={s.id}
              className={`session-item ${s.id === activeId && view === 'chat' ? 'active' : ''}`}
            >
              {editingId === s.id ? (
                <input
                  ref={inputRef}
                  className="session-rename"
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={e => {
                    if (e.key === 'Enter') commitRename();
                    if (e.key === 'Escape') setEditingId(null);
                  }}
                  autoFocus
                />
              ) : (
                <>
                  <button className="session-open" onClick={() => onSelect(s.id)}>
                    <span className="session-title">{s.title}</span>
                    <span className="session-meta">
                      {s.message_count} msg · {relativeDay(s.updated_at)}
                    </span>
                  </button>
                  <div className="session-actions">
                    {confirmId === s.id ? (
                      <>
                        <button
                          className="btn-icon danger"
                          title="Confirmar exclusão"
                          onClick={() => { setConfirmId(null); onDelete(s.id); }}
                        >✓</button>
                        <button
                          className="btn-icon"
                          title="Cancelar"
                          onClick={() => setConfirmId(null)}
                        >✕</button>
                      </>
                    ) : (
                      <>
                        <button
                          className="btn-icon"
                          title="Renomear"
                          onClick={() => startRename(s)}
                        >✎</button>
                        <button
                          className="btn-icon"
                          title="Apagar sessão"
                          onClick={() => setConfirmId(s.id)}
                        >🗑</button>
                      </>
                    )}
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      </aside>
    </>
  );
};

export default Sidebar;
