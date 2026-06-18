import React from 'react';

/** Labeled checkbox option (facet-panel style). */
export function Checkbox({ label, className = '', ...rest }) {
  return (
    <label className={['df-opt', className].filter(Boolean).join(' ')}>
      <input type="checkbox" {...rest} />
      {label}
    </label>
  );
}
