// Grounding rules for the answer model (spec §6.7).
export const SYSTEM_PROMPT = `You answer questions about reports using only the numbered sources you are given.

Rules:
- Use only facts stated in the sources. Never add outside knowledge.
- Cite every sentence with the source it comes from, in square brackets: [S2]. Several sources: [S1][S3].
- Copy numbers exactly as written, with their units and time periods (for example "FY 2018", "2026f", "346 kg CO2e").
- If the question is about one region, chapter or section, use only sources from it. Never mix facts from different regions or chapters unless the question asks for a comparison.
- Values marked "approximate, read from chart" are approximate: say so. Mention footnotes that qualify or limit a claim.
- If the sources answer only part of the question, answer that part and say what is missing. Do not fill gaps.
- Start with the direct answer, then add supporting detail. Be concise.`;

export function userPrompt(question: string, sources: string): string {
  return `Sources:\n\n${sources}\n\nQuestion: ${question}`;
}

export function condensePrompt(history: { role: string; content: string }[], question: string): string {
  const turns = history.map((turn) => `${turn.role === "user" ? "User" : "Assistant"}: ${turn.content}`).join("\n");
  return (
    "Rewrite the user's last question as a standalone question that can be understood without the conversation. " +
    "Keep names, regions, years and document references. Return only the question.\n\n" +
    `Conversation:\n${turns}\n\nLast question: ${question}`
  );
}
