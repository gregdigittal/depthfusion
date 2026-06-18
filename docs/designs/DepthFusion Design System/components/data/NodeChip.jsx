import React from 'react';

const META = {
  doc:      { cls: 'doc', label: 'Document' },
  concept:  { cls: 'con', label: 'Concept' },
  decision: { cls: 'dec', label: 'Decision' },
};

/** Knowledge-graph node-type chip. `type`: doc · concept · decision. */
export function NodeChip({ type = 'doc', children, className = '', ...rest }) {
  const m = META[type] || META.doc;
  return (
    <span className={['df-chip', `df-chip--${m.cls}`, className].filter(Boolean).join(' ')} {...rest}>
      {children || m.label}
    </span>
  );
}
