// Deliberately tiny: the primer is the only markdown in the app and the model is
// told exactly which constructs to use (headings, paragraphs, occasional bullets
// and bold). A full markdown dependency would be more surface than the job needs.
// Anything unrecognised falls through as a paragraph rather than disappearing.

function inline(text: string) {
  // **bold** — split on the delimiter so odd/even alternates plain/bold
  return text.split(/\*\*/).map((part, i) =>
    i % 2 === 1 ? <strong key={i} className="font-semibold">{part}</strong> : part,
  );
}

export default function Markdown({ text }: { text: string }) {
  const blocks: JSX.Element[] = [];
  let bullets: string[] = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="ml-5 list-disc space-y-1">
        {bullets.map((b, i) => <li key={i}>{inline(b)}</li>)}
      </ul>,
    );
    bullets = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) { bullets.push(bullet[1]); continue; }
    flushBullets();
    if (!line) continue;
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const size = heading[1].length <= 2 ? "text-base" : "text-sm";
      blocks.push(
        <h3 key={blocks.length} className={`${size} mt-4 font-semibold text-neutral-900 first:mt-0 dark:text-neutral-100`}>
          {inline(heading[2])}
        </h3>,
      );
    } else {
      blocks.push(<p key={blocks.length}>{inline(line)}</p>);
    }
  }
  flushBullets();

  return <div className="space-y-2">{blocks}</div>;
}
