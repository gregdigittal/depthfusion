import * as React from 'react';

/** Surface container with an optional title row. */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Optional heading rendered at the top of the card. */
  title?: React.ReactNode;
  children?: React.ReactNode;
}
export function Card(props: CardProps): JSX.Element;
