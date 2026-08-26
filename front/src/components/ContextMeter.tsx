import type { ContextUsage } from '../api';

const WARN = 0.75;
const CRIT = 0.9;

const SOURCE_LABEL: Record<ContextUsage['source'], string> = {
  last_turn: 'contagem real do último turno',
  tokenizer: 'tokenizer do modelo',
  estimate: 'estimativa — Ollama fora do ar',
};

interface Props {
  usage: ContextUsage | null;
  pending: number;
  onRefresh: () => void;
  refreshing: boolean;
}

/** Context gauge fed by prompt_eval_count — the model's own token count. */
const ContextMeter = ({ usage, pending, onRefresh, refreshing }: Props) => {
  if (!usage) {
    return (
      <div className="context-meter">
        <div className="context-meter-label">
          <span className="ctx-dim">Contexto: medindo…</span>
        </div>
        <div className="context-bar"><div className="context-bar-fill" style={{ width: '0%' }} /></div>
      </div>
    );
  }

  const used = usage.used_tokens + pending;
  const pct = Math.min(used / usage.max_tokens, 1);
  const free = Math.max(usage.max_tokens - used, 0);
  const level = pct >= CRIT ? 'crit' : pct >= WARN ? 'warn' : 'ok';

  return (
    <div
      className="context-meter"
      title={`${usage.model} · ${SOURCE_LABEL[usage.source]}${pending ? ` · +${pending} tokens não enviados` : ''}`}
    >
      <div className="context-meter-label">
        <span>
          <strong>{used.toLocaleString('pt-BR')}</strong>
          <span className="ctx-dim"> / {usage.max_tokens.toLocaleString('pt-BR')} tokens</span>
          {!usage.exact && <span className="ctx-badge ctx-badge-warn">estimado</span>}
        </span>
        <span className="ctx-right">
          <span className="ctx-dim">{(pct * 100).toFixed(1)}% · {free.toLocaleString('pt-BR')} livres</span>
          <button
            type="button"
            className="ctx-refresh"
            onClick={onRefresh}
            disabled={refreshing}
            title="Recontar com o tokenizer do modelo"
          >{refreshing ? '…' : '↻'}</button>
        </span>
      </div>
      <div
        className="context-bar"
        role="progressbar"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={usage.max_tokens}
      >
        <div className={`context-bar-fill ${level}`} style={{ width: `${pct * 100}%` }} />
        {pending > 0 && (
          <div
            className="context-bar-pending"
            style={{
              left: `${Math.min((usage.used_tokens / usage.max_tokens) * 100, 100)}%`,
              width: `${Math.min((pending / usage.max_tokens) * 100, 100)}%`,
            }}
          />
        )}
      </div>
    </div>
  );
};

export default ContextMeter;
