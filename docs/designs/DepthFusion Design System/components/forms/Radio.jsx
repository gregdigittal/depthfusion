import React from 'react';

/** Labeled radio option (facet-panel style). Pass a shared `name` to group. */
export function Radio({ label, className = '', ...rest }) {
  return (
    <label className={['df-opt', className].filter(Boolean).join(' ')}>
      <input type="radio" {...rest} />
      {label}
    </label>
  );
}
