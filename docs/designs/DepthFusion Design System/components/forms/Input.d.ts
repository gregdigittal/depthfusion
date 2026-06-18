import * as React from 'react';

/** Text input; pass `icon` for a leading-icon field (search). */
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Leading icon node (renders the search-field layout). */
  icon?: React.ReactNode;
}
export function Input(props: InputProps): JSX.Element;
