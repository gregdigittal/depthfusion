import * as React from 'react';

/** Circular initial avatar. */
export interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Display name; first letter is shown. */
  name?: string;
  /** Diameter in px. Default 46. */
  size?: number;
}
export function Avatar(props: AvatarProps): JSX.Element;
