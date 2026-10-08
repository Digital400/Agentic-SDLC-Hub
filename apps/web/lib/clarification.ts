// Helpers for the "Clarification needed" round trip — see
// components/documents/clarification-panel.tsx.

const HEADING = /^#{1,6}\s/;

/** Pulls the agent's individual questions out of its clarification
 * markdown (bullets or numbered lines). Falls back to an empty list, in which
 * case the caller shows the raw text instead. */
export function extractQuestions(markdown: string): string[] {
  const questions: string[] = [];
  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    if (line === "" || HEADING.test(line)) continue;
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item && item[1].trim() !== "") questions.push(item[1].trim());
  }
  return questions;
}

/** The agent's explanation text (everything that isn't a heading or one of
 * the extracted questions), e.g. "The agent needs more information…". */
export function clarificationIntro(markdown: string): string {
  return markdown
    .split("\n")
    .filter((l) => {
      const t = l.trim();
      return t !== "" && !HEADING.test(t) && !/^(?:[-*•]|\d+[.)])\s+/.test(t);
    })
    .join(" ")
    .trim();
}

/** Scaffold for "answer question by question": "Q: …\nA: " blocks. */
export function answerScaffold(questions: string[]): string {
  return questions.map((q, i) => `Q${i + 1}: ${q}\nA${i + 1}: `).join("\n\n");
}
