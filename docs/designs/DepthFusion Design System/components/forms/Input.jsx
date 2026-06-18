import React from 'react';

/** Text input. Pass `icon` to render a leading icon (e.g. the search field). */
export function Input({ icon, className = '', ...rest }) {
  if (icon) {
    return (
      <span className="df-input-wrap">
        <span className="df-search-ic">{icon}</span>
        <input className={['df-input', className].filter(Boolean).join(' ')} {...rest} />
      </span>
    );
  }
  return (
    <input className={['df-input', 'df-input--plain', className].filter(Boolean).join(' ')} {...rest} />
  );
}
