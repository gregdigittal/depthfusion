import React from 'react';

/**
 * Small status pill. Classification variants (public/internal/confidential/
 * restricted) plus a neutral `source` variant for source-type tags.
 */
export function Badge({ variant = 'source', children, className = '', ...rest }) {
  const cls = ['df-badge', `df-badge--${variant}`, className].filter(Boolean).join(' ');
  return (
    <span className={cls} {...rest}>{children}</span>
  );
}
