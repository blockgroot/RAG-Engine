import type { ReactNode } from "react";

/** Page title card, in the Ask page's look: a small label, a gradient title, meta chips. */

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="space-hero page-hero">
      <div className="space-hero-copy">
        {eyebrow ? (
          <span className="ask-scope">
            <span className="ask-scope-dot" aria-hidden />
            {eyebrow}
          </span>
        ) : null}
        <h1>{title}</h1>
        {description ? <p className="ask-hello-copy">{description}</p> : null}
        {meta ? <div className="space-hero-chips">{meta}</div> : null}
      </div>
      {actions ? <div className="page-hero-actions">{actions}</div> : null}
    </header>
  );
}
