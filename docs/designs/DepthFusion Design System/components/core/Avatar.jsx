import React from 'react';

/** Circular initial avatar (amber fill, ember glow). Derives the initial from `name`. */
export function Avatar({ name = '', size = 46, className = '', ...rest }) {
  const initial = (String(name).trim()[0] || '?').toUpperCase();
  return (
    <div
      className={['df-avatar', className].filter(Boolean).join(' ')}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.41) }}
      {...rest}
    >
      {initial}
    </div>
  );
}
