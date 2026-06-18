import React from 'react';

/** Surface container with an optional title. Used for panels, settings sections, tiles. */
export function Card({ title, children, className = '', ...rest }) {
  return (
    <div className={['df-card', className].filter(Boolean).join(' ')} {...rest}>
      {title ? <div className="df-card__title">{title}</div> : null}
      {children}
    </div>
  );
}
