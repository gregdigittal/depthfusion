import * as React from 'react';

/** Knowledge-graph node-type chip. */
export interface NodeChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  type?: 'doc' | 'concept' | 'decision';
  /** Override label/content; defaults to the type's name. */
  children?: React.ReactNode;
}
export function NodeChip(props: NodeChipProps): JSX.Element;
