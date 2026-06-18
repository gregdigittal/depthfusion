import React from 'react';

/**
 * Horizontal tab/segment nav. `tabs` is an array of strings or {id,label}.
 * Controlled: pass `value` and `onChange(id)`.
 */
export function Tabs({ tabs = [], value, onChange, className = '' }) {
  return (
    <div className={['df-tabs', className].filter(Boolean).join(' ')}>
      {tabs.map((t) => {
        const id = typeof t === 'string' ? t : t.id;
        const label = typeof t === 'string' ? t : t.label;
        return (
          <button
            key={id}
            className={'df-tab' + (value === id ? ' df-tab--active' : '')}
            onClick={() => onChange && onChange(id)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
