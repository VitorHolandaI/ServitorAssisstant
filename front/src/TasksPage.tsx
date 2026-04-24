import { useEffect, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE ?? `http://${import.meta.env.VITE_SERVER_IP}:8000`;

export interface Task {
  id: number;
  title: string;
  description: string | null;
  created_at: string;
  due_at: string | null;
  is_completed: number;
  recurrence_type: string;
  recurrence_interval: number;
  recurrence_day_of_week: number | null;
  recurrence_day_of_month: number | null;
  timezone: string;
}

type TaskDraft = Omit<Task, 'id' | 'created_at'> & { id?: number };

const emptyDraft = (): TaskDraft => ({
  title: '',
  description: '',
  due_at: '',
  is_completed: 0,
  recurrence_type: 'none',
  recurrence_interval: 1,
  recurrence_day_of_week: null,
  recurrence_day_of_month: null,
  timezone: 'America/Recife',
});

const nullable = (v: string) => (v.trim() === '' ? null : v);

const TasksPage: React.FC = () => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [editing, setEditing] = useState<TaskDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTasks = async () => {
    try {
      const r = await fetch(`${API_BASE}/tasks`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setTasks(data.tasks ?? []);
      setError(null);
    } catch (e) {
      setError(`Failed to load tasks: ${(e as Error).message}`);
    }
  };

  useEffect(() => { loadTasks(); }, []);

  const startEdit = (t: Task) => setEditing({ ...t, description: t.description ?? '', due_at: t.due_at ?? '' });
  const startNew = () => setEditing(emptyDraft());
  const cancelEdit = () => setEditing(null);

  const saveTask = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      const body = {
        title: editing.title,
        description: nullable(editing.description ?? ''),
        due_at: nullable(editing.due_at ?? ''),
        is_completed: !!editing.is_completed,
        recurrence_type: editing.recurrence_type,
        recurrence_interval: editing.recurrence_interval,
        recurrence_day_of_week: editing.recurrence_day_of_week,
        recurrence_day_of_month: editing.recurrence_day_of_month,
        timezone: editing.timezone,
      };
      const url = editing.id ? `${API_BASE}/tasks/${editing.id}` : `${API_BASE}/tasks`;
      const method = editing.id ? 'PUT' : 'POST';
      const r = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      setEditing(null);
      await loadTasks();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const deleteTask = async (id: number) => {
    if (!confirm(`Delete task #${id}?`)) return;
    setBusy(true);
    try {
      const r = await fetch(`${API_BASE}/tasks/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await loadTasks();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleComplete = async (t: Task) => {
    setBusy(true);
    try {
      const r = await fetch(`${API_BASE}/tasks/${t.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_completed: !t.is_completed }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await loadTasks();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const update = <K extends keyof TaskDraft>(key: K, value: TaskDraft[K]) =>
    setEditing(prev => (prev ? { ...prev, [key]: value } : prev));

  return (
    <div className="tasks-content">
      {error && <div className="task-error">{error}</div>}

      <div className="tasks-toolbar">
        <button className="btn-send" onClick={startNew} disabled={busy || !!editing}>+ New Task</button>
        <button className="btn-clear" onClick={loadTasks} disabled={busy}>Refresh</button>
      </div>

      {editing && (
        <div className="task-editor">
          <div className="task-editor-title">
            {editing.id ? `Edit Task #${editing.id}` : 'New Task'}
          </div>

          <label className="field">
            <span>Title</span>
            <input value={editing.title} onChange={e => update('title', e.target.value)} />
          </label>

          <label className="field">
            <span>Description</span>
            <textarea
              value={editing.description ?? ''}
              onChange={e => update('description', e.target.value)}
              rows={3}
            />
          </label>

          <label className="field">
            <span>Due (YYYY-MM-DD HH:MM:SS)</span>
            <input
              value={editing.due_at ?? ''}
              onChange={e => update('due_at', e.target.value)}
              placeholder="2026-04-25 14:30:00"
            />
          </label>

          <div className="field-row">
            <label className="field">
              <span>Recurrence</span>
              <select
                value={editing.recurrence_type}
                onChange={e => update('recurrence_type', e.target.value)}
              >
                <option value="none">none</option>
                <option value="daily">daily</option>
                <option value="weekly">weekly</option>
                <option value="monthly">monthly</option>
              </select>
            </label>

            <label className="field">
              <span>Interval</span>
              <input
                type="number"
                min={1}
                value={editing.recurrence_interval}
                onChange={e => update('recurrence_interval', parseInt(e.target.value) || 1)}
              />
            </label>
          </div>

          <div className="field-row">
            <label className="field">
              <span>Day of week (0-6)</span>
              <input
                type="number"
                min={0}
                max={6}
                value={editing.recurrence_day_of_week ?? ''}
                onChange={e => update('recurrence_day_of_week', e.target.value === '' ? null : parseInt(e.target.value))}
              />
            </label>
            <label className="field">
              <span>Day of month (1-31)</span>
              <input
                type="number"
                min={1}
                max={31}
                value={editing.recurrence_day_of_month ?? ''}
                onChange={e => update('recurrence_day_of_month', e.target.value === '' ? null : parseInt(e.target.value))}
              />
            </label>
          </div>

          <label className="field">
            <span>Timezone</span>
            <input value={editing.timezone} onChange={e => update('timezone', e.target.value)} />
          </label>

          <label className="toggle-label">
            <input
              type="checkbox"
              checked={!!editing.is_completed}
              onChange={e => update('is_completed', e.target.checked ? 1 : 0)}
            />
            <span>Completed</span>
          </label>

          <div className="editor-actions">
            <button className="btn-send" onClick={saveTask} disabled={busy || !editing.title.trim()}>
              {editing.id ? 'Save (PUT)' : 'Create'}
            </button>
            <button className="btn-clear" onClick={cancelEdit} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}

      <div className="task-list">
        {tasks.length === 0 && <div className="task-empty">No tasks recorded.</div>}
        {tasks.map(t => (
          <div key={t.id} className={`task-card ${t.is_completed ? 'done' : ''}`}>
            <div className="task-head">
              <div className="task-title">
                <span className="task-id">#{t.id}</span> {t.title}
              </div>
              <div className="task-actions">
                <button className="btn-mini" onClick={() => toggleComplete(t)} disabled={busy}>
                  {t.is_completed ? '↺' : '✓'}
                </button>
                <button className="btn-mini" onClick={() => startEdit(t)} disabled={busy || !!editing}>✎</button>
                <button className="btn-mini danger" onClick={() => deleteTask(t.id)} disabled={busy}>✕</button>
              </div>
            </div>
            {t.description && <div className="task-desc">{t.description}</div>}
            <div className="task-meta">
              {t.due_at && <span>Due: {t.due_at}</span>}
              {t.recurrence_type !== 'none' && (
                <span>↻ {t.recurrence_type} /{t.recurrence_interval}</span>
              )}
              <span className="task-created">created {t.created_at}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TasksPage;
