// Greetings and thanks get a fixed reply: retrieval would only refuse them with a list of unrelated "closest sections",
// and answering them costs no embedding or generation calls. A message counts only if every word is small talk,
// so "Hello, what is the growth outlook for South Asia?" still goes to retrieval.

const GREETING = new Set(["hi", "hello", "hey", "hiya", "yo", "morning", "afternoon", "evening", "how", "hows"]);
const THANKS = new Set(["thanks", "thank", "thx", "ty", "cheers"]);
const FILLER = new Set(["there", "good", "day", "are", "you", "u", "doing", "it", "going", "whats", "up", "sup", "ok", "okay", "so", "much", "very", "many", "great"]);

export const GREETING_REPLY =
  "Hi! I answer questions about the GPO 2018 Annual Report, the World Bank's Global Economic Prospects (June 2026) " +
  "and the iMac Product Environmental Report, citing the passages I use. What would you like to know?";
export const THANKS_REPLY = "You're welcome. Ask another question about the reports whenever you like.";

/** The fixed reply for a message that is only a greeting or thanks, otherwise null. */
export function smallTalkReply(text: string): string | null {
  const words = text.toLowerCase().replace(/['’]/g, "").split(/[^a-z]+/).filter(Boolean);
  if (!words.length || words.length > 8) return null;
  if (!words.every((w) => GREETING.has(w) || THANKS.has(w) || FILLER.has(w))) return null;
  if (words.some((w) => THANKS.has(w))) return THANKS_REPLY;
  if (words.some((w) => GREETING.has(w))) return GREETING_REPLY;
  return null;
}
