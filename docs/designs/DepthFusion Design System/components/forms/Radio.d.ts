import * as React from 'react';

/** Labeled radio option. Group with a shared `name`. */
export interface RadioProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: React.ReactNode;
}
export function Radio(props: RadioProps): JSX.Element;
