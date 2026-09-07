"use client";

/**
 * Scroll-in motion for the marketing pages.
 *
 * Mount once per page; it observes every `[data-reveal]` element on it and
 * adds `is-in` when it first enters the viewport. Elements opt in with an
 * attribute instead of being wrapped in a component, because a wrapper div
 * around a grid child breaks the grid — and these pages are mostly grids.
 *
 * The hidden state is scoped to `.has-reveal`, a class this component adds to
 * the document. So with JavaScript off, or if this never runs, the page is
 * simply visible: motion is an enhancement and must never be able to hide
 * content.
 *
 * Reduced motion is handled in CSS rather than here, so the preference is
 * respected even mid-session when it changes.
 */

import { useEffect } from "react";

export function RevealOnScroll() {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("has-reveal");

    const nodes = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    if (!("IntersectionObserver" in window)) {
      // No observer: show everything rather than leaving a blank page.
      nodes.forEach((n) => n.classList.add("is-in"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-in");
          // One-way: re-hiding on scroll-up makes a page feel unstable, and
          // re-animating something already read is noise.
          observer.unobserve(entry.target);
        }
      },
      // Fires slightly before the element is fully in view, so the motion
      // finishes as it settles rather than starting once it is already read.
      { rootMargin: "0px 0px -12% 0px", threshold: 0.08 }
    );

    nodes.forEach((n) => observer.observe(n));
    return () => observer.disconnect();
  }, []);

  return null;
}

/**
 * A pointer-following highlight, for one hero at a time.
 *
 * Writes the cursor position to CSS custom properties on the element and lets
 * CSS decide what to do with it, so the effect can be restyled or switched off
 * per breakpoint without touching this. No-ops on touch: there is no hover
 * there, and tracking taps would make the highlight jump.
 */
export function useSpotlight<T extends HTMLElement>(ref: React.RefObject<T | null>) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(hover: none)").matches) return;

    let frame = 0;
    function onMove(event: PointerEvent) {
      if (frame) return;  // one write per frame, not one per event
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const box = el!.getBoundingClientRect();
        el!.style.setProperty("--mx", `${((event.clientX - box.left) / box.width) * 100}%`);
        el!.style.setProperty("--my", `${((event.clientY - box.top) / box.height) * 100}%`);
      });
    }

    el.addEventListener("pointermove", onMove);
    return () => {
      el.removeEventListener("pointermove", onMove);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [ref]);
}
