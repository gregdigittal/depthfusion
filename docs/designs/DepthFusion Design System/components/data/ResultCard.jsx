import React from 'react';

function renderSnippet(s) {
  return String(s).split(/(\{[^}]+\})/g).map((p, i) =>
    p.startsWith('{') && p.endsWith('}')
      ? <mark key={i}>{p.slice(1, -1)}</mark>
      : <React.Fragment key={i}>{p}</React.Fragment>
  );
}
function scoreColor(n) {
  return n > 80 ? 'var(--ok-soft)' : n >= 50 ? 'var(--accent)' : 'var(--warn)';
}

/**
 * Search result card. `result` = { title, cls, source, snippet, score, date, loc }.
 * Wrap query terms in {curly braces} inside `snippet` to highlight them.
 */
export function ResultCard({ result, className = '', ...rest }) {
  const r = result || {};
  return (
    <div className={['df-result', className].filter(Boolean).join(' ')} role="article" tabIndex={0} {...rest}>
      <div className="df-result__top">
        <div className="df-result__title">{r.title}</div>
        <div className="df-badges">
          {r.cls ? <span className={`df-badge df-badge--${r.cls}`}>{r.cls}</span> : null}
          {r.source ? <span className="df-badge df-badge--source">{r.source}</span> : null}
        </div>
      </div>
      {r.snippet ? <div className="df-result__snip">{renderSnippet(r.snippet)}</div> : null}
      <div className="df-result__foot">
        {typeof r.score === 'number' ? (
          <div className="df-score">
            <div className="df-score__bar">
              <div className="df-score__fill" style={{ width: r.score + '%', background: scoreColor(r.score) }} />
            </div>
            <span className="df-score__pct">{r.score}%</span>
          </div>
        ) : null}
        {r.date ? <span className="df-result__date">{r.date}</span> : null}
        {r.loc ? <span className="df-result__loc">{r.loc}</span> : null}
      </div>
    </div>
  );
}
