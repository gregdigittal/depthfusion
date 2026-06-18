import * as React from 'react';

export interface ResultData {
  title: string;
  /** Classification → badge variant. */
  cls?: 'public' | 'internal' | 'confidential' | 'restricted';
  /** Source type, e.g. "Document". */
  source?: string;
  /** Snippet; wrap query terms in {curly braces} to highlight. */
  snippet?: string;
  /** 0–100 relevance; bar color thresholds at 50 / 80. */
  score?: number;
  date?: string;
  /** Mono locator path. */
  loc?: string;
}

/** Search result card. */
export interface ResultCardProps extends React.HTMLAttributes<HTMLDivElement> {
  result: ResultData;
}
export function ResultCard(props: ResultCardProps): JSX.Element;
