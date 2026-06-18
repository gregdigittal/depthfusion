import * as React from 'react';

/** Classification / source status pill. */
export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Default "source". */
  variant?: 'public' | 'internal' | 'confidential' | 'restricted' | 'source';
  children?: React.ReactNode;
}
export function Badge(props: BadgeProps): JSX.Element;
