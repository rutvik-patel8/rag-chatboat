import { describe, expect, it } from "vitest";
import { smallTalkReply } from "@/lib/rag/smalltalk";

describe("smallTalkReply", () => {
  it("answers greetings with what the assistant can do", () => {
    for (const text of ["Hello", "hi!", "Hey there", "Good morning", "Hello how are you?", "hi, how's it going"]) {
      expect(smallTalkReply(text)).toContain("Global Economic Prospects");
    }
  });

  it("answers thanks with a short acknowledgement", () => {
    for (const text of ["Thanks", "thank you so much!", "ok thanks", "Cheers"]) {
      expect(smallTalkReply(text)).toMatch(/^You're welcome/);
    }
  });

  it("leaves real questions, even ones that start with a greeting, to retrieval", () => {
    for (const text of [
      "Hello, what is the growth outlook for South Asia?",
      "hi what is the iMac stand made of",
      "How are depository libraries funded?",
      "Thanks, and what about East Asia?",
      "growth",
    ]) {
      expect(smallTalkReply(text)).toBeNull();
    }
  });
});
