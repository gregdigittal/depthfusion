import * as React from 'react';

/** Primary action button. */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual variant. Default "primary". */
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  children?: React.ReactNode;
}
export function Button(props: ButtonProps): JSX.Element;
