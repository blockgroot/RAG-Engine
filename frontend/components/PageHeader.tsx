import type { ReactNode } from "react";
import { PageSceneArt, type PageSceneVariant } from "@/components/PageSceneArt";

/** Consistent page title block — optional scene art + meta chips for studio pages. */

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  scene,
  meta,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  scene?: PageSceneVariant;
  meta?: ReactNode;
}) {
  return (
    <header className={`page-header${scene ? " page-header-studio" : ""}`}>
      <div className="page-header-copy">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="lede">{description}</p> : null}
        {meta ? <div className="page-header-meta">{meta}</div> : null}
      </div>
      {actions ? <div className="page-header-actions">{actions}</div> : null}
      {scene ? <PageSceneArt variant={scene} /> : null}
    </header>
  );
}
