import { useCallback, useEffect, useState } from 'react';
import './App.css';
import { api, type Session } from './api';
import Sidebar from './components/Sidebar';
import ChatView from './components/ChatView';
import TasksPage from './TasksPage';

type View = 'chat' | 'tasks';

const DESKTOP_QUERY = '(min-width: 900px)';

const App: React.FC = () => {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [view, setView] = useState<View>('chat');
  const [isDesktop, setIsDesktop] = useState(() => window.matchMedia(DESKTOP_QUERY).matches);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const refreshSessions = useCallback(async () => {
    const data = await api.listSessions();
    setSessions(data.sessions);
    return data;
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.listSessions()
      .then(async data => {
        if (cancelled) return;
        if (data.sessions.length === 0) {
          const created = await api.createSession();
          if (cancelled) return;
          setSessions([created]);
          setActiveId(created.id);
          return;
        }
        setSessions(data.sessions);
        setActiveId(data.active_id);
      })
      .catch(err => console.warn('Falha ao carregar sessões:', err));
    return () => { cancelled = true; };
  }, []);

  const selectSession = async (id: number) => {
    setActiveId(id);
    setView('chat');
    if (!isDesktop) setSidebarOpen(false);
    // The backend keeps the active session so the ESP32 voice path writes here too.
    await api.activateSession(id).catch(err => console.warn('Falha ao ativar sessão:', err));
  };

  const createSession = async () => {
    try {
      const s = await api.createSession();
      setSessions(prev => [s, ...prev]);
      setActiveId(s.id);
      setView('chat');
      if (!isDesktop) setSidebarOpen(false);
    } catch (err) {
      console.warn('Falha ao criar sessão:', err);
    }
  };

  const deleteSession = async (id: number) => {
    try {
      const { active_id } = await api.deleteSession(id);
      const data = await refreshSessions();
      const next = data.sessions.some(s => s.id === active_id) ? active_id : data.sessions[0]?.id ?? null;
      if (next === null) {
        const s = await api.createSession();
        setSessions([s]);
        setActiveId(s.id);
        return;
      }
      if (id === activeId) {
        setActiveId(next);
        await api.activateSession(next).catch(() => {});
      }
    } catch (err) {
      console.warn('Falha ao apagar sessão:', err);
    }
  };

  const renameSession = async (id: number, title: string) => {
    setSessions(prev => prev.map(s => (s.id === id ? { ...s, title } : s)));
    await api.renameSession(id, title).catch(err => console.warn('Falha ao renomear:', err));
  };

  const activeTitle = sessions.find(s => s.id === activeId)?.title ?? 'Servitor';

  return (
    <div className="app">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        view={view}
        open={isDesktop || sidebarOpen}
        onSelect={selectSession}
        onCreate={createSession}
        onDelete={deleteSession}
        onRename={renameSession}
        onView={v => { setView(v); if (!isDesktop) setSidebarOpen(false); }}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="main-pane">
        {view === 'tasks' ? (
          <section className="chat-pane">
            <header className="chat-topbar">
              <button className="btn-icon menu-toggle" onClick={() => setSidebarOpen(true)} title="Sessões">☰</button>
              <div className="topbar-title">Tasks</div>
            </header>
            <TasksPage />
          </section>
        ) : (
          <ChatView
            sessionId={activeId}
            sessionTitle={activeTitle}
            onActivity={() => { refreshSessions().catch(() => {}); }}
            onToggleSidebar={() => setSidebarOpen(o => !o)}
          />
        )}
      </main>
    </div>
  );
};

export default App;
