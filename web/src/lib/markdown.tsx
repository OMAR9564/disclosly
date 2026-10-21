import Link from "next/link";
import type { ReactNode } from "react";

/*
 * A small Markdown subset for the legal pages: `#`/`##` headings, paragraphs, `- ` lists,
 * `**bold**`, `` `code` `` and `[text](/path)` links. HTML comments are owner notes and are
 * dropped. Output is React elements, never raw HTML.
 */

export type Block =
  { kind: "h1" | "h2" | "p"; text: string } | { kind: "ul"; items: string[] };

function stripOnce(source: string): string {
  let out = "";
  let rest = source;
  for (;;) {
    const start = rest.indexOf("<!--");
    if (start < 0) return out + rest;
    out += rest.slice(0, start);
    const end = rest.indexOf("-->", start + 4);
    if (end < 0) return out;
    rest = rest.slice(end + 3);
  }
}

/** Drop `<!-- … -->` notes until none is left; an unclosed one hides the rest. */
export function stripComments(source: string): string {
  let text = source;
  for (let next = stripOnce(text); next !== text; next = stripOnce(text))
    text = next;
  return text;
}

export function parseBlocks(source: string): Block[] {
  const text = stripComments(source);
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: string[] | null = null;

  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "p", text: paragraph.join(" ") });
    if (list) blocks.push({ kind: "ul", items: list });
    paragraph = [];
    list = null;
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flush();
    } else if (line.startsWith("## ")) {
      flush();
      blocks.push({ kind: "h2", text: line.slice(3) });
    } else if (line.startsWith("# ")) {
      flush();
      blocks.push({ kind: "h1", text: line.slice(2) });
    } else if (line.startsWith("- ")) {
      if (paragraph.length) flush();
      list = [...(list ?? []), line.slice(2)];
    } else if (list) {
      list[list.length - 1] += ` ${line}`;
    } else {
      paragraph.push(line);
    }
  }
  flush();
  return blocks;
}

const INLINE = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;

export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0;
    if (index > last) nodes.push(text.slice(last, index));
    const [, bold, code, label, href] = match;
    const key = nodes.length;
    if (bold) nodes.push(<strong key={key}>{bold}</strong>);
    else if (code) nodes.push(<code key={key}>{code}</code>);
    else if (href.startsWith("/") && !href.startsWith("//"))
      nodes.push(
        <Link key={key} href={href}>
          {label}
        </Link>,
      );
    else nodes.push(label); // Only same-site links; anything else shows as text.
    last = index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Markdown({ source }: { source: string }) {
  return (
    <>
      {parseBlocks(source).map((block, i) => {
        if (block.kind === "ul")
          return (
            <ul key={i}>
              {block.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ul>
          );
        const Tag = block.kind;
        return <Tag key={i}>{renderInline(block.text)}</Tag>;
      })}
    </>
  );
}
