"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

const TOTAL = 12;

export function TalkDeck() {
  const [i, setI] = useState(0);
  const [overview, setOverview] = useState(false);
  const go = useCallback((n: number) => {
    setOverview(false);
    setI(((n % TOTAL) + TOTAL) % TOTAL);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown") {
        e.preventDefault();
        go(i + 1);
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        go(i - 1);
      } else if (e.key === "Home") {
        go(0);
      } else if (e.key === "End") {
        go(TOTAL - 1);
      } else if (e.key === "o" || e.key === "Escape") {
        setOverview((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, i]);

  return (
    <div className="talk">
      <header className="talk-bar">
        <p className="talk-kicker">Tech talk · 12 slides</p>
        <strong>RAG, in plain English</strong>
        <span className="talk-count" aria-live="polite">
          {i + 1} / {TOTAL}
        </span>
      </header>

      {overview ? (
        <div className="talk-overview" role="navigation" aria-label="Slide overview">
          {SLIDES.map((s, n) => (
            <button key={s.id} type="button" className="talk-thumb" onClick={() => go(n)}>
              <span>{n + 1}</span>
              {s.title}
            </button>
          ))}
        </div>
      ) : null}

      <div className={overview ? "talk-print-only" : undefined}>
        {SLIDES.map((s, n) => (
          <article
            key={s.id}
            className={`talk-stage${n === i ? " is-active" : ""}`}
            hidden={n !== i}
            aria-label={`Slide ${n + 1}: ${s.title}`}
          >
            {s.body}
          </article>
        ))}
      </div>

      <nav className="talk-nav" aria-label="Slides">
        <button type="button" onClick={() => go(i - 1)} disabled={i === 0}>
          Previous
        </button>
        <ol className="talk-dots">
          {SLIDES.map((s, n) => (
            <li key={s.id}>
              <button
                type="button"
                className={n === i ? "is-on" : ""}
                aria-current={n === i ? "true" : undefined}
                aria-label={`Slide ${n + 1}: ${s.title}`}
                onClick={() => go(n)}
              />
            </li>
          ))}
        </ol>
        <button type="button" onClick={() => go(i + 1)} disabled={i === TOTAL - 1}>
          Next
        </button>
        <button type="button" className="talk-ghost" onClick={() => setOverview((v) => !v)}>
          {overview ? "Back" : "All slides"}
        </button>
      </nav>
      <p className="talk-hint">Arrow keys or space · O for overview · print this page for a PDF</p>
    </div>
  );
}

type Slide = { id: string; title: string; body: ReactNode };

function SlideHead({ kicker, title, lead }: { kicker: string; title: string; lead?: string }) {
  return (
    <header className="talk-head">
      <p className="talk-kicker">{kicker}</p>
      <h1>{title}</h1>
      {lead ? <p className="talk-lead">{lead}</p> : null}
    </header>
  );
}

function ClosedVsOpen() {
  return (
    <svg className="talk-svg" viewBox="0 0 720 220" role="img" aria-label="Closed-book model versus open-book RAG">
      <rect x="8" y="8" width="340" height="204" rx="16" fill="#fff" stroke="#e4e7ec" />
      <text x="178" y="36" textAnchor="middle" className="talk-svg-label">Closed book</text>
      <rect x="48" y="56" width="88" height="112" rx="6" fill="#101828" />
      <rect x="58" y="68" width="68" height="8" rx="2" fill="#f4f6f8" opacity="0.4" />
      <rect x="58" y="84" width="52" height="6" rx="2" fill="#f4f6f8" opacity="0.3" />
      <circle cx="230" cy="112" r="36" fill="#ccfbf1" stroke="#0f766e" />
      <text x="230" y="118" textAnchor="middle" fontSize="13" fill="#0d5c56">LLM</text>
      <text x="178" y="192" textAnchor="middle" fontSize="12" fill="#667085">Memorised during training</text>

      <rect x="372" y="8" width="340" height="204" rx="16" fill="#fff" stroke="#0f766e" />
      <text x="542" y="36" textAnchor="middle" className="talk-svg-label">Open book</text>
      <rect x="404" y="56" width="72" height="96" rx="6" fill="#0f766e" />
      <rect x="412" y="68" width="56" height="6" rx="2" fill="#f0fdfa" />
      <rect x="412" y="80" width="44" height="5" rx="2" fill="#f0fdfa" opacity="0.8" />
      <path d="M488 104h28" stroke="#0f766e" strokeWidth="2.5" markerEnd="url(#arr)" />
      <circle cx="572" cy="104" r="36" fill="#ccfbf1" stroke="#0f766e" />
      <text x="572" y="110" textAnchor="middle" fontSize="13" fill="#0d5c56">LLM</text>
      <text x="542" y="192" textAnchor="middle" fontSize="12" fill="#667085">Looks things up, then answers</text>
      <defs>
        <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">
          <path d="M0 0 L8 4 L0 8 z" fill="#0f766e" />
        </marker>
      </defs>
    </svg>
  );
}

function RagLetters() {
  return (
    <svg className="talk-svg" viewBox="0 0 720 160" role="img" aria-label="Retrieve, then add, then generate">
      {[
        { x: 20, t: "Retrieve", d: "Find the pages that matter" },
        { x: 256, t: "Augment", d: "Stick them next to the question" },
        { x: 492, t: "Generate", d: "Write an answer from that pack" },
      ].map((b, n) => (
        <g key={b.t} transform={`translate(${b.x} 16)`}>
          <rect width="208" height="128" rx="16" fill="#fff" stroke={n === 1 ? "#0f766e" : "#e4e7ec"} />
          <circle cx="28" cy="32" r="14" fill="#ccfbf1" />
          <text x="28" y="37" textAnchor="middle" fontSize="13" fill="#0d5c56">{n + 1}</text>
          <text x="52" y="38" fontSize="16" fontWeight="650" fill="#101828">{b.t}</text>
          <text x="20" y="78" fontSize="13" fill="#667085">{b.d}</text>
        </g>
      ))}
    </svg>
  );
}

function TwoLanes() {
  return (
    <svg className="talk-svg talk-svg-tall" viewBox="0 0 720 280" role="img" aria-label="Prepare the library, then look things up">
      <text x="180" y="24" textAnchor="middle" className="talk-svg-label">Night shift · prepare</text>
      <text x="540" y="24" textAnchor="middle" className="talk-svg-label">Day shift · answer</text>
      {["Files in", "Clean & split", "Number each piece", "File in the library"].map((t, n) => (
        <g key={t}>
          <rect x="40" y={44 + n * 52} width="280" height="42" rx="10" fill="#fff" stroke="#e4e7ec" />
          <text x="180" y={70 + n * 52} textAnchor="middle" fontSize="14" fill="#101828">{t}</text>
          {n < 3 ? <path d={`M180 ${86 + n * 52} v10`} stroke="#0f766e" strokeWidth="2" /> : null}
        </g>
      ))}
      {["Question in", "Find nearby pieces", "Add them to the prompt", "Write — or say you can’t"].map((t, n) => (
        <g key={t}>
          <rect x="400" y={44 + n * 52} width="280" height="42" rx="10" fill="#fff" stroke="#0f766e" />
          <text x="540" y={70 + n * 52} textAnchor="middle" fontSize="14" fill="#101828">{t}</text>
          {n < 3 ? <path d={`M540 ${86 + n * 52} v10`} stroke="#0f766e" strokeWidth="2" /> : null}
        </g>
      ))}
    </svg>
  );
}

function IngestFlow() {
  const steps = ["Source", "Clean", "Split", "Embed", "Index"];
  return (
    <svg className="talk-svg" viewBox="0 0 720 140" role="img" aria-label="Ingestion steps">
      {steps.map((t, n) => (
        <g key={t} transform={`translate(${24 + n * 140} 28)`}>
          <rect width="120" height="84" rx="14" fill="#fff" stroke="#e4e7ec" />
          <circle cx="60" cy="28" r="12" fill="#ccfbf1" />
          <text x="60" y="33" textAnchor="middle" fontSize="12" fill="#0d5c56">{n + 1}</text>
          <text x="60" y="62" textAnchor="middle" fontSize="14" fontWeight="650" fill="#101828">{t}</text>
          {n < 4 ? <path d="M122 42 h16" stroke="#0f766e" strokeWidth="2" /> : null}
        </g>
      ))}
    </svg>
  );
}

function ChunkViz() {
  return (
    <svg className="talk-svg" viewBox="0 0 720 170" role="img" aria-label="A document split into overlapping pieces">
      <rect x="20" y="24" width="680" height="36" rx="6" fill="#eef2f5" />
      <text x="36" y="48" fontSize="13" fill="#667085">One long policy page …………………………………………………………</text>
      <rect x="40" y="84" width="240" height="56" rx="10" fill="#fff" stroke="#0f766e" />
      <text x="160" y="118" textAnchor="middle" fontSize="13">Piece A</text>
      <rect x="220" y="84" width="240" height="56" rx="10" fill="#ccfbf1" stroke="#0f766e" opacity="0.95" />
      <text x="340" y="118" textAnchor="middle" fontSize="13">Overlap</text>
      <rect x="400" y="84" width="240" height="56" rx="10" fill="#fff" stroke="#0f766e" />
      <text x="520" y="118" textAnchor="middle" fontSize="13">Piece B</text>
    </svg>
  );
}

function EmbedViz() {
  return (
    <svg className="talk-svg" viewBox="0 0 720 220" role="img" aria-label="Similar meanings sit near each other">
      <rect x="20" y="16" width="680" height="188" rx="16" fill="#fff" stroke="#e4e7ec" />
      <circle cx="210" cy="110" r="54" fill="#ccfbf1" opacity="0.55" />
      <circle cx="520" cy="120" r="48" fill="#eef2f5" />
      <circle cx="190" cy="98" r="8" fill="#0f766e" />
      <circle cx="228" cy="118" r="8" fill="#0f766e" />
      <circle cx="206" cy="132" r="7" fill="#0d5c56" />
      <text x="210" y="178" textAnchor="middle" fontSize="12" fill="#0d5c56">heart / cardiac</text>
      <circle cx="500" cy="108" r="8" fill="#667085" />
      <circle cx="540" cy="132" r="8" fill="#667085" />
      <text x="520" y="178" textAnchor="middle" fontSize="12" fill="#667085">leave days / PTO</text>
      <circle cx="320" cy="70" r="9" fill="#b54708" />
      <text x="338" y="74" fontSize="12" fill="#b54708">your question</text>
    </svg>
  );
}

function StoreViz() {
  return (
    <svg className="talk-svg" viewBox="0 0 720 150" role="img" aria-label="Ask, search nearest pieces, keep the top few">
      {["Question → numbers", "Scan the library", "Keep the closest few"].map((t, n) => (
        <g key={t} transform={`translate(${40 + n * 230} 30)`}>
          <rect width="210" height="90" rx="14" fill="#fff" stroke="#e4e7ec" />
          <text x="105" y="52" textAnchor="middle" fontSize="14" fill="#101828">{t}</text>
        </g>
      ))}
    </svg>
  );
}

function HybridViz() {
  return (
    <svg className="talk-svg talk-svg-tall" viewBox="0 0 720 220" role="img" aria-label="Meaning search and word search vote together">
      <rect x="40" y="20" width="200" height="120" rx="12" fill="#fff" stroke="#e4e7ec" />
      <text x="140" y="48" textAnchor="middle" fontSize="14" fontWeight="650">Meaning search</text>
      <text x="140" y="78" textAnchor="middle" fontSize="12" fill="#667085">1. leave carryover</text>
      <text x="140" y="98" textAnchor="middle" fontSize="12" fill="#667085">2. PTO policy</text>
      <rect x="480" y="20" width="200" height="120" rx="12" fill="#fff" stroke="#e4e7ec" />
      <text x="580" y="48" textAnchor="middle" fontSize="14" fontWeight="650">Word search</text>
      <text x="580" y="78" textAnchor="middle" fontSize="12" fill="#667085">1. “part-time”</text>
      <text x="580" y="98" textAnchor="middle" fontSize="12" fill="#667085">2. form HR-12</text>
      <rect x="260" y="48" width="200" height="64" rx="12" fill="#ccfbf1" stroke="#0f766e" />
      <text x="360" y="76" textAnchor="middle" fontSize="14" fontWeight="650">Merge ranks</text>
      <text x="360" y="96" textAnchor="middle" fontSize="12">both lists get a vote</text>
      <rect x="210" y="160" width="300" height="44" rx="10" fill="#fff" stroke="#0f766e" />
      <text x="360" y="188" textAnchor="middle" fontSize="14">A shortlist that covers meaning and exact words</text>
    </svg>
  );
}

function FailViz() {
  return (
    <svg className="talk-svg" viewBox="0 0 720 130" role="img" aria-label="Misses and the repairs">
      {[
        ["Typo / odd wording", "Rewrite or spell-fix"],
        ["Almost the right page", "Look again, then re-sort"],
        ["On-topic, no answer", "Refuse instead of guess"],
      ].map((row, n) => (
        <g key={row[0]} transform={`translate(${24 + n * 232} 16)`}>
          <rect width="216" height="98" rx="14" fill="#fff" stroke="#e4e7ec" />
          <text x="16" y="36" fontSize="13" fill="#b54708">{row[0]}</text>
          <text x="16" y="68" fontSize="13" fill="#0d5c56">{row[1]}</text>
        </g>
      ))}
    </svg>
  );
}

function PromptViz() {
  return (
    <svg className="talk-svg talk-svg-tall" viewBox="0 0 720 200" role="img" aria-label="Rules, found text, then the question">
      <rect x="80" y="16" width="560" height="48" rx="10" fill="#101828" />
      <text x="360" y="46" textAnchor="middle" fill="#f0fdfa" fontSize="14">Rules: only use the pack below. If it doesn’t say, say you don’t know.</text>
      <rect x="80" y="76" width="560" height="56" rx="10" fill="#ccfbf1" stroke="#0f766e" />
      <text x="360" y="110" textAnchor="middle" fontSize="14">Found pages (the evidence)</text>
      <rect x="80" y="144" width="560" height="40" rx="10" fill="#fff" stroke="#e4e7ec" />
      <text x="360" y="170" textAnchor="middle" fontSize="14">The person’s question</text>
    </svg>
  );
}

function ModernViz() {
  const items = [
    "Simple lookup",
    "+ word search",
    "+ chat memory",
    "+ a second look",
    "+ tools / agents",
    "+ pictures / graphs",
  ];
  return (
    <svg className="talk-svg" viewBox="0 0 720 120" role="img" aria-label="From a simple lookup to a fuller system">
      {items.map((t, n) => (
        <g key={t} transform={`translate(${12 + n * 118} 28)`}>
          <rect width="108" height="64" rx="12" fill={n === 0 ? "#ccfbf1" : "#fff"} stroke="#0f766e" />
          <text x="54" y="46" textAnchor="middle" fontSize="11" fill="#101828">{t}</text>
        </g>
      ))}
    </svg>
  );
}

const SLIDES: Slide[] = [
  {
    id: "hook",
    title: "Giving language models an open book",
    body: (
      <>
        <SlideHead
          kicker="01 · The idea"
          title="RAG: giving language models an open book"
          lead="A language model is a closed-book student. RAG is the same student, allowed to look things up before writing."
        />
        <ClosedVsOpen />
        <ul className="talk-agenda">
          <li>Why ordinary chatbots miss private, fresh facts</li>
          <li>How we prepare a library, then search it</li>
          <li>Where this still fails — and how teams keep answers honest</li>
        </ul>
      </>
    ),
  },
  {
    id: "why",
    title: "Why we need this at all",
    body: (
      <>
        <SlideHead
          kicker="02 · The gap"
          title="Why do we need RAG?"
          lead="A general chatbot is trained once on a huge public pile. Your flight, your blood test, your company leave policy are not in that pile."
        />
        <div className="talk-grid3">
          <article>
            <h2>It guesses with confidence</h2>
            <p>If the model never saw the fact, it still often writes a fluent answer. That is the hallucination problem: smooth, wrong.</p>
          </article>
          <article>
            <h2>It goes stale</h2>
            <p>Training has a cutoff. Policies change. Bookings change. Fine-tuning the whole model for every update is slow and expensive.</p>
          </article>
          <article>
            <h2>It shouldn’t see everything</h2>
            <p>Enterprises need the model to read only the pages relevant to this person, this company — not dump the whole archive into the prompt.</p>
          </article>
        </div>
      </>
    ),
  },
  {
    id: "mean",
    title: "What the three words mean",
    body: (
      <>
        <SlideHead
          kicker="03 · The name"
          title="What does retrieval-augmented generation actually mean?"
          lead="It is not one product. It is a habit: look up, then write. Same idea as an open-book exam."
        />
        <RagLetters />
        <p className="talk-note">
          Closed book: “I memorised the internet.” Open book: “I will fetch your booking, then answer.” That is why support, legal, and health tools use this pattern — the facts live in a system of record, not in the model’s memory.
        </p>
      </>
    ),
  },
  {
    id: "map",
    title: "The whole map",
    body: (
      <>
        <SlideHead
          kicker="04 · Architecture"
          title="From data to answer: two lanes, one library"
          lead="Nothing magical happens at question time that you didn’t prepare earlier. First you file the knowledge. Then you search it."
        />
        <TwoLanes />
      </>
    ),
  },
  {
    id: "ingest",
    title: "Ingestion",
    body: (
      <>
        <SlideHead
          kicker="05 · Night shift"
          title="Turning raw files into a searchable library"
          lead="PDFs, wiki pages, tickets — whatever you have — get cleaned, cut into pieces, turned into number lists, and stored so search can find them later."
        />
        <IngestFlow />
        <div className="talk-grid2">
          <p><strong>Clean</strong> means drop junk (headers, noise) so search isn’t chasing page numbers.</p>
          <p><strong>Index</strong> means “put it on a shelf the computer can scan quickly,” not a separate kind of magic.</p>
        </div>
      </>
    ),
  },
  {
    id: "chunk",
    title: "Chunking",
    body: (
      <>
        <SlideHead
          kicker="06 · First hard choice"
          title="Where does a piece of knowledge begin and end?"
          lead="You cannot stuff a whole handbook into one lookup. You cut it into pieces. Too big: mixed topics. Too small: a sentence with no setup."
        />
        <ChunkViz />
        <div className="talk-grid3">
          <article>
            <h2>Size</h2>
            <p>Models count “tokens” (word pieces). Chunk size is “how much text in one shelf slot.”</p>
          </article>
          <article>
            <h2>Overlap</h2>
            <p>A little repeated tail so a sentence isn’t chopped in half at the cut.</p>
          </article>
          <article>
            <h2>Smarter cuts</h2>
            <p>Split on headings or meaning when you can. Last resort: don’t leave a giant uncut blob.</p>
          </article>
        </div>
      </>
    ),
  },
  {
    id: "embed",
    title: "Embeddings",
    body: (
      <>
        <SlideHead
          kicker="07 · Meaning as place"
          title="Turning meaning into a point on a map"
          lead="An embedding is a list of numbers that places a sentence on a huge map. Similar ideas sit near each other — even if the words differ."
        />
        <EmbedViz />
        <p className="talk-note">
          Search “heart attack symptoms” and you can still land on a page that says “cardiac arrest.” That is meaning search. “How close” is usually cosine similarity: a score for “are these two arrows pointing the same way?”
        </p>
      </>
    ),
  },
  {
    id: "store",
    title: "The library",
    body: (
      <>
        <SlideHead
          kicker="08 · Finding neighbours"
          title="A vector store is a library built for ‘nearby meaning’"
          lead="A normal database is great at exact fields. This library is built to answer: which stored pieces sit closest to this question on the map?"
        />
        <StoreViz />
        <p className="talk-note">
          You ask. The question is turned into numbers with the <em>same</em> embedding model you used when filing. You keep the top few neighbours (top-k). Those neighbours are the only evidence the writer should see.
        </p>
      </>
    ),
  },
  {
    id: "retrieve",
    title: "Retrieval",
    body: (
      <>
        <SlideHead
          kicker="09 · The real heart"
          title="Retrieval is more than ‘search by vibe’"
          lead="Meaning search misses exact codes and rare names. Word search misses paraphrases. Production systems run both, then merge the two ranked lists."
        />
        <HybridViz />
        <p className="talk-note">
          Reciprocal rank fusion is a boring, reliable merge: high rank on either list still counts. No need to force two different scores onto one scale. That is the leap from a demo to something that catches both “carry-over leave” and “form HR-12.”
        </p>
      </>
    ),
  },
  {
    id: "fails",
    title: "When it fails",
    body: (
      <>
        <SlideHead
          kicker="10 · Reality"
          title="When RAG fails, retrieval usually failed first"
          lead="If the wrong pages arrive, a clever prompt cannot invent the right policy. Treat failure as a search problem, then a honesty problem."
        />
        <FailViz />
        <ul className="talk-list">
          <li><strong>Rewrite the question</strong> so a follow-up like “what about part-timers?” becomes a full sentence.</li>
          <li><strong>Re-sort a bigger pile</strong> (rerank) so the best page isn’t stuck at rank 20.</li>
          <li><strong>One bounded second look</strong> for typos and synonyms — not an endless agent loop.</li>
          <li><strong>If the best match is still weak, stop.</strong> A clear “I don’t know” beats a confident invention.</li>
        </ul>
      </>
    ),
  },
  {
    id: "generate",
    title: "Writing the answer",
    body: (
      <>
        <SlideHead
          kicker="11 · Day shift, last step"
          title="Generation and grounding: from a pack of pages to a reply"
          lead="Augment just means: put the found text in the prompt. Grounding means: the model may only claim what that text supports."
        />
        <PromptViz />
        <div className="talk-grid2">
          <p><strong>Citations</strong> show which page a sentence came from, so a human can check.</p>
          <p><strong>Fallback</strong> is a fixed honest line when the pack doesn’t answer — including “related, but not explicit.”</p>
        </div>
      </>
    ),
  },
  {
    id: "modern",
    title: "Beyond the demo",
    body: (
      <>
        <SlideHead
          kicker="12 · Where this goes"
          title="From a basic pipeline to a production system"
          lead="Industry talks mix these patterns. You almost never ship only one. You add a piece when a real miss shows up — not because the diagram looks impressive."
        />
        <ModernViz />
        <div className="talk-grid3">
          <article>
            <h2>Memory</h2>
            <p>Keep recent turns. Fold older ones into a short summary so follow-ups still make sense.</p>
          </article>
          <article>
            <h2>Tools / agents</h2>
            <p>One extra lookup (web, live API) when the library cannot know — labelled, bounded, not a mystery loop.</p>
          </article>
          <article>
            <h2>Eval</h2>
            <p>Check: did we fetch the right page? Did we refuse when we should? Graphs and pictures are extras, not the starting point.</p>
          </article>
        </div>
        <p className="talk-note">
          Takeaway: RAG is an open-book exam. File carefully, search twice (meaning + words), write only from the pack, and say so when the pack is empty.
        </p>
      </>
    ),
  },
];
