import * as React from 'react';

/** Horizontal tab / segment navigation. */
export interface TabsProps {
  /** Strings or {id,label} objects. */
  tabs: Array<string | { id: string; label: React.ReactNode }>;
  /** Currently active id. */
  value?: string;
  /** Called with the clicked tab id. */
  onChange?: (id: string) => void;
  className?: string;
}
export function Tabs(props: TabsProps): JSX.Element;
