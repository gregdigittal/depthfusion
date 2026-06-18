import React from 'react';

/** Primary action control. Variants: primary · secondary · danger · ghost. */
export function Button({ variant = 'primary', children, className = '', ...rest }) {
  const cls = ['df-btn', `df-btn--${variant}`, className].filter(Boolean).join(' ');
  return (
    <button className={cls} {...rest}>{children}</button>
  );
}
